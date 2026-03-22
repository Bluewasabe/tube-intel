import json
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from worker.pipeline import extract_video_id, parse_claude_response, build_prompt

def test_extract_standard_url():
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

def test_extract_short_url():
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

def test_extract_url_with_extra_params():
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s") == "dQw4w9WgXcQ"

def test_extract_invalid_returns_none():
    assert extract_video_id("https://example.com") is None
    assert extract_video_id("not a url") is None

def test_parse_claude_response_valid():
    raw = json.dumps({
        "summary": "A video about Docker.",
        "category": "homelab",
        "relevant_projects": ["homelab"],
        "recommendation": "Use this pattern for your Proxmox setup.",
        "confidence": "high"
    })
    result = parse_claude_response(raw)
    assert result["category"] == "homelab"
    assert result["confidence"] == "high"
    assert result["relevant_projects"] == ["homelab"]

def test_parse_claude_response_strips_markdown():
    raw = '```json\n{"summary":"S","category":"learning","relevant_projects":[],"recommendation":"R","confidence":"low"}\n```'
    result = parse_claude_response(raw)
    assert result["category"] == "learning"

def test_parse_claude_response_invalid_category():
    raw = json.dumps({
        "summary": "S", "category": "garbage",
        "relevant_projects": [], "recommendation": "R", "confidence": "high"
    })
    with pytest.raises(ValueError, match="invalid category"):
        parse_claude_response(raw)

def test_parse_claude_response_invalid_confidence():
    raw = json.dumps({
        "summary": "S", "category": "learning",
        "relevant_projects": [], "recommendation": "R", "confidence": "very_high"
    })
    with pytest.raises(ValueError, match="invalid confidence"):
        parse_claude_response(raw)

def test_build_prompt_contains_title_and_transcript():
    prompt = build_prompt("My Cool Video", "this is the transcript", "## context")
    assert "My Cool Video" in prompt
    assert "this is the transcript" in prompt
    assert "## context" in prompt
    assert "JSON" in prompt
