"""FleetRoll set override command implementation."""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

import click

from ..audit import append_jsonl
from ..constants import BACKUP_TIME_FORMAT
from ..exceptions import UserError
from ..ssh import build_ssh_options, remote_set_script, run_ssh
from ..utils import (
    default_audit_log_path,
    ensure_host_or_file,
    infer_actor,
    is_host_file,
    parse_host_list,
    sha256_hex,
    utc_now_iso,
)

if TYPE_CHECKING:
    from ..cli import Args


def set_override_for_host(
    host: str,
    *,
    args: Args,
    ssh_opts: List[str],
    remote_cmd: str,
    data: bytes,
    actor: str,
    audit_log: Path,
    content_hash: str,
    backup_suffix: str,
    source: str,
    log_lock: threading.Lock | None = None,
) -> Dict[str, Any]:
    """Set override file for a single host and append audit log."""
    rc, out, err = run_ssh(
        host,
        remote_cmd,
        ssh_options=ssh_opts,
        input_bytes=data,
        timeout_s=args.timeout,
    )

    result: Dict[str, Any] = {
        "ts": utc_now_iso(),
        "actor": actor,
        "action": "host.set_override",
        "host": host,
        "override_path": args.override_path,
        "ok": (rc == 0),
        "ssh_rc": rc,
        "stderr": err.strip(),
        "parameters": {
            "source": source,
            "sha256": content_hash,
            "mode": args.mode,
            "owner": args.owner,
            "group": args.group,
            "backup": (not args.no_backup),
            "backup_suffix": backup_suffix,
            "reason": args.reason,
        },
    }

    if log_lock:
        with log_lock:
            append_jsonl(audit_log, result)
    else:
        append_jsonl(audit_log, result)
    return result


def format_set_line(result: Dict[str, Any]) -> str:
    """Format a single-line status for batch set results."""
    host = result.get("host", "?")
    if result.get("ok"):
        return f"OK {host} override set"
    error = result.get("error") or result.get("stderr") or "unknown_error"
    rc = result.get("ssh_rc")
    rc_str = f" rc={rc}" if rc is not None else ""
    return f"FAIL {host}{rc_str} {error}"


def validate_override_syntax(data: bytes) -> None:
    """Validate override content using bash -n."""
    bash_path = shutil.which("bash") or "/bin/bash"
    if not Path(bash_path).exists():
        raise UserError("bash not found; cannot validate override syntax.")
    with tempfile.NamedTemporaryFile("wb", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            [bash_path, "-n", str(tmp_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip() or "syntax error"
            raise UserError(f"Override syntax validation failed: {detail}")
    finally:
        tmp_path.unlink(missing_ok=True)


def cmd_host_set(args: Args) -> None:
    """Set the override file on a host."""
    ensure_host_or_file(args.host)
    actor = infer_actor()
    ssh_opts = build_ssh_options(args)
    audit_log = Path(args.audit_log) if args.audit_log else default_audit_log_path()

    # Load contents (--from-file required)
    if not args.from_file:
        raise UserError("Must specify --from-file.")

    p = Path(args.from_file)
    data = p.read_bytes()
    source = f"file:{p}"

    content_hash = sha256_hex(data)

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
                "action": "host.set_override",
                "host_count": len(hosts),
                "host": None if is_batch else hosts[0],
                "host_file": str(host_file) if is_batch else None,
                "override_path": args.override_path,
                "source": source,
                "sha256": content_hash,
                "mode": args.mode,
                "owner": args.owner,
                "group": args.group,
                "backup": (not args.no_backup),
                "backup_suffix_format": BACKUP_TIME_FORMAT,
                "validate": args.validate,
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
            print(f"Action: set override on {action_target}")
            print(f"Override path: {args.override_path}")
            print(f"Override source: {source}")
            print(f"Override checksum: sha256={content_hash}")
            print(f"Mode: {args.mode} Owner: {args.owner} Group: {args.group}")
            print(f"Validation: {'enabled' if args.validate else 'disabled'}")
            print(f"Backup: {'yes' if not args.no_backup else 'no'}")
            if args.reason:
                print(f"Reason: {args.reason}")
            content_text = data.decode("utf-8", errors="replace")
            print("--- override contents (begin) ---")
            print(content_text, end="" if content_text.endswith("\n") else "\n")
            print("--- override contents (end) ---")
            print(f"Audit log: {audit_log}")
            print("Run again with --confirm to apply changes.")
        return

    if args.validate:
        validate_override_syntax(data)

    backup_suffix = dt.datetime.now(dt.timezone.utc).strftime(BACKUP_TIME_FORMAT)

    remote_cmd = remote_set_script(
        override_path=args.override_path,
        mode=args.mode,
        owner=args.owner,
        group=args.group,
        backup=not args.no_backup,
        backup_suffix=backup_suffix,
    )

    if not is_batch:
        result = set_override_for_host(
            args.host,
            args=args,
            ssh_opts=ssh_opts,
            remote_cmd=remote_cmd,
            data=data,
            actor=actor,
            audit_log=audit_log,
            content_hash=content_hash,
            backup_suffix=backup_suffix,
            source=source,
        )
        rc = result["ssh_rc"]
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
            if rc != 0:
                sys.exit(1)
            return

        if rc != 0:
            print(
                f"[{args.host}] set override FAILED (rc={rc}). stderr:\n{result.get('stderr','')}",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"[{args.host}] override written to {args.override_path}")
        print(f"sha256={content_hash}")
        if not args.no_backup:
            print(f"backup (if existed): {args.override_path}.bak.{backup_suffix}")
        if args.reason:
            print(f"reason: {args.reason}")
        print(f"Audit log: {audit_log}")
        return

    results: List[Dict[str, Any]] = []
    log_lock = threading.Lock()
    show_progress = not args.json
    start_time = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_host = {
            executor.submit(
                set_override_for_host,
                host,
                args=args,
                ssh_opts=ssh_opts,
                remote_cmd=remote_cmd,
                data=data,
                actor=actor,
                audit_log=audit_log,
                content_hash=content_hash,
                backup_suffix=backup_suffix,
                source=source,
                log_lock=log_lock,
            ): host
            for host in hosts
        }
        with (
            click.progressbar(
                length=len(hosts),
                label="Setting overrides",
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
                        "action": "host.set_override",
                        "host": host,
                        "override_path": args.override_path,
                        "ok": False,
                        "ssh_rc": None,
                        "stderr": "",
                        "error": str(e),
                        "parameters": {
                            "source": source,
                            "sha256": content_hash,
                            "mode": args.mode,
                            "owner": args.owner,
                            "group": args.group,
                            "backup": (not args.no_backup),
                            "backup_suffix": backup_suffix,
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
            print(format_set_line(result))
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
