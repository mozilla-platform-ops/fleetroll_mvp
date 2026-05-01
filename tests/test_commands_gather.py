"""Tests for the `gather` wrapper command."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner
from fleetroll.cli import cli
from fleetroll.cli_types import HostAuditArgs, TcFetchArgs


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestGatherHelp:
    def test_help(self, runner: CliRunner):
        result = runner.invoke(cli, ["gather", "--help"])
        assert result.exit_code == 0
        assert "--skip-host" in result.output
        assert "--skip-tc" in result.output
        assert "--skip-gh" in result.output
        assert "--verbose" in result.output
        assert "--quiet" in result.output

    def test_missing_host_arg(self, runner: CliRunner):
        result = runner.invoke(cli, ["gather"])
        assert result.exit_code != 0


class TestGatherSkipFlags:
    """Each --skip-* flag suppresses the corresponding sub-command."""

    def _run_gather(self, runner: CliRunner, extra_args: list[str] | None = None):
        args = ["gather", "somehost"] + (extra_args or [])
        with (
            patch("fleetroll.cli.cmd_host_audit") as mock_host,
            patch("fleetroll.cli.cmd_tc_fetch") as mock_tc,
            patch("fleetroll.cli.cmd_gh_fetch") as mock_gh,
        ):
            result = runner.invoke(cli, args)
            return result, mock_host, mock_tc, mock_gh

    def test_all_subcommands_run_by_default(self, runner: CliRunner):
        result, mock_host, mock_tc, mock_gh = self._run_gather(runner)
        assert result.exit_code == 0, result.output
        mock_host.assert_called_once()
        mock_tc.assert_called_once()
        mock_gh.assert_called_once()

    def test_skip_host(self, runner: CliRunner):
        result, mock_host, mock_tc, mock_gh = self._run_gather(runner, ["--skip-host"])
        assert result.exit_code == 0
        mock_host.assert_not_called()
        mock_tc.assert_called_once()
        mock_gh.assert_called_once()

    def test_skip_tc(self, runner: CliRunner):
        result, mock_host, mock_tc, mock_gh = self._run_gather(runner, ["--skip-tc"])
        assert result.exit_code == 0
        mock_host.assert_called_once()
        mock_tc.assert_not_called()
        mock_gh.assert_called_once()

    def test_skip_gh(self, runner: CliRunner):
        result, mock_host, mock_tc, mock_gh = self._run_gather(runner, ["--skip-gh"])
        assert result.exit_code == 0
        mock_host.assert_called_once()
        mock_tc.assert_called_once()
        mock_gh.assert_not_called()

    def test_skip_all(self, runner: CliRunner):
        result, mock_host, mock_tc, mock_gh = self._run_gather(
            runner, ["--skip-host", "--skip-tc", "--skip-gh"]
        )
        assert result.exit_code == 0
        mock_host.assert_not_called()
        mock_tc.assert_not_called()
        mock_gh.assert_not_called()


class TestGatherArgPassthrough:
    """Verify that options are forwarded correctly to sub-commands."""

    def test_quiet_propagates(self, runner: CliRunner):
        with (
            patch("fleetroll.cli.cmd_host_audit") as mock_host,
            patch("fleetroll.cli.cmd_tc_fetch") as mock_tc,
            patch("fleetroll.cli.cmd_gh_fetch") as mock_gh,
        ):
            result = runner.invoke(cli, ["gather", "somehost", "--quiet"])
            assert result.exit_code == 0
            host_args: HostAuditArgs = mock_host.call_args[0][0]
            tc_args: TcFetchArgs = mock_tc.call_args[0][0]
            assert host_args.quiet is True
            assert tc_args.quiet is True
            mock_gh.assert_called_once_with(override_delay=False, quiet=True)

    def test_verbose_count_propagates(self, runner: CliRunner):
        with (
            patch("fleetroll.cli.cmd_host_audit") as mock_host,
            patch("fleetroll.cli.cmd_tc_fetch") as mock_tc,
            patch("fleetroll.cli.cmd_gh_fetch"),
        ):
            result = runner.invoke(cli, ["gather", "somehost", "-vv"])
            assert result.exit_code == 0
            host_args: HostAuditArgs = mock_host.call_args[0][0]
            tc_args: TcFetchArgs = mock_tc.call_args[0][0]
            assert host_args.verbose is True
            assert tc_args.verbose == 2

    def test_host_arg_forwarded(self, runner: CliRunner):
        with (
            patch("fleetroll.cli.cmd_host_audit") as mock_host,
            patch("fleetroll.cli.cmd_tc_fetch") as mock_tc,
            patch("fleetroll.cli.cmd_gh_fetch"),
        ):
            result = runner.invoke(cli, ["gather", "myhost.example.com"])
            assert result.exit_code == 0
            host_args: HostAuditArgs = mock_host.call_args[0][0]
            tc_args: TcFetchArgs = mock_tc.call_args[0][0]
            assert host_args.host == "myhost.example.com"
            assert tc_args.host == "myhost.example.com"

    def test_stop_on_host_failure(self, runner: CliRunner):
        """If gather-host raises, gather-tc and gather-gh are not called."""
        with (
            patch("fleetroll.cli.cmd_host_audit", side_effect=RuntimeError("ssh failed")),
            patch("fleetroll.cli.cmd_tc_fetch") as mock_tc,
            patch("fleetroll.cli.cmd_gh_fetch") as mock_gh,
        ):
            result = runner.invoke(cli, ["gather", "somehost"])
            assert result.exit_code != 0
            mock_tc.assert_not_called()
            mock_gh.assert_not_called()
