"""Configuration capture and typed provenance cannot manufacture convergence proof."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fleetroll.audit import process_audit_result
from fleetroll.configuration_evidence import collect_evidence, new_attempt
from fleetroll.db import get_connection, get_latest_host_observations, init_db

RUBY = shutil.which("ruby")
COLLECTOR = Path(__file__).parents[1] / "fleetroll" / "configuration_snapshot.rb"


def metadata(**updates):
    return {
        "schema_version": 1,
        "ts": "2026-11-16T12:00:00Z",
        "success": True,
        "exit_code": 2,
        "duration_s": 30,
        "git_sha": "a" * 40,
        "git_repo": "https://example.com/puppet.git",
        "git_branch": "main",
        "git_dirty": False,
        "override_sha": None,
        "vault_sha": None,
        **updates,
    }


def collect(paths, *, action="none", timeout_s=10):
    if RUBY is None:
        pytest.skip("Ruby required for the remote collector")
    program = """request = JSON.parse(STDIN.read)
callback = lambda do
  case request['action']
  when 'replace_vault'
    File.write(request['paths']['vault'] + '.new', 'changed-vault')
    File.rename(request['paths']['vault'] + '.new', request['paths']['vault'])
  when 'change_puppet'
    File.write(request['paths']['puppet'], '{}')
  when 'timeout'
    sleep 0.1
  when 'remove_override'
    File.unlink(request['paths']['override'])
  end
end
snapshot = FleetrollConfigurationSnapshot.collect(
  paths: request['paths'], timeout_s: request['timeout'], between_reads: callback)
