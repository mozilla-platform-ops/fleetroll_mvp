"""Worker type deployment checks using real recorded observations."""

import json

import pytest
from fleetroll.commands.override_safety import check_worker_type
from fleetroll.commands.set import cmd_host_set
from fleetroll.db import get_connection, init_db, insert_host_observation
from fleetroll.exceptions import UserError


@pytest.fixture
def observations(tmp_path, monkeypatch):
    path = tmp_path / "hosts.db"
    init_db(path)
    monkeypatch.setattr("fleetroll.commands.override_safety.get_db_path", lambda: path)

    def add(host, *, role, ts="2020-01-01T00:00:00Z", ok=True):
        conn = get_connection(path)
        try:
            insert_host_observation(
                conn,
                {
                    "host": host,
                    "ts": ts,
                    "ok": ok,
                    "observed": {"role": role},
                },
            )
            conn.commit()
        finally:
            conn.close()

    add("talos", role="gecko_t_linux_2404_talos")
    add("netperf", role="gecko_t_linux_2404_netperf")
    return add


@pytest.mark.parametrize(
    ("worker", "compatible"),
    [
        ("gecko-t-linux-talos-2404", True),
        ("gecko-t-linux-talos-2404-tc-v115", True),
        ("gecko-t-linux-talos-2404-", False),
        ("gecko-t-linux-talos-24040", False),
        ("gecko-t-linux-talos-1804-tc-v115", False),
    ],
)
def test_base_and_suffix(observations, worker, compatible):
    check = check_worker_type(f"WORKER_TYPE_OVERRIDE='{worker}'".encode(), hosts=["talos"])[0]
    assert check["compatible"] is compatible
    assert check["observed_at"] == "2020-01-01T00:00:00Z"


def test_uses_last_successful_role_not_current_override(observations):
    observations("talos", role="gecko_t_linux_2404_netperf", ts="2021-01-01T00:00:00Z", ok=False)
    check = check_worker_type(
        b"export WORKER_TYPE_OVERRIDE=gecko-t-linux-talos-2404-test", hosts=["talos"]
    )[0]
    assert check["compatible"]


@pytest.mark.parametrize(
    "line",
    [
        'WORKER_TYPE_OVERRIDE="$POOL"',
        "WORKER_TYPE_OVERRIDE='gecko-t-linux-talos-2404'; WORKER_TYPE_OVERRIDE=other",
    ],
)
def test_uncertain_assignment_blocks(observations, line):
    assert not check_worker_type(line.encode(), hosts=["talos"])[0]["compatible"]


def test_no_worker_type_needs_no_database(mocker):
    db = mocker.patch("fleetroll.commands.override_safety.get_connection")
    assert (
        check_worker_type(
            b"# WORKER_TYPE_OVERRIDE='other'\nPUPPET_BRANCH='test'", hosts=["unknown"]
        )
        == []
    )
    db.assert_not_called()


def test_database_failure_blocks(mocker):
    mocker.patch(
        "fleetroll.commands.override_safety.get_connection", side_effect=OSError("unavailable")
    )
    check = check_worker_type(b"WORKER_TYPE_OVERRIDE='gecko-t-linux-talos-2404'", hosts=["talos"])[
        0
    ]
    assert not check["compatible"]
    assert "could not read recorded host roles" in check["reason"]


def test_last_assignment_is_checked(observations):
    checks = check_worker_type(
        b"WORKER_TYPE_OVERRIDE='gecko-t-linux-talos-2404'\nWORKER_TYPE_OVERRIDE=other\n",
        hosts=["talos"],
    )
    assert checks[0]["requested"] == "other"
    assert not checks[0]["compatible"]


def test_unknown_and_mac_roles(observations):
    observations("mac", role="gecko_t_osx_1400_r8")
    observations("new", role="new_role")
    checks = check_worker_type(
        b"WORKER_TYPE_OVERRIDE='gecko-t-osx-1400-r8-test'", hosts=["mac", "new", "missing"]
    )
    assert [c["compatible"] for c in checks] == [True, False, False]


def test_preview_rechecked_on_apply_and_batch_blocked(
    observations, mock_args_set, tmp_path, mocker, capsys
):
    file = tmp_path / "override"
    file.write_text("WORKER_TYPE_OVERRIDE='gecko-t-linux-talos-2404-tc-v115'\n")
    args = mock_args_set
    args.hosts = ["talos"]
    args.from_file = str(file)
    args.confirm = False
    args.json = True
    args.force = True
    args.validate = False
    args.no_audit = True
    ssh = mocker.patch("fleetroll.commands.set.run_ssh", return_value=(0, "", ""))
    cmd_host_set(args)
    preview = json.loads(capsys.readouterr().out)
    assert preview["worker_type_checks"][0]["compatible"]
    observations("talos", role="gecko_t_linux_2404_netperf", ts="2022-01-01T00:00:00Z")
    args.confirm = True
    with pytest.raises(UserError, match="compatibility check failed"):
        cmd_host_set(args)
    ssh.assert_not_called()
    observations("talos2", role="gecko_t_linux_2404_talos")
    args.hosts = ["talos2", "missing"]
    with pytest.raises(UserError, match="missing"):
        cmd_host_set(args)
    ssh.assert_not_called()
    args.hosts = ["talos"]
    args.allow_worker_type_mismatch = True
    cmd_host_set(args)
    result = json.loads(capsys.readouterr().out)
    assert result["parameters"]["allow_worker_type_mismatch"]
    ssh.assert_called_once()
