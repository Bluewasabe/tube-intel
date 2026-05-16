import asyncio
import json
import os
import re
import sys
import time

import httpx
from anthropic import AsyncAnthropic
from structlog.contextvars import bound_contextvars
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shared.db import (
    insert_video, get_video_by_video_id, update_video_status,
    insert_analysis, get_video_by_id, get_pending_video
)
from shared.logging_config import get_logger

logger = get_logger(__name__)


# Sentinel for rate limit exhaustion — lets run_pipeline set fail_reason="rate_limited" vs generic "claude_error"
class RateLimitExhausted(Exception):
    pass


VALID_CATEGORIES = {"new_project", "apply_to_existing", "learning", "homelab", "velvet_verve", "low_value"}
VALID_CONFIDENCE = {"high", "medium", "low"}
YT_PATTERN = re.compile(r'(?:youtube\.com/watch\?.*v=|youtu\.be/)([a-zA-Z0-9_-]{11})')

DB_PATH = os.environ.get("DB_PATH", "/data/tubeintel.db")
CONTEXT_PATH = os.environ.get("CONTEXT_PATH", "/app/prompt_context.md")


def extract_video_id(url: str) -> str | None:
    m = YT_PATTERN.search(url)
    return m.group(1) if m else None


def parse_claude_response(raw: str) -> dict:
    """Parse Claude's JSON response, strip markdown fences if present."""
    text = raw.strip()
    # Strip ```json ... ``` fences
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON parse failed: {e}") from e

    category = data.get("category", "")
    if category not in VALID_CATEGORIES:
        raise ValueError(f"invalid category: {category!r}")
    confidence = data.get("confidence", "")
    if confidence not in VALID_CONFIDENCE:
        raise ValueError(f"invalid confidence: {confidence!r}")
    return data


def build_system_prompt(context: str) -> str:
    """Frozen prefix sent as the system prompt — cached via cache_control.

    Includes persona + projects/goals context + JSON-shape instructions. Stable
    across every video, which is what makes it cacheable. The video title and
    transcript are the only per-request bits and live in the user message.
    """
    return f"""You are an AI assistant helping a senior software engineer categorize YouTube videos.

{context}

---

## Instructions

Based on the projects and goals described above, analyze the video provided in the next message and return a single JSON object:

{{
  "summary": "2-3 sentence plain English summary of what the video covers",
  "category": "one of: new_project | apply_to_existing | learning | homelab | velvet_verve | low_value",
  "relevant_projects": ["list", "of", "project", "names", "from", "the", "context", "above"],
  "recommendation": "Specific actionable recommendation — reference concrete timestamps or techniques if applicable",
  "confidence": "high | medium | low"
}}

Return ONLY the JSON object. No markdown, no explanation."""


def build_user_prompt(title: str, transcript: str) -> str:
    """Per-request user message — title + transcript only. Not cached."""
    return f"""## Video to Analyze

**Title:** {title}

**Transcript:**
{transcript}"""


def build_prompt(title: str, transcript: str, context: str) -> str:
    """Combined prompt — kept for backward compatibility with existing tests.

    Production path uses build_system_prompt + build_user_prompt so the system
    portion can be cached. This shim concatenates them for callers that still
    want a single string.
    """
    return build_system_prompt(context) + "\n\n---\n\n" + build_user_prompt(title, transcript)


