"""FleetRoll unset override command implementation."""

from __future__ import annotations

import datetime as dt
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ..audit import append_jsonl
from ..constants import BACKUP_TIME_FORMAT, DRY_RUN_PREVIEW_LIMIT
from ..exceptions import CommandFailureError
from ..override_removal import describe_removal_plan
from ..ssh import build_ssh_options, parse_backup_path, remote_unset_script, run_ssh
from ..utils import (
    default_audit_log_path,
    format_host_preview,
    infer_actor,
    parse_kv_lines,
    utc_now_iso,
)
from ._auto_audit import _maybe_auto_audit

if TYPE_CHECKING:
    from ..cli_types import HostUnsetOverrideArgs
    from ..override_removal import RemovalTarget


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
    target: RemovalTarget | None = None,
) -> dict[str, Any]:
    """Unset override file for a single host and append audit log."""
    blocked = target.blocked_reason() if target else None
    rc, out, err = None, "", ""
    if target is not None and not blocked:
        remote_cmd = remote_unset_script(
            backup=not args.no_backup,
            backup_suffix=backup_suffix,
            expected_checksum=target.checksum or "",
        )
    if not blocked:
        try:
            rc, out, err = run_ssh(host, remote_cmd, ssh_options=ssh_opts, timeout_s=args.timeout)
        except Exception as exc:
            if target is None:
                raise
            err = str(exc)
    info = parse_kv_lines(out)
    status = "blocked" if blocked else info.get("UNSET_STATUS", "verification_unavailable")
    checked_ok = rc == 0 and (
        (status == "removed" and info.get("REMOVED") == "1")
        or (status == "absent" and info.get("REMOVED") == "0")
    )
    removed = info.get("REMOVED") == "1"

    result: dict[str, Any] = {
        "ts": utc_now_iso(),
        "actor": actor,
        "action": "host.unset_override",
        "host": host,
        "ok": checked_ok if target else (rc == 0),
        "ssh_rc": rc,
        "stderr": err.strip(),
        "observed": {
            "removed": removed,
            "backup": (not args.no_backup),
            "backup_suffix": backup_suffix,
            "backup_path": parse_backup_path(out),
        },
        "parameters": {
            "reason": args.reason,
        },
    }
    if target is not None:
        result["status"] = status
        result["selection"] = target.model_dump()
        result["parameters"]["expected_override_sha256"] = target.checksum
        if args.removal_plan is not None:
            result["parameters"]["selection_source"] = args.removal_plan.source
            result["parameters"]["selection_query"] = args.removal_plan.query
            result["parameters"]["plan_created_at"] = args.removal_plan.created_at
        result["observed"]["override_sha256_before"] = info.get("CURRENT_SHA256")
        result["puppet_convergence"] = "not_checked"
        if not result["ok"]:
            result["error"] = blocked or err.strip() or status

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
    actor = infer_actor()
    ssh_opts = build_ssh_options(args)
    audit_log = Path(args.audit_log) if args.audit_log else default_audit_log_path()

    if args.removal_plan is not None:
        _cmd_population_unset(args, ssh_opts=ssh_opts, actor=actor, audit_log=audit_log)
        return

    hosts = args.hosts
    host_file = args.host_file
    is_batch = host_file is not None or len(hosts) > 1

    if not args.confirm:
        if args.json:
            summary = {
                "dry_run": True,
                "action": "host.unset_override",
                "host_count": len(hosts),
                "host": None if is_batch else hosts[0],
                "host_file": str(host_file) if host_file is not None else None,
                "backup": (not args.no_backup),
                "backup_suffix_format": BACKUP_TIME_FORMAT,
                "reason": args.reason,
                "audit_log": str(audit_log),
            }
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            print(
                click.style(
                    "DRY RUN: --confirm not provided; no changes will be made.", fg="yellow"
                )
            )
            if is_batch:
                if host_file is not None:
                    print(f"{click.style('Hosts file:', fg='cyan')} {host_file}")
                print(f"{click.style('Host count:', fg='cyan')} {len(hosts)}")
                print(f"{click.style('Hosts:', fg='cyan')}")
                for line in format_host_preview(hosts, limit=DRY_RUN_PREVIEW_LIMIT):
                    print(line)
            else:
                print(f"{click.style('Host:', fg='cyan')} {hosts[0]}")
            action_target = f"{len(hosts)} host(s)"
            print(f"{click.style('Action:', fg='cyan')} unset override on {action_target}")
            print(f"{click.style('Backup:', fg='cyan')} {'yes' if not args.no_backup else 'no'}")
            if args.reason:
                print(f"{click.style('Reason:', fg='cyan')} {args.reason}")
            print(f"{click.style('Audit log:', fg='cyan')} {audit_log}")
            print(click.style("Run again with --confirm to apply changes.", fg="yellow"))
        return

    backup_suffix = dt.datetime.now(dt.UTC).strftime(BACKUP_TIME_FORMAT)
    remote_cmd = remote_unset_script(
        backup=not args.no_backup,
        backup_suffix=backup_suffix,
    )

    if not is_batch:
        host = hosts[0]
        result = unset_override_for_host(
            host,
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
                raise CommandFailureError
            _maybe_auto_audit([host], args, audit_log)
            return

        if rc != 0:
            print(
                f"[{host}] unset override FAILED (rc={rc}). stderr:\n{result.get('stderr', '')}",
                file=sys.stderr,
            )
            raise CommandFailureError

        if removed:
            print(f"[{host}] override removed")
            backup_path = result.get("observed", {}).get("backup_path", "")
            if backup_path:
                print(f"backup: {backup_path}")
        else:
            print(f"[{host}] override did not exist (no change)")

        if args.reason:
            print(f"reason: {args.reason}")
        print(f"Audit log: {audit_log}")
        _maybe_auto_audit([host], args, audit_log)
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

    _maybe_auto_audit([r["host"] for r in results], args, audit_log)

    if any(not r.get("ok") for r in results):
        raise CommandFailureError


def _cmd_population_unset(
    args: HostUnsetOverrideArgs, *, ssh_opts: list[str], actor: str, audit_log: Path
) -> None:
    plan = args.removal_plan
    assert plan is not None  # noqa: S101 - internal dispatch invariant
    description = describe_removal_plan(plan)
    if not args.confirm:
        if args.json:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "action": plan.action,
                        "status": "preview" if plan.targets else "no_matches",
                        "plan": description,
                        "backup": not args.no_backup,
                        "reason": args.reason,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print("DRY RUN: --confirm not provided; no changes will be made.")
            print(f"Selected {len(plan.targets)}/{plan.candidate_count} hosts from {plan.source}")
            print(f"Query: {plan.query}")
            print(f"Backup: {'no' if args.no_backup else 'yes'}; reason: {args.reason or '-'}")
            for target in description["targets"]:
                print(
                    f"  {target['host']} branch={target['branch'] or '?'} "
                    f"present={target['override_present']} checksum={target['checksum'] or '-'}\n"
                    f"    observed={target['observation_ts'] or '?'} {target['freshness']} "
                    f"latest_attempt={target['latest_attempt_ts'] or '?'} "
                    f"latest_ok={target['latest_attempt_ok']} "
                    f"blocked={target['blocked_reason'] or '-'}"
                )
            print("Recorded evidence may be stale; execution verifies each full checksum live.")
            print(
                "Use --save-plan FILE then --from-plan FILE --confirm to execute a frozen preview."
            )
        return

    results = []
    suffix = dt.datetime.now(dt.UTC).strftime(BACKUP_TIME_FORMAT)
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                unset_override_for_host,
                target.host,
                args=args,
                ssh_opts=ssh_opts,
                remote_cmd="",
                actor=actor,
                audit_log=audit_log,
                backup_suffix=suffix,
                log_lock=lock,
                target=target,
            )
            for target in plan.targets
        ]
        results = [future.result() for future in as_completed(futures)]
    results.sort(key=lambda r: r["host"])
    attempted = [r["host"] for r in results if r["status"] != "blocked"]
    if attempted:
        # The shared refresh helper can print; keep the population JSON one document.
        with redirect_stdout(sys.stderr) if args.json else nullcontext():
            _maybe_auto_audit(attempted, args, audit_log)
    failed = sum(not r["ok"] for r in results)
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "action": plan.action,
                    "dry_run": False,
                    "status": "failed" if failed else "completed" if results else "no_matches",
                    "plan": description,
                    "results": results,
                    "failed": failed,
                    "puppet_convergence": "not_checked",
                    "refresh_requested": bool(attempted) and not args.no_audit,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for result in results:
            print(format_unset_line(result))
        print(f"Summary: total={len(results)} successful={len(results) - failed} failed={failed}")
        print("Removal results describe files only; Puppet convergence has not been verified.")
        print(f"Audit log: {audit_log}")
    if failed:
        raise CommandFailureError
