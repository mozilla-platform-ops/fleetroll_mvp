"""FleetRoll set vault command implementation."""

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

from ..audit import append_jsonl, store_content_file
from ..constants import BACKUP_TIME_FORMAT, VAULT_YAMLS_DIR_NAME
from ..exceptions import UserError
from ..humanhash import humanize
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
    from ..cli_types import HostSetVaultArgs, VaultShowArgs


def resolve_vault_path(sha_prefix: str, *, vault_dir: Path) -> Path:
    """Resolve a vault file by SHA prefix in the vault directory."""
    if not vault_dir.exists():
        raise UserError(f"Vault directory not found: {vault_dir}")

    matches: list[Path] = []
    for entry in vault_dir.iterdir():
        if entry.is_symlink():
            continue
        if entry.is_file() and entry.name.startswith(sha_prefix):
            matches.append(entry)

    if not matches:
        raise UserError(f"No vault file found for prefix: {sha_prefix}")
    if len(matches) > 1:
        choices = ", ".join(sorted(p.name for p in matches))
        raise UserError(f"Ambiguous prefix '{sha_prefix}', matches: {choices}")
    return matches[0]


def resolve_vault_humanhash(human_hash: str, *, vault_dir: Path) -> Path:
    """Resolve a vault file by 2-word humanhash."""
    if not vault_dir.exists():
        raise UserError(f"Vault directory not found: {vault_dir}")

    matches: list[tuple[Path, str]] = []
    for entry in vault_dir.iterdir():
        if entry.is_symlink():
            continue
        if not entry.is_file():
            continue
        content = entry.read_bytes()
        sha = sha256_hex(content)
        if humanize(sha, words=2) == human_hash:
            matches.append((entry, sha))

    if not matches:
        raise UserError(f"No vault file found for human-hash: {human_hash}")
    if len(matches) > 1:
        choices = "\n".join(
            f"- {sha[:12]} ({path.name})"
            for path, sha in sorted(matches, key=lambda item: (item[1], item[0].name))
        )
        raise UserError(f"Ambiguous human-hash '{human_hash}', matches:\n{choices}")
    return matches[0][0]


def cmd_vault_show(args: VaultShowArgs) -> None:
    """Print stored vault contents by SHA prefix or humanhash."""
    audit_log = Path(args.audit_log) if args.audit_log else default_audit_log_path()
    vault_dir = audit_log.parent / VAULT_YAMLS_DIR_NAME
    try:
        vault_path = resolve_vault_path(args.sha_prefix, vault_dir=vault_dir)
    except UserError as exc:
        if "No vault file found for prefix" not in str(exc):
            raise
        vault_path = resolve_vault_humanhash(args.sha_prefix, vault_dir=vault_dir)
    print(vault_path.read_text(encoding="utf-8", errors="replace"), end="")


def set_vault_for_host(
    host: str,
    *,
    args: HostSetVaultArgs,
    ssh_opts: list[str],
    remote_cmd: str,
    data: bytes,
    actor: str,
    audit_log: Path,
    content_hash: str,
    backup_suffix: str,
    source: str,
    stored_path: Path | None = None,
    log_lock: threading.Lock | None = None,
) -> dict[str, Any]:
    """Set vault file for a single host and append audit log."""
    rc, out, err = run_ssh(
        host,
        remote_cmd,
        ssh_options=ssh_opts,
        input_bytes=data,
        timeout_s=args.timeout,
    )

    result: dict[str, Any] = {
        "ts": utc_now_iso(),
        "actor": actor,
        "action": "host.set_vault",
        "host": host,
        "vault_path": args.vault_path,
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
            "stored_path": str(stored_path) if stored_path else None,
        },
    }

    if log_lock:
        with log_lock:
            append_jsonl(audit_log, result)
    else:
        append_jsonl(audit_log, result)
    return result


def format_set_line(result: dict[str, Any]) -> str:
    """Format a single-line status for batch set results."""
    host = result.get("host", "?")
    if result.get("ok"):
        return f"OK {host} vault set"
    error = result.get("error") or result.get("stderr") or "unknown_error"
    rc = result.get("ssh_rc")
    rc_str = f" rc={rc}" if rc is not None else ""
    return f"FAIL {host}{rc_str} {error}"


def cmd_host_set_vault(args: HostSetVaultArgs) -> None:
    """Set the vault file on a host."""
    ensure_host_or_file(args.host)
    actor = infer_actor()
    ssh_opts = build_ssh_options(args)
    audit_log = Path(args.audit_log) if args.audit_log else default_audit_log_path()

    if not args.from_file:
        raise UserError("Must specify --from-file for vault contents.")

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
                "action": "host.set_vault",
                "host_count": len(hosts),
                "host": None if is_batch else hosts[0],
                "host_file": str(host_file) if is_batch else None,
                "vault_path": args.vault_path,
                "source": source,
                "sha256": content_hash,
                "mode": args.mode,
                "owner": args.owner,
                "group": args.group,
                "backup": (not args.no_backup),
                "backup_suffix_format": BACKUP_TIME_FORMAT,
                "reason": args.reason,
                "audit_log": str(audit_log),
                "vault_store_dir": str(audit_log.parent / VAULT_YAMLS_DIR_NAME),
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
            print(f"Action: set vault on {action_target}")
            print(f"Vault path: {args.vault_path}")
            print(f"Vault source: {source}")
            print(f"Vault checksum: sha256={content_hash}")
            print(f"Mode: {args.mode} Owner: {args.owner} Group: {args.group}")
            print(f"Backup: {'yes' if not args.no_backup else 'no'}")
            if args.reason:
                print(f"Reason: {args.reason}")
            print("Vault contents: (suppressed)")
            print(f"Local store: {audit_log.parent / VAULT_YAMLS_DIR_NAME}")
            print(f"Audit log: {audit_log}")
            print("Run again with --confirm to apply changes.")
        return

    backup_suffix = dt.datetime.now(dt.UTC).strftime(BACKUP_TIME_FORMAT)
    vault_dir = audit_log.parent / VAULT_YAMLS_DIR_NAME
    content_text = data.decode("utf-8", errors="replace")
    stored_path = store_content_file(content_text, content_hash, vault_dir)

    remote_cmd = remote_set_script(
        override_path=args.vault_path,
        mode=args.mode,
        owner=args.owner,
        group=args.group,
        backup=not args.no_backup,
        backup_suffix=backup_suffix,
    )

    if not is_batch:
        result = set_vault_for_host(
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
            stored_path=stored_path,
        )
        rc = result["ssh_rc"]
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
            if rc != 0:
                sys.exit(1)
            return

        if rc != 0:
            print(
                f"[{args.host}] set vault FAILED (rc={rc}). stderr:\n{result.get('stderr', '')}",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"[{args.host}] vault written to {args.vault_path}")
        print(f"sha256={content_hash}")
        print(f"stored: {stored_path}")
        if not args.no_backup:
            print(f"backup (if existed): {args.vault_path}.bak.{backup_suffix}")
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
                set_vault_for_host,
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
                stored_path=stored_path,
                log_lock=log_lock,
            ): host
            for host in hosts
        }
        with (
            click.progressbar(
                length=len(hosts),
                label="Setting vaults",
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
                        "action": "host.set_vault",
                        "host": host,
                        "vault_path": args.vault_path,
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
                            "stored_path": str(stored_path) if stored_path else None,
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