def _load_context() -> str:
    try:
        with open(CONTEXT_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning("prompt_context_missing", path=CONTEXT_PATH)
        return "## No context available"


async def fetch_metadata(video_id: str) -> dict:
    """Fetch video metadata via YouTube oEmbed (no API key)."""
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    start = time.monotonic()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
    duration_ms = int((time.monotonic() - start) * 1000)
    logger.info("outbound_api_call", service="youtube_oembed",
                status_code=r.status_code, duration_ms=duration_ms)
    r.raise_for_status()
    data = r.json()
    return {
        "title": data.get("title"),
        "channel_name": data.get("author_name"),
        "channel_id": None,
        "thumbnail_url": data.get("thumbnail_url"),
        "published_at": None,
    }


def fetch_transcript(video_id: str) -> str | None:
    """Fetch transcript synchronously (youtube-transcript-api is not async)."""
    start = time.monotonic()
    try:
        segments = YouTubeTranscriptApi.get_transcript(video_id)
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info("transcript_fetched", segments=len(segments), duration_ms=duration_ms)
        return " ".join(s["text"] for s in segments)
    except (NoTranscriptFound, TranscriptsDisabled) as e:
        logger.info("transcript_unavailable", reason=type(e).__name__)
        return None
    except Exception as e:
        logger.warning("transcript_fetch_error", error=str(e))
        return None


CLAUDE_MODEL = "claude-haiku-4-5"


async def _call_claude(system: str, user_message: str) -> str:
    """Call Claude with a cacheable system prompt + per-request user message.

    The system block carries cache_control: ephemeral so identical system
    content across calls is served from cache (~10% of input cost) instead of
    re-tokenized. Caching only fires once the cacheable prefix is large enough
    for the model — Haiku 4.5's minimum is 4096 tokens; smaller prefixes are a
    silent no-op (cache_creation_input_tokens stays 0). The cache_read /
    cache_write counters are emitted on every outbound_api_call log so we can
    see when caching actually kicks in.
    """
    client = AsyncAnthropic()
    for attempt, wait in enumerate([0, 5, 15, 45]):
        if wait:
            await asyncio.sleep(wait)
        start = time.monotonic()
        try:
            msg = await client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1024,
                system=[{
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_message}],
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                "outbound_api_call",
                service="anthropic",
                model=CLAUDE_MODEL,
                duration_ms=duration_ms,
                attempt=attempt,
                input_tokens=msg.usage.input_tokens,
                output_tokens=msg.usage.output_tokens,
                cache_read_tokens=getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(msg.usage, "cache_creation_input_tokens", 0) or 0,
            )
            return msg.content[0].text
        except Exception as e:
            if "429" in str(e):
                if attempt < 3:
                    logger.warning("claude_rate_limited",
                                   retry_in_seconds=[5, 15, 45][attempt],
                                   attempt=attempt)
                    continue
                raise RateLimitExhausted("Rate limit exhausted after retries") from e
            raise


async def post_discord_success(video: dict, analysis: dict, base_url: str = "https://youtube-intel.bookclub44.com"):
    # Read from env at call time (not module level) so runtime env changes take effect without restart
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        return
    rp = analysis.get("relevant_projects") or []
    if isinstance(rp, str):
        rp = json.loads(rp)
    projects_str = ", ".join(rp) if rp else "—"
    msg = (
        f"✅ **New scan complete**\n"
        f"📺 {video['title']}\n"
        f"📁 Category: `{analysis['category']}`  |  Confidence: `{analysis['confidence']}`\n"
        f"🎯 Relevant to: {projects_str}\n"
        f"💡 {analysis['recommendation']}\n"
        f"🔗 View full analysis → {base_url}/video/{video['id']}"
    )
    start = time.monotonic()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(webhook_url, json={"content": msg})
    logger.info("outbound_api_call", service="discord_webhook",
                status_code=r.status_code, kind="success",
                duration_ms=int((time.monotonic() - start) * 1000))


async def post_discord_failure(video_id: str, title: str | None, fail_reason: str):
    # Read from env at call time (not module level) so runtime env changes take effect without restart
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        return
    label = title or f"video_id={video_id}"
    msg = f"⚠️ **Scan failed**\n📺 {label}\n❌ Reason: `{fail_reason}`"
    start = time.monotonic()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(webhook_url, json={"content": msg})
    logger.info("outbound_api_call", service="discord_webhook",
                status_code=r.status_code, kind="failure",
                duration_ms=int((time.monotonic() - start) * 1000))


