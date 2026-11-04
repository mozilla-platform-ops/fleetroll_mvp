"""Read-only live worker and Puppet activity checks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..exceptions import CommandFailureError, UserError
from ..ssh import build_ssh_options, remote_idle_script, run_ssh
from ..utils import expand_hostname, looks_like_host, utc_now_iso

if TYPE_CHECKING:
    from ..cli_types import HostIdleArgs

_PREFIX = "FLEETROLL_IDLE_"


def _read_health(payload: str) -> tuple[dict[str, Any], str | None]:
    """Validate gwhc health and classify only recognized worker states."""
    try:
        health = json.loads(payload)
    except (ValueError, TypeError):
        return {}, "invalid_gwhc_json"
    if not isinstance(health, dict):
        return {}, "invalid_gwhc_json"
    if health.get("ok") is not True:
        return health, "gwhc_unhealthy"
    if health.get("state") not in ("IDLE", "WORKING", "BUSY"):
        return health, "unknown_worker_state"
    return health, None


def parse_idle_output(stdout: str, *, ssh_rc: int) -> dict[str, Any]:
    """Normalize live health evidence, retaining unknowns rather than guessing idle."""
    envelope, _, payload = stdout.partition(f"{_PREFIX}JSON_BEGIN=\n")
    markers = {}
    for line in envelope.splitlines():
        if line.startswith(_PREFIX) and "=" in line:
            key, value = line[len(_PREFIX) :].split("=", 1)
            markers[key] = value
    result: dict[str, Any] = {
        "ok": False,
        "checked_at": markers.get("CHECKED_AT"),
        "os_type": markers.get("OS_TYPE"),
        "worker_state": "unknown",
        "puppet_running": None,
        "idle": None,
        "reason": markers.get("ERROR"),
        "gwhc_state": None,
        "gwhc_ts": None,
        "worker_reason": None,
        "evidence": [],
    }
    if ssh_rc != 0:
        result["reason"] = "ssh_failed"
        return result
    result["puppet_running"] = {"true": True, "false": False}.get(markers.get("PUPPET_RUNNING"))
    if result["reason"]:
        return result
    if result["os_type"] != "Linux":
        result["reason"] = "unsupported_os"
        return result
    if markers.get("GWHC_EXIT") != "0":
        result["reason"] = "gwhc_failed"
        return result
    health, health_error = _read_health(payload)
    state = health.get("state")
    result["gwhc_state"] = state if isinstance(state, str) else None
    result["gwhc_ts"] = health.get("ts")
    result["worker_reason"] = health.get("state_description")
    checks = health.get("checks")
    if isinstance(checks, list):
        result["evidence"] = [
            {key: check.get(key) for key in ("name", "status", "detail")}
            for check in checks
            if isinstance(check, dict)
            and check.get("name") in {"puppet", "generic_worker_process", "worker_runner_process"}
        ]
    if health_error:
        result["reason"] = health_error
        return result
    if state == "IDLE":
        result["worker_state"] = "idle"
    elif state in ("WORKING", "BUSY"):
        result["worker_state"] = "working"
    puppet_running = result["puppet_running"]
    if puppet_running is None:
        result["reason"] = "puppet_activity_unknown"
    elif not result["checked_at"]:
        result["reason"] = "check_timestamp_missing"
    else:
        result["idle"] = result["worker_state"] == "idle" and not puppet_running
        result["ok"] = True
        result["reason"] = (
            "puppet_running" if puppet_running else f"worker_{result['worker_state']}"
        )
    return result


def cmd_host_idle(args: HostIdleArgs) -> None:
    """Print a live snapshot; do not persist it as a full host audit."""
    if not looks_like_host(args.host):
        raise UserError("host idle requires exactly one hostname; host files are unsupported.")
    if "@" in args.host:
        user, hostname = args.host.rsplit("@", 1)
        host = f"{user}@{expand_hostname(hostname)}"
    else:
        host = expand_hostname(args.host)
    try:
        ssh_rc, stdout, stderr = run_ssh(
            host,
            remote_idle_script(),
            ssh_options=build_ssh_options(args),
            timeout_s=args.timeout,
        )
        result = parse_idle_output(stdout, ssh_rc=ssh_rc)
        result.update(ssh_rc=ssh_rc, stderr=stderr.strip())
    except Exception as exc:
        result = parse_idle_output("", ssh_rc=1)
        result.update(reason="ssh_error", error=f"{type(exc).__name__}: {exc}", ssh_rc=None)
    result.update(host=host, ts=utc_now_iso())
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        puppet = {True: "running", False: "idle", None: "unknown"}[result["puppet_running"]]
        idle = {True: "yes", False: "no", None: "unknown"}[result["idle"]]
        print(f"Host: {host}")
        print(f"Checked at: {result['checked_at'] or result['ts']}")
        print(f"Worker: {result['worker_state']}; Puppet: {puppet}; Host idle: {idle}")
        print(f"Reason: {result.get('worker_reason') or result['reason']}")
        if not result["ok"]:
            print(f"Check incomplete: {result['reason']}")
        print("Snapshot only; new tasks may start after this check.")
    if not result["ok"]:
        raise CommandFailureError
