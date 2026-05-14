import asyncio
import os
import sys
import time

import feedparser
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from structlog.contextvars import bound_contextvars

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shared.db import (
    get_enabled_channels, update_channel_last_checked,
    get_pending_video, get_conn
)
from shared.logging_config import get_logger
from worker.pipeline import run_pipeline

logger = get_logger(__name__)

DB_PATH = os.environ.get("DB_PATH", "/data/tubeintel.db")
WEB_BASE = os.environ.get("WEB_BASE_URL", "http://web:5090")

YT_RSS_BASE = "https://www.youtube.com/feeds/videos.xml?channel_id="

# In-memory fail count per channel_id (resets on worker restart)
_channel_fail_counts: dict[str, int] = {}


def diff_rss_entries(rss_video_ids: list, known_ids: set) -> list:
    """Return video IDs in rss_video_ids that are not in known_ids."""
    return [vid for vid in rss_video_ids if vid not in known_ids]


async def fetch_rss_video_ids(channel_id: str) -> list[str]:
    """Fetch last 15 video IDs from a channel's YouTube RSS feed."""
    url = YT_RSS_BASE + channel_id
    start = time.monotonic()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url)
    logger.info("outbound_api_call", service="youtube_rss",
                status_code=r.status_code,
                duration_ms=int((time.monotonic() - start) * 1000))
    r.raise_for_status()
    feed = feedparser.parse(r.text)
    ids = []
    for entry in feed.entries:
        # yt:videoId tag parsed by feedparser
        vid_id = getattr(entry, "yt_videoid", None)
        if vid_id:
            ids.append(vid_id)
    return ids


async def check_channel(channel: dict, db_path: str = DB_PATH):
    """Check a single channel's RSS feed and queue any new videos."""
    channel_id = channel["channel_id"]
    with bound_contextvars(channel_id=channel_id, channel_name=channel["channel_name"]):
        logger.info("channel_check_start")
        try:
            rss_ids = await fetch_rss_video_ids(channel_id)
        except Exception as e:
            fail_count = _channel_fail_counts.get(channel_id, 0) + 1
            _channel_fail_counts[channel_id] = fail_count
            logger.warning("rss_fetch_failed", error=str(e),
                           error_type=type(e).__name__, fail_count=fail_count)
            if fail_count >= 3:
                # Warn via Discord webhook after 3 consecutive failures
                webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
                if webhook_url:
                    async with httpx.AsyncClient(timeout=10) as client:
                        await client.post(webhook_url, json={
                            "content": f"⚠️ RSS feed for **{channel['channel_name']}** has failed 3+ consecutive times. Check channel_id: `{channel_id}`"
                        })
            update_channel_last_checked(db_path, channel_id)
            return

        _channel_fail_counts[channel_id] = 0

        # Get known video IDs for this channel from DB
        with get_conn(db_path) as conn:
            rows = conn.execute(
                "SELECT video_id FROM videos WHERE channel_id = ?",
                (channel_id,)
            ).fetchall()
            known_ids = {r[0] for r in rows}

        new_ids = diff_rss_entries(rss_ids, known_ids)
        logger.info("channel_check_diff", new_videos=len(new_ids),
                    rss_total=len(rss_ids), known_total=len(known_ids))

        for vid_id in new_ids:
            url = f"https://www.youtube.com/watch?v={vid_id}"
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"{WEB_BASE}/api/submit",
                        json={"url": url, "source": "scheduled"}
                    )
            except Exception as e:
                logger.warning("queue_failed", video_id=vid_id, error=str(e))

        update_channel_last_checked(db_path, channel_id)


async def process_next_job(db_path: str = DB_PATH):
    """Process one pending video from the DB."""
    video = get_pending_video(db_path)
    if not video:
        return
    # video_id is also bound inside run_pipeline, but binding here makes the
    # "picked up by job processor" log line carry the ID too.
    with bound_contextvars(video_id=video["video_id"]):
        logger.info("job_picked_up", source=video["source"])
        try:
            await run_pipeline(video["youtube_url"], video["source"], db_path)
        except Exception as e:
            logger.error("pipeline_unhandled_exception", error=str(e),
                         error_type=type(e).__name__, exc_info=True)


def build_scheduler(db_path: str = DB_PATH) -> AsyncIOScheduler:
    """Build and return the AsyncIOScheduler with the job processor job registered."""
    scheduler = AsyncIOScheduler()
    # One video per 30s cycle — rate-limits Claude API and avoids transcript fetch spikes
    scheduler.add_job(process_next_job, "interval", seconds=30,
                      id="job_processor", args=[db_path])
    return scheduler


def _log_catchup_exception(task: asyncio.Task):
    """Surface exceptions from fire-and-forget startup catch-up tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("catchup_task_failed", error=repr(exc),
                     error_type=type(exc).__name__)


async def reschedule_channels(scheduler: AsyncIOScheduler, db_path: str = DB_PATH):
    """Add or update per-channel check jobs. Also triggers immediate catch-up on startup."""
    channels = get_enabled_channels(db_path)
    for ch in channels:
        job_id = f"channel_{ch['channel_id']}"
        if scheduler.get_job(job_id):
            scheduler.reschedule_job(job_id, trigger="interval",
                                     hours=ch["check_interval_hours"])
        else:
            scheduler.add_job(check_channel, "interval",
                              hours=ch["check_interval_hours"],
                              id=job_id, args=[ch, db_path])
        # Fire-and-forget catch-up: create_task so startup doesn't block waiting for each RSS fetch.
        # add_done_callback surfaces unhandled exceptions instead of letting asyncio swallow them.
        task = asyncio.create_task(check_channel(ch, db_path))
        task.add_done_callback(_log_catchup_exception)