async def run_pipeline(youtube_url: str, source: str, db_path: str = DB_PATH):
    """
    Full analysis pipeline for one video URL.
    Returns dict with keys: video_id, status, id
    """
    video_id = extract_video_id(youtube_url)
    if not video_id:
        return {"error": "invalid URL"}

    # bound_contextvars makes every log call inside this block (and inside any
    # nested coroutine in the same asyncio context) automatically include video_id.
    with bound_contextvars(video_id=video_id, source=source):
        # Dedup
        existing = get_video_by_video_id(db_path, video_id)
        if existing and existing["status"] == "done":
            logger.info("pipeline_skip_duplicate")
            return {"video_id": video_id, "status": "exists", "id": existing["id"]}

        logger.info("pipeline_start")
        pipeline_start = time.monotonic()

        # Insert pending row (may already exist if requeued)
        row_id = insert_video(db_path, youtube_url, video_id,
                              title=None, channel_name=None, channel_id=None,
                              thumbnail_url=None, published_at=None, source=source)
        if row_id is None:
            existing = get_video_by_video_id(db_path, video_id)
            row_id = existing["id"] if existing else None

        if row_id is None:
            logger.error("pipeline_db_error", message="could not resolve row_id")
            update_video_status(db_path, video_id, "failed", fail_reason="db_error")
            await post_discord_failure(video_id, None, "db_error")
            return {"video_id": video_id, "status": "failed"}

        update_video_status(db_path, video_id, "processing")

        # Fetch metadata
        title = None
        try:
            meta = await fetch_metadata(video_id)
            title = meta["title"]
            from shared.db import get_conn
            with get_conn(db_path) as conn:
                conn.execute(
                    "UPDATE videos SET title=?, channel_name=?, thumbnail_url=? WHERE video_id=?",
                    (meta["title"], meta["channel_name"], meta["thumbnail_url"], video_id)
                )
        except Exception as e:
            logger.error("pipeline_failed", stage="metadata", error=str(e),
                         error_type=type(e).__name__)
            update_video_status(db_path, video_id, "failed", fail_reason="fetch_error")
            await post_discord_failure(video_id, None, "fetch_error")
            return {"video_id": video_id, "status": "failed"}

        # Fetch transcript (sync — run in executor so we don't block the event loop)
        transcript = await asyncio.get_running_loop().run_in_executor(None, fetch_transcript, video_id)
        if not transcript:
            logger.warning("pipeline_failed", stage="transcript", reason="no_transcript")
            update_video_status(db_path, video_id, "failed", fail_reason="no_transcript")
            await post_discord_failure(video_id, title, "no_transcript")
            return {"video_id": video_id, "status": "failed"}

        update_video_status(db_path, video_id, "processing", transcript=transcript)

        # Build prompt and call Claude. System carries the frozen prefix
        # (persona + projects/goals + JSON instructions) and is cacheable;
        # user_message carries the variable per-video bits.
        context = _load_context()
        system = build_system_prompt(context)
        user_message = build_user_prompt(title, transcript)

        try:
            raw = await _call_claude(system, user_message)
        except RateLimitExhausted:
            logger.error("pipeline_failed", stage="claude", reason="rate_limited")
            update_video_status(db_path, video_id, "failed", fail_reason="rate_limited")
            await post_discord_failure(video_id, title, "rate_limited")
            return {"video_id": video_id, "status": "failed"}
        except Exception as e:
            logger.error("pipeline_failed", stage="claude", error=str(e),
                         error_type=type(e).__name__)
            update_video_status(db_path, video_id, "failed", fail_reason="claude_error")
            await post_discord_failure(video_id, title, "claude_error")
            return {"video_id": video_id, "status": "failed"}

        # Parse response — retry once with stricter prompt on failure.
        # The strictness reminder appends to the user message only so the
        # system prefix stays byte-identical and the cache (when it fires)
        # remains valid across the retry.
        try:
            analysis_data = parse_claude_response(raw)
        except ValueError:
            try:
                raw2 = await _call_claude(
                    system,
                    user_message + "\n\nIMPORTANT: Return ONLY valid JSON. No text before or after.",
                )
                analysis_data = parse_claude_response(raw2)
            except RateLimitExhausted:
                logger.error("pipeline_failed", stage="claude_retry", reason="rate_limited")
                update_video_status(db_path, video_id, "failed", fail_reason="rate_limited")
                await post_discord_failure(video_id, title, "rate_limited")
                return {"video_id": video_id, "status": "failed"}
            except Exception as e:
                logger.error("pipeline_failed", stage="parse", error=str(e),
                             error_type=type(e).__name__)
                update_video_status(db_path, video_id, "failed", fail_reason="parse_error")
                await post_discord_failure(video_id, title, "parse_error")
                return {"video_id": video_id, "status": "failed"}

        # Write analysis
        insert_analysis(db_path, row_id,
                        summary=analysis_data["summary"],
                        category=analysis_data["category"],
                        relevant_projects=analysis_data["relevant_projects"],
                        recommendation=analysis_data["recommendation"],
                        confidence=analysis_data["confidence"])
        update_video_status(db_path, video_id, "done")

        video_row = get_video_by_id(db_path, row_id)
        await post_discord_success(video_row, analysis_data)

        logger.info("pipeline_done",
                    category=analysis_data["category"],
                    confidence=analysis_data["confidence"],
                    duration_ms=int((time.monotonic() - pipeline_start) * 1000))

        return {"video_id": video_id, "status": "done", "id": row_id}
