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
        "channel_id": "UCtest123",
        "channel_name": "Test Channel",
        "channel_url": "https://youtube.com/@testchan",
        "check_interval_hours": 8
    })
    assert r.status_code == 200
    r2 = client.get("/api/channels")
    assert r2.status_code == 200
    channels = r2.get_json()["channels"]
    assert any(c["channel_id"] == "UCtest123" for c in channels)

def test_add_channel_invalid_interval(client):
    r = client.post("/api/channels", json={
        "channel_id": "UCbad",
        "channel_name": "Bad",
        "channel_url": "https://youtube.com/@bad",
        "check_interval_hours": 6
    })
    assert r.status_code == 400

def test_delete_channel(client):
    client.post("/api/channels", json={
        "channel_id": "UCdelete",
        "channel_name": "Del",
        "channel_url": "https://youtube.com/@del",
        "check_interval_hours": 12
    })
    r = client.delete("/api/channels/UCdelete")
    assert r.status_code == 200
    channels = client.get("/api/channels").get_json()["channels"]
    assert not any(c["channel_id"] == "UCdelete" for c in channels)

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
        "channel_id": "UCpatch",
        "channel_name": "Patch",
        "channel_url": "https://youtube.com/@patch",
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
