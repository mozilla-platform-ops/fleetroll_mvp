"""Behavior of mixed-result host file operations."""

import json

import pytest
from fleetroll.commands.set import cmd_host_set
from fleetroll.commands.unset import cmd_host_unset
from fleetroll.commands.vault import cmd_host_set_vault
from fleetroll.exceptions import CommandFailureError
from fleetroll.utils import sha256_hex

OPERATIONS = [
    ("set", "mock_args_set", cmd_host_set),
    ("unset", "mock_args_unset", cmd_host_unset),
    ("vault", "mock_args_vault", cmd_host_set_vault),
]


def prepare(request, fixture, tmp_path):
    args = request.getfixturevalue(fixture)
    args.audit_log = str(tmp_path / ".fleetroll" / "audit.jsonl")
    args.hosts = ["good.example.com", "bad.example.com"]
    args.json = True
    args.no_audit = False
    if fixture != "mock_args_unset":
        source = tmp_path / "source"
        source.write_text("key: value\n")
        args.from_file = str(source)
        args.validate = False
        args.ignore_state = True
        args.force = True
    return args


@pytest.mark.parametrize(("module", "fixture", "command"), OPERATIONS)
@pytest.mark.parametrize("transport_exception", [False, True])
def test_mixed_results_are_audited_and_refreshed(
    request, tmp_path, mocker, capsys, module, fixture, command, transport_exception
):
    args = prepare(request, fixture, tmp_path)

    def ssh(host, *unused, **kwargs):
        if host.startswith("bad"):
            if transport_exception:
                raise OSError("transport unavailable")
            return 255, "", "connection failed"
        return 0, "CONTENT_CHANGED=1\nREMOVED=1\n", ""

    mocker.patch(f"fleetroll.commands.{module}.run_ssh", side_effect=ssh)
    refresh = mocker.patch("fleetroll.commands._auto_audit._run_auto_audit")
    with pytest.raises(CommandFailureError) as failure:
        command(args)
    assert failure.value.rc == 1
    results = {r["host"]: r for r in json.loads(capsys.readouterr().out)}
    assert results["good.example.com"]["ok"] is True
    assert results["bad.example.com"]["ok"] is False
    assert results["bad.example.com"]["ssh_rc"] == (None if transport_exception else 255)
    records = [
        json.loads(line)
        for line in (tmp_path / ".fleetroll" / "audit.jsonl").read_text().splitlines()
    ]
    assert {r["host"]: r["ok"] for r in records} == {
        "good.example.com": True,
        "bad.example.com": False,
    }
    assert set(refresh.call_args.args[0]) == set(args.hosts)


@pytest.mark.parametrize(("module", "fixture", "command"), OPERATIONS)
def test_partial_failure_summary(request, tmp_path, mocker, capsys, module, fixture, command):
    args = prepare(request, fixture, tmp_path)
    args.json = False
    args.no_audit = True
    mocker.patch(
        f"fleetroll.commands.{module}.run_ssh",
        side_effect=lambda host, *a, **kw: (0, "", "")
        if host.startswith("good")
        else (255, "", "offline"),
    )
    with pytest.raises(CommandFailureError):
        command(args)
    output = capsys.readouterr().out
    assert "OK good.example.com" in output
    assert "FAIL bad.example.com" in output
    assert "failed=1" in output


@pytest.mark.parametrize(("module", "fixture", "command"), OPERATIONS)
def test_preview_does_not_contact_hosts_or_write_audit(
    request, tmp_path, mocker, capsys, module, fixture, command
):
    args = prepare(request, fixture, tmp_path)
    args.confirm = False
    ssh = mocker.patch(f"fleetroll.commands.{module}.run_ssh")
    refresh = mocker.patch("fleetroll.commands._auto_audit._run_auto_audit")
    command(args)
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    ssh.assert_not_called()
    refresh.assert_not_called()
    assert not (tmp_path / ".fleetroll" / "audit.jsonl").exists()


@pytest.mark.parametrize(
    ("module", "fixture", "command", "field"),
    [
        ("set", "mock_args_set", cmd_host_set, "override_sha256"),
        ("vault", "mock_args_vault", cmd_host_set_vault, "vault_sha256"),
    ],
)
def test_matching_state_skips_one_host_and_no_audit_disables_refresh(
    request, tmp_path, mocker, capsys, module, fixture, command, field
):
    args = prepare(request, fixture, tmp_path)
    args.ignore_state = False
    args.no_audit = True
    checksum = sha256_hex((tmp_path / "source").read_bytes())
    mocker.patch("fleetroll.db.get_connection")
    mocker.patch(
        "fleetroll.db.get_latest_host_observations",
        return_value=({"good.example.com": {"observed": {field: checksum}}}, {}),
    )
    ssh = mocker.patch(
        f"fleetroll.commands.{module}.run_ssh", return_value=(0, "CONTENT_CHANGED=1", "")
    )
    refresh = mocker.patch("fleetroll.commands._auto_audit._run_auto_audit")
    command(args)
    results = {r["host"]: r for r in json.loads(capsys.readouterr().out)}
    assert results["good.example.com"]["no_change"] is True
    assert results["bad.example.com"]["ok"] is True
    assert [call.args[0] for call in ssh.call_args_list] == ["bad.example.com"]
    refresh.assert_not_called()
    records = [
        json.loads(line)
        for line in (tmp_path / ".fleetroll" / "audit.jsonl").read_text().splitlines()
    ]
    assert [r["host"] for r in records] == ["bad.example.com"]
