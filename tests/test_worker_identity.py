"""Exercise the remote Ruby collector without SSH or real worker credentials."""

from __future__ import annotations

import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fleetroll.audit import process_audit_result
from fleetroll.db import get_latest_host_observations

RUBY = shutil.which("ruby")
COLLECTOR = Path(__file__).parents[1] / "fleetroll" / "worker_identity.rb"
IDENTITY = {
    "provisionerId": "releng-hardware",
    "workerType": "gecko-t-linux-talos-2404-wayland",
    "workerGroup": "mdc1",
    "workerId": "configured-worker",
}


def collect(commands, *, os_type="Linux", plist_paths=None):
    if not RUBY:
        pytest.skip("Ruby is required to exercise the remote collector")
    result = subprocess.run(
        [
            RUBY,
            "-r",
            str(COLLECTOR),
            "-e",
            "request = JSON.parse(STDIN.read); puts JSON.generate("
            'FleetrollWorkerIdentity.collect(os: request["os"], commands: request["commands"], '
            'plist_paths: request["plists"]))',
        ],
        input=json.dumps({"os": os_type, "commands": commands, "plists": plist_paths}),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    assert "fake-sensitive-token" not in result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("os_type", ["Linux", "Darwin"])
def test_collect_selected_config_only(tmp_path, os_type):
    config = tmp_path / "effective config.json"
    config.write_text(json.dumps({**IDENTITY, "accessToken": "fake-sensitive-token"}))
    (tmp_path / "stale.json").write_text(json.dumps({**IDENTITY, "workerType": "old-pool"}))
    result = collect(
        [
            "logger -t generic-worker",
            f"/usr/local/bin/generic-worker run --config {shlex.quote(str(config))}",
        ],
        os_type=os_type,
    )
    assert result == {"status": "ok", "identity": IDENTITY}


@pytest.mark.parametrize("launcher", ["start-worker", "worker-runner.sh"])
def test_collect_runner_provider(tmp_path, launcher):
    config = tmp_path / "runner.yaml"
    config.write_text(
        "provider:\n  workerPoolID: releng-hardware/gecko-t-linux-talos-2404-wayland\n"
        "  workerGroup: mdc1\n  workerID: configured-worker\n"
        "  accessToken: fake-sensitive-token\nworkerConfig:\n  workerType: stale-ignored-pool\n"
    )
    assert collect([f"/usr/local/bin/{launcher} {config}"]) == {
        "status": "ok",
        "identity": IDENTITY,
    }


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS plutil is required")
@pytest.mark.parametrize("launcher", ["run-generic-worker.sh", "worker-runner.sh"])
def test_macos_launch_plist_selects_custom_configuration(tmp_path, launcher):
    config = tmp_path / "custom.json"
    config.write_text(json.dumps(IDENTITY))
    args = [f"/usr/local/bin/{launcher}"]
    args += ["run", "--config"] if launcher == "run-generic-worker.sh" else []
    args.append(str(config))
    plist = tmp_path / "worker.plist"
    plist.write_bytes(plistlib.dumps({"ProgramArguments": args}))
    assert collect([], os_type="Darwin", plist_paths=[str(plist)]) == {
        "status": "ok",
        "identity": IDENTITY,
    }


def test_conflicting_running_configs_are_ambiguous(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(IDENTITY))
    second.write_text(json.dumps({**IDENTITY, "workerType": "other-pool"}))
    assert collect(
        [
            f"/usr/local/bin/generic-worker run --config={first}",
            f"/usr/local/bin/start-worker {second}",
        ]
    ) == {"status": "ambiguous"}


@pytest.mark.parametrize("wrapper_state", ["missing", "invalid", "conflicting"])
@pytest.mark.parametrize("wrapper_first", [False, True])
def test_active_worker_takes_precedence_over_wrapper_config(tmp_path, wrapper_state, wrapper_first):
    config = tmp_path / "generic-worker.config"
    config.write_text(json.dumps(IDENTITY))
    wrapper_config = tmp_path / "start-worker.yml"
    if wrapper_state == "invalid":
        wrapper_config.write_text("{bad: [fake-sensitive-token")
    elif wrapper_state == "conflicting":
        wrapper_config.write_text(json.dumps({**IDENTITY, "workerType": "old-pool"}))
    commands = [
        f"/usr/local/bin/generic-worker run --config {config}",
        f"bash /usr/local/bin/run-start-worker.sh {wrapper_config}",
    ]
    if wrapper_first:
        commands.reverse()
    assert collect(commands) == {"status": "ok", "identity": IDENTITY}


@pytest.mark.parametrize(
    "launcher", ["run-start-worker.sh", "run-generic-worker.sh", "worker-runner.sh"]
)
def test_wrapper_configuration_is_used_without_active_executable(tmp_path, launcher):
    config = tmp_path / "config.json"
    config.write_text(json.dumps(IDENTITY))
    assert collect([f"bash /usr/local/bin/{launcher} --config {config}"]) == {
        "status": "ok",
        "identity": IDENTITY,
    }


@pytest.mark.parametrize("executable", ["generic-worker", "start-worker"])
def test_missing_active_configuration_cannot_fall_back_to_wrapper(tmp_path, executable):
    config = tmp_path / "wrapper.json"
    config.write_text(json.dumps(IDENTITY))
    assert collect(
        [
            f"/usr/local/bin/{executable} --config {tmp_path}/missing.json",
            f"bash /usr/local/bin/run-generic-worker.sh --config {config}",
        ]
    ) == {"status": "missing"}


def test_missing_active_runner_configuration_blocks_valid_worker_config(tmp_path):
    config = tmp_path / "worker.json"
    config.write_text(json.dumps(IDENTITY))
    assert collect(
        [
            f"/usr/local/bin/generic-worker run --config {config}",
            f"/usr/local/bin/start-worker {tmp_path}/missing.yml",
        ]
    ) == {"status": "missing"}


def test_relative_active_configuration_cannot_fall_back_to_wrapper(tmp_path):
    config = tmp_path / "wrapper.json"
    config.write_text(json.dumps(IDENTITY))
    assert collect(
        [
            "/usr/local/bin/generic-worker run --config relative.json",
            f"bash /usr/local/bin/run-generic-worker.sh --config {config}",
        ]
    ) == {"status": "unsupported_launch_config"}


@pytest.mark.parametrize(
    "content", ["{bad: [fake-sensitive-token", "[]", '{"workerId": "partial"}']
)
def test_invalid_config_does_not_leak_content(tmp_path, content):
    config = tmp_path / "config.json"
    config.write_text(content)
    assert collect([f"/usr/local/bin/generic-worker run --config {config}"]) == {
        "status": "invalid",
    }


def test_missing_selected_config_is_explicit(tmp_path):
    assert collect([f"/usr/local/bin/start-worker {tmp_path}/absent"]) == {"status": "missing"}


def test_relative_running_config_does_not_fall_back():
    assert collect(["/usr/local/bin/generic-worker run --config relative.json"]) == {
        "status": "unsupported_launch_config",
    }


def test_unreadable_selected_config(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("Root can read files regardless of permission bits")
    config = tmp_path / "private.json"
    config.write_text(json.dumps(IDENTITY))
    config.chmod(0)
    try:
        assert collect([f"/usr/local/bin/generic-worker run --config {config}"]) == {
            "status": "unreadable",
        }
    finally:
        config.chmod(0o600)


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"status": "ok", "identity": {**IDENTITY, "accessToken": "fake-sensitive-token"}}, "ok"),
        ({"status": "ok", "identity": {}}, "invalid"),
        ({"status": "unreadable"}, "unreadable"),
        (["fake-sensitive-token"], "invalid"),
    ],
)
def test_audit_persists_only_identity_fields(db_conn, payload, status):
    result = process_audit_result(
        "host",
        rc=0,
        out=f"TC_WORKER_IDENTITY={json.dumps(payload)}\n",
        err="",
        db_conn=db_conn,
        actor="test",
    )
    latest, _ = get_latest_host_observations(db_conn, ["host"])
    assert latest["host"]["observed"]["tc_worker_identity_status"] == status
    assert result["ok"] is True
    assert "fake-sensitive-token" not in json.dumps(latest)
    assert result["observed"]["tc_worker_identity"] == (IDENTITY if status == "ok" else None)
