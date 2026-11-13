"""Task metadata reuse is isolated, expires, and cannot grow without bound."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

from fleetroll import task_name_cache as cache


def test_enrichment_deduplicates_runs_and_skips_ineligible_jobs():
    jobs = [{"task_id": "same-task", "run_id": run_id, "in_window": True} for run_id in (0, 1)]
    outside = {"task_id": "outside", "in_window": False}
    unresolved = {"task_id": "unresolved", "in_window": True, "query_error": "bad identity"}
    clients = SimpleNamespace(queue=Mock())
    clients.queue.task.return_value = {"metadata": {"name": "shared workload"}}
    stats = cache.enrich_task_names(
        [{"jobs": [jobs[0], outside]}, {"jobs": [jobs[1], unresolved]}],
        clients=clients,
        workers=2,
    )
    clients.queue.task.assert_called_once_with("same-task")
    assert stats["fetched"] == 1
    assert all(job["name"] == "shared workload" for job in jobs)
    assert "name" not in outside
    assert "name" not in unresolved


def test_existing_name_reused_for_other_runs_and_persisted(tmp_path):
    path = tmp_path / "names.sqlite3"
    jobs = [
        {"task_id": "known-task", "in_window": True, "name": "known workload"},
        {"task_id": "known-task", "in_window": True, "name": None},
    ]
    clients = SimpleNamespace(queue=Mock())
    stats = cache.enrich_task_names(
        [{"jobs": jobs}], clients=clients, workers=2, cache_path=path, root_url="test-root"
    )
    clients.queue.task.assert_not_called()
    assert stats["reused"] == 1
    assert jobs[1]["name"] == "known workload"
    assert cache.load_names(path, root_url="test-root") == {"known-task": "known workload"}


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
