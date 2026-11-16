"""Role percentage selection retains canaries and never silently changes waves."""

import json
import os
import shlex
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from click.testing import CliRunner
from fleetroll.cli import cli
from fleetroll.db import get_connection, get_db_path, init_db, insert_host_observation
from fleetroll.ssh import remote_set_script
from fleetroll.utils import sha256_hex

ROLE = "gecko_t_linux_2404_talos"
CONTENT = 'PUPPET_BRANCH="test-percentage"\n'
CHECKSUM = sha256_hex(CONTENT.encode())
CONFLICT = sha256_hex(b"conflicting-override")
NOW = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
OLD = (datetime.now(UTC) - timedelta(days=2)).isoformat()


def hostname(number):
    return f"host-{number:02}.example.test"


def seed(
    conn, number, *, checksum=None, present=False, role=ROLE, stamp=NOW, ok=True, os_type="Linux"
):
    insert_host_observation(
        conn,
        {
            "host": hostname(number),
            "ts": stamp,
            "ok": ok,
            "observed": {
                "role_present": bool(role),
                "role": role,
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
    conn = get_connection(get_db_path())
    for number in range(1, 11):
        seed(conn, number, checksum=CHECKSUM if number == 1 else None, present=number == 1)
    source = tmp_path / "candidate.override"
    source.write_text(CONTENT)
    ssh = mocker.patch("fleetroll.commands.set.run_ssh")
    mocker.patch(
        "fleetroll.commands.set.utc_now_iso",
        return_value=(datetime.now(UTC) - timedelta(seconds=5)).isoformat(timespec="seconds"),
    )
    ssh.return_value = (0, "SET_STATUS=written\nCONTENT_CHANGED=1\n", "")
    refresh = mocker.patch("fleetroll.commands._auto_audit._run_auto_audit")
    yield conn, source, ssh, refresh
    conn.close()


def invoke(source, *args):
    return CliRunner().invoke(cli, ["override", "set", "--from-file", str(source), *args])


def plan(source, percent="50", *args):
    return invoke(source, "--role", ROLE, "--target-percent", percent, "--json", *args)


def test_preview_expands_total_coverage_and_preserves_controls(env):
    conn, source, ssh, refresh = env
    result = plan(source)
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)["plan"]
    assert report["denominator"] == 10
    assert report["recorded_matching"] == [hostname(1)]
    assert report["target_count"] == 5
    assert report["additions"] == [hostname(i) for i in range(2, 6)]
    assert report["controls"] == [hostname(i) for i in range(6, 11)]
    assert report["source"] == "local database"
    assert {h["observation_ts"] for h in report["scope"]} == {NOW}
    ssh.assert_not_called()
    refresh.assert_not_called()
    assert not (source.parent / ".fleetroll" / "audit.jsonl").exists()


def test_confirmation_and_repeated_target_do_not_select_another_wave(env):
    conn, source, ssh, refresh = env
    result = plan(source, "50", "--confirm", "--reason", "expand canaries")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["achieved_coverage"]["matching_count"] == 5
    assert payload["achieved_coverage"]["percent"] == 50
    assert payload["puppet_convergence"] == "not_checked"
    selected = {call.args[0] for call in ssh.call_args_list}
    assert selected == {hostname(i) for i in range(2, 6)}
    assert set(refresh.call_args.args[0]) == selected
    records = [
        json.loads(line)
        for line in (source.parent / ".fleetroll" / "audit.jsonl").read_text().splitlines()
    ]
    assert len(records) == 4
    assert all(r["parameters"]["percentage_role"] == ROLE for r in records)
    assert all(r["parameters"]["sha256"] == CHECKSUM for r in records)
    for i in range(2, 6):
        seed(conn, i, checksum=CHECKSUM, present=True, stamp=datetime.now(UTC).isoformat())
    ssh.reset_mock()
    result = plan(source, "50", "--confirm")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["plan"]["status"] == "target_met"
    assert payload["results"] == []
    ssh.assert_not_called()


def test_unverified_writes_block_a_new_wave_until_collection_catches_up(env):
    conn, source, ssh, refresh = env
    first = plan(source, "50", "--confirm", "--no-audit")
    assert first.exit_code == 0, first.output
    ssh.reset_mock()
    repeated = plan(source, "50", "--confirm", "--no-audit")
    assert repeated.exit_code == 1, repeated.output
    payload = json.loads(repeated.stdout)
    assert payload["blocked_reason"] == "verification_required"
    assert payload["plan"]["additions"] == []
    assert set(payload["plan"]["writes_awaiting_verification"]) == {
        hostname(i) for i in range(2, 6)
    }
    ssh.assert_not_called()
    event = json.loads((source.parent / ".fleetroll" / "audit.jsonl").read_text().splitlines()[0])
    written_at = datetime.fromisoformat(event["ts"])
    for offset, status in [(0, "verification_required"), (1, "target_met")]:
        for i in range(2, 6):
            seed(
                conn,
                i,
                present=True,
                checksum=CHECKSUM,
                stamp=(written_at + timedelta(seconds=offset)).isoformat(),
            )
        repeated = plan(source, "50", "--confirm")
        assert json.loads(repeated.stdout)["plan"]["status"] == status
    ssh.assert_not_called()


def test_one_hundred_percent_adds_only_remaining_role_hosts(env):
    conn, source, ssh, refresh = env
    for i in range(2, 6):
        seed(conn, i, present=True, checksum=CHECKSUM, stamp=datetime.now(UTC).isoformat())
    result = plan(source, "100", "--confirm")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["achieved_coverage"]["percent"] == 100
    assert {call.args[0] for call in ssh.call_args_list} == {hostname(i) for i in range(6, 11)}


@pytest.mark.parametrize(
    ("percent", "target", "status"),
    [
        ("0", 0, "target_exceeded"),
        ("1", 1, "target_met"),
        ("10", 1, "target_met"),
        ("10.01", 2, "ready"),
        ("100", 10, "ready"),
    ],
)
def test_rounding_and_lower_targets_never_remove_overrides(env, percent, target, status):
    conn, source, ssh, refresh = env
    result = plan(source, percent)
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)["plan"]
    assert report["target_count"] == target
    assert report["status"] == status
    assert hostname(1) not in report["additions"]
    ssh.assert_not_called()


def test_exclusions_stay_in_denominator_and_unknown_membership_is_separate(env):
    conn, source, ssh, refresh = env
    seed(conn, 2, present=True, checksum=CONFLICT, stamp=datetime.now(UTC).isoformat())
    conn.execute("DELETE FROM host_observations WHERE host=?", (hostname(3),))
    seed(conn, 3, stamp=OLD)
    # Latest failed gather retains the original successful role and presence.
    seed(conn, 4, ok=False, stamp=datetime.now(UTC).isoformat())
    seed(conn, 5, present=None, stamp=datetime.now(UTC).isoformat())
    seed(conn, 20, role=None)
    seed(conn, 21, role="another_role")
    result = plan(source, "100", "--confirm")
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    report = payload["plan"]
    assert report["denominator"] == 10
    assert report["unknown_membership"] == [hostname(20)]
    assert report["outside_role"] == [hostname(21)]
    reasons = {item["host"]: item["reason"] for item in report["excluded"]}
    assert reasons[hostname(2)] == "conflicting_override"
    assert reasons[hostname(3)] == "observation_stale"
    assert reasons[hostname(4)] == "latest_collection_failed"
    assert reasons[hostname(5)] == "override_presence_unknown"
    assert payload["blocked_reason"] == "insufficient_eligible_hosts"
    ssh.assert_not_called()


def test_role_scope_and_empty_population(env):
    conn, source, ssh, refresh = env
    subset = source.parent / "subset.list"
    subset.write_text("\n".join(hostname(i) for i in (1, 2, 3)) + "\n")
    result = plan(source, "50", str(subset))
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)["plan"]
    assert report["denominator"] == 3
    assert report["target_count"] == 2
    assert report["additions"] == [hostname(2)]
    result = invoke(
        source, "--role", "missing_role", "--target-percent", "100", "--json", "--confirm"
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["plan"]["status"] == "no_matches"
    assert report["achieved_coverage"]["percent"] is None
    ssh.assert_not_called()


def test_saved_plan_binds_population_and_source_contents_and_allows_own_progress(env):
    conn, source, ssh, refresh = env
    path = source.parent / "deploy.json"
    result = plan(source, "50", "--save-plan", str(path))
    assert result.exit_code == 0, result.output
    # A new DB host is outside this frozen population.
    seed(conn, 30)
    result = invoke(source, "--from-plan", str(path), "--confirm", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["plan"]["denominator"] == 10
    for i in range(2, 6):
        seed(conn, i, present=True, checksum=CHECKSUM, stamp=datetime.now(UTC).isoformat())
    ssh.return_value = (0, "SET_STATUS=already_current\nCONTENT_CHANGED=0\n", "")
    result = invoke(source, "--from-plan", str(path), "--confirm", "--json")
    assert result.exit_code == 0, result.output
    assert all(r["status"] == "already_current" for r in json.loads(result.stdout)["results"])
    source.write_text('PUPPET_BRANCH="changed"\n')
    ssh.reset_mock()
    result = invoke(source, "--from-plan", str(path), "--confirm", "--json")
    assert result.exit_code == 2, result.output
    ssh.assert_not_called()


@pytest.mark.parametrize("change", ["schema", "duplicate", "addition", "nan"])
def test_saved_plan_validation_and_no_overwrite(env, change):
    conn, source, ssh, refresh = env
    path = source.parent / "deploy.json"
    assert plan(source, "50", "--save-plan", str(path)).exit_code == 0
    original = path.read_text()
    assert plan(source, "50", "--save-plan", str(path)).exit_code == 2
    assert path.read_text() == original
    contents = json.loads(original)
    if change == "schema":
        contents["schema_version"] = 2
    elif change == "duplicate":
        contents["scope"].append(contents["scope"][0])
    elif change == "addition":
        contents["additions"] = [hostname(10)]
    else:
        contents["scope"][0]["pending_write_ts"] = float("nan")
    path.write_text(json.dumps(contents))
    result = invoke(source, "--from-plan", str(path), "--confirm")
    assert result.exit_code == 2, result.output
    ssh.assert_not_called()


@pytest.mark.parametrize("change", ["role", "checksum", "failed", "stale"])
def test_saved_plan_detects_relevant_recorded_changes_before_writing(env, change):
    conn, source, ssh, refresh = env
    path = source.parent / "deploy.json"
    assert plan(source, "50", "--save-plan", str(path)).exit_code == 0
    if change == "stale":
        # Restore stale evidence with the same role/presence/checksum values.
        conn.execute("DELETE FROM host_observations WHERE host=?", (hostname(2),))
        seed(conn, 2, stamp=OLD)
    else:
        seed(
            conn,
            2,
            role="new_role" if change == "role" else ROLE,
            present=change == "checksum",
            checksum=CONFLICT if change == "checksum" else None,
            ok=change != "failed",
            stamp=datetime.now(UTC).isoformat(),
        )
    result = invoke(source, "--from-plan", str(path), "--confirm", "--json")
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["blocked_reason"]
    ssh.assert_not_called()


@pytest.mark.parametrize("failure", ["conflict", "role", "ssh", "exception", "missing_protocol"])
def test_partial_deployment_results_and_coverage_are_explicit(env, failure):
    conn, source, ssh, refresh = env

    def run(host, *args, **kwargs):
        if host == hostname(2):
            if failure == "exception":
                raise OSError("transport failed")
            return {
                "conflict": (3, "SET_STATUS=conflicting_override\n", "replacement"),
                "role": (3, "SET_STATUS=role_changed\n", "role changed"),
                "ssh": (255, "", "offline"),
                "missing_protocol": (0, "", ""),
            }[failure]
        return 0, "SET_STATUS=written\nCONTENT_CHANGED=1\n", ""

    ssh.side_effect = run
    refresh.side_effect = lambda *a: print("refresh message")
    result = plan(source, "50", "--confirm")
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["failed"] == 1
    assert payload["achieved_coverage"]["matching_count"] == 4
    assert payload["achieved_coverage"]["percent"] == 40
    assert "refresh message" in result.stderr
    assert len((source.parent / ".fleetroll" / "audit.jsonl").read_text().splitlines()) == 4


def test_worker_pool_safety_and_no_audit_are_preserved(env):
    conn, source, ssh, refresh = env
    source.write_text(CONTENT + 'WORKER_TYPE_OVERRIDE="wrong-pool"\n')
    result = plan(source, "50", "--confirm")
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["blocked_reason"] == "worker_type_incompatible"
    ssh.assert_not_called()
    result = plan(source, "50", "--confirm", "--allow-worker-type-mismatch", "--no-audit")
    assert result.exit_code == 0, result.output
    assert ssh.call_count == 5  # Original canary has different content and remains a conflict.
    refresh.assert_not_called()


@pytest.mark.parametrize(
    "extra",
    [
        ["--role", ROLE],
        ["--target-percent", "50"],
        ["--role", ROLE, "--target-percent", "-1"],
        ["--role", ROLE, "--target-percent", "101"],
        ["--role", ROLE, "--target-percent", "nan"],
        ["--role", ROLE, "--target-percent", "50", "--force"],
        ["--role", ROLE, "--target-percent", "50", "--ignore-state"],
        ["--role", ROLE, "--target-percent", "50", "--workers", "0"],
    ],
)
def test_invalid_percentage_options_do_not_contact_hosts(env, extra):
    conn, source, ssh, refresh = env
    result = invoke(source, *extra, "--confirm")
    assert result.exit_code == 2, result.output
    ssh.assert_not_called()


@pytest.fixture
def remote(tmp_path, monkeypatch):
    real_link = shutil.which("link")
    assert real_link
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {
        "sudo": 'shift\nexec "$@"\n',
        "uname": 'echo "${TEST_OS:-Linux}"\n',
        "chown": "exit 0\n",
    }.items():
        script = bin_dir / name
        script.write_text("#!/bin/sh\n" + body)
        script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    (tmp_path / "role").write_text(ROLE + "\n")
    return tmp_path / "override", bin_dir, real_link


def run_remote(path, *, content=CONTENT):
    script = remote_set_script(
        mode="0644",
        owner="root",
        group="auto",
        backup=True,
        backup_suffix="test-backup",
        expected_role=ROLE,
        desired_checksum=CHECKSUM,
    )
    for name in ("/etc/puppet/ronin_settings", "/opt/puppet_environments/ronin_settings"):
        script = script.replace(name, str(path))
    script = script.replace("/etc/puppet_role", str(path.parent / "role"))
    return subprocess.run(
        shlex.split(script), input=content, text=True, capture_output=True, check=False
    )


@pytest.mark.parametrize("platform", ["Linux", "Darwin"])
def test_checked_remote_creation_and_repeat_are_atomic_noops(remote, monkeypatch, platform):
    path, _, _ = remote
    monkeypatch.setenv("TEST_OS", platform)
    result = run_remote(path)
    assert result.returncode == 0, result.stderr
    assert path.read_text() == CONTENT
    assert "SET_STATUS=written" in result.stdout
    result = run_remote(path)
    assert result.returncode == 0, result.stderr
    assert "SET_STATUS=already_current" in result.stdout
    assert not list(path.parent.glob(".ronin_settings.tmp.*"))


@pytest.mark.parametrize("change", ["role", "conflict", "symlink", "incoming"])
def test_remote_changes_prevent_installation(remote, change):
    path, _, _ = remote
    if change == "role":
        (path.parent / "role").write_text("different-role\n")
    if change == "conflict":
        path.write_text("replacement")
    if change == "symlink":
        path.symlink_to(path.parent / "missing")
    result = run_remote(path, content="changed input" if change == "incoming" else CONTENT)
    assert result.returncode != 0
    if change == "conflict":
        assert path.read_text() == "replacement"
    elif change == "symlink":
        assert path.is_symlink()
    else:
        assert not path.exists()
    assert not list(path.parent.glob(".ronin_settings.tmp.*"))


@pytest.mark.parametrize("replacement", ["file", "directory", "same"])
def test_atomic_install_never_overwrites_a_concurrent_destination(remote, replacement):
    path, bin_dir, real_link = remote
    link = bin_dir / "link"
    action = (
        'mkdir "$2"'
        if replacement == "directory"
        else ('cp "$1" "$2"' if replacement == "same" else 'printf replacement > "$2"')
    )
    link.write_text(f'#!/bin/sh\n{action}\nexec {shlex.quote(real_link)} "$@"\n')
    link.chmod(0o755)
    result = run_remote(path)
    if replacement == "same":
        assert result.returncode == 0, result.stderr
        assert "SET_STATUS=already_current" in result.stdout
        assert path.read_text() == CONTENT
    else:
        assert result.returncode != 0
        if replacement == "file":
            assert path.read_text() == "replacement"
        else:
            assert path.is_dir()
            assert list(path.iterdir()) == []
    assert not list(path.parent.glob(".ronin_settings.tmp.*"))
