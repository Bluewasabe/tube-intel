import asyncio
import logging
import os
import sys

import feedparser
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shared.db import (
    get_enabled_channels, update_channel_last_checked,
    get_pending_video, get_conn
)
from worker.pipeline import run_pipeline

logger = logging.getLogger(__name__)

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
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url)
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
    logger.info(f"Checking channel {channel['channel_name']} ({channel_id})")
    try:
        rss_ids = await fetch_rss_video_ids(channel_id)
    except Exception as e:
        logger.warning(f"RSS fetch failed for {channel_id}: {e}")
        _channel_fail_counts[channel_id] = _channel_fail_counts.get(channel_id, 0) + 1
        if _channel_fail_counts[channel_id] >= 3:
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
    logger.info(f"Found {len(new_ids)} new videos for {channel['channel_name']}")

    for vid_id in new_ids:
        url = f"https://www.youtube.com/watch?v={vid_id}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{WEB_BASE}/api/submit",
                    json={"url": url, "source": "scheduled"}
                )
        except Exception as e:
            logger.warning(f"Failed to queue {vid_id}: {e}")

    update_channel_last_checked(db_path, channel_id)


async def process_next_job(db_path: str = DB_PATH):
    """Process one pending video from the DB."""
    video = get_pending_video(db_path)
    if not video:
        return
    logger.info(f"Processing job: {video['video_id']}")
    try:
        await run_pipeline(video["youtube_url"], video["source"], db_path)
    except Exception as e:
        logger.error(f"Pipeline error for {video['video_id']}: {e}")


def build_scheduler(db_path: str = DB_PATH) -> AsyncIOScheduler:
    """Build and return the AsyncIOScheduler with the job processor job registered."""
    scheduler = AsyncIOScheduler()
    # One video per 30s cycle — rate-limits Claude API and avoids transcript fetch spikes
    scheduler.add_job(process_next_job, "interval", seconds=30,
                      id="job_processor", args=[db_path])
    return scheduler


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
        # Fire-and-forget catch-up: create_task so startup doesn't block waiting for each RSS fetch
        asyncio.create_task(check_channel(ch, db_path))
