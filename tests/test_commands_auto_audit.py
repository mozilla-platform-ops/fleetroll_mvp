"""Tests for fleetroll/commands/_auto_audit.py - shared auto-audit helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fleetroll.commands._auto_audit import _maybe_auto_audit, _run_auto_audit


@pytest.fixture
def audit_args():
    args = MagicMock()
    args.host = "test.example.com"
    args.ssh_option = None
    args.connect_timeout = 10
    args.timeout = 60
    args.workers = 5
    args.no_audit = False
    return args


class TestMaybeAutoAudit:
    def test_skips_when_no_audit_true(self, mocker, audit_args, tmp_path: Path):
        audit_args.no_audit = True
        mock_run = mocker.patch("fleetroll.commands._auto_audit._run_auto_audit")

        _maybe_auto_audit(["host1.example.com"], audit_args, tmp_path / "audit.jsonl")

        mock_run.assert_not_called()

    def test_calls_run_auto_audit_when_enabled(self, mocker, audit_args, tmp_path: Path):
        audit_args.no_audit = False
        mock_run = mocker.patch("fleetroll.commands._auto_audit._run_auto_audit")

        _maybe_auto_audit(["host1.example.com"], audit_args, tmp_path / "audit.jsonl")

        mock_run.assert_called_once()

    def test_swallows_exceptions_on_failure(self, mocker, audit_args, tmp_path: Path, capsys):
        audit_args.no_audit = False
        mocker.patch(
            "fleetroll.commands._auto_audit._run_auto_audit",
            side_effect=RuntimeError("db unreachable"),
        )

        # Must not raise
        _maybe_auto_audit(["host1.example.com"], audit_args, tmp_path / "audit.jsonl")

        stderr = capsys.readouterr().err
        assert "auto-audit failed" in stderr.lower()

    def test_passes_hosts_and_audit_log(self, mocker, audit_args, tmp_path: Path):
        audit_args.no_audit = False
        mock_run = mocker.patch("fleetroll.commands._auto_audit._run_auto_audit")
        audit_log = tmp_path / "audit.jsonl"
        hosts = ["host1.example.com", "host2.example.com"]

        _maybe_auto_audit(hosts, audit_args, audit_log)

        call_args = mock_run.call_args
        assert call_args[0][0] == hosts
        assert call_args[0][2] == audit_log


class TestRunAutoAudit:
    def test_builds_audit_args_and_calls_batch(self, mocker, audit_args, tmp_path: Path):
        mock_batch = mocker.patch("fleetroll.commands.gather_host.cmd_host_audit_batch")
        mocker.patch("builtins.print")
        hosts = ["host1.example.com"]
        audit_log = tmp_path / "audit.jsonl"

        _run_auto_audit(hosts, audit_args, audit_log)

        mock_batch.assert_called_once()
        call_hosts, call_audit_args = mock_batch.call_args[0]
        assert call_hosts == hosts
        assert call_audit_args.host == audit_args.host
        assert call_audit_args.connect_timeout == audit_args.connect_timeout
        assert call_audit_args.workers == audit_args.workers
        assert call_audit_args.quiet is True

    def test_prints_refresh_message(self, mocker, audit_args, tmp_path: Path):
        mocker.patch("fleetroll.commands.gather_host.cmd_host_audit_batch")
        captured = []
        mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(str(a[0])))

        _run_auto_audit(["h1.example.com", "h2.example.com"], audit_args, tmp_path / "audit.jsonl")

        assert any("2 host(s)" in line for line in captured)