puts JSON.generate(snapshot)
"""
    result = subprocess.run(
        [RUBY, "-r", str(COLLECTOR), "-e", program],
        input=json.dumps(
            {"paths": {k: str(v) for k, v in paths.items()}, "action": action, "timeout": timeout_s}
        ),
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
    )
    assert "FAKE_VAULT_BYTES" not in result.stdout
    return json.loads(result.stdout)


@pytest.fixture
def paths(tmp_path):
    result = {name: tmp_path / name for name in ("puppet", "override", "vault")}
    result["puppet"].write_text(json.dumps(metadata()))
    result["override"].write_bytes(b"PUPPET_BRANCH=test\n")
    result["vault"].write_bytes(b"FAKE_VAULT_BYTES")
    return result


def normalize(snapshot, *, ssh_rc=0):
    return collect_evidence(
        {"OS_TYPE": "Linux", "CONFIG_SNAPSHOT_JSON": json.dumps(snapshot)},
        ssh_rc=ssh_rc,
        attempt=new_attempt(),
    )


def output(snapshot):
    return "OS_TYPE=Linux\nCONFIG_SNAPSHOT_JSON=" + json.dumps(snapshot) + "\n"


def test_capture_regular_files_and_same_run_metadata(paths):
    snapshot = collect(paths)
    evidence = normalize(snapshot)
    assert evidence["attempt"]["status"] == "completed"
    assert evidence["snapshot"]["consistency"] == "stable"
    for name, path in paths.items():
        component = evidence["components"][name]
        assert component["status"] == "present"
        assert component["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert component["attempt_id"] == evidence["attempt"]["id"]
        assert component["observed_at"] <= component["confirmed_at"]
    puppet = evidence["components"]["puppet"]
    assert puppet["values"] == {k: v for k, v in metadata().items() if k != "schema_version"}
    assert puppet["run_provenance"]["exclusive"] is False
    assert puppet["run_provenance"]["invocation_id"] is None
    assert puppet["applied_files"]["vault"]["status"] == "unknown"
    assert "content_b64" not in evidence["components"]["vault"]


def test_explicit_file_absence_is_not_unreadable_or_missing_checksum(paths):
    paths["override"].unlink()
    paths["vault"].unlink()
    evidence = normalize(collect(paths))
    assert evidence["attempt"]["status"] == "completed"
    for name in ("override", "vault"):
        assert evidence["components"][name]["status"] == "absent"
        assert "sha256" not in evidence["components"][name]
        # v1 applied nulls cannot prove that Puppet ran without the file.
        assert evidence["components"]["puppet"]["applied_files"][name]["status"] == "unknown"


@pytest.mark.parametrize(
    ("action", "component"),
    [("replace_vault", "vault"), ("change_puppet", "puppet"), ("remove_override", "override")],
)
def test_changed_sources_are_not_a_coherent_snapshot(paths, action, component):
    evidence = normalize(collect(paths, action=action))
    assert evidence["attempt"]["status"] == "partial"
    assert evidence["snapshot"]["consistency"] == "changed"
    item = evidence["components"][component]
    assert item["status"] == "invalid"
    assert item["reason"] == "file_changed_during_capture"
    assert item["observed_at"] is None


def test_bounded_capture_timeout_does_not_refresh_components(paths):
    evidence = normalize(collect(paths, action="timeout", timeout_s=0.01))
    assert evidence["attempt"]["status"] == "failed"
    for item in evidence["components"].values():
        assert item["status"] == "not_collected"
        assert item["reason"] == "snapshot_timeout"
        assert item["observed_at"] is None


@pytest.mark.parametrize("kind", ["directory", "fifo", "dangling", "oversized"])
def test_invalid_files_are_bounded_and_explicit(paths, kind):
    path = paths["override"]
    path.unlink()
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "dangling":
        path.symlink_to(path.parent / "missing-target")
    else:
        path.write_bytes(b"x" * 1_048_577)
    evidence = normalize(collect(paths))
    assert evidence["attempt"]["status"] == "partial"
    item = evidence["components"]["override"]
    assert item["status"] == ("unreadable" if kind == "dangling" else "invalid")
    assert item["observed_at"] is None
    assert evidence["components"]["vault"]["status"] == "present"


def test_permission_denial_does_not_mean_absence(paths):
    if os.geteuid() == 0:
        pytest.skip("Root can read chmod-000 fixtures")
    paths["vault"].chmod(0)
    try:
        evidence = normalize(collect(paths))
    finally:
        paths["vault"].chmod(0o600)
    assert evidence["components"]["vault"]["status"] == "unreadable"


@pytest.mark.parametrize("version", [None, 2, True, "1", 1.0])
def test_unsupported_producer_versions_are_preserved_not_interpreted(paths, version):
    state = metadata(schema_version=version)
    paths["puppet"].write_text(json.dumps(state))
    evidence = normalize(collect(paths))
    puppet = evidence["components"]["puppet"]
    assert puppet["status"] == "unsupported"
    assert puppet["raw"] == state
    assert puppet["values"] == {}


@pytest.mark.parametrize(
    "updates",
    [
        {"success": False},
        {"success": "true"},
        {"exit_code": True},
        {"exit_code": 256},
        {"duration_s": -1},
        {"ts": "2026-11-16T12:00:00"},
        {"vault_sha": "invalid-checksum"},
        {"git_dirty": "false"},
    ],
)
def test_invalid_or_inconsistent_metadata_is_not_filled_from_other_sources(paths, updates):
    paths["puppet"].write_text(json.dumps(metadata(**updates)))
    puppet = normalize(collect(paths))["components"]["puppet"]
    assert puppet["status"] == "invalid"
    assert puppet["values"] == {}


def test_omitted_fields_are_distinct_from_null_and_reported_run_id_is_not_exclusive(paths):
    state = metadata(run_id="test-reported-run")
    del state["override_sha"]
    paths["puppet"].write_text(json.dumps(state))
    puppet = normalize(collect(paths))["components"]["puppet"]
    assert "override_sha" not in puppet["fields_present"]
    assert "vault_sha" in puppet["fields_present"]
    assert "override_sha" not in puppet["values"]
    assert puppet["values"]["vault_sha"] is None
    assert puppet["applied_files"]["override"]["status"] == "not_collected"
    assert puppet["applied_files"]["vault"]["status"] == "unknown"
    assert puppet["run_provenance"]["reported_run_id"] == "test-reported-run"
    assert puppet["run_provenance"]["exclusive"] is False


def test_failed_puppet_run_is_valid_collection_evidence(paths):
    paths["puppet"].write_text(json.dumps(metadata(exit_code=4, success=False)))
    evidence = normalize(collect(paths))
    assert evidence["attempt"]["status"] == "completed"
    assert evidence["components"]["puppet"]["status"] == "present"
    assert evidence["components"]["puppet"]["values"]["success"] is False


@pytest.mark.parametrize("number", ["NaN", "1e999"])
def test_nonfinite_additive_metadata_cannot_break_json_consumers(paths, number):
    text = json.dumps(metadata())[:-1] + ', "extra": ' + number + "}"
    paths["puppet"].write_text(text)
    evidence = normalize(collect(paths))
    assert evidence["components"]["puppet"]["status"] == "invalid"
    assert evidence["components"]["vault"]["status"] == "present"
    json.dumps(evidence, allow_nan=False)


@pytest.mark.parametrize(
    "payload", ["not json", "[]", "null", '{"schema_version":1,"success":true,"success":false}']
)
def test_malformed_metadata_cannot_borrow_legacy_fields(paths, db_conn, payload):
    paths["puppet"].write_text(payload)
    snapshot = collect(paths)
    out = (
        output(snapshot)
        + """PP_SUCCESS=1
