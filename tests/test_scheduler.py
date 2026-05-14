import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from worker.scheduler import diff_rss_entries

def test_diff_returns_only_new_ids():
    rss_ids = ["aaa", "bbb", "ccc"]
    known_ids = {"bbb", "ccc"}
    new_ids = diff_rss_entries(rss_ids, known_ids)
    assert new_ids == ["aaa"]

def test_diff_empty_when_all_known():
    rss_ids = ["aaa", "bbb"]
    known_ids = {"aaa", "bbb"}
    assert diff_rss_entries(rss_ids, known_ids) == []

def test_diff_all_new():
    rss_ids = ["aaa", "bbb"]
    new_ids = diff_rss_entries(rss_ids, set())
    assert set(new_ids) == {"aaa", "bbb"}


import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def test_diff_preserves_order():
    """diff_rss_entries preserves the order of new IDs from the RSS feed."""
    rss_ids = ["new1", "known", "new2"]
    known_ids = {"known"}
    result = diff_rss_entries(rss_ids, known_ids)
    assert result == ["new1", "new2"]


def test_fetch_rss_video_ids_extracts_yt_videoid():
    """fetch_rss_video_ids correctly extracts yt:videoId from feedparser entries."""
    from worker.scheduler import fetch_rss_video_ids

    # Build mock feedparser entries with yt_videoid attributes
    entry1 = MagicMock()
    entry1.yt_videoid = "abc123"
    entry2 = MagicMock()
    entry2.yt_videoid = "def456"

    mock_feed = MagicMock()
    mock_feed.entries = [entry1, entry2]

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.text = "<feed/>"  # feedparser will parse this

    async def run():
        with patch("worker.scheduler.feedparser.parse", return_value=mock_feed), \
             patch("worker.scheduler.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await fetch_rss_video_ids("UCtest123")
        return result

    result = asyncio.run(run())
    assert result == ["abc123", "def456"]


def test_fetch_rss_video_ids_skips_entries_without_id():
    """fetch_rss_video_ids skips entries where yt_videoid is None or absent."""
    from worker.scheduler import fetch_rss_video_ids

    entry_with_id = MagicMock(spec=[])
    entry_with_id.yt_videoid = "abc123"

    entry_without_id = MagicMock(spec=[])
    # No yt_videoid attribute at all — getattr should return None

    mock_feed = MagicMock()
    mock_feed.entries = [entry_with_id, entry_without_id]

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.text = "<feed/>"

    async def run():
        with patch("worker.scheduler.feedparser.parse", return_value=mock_feed), \
             patch("worker.scheduler.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await fetch_rss_video_ids("UCtest123")
        return result

    result = asyncio.run(run())
    assert result == ["abc123"]


def test_check_channel_queues_new_videos_only(tmp_path):
    """check_channel diffs RSS against DB and POSTs only new video IDs."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from shared.db import init_db, insert_video
    from worker.scheduler import check_channel

    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    # Pre-populate DB with one video already known for this channel
    insert_video(db_path, "https://youtube.com/watch?v=known1", "known1",
                 title="Known", channel_name="TestChan", channel_id="UCtest",
                 thumbnail_url="", published_at=None, source="scheduled")

    # RSS feed returns 2 videos: one known, one new
    rss_ids = ["known1", "new_video_1"]

    posted_jsons = []

    async def run():
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.json = MagicMock(return_value={"status": "queued", "id": 1})
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("worker.scheduler.fetch_rss_video_ids", AsyncMock(return_value=rss_ids)), \
             patch("worker.scheduler.update_channel_last_checked"), \
             patch("worker.scheduler.httpx.AsyncClient", return_value=mock_client):

            channel = {"channel_id": "UCtest", "channel_name": "TestChan"}
            await check_channel(channel, db_path)

            for call in mock_client.post.call_args_list:
                # call.kwargs["json"] or positional args[1]["json"]
                kw = call.kwargs if call.kwargs else {}
                if "json" not in kw and len(call.args) > 1:
                    kw = call.args[1]
                posted_jsons.append(kw.get("json", {}))

    asyncio.run(run())

    # Should have POSTed exactly once — only the new video
    assert len(posted_jsons) == 1
    assert "new_video_1" in posted_jsons[0].get("url", "")
    assert posted_jsons[0].get("source") == "scheduled"
