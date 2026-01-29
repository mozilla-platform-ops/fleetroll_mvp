"""FleetRoll unset override command implementation."""

from __future__ import annotations

import datetime as dt
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ..audit import append_jsonl
from ..constants import BACKUP_TIME_FORMAT
from ..ssh import build_ssh_options, remote_unset_script, run_ssh
from ..utils import (
    default_audit_log_path,
    ensure_host_or_file,
    infer_actor,
    is_host_file,
    parse_host_list,
    utc_now_iso,
)

if TYPE_CHECKING:
    from ..cli_types import HostUnsetOverrideArgs


def unset_override_for_host(
    host: str,
    *,
    args: HostUnsetOverrideArgs,
    ssh_opts: list[str],
    remote_cmd: str,
    actor: str,
    audit_log: Path,
    backup_suffix: str,
    log_lock: threading.Lock | None = None,
) -> dict[str, Any]:
    """Unset override file for a single host and append audit log."""
    rc, out, err = run_ssh(host, remote_cmd, ssh_options=ssh_opts, timeout_s=args.timeout)
    removed = "REMOVED=1" in out

    result: dict[str, Any] = {
        "ts": utc_now_iso(),
        "actor": actor,
        "action": "host.unset_override",
        "host": host,
        "ok": (rc == 0),
        "ssh_rc": rc,
        "stderr": err.strip(),
        "observed": {
            "removed": removed,
            "backup": (not args.no_backup),
            "backup_suffix": backup_suffix,
        },
        "parameters": {
            "reason": args.reason,
        },
    }

    if log_lock:
        with log_lock:
            append_jsonl(audit_log, result)
    else:
        append_jsonl(audit_log, result)
    return result


def format_unset_line(result: dict[str, Any]) -> str:
    """Format a single-line status for batch unset results."""
    host = result.get("host", "?")
    if result.get("ok"):
        removed = result.get("observed", {}).get("removed")
        if removed:
            return f"OK {host} override removed"
        return f"OK {host} override absent"
    error = result.get("error") or result.get("stderr") or "unknown_error"
    rc = result.get("ssh_rc")
    rc_str = f" rc={rc}" if rc is not None else ""
    return f"FAIL {host}{rc_str} {error}"


def cmd_host_unset(args: HostUnsetOverrideArgs) -> None:
    """Remove the override file from a host."""
    ensure_host_or_file(args.host)
    actor = infer_actor()
    ssh_opts = build_ssh_options(args)
    audit_log = Path(args.audit_log) if args.audit_log else default_audit_log_path()

    host_file = None
    if is_host_file(args.host):
        host_file = Path(args.host)
        hosts = parse_host_list(host_file)
        is_batch = True
    else:
        hosts = [args.host]
        is_batch = False

    if not args.confirm:
        if args.json:
            summary = {
                "dry_run": True,
                "action": "host.unset_override",
                "host_count": len(hosts),
                "host": None if is_batch else hosts[0],
                "host_file": str(host_file) if is_batch else None,
                "backup": (not args.no_backup),
                "backup_suffix_format": BACKUP_TIME_FORMAT,
                "reason": args.reason,
                "audit_log": str(audit_log),
            }
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            print("DRY RUN: --confirm not provided; no changes will be made.")
            if is_batch:
                print(f"Hosts file: {host_file}")
                print(f"Host count: {len(hosts)}")
            else:
                print(f"Host: {hosts[0]}")
            action_target = f"{len(hosts)} host(s)"
            print(f"Action: unset override on {action_target}")
            print(f"Backup: {'yes' if not args.no_backup else 'no'}")
            if args.reason:
                print(f"Reason: {args.reason}")
            print(f"Audit log: {audit_log}")
            print("Run again with --confirm to apply changes.")
        return

    backup_suffix = dt.datetime.now(dt.UTC).strftime(BACKUP_TIME_FORMAT)
    remote_cmd = remote_unset_script(
        backup=not args.no_backup,
        backup_suffix=backup_suffix,
    )

    if not is_batch:
        result = unset_override_for_host(
            args.host,
            args=args,
            ssh_opts=ssh_opts,
            remote_cmd=remote_cmd,
            actor=actor,
            audit_log=audit_log,
            backup_suffix=backup_suffix,
        )
        rc = result["ssh_rc"]
        removed = result.get("observed", {}).get("removed")

        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
            if rc != 0:
                sys.exit(1)
            return

        if rc != 0:
            print(
                f"[{args.host}] unset override FAILED (rc={rc}). stderr:\n{result.get('stderr', '')}",
                file=sys.stderr,
            )
            sys.exit(1)

        if removed:
            print(f"[{args.host}] override removed")
            if not args.no_backup:
                print("backup created")
        else:
            print(f"[{args.host}] override did not exist (no change)")

        if args.reason:
            print(f"reason: {args.reason}")
        print(f"Audit log: {audit_log}")
        return

    results: list[dict[str, Any]] = []
    log_lock = threading.Lock()
    show_progress = not args.json
    start_time = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_host = {
            executor.submit(
                unset_override_for_host,
                host,
                args=args,
                ssh_opts=ssh_opts,
                remote_cmd=remote_cmd,
                actor=actor,
                audit_log=audit_log,
                backup_suffix=backup_suffix,
                log_lock=log_lock,
            ): host
            for host in hosts
        }
        with (
            click.progressbar(
                length=len(hosts),
                label="Unsetting overrides",
                show_eta=True,
                show_percent=True,
                file=sys.stderr,
            )
            if show_progress
            else nullcontext()
        ) as bar:
            for future in as_completed(future_to_host):
                host = future_to_host[future]
                try:
                    results.append(future.result())
                except Exception as e:
                    result = {
                        "ts": utc_now_iso(),
                        "actor": actor,
                        "action": "host.unset_override",
                        "host": host,
                        "ok": False,
                        "ssh_rc": None,
                        "stderr": "",
                        "error": str(e),
                        "observed": {
                            "removed": False,
                            "backup": (not args.no_backup),
                            "backup_suffix": backup_suffix,
                        },
                        "parameters": {
                            "reason": args.reason,
                        },
                    }
                    with log_lock:
                        append_jsonl(audit_log, result)
                    results.append(result)
                if show_progress:
                    bar.update(1)

    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for result in sorted(results, key=lambda r: r["host"]):
            print(format_unset_line(result))
        total = len(results)
        failed = sum(1 for r in results if not r.get("ok"))
        successful = total - failed
        duration_s = time.monotonic() - start_time
        print(
            "\nSummary: total="
            f"{total} successful={successful} failed={failed} "
            f"duration={duration_s:.1f}s"
        )
        print(f"Audit log: {audit_log}")

    if any(not r.get("ok") for r in results):
        sys.exit(1)