PP_GIT_SHA=legacy-revision
PP_LAST_RUN_EPOCH=1700000000
"""
    )
    result = process_audit_result(
        "host.example.com", rc=0, out=out, err="", db_conn=db_conn, actor="test"
    )
    puppet = result["observed"]["configuration_evidence"]["components"]["puppet"]
    assert puppet["status"] == "invalid"
    assert puppet["values"] == {}
    assert result["ok"] is True  # Collection quality is separate from legacy SSH success.


def test_partial_and_failed_attempts_retain_original_components_through_reload(
    paths, db_conn, mocker
):
    snapshot = collect(paths)
    host = "host.example.com"
    mocker.patch(
        "fleetroll.audit.utc_now_iso",
        side_effect=["2026-11-17T01:00:00Z", "2026-11-17T01:01:00Z", "2026-11-17T01:02:00Z"],
    )
    first = process_audit_result(
        host, rc=0, out=output(snapshot), err="", db_conn=db_conn, actor="test"
    )
    original = copy.deepcopy(first["observed"]["configuration_evidence"])
    snapshot["components"]["vault"] = {
        "status": "unreadable",
        "reason": "file_read_failed",
        "consistency": "unknown",
        "observed_at": None,
    }
    snapshot["consistency"] = "unknown"
    second = process_audit_result(
        host, rc=0, out=output(snapshot), err="", db_conn=db_conn, actor="test"
    )
    evidence = second["observed"]["configuration_evidence"]
    assert evidence["attempt"]["id"] != original["attempt"]["id"]
    assert evidence["components"]["vault"]["observed_at"] is None
    assert evidence["retained_components"]["vault"] == original["components"]["vault"]
    process_audit_result(host, rc=255, out="", err="failed", db_conn=db_conn, actor="test")
    latest, _ = get_latest_host_observations(db_conn, [host])
    failed = latest[host]["observed"]["configuration_evidence"]
    assert failed["attempt"]["status"] == "failed"
    assert len(failed["retained_components"]) == 3
    assert failed["retained_components"]["vault"] == original["components"]["vault"]
    assert failed["retained_components"]["puppet"]["attempt_id"] == evidence["attempt"]["id"]


def test_old_observations_supply_no_new_absence_proof(db_conn):
    result = process_audit_result(
        "host.example.com",
        rc=0,
        out="OVERRIDE_PRESENT=0\nVLT_PRESENT=0\n",
        err="",
        db_conn=db_conn,
        actor="test",
    )
    evidence = result["observed"]["configuration_evidence"]
    assert evidence["attempt"]["status"] == "failed"
    assert all(item["status"] == "not_collected" for item in evidence["components"].values())


@pytest.mark.parametrize("os_type", ["Linux", "Darwin"])
def test_real_embedded_entry_point_uses_platform_paths_without_exporting_files(paths, os_type):
    if RUBY is None:
        pytest.skip("Ruby required")
    root = paths["puppet"].parent
    (root / "last_run_metadata.json").write_bytes(paths["puppet"].read_bytes())
    (root / "ronin_settings").write_bytes(paths["override"].read_bytes())
    source = (
        COLLECTOR.read_text()
        .replace("/opt/puppet_environments", str(root))
        .replace("/etc/puppet", str(root))
    )
    source = source.replace("/var/root/vault.yaml", str(paths["vault"])).replace(
        "/root/vault.yaml", str(paths["vault"])
    )
    result = subprocess.run(
        [RUBY, "-e", source, os_type], check=True, capture_output=True, text=True, timeout=5
    )
    snapshot = json.loads(result.stdout)
    assert normalize(snapshot)["attempt"]["status"] == "completed"
    assert snapshot["components"]["puppet"]["path"] == str(root / "last_run_metadata.json")
    assert "FAKE_VAULT_BYTES" not in result.stdout


@pytest.mark.parametrize(
    "updates",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"method": "unknown"},
        {"started_at": "not-a-date"},
        {"finished_at": "2020-01-01T00:00:00Z"},
        {"components": []},
    ],
)
def test_invalid_snapshot_contracts_fail_unknown(paths, updates):
    snapshot = {**collect(paths), **updates}
    evidence = normalize(snapshot)
    assert evidence["attempt"]["status"] == "failed"
    assert all(c["status"] == "not_collected" for c in evidence["components"].values())


def test_legacy_json_never_borrows_individual_null_or_missing_fields(db_conn):
    encoded = base64.b64encode(
        json.dumps({"ts": "2026-11-16T12:00:00Z", "vault_sha": None}).encode()
    ).decode()
    out = f"PP_STATE_JSON={encoded}\nPP_VAULT_SHA_APPLIED=legacy-value\nPP_SUCCESS=1\nPP_EXIT_CODE=0\n"
    result = process_audit_result(
        "host.example.com", rc=0, out=out, err="", db_conn=db_conn, actor="test"
    )
    assert result["observed"]["puppet_success"] is None
    assert result["observed"]["puppet_exit_code"] is None
    assert result["observed"]["puppet_vault_sha_applied"] is None


def test_component_evidence_survives_retention_and_monitor_json(paths, db_conn, mocker, tmp_path):
    from fleetroll.commands.monitor.cache import ShaInfoCache
    from fleetroll.commands.monitor.json_output import build_monitor_json_record

    snapshot = collect(paths)
    host = "host.example.com"
    first = process_audit_result(
        host,
        rc=0,
        out=output(snapshot),
        err="",
        db_conn=db_conn,
        actor="test",
        collection_attempt=new_attempt(),
    )
    original = copy.deepcopy(first["observed"]["configuration_evidence"]["components"])
    for _ in range(13):
        process_audit_result(
            host,
            rc=255,
            out="",
            err="failed",
            db_conn=db_conn,
            actor="test",
            collection_attempt=new_attempt(),
        )
    latest, latest_ok = get_latest_host_observations(db_conn, [host])
    retained = latest[host]["observed"]["configuration_evidence"]
    assert retained["retained_components"] == original
    assert retained["attempt"]["id"] != first["observed"]["configuration_evidence"]["attempt"]["id"]
    assert latest_ok[host]["ts"] == first["ts"]
    assert (
        db_conn.execute(
            "SELECT COUNT(*) FROM host_observations WHERE host = ?", (host,)
        ).fetchone()[0]
        == 11
    )
    cache = ShaInfoCache(tmp_path / "overrides", tmp_path / "vault")
    monitor = build_monitor_json_record(host, record=latest[host], tc_record=None, sha_cache=cache)
    assert monitor["worker_evidence_schema_version"] == 3
    assert json.loads(json.dumps(monitor))["observed"]["configuration_evidence"] == retained


@pytest.mark.parametrize("failure", ["retry_exhausted", "local_exception", "batch_expired"])
def test_gather_failures_persist_attempt_and_keep_success(
    paths, tmp_path, mocker, mock_args_audit, failure
):
    from fleetroll.commands.gather_host import audit_single_host_with_retry

    path = tmp_path / "gather.db"
    init_db(path)
    host = "host.example.com"
    with get_connection(path) as conn:
        first = process_audit_result(
            host,
            rc=0,
            out=output(collect(paths)),
            err="",
            db_conn=conn,
            actor="test",
            collection_attempt=new_attempt(),
        )
    run = mocker.patch(
        "fleetroll.commands.gather_host.run_ssh", return_value=(255, "", "Connection refused")
    )
    if failure == "local_exception":
        run.side_effect = OSError("test SSH execution error")
    mocker.patch("fleetroll.commands.gather_host.AUDIT_MAX_RETRIES", 2)
    mocker.patch("fleetroll.commands.gather_host.time.sleep")
    result = audit_single_host_with_retry(
        host,
        args=mock_args_audit,
        ssh_opts=[],
        include_content=False,
        db_path=path,
        actor="test",
        retry_budget={"deadline": time.time() + (-1 if failure == "batch_expired" else 60)},
        lock=threading.Lock(),
        log_lock=threading.Lock(),
    )
    assert result["ok"] is False
    evidence = result["observed"]["configuration_evidence"]
    assert evidence["attempt"]["status"] == "failed"
    assert (
        evidence["retained_components"] == first["observed"]["configuration_evidence"]["components"]
    )
    with get_connection(path) as conn:
        rows = conn.execute(
            "SELECT data FROM host_observations WHERE host = ? ORDER BY ts", (host,)
        ).fetchall()
    assert len(rows) == (3 if failure == "retry_exhausted" else 2)
    attempt_ids = {
        json.loads(row[0])["observed"]["configuration_evidence"]["attempt"]["id"] for row in rows
    }
    assert len(attempt_ids) == len(rows)
    assert evidence["attempt"]["started_at"] <= evidence["attempt"]["finished_at"]


def test_gather_and_monitor_cli_preserve_current_and_retained_evidence(
    paths, tmp_path, monkeypatch, mocker
):
    from click.testing import CliRunner
    from fleetroll.cli import cli

    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    host = "host.example.com"
    run = mocker.patch(
        "fleetroll.commands.gather_host.run_ssh", return_value=(0, output(collect(paths)), "")
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["gather", "host", host, "--json"])
    assert result.exit_code == 0, result.output
    first = json.loads(result.output)["observed"]["configuration_evidence"]
    assert first["attempt"]["status"] == "completed"
    run.return_value = (255, "", "Connection refused")
    result = runner.invoke(cli, ["gather", "host", host, "--json"])
    assert result.exit_code != 0
    failed = json.loads(result.output)["observed"]["configuration_evidence"]
    assert failed["attempt"]["status"] == "failed"
    result = runner.invoke(cli, ["host", "monitor", host, "--once", "--json"])
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)[host]
    assert record["worker_evidence_schema_version"] == 3
    assert record["observed"]["configuration_evidence"] == failed
    assert failed["retained_components"] == first["components"]
