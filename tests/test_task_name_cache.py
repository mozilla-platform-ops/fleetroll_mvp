"""Task metadata reuse is isolated, expires, and cannot grow without bound."""

import sqlite3

from fleetroll import task_name_cache as cache


def test_names_expire_without_sliding_on_reads_and_roots_are_isolated(tmp_path, monkeypatch):
    path = tmp_path / "names.sqlite3"
    now = 1_000_000
    monkeypatch.setattr(cache.time, "time", lambda: now)
    assert cache.load_names(path, root_url="https://one.invalid") == {}
    assert not path.exists()
    cache.save_names(path, root_url="https://one.invalid", names={"task": "original"})
    cache.save_names(path, root_url="https://two.invalid", names={"task": "other"})
    assert cache.load_names(path, root_url="https://one.invalid") == {"task": "original"}
    assert cache.load_names(path, root_url="https://two.invalid") == {"task": "other"}
    now += cache.RETENTION_SECONDS
    assert cache.load_names(path, root_url="https://one.invalid") == {}
    cache.save_names(path, root_url="https://one.invalid", names={})
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM task_names").fetchone()[0] == 0


def test_cache_caps_entries_across_roots_and_keeps_newest(tmp_path, monkeypatch):
    path = tmp_path / "names.sqlite3"
    monkeypatch.setattr(cache, "MAX_ENTRIES", 2)
    monkeypatch.setattr(cache.time, "time", lambda: 100)
    cache.save_names(path, root_url="one", names={"old": "old"})
    monkeypatch.setattr(cache.time, "time", lambda: 200)
    cache.save_names(path, root_url="two", names={"new": "new", "newer": "newer"})
    assert cache.load_names(path, root_url="one") == {}
    assert cache.load_names(path, root_url="two") == {"new": "new", "newer": "newer"}
