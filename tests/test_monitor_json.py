"""Worker evidence contracts and JSON entry point behavior."""

import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from click.testing import CliRunner
from fleetroll.cli import cli
from fleetroll.commands.monitor.cache import ShaInfoCache
from fleetroll.commands.monitor.json_output import (
    build_monitor_json_record,
    build_monitor_json_snapshot,
    follow_monitor_json,
)
from fleetroll.db import (
    get_connection,
    get_db_path,
    init_db,
    insert_host_observation,
    insert_tc_worker,
)

HOST = "ms017.example.test"
BASE = "gecko-t-linux-talos-2404"
OVERRIDE = BASE + "-tc-v115"
TS = "2026-11-05T04:00:00Z"
NOW = datetime(2026, 11, 5, 4, 10, tzinfo=UTC)


@pytest.fixture
def evidence(tmp_path):
    cache = ShaInfoCache(tmp_path / "overrides", tmp_path / "vault")
    cache.overrides_dir.mkdir()
    (cache.overrides_dir / "test_override_sha"[:12]).write_text(
        f"PUPPET_BRANCH=testing\nWORKER_TYPE_OVERRIDE={OVERRIDE}\n"
    )
    record = {
        "host": HOST,
        "ts": TS,
        "ok": True,
        "observed": {
            "role": "gecko_t_linux_2404_talos",
            "override_present": True,
            "override_sha256": "test_override_sha",
            "puppet_override_sha_applied": "test_override_sha",
            "puppet_success": True,
            "tc_worker_identity_status": "ok",
            "tc_worker_identity": {
                "provisionerId": "releng-hardware",
                "workerType": OVERRIDE,
                "workerGroup": "test-group",
                "workerId": "ms017",
            },
        },
    }
    tc = {
        "host": HOST,
        "ts": TS,
        "type": "worker",
        "provisioner": "releng-hardware",
        "worker_type": OVERRIDE,
        "worker_group": "test-group",
        "worker_id": "ms017",
        "last_date_active": TS,
    }
    return record, tc, cache


def build(evidence):
    record, tc, cache = evidence
    return build_monitor_json_record(HOST, record=record, tc_record=tc, sha_cache=cache, now=NOW)


def test_applied_override_keeps_audit_compatible(evidence):
    original = deepcopy(evidence[0])
    output = build(evidence)
    assert evidence[0] == original
    assert all(output[key] == value for key, value in original.items())
    assert output["observed"]["role"] == "gecko_t_linux_2404_talos"
    configured = output["configured_worker_type"]
    assert configured["worker_type"] == OVERRIDE
    assert configured["source"] == "override"
    assert configured["application_status"] == "applied"
    assert output["observed_worker_identity"]["configured_pool_match"] == "match"
    assert output["taskcluster"]["observed_identity_match"] == "match"
    assert output["taskcluster"]["freshness"] == "fresh"
    assert output["taskcluster"]["latest_lookup_status"] == "unknown"


def test_no_override_uses_shared_role_mapping(evidence):
    observed = evidence[0]["observed"]
    observed.update(override_present=False, override_sha256=None, puppet_override_sha_applied=None)
    output = build(evidence)
    assert output["configured_worker_type"]["worker_type"] == BASE
    assert output["configured_worker_type"]["source"] == "role_default"
    assert output["configured_worker_type"]["application_status"] == "applied"
    assert output["taskcluster"]["configured_pool_match"] == "mismatch"


@pytest.mark.parametrize(
    ("success", "expected"), [(True, "pending"), (False, "failed"), (None, "pending")]
)
def test_deployed_override_does_not_claim_application(evidence, success, expected):
    observed = evidence[0]["observed"]
    observed["puppet_override_sha_applied"] = None
    observed["puppet_success"] = success
    observed["tc_worker_identity"]["workerType"] = BASE
    output = build(evidence)
    assert output["configured_worker_type"]["application_status"] == expected
    assert output["observed_worker_identity"]["configured_pool_match"] == "mismatch"
    assert output["taskcluster"]["observed_identity_match"] == "mismatch"


@pytest.mark.parametrize("status", ["failed", "missing", None, "ok"])
def test_invalid_or_missing_identity_never_falls_back(evidence, status):
    observed = evidence[0]["observed"]
    observed["tc_worker_identity"] = None
    if status is None:
        del observed["tc_worker_identity_status"]
    else:
        observed["tc_worker_identity_status"] = status
    output = build(evidence)
    identity = output["observed_worker_identity"]
    assert identity["status"] == "unresolved"
    assert identity["worker_type"] is None
    assert identity["collection_status"] == (status or "not_collected")
    assert output["taskcluster"]["observed_identity_match"] == "unknown"


def test_failed_audit_does_not_reuse_last_success(evidence):
    evidence[0]["ok"] = False
    output = build(evidence)
    assert output["configured_worker_type"]["status"] == "unresolved"
    assert output["configured_worker_type"]["host_audit_status"] == "failed"
    assert output["observed_worker_identity"]["status"] == "unresolved"
    assert output["taskcluster"]["record_status"] == "stored_success"
    assert output["taskcluster"]["configured_pool_match"] == "unknown"


