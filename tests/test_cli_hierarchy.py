"""Resource discovery, legacy routing, and shared deployment safeguards."""

import json

import click
import pytest
from click.testing import CliRunner
from fleetroll.cli import cli
from fleetroll.cli_hierarchy import COMMAND_PATHS, GROUP_HELP
from fleetroll.exceptions import UserError


@pytest.mark.parametrize("group", GROUP_HELP)
def test_groups_show_help_and_visible_actions(group):
    runner = CliRunner()
    result = runner.invoke(cli, [group])
    assert "Usage:" in result.output
    result = runner.invoke(cli, [group, "--help"])
    assert result.exit_code == 0
    for resource, action in COMMAND_PATHS.values():
        if resource == group:
            assert f"  {action} " in result.output


@pytest.mark.parametrize("legacy", [name for name in COMMAND_PATHS if name != "gather"])
def test_alias_and_canonical_parse_identically(legacy, monkeypatch, tmp_path):
    group, action = COMMAND_PATHS[legacy]
    alias = cli.commands[legacy]
    canonical = cli.commands[group].commands[action]
    assert alias.callback is canonical.callback
    assert alias.hidden
    assert not canonical.hidden
    calls = []

    def record(**kwargs):
        calls.append(kwargs)
        click.echo(json.dumps(kwargs, default=str, sort_keys=True))

    monkeypatch.setattr(alias, "callback", record)
    monkeypatch.setattr(canonical, "callback", record)
    args = [
        "sample" for param in alias.params if isinstance(param, click.Argument) and param.required
    ]
    if any(param.name == "json_output" for param in alias.params):
        args.append("--json")
    runner = CliRunner()
    old = runner.invoke(cli, [legacy, *args])
    new = runner.invoke(cli, [group, action, *args])
    assert old.exit_code == new.exit_code == 0, (old.output, new.output)
    assert old.output == new.output
    assert len(calls) == 2
    assert calls[0] == calls[1]


@pytest.mark.parametrize("prefix", [["gather"], ["gather", "all"]])
@pytest.mark.parametrize("before", [False, True])
def test_combined_gather_option_positions(prefix, before, mocker, tmp_path):
    hosts = tmp_path / "hosts.list"
    hosts.write_text("test.example.com\n")
    host = mocker.patch("fleetroll.cli.cmd_host_audit")
    tc = mocker.patch("fleetroll.cli.cmd_tc_fetch")
    gh = mocker.patch("fleetroll.cli.cmd_gh_fetch")
    options = ["--quiet", "--skip-tc", "--workers", "3"]
    args = [*options, str(hosts)] if before else [str(hosts), *options]
    result = CliRunner().invoke(cli, [*prefix, *args])
    assert result.exit_code == 0, result.output
    assert host.call_args.args[0].host == str(hosts)
    assert host.call_args.args[0].workers == 3
    assert host.call_args.args[0].quiet
    tc.assert_not_called()
    gh.assert_called_once_with(override_delay=False, quiet=True)


def test_explicit_gather_action_and_ambiguous_file(mocker, tmp_path, monkeypatch):
    host = mocker.patch("fleetroll.cli.cmd_host_audit")
    tc = mocker.patch("fleetroll.cli.cmd_tc_fetch")
    gh = mocker.patch("fleetroll.cli.cmd_gh_fetch")
    runner = CliRunner()
    result = runner.invoke(cli, ["gather", "tc", "test.example.com"])
    assert result.exit_code == 0, result.output
    tc.assert_called_once()
    host.assert_not_called()
    gh.assert_not_called()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "host").write_text("test.example.com\n")
    result = runner.invoke(cli, ["gather", "./host"])
    assert result.exit_code == 0, result.output
    host.assert_called_once()
    gh.assert_called_once()


@pytest.mark.parametrize("prefix", [["host-set-override"], ["override", "set"]])
def test_override_confirmation_and_worker_safety(prefix, tmp_path, mocker):
    source = tmp_path / "test.override"
    source.write_text("PUPPET_BRANCH='test'\nWORKER_TYPE_OVERRIDE='other'\n")
    mocker.patch("fleetroll.commands.override_safety.get_connection", side_effect=OSError("no DB"))
    ssh = mocker.patch("fleetroll.commands.set.run_ssh", return_value=(0, "", ""))
    args = [
        *prefix,
        "test.example.com",
        "--from-file",
        str(source),
        "--json",
        "--force",
        "--no-audit",
    ]
    runner = CliRunner()
    for confirm in ([], ["--confirm"]):
        result = runner.invoke(cli, [*args, *confirm])
        assert isinstance(result.exception, UserError)
        assert "compatibility check failed" in str(result.exception)
    ssh.assert_not_called()
    result = runner.invoke(cli, [*args, "--allow-worker-type-mismatch"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["dry_run"]
    ssh.assert_not_called()
    result = runner.invoke(cli, [*args, "--allow-worker-type-mismatch", "--confirm"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["action"] == "host.set_override"
    ssh.assert_called_once()


@pytest.mark.parametrize("prefix", [["host-reboot-if-idle"], ["host", "reboot-if-idle"]])
def test_reboot_still_requires_confirmation(prefix, mocker):
    ssh = mocker.patch("fleetroll.commands.reboot.run_ssh")
    result = CliRunner().invoke(cli, [*prefix, "test.example.com", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["dry_run"]
    ssh.assert_not_called()


def test_rollout_report_passes_through_help():
    runner = CliRunner()
    old = runner.invoke(cli, ["rollout-report", "--help"])
    new = runner.invoke(cli, ["rollout", "report", "--help"])
    assert old.exit_code == new.exit_code == 0
    assert old.output.replace("rollout-report", "rollout report") == new.output


def test_compare_pools_discovery_and_compatibility_alias():
    runner = CliRunner()
    actions = runner.invoke(cli, ["rollout", "--help"])
    assert "  compare-pools " in actions.output
    assert "  compare " not in actions.output
    old = runner.invoke(cli, ["rollout", "compare", "--help"])
    new = runner.invoke(cli, ["rollout", "compare-pools", "--help"])
    assert old.exit_code == new.exit_code == 0
    assert (
        old.output.replace("rollout compare", "rollout compare-pools").split() == new.output.split()
    )
    for action in ("report", "compare-pools"):
        help_result = runner.invoke(cli, ["rollout", action, "--help"])
        assert "Runs once." in " ".join(help_result.output.split())
        assert "fr gather all" in help_result.output


@pytest.mark.parametrize("prefix", [["host-set-override"], ["override", "set"]])
def test_parse_errors_keep_exit_code(prefix):
    result = CliRunner().invoke(cli, [*prefix, "test.example.com", "--workers", "invalid"])
    assert result.exit_code == 2
    assert "not a valid integer" in result.output
