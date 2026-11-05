"""Live operator hold operations, independent of gwhc."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..audit import append_jsonl
from ..exceptions import CommandFailureError, UserError
from ..ssh import build_ssh_options, remote_hold_script, run_ssh
from ..utils import (
    default_audit_log_path,
    expand_hostname,
    infer_actor,
    is_host_file,
    looks_like_host,
    utc_now_iso,
)

if TYPE_CHECKING:
    from ..cli_types import HostHoldArgs

HOLD_PATH = "/home/cltbld/operator_hold"


def parse_hold_output(stdout: str, *, ssh_rc: int, operation: str) -> dict[str, Any]:
    """Require consistent live evidence before reporting a successful operation."""
    markers = {}
    prefix = "FLEETROLL_HOLD_"
    for line in stdout.splitlines():
        if line.startswith(prefix) and "=" in line:
            key, value = line[len(prefix) :].split("=", 1)
            markers[key] = value
    before = {"0": False, "1": True}.get(markers.get("BEFORE"))
    present = {"0": False, "1": True}.get(markers.get("PRESENT"))
    outcome = markers.get("RESULT")
    expected = {"set": True, "clear": False}.get(operation)
    consistent = (
        outcome == "observed"
        if operation == "show"
        else before is not None
        and present is expected
        and (
            (outcome == "no_change" and before is present)
            or (outcome == "changed" and before is not present)
        )
    )
    ok = bool(
        ssh_rc == 0
        and markers.get("OS_TYPE") == "Linux"
        and markers.get("CHECKED_AT")
        and present is not None
        and consistent
    )
    reason = None if ok else markers.get("REASON") or "incomplete_verification"
    if ssh_rc != 0:
        reason = "ssh_or_sudo_failed"
    return {
        "ok": ok,
        "outcome": outcome if ok or outcome in {"failed", "refused"} else "unknown",
        "reason": reason,
        "operator_hold": present if ok else None,
        "previous_operator_hold": before,
        "checked_at": markers.get("CHECKED_AT"),
        "os_type": markers.get("OS_TYPE"),
    }


def cmd_host_hold(args: HostHoldArgs) -> None:
    """Show a hold or make a confirmed, verified, audited hold change."""
    if args.operation not in {"show", "set", "clear"}:
        raise UserError(f"Unsupported hold operation: {args.operation}")
    if is_host_file(args.host) or not looks_like_host(args.host):
        raise UserError("host hold requires exactly one hostname; host files are unsupported.")
    user, separator, hostname = args.host.rpartition("@")
    host = f"{user}@{expand_hostname(hostname)}" if separator else expand_hostname(args.host)
    changing = args.operation != "show"
    audit_log = Path(args.audit_log) if args.audit_log else default_audit_log_path()
    parameters = {"path": HOLD_PATH, "reason": args.reason}
    common = {"host": host, "action": f"host.hold.{args.operation}", "parameters": parameters}
    if changing and not args.confirm:
        preview = {**common, "dry_run": True, "audit_log": str(audit_log)}
        if args.json:
            print(json.dumps(preview, indent=2, sort_keys=True))
        else:
            print(f"DRY RUN: {args.operation} operator hold on {host} ({HOLD_PATH}).")
            if args.reason:
                print(f"Reason: {args.reason}")
            print("Run again with --confirm to apply changes.")
        return

    actor = infer_actor() if changing else None
    try:
        rc, stdout, stderr = run_ssh(
            host,
            remote_hold_script(args.operation),
            ssh_options=build_ssh_options(args),
            timeout_s=args.timeout,
        )
        result = parse_hold_output(stdout, ssh_rc=rc, operation=args.operation)
        result.update(ssh_rc=rc, stderr=stderr.strip())
    except Exception as exc:
        result = parse_hold_output("", ssh_rc=1, operation=args.operation)
        result.update(reason="ssh_error", ssh_rc=None, error=f"{type(exc).__name__}: {exc}")
    result.update(common)
    result["ts"] = utc_now_iso()
    if changing:
        result["actor"] = actor
        append_jsonl(audit_log, result)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        state = {True: "set", False: "clear", None: "unknown"}[result["operator_hold"]]
        print(f"Operator hold on {host}: {state} ({result['outcome']}).")
        print(f"Checked at: {result['checked_at'] or result['ts']}")
        if not result["ok"]:
            print(f"Check failed: {result['reason']}")
        if changing:
            print(f"Audit log: {audit_log}")
        if result["ok"] and result["operator_hold"]:
            print("Hold prevents new jobs; an already-running job may still be active.")
    if not result["ok"]:
        raise CommandFailureError