def test_unavailable_override_does_not_fall_back_to_role(evidence):
    evidence[0]["observed"]["override_sha256"] = "missing_override_sha"
    output = build(evidence)
    assert output["configured_worker_type"]["worker_type"] is None
    assert output["configured_worker_type"]["status"] == "unresolved"


@pytest.mark.parametrize(
    ("assignment", "expected"),
    [
        ("WORKER_TYPE_OVERRIDE=$POOL", None),
        ('WORKER_TYPE_OVERRIDE="${POOL}"', None),
        ("WORKER_TYPE_OVERRIDE=''", BASE),
        ("# WORKER_TYPE_OVERRIDE=other", BASE),
    ],
)
def test_nonliteral_override_is_unknown(evidence, assignment, expected):
    cache = evidence[2]
    (cache.overrides_dir / "test_override_sha"[:12]).write_text(
        f"PUPPET_BRANCH=testing\n{assignment}\n"
    )
    assert build(evidence)["configured_worker_type"]["worker_type"] == expected


def test_unknown_role_and_no_override_is_unresolved(evidence):
    evidence[0]["observed"].update(role="unmapped", override_present=False, override_sha256=None)
    configured = build(evidence)["configured_worker_type"]
    assert configured["status"] == "unresolved"
    assert configured["reason"] == "unmapped_role"


def test_override_without_content_sha_does_not_claim_application(evidence):
    evidence[0]["observed"].update(override_sha256=None, puppet_override_sha_applied=None)
    configured = build(evidence)["configured_worker_type"]
    assert configured["status"] == "unresolved"
    assert configured["application_status"] == "unknown"


@pytest.mark.parametrize(
    ("stamp", "freshness"),
    [
        ("2026-11-05T02:00:00Z", "stale"),
        ("bad", "unknown"),
        (None, "unknown"),
        ("2026-11-06T04:00:00Z", "unknown"),
    ],
)
def test_tc_freshness_keeps_old_evidence(evidence, stamp, freshness):
    evidence[1]["ts"] = stamp
    output = build(evidence)["taskcluster"]
    assert output["freshness"] == freshness
    assert output["observation_ts"] == stamp
    assert output["last_date_active"] == TS


def test_missing_evidence_is_explicit(evidence):
    output = build_monitor_json_record(
        HOST, record=None, tc_record=None, sha_cache=evidence[2], now=NOW
    )
    assert output["configured_worker_type"]["host_audit_status"] == "missing"
    assert output["taskcluster"]["record_status"] == "missing"
    assert output["taskcluster"]["freshness"] == "unknown"


@pytest.mark.parametrize("field", ["worker_group", "worker_id", "worker_type", "provisioner"])
def test_tc_matching_uses_entire_identity(evidence, field):
    evidence[1][field] = "different"
    assert build(evidence)["taskcluster"]["observed_identity_match"] == "mismatch"


def test_follow_emits_tc_only_and_failed_host_changes(evidence, mocker):
    record, tc, cache = evidence
    initial = build_monitor_json_snapshot(
        [HOST], latest={HOST: record}, tc_data={"ms017": tc}, sha_cache=cache
    )
    provider = mocker.Mock()
    updated_tc = {**tc, "last_date_active": "2026-11-05T04:05:00Z"}
    provider.load_latest_records.return_value = ({HOST: record}, {})
    provider.load_tc_workers.return_value = {"ms017": updated_tc}
    mocker.patch("fleetroll.commands.monitor.json_output.time.sleep")
    stream = follow_monitor_json([HOST], provider=provider, initial=initial, sha_cache=cache)
    changed = next(stream)
    assert changed["taskcluster"]["last_date_active"] == updated_tc["last_date_active"]
    provider.load_latest_records.return_value = ({HOST: {**record, "ok": False}}, {})
    changed = next(stream)
    assert changed["configured_worker_type"]["host_audit_status"] == "failed"


def test_once_and_initial_follow_use_same_contract(evidence, tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("HOME", str(tmp_path))
    record, tc, _ = evidence
    init_db(get_db_path())
    conn = get_connection(get_db_path())
    insert_host_observation(conn, record)
    insert_tc_worker(conn, tc)
    conn.commit()
    conn.close()
    runner = CliRunner()
    once = runner.invoke(cli, ["host", "monitor", HOST, "--once", "--json"])
    assert once.exit_code == 0, once.output
    follow = mocker.patch(
        "fleetroll.commands.monitor.entry.follow_monitor_json", return_value=iter([])
    )
    initial = runner.invoke(cli, ["host", "monitor", HOST, "--json"])
    assert initial.exit_code == 0, initial.output
    assert json.loads(once.output) == json.loads(initial.output)
    assert json.loads(once.output)[HOST]["taskcluster"]["identity"]["worker_type"] == OVERRIDE
    assert follow.call_args.kwargs["initial"] == json.loads(once.output)
