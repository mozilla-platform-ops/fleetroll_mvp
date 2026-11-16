"""Population removal freezes reviewed evidence and checks the live file identity."""

import json
import os
import shlex
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from click.testing import CliRunner
from fleetroll.cli import cli
from fleetroll.db import get_connection, get_db_path, init_db, insert_host_observation
from fleetroll.override_removal import load_removal_plan
from fleetroll.ssh import remote_unset_script
from fleetroll.utils import sha256_hex

CONTENT = 'PUPPET_BRANCH="test-release"\nWORKER_TYPE_OVERRIDE="test-pool"\n'
CHECKSUM = sha256_hex(CONTENT.encode())
HOST = "selected.example.test"
OTHER = "other.example.test"
OLD = (datetime.now(UTC) - timedelta(days=2)).isoformat()
NOW = datetime.now(UTC).isoformat()


def seed(conn, host=HOST, *, checksum=CHECKSUM, present=True, ok=True, stamp=OLD, os_type="Linux"):
    insert_host_observation(
        conn,
        {
            "host": host,
            "ts": stamp,
            "ok": ok,
            "observed": {
                "override_present": present,
                "override_sha256": checksum,
                "os_type": os_type,
            },
        },
    )
    conn.commit()


@pytest.fixture
def env(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("HOME", str(tmp_path))
    init_db(get_db_path())
    overrides = get_db_path().parent / "overrides"
    overrides.mkdir(exist_ok=True)
    (overrides / CHECKSUM[:12]).write_text(CONTENT)
    conn = get_connection(get_db_path())
    seed(conn)
    seed(conn, OTHER, present=False, checksum=None)
    ssh = mocker.patch("fleetroll.commands.unset.run_ssh")
    ssh.return_value = (0, f"UNSET_STATUS=removed\nREMOVED=1\nCURRENT_SHA256={CHECKSUM}\n", "")
    refresh = mocker.patch("fleetroll.commands._auto_audit._run_auto_audit")
    yield conn, ssh, refresh, tmp_path / "plan.json"
    conn.close()


def invoke(*args):
    return CliRunner().invoke(cli, ["override", "unset", *args])


def test_preview_shows_exact_identity_and_original_failed_evidence(env):
    conn, ssh, refresh, path = env
    seed(conn, ok=False, checksum=None, stamp=NOW)
    result = invoke("--filter", "ovr_bch=test-release", "--json", "--save-plan", str(path))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    target = payload["plan"]["targets"][0]
    assert target["host"] == HOST
    assert target["branch"] == "test-release"
    assert target["checksum"] == CHECKSUM
    assert target["observation_ts"] == OLD
    assert target["latest_attempt_ts"] == NOW
    assert target["latest_attempt_ok"] is False
    assert target["freshness"] == "stale"
    assert load_removal_plan(path).targets[0].checksum == CHECKSUM
    ssh.assert_not_called()
    refresh.assert_not_called()
    assert not (path.parent / ".fleetroll" / "audit.jsonl").exists()


def test_human_preview_lists_every_selected_host(env):
    conn, ssh, refresh, path = env
    for i in range(8):
        seed(conn, f"selected-{i}.example.test")
    result = invoke("--filter", "override=present")
    assert result.exit_code == 0, result.output
    for i in range(8):
        assert f"selected-{i}.example.test" in result.stdout
    assert CHECKSUM in result.stdout
    assert OLD in result.stdout
    assert "stale" in result.stdout
    ssh.assert_not_called()


def test_saved_plan_does_not_reselect_and_refresh_stdout_preserves_json(env):
    conn, ssh, refresh, path = env
    result = invoke("--filter", "ovr_bch=test-release", "--save-plan", str(path))
    assert result.exit_code == 0, result.output
    # Membership and checksums change after preview; execution retains the reviewed expectation.
    seed(conn, checksum=sha256_hex(b"replacement"), stamp=NOW)
    seed(conn, OTHER, stamp=NOW)
    refresh.side_effect = lambda *a: print("refresh output")
    result = invoke("--from-plan", str(path), "--confirm", "--json", "--reason", "merged")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "completed"
    assert [r["host"] for r in payload["results"]] == [HOST]
    assert payload["results"][0]["parameters"]["expected_override_sha256"] == CHECKSUM
    assert payload["results"][0]["parameters"]["reason"] == "merged"
    assert payload["puppet_convergence"] == "not_checked"
    assert CHECKSUM in ssh.call_args.args[1]
    assert "refresh output" in result.stderr
    assert refresh.call_args.args[0] == [HOST]
    records = (path.parent / ".fleetroll" / "audit.jsonl").read_text().splitlines()
    assert json.loads(records[0])["selection"]["observation_ts"] == OLD


@pytest.mark.parametrize("no_audit", [False, True])
@pytest.mark.parametrize("failure", ["mismatch", "ssh", "exception", "missing_protocol"])
def test_partial_results_are_audited_and_fail_nonzero(env, no_audit, failure):
    conn, ssh, refresh, path = env
    seed(conn, OTHER)

    def run(host, *args, **kwargs):
        if host == OTHER:
            if failure == "exception":
                raise OSError("transport failed")
            return {
                "mismatch": (3, "UNSET_STATUS=checksum_mismatch\n", "changed override"),
                "ssh": (255, "", "offline"),
                "missing_protocol": (0, "", ""),
            }[failure]
        return 0, "UNSET_STATUS=removed\nREMOVED=1\n", ""

    ssh.side_effect = run
    result = invoke(
        "--filter",
        "override=present",
        "--confirm",
        "--json",
        *(["--no-audit"] if no_audit else []),
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["failed"] == 1
    assert {r["host"]: r["ok"] for r in payload["results"]} == {HOST: True, OTHER: False}
    records = [
        json.loads(line)
        for line in (path.parent / ".fleetroll" / "audit.jsonl").read_text().splitlines()
    ]
    assert len(records) == 2
    assert all(r["action"] == "host.unset_override" for r in records)
    if no_audit:
        refresh.assert_not_called()
    else:
        assert set(refresh.call_args.args[0]) == {HOST, OTHER}


@pytest.mark.parametrize(
    ("present", "checksum", "os_type"),
    [
        (None, None, "Linux"),
        (True, None, "Linux"),
        (True, "short", "Linux"),
        (False, CHECKSUM, "Linux"),
        (True, CHECKSUM, "Windows"),
    ],
)
def test_unknown_or_contradictory_evidence_never_reaches_ssh(env, present, checksum, os_type):
    conn, ssh, refresh, path = env
    seed(conn, present=present, checksum=checksum, os_type=os_type, stamp=NOW)
    result = invoke(HOST, "--filter", "host~selected", "--confirm", "--json")
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["results"][0]["status"] == "blocked"
    assert payload["results"][0]["ok"] is False
    ssh.assert_not_called()
    refresh.assert_not_called()


def test_absent_is_idempotent_and_guarded(env):
    conn, ssh, refresh, path = env
    ssh.return_value = (0, "UNSET_STATUS=absent\nREMOVED=0\n", "")
    result = invoke("--filter", "override=absent", "--confirm", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["results"][0]["status"] == "absent"
    assert payload["results"][0]["host"] == OTHER
    assert "expected=''" in shlex.split(ssh.call_args.args[1])[-1]


def test_scoped_filter_file_and_no_matches(env):
    conn, ssh, refresh, path = env
    hosts = path.parent / "hosts.list"
    hosts.write_text(OTHER + "\n")
    saved = path.parent / "selection.yaml"
    saved.write_text("query: ovr_bch~test-release\n")
    result = invoke(str(hosts), "--filter-file", str(saved), "--confirm", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "no_matches"
    assert json.loads(result.stdout)["plan"]["candidate_count"] == 1
    ssh.assert_not_called()
    refresh.assert_not_called()


def test_branch_substrings_and_checksum_selection_are_shared(env):
    conn, ssh, refresh, path = env
    content = 'PUPPET_BRANCH="test-release-next"\n'
    other_sha = sha256_hex(content.encode())
    (get_db_path().parent / "overrides" / other_sha[:12]).write_text(content)
    seed(conn, OTHER, checksum=other_sha)
    for query, expected in [
        ("ovr_bch=test-release", {HOST: CHECKSUM}),
        ("ovr_bch~test-release", {HOST: CHECKSUM, OTHER: other_sha}),
        (f"ovr_checksum={CHECKSUM}", {HOST: CHECKSUM}),
    ]:
        result = invoke("--filter", query, "--json")
        assert result.exit_code == 0, result.output
        targets = json.loads(result.stdout)["plan"]["targets"]
        assert {target["host"]: target["checksum"] for target in targets} == expected
    ssh.assert_not_called()


@pytest.mark.parametrize(
    "bad_args",
    [
        ["--filter", ""],
        ["--filter", "oops=present"],
        ["--filter", "override=present", "--filter-file", "FILTER"],
        ["--filter", "override=present", "--workers", "0"],
        ["--save-plan", "PLAN"],
        ["--filter", "override=present", "--save-plan", "PLAN", "--confirm"],
    ],
)
def test_invalid_selectors_do_not_contact_hosts(env, bad_args):
    conn, ssh, refresh, path = env
    saved = path.parent / "filter.yaml"
    saved.write_text("query: override=present\n")
    result = invoke(
        *(str(saved) if a == "FILTER" else str(path) if a == "PLAN" else a for a in bad_args)
    )
    assert result.exit_code == 2, result.output
    ssh.assert_not_called()


def test_saved_plan_validation_and_no_overwrite(env):
    conn, ssh, refresh, path = env
    args = ["--filter", "override=present", "--save-plan", str(path)]
    assert invoke(*args).exit_code == 0
    original = path.read_text()
    assert invoke(*args).exit_code == 2
    assert path.read_text() == original
    assert invoke("--from-plan", str(path), HOST, "--confirm").exit_code == 2
    plan = json.loads(original)
    for change in [
        {"schema_version": 2},
        {"targets": plan["targets"] * 2},
        {"targets": [{**plan["targets"][0], "host": "-oProxyCommand=bad"}]},
    ]:
        path.write_text(json.dumps({**plan, **change}))
        result = invoke("--from-plan", str(path), "--confirm")
        assert result.exit_code == 2, result.output
    ssh.assert_not_called()


@pytest.fixture
def remote_env(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {
        "sudo": 'shift\nexec "$@"\n',
        "uname": 'echo "${TEST_OS:-Linux}"\n',
    }.items():
        script = bin_dir / name
        script.write_text("#!/bin/sh\n" + body)
        script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    return tmp_path / "ronin_settings", bin_dir


def run_remote(path, *, expected=CHECKSUM, backup=True):
    script = remote_unset_script(
        backup=backup, backup_suffix="test-backup", expected_checksum=expected
    )
    for remote_path in ("/etc/puppet/ronin_settings", "/opt/puppet_environments/ronin_settings"):
        script = script.replace(remote_path, str(path))
    return subprocess.run(shlex.split(script), capture_output=True, text=True, check=False)


@pytest.mark.parametrize("platform", ["Linux", "Darwin"])
@pytest.mark.parametrize("backup", [False, True])
def test_remote_guard_removes_matching_file_and_preserves_backup(
    remote_env, monkeypatch, platform, backup
):
    path, _ = remote_env
    monkeypatch.setenv("TEST_OS", platform)
    path.write_text(CONTENT)
    result = run_remote(path, backup=backup)
    assert result.returncode == 0, result.stderr
    assert "UNSET_STATUS=removed" in result.stdout
    assert not path.exists()
    backups = list(path.parent.glob("ronin_settings.bak.*"))
    assert len(backups) == int(backup)
    if backup:
        assert backups[0].read_text() == CONTENT


@pytest.mark.parametrize("expected", [CHECKSUM, ""])
def test_remote_absent_file_is_noop(remote_env, expected):
    path, _ = remote_env
    result = run_remote(path, expected=expected)
    assert result.returncode == 0, result.stderr
    assert "UNSET_STATUS=absent" in result.stdout


@pytest.mark.parametrize("expected", [CHECKSUM, ""])
def test_remote_changed_file_is_preserved(remote_env, expected):
    path, _ = remote_env
    path.write_text("replacement")
    result = run_remote(path, expected=expected)
    assert result.returncode != 0
    assert "UNSET_STATUS=checksum_mismatch" in result.stdout
    assert path.read_text() == "replacement"
    assert not list(path.parent.glob("*.bak.*"))


def test_remote_replacement_during_backup_is_preserved(remote_env):
    path, bin_dir = remote_env
    path.write_text(CONTENT)
    cp = bin_dir / "cp"
    cp.write_text('#!/bin/sh\n/bin/cp "$@"\nprintf replacement > "$2"\n')
    cp.chmod(0o755)
    result = run_remote(path)
    assert result.returncode != 0
    assert "UNSET_STATUS=checksum_mismatch" in result.stdout
    assert path.read_text() == "replacement"


def test_remote_symlink_and_sudo_failure_are_not_absence(remote_env):
    path, bin_dir = remote_env
    path.symlink_to(path.parent / "missing-target")
    result = run_remote(path)
    assert result.returncode != 0
    assert path.is_symlink()
    assert "UNSET_STATUS=unavailable" in result.stdout
    (bin_dir / "sudo").write_text("#!/bin/sh\nexit 1\n")
    result = run_remote(path)
    assert result.returncode != 0
    assert "UNSET_STATUS=absent" not in result.stdout
