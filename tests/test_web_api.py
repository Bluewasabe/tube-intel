import os
import sys
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'web'))

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.setenv("FLASK_PORT", "5090")
    from shared.db import init_db
    init_db(db_path)
    from web.app import create_app
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200

def test_submit_valid_url(client):
    r = client.post("/api/submit", json={
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "source": "manual"
    })
    assert r.status_code == 200
    data = r.get_json()
    assert data["video_id"] == "dQw4w9WgXcQ"
    assert data["status"] in ("queued", "exists")

def test_submit_short_url(client):
    r = client.post("/api/submit", json={
        "url": "https://youtu.be/dQw4w9WgXcQ",
        "source": "discord"
    })
    assert r.status_code == 200
    data = r.get_json()
    assert data["video_id"] == "dQw4w9WgXcQ"

def test_submit_invalid_url(client):
    r = client.post("/api/submit", json={"url": "https://example.com", "source": "manual"})
    assert r.status_code == 400

def test_submit_missing_url(client):
    r = client.post("/api/submit", json={"source": "manual"})
    assert r.status_code == 400

def test_api_videos_empty(client):
    r = client.get("/api/videos")
    assert r.status_code == 200
    data = r.get_json()
    assert data["videos"] == []
    assert data["total"] == 0

def test_submit_dedup(client):
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    r1 = client.post("/api/submit", json={"url": url, "source": "manual"})
    r2 = client.post("/api/submit", json={"url": url, "source": "manual"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.get_json()["status"] == "exists"

def test_add_and_list_channel(client):
    r = client.post("/api/channels", json={
        "channel_url": "https://www.youtube.com/channel/UCtest123",
        "channel_name": "Test Channel",
        "check_interval_hours": 8
    })
    assert r.status_code == 200
    r2 = client.get("/api/channels")
    assert r2.status_code == 200
    channels = r2.get_json()["channels"]
    assert any(c["channel_id"] == "UCtest123" for c in channels)

def test_add_channel_invalid_interval(client):
    r = client.post("/api/channels", json={
        "channel_url": "https://www.youtube.com/channel/UCbad",
        "channel_name": "Bad",
        "check_interval_hours": 6
    })
    assert r.status_code == 400

def test_delete_channel(client):
    client.post("/api/channels", json={
        "channel_url": "https://www.youtube.com/channel/UCdelete",
        "channel_name": "Del",
        "check_interval_hours": 12
    })
    r = client.delete("/api/channels/UCdelete")
    assert r.status_code == 200
    channels = client.get("/api/channels").get_json()["channels"]
    assert not any(c["channel_id"] == "UCdelete" for c in channels)

def test_add_channel_invalid_url(client):
    r = client.post("/api/channels", json={"channel_url": "https://example.com/notYouTube"})
    assert r.status_code == 400

def test_video_detail_not_found(client):
    r = client.get("/api/video/999")
    assert r.status_code == 404

def test_video_detail_found(client):
    client.post("/api/submit", json={"url": "https://youtu.be/abc1234abcd", "source": "manual"})
    videos = client.get("/api/videos").get_json()["videos"]
    assert len(videos) == 1
    vid_id = videos[0]["id"]
    r = client.get(f"/api/video/{vid_id}")
    assert r.status_code == 200
    data = r.get_json()
    assert data["video"]["video_id"] == "abc1234abcd"

def test_patch_channel_toggle(client):
    client.post("/api/channels", json={
        "channel_url": "https://www.youtube.com/channel/UCpatch",
        "channel_name": "Patch",
        "check_interval_hours": 12
    })
    r = client.patch("/api/channels/UCpatch", json={"enabled": False})
    assert r.status_code == 200
    channels = client.get("/api/channels").get_json()["channels"]
    match = next(c for c in channels if c["channel_id"] == "UCpatch")
    assert match["enabled"] == 0

def test_patch_channel_empty_body_returns_400(client):
    r = client.patch("/api/channels/UCany", json={})
    assert r.status_code == 400

def test_api_videos_total_reflects_actual_count(client):
    client.post("/api/submit", json={"url": "https://youtu.be/aaaaaaaaaaa", "source": "manual"})
    client.post("/api/submit", json={"url": "https://youtu.be/bbbbbbbbbbb", "source": "manual"})
    # Fetch with limit=1 — total should still be 2, not 1
    r = client.get("/api/videos?limit=1")
    data = r.get_json()
    assert data["total"] == 2
    assert len(data["videos"]) == 1

def test_api_videos_invalid_params(client):
    r = client.get("/api/videos?limit=abc")
    assert r.status_code == 400

# ── Additional coverage added by Phase 2 gate review ─────────────────────────

def test_health_response_shape(client):
    """Health endpoint must return {"ok": True}, not just 200."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}

def test_submit_no_body_returns_400(client):
    """POST /api/submit with no JSON body should return 400, not 500."""
    r = client.post("/api/submit")
    assert r.status_code == 400

def test_submit_dedup_returns_existing_id(client):
    """Dedup response must include the id of the existing row."""
    url = "https://youtu.be/dQw4w9WgXcQ"
    r1 = client.post("/api/submit", json={"url": url, "source": "manual"})
    first_id = r1.get_json()["id"]
    r2 = client.post("/api/submit", json={"url": url, "source": "discord"})
    data = r2.get_json()
    assert data["status"] == "exists"
    assert data["id"] == first_id

def test_video_detail_response_shape(client):
    """Detail endpoint must return both 'video' and 'analysis' keys; analysis is null before processing."""
    client.post("/api/submit", json={"url": "https://youtu.be/abc1234abcd", "source": "manual"})
    vid_id = client.get("/api/videos").get_json()["videos"][0]["id"]
    r = client.get(f"/api/video/{vid_id}")
    assert r.status_code == 200
    data = r.get_json()
    assert "video" in data
    assert "analysis" in data
    assert data["analysis"] is None  # no analyzer has run yet

def test_api_videos_offset_reflected_in_response(client):
    """Offset value must be echoed back in the response body."""
    client.post("/api/submit", json={"url": "https://youtu.be/aaaaaaaaaaa", "source": "manual"})
    client.post("/api/submit", json={"url": "https://youtu.be/bbbbbbbbbbb", "source": "manual"})
    r = client.get("/api/videos?offset=1&limit=10")
    data = r.get_json()
    assert r.status_code == 200
    assert data["offset"] == 1
    assert data["total"] == 2
    assert len(data["videos"]) == 1

def test_api_videos_invalid_offset_returns_400(client):
    """Non-integer offset must return 400."""
    r = client.get("/api/videos?offset=abc")
    assert r.status_code == 400

def test_api_videos_limit_capped_at_100(client):
    """limit=9999 must be accepted (capped to 100 server-side) and return 200."""
    r = client.get("/api/videos?limit=9999")
    assert r.status_code == 200

def test_add_channel_missing_channel_url_returns_400(client):
    """POST /api/channels without channel_url must return 400."""
    r = client.post("/api/channels", json={
        "channel_name": "No URL",
        "check_interval_hours": 12
    })
    assert r.status_code == 400

def test_add_channel_bad_interval_string_defaults_to_12(client):
    """Non-integer check_interval_hours silently defaults to 12 and succeeds."""
    r = client.post("/api/channels", json={
        "channel_url": "https://www.youtube.com/channel/UCdefault",
        "channel_name": "Default",
        "check_interval_hours": "bad"
    })
    assert r.status_code == 200

def test_patch_channel_interval_only(client):
    """PATCH with only check_interval_hours (no enabled) must succeed."""
    client.post("/api/channels", json={
        "channel_url": "https://www.youtube.com/channel/UCinterval",
        "channel_name": "Interval",
        "check_interval_hours": 12
    })
    r = client.patch("/api/channels/UCinterval", json={"check_interval_hours": 8})
    assert r.status_code == 200

def test_patch_channel_invalid_interval_value_returns_400(client):
    """PATCH with an integer interval not in {8,12,24} must return 400."""
    client.post("/api/channels", json={
        "channel_url": "https://www.youtube.com/channel/UCbadval",
        "channel_name": "BadVal",
        "check_interval_hours": 12
    })
    r = client.patch("/api/channels/UCbadval", json={"check_interval_hours": 99})
    assert r.status_code == 400

def test_patch_channel_interval_non_integer_returns_400(client):
    """PATCH with a string interval must return 400."""
    client.post("/api/channels", json={
        "channel_url": "https://www.youtube.com/channel/UCstrval",
        "channel_name": "StrVal",
        "check_interval_hours": 12
    })
    r = client.patch("/api/channels/UCstrval", json={"check_interval_hours": "bad"})
    assert r.status_code == 400
