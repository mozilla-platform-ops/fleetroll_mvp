"""Durable recovery baselines, contradiction boundaries, and JSON provenance."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fleetroll.audit import append_jsonl
from fleetroll.commands.monitor.cache import ShaInfoCache
from fleetroll.commands.monitor.json_output import build_monitor_json_record, follow_monitor_json
from fleetroll.configuration_history import cleanup
from fleetroll.data_provider import LocalProvider
from fleetroll.db import get_latest_host_observations, init_db, insert_host_observation
from fleetroll.worker_history import initialize, load_history, record_write, render_history

HOST = "worker.example.test"
BASE = "gecko-t-linux-talos-2404"
POOL = BASE + "-test"
START = datetime(2026, 11, 1, tzinfo=UTC)


def stamp(offset):
    return (START + timedelta(minutes=offset)).isoformat()


def audit(offset=0, *, pool=BASE, override=False, success=True, status="ok"):
    return {
        "host": HOST,
        "ts": stamp(offset),
        "ok": True,
        "observed": {
            "role": "gecko_t_linux_2404_talos",
            "override_present": override,
            "override_sha256": "test_override_sha" if override else None,
            "puppet_override_sha_applied": "test_override_sha" if override else None,
            "puppet_success": success,
            "tc_worker_identity_status": status,
            "tc_worker_identity": {
                "provisionerId": "releng-hardware",
                "workerType": pool,
                "workerGroup": "test-group",
                "workerId": "configured-id",
            },
        },
    }


@pytest.fixture
def cache(tmp_path):
    overrides = tmp_path / "overrides"
    overrides.mkdir()
    (overrides / "test_overrid").write_text(f"PUPPET_BRANCH=testing\nWORKER_TYPE_OVERRIDE={POOL}\n")
    return ShaInfoCache(overrides, tmp_path / "vault")


def render(conn, cache):
    return render_history(
        load_history(conn, hosts=[HOST]).get(HOST), sha_cache=cache, now=START + timedelta(days=1)
    )


def write(conn, offset=1, *, action="host.set_override"):
    record_write(conn, record={"host": HOST, "ts": stamp(offset), "action": action})


def test_baseline_survives_identityless_successes_failures_and_pruning(db_conn, cache):
    insert_host_observation(db_conn, audit())
    for offset in range(1, 15):
        insert_host_observation(db_conn, audit(offset, status="invalid"), retention_limit=2)
    insert_host_observation(
        db_conn, {"host": HOST, "ts": stamp(15), "ok": False}, retention_limit=2
    )
    cleanup(db_conn, now=START + timedelta(days=100))
    raw = db_conn.execute("SELECT ts FROM host_observations").fetchall()
    assert stamp(0) not in {row[0] for row in raw}
    baseline = render(db_conn, cache)
    assert baseline["observation_ts"] == stamp(0)
    assert baseline["worker_id"] == "configured-id"
    assert baseline["contradiction_status"] == "uncertain"
    assert baseline["freshness"] == "stale"
    # Reinitialization must not erase the durable baseline either.
    db_conn.commit()
    path = db_conn.execute("PRAGMA database_list").fetchone()[2]
    init_db(Path(path))
    assert render(db_conn, cache) == baseline


@pytest.mark.parametrize("status", ["invalid", "unreadable", "ambiguous", "ok", None])
def test_unusable_new_identity_is_uncertainty(db_conn, cache, status):
    insert_host_observation(db_conn, audit())
    incomplete = audit(2, status=status)
    incomplete["observed"]["tc_worker_identity"].pop("workerId")
    if status is None:
        incomplete["observed"].pop("tc_worker_identity_status")
    insert_host_observation(db_conn, incomplete)
    baseline = render(db_conn, cache)
    assert baseline["observation_ts"] == stamp(0)
    assert baseline["contradiction_status"] == "uncertain"


def test_failed_ssh_alone_does_not_contradict_or_refresh(db_conn, cache):
    insert_host_observation(db_conn, audit())
    before = render(db_conn, cache)
    insert_host_observation(db_conn, {"host": HOST, "ts": stamp(2), "ok": False})
    assert render(db_conn, cache) == before
    latest, _ = get_latest_host_observations(db_conn, [HOST])
    output = build_monitor_json_record(
        HOST,
        record=latest[HOST],
        tc_record=None,
        sha_cache=cache,
        historical_state=load_history(db_conn, hosts=[HOST])[HOST],
        now=START + timedelta(days=1),
    )
    assert output["observed_worker_identity"]["status"] == "unresolved"
    assert output["historical_worker_identity"] == before
    assert before["contradiction_status"] == "none_known"


def test_missing_configuration_contradiction_survives_later_collection_error(db_conn, cache):
    insert_host_observation(db_conn, audit())
    insert_host_observation(db_conn, audit(2, status="missing"))
    insert_host_observation(db_conn, audit(3, status="invalid"))
    assert render(db_conn, cache)["contradiction_status"] == "contradicted"
    insert_host_observation(db_conn, audit(4))
    assert render(db_conn, cache)["contradiction_status"] == "none_known"


@pytest.mark.parametrize("unset", [False, True])
@pytest.mark.parametrize(
    "failure", ["old_pool", "provisioner", "unresolved", "pending", "failed", "unknown"]
)
def test_override_block_requires_complete_applied_matching_destination(
    db_conn, cache, unset, failure
):
    insert_host_observation(db_conn, audit(pool=POOL if unset else BASE, override=unset))
    write(db_conn, action="host.unset_override" if unset else "host.set_override")
    destination = BASE if unset else POOL
    changed = audit(2, pool=destination, override=not unset)
    observed = changed["observed"]
    if failure == "old_pool":
        observed["tc_worker_identity"]["workerType"] = POOL if unset else BASE
    elif failure == "provisioner":
        observed["tc_worker_identity"]["provisionerId"] = "other-provisioner"
    elif failure == "unresolved":
        if unset:
            observed["role"] = "unmapped_role"
        else:
            observed["override_sha256"] = "unavailable_sha"
    elif failure == "pending":
        observed["puppet_override_sha_applied"] = "pending_sha"
    elif failure == "failed":
        observed["puppet_success"] = False
    else:
        observed["puppet_success"] = None
    insert_host_observation(db_conn, changed)
    result = render(db_conn, cache)
    assert result["observation_ts"] == stamp(2)
    assert result["contradiction_status"] == "contradicted"
    assert "override_change_unverified" in {r["code"] for r in result["reasons"]}
    insert_host_observation(db_conn, {"host": HOST, "ts": stamp(3), "ok": False})
    assert render(db_conn, cache)["contradiction_status"] == "contradicted"
    insert_host_observation(db_conn, audit(4, pool=destination, override=not unset))
    assert render(db_conn, cache)["contradiction_status"] == "none_known"


def test_remote_override_change_without_recorded_write_is_detected(db_conn, cache):
    insert_host_observation(db_conn, audit())
    insert_host_observation(db_conn, audit(2, override=True))
    assert render(db_conn, cache)["contradiction_status"] == "contradicted"
    insert_host_observation(db_conn, audit(3, override=True, pool=POOL))
    assert render(db_conn, cache)["contradiction_status"] == "none_known"


def test_short_write_before_baseline_and_live_audit_logging(db_conn, cache, monkeypatch, tmp_path):
    db_path = db_conn.execute("PRAGMA database_list").fetchone()[2]
    monkeypatch.setattr("fleetroll.db.get_db_path", lambda: Path(db_path))
    # A write can precede the first FQDN observation.
    record_write(db_conn, record={"host": "worker", "ts": stamp(1), "action": "host.set_override"})
    insert_host_observation(db_conn, audit(2, override=True))
    db_conn.commit()
    assert render(db_conn, cache)["contradiction_status"] == "contradicted"
    append_jsonl(
        tmp_path / "audit.jsonl", {"host": HOST, "ts": stamp(3), "action": "host.unset_override"}
    )
    assert load_history(db_conn, hosts=[HOST])[HOST]["override_write"]["ts"] == stamp(3)


def test_dropped_write_boundary_is_logged_and_replayed(
    db_conn, cache, monkeypatch, tmp_path, caplog
):
    import sqlite3

    db_path = db_conn.execute("PRAGMA database_list").fetchone()[2]
    monkeypatch.setattr("fleetroll.db.get_db_path", lambda: Path(db_path))

    def locked(conn, *, record):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("fleetroll.worker_history.record_write", locked)
    log = tmp_path / "audit.jsonl"
    event = {"host": HOST, "ts": stamp(3), "action": "host.set_override"}
    append_jsonl(log, event)
    assert f"host={HOST} ts={stamp(3)} action=host.set_override" in caplog.text
    assert "retained for replay on next database initialization" in caplog.text
    assert HOST not in load_history(db_conn, hosts=[HOST])
    monkeypatch.undo()
    initialize(db_conn, audit_log=log)
    assert load_history(db_conn, hosts=[HOST])[HOST]["override_write"]["ts"] == stamp(3)


def test_unchanged_history_is_not_rewritten(db_conn):
    saves = []
    db_conn.set_trace_callback(
        lambda sql: saves.append(sql)
        if sql.startswith("INSERT INTO worker_identity_history")
        else None
    )
    insert_host_observation(db_conn, audit())
    assert len(saves) == 1
    # Replaying the same observation and a later failed audit leave state unchanged.
    insert_host_observation(db_conn, audit())
    insert_host_observation(db_conn, {"host": HOST, "ts": stamp(5), "ok": False})
    assert len(saves) == 1
    # A newer successful observation changes state and is saved.
    insert_host_observation(db_conn, audit(6))
    assert len(saves) == 2


def test_migration_recovers_history_and_replays_writes_once(db_conn, cache, tmp_path):
    insert_host_observation(db_conn, audit())
    insert_host_observation(db_conn, audit(2, status="invalid"))
    db_conn.execute("DROP TABLE worker_identity_history")
    log = tmp_path / "audit.jsonl"
    log.write_text(
        '{"host":"worker.example.test","ts":"2026-11-01T00:01:00+00:00","action":"host.set_override"}\n'
    )
    initialize(db_conn, audit_log=log)
    before = render(db_conn, cache)
    assert before["observation_ts"] == stamp(0)
    assert before["contradiction_status"] == "contradicted"
    initialize(db_conn, audit_log=log)
    assert render(db_conn, cache) == before


def test_missing_history_is_explicit(db_conn, cache):
    missing = render(db_conn, cache)
    assert missing["status"] == "missing"
    assert missing["contradiction_status"] == "unknown"
    assert missing["observation_ts"] is None
    assert missing["worker_type"] is None


@pytest.mark.parametrize("field", ["provisionerId", "workerType", "workerGroup", "workerId"])
def test_new_full_identity_replaces_baseline_and_provenance(db_conn, cache, field):
    insert_host_observation(db_conn, audit())
    first = render(db_conn, cache)
    updated = audit(2)
    updated["observed"]["tc_worker_identity"][field] = "different"
    insert_host_observation(db_conn, updated)
    second = render(db_conn, cache)
    assert second["observation_ts"] == stamp(2)
    tc = {**first["provenance"]["identity"], "historical_baseline": first["provenance"]}
    output = build_monitor_json_record(
        HOST,
        record=updated,
        tc_record=tc,
        lookup_attempt={"identity": tc},
        sha_cache=cache,
        historical_state=load_history(db_conn, hosts=[HOST])[HOST],
    )
    assert output["taskcluster"]["historical_identity_match"] == "mismatch"
    assert output["taskcluster"]["historical_baseline_match"] == "mismatch"


def test_follow_emits_write_without_host_audit(db_conn, cache, mocker):
    insert_host_observation(db_conn, audit())
    db_conn.commit()
    provider = LocalProvider(db_conn)
    before = build_monitor_json_record(
        HOST,
        record=audit(),
        tc_record=None,
        sha_cache=cache,
        historical_state=provider.load_worker_history(hosts=[HOST])[HOST],
    )
    write(db_conn)
    db_conn.commit()
    mocker.patch("fleetroll.commands.monitor.json_output.time.sleep")
    stream = follow_monitor_json([HOST], provider=provider, initial={HOST: before}, sha_cache=cache)
    update = next(stream)
    stream.close()
    assert update["historical_worker_identity"]["contradiction_status"] == "contradicted"
    assert update["historical_worker_identity"]["observation_ts"] == stamp(0)


def test_source_provenance_does_not_change_as_it_ages(db_conn, cache):
    insert_host_observation(db_conn, audit())
    state = load_history(db_conn, hosts=[HOST])[HOST]
    earlier = render_history(state, sha_cache=cache, now=START + timedelta(minutes=5))
    later = render_history(state, sha_cache=cache, now=START + timedelta(days=1))
    assert earlier["freshness"] == "fresh"
    assert later["freshness"] == "stale"
    assert earlier["provenance"] == later["provenance"]


@pytest.mark.parametrize(
    "noop",
    [
        {"no_change": True},
        {"observed": {"content_changed": False}},
        {"observed": {"removed": False}},
    ],
)
def test_confirmed_noop_does_not_create_override_block(db_conn, cache, noop):
    insert_host_observation(db_conn, audit())
    record_write(
        db_conn,
        record={
            "host": HOST,
            "ts": stamp(2),
            "action": "host.set_override",
            "ok": True,
            **noop,
        },
    )
    assert render(db_conn, cache)["contradiction_status"] == "none_known"


def test_conflicting_same_timestamp_audits_fail_closed(db_conn, cache):
    insert_host_observation(db_conn, audit())
    changed = audit()
    changed["observed"]["tc_worker_identity"]["workerId"] = "changed-id"
    insert_host_observation(db_conn, changed)
    result = render(db_conn, cache)
    assert result["worker_id"] == "changed-id"
    assert result["contradiction_status"] == "uncertain"
    assert result["reasons"][0]["code"] == "observation_conflict"
    insert_host_observation(db_conn, audit(2))
    assert render(db_conn, cache)["contradiction_status"] == "none_known"


def test_migration_recovers_compact_endpoint_after_raw_pruning(db_conn, cache, tmp_path):
    insert_host_observation(db_conn, audit())
    for offset in range(1, 12):
        insert_host_observation(db_conn, audit(offset, status="invalid"), retention_limit=1)
    db_conn.execute("DROP TABLE worker_identity_history")
    initialize(db_conn, audit_log=tmp_path / "absent.jsonl")
    result = render(db_conn, cache)
    assert result["worker_id"] == "configured-id"
    assert result["observation_ts"] == stamp(0)
    assert result["contradiction_status"] == "uncertain"


def test_observation_and_history_rollback_together(db_conn, cache):
    insert_host_observation(db_conn, audit())
    db_conn.commit()
    before = render(db_conn, cache)
    insert_host_observation(db_conn, audit(2, pool=POOL, override=True))
    db_conn.rollback()
    assert render(db_conn, cache) == before
    assert get_latest_host_observations(db_conn, [HOST])[0][HOST]["ts"] == stamp(0)


def test_stale_replay_cannot_replace_newer_baseline_or_erase_uncertainty(db_conn, cache):
    insert_host_observation(db_conn, audit(2))
    insert_host_observation(db_conn, audit(4, status="invalid"))
    insert_host_observation(db_conn, audit(0, pool="old-pool"))
    result = render(db_conn, cache)
    assert result["observation_ts"] == stamp(2)
    assert result["worker_type"] == BASE
    assert result["contradiction_status"] == "uncertain"


def test_recorded_target_must_be_observed_before_override_block_clears(db_conn, cache):
    insert_host_observation(db_conn, audit())
    record_write(
        db_conn,
        record={
            "host": HOST,
            "ts": stamp(1),
            "action": "host.set_override",
            "parameters": {"sha256": "test_override_sha"},
        },
    )
    # A later successful scan of the old, internally consistent state is insufficient.
    insert_host_observation(db_conn, audit(2))
    assert render(db_conn, cache)["contradiction_status"] == "contradicted"
    insert_host_observation(db_conn, audit(3, override=True, pool=POOL))
    assert render(db_conn, cache)["contradiction_status"] == "none_known"
