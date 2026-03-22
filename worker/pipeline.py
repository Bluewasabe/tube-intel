import asyncio
import json
import logging
import os
import re
import sys

import httpx
from anthropic import AsyncAnthropic
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shared.db import (
    insert_video, get_video_by_video_id, update_video_status,
    insert_analysis, get_video_by_id, get_pending_video
)

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {"new_project", "apply_to_existing", "learning", "homelab", "velvet_verve", "low_value"}
VALID_CONFIDENCE = {"high", "medium", "low"}
YT_PATTERN = re.compile(r'(?:youtube\.com/watch\?.*v=|youtu\.be/)([a-zA-Z0-9_-]{11})')

DB_PATH = os.environ.get("DB_PATH", "/data/tubeintel.db")
CONTEXT_PATH = os.environ.get("CONTEXT_PATH", "/app/prompt_context.md")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")


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


def build_prompt(title: str, transcript: str, context: str) -> str:
    return f"""You are an AI assistant helping a senior software engineer categorize YouTube videos.

{context}

---

## Video to Analyze

**Title:** {title}

**Transcript:**
{transcript}

---

## Instructions

Based on the projects and goals described above, analyze this video and return a single JSON object:

{{
  "summary": "2-3 sentence plain English summary of what the video covers",
  "category": "one of: new_project | apply_to_existing | learning | homelab | velvet_verve | low_value",
  "relevant_projects": ["list", "of", "project", "names", "from", "the", "context", "above"],
  "recommendation": "Specific actionable recommendation — reference concrete timestamps or techniques if applicable",
  "confidence": "high | medium | low"
}}

Return ONLY the JSON object. No markdown, no explanation.
"""


def _load_context() -> str:
    try:
        with open(CONTEXT_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning(f"prompt_context.md not found at {CONTEXT_PATH}")
        return "## No context available"


async def fetch_metadata(video_id: str) -> dict:
    """Fetch video metadata via YouTube oEmbed (no API key)."""
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
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
    try:
        segments = YouTubeTranscriptApi.get_transcript(video_id)
        return " ".join(s["text"] for s in segments)
    except (NoTranscriptFound, TranscriptsDisabled):
        return None
    except Exception as e:
        logger.warning(f"Transcript fetch error for {video_id}: {e}")
        return None


async def _call_claude(prompt: str) -> str:
    client = AsyncAnthropic()
    for attempt, wait in enumerate([0, 5, 15, 45]):
        if wait:
            await asyncio.sleep(wait)
        try:
            msg = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )
            return msg.content[0].text
        except Exception as e:
            if "429" in str(e) and attempt < 3:
                logger.warning(f"Rate limited, retrying in {wait}s...")
                continue
            raise


async def post_discord_success(video: dict, analysis: dict, base_url: str = "https://youtube-intel.bookclub44.com"):
    if not DISCORD_WEBHOOK_URL:
        return
    projects = json.loads(analysis.get("relevant_projects") or "[]")
    projects_str = ", ".join(projects) if projects else "—"
    msg = (
        f"✅ **New scan complete**\n"
        f"📺 {video['title']}\n"
        f"📁 Category: `{analysis['category']}`  |  Confidence: `{analysis['confidence']}`\n"
        f"🎯 Relevant to: {projects_str}\n"
        f"💡 {analysis['recommendation']}\n"
        f"🔗 {base_url}/video/{video['id']}"
    )
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(DISCORD_WEBHOOK_URL, json={"content": msg})


async def post_discord_failure(video_id: str, title: str | None, fail_reason: str):
    if not DISCORD_WEBHOOK_URL:
        return
    label = title or f"video_id={video_id}"
    msg = f"⚠️ **Scan failed**\n📺 {label}\n❌ Reason: `{fail_reason}`"
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(DISCORD_WEBHOOK_URL, json={"content": msg})


async def run_pipeline(youtube_url: str, source: str, db_path: str = DB_PATH):
    """
    Full analysis pipeline for one video URL.
    Returns dict with keys: video_id, status, id
    """
    video_id = extract_video_id(youtube_url)
    if not video_id:
        return {"error": "invalid URL"}

    # Dedup
    existing = get_video_by_video_id(db_path, video_id)
    if existing and existing["status"] == "done":
        return {"video_id": video_id, "status": "exists", "id": existing["id"]}

    # Insert pending row (may already exist if requeued)
    row_id = insert_video(db_path, youtube_url, video_id,
                          title=None, channel_name=None, channel_id=None,
                          thumbnail_url=None, published_at=None, source=source)
    if row_id is None:
        existing = get_video_by_video_id(db_path, video_id)
        row_id = existing["id"] if existing else None

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
        logger.error(f"Metadata fetch failed for {video_id}: {e}")
        update_video_status(db_path, video_id, "failed", fail_reason="fetch_error")
        await post_discord_failure(video_id, None, "fetch_error")
        return {"video_id": video_id, "status": "failed"}

    # Fetch transcript (sync — run in executor so we don't block the event loop)
    loop = asyncio.get_event_loop()
    transcript = await loop.run_in_executor(None, fetch_transcript, video_id)
    if not transcript:
        update_video_status(db_path, video_id, "failed", fail_reason="no_transcript")
        await post_discord_failure(video_id, title, "no_transcript")
        return {"video_id": video_id, "status": "failed"}

    update_video_status(db_path, video_id, "processing", transcript=transcript)

    # Build prompt and call Claude
    context = _load_context()
    prompt = build_prompt(title, transcript, context)

    try:
        raw = await _call_claude(prompt)
    except Exception as e:
        logger.error(f"Claude API error for {video_id}: {e}")
        update_video_status(db_path, video_id, "failed", fail_reason="claude_error")
        await post_discord_failure(video_id, title, "claude_error")
        return {"video_id": video_id, "status": "failed"}

    # Parse response — retry once with stricter prompt on failure
    try:
        analysis_data = parse_claude_response(raw)
    except ValueError:
        try:
            raw2 = await _call_claude(prompt + "\n\nIMPORTANT: Return ONLY valid JSON. No text before or after.")
            analysis_data = parse_claude_response(raw2)
        except (ValueError, Exception) as e:
            logger.error(f"Parse error for {video_id}: {e}")
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

    return {"video_id": video_id, "status": "done", "id": row_id}
