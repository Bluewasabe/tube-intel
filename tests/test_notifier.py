import json
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from worker.pipeline import post_discord_success, post_discord_failure
import asyncio

def test_success_message_no_exception():
    """Smoke test — verifies no exception building the coroutine (webhook not called with no URL)."""
    video = {"id": 1, "title": "Test Video", "video_id": "abc"}
    analysis = {
        "category": "homelab",
        "confidence": "high",
        "relevant_projects": json.dumps(["homelab"]),
        "recommendation": "Do the thing."
    }
    os.environ["DISCORD_WEBHOOK_URL"] = ""
    asyncio.run(post_discord_success(video, analysis))

def test_failure_message_no_exception():
    os.environ["DISCORD_WEBHOOK_URL"] = ""
    asyncio.run(post_discord_failure("abc123", "Some Video", "no_transcript"))

def test_success_with_list_relevant_projects():
    """Verify post_discord_success handles relevant_projects as Python list (not JSON string)."""
    video = {"id": 2, "title": "List Test", "video_id": "xyz"}
    analysis = {
        "category": "learning",
        "confidence": "medium",
        "relevant_projects": ["FIBI", "homelab"],  # Python list, not JSON string
        "recommendation": "Watch more videos."
    }
    os.environ["DISCORD_WEBHOOK_URL"] = ""
    asyncio.run(post_discord_success(video, analysis))
