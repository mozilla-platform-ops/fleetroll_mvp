"""FleetRoll host-run-puppet command implementation."""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ..audit import append_jsonl
from ..cli_types import HostAuditArgs
from ..constants import DRY_RUN_PREVIEW_LIMIT
from ..exceptions import CommandFailureError
from ..ssh import build_ssh_options, is_windows_host, remote_run_puppet_script, run_ssh
from ..utils import (
    default_audit_log_path,
    ensure_host_or_file,
    format_host_preview,
    infer_actor,
    is_host_file,
    parse_host_list,
    utc_now_iso,
)

if TYPE_CHECKING:
    from ..cli_types import HostRunPuppetArgs

logger = logging.getLogger("fleetroll")

_PUPPET_SUCCESS_EXITS = frozenset({0, 2})

_EXIT_RE = re.compile(r"(?m)^EXIT=(\d+)")


def _parse_puppet_exit(stdout: str) -> int | None:
    """Extract puppet exit code from the trailing EXIT=N marker."""
    m = _EXIT_RE.search(stdout)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def run_puppet_for_host(
    host: str,
    *,
    args: HostRunPuppetArgs,
    ssh_opts: list[str],
    remote_cmd: str,
    actor: str,
    audit_log: Path,
    log_lock: threading.Lock | None = None,
) -> dict[str, Any]:
    """Run puppet on a single host and append audit log."""
    start = time.monotonic()
    rc, out, err = run_ssh(host, remote_cmd, ssh_options=ssh_opts, timeout_s=args.timeout)
    duration_s = round(time.monotonic() - start, 2)

    puppet_exit = _parse_puppet_exit(out)
    ok = rc == 0 and puppet_exit in _PUPPET_SUCCESS_EXITS

    result: dict[str, Any] = {
        "ts": utc_now_iso(),
        "actor": actor,
        "action": "host.run_puppet",
        "host": host,
        "ok": ok,
        "ssh_rc": rc,
        "stderr": err.strip(),
        "observed": {
            "puppet_exit": puppet_exit,
            "changes_applied": (puppet_exit == 2),
            "duration_s": duration_s,
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


def format_puppet_line(result: dict[str, Any]) -> str:
    """Format a single-line status for a puppet run result."""
    host = result.get("host", "?")
    if result.get("ok"):
        observed = result.get("observed", {})
        duration_s = observed.get("duration_s", 0)
        if observed.get("changes_applied"):
            return f"OK {host} changes applied ({duration_s:.1f}s)"
        return f"OK {host} no changes ({duration_s:.1f}s)"
    puppet_exit = result.get("observed", {}).get("puppet_exit")
    error = result.get("error") or result.get("stderr") or "unknown_error"
    rc = result.get("ssh_rc")
    rc_str = f" rc={rc}" if rc is not None else ""
    pe_str = f" puppet_exit={puppet_exit}" if puppet_exit is not None else ""
    return f"FAIL {host}{rc_str}{pe_str} {error}"


def _print_dry_run(
    args: HostRunPuppetArgs,
    hosts: list[str],
    windows_hosts: list[str],
    host_file: Path | None,
    is_batch: bool,
    audit_log: Path,
) -> None:
    """Print DRY RUN summary and exit without making changes."""
    if args.json:
        summary = {
            "dry_run": True,
            "action": "host.run_puppet",
            "host_count": len(hosts),
            "skipped_windows": len(windows_hosts),
            "host": None if is_batch else hosts[0] if hosts else None,
            "host_file": str(host_file) if is_batch else None,
            "reason": args.reason,
            "auto_audit": not args.no_audit,
            "audit_log": str(audit_log),
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    print(click.style("DRY RUN: --confirm not provided; no changes will be made.", fg="yellow"))
    if is_batch:
        print(f"{click.style('Hosts file:', fg='cyan')} {host_file}")
        print(f"{click.style('Host count:', fg='cyan')} {len(hosts)}")
        if windows_hosts:
            print(f"{click.style('Skipped (Windows):', fg='cyan')} {len(windows_hosts)}")
        print(f"{click.style('Hosts:', fg='cyan')}")
        for line in format_host_preview(hosts, limit=DRY_RUN_PREVIEW_LIMIT):
            print(line)
    else:
        print(f"{click.style('Host:', fg='cyan')} {hosts[0] if hosts else '(none)'}")
    print(f"{click.style('Action:', fg='cyan')} run-puppet.sh on {len(hosts)} host(s)")
    if args.reason:
        print(f"{click.style('Reason:', fg='cyan')} {args.reason}")
    print(f"{click.style('Auto-audit after:', fg='cyan')} {'yes' if not args.no_audit else 'no'}")
    print(f"{click.style('Audit log:', fg='cyan')} {audit_log}")
    print(click.style("Run again with --confirm to apply changes.", fg="yellow"))


def _run_puppet_batch(
    hosts: list[str],
    *,
    args: HostRunPuppetArgs,
    ssh_opts: list[str],
    remote_cmd: str,
    actor: str,
    audit_log: Path,
) -> list[dict[str, Any]]:
    """Run puppet on all hosts in parallel and return result dicts."""
    results: list[dict[str, Any]] = []
    log_lock = threading.Lock()
    show_progress = not args.json

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_host = {
            executor.submit(
                run_puppet_for_host,
                host,
                args=args,
                ssh_opts=ssh_opts,
                remote_cmd=remote_cmd,
                actor=actor,
                audit_log=audit_log,
                log_lock=log_lock,
            ): host
            for host in hosts
        }
        with (
            click.progressbar(
                length=len(hosts),
                label="Running puppet",
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
                        "action": "host.run_puppet",
                        "host": host,
                        "ok": False,
                        "ssh_rc": None,
                        "stderr": "",
                        "error": str(e),
                        "observed": {
                            "puppet_exit": None,
                            "changes_applied": False,
                            "duration_s": 0.0,
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

    return results


def _maybe_auto_audit(hosts: list[str], args: HostRunPuppetArgs, audit_log: Path) -> None:
    """Run auto-audit if not suppressed; log warnings on failure."""
    if args.no_audit:
        return
    try:
        _run_auto_audit(hosts, args, audit_log)
    except Exception as exc:
        logger.warning("Auto-audit failed: %s", exc)
        click.echo(
            click.style(f"Warning: auto-audit failed: {exc}", fg="yellow"),
            file=sys.stderr,
        )


def cmd_host_run_puppet(args: HostRunPuppetArgs) -> None:
    """SSH to each host and run puppet, then refresh audit data."""
    ensure_host_or_file(args.host)
    actor = infer_actor()
    ssh_opts = build_ssh_options(args)
    audit_log = Path(args.audit_log) if args.audit_log else default_audit_log_path()

    host_file = None
    if is_host_file(args.host):
        host_file = Path(args.host)
        all_hosts = parse_host_list(host_file)
        is_batch = True
    else:
        all_hosts = [args.host]
        is_batch = False

    windows_hosts = [h for h in all_hosts if is_windows_host(h)]
    hosts = [h for h in all_hosts if not is_windows_host(h)]

    is_non_staging = is_batch and "staging" not in host_file.name.lower()

    def _staging_warn() -> None:
        if is_non_staging:
            click.echo(
                click.style(
                    "WARNING: host list filename does not contain 'staging'."
                    " Running puppet on non-staging hosts may disrupt in-flight tests.",
                    fg="yellow",
                ),
                file=sys.stderr,
            )

    if not args.confirm:
        _staging_warn()
        _print_dry_run(args, hosts, windows_hosts, host_file, is_batch, audit_log)
        return

    if not hosts:
        print("No Linux hosts to run puppet on (all hosts were Windows or list was empty).")
        return

    _staging_warn()
    remote_cmd = remote_run_puppet_script()

    if not is_batch:
        result = run_puppet_for_host(
            hosts[0],
            args=args,
            ssh_opts=ssh_opts,
            remote_cmd=remote_cmd,
            actor=actor,
            audit_log=audit_log,
        )
        rc = result["ssh_rc"]
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(format_puppet_line(result))
            if not result["ok"]:
                print(
                    f"[{hosts[0]}] puppet FAILED (rc={rc}, "
                    f"puppet_exit={result['observed']['puppet_exit']}). "
                    f"stderr:\n{result.get('stderr', '')}",
                    file=sys.stderr,
                )
        _maybe_auto_audit([hosts[0]], args, audit_log)
        if not result["ok"]:
            raise CommandFailureError
        return

    start_time = time.monotonic()
    results = _run_puppet_batch(
        hosts, args=args, ssh_opts=ssh_opts, remote_cmd=remote_cmd, actor=actor, audit_log=audit_log
    )

    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for result in sorted(results, key=lambda r: r["host"]):
            print(format_puppet_line(result))
        total = len(results)
        failed = sum(1 for r in results if not r.get("ok"))
        successful = total - failed
        skipped = len(windows_hosts)
        duration_s = time.monotonic() - start_time
        print(
            f"\nSummary: total={total} successful={successful} failed={failed} "
            f"skipped={skipped} duration={duration_s:.1f}s"
        )
        if windows_hosts:
            print(f"Skipped (Windows): {', '.join(windows_hosts)}")
        print(f"Audit log: {audit_log}")

    _maybe_auto_audit(hosts, args, audit_log)

    if any(not r.get("ok") for r in results):
        raise CommandFailureError


def _run_auto_audit(hosts: list[str], args: HostRunPuppetArgs, audit_log: Path) -> None:
    """Run host-audit on the given hosts to refresh DB observations."""
    from .audit import cmd_host_audit_batch

    audit_args = HostAuditArgs(
        host=args.host,
        ssh_option=args.ssh_option,
        connect_timeout=args.connect_timeout,
        timeout=args.timeout,
        audit_log=str(audit_log),
        json=False,
        no_content=False,
        workers=args.workers,
        batch_timeout=600,
        verbose=False,
        quiet=True,
    )
    cmd_host_audit_batch(hosts, audit_args)
    print(f"Audit refreshed for {len(hosts)} host(s).")
