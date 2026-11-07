"""Recorded selection at CLI boundaries; collection targets never change mid-run."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from fleetroll.cli import cli
from fleetroll.commands.monitor.data import build_row_values
from fleetroll.commands.monitor.query import apply_query, parse_query
from fleetroll.db import (
    get_connection,
    get_latest_tc_workers,
    init_db,
    insert_host_observation,
    insert_tc_worker,
)
from fleetroll.taskcluster import WorkerFetchResult

TS = "2026-11-06T00:00:00+00:00"
LATER = "2026-11-06T01:00:00+00:00"
PRESENT = "present.example.com"
ABSENT = "absent.example.com"
UNKNOWN = "unknown.example.com"


def seed(conn, host, *, present=None, ts=TS, ok=True):
    observed = {"role_present": True, "role": "gecko_t_linux_talos", "os_type": "Linux"}
    if present is not None:
        observed["override_present"] = present
    insert_host_observation(conn, {"host": host, "ts": ts, "ok": ok, "observed": observed})
    conn.commit()


@pytest.fixture
def selection_env(tmp_path, monkeypatch, mocker):
    path = tmp_path / "fleetroll.db"
    init_db(path, audit_log=tmp_path / "audit.jsonl")
    monkeypatch.setattr("fleetroll.db.get_db_path", lambda: path)
    monkeypatch.setattr("fleetroll.selection.default_notes_path", lambda: tmp_path / "notes.jsonl")
    conn = get_connection(path)
    seed(conn, PRESENT, present=True)
    seed(conn, ABSENT, present=False)
    seed(conn, UNKNOWN)
    calls = {
        name: mocker.patch(f"fleetroll.cli.cmd_{name}")
        for name in ("host_audit", "tc_fetch", "gh_fetch")
    }
    yield path, conn, calls
    conn.close()


@pytest.mark.parametrize(
    "action",
    [
        ["gather", "host"],
        ["gather", "tc"],
        ["gather", "all"],
        ["gather"],
        ["gather-host"],
        ["gather-tc"],
    ],
)
def test_filter_without_host_selects_database_population(selection_env, action):
    _, _, calls = selection_env
    result = CliRunner().invoke(cli, [*action, "--filter", "override=present"])
    assert result.exit_code == 0, result.output
    assert "Selected 1/3 hosts from local database" in result.stderr
    for name in ("host_audit", "tc_fetch"):
        if calls[name].called:
            assert calls[name].call_args.args[0].hosts == [PRESENT]
    assert calls["host_audit"].called or calls["tc_fetch"].called


def test_explicit_file_restricts_database_matches(selection_env, tmp_path):
    _, _, calls = selection_env
    subset = tmp_path / "subset.list"
    subset.write_text(ABSENT + "\n")
    result = CliRunner().invoke(cli, ["gather", "all", str(subset), "--filter", "override=present"])
    assert result.exit_code == 0, result.output
    assert "Selected 0/1" in result.stderr
    assert all(not call.called for call in calls.values())


def test_saved_filter_and_selection_order(selection_env, tmp_path):
    _, _, calls = selection_env
    saved = tmp_path / "filter.yaml"
    saved.write_text("query: override=present|absent sort:host:desc\n")
    result = CliRunner().invoke(cli, ["gather", "host", "--filter-file", str(saved)])
    assert result.exit_code == 0, result.output
    assert calls["host_audit"].call_args.args[0].hosts == [PRESENT, ABSENT]


def test_combined_selection_is_frozen_before_host_collection(selection_env):
    _, conn, calls = selection_env

    def change_configuration(args):
        seed(conn, PRESENT, present=False, ts=LATER)
        seed(conn, ABSENT, present=True, ts=LATER)
        args.hosts.clear()  # Subcommands do not share a mutable target list either.

    calls["host_audit"].side_effect = change_configuration
    result = CliRunner().invoke(cli, ["gather", "all", "--filter", "override=present"])
    assert result.exit_code == 0, result.output
    assert calls["tc_fetch"].call_args.args[0].hosts == [PRESENT]
    # A subsequent invocation reevaluates membership.
    result = CliRunner().invoke(cli, ["gather", "tc", "--filter", "override=present"])
    assert result.exit_code == 0, result.output
    assert calls["tc_fetch"].call_args.args[0].hosts == [ABSENT]


def test_failed_audit_retains_last_successful_override(selection_env):
    _, conn, calls = selection_env
    seed(conn, PRESENT, present=False, ts=LATER, ok=False)
    result = CliRunner().invoke(cli, ["gather", "host", "--filter", "override=present"])
    assert result.exit_code == 0, result.output
    assert calls["host_audit"].call_args.args[0].hosts == [PRESENT]
    assert "may be stale" in result.stderr


@pytest.mark.parametrize(
    "expression", ["", "bogus", "unknown=value", "override=present broken", "override~"]
)
def test_invalid_filter_never_collects(selection_env, expression):
    _, _, calls = selection_env
    result = CliRunner().invoke(cli, ["gather", "all", "--filter", expression])
    assert result.exit_code == 2, result.output
    assert all(not call.called for call in calls.values())


@pytest.mark.parametrize("content", ["query: [bad", "- value", "query: 42", "query: ''"])
def test_invalid_saved_filter_never_collects(selection_env, tmp_path, content):
    _, _, calls = selection_env
    saved = tmp_path / "bad.yaml"
    saved.write_text(content)
    result = CliRunner().invoke(cli, ["gather", "all", "--filter-file", str(saved)])
    assert result.exit_code == 2, result.output
    assert all(not call.called for call in calls.values())


def test_conflicting_filters_never_collect(selection_env, tmp_path):
    _, _, calls = selection_env
    saved = tmp_path / "filter.yaml"
    saved.write_text("query: override=present\n")
    result = CliRunner().invoke(
        cli, ["gather", "all", "--filter-file", str(saved), "--filter", "override=present"]
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output
    assert all(not call.called for call in calls.values())


@pytest.mark.parametrize("action", ["host", "tc", "all"])
def test_missing_host_and_filter_is_usage_error(selection_env, action):
    _, _, calls = selection_env
    result = CliRunner().invoke(cli, ["gather", action])
    assert result.exit_code == 2
    assert "Specify HOST_OR_FILE" in result.output
    assert all(not call.called for call in calls.values())


def test_missing_database_and_no_matches_have_no_collection(selection_env, tmp_path, monkeypatch):
    _, _, calls = selection_env
    missing = tmp_path / "missing.db"
    monkeypatch.setattr("fleetroll.db.get_db_path", lambda: missing)
    result = CliRunner().invoke(cli, ["gather", "all", "--filter", "override=present", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"status": "no_matches", "results": []}
    assert "Selected 0/0" in result.stderr
    assert not missing.exists()
    assert all(not call.called for call in calls.values())


def test_tc_only_database_host_is_selectable(selection_env):
    _, conn, calls = selection_env
    insert_tc_worker(conn, {"host": "tc-only.example.com", "type": "worker", "ts": TS})
    conn.commit()
    result = CliRunner().invoke(cli, ["gather", "host", "--filter", "host~tc-only"])
    assert result.exit_code == 0, result.output
    assert calls["host_audit"].call_args.args[0].hosts == ["tc-only.example.com"]


def test_short_hostname_selection_keeps_json_stdout_clean(selection_env):
    _, _, calls = selection_env
    result = CliRunner().invoke(
        cli, ["gather", "host", "ms001", "--filter", "override=present", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "no_matches"
    assert "Selected 0/1" in result.stderr
    assert all(not call.called for call in calls.values())


def test_branch_note_and_pool_filters_share_monitor_rows(selection_env, tmp_path):
    _, conn, calls = selection_env
    overrides = tmp_path / "overrides"
    overrides.mkdir()
    sha = "test_override_sha_1234"
    (overrides / sha[:12]).write_text(
        "PUPPET_REPO='https://github.com/example/ronin_puppet.git'\n"
        "PUPPET_BRANCH='test-filter-branch'\n"
    )
    insert_host_observation(
        conn,
        {
            "host": PRESENT,
            "ts": LATER,
            "ok": True,
            "observed": {"override_present": True, "override_sha256": sha},
        },
    )
    insert_tc_worker(
        conn,
        {
            "host": PRESENT,
            "type": "worker",
            "ts": TS,
            "worker_type": "test-pool",
        },
    )
    conn.commit()
    (tmp_path / "notes.jsonl").write_text(
        json.dumps(
            {
                "action": "host.note_add",
                "host": PRESENT,
                "note": "selected rollout",
            }
        )
        + "\n"
    )
    result = CliRunner().invoke(
        cli,
        [
            "gather",
            "host",
            "--filter",
            "ovr_bch~test-filter-branch note~selected pool=test-pool",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls["host_audit"].call_args.args[0].hosts == [PRESENT]


@pytest.mark.parametrize(
    ("present", "expected"), [(True, "present"), (False, "absent"), (None, "?")]
)
def test_override_field_uses_presence_not_checksum(present, expected):
    observed = {} if present is None else {"override_present": present}
    row = build_row_values(PRESENT, {"ok": True, "observed": observed})
    assert row["override"] == expected
    for value in ("present", "absent", "?"):
        assert bool(apply_query([row], parse_query(f"override={value}"))) == (value == expected)


def test_filtered_tc_dispatch_collects_every_selected_host(tmp_path, monkeypatch, mocker):
    path = tmp_path / "fleetroll.db"
    init_db(path, audit_log=tmp_path / "audit.jsonl")
    monkeypatch.setattr("fleetroll.db.get_db_path", lambda: path)
    monkeypatch.setattr("fleetroll.selection.default_notes_path", lambda: tmp_path / "notes.jsonl")
    mocker.patch("fleetroll.commands.gather_tc.load_tc_credentials", return_value={})
    mocker.patch("fleetroll.commands.gather_tc.create_tc_clients", return_value={})
    mocker.patch(
        "fleetroll.commands.gather_tc.default_audit_log_path", return_value=tmp_path / "audit.jsonl"
    )
    mocker.patch("fleetroll.github.do_github_fetch")
    conn = get_connection(path)
    try:
        for host in (PRESENT, ABSENT):
            record = {
                "host": host,
                "ts": TS,
                "ok": True,
                "observed": {
                    "override_present": True,
                    "tc_worker_identity_status": "ok",
                    "tc_worker_identity": {
                        "provisionerId": "releng-hardware",
                        "workerType": "test-pool",
                        "workerGroup": "test-group",
                        "workerId": host.split(".")[0],
                    },
                },
            }
            insert_host_observation(conn, record)
        conn.commit()
        fetch = mocker.patch(
            "fleetroll.commands.gather_tc.fetch_workers",
            return_value=WorkerFetchResult(
                workers=[
                    {"workerId": h.split(".")[0], "workerGroup": "test-group", "lastDateActive": TS}
                    for h in (PRESENT, ABSENT)
                ],
                status_errors=0,
            ),
        )
        result = CliRunner().invoke(
            cli, ["gather", "tc", "--filter", "override=present", "--quiet"]
        )
        assert result.exit_code == 0, result.output
        assert fetch.call_args.kwargs["worker_identities"] == {
            ("test-group", "present"),
            ("test-group", "absent"),
        }
        assert set(get_latest_tc_workers(conn, [PRESENT, ABSENT])) == {PRESENT, ABSENT}
    finally:
        conn.close()
