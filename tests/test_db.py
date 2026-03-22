import os
import sqlite3
import tempfile
import pytest
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shared.db import init_db, get_conn, insert_video, get_video_by_video_id, update_video_status, insert_analysis, get_analysis_by_video_id, insert_channel, get_enabled_channels, get_video_by_id, get_pending_video, list_all_channels

@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path

def test_init_creates_tables(db_path):
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"videos", "analysis", "watched_channels"} <= tables
    conn.close()

def test_wal_mode_enabled(db_path):
    conn = sqlite3.connect(db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    conn.close()

def test_insert_and_fetch_video(db_path):
    vid_id = insert_video(db_path, "https://youtube.com/watch?v=abc123", "abc123",
                          title="Test Video", channel_name="Test Channel",
                          channel_id="UCtest", thumbnail_url="http://thumb.jpg",
                          published_at="2026-03-22T00:00:00", source="manual")
    assert vid_id is not None
    row = get_video_by_video_id(db_path, "abc123")
    assert row["title"] == "Test Video"
    assert row["status"] == "pending"
    assert row["source"] == "manual"

def test_dedup_on_video_id(db_path):
    insert_video(db_path, "https://youtube.com/watch?v=abc123", "abc123",
                 title="Test", channel_name="Ch", channel_id="UC1",
                 thumbnail_url="", published_at=None, source="manual")
    existing = get_video_by_video_id(db_path, "abc123")
    insert_video(db_path, "https://youtube.com/watch?v=abc123", "abc123",
                 title="Test", channel_name="Ch", channel_id="UC1",
                 thumbnail_url="", published_at=None, source="discord")
    row = get_video_by_video_id(db_path, "abc123")
    assert row["id"] == existing["id"]  # same row, not a new one

def test_update_video_status(db_path):
    insert_video(db_path, "https://youtube.com/watch?v=xyz", "xyz",
                 title="T", channel_name="C", channel_id="UC2",
                 thumbnail_url="", published_at=None, source="manual")
    update_video_status(db_path, "xyz", "failed", fail_reason="no_transcript")
    row = get_video_by_video_id(db_path, "xyz")
    assert row["status"] == "failed"
    assert row["fail_reason"] == "no_transcript"

def test_insert_and_fetch_analysis(db_path):
    vid_id = insert_video(db_path, "https://youtube.com/watch?v=ana", "ana",
                          title="A", channel_name="C", channel_id="UC3",
                          thumbnail_url="", published_at=None, source="manual")
    insert_analysis(db_path, vid_id, summary="A summary.", category="homelab",
                    relevant_projects=["homelab"], recommendation="Do the thing.",
                    confidence="high")
    a = get_analysis_by_video_id(db_path, vid_id)
    assert a["category"] == "homelab"
    assert a["confidence"] == "high"
    # relevant_projects is now deserialized to a list on read
    assert a["relevant_projects"] == ["homelab"]

def test_insert_channel_and_list(db_path):
    insert_channel(db_path, channel_id="UCtest", channel_name="Test Chan",
                   channel_url="https://youtube.com/@testchan", check_interval_hours=8)
    channels = get_enabled_channels(db_path)
    assert any(c["channel_id"] == "UCtest" for c in channels)


def test_get_video_by_id(db_path):
    row_id = insert_video(db_path, "https://youtube.com/watch?v=byid", "byid",
                          title="By ID", channel_name="C", channel_id="UC1",
                          thumbnail_url="", published_at=None, source="manual")
    row = get_video_by_id(db_path, row_id)
    assert row["video_id"] == "byid"


def test_get_pending_video(db_path):
    insert_video(db_path, "https://youtube.com/watch?v=pend", "pend",
                 title="Pending", channel_name="C", channel_id="UC1",
                 thumbnail_url="", published_at=None, source="manual")
    pending = get_pending_video(db_path)
    assert pending is not None
    assert pending["video_id"] == "pend"


def test_update_video_status_returns_false_on_missing(db_path):
    from shared.db import update_video_status
    result = update_video_status(db_path, "doesnotexist", "done")
    assert result is False


def test_toggle_and_delete_channel(db_path):
    from shared.db import toggle_channel, delete_channel, list_all_channels
    insert_channel(db_path, channel_id="UCtoggle", channel_name="Toggle",
                   channel_url="https://youtube.com/@toggle", check_interval_hours=12)
    toggle_channel(db_path, "UCtoggle", False)
    channels = get_enabled_channels(db_path)
    assert not any(c["channel_id"] == "UCtoggle" for c in channels)
    all_ch = list_all_channels(db_path)
    assert any(c["channel_id"] == "UCtoggle" for c in all_ch)
    delete_channel(db_path, "UCtoggle")
    all_ch2 = list_all_channels(db_path)
    assert not any(c["channel_id"] == "UCtoggle" for c in all_ch2)


def test_update_channel_interval_invalid_raises(db_path):
    insert_channel(db_path, channel_id="UCint", channel_name="Int",
                   channel_url="https://youtube.com/@int", check_interval_hours=12)
    with pytest.raises(ValueError):
        from shared.db import update_channel_interval
        update_channel_interval(db_path, "UCint", 6)


def test_analysis_relevant_projects_deserialized(db_path):
    vid_id = insert_video(db_path, "https://youtube.com/watch?v=deser", "deser",
                          title="D", channel_name="C", channel_id="UC1",
                          thumbnail_url="", published_at=None, source="manual")
    insert_analysis(db_path, vid_id, summary="S", category="homelab",
                    relevant_projects=["homelab", "FIBI"], recommendation="R",
                    confidence="high")
    a = get_analysis_by_video_id(db_path, vid_id)
    # Should already be a list, not a JSON string
    assert isinstance(a["relevant_projects"], list)
    assert "homelab" in a["relevant_projects"]
