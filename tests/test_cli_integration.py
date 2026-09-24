"""CLI behavior from argument parsing through SSH, output, and exit status."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import pytest
from fleetroll.cli import main
from fleetroll.db import get_connection, get_db_path, get_latest_host_observations


def invoke_fleetroll(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *args: str,
) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "argv", ["fleetroll", *args])
    with pytest.raises(SystemExit) as exc_info:
        main()
    output = capsys.readouterr()
    return exc_info.value.code, output.out, output.err


def test_gather_host_batch_json_reports_partial_failure_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mocker,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    hosts = tmp_path / "hosts.list"
    hosts.write_text("good.example.test\nbad.example.test\n", encoding="utf-8")
    audit_log = tmp_path / "audit.jsonl"

    def fake_ssh(host: str, _command: str, **_kwargs) -> tuple[int, str, str]:
        if host == "good.example.test":
            return (
                0,
                "OS_TYPE=Linux\nROLE_PRESENT=1\nROLE=test-role\n"
                "OVERRIDE_PRESENT=0\nVLT_PRESENT=0\nUPTIME_S=123\n",
                "",
            )
        return 1, "", "Permission denied"

    ssh = mocker.patch("fleetroll.commands.gather_host.run_ssh", side_effect=fake_ssh)
    code, stdout, stderr = invoke_fleetroll(
        monkeypatch,
        capsys,
        "gather-host",
        str(hosts),
        "--json",
        "--audit-log",
        str(audit_log),
    )

    assert code == 1
    assert stderr == ""
    result = json.loads(stdout)
    assert (result["total"], result["successful"], result["failed"]) == (2, 1, 1)
    by_host = {item["host"]: item for item in result["results"]}
    assert by_host["good.example.test"]["observed"]["role"] == "test-role"
    assert by_host["bad.example.test"]["stderr"] == "Permission denied"
    assert ssh.call_count == 2
    conn = get_connection(get_db_path())
    try:
        latest, _ = get_latest_host_observations(conn, list(by_host))
    finally:
        conn.close()
    assert set(latest) == set(by_host)


def test_gather_host_quiet_failure_shows_error_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mocker,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    mocker.patch(
        "fleetroll.commands.gather_host.run_ssh",
        return_value=(1, "", "Permission denied"),
    )

    code, stdout, stderr = invoke_fleetroll(
        monkeypatch,
        capsys,
        "gather-host",
        "bad.example.test",
        "--quiet",
        "--audit-log",
        str(tmp_path / "audit.jsonl"),
    )

    assert code == 1
    assert "FAILED bad.example.test" in click.unstyle(stdout)
    assert "Permission denied" in stdout
    assert stderr == ""


def test_set_override_json_dry_run_reports_syntax_error_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mocker,
    tmp_path: Path,
) -> None:
    override = tmp_path / "invalid-override.sh"
    override.write_text("if then\n", encoding="utf-8")
    ssh = mocker.patch("fleetroll.commands.set.run_ssh")

    code, stdout, stderr = invoke_fleetroll(
        monkeypatch,
        capsys,
        "host-set-override",
        "host.example.test",
        "--from-file",
        str(override),
        "--json",
        "--audit-log",
        str(tmp_path / "audit.jsonl"),
    )

    assert code == 2
    assert json.loads(stdout)["dry_run"] is True
    assert "Override syntax validation failed" in stderr
    assert not (tmp_path / "audit.jsonl").exists()
    ssh.assert_not_called()


def test_unset_override_json_ssh_failure_is_audited_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mocker,
    tmp_path: Path,
) -> None:
    audit_log = tmp_path / "audit.jsonl"
    mocker.patch(
        "fleetroll.commands.unset.run_ssh",
        return_value=(1, "", "Permission denied"),
    )

    code, stdout, stderr = invoke_fleetroll(
        monkeypatch,
        capsys,
        "host-unset-override",
        "host.example.test",
        "--confirm",
        "--json",
        "--audit-log",
        str(audit_log),
    )

    assert code == 1
    assert stderr == ""
    result = json.loads(stdout)
    assert result["action"] == "host.unset_override"
    assert result["ok"] is False
    assert result["stderr"] == "Permission denied"
    assert json.loads(audit_log.read_text().strip())["ssh_rc"] == 1
