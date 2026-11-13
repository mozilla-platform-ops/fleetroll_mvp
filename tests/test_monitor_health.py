"""Vault convergence narrows the legacy monitor's positive health signal."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from fleetroll.cli import cli
from fleetroll.commands.monitor.cache import ShaInfoCache
from fleetroll.commands.monitor.data import build_row_values
from fleetroll.commands.monitor.query import apply_query, parse_query
from fleetroll.commands.web.app import create_app
from fleetroll.constants import DEFAULT_GITHUB_REPO
from fleetroll.db import (
    get_connection,
    get_db_path,
    init_db,
    insert_github_ref,
    insert_host_observation,
    insert_tc_worker,
)

VAULT_SHA = "a" * 64
OTHER_VAULT_SHA = "a" * 63 + "b"  # Same displayed prefix; different full checksum.


@pytest.fixture
def health_evidence(tmp_path):
    now = datetime.now(UTC)
    record = {
        "host": "health.example.test",
        "ts": now.isoformat(),
        "ok": True,
        "observed": {
            "os_type": "Linux",
            "role_present": True,
            "role": "gecko_t_linux_2404_talos",
            "override_present": False,
            "vault_present": True,
            "vault_sha256": VAULT_SHA,
            "puppet_vault_sha_applied": VAULT_SHA,
            "puppet_git_sha": "test_revision",
            "puppet_success": True,
            "puppet_state_ts": (now - timedelta(minutes=10)).isoformat(),
        },
    }
    return record, {
        "tc_data": {"ts": now.isoformat(), "last_date_active": now.isoformat()},
        "github_refs": {f"{DEFAULT_GITHUB_REPO}:master": {"sha": "test_revision"}},
        "sha_cache": ShaInfoCache(tmp_path / "overrides", tmp_path / "vaults"),
    }


@pytest.mark.parametrize("platform", ["Linux", "Darwin"])
@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize(
    ("applied", "healthy"),
    [(VAULT_SHA, "Y"), (OTHER_VAULT_SHA, "N"), (None, "-")],
)
def test_health_requires_full_vault_match(health_evidence, platform, override, applied, healthy):
    record, kwargs = health_evidence
    record["observed"].update(
        os_type=platform,
        override_present=override,
        puppet_vault_sha_applied=applied,
    )
    if override:
        record["observed"]["override_sha256"] = "c" * 64
        kwargs["sha_cache"].override_cache["c" * 64] = {
            "user": "test_owner",
            "repo": "test_repo",
            "branch": "test_branch",
        }
        kwargs["github_refs"] = {"test_owner/test_repo:test_branch": {"sha": "test_revision"}}
    original = deepcopy(record)
    values = build_row_values(record["host"], record, **kwargs)
    assert values["pp_match"] == "Y"  # Revision comparison is independent of the vault.
    assert values["healthy"] == healthy
    assert record == original


@pytest.mark.parametrize("field", ["vault_sha256", "puppet_vault_sha_applied"])
@pytest.mark.parametrize("value", [None, "", " ", "NA", "-", "?", False, 123, []])
def test_missing_or_invalid_vault_checksum_cannot_be_healthy(health_evidence, field, value):
    record, kwargs = health_evidence
    record["observed"][field] = value
    assert build_row_values(record["host"], record, **kwargs)["healthy"] == "-"


@pytest.mark.parametrize(
    "changes",
    [
        {"vault_sha256": None, "puppet_vault_sha_applied": None},
        {"vault_present": False, "vault_sha256": None, "puppet_vault_sha_applied": None},
        {"vault_present": False},  # A checksum conflicts with the explicit absence flag.
    ],
)
def test_absence_or_contradiction_is_not_a_positive_match(health_evidence, changes):
    record, kwargs = health_evidence
    record["observed"].update(changes)
    assert build_row_values(record["host"], record, **kwargs)["healthy"] == "-"


def test_legacy_observation_without_applied_field_is_unknown(health_evidence):
    record, kwargs = health_evidence
    del record["observed"]["puppet_vault_sha_applied"]
    assert build_row_values(record["host"], record, **kwargs)["healthy"] == "-"


@pytest.mark.parametrize("checksum", ["NA", "same_but_not_a_checksum", "a" * 12])
def test_identical_invalid_or_abbreviated_checksums_are_not_proof(health_evidence, checksum):
    record, kwargs = health_evidence
    record["observed"].update(vault_sha256=checksum, puppet_vault_sha_applied=checksum)
    assert build_row_values(record["host"], record, **kwargs)["healthy"] == "-"


def test_checksum_hex_case_does_not_change_file_identity(health_evidence):
    record, kwargs = health_evidence
    record["observed"]["puppet_vault_sha_applied"] = VAULT_SHA.upper()
    assert build_row_values(record["host"], record, **kwargs)["healthy"] == "Y"


@pytest.mark.parametrize("success", [False, None])
def test_equal_vaults_do_not_override_puppet_failure_or_missing_success(health_evidence, success):
    record, kwargs = health_evidence
    record["observed"]["puppet_success"] = success
    assert build_row_values(record["host"], record, **kwargs)["healthy"] != "Y"


def test_equal_vaults_do_not_replace_expected_revision_evidence(health_evidence):
    record, kwargs = health_evidence
    kwargs["github_refs"] = {}
    assert build_row_values(record["host"], record, **kwargs)["healthy"] == "-"


def test_failed_gather_retains_historical_row_without_refreshing_its_time(health_evidence):
    last_ok, kwargs = health_evidence
    last_ok["ts"] = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    last_ok["observed"]["puppet_vault_sha_applied"] = OTHER_VAULT_SHA
    failed = {"ok": False, "ts": datetime.now(UTC).isoformat(), "error": "ssh failed"}
    original = deepcopy(last_ok)
    values = build_row_values(last_ok["host"], failed, last_ok=last_ok, **kwargs)
    assert values["status"] == "FAIL"
    assert values["healthy"] == "N"
    assert "3h" in values["data"]
    assert last_ok == original


@pytest.mark.parametrize("vault", [None, OTHER_VAULT_SHA])
def test_windows_keeps_its_independent_pool_hash_semantics(health_evidence, vault):
    record, kwargs = health_evidence
    record["observed"].update(
        os_type="Windows",
        role="testwindowspool",
        puppet_git_sha="testwin_revision",
        puppet_success=None,
        vault_sha256=vault,
        puppet_vault_sha_applied=None,
    )
    kwargs["windows_pools"] = {"test-windows-pool": {"hash": "testwin_revision"}}
    values = build_row_values(record["host"], record, **kwargs)
    assert values["pp_match"] == "Y"
    assert values["healthy"] == "Y"


def test_queries_distinguish_vault_mismatch_and_unknown(health_evidence):
    record, kwargs = health_evidence
    rows = []
    for host, applied in [("matched", VAULT_SHA), ("mismatch", OTHER_VAULT_SHA), ("unknown", None)]:
        record["observed"]["puppet_vault_sha_applied"] = applied
        rows.append(build_row_values(host, record, **kwargs))
    assert [r["host"] for r in apply_query(rows, parse_query("healthy=y"))] == ["matched"]
    assert [r["host"] for r in apply_query(rows, parse_query("healthy=n"))] == ["mismatch"]
    assert [r["host"] for r in apply_query(rows, parse_query("healthy!=y"))] == [
        "mismatch",
        "unknown",
    ]
    assert [r["host"] for r in apply_query(rows, parse_query("healthy="))] == ["unknown"]


@pytest.mark.parametrize("surface", ["cli", "web"])
def test_cli_json_selection_and_web_rows_use_the_same_vault_gate(
    health_evidence,
    tmp_path,
    monkeypatch,
    surface,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    record, kwargs = health_evidence
    init_db(get_db_path())
    conn = get_connection(get_db_path())
    hosts = {}
    try:
        owner, repo = DEFAULT_GITHUB_REPO.split("/")
        insert_github_ref(
            conn,
            {
                "owner": owner,
                "repo": repo,
                "branch": "master",
                "ts": record["ts"],
                "sha": "test_revision",
            },
        )
        for name, applied in [
            ("matched", VAULT_SHA),
            ("mismatch", OTHER_VAULT_SHA),
            ("unknown", None),
        ]:
            host = f"health-{name}.example.test"
            hosts[name] = host
            observation = deepcopy(record)
            observation["host"] = host
            observation["observed"]["puppet_vault_sha_applied"] = applied
            insert_host_observation(conn, observation)
            insert_tc_worker(conn, {**kwargs["tc_data"], "host": host})
        conn.commit()
    finally:
        conn.close()
    host_file = tmp_path / "hosts.list"
    host_file.write_text("\n".join(hosts.values()) + "\n")
    expected_queries = {
        "healthy=y": {hosts["matched"]},
        "healthy=n": {hosts["mismatch"]},
        "healthy!=y": {hosts["mismatch"], hosts["unknown"]},
        "healthy=": {hosts["unknown"]},
    }
    if surface == "cli":
        runner = CliRunner()
        for query, expected in expected_queries.items():
            result = runner.invoke(
                cli, ["host", "monitor", str(host_file), "--once", "--json", "--filter", query]
            )
            assert result.exit_code == 0, result.output
            payload = json.loads(result.output)
            assert set(payload) == expected
            for item in payload.values():
                assert item["ts"] == record["ts"]
                assert item["observed"]["vault_sha256"] == VAULT_SHA
                assert item["worker_evidence_schema_version"] == 3
                assert "verification" not in item  # The design is not a shipped JSON verdict.
    else:
        with TestClient(create_app()) as client:
            for query, expected in expected_queries.items():
                result = client.get("/api/hosts", params={"filter": query})
                assert result.status_code == 200, result.text
                rows = result.json()["rows"]
                assert {row["host"] for row in rows} == expected
                assert all(row["pp_match"] == "Y" for row in rows)
