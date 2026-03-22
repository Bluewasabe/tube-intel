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
