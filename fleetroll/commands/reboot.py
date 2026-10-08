"""Operator-triggered best-effort reboot when gwhc reports idle."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ..audit import append_jsonl
from ..exceptions import CommandFailureError, UserError
from ..ssh import build_ssh_options, remote_reboot_if_idle_script, run_ssh
from ..utils import (
    default_audit_log_path,
    expand_hostname,
    infer_actor,
    looks_like_host,
    utc_now_iso,
)

if TYPE_CHECKING:
    from ..cli_types import HostRebootIfIdleArgs

_MARKER_PREFIX = "FLEETROLL_REBOOT_IF_IDLE_"


def _parse_remote_markers(stdout: str) -> dict[str, str]:
    """Parse the structured result lines emitted by the remote script."""
    markers: dict[str, str] = {}
    for line in stdout.splitlines():
        if not line.startswith(_MARKER_PREFIX) or "=" not in line:
            continue
        key, value = line[len(_MARKER_PREFIX) :].split("=", 1)
        markers[key] = value
    return markers


def _marker_int(markers: dict[str, str], key: str) -> int | None:
    """Parse an integer marker, returning None when missing or invalid."""
    try:
        return int(markers[key])
    except (KeyError, ValueError):
        return None


def _marker_bool(markers: dict[str, str], key: str) -> bool | None:
    """Parse a boolean marker, returning None when missing or invalid."""
    value = markers.get(key)
    if value == "1":
        return True
    if value == "0":
        return False
    return None


def run_reboot_if_idle_for_host(
    host: str,
    *,
    args: HostRebootIfIdleArgs,
    ssh_opts: list[str],
    audit_log: Path,
    actor: str,
) -> dict[str, Any]:
    """Run the live gwhc gate and reboot request in one SSH session."""
    start = time.monotonic()
    remote_cmd = remote_reboot_if_idle_script()
    try:
        ssh_rc, stdout, stderr = run_ssh(
            host,
            remote_cmd,
            ssh_options=ssh_opts,
            timeout_s=args.timeout,
        )
    except Exception as exc:
        result: dict[str, Any] = {
            "ts": utc_now_iso(),
            "actor": actor,
            "action": "host.reboot_if_idle",
            "host": host,
            "ok": False,
            "ssh_rc": None,
            "stderr": "",
            "error": f"{type(exc).__name__}: {exc}",
            "outcome": "unknown",
            "reason": "ssh_error",
            "observed": {},
            "parameters": {
                "check_command": "sudo -n gwhc --json",
                "reboot_command": "sudo -n systemctl --no-block reboot",
            },
            "duration_s": round(time.monotonic() - start, 2),
        }
        append_jsonl(audit_log, result)
        return result

    markers = _parse_remote_markers(stdout)
    remote_outcome = markers.get("RESULT")
    if remote_outcome not in {"requested", "refused", "failed", "unknown"}:
        outcome = "unknown"
        reason = "remote_result_missing"
    else:
        outcome = remote_outcome
        reason = markers.get("REASON")

    gwhc_exit = _marker_int(markers, "GWHC_EXIT")
    reboot_exit = _marker_int(markers, "REBOOT_EXIT")
    result = {
        "ts": utc_now_iso(),
        "actor": actor,
        "action": "host.reboot_if_idle",
        "host": host,
        "ok": outcome == "requested",
        "ssh_rc": ssh_rc,
        "stderr": stderr.strip(),
        "outcome": outcome,
        "reason": reason,
        "observed": {
            "os_type": markers.get("OS_TYPE"),
            "gwhc_present": _marker_bool(markers, "GWHC_PRESENT"),
            "gwhc_exit_code": gwhc_exit,
            "gwhc_json_valid": _marker_bool(markers, "JSON_VALID"),
            "gwhc_state": markers.get("GWHC_STATE"),
            "reboot_exit_code": reboot_exit,
        },
        "parameters": {
            "check_command": "sudo -n gwhc --json",
            "reboot_command": "sudo -n systemctl --no-block reboot",
        },
        "duration_s": round(time.monotonic() - start, 2),
    }
    append_jsonl(audit_log, result)
    return result


def cmd_host_reboot_if_idle(args: HostRebootIfIdleArgs) -> None:
    """Request a reboot for one Linux host only when live gwhc says IDLE."""
    if not looks_like_host(args.host):
        raise UserError(
            "host-reboot-if-idle requires exactly one hostname; host files are unsupported."
        )
    if "@" in args.host:
        user, hostname = args.host.rsplit("@", 1)
        host = f"{user}@{expand_hostname(hostname)}"
    else:
        host = expand_hostname(args.host)
    if not looks_like_host(host):
        raise UserError(f"Invalid hostname for host-reboot-if-idle: {args.host}")
    audit_log = Path(args.audit_log) if args.audit_log else default_audit_log_path()

    if not args.confirm:
        if args.json:
            print(
                json.dumps(
                    {
                        "action": "host.reboot_if_idle",
                        "dry_run": True,
                        "host": host,
                        "reboot_command": "sudo -n systemctl --no-block reboot",
                        "audit_log": str(audit_log),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(
                click.style(
                    "DRY RUN: --confirm not provided; no changes will be made.", fg="yellow"
                )
            )
            print(f"Host: {host}")
            print("Action: check live gwhc state and request reboot only if state is IDLE")
            print(f"Audit log: {audit_log}")
        return

    result = run_reboot_if_idle_for_host(
        host,
        args=args,
        ssh_opts=build_ssh_options(args),
        audit_log=audit_log,
        actor=infer_actor(),
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result["outcome"] == "requested":
        print(f"Reboot requested for {host}.")
        print(f"Audit log: {audit_log}")
    elif result["outcome"] == "unknown":
        print(f"Reboot outcome unknown for {host}: {result.get('reason') or result.get('error')}.")
        print(f"Audit log: {audit_log}")
    else:
        print(f"Reboot not requested for {host}: {result.get('reason') or result['outcome']}.")
        print(f"Audit log: {audit_log}")

    if result["outcome"] != "requested":
        raise CommandFailureError
