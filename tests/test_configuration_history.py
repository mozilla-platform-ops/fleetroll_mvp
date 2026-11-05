"""Compact evidence must preserve supported attribution and refuse uncertainty."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fleetroll import configuration_history as compact
from fleetroll import db
from fleetroll.rollout_history import build_history, classify_run
from fleetroll.rollout_report import load_evidence

BASE = dt.datetime(2026, 11, 3, 10, tzinfo=dt.UTC)
NOW = BASE + dt.timedelta(days=1)
REPO = "mozilla-platform-ops/ronin_puppet"


def observation(minute=0, *, host="host.example.com", **changes):
    observed = {
        "role": "gecko_t_linux_2404_talos",
        "puppet_role": "gecko_t_linux_2404_talos",
        "override_present": False,
        "override_sha256": None,
        "puppet_override_sha_applied": None,
        "vault_present": False,
        "puppet_success": True,
        "puppet_git_dirty": False,
        "puppet_git_sha": "test_master_revision",
        "puppet_git_repo": f"https://github.com/{REPO}.git",
        "puppet_git_branch": "master",
        "puppet_state_ts": BASE.isoformat(),
        "tc_worker_identity_status": "ok",
        "tc_worker_identity": {
            "provisionerId": "releng-hardware",
            "workerType": "test-pool",
            "workerGroup": "test-group",
            "workerId": host.split(".")[0],
        },
    }
    observed.update(changes)
    return {
        "host": host,
        "ts": (BASE + dt.timedelta(minutes=minute)).isoformat(),
        "action": "host.audit",
        "ok": True,
        "observed": observed,
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(compact, "utc_now", lambda: NOW)
    path = tmp_path / "fleetroll.db"
    db.init_db(path)
    overrides = tmp_path / "overrides"
    overrides.mkdir()
    conn = db.get_connection(path)
    yield SimpleNamespace(path=path, conn=conn, overrides=overrides, audit=tmp_path / "audit.jsonl")
    conn.close()


def store(env, records):
    for record in records:
        db.insert_host_observation(env.conn, record, overrides_dir=env.overrides)
    env.conn.commit()


def intervals(env, records=None, *, gap=3600, until=NOW):
    if records is None:
        records, _ = compact.read_evidence(env.conn, until=until)
    return build_history(
        records,
        overrides_dir=env.overrides,
        master_refs=set(),
        max_gap=dt.timedelta(seconds=gap),
    )


def classified(items, minute):
    stamp = BASE + dt.timedelta(minutes=minute)
    return classify_run(items, started=stamp, ended=stamp)[0]


def count(env, table):
    return env.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608


def test_stable_polling_reruns_pruning_and_raw_equivalence(env):
    records = [
        observation(minute, puppet_state_ts=(BASE + dt.timedelta(minutes=minute)).isoformat())
        for minute in range(0, 301, 3)
    ]
    store(env, records)
    assert count(env, "host_observations") == 10
    assert count(env, "configuration_spans") == 1
    assert count(env, "configuration_states") == 1
    raw, retained = intervals(env, records), intervals(env)
    assert [(x.start, x.end, x.configuration) for x in raw] == [
        (x.start, x.end, x.configuration) for x in retained
    ]
    assert retained[0].start == BASE
    assert classified(retained, 150)
    assert classified(retained, 301) is None
    records_by_host, _, _ = load_evidence(env.path, audit_log=env.audit, until=NOW)
    assert classified(intervals(env, records_by_host["host.example.com"]), 150)


@pytest.mark.parametrize("gap", [180, 600, 3600, 7200])
def test_supported_queries_match_raw(env, gap):
    records = [observation(minute) for minute in range(0, 121, 3)]
    store(env, records)
    raw, retained = intervals(env, records, gap=gap), intervals(env, gap=gap)
    for minute in range(-1, 122):
        assert classified(raw, minute) == classified(retained, minute)


def test_stricter_gap_fails_closed_and_saved_raw_restores_coverage(env):
    records = [observation(minute) for minute in [0, 3, 6, 16, 19, 22]]
    store(env, records)
    retained = intervals(env, gap=180)
    assert classified(retained, 10) is None
    assert classified(retained, 20) is None  # interior anchors were deliberately compacted
    assert classified(intervals(env, records, gap=180), 20)
    assert classified(intervals(env), 10)


@pytest.mark.parametrize(
    "changes",
    [
        {"puppet_success": False},
        {"puppet_git_dirty": True},
        {"puppet_role": "other-role"},
        {"override_present": True, "override_sha256": "missing"},
        {"vault_present": True, "vault_sha256": "test-vault"},
        {"tc_worker_identity_status": "error"},
    ],
)
def test_unknown_boundaries_never_bridge_matching_states(env, changes):
    records = [observation(0), observation(3, **changes), observation(6), observation(9)]
    store(env, records)
    raw, retained = intervals(env, records), intervals(env)
    for minute in range(10):
        assert classified(raw, minute) == classified(retained, minute)
    assert classified(retained, 4) is None


def test_failed_scans_and_long_outages_preserve_last_success(env):
    records = [observation(0)] + [
        {**observation(minute), "ok": False} for minute in range(3, 63, 3)
    ]
    store(env, records)
    assert count(env, "host_observations") == 11
    assert count(env, "configuration_spans") == 2
    store(env, [observation(180, puppet_state_ts=(BASE + dt.timedelta(minutes=175)).isoformat())])
    assert classified(intervals(env), 90) is None
    assert classified(intervals(env), 178)


def test_failed_scan_does_not_break_matching_retained_observations(env):
    records = [observation(0), {**observation(3), "ok": False}, observation(6)]
    store(env, records)
    assert classified(intervals(env, records), 4)
    assert classified(intervals(env), 4)
    assert classified(intervals(env, gap=180), 2) is None


@pytest.mark.parametrize("action", sorted(compact.WRITE_ACTIONS))
@pytest.mark.parametrize("ok", [True, False])
def test_write_attempt_requires_new_convergence_and_replay_is_idempotent(env, action, ok):
    store(env, [observation(0)])
    event = {
        "host": "host",
        "ts": (BASE + dt.timedelta(minutes=3)).isoformat(),
        "action": action,
        "ok": ok,
    }
    compact.record_boundary(env.conn, event)
    store(
        env,
        [
            observation(6),
            observation(9, puppet_state_ts=(BASE + dt.timedelta(minutes=8)).isoformat()),
        ],
    )
    before = [tuple(row) for row in env.conn.execute("SELECT * FROM configuration_spans")]
    compact.record_boundary(env.conn, event)
    assert before == [tuple(row) for row in env.conn.execute("SELECT * FROM configuration_spans")]
    assert classified(intervals(env), 5) is None
    assert classified(intervals(env), 8)


def test_late_write_invalidates_compacted_span_and_audit_replay_recovers(env):
    store(env, [observation(minute) for minute in range(0, 61, 3)])
    event = {
        "host": "host",
        "ts": (BASE + dt.timedelta(minutes=15)).isoformat(),
        "action": "host.set_vault",
    }
    env.audit.write_text(json.dumps(event) + "\n")
    db.init_db(env.path)
    assert classified(intervals(env), 30) is None
    before = env.conn.total_changes
    compact.initialize(env.conn, audit_log=env.audit)
    env.conn.commit()
    assert env.conn.total_changes == before


def test_duplicate_and_late_conflicting_observations(env):
    store(env, [observation(0), observation(30)])
    store(env, [observation(0), observation(15), observation(30)])
    assert count(env, "configuration_spans") == 1
    store(env, [observation(15, puppet_git_dirty=True)])
    assert classified(intervals(env), 10) is None
    assert classified(intervals(env), 20) is None
    store(env, [observation(33)])
    assert classified(intervals(env), 33)


def test_same_timestamp_conflict_remains_unknown_after_raw_upsert(env):
    store(env, [observation(0), observation(0, puppet_git_dirty=True), observation(3)])
    evidence, _, _ = load_evidence(env.path, audit_log=env.audit, until=NOW)
    assert classified(intervals(env, evidence["host.example.com"]), 0) is None
    assert classified(intervals(env, evidence["host.example.com"]), 2) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"role": "other-role", "puppet_role": "other-role"},
        {"puppet_git_sha": "test_new_revision"},
        {
            "tc_worker_identity": {
                "provisionerId": "releng-hardware",
                "workerType": "other-pool",
                "workerGroup": "test-group",
                "workerId": "host",
            }
        },
        {
            "vault_present": True,
            "vault_sha256": "test-vault",
            "puppet_vault_sha_applied": "test-vault",
        },
    ],
)
def test_material_changes_split_history(env, changes):
    records = [
        observation(0),
        observation(30, puppet_state_ts=(BASE + dt.timedelta(minutes=20)).isoformat(), **changes),
    ]
    store(env, records)
    assert count(env, "configuration_spans") == 2
    for minute in range(31):
        assert classified(intervals(env), minute) == classified(intervals(env, records), minute)


def test_cache_and_master_reference_expiry_preserve_provenance(env):
    content = f'PUPPET_REPO="https://github.com/{REPO}.git"\nPUPPET_BRANCH="test-rollout"\n'
    sha = hashlib.sha256(content.encode()).hexdigest()
    path = env.overrides / sha[:12]
    path.write_text(content)
    records = [
        observation(
            minute, override_present=True, override_sha256=sha, puppet_override_sha_applied=sha
        )
        for minute in [0, 3, 6]
    ]
    store(env, records)
    path.unlink()
    store(
        env,
        [
            observation(
                9, **{k: v for k, v in records[-1]["observed"].items() if k != "puppet_state_ts"}
            )
        ],
    )
    assert classified(intervals(env), 5).override_sha == sha
    env.conn.execute(
        "INSERT INTO github_refs VALUES (?, ?, 'master', ?, ?, '{}')",
        (*REPO.split("/"), BASE.isoformat(), "test_master_revision"),
    )
    store(
        env, [observation(12, puppet_git_branch="HEAD"), observation(15, puppet_git_branch="HEAD")]
    )
    env.conn.execute("DELETE FROM github_refs")
    env.conn.commit()
    evidence, metadata = compact.read_evidence(env.conn, until=NOW)
    assert metadata["retention_days"] == 30
    assert any(
        (r.get("_configuration_proof") or {}).get("provenance", {}).get("master_reference_ts")
        for r in evidence
    )
    assert classified(intervals(env), 14).override_sha is None


def test_allowlisted_storage_excludes_contents_credentials_and_output(env):
    record = observation(
        0,
        vault_content="test-private-content",
        output="test-command-output",
        puppet_git_repo=f"https://user:{'test-password'}@github.com/{REPO}.git",
    )
    record["observed"]["tc_worker_identity"]["credentials"] = "test-identity-secret"
    store(env, [record])
    data = " ".join(
        str(tuple(row)) for row in env.conn.execute("SELECT * FROM configuration_states")
    )
    for secret in [
        "test-private-content",
        "test-command-output",
        "test-password",
        "test-identity-secret",
    ]:
        assert secret not in data


def test_retention_crossing_anchor_cleanup_and_future_clocks(env, monkeypatch):
    store(env, [observation(minute) for minute in [0, 30, 60, 90]])
    future = NOW + dt.timedelta(days=29, minutes=45)
    monkeypatch.setattr(compact, "utc_now", lambda: future)
    compact.cleanup(env.conn)
    retained = intervals(env, until=future)
    assert retained[0].start == BASE + dt.timedelta(minutes=45)
    assert retained[0].end == BASE + dt.timedelta(minutes=90)
    store(env, [observation(100000)])
    assert count(env, "configuration_spans") == 1
    compact.cleanup(env.conn, now=future + dt.timedelta(days=1))
    assert count(env, "configuration_spans") == count(env, "configuration_states") == 0


def test_migration_bootstraps_only_existing_rows_and_is_read_only_in_report(env):
    store(env, [observation(0), observation(30)])
    for table in [
        "configuration_history_meta",
        "configuration_spans",
        "configuration_states",
        "configuration_boundaries",
    ]:
        env.conn.execute(f"DROP TABLE {table}")
    env.conn.commit()
    metadata = {}
    load_evidence(env.path, audit_log=env.audit, until=NOW, history_metadata=metadata)
    assert metadata["available"] is False
    assert not compact.history_available(env.conn)
    db.init_db(env.path)
    assert count(env, "configuration_spans") == 1
    assert classified(intervals(env), 15)
    db.init_db(env.path)
    assert count(env, "configuration_spans") == 1


def test_observation_and_compact_history_rollback_together(env, monkeypatch):
    original = compact.record_observation

    def fail(conn, record, **kwargs):
        original(conn, record, **kwargs)
        raise sqlite3.OperationalError("test injected failure")

    monkeypatch.setattr(compact, "record_observation", fail)
    with pytest.raises(sqlite3.OperationalError):
        store(env, [observation(0)])
    assert count(env, "host_observations") == count(env, "configuration_spans") == 0


def test_concurrent_writers_do_not_lose_conflicting_transitions(env):
    records = [observation(0), observation(0, puppet_git_dirty=True)]

    def write(record):
        conn = db.get_connection(env.path)
        try:
            db.insert_host_observation(conn, record)
            conn.commit()
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(write, records))
    assert count(env, "configuration_spans") == 2
    assert count(env, "configuration_boundaries") == 1
    assert classified(intervals(env), 0) is None


def test_saved_snapshots_restore_strict_query_without_writing_database(env):
    records = [observation(minute) for minute in [0, 3, 6, 16, 19, 22, *range(25, 101, 3)]]
    store(env, records)
    snapshot = env.path.parent / "saved.json"
    snapshot.write_text(json.dumps(records))
    before = env.conn.total_changes
    loaded, _, _ = load_evidence(
        env.path, audit_log=env.audit, until=NOW, observation_files=[snapshot]
    )
    assert classified(intervals(env, gap=180), 20) is None
    assert classified(intervals(env, loaded["host.example.com"], gap=180), 20)
    assert env.conn.total_changes == before


def test_historical_horizon_does_not_extrapolate_past_retained_endpoint(env):
    store(env, [observation(minute) for minute in range(0, 91, 3)])
    assert classified(intervals(env, until=BASE + dt.timedelta(minutes=45)), 44) is None


def test_unknown_revision_is_not_relabelled_after_cache_expiry(env):
    store(
        env,
        [
            observation(minute, puppet_git_branch="HEAD", puppet_git_sha="test_unknown_revision")
            for minute in [0, 3, 6]
        ],
    )
    assert intervals(env) == []


def test_same_timestamp_different_applied_time_is_a_conflict(env):
    record = observation(30)
    store(env, [record])
    changed = copy.deepcopy(record)
    changed["observed"]["puppet_state_ts"] = (BASE + dt.timedelta(minutes=15)).isoformat()
    store(env, [changed])
    assert classified(intervals(env), 30) is None


def test_future_write_is_clamped_once_and_replay_is_idempotent(env):
    event = {
        "host": "host.example.com",
        "ts": (NOW + dt.timedelta(days=10)).isoformat(),
        "action": "host.set_vault",
    }
    compact.record_boundary(env.conn, event, now=NOW)
    compact.record_boundary(env.conn, event, now=NOW + dt.timedelta(days=1))
    rows = env.conn.execute("SELECT ts FROM configuration_boundaries").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == NOW.timestamp()


def test_existing_boundary_schema_upgrades_without_losing_events(env):
    env.conn.execute("DROP TABLE configuration_boundaries")
    env.conn.execute(
        "CREATE TABLE configuration_boundaries (host TEXT, ts REAL, kind TEXT, PRIMARY KEY(host,ts,kind))"
    )
    env.conn.execute(
        "INSERT INTO configuration_boundaries VALUES (?, ?, ?)",
        ("host.example.com", BASE.timestamp(), "host.set_vault"),
    )
    env.conn.commit()
    db.init_db(env.path)
    db.init_db(env.path)
    row = env.conn.execute("SELECT ts, source_ts FROM configuration_boundaries").fetchone()
    assert tuple(row) == (BASE.timestamp(), BASE.timestamp())


def test_custom_audit_log_is_replayed_during_collection_initialization(env):
    custom = env.path.parent / "custom-audit.jsonl"
    event = {"host": "host.example.com", "ts": BASE.isoformat(), "action": "host.set_vault"}
    custom.write_text(json.dumps(event) + "\n")
    db.init_db(env.path, audit_log=custom)
    assert count(env, "configuration_boundaries") == 1


def test_report_keeps_compact_coverage_when_raw_anchors_outlive_override_cache(env):
    content = f'PUPPET_REPO="https://github.com/{REPO}.git"\nPUPPET_BRANCH="test-rollout"\n'
    sha = hashlib.sha256(content.encode()).hexdigest()
    path = env.overrides / sha[:12]
    path.write_text(content)
    store(
        env,
        [
            observation(
                minute, override_present=True, override_sha256=sha, puppet_override_sha_applied=sha
            )
            for minute in range(0, 301, 3)
        ],
    )
    path.unlink()
    evidence, _, _ = load_evidence(env.path, audit_log=env.audit, until=NOW)
    assert classified(intervals(env, evidence["host.example.com"]), 150).override_sha == sha


def test_operation_audit_keeps_success_semantics_and_persists_failures(env, monkeypatch, caplog):
    from fleetroll.audit import append_jsonl

    monkeypatch.setattr(db, "get_db_path", lambda: env.path)
    event = {
        "host": "host.example.com",
        "ts": BASE.isoformat(),
        "action": "host.set_vault",
        "ok": False,
    }
    append_jsonl(env.audit, event)
    assert count(env, "configuration_boundaries") == 1

    def fail(path):
        raise sqlite3.OperationalError("test unavailable DB")

    monkeypatch.setattr(db, "get_connection", fail)
    append_jsonl(env.audit, {**event, "ok": True})
    assert [json.loads(line)["ok"] for line in env.audit.read_text().splitlines()] == [False, True]
    assert "audit event retained for replay" in caplog.text


def test_migration_includes_legacy_audit_observations_and_conflicts(env):
    store(env, [observation(30)])
    for table in [
        "configuration_history_meta",
        "configuration_spans",
        "configuration_states",
        "configuration_boundaries",
    ]:
        env.conn.execute(f"DROP TABLE {table}")
    env.conn.commit()
    env.audit.write_text(
        "\n".join(
            json.dumps(record)
            for record in [observation(0), observation(15), observation(15, puppet_git_dirty=True)]
        )
    )
    db.init_db(env.path)
    assert count(env, "configuration_boundaries") == 1
    assert classified(intervals(env), 10) is None
    assert classified(intervals(env), 30)
