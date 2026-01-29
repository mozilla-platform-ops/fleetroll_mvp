"""FleetRoll audit command implementation."""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ..audit import (
    has_content_file,
    load_latest_vault_checksums,
    process_audit_result,
    store_content_file,
)
from ..constants import (
    AUDIT_DIR_NAME,
    AUDIT_MAX_RETRIES,
    AUDIT_RETRY_DELAY_S,
    OVERRIDES_DIR_NAME,
    VAULT_YAMLS_DIR_NAME,
)
from ..ssh import (
    build_ssh_options,
    remote_audit_script,
    remote_read_file_script,
    run_ssh,
)
from ..utils import (
    default_audit_log_path,
    ensure_host_or_file,
    format_elapsed_time,
    infer_actor,
    is_host_file,
    parse_host_list,
    utc_now_iso,
)

if TYPE_CHECKING:
    from ..cli_types import HostAuditArgs

logger = logging.getLogger("fleetroll")


def format_single_host_quiet(result: dict[str, Any], elapsed_seconds: float) -> str:
    """Format single host audit result in quiet mode.

    HostAuditArgs:
        result: Audit result dictionary
        elapsed_seconds: Elapsed time in seconds

    Returns:
        Single-line formatted output
    """
    host = result["host"]
    elapsed = format_elapsed_time(elapsed_seconds)

    if result.get("ok"):
        return f"✓ {host} ({elapsed})"
    error = result.get("error", result.get("stderr", "unknown"))
    return f"✗ {host}: {error} ({elapsed})"


def format_batch_quiet(summary: dict[str, Any], elapsed_seconds: float) -> str:
    """Format batch audit results in quiet mode.

    HostAuditArgs:
        summary: Summary dictionary with results, total, successful, failed
        elapsed_seconds: Elapsed time in seconds

    Returns:
        Single-line formatted output
    """
    total = summary["total"]
    successful = summary["successful"]
    failed = summary["failed"]
    elapsed = format_elapsed_time(elapsed_seconds)

    if failed == 0:
        symbol = "✓"
        failure_text = ""
    elif failed == total:
        symbol = "✗"
        failure_text = f" ({failed} failed)"
    else:
        symbol = "⚠"
        failure_text = f" ({failed} failed)"

    return f"{symbol} {successful}/{total} hosts successful{failure_text} ({elapsed})"


def format_progress_label(remaining: int, *, elapsed_s: float) -> str:
    """Format the progress bar label with remaining hosts and elapsed time."""
    elapsed_total = int(elapsed_s)
    minutes, seconds = divmod(elapsed_total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        elapsed = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    else:
        elapsed = f"{minutes:02d}:{seconds:02d}"
    return f"Auditing hosts (remaining: {remaining}, elapsed: {elapsed})"


def audit_single_host_with_retry(
    host: str,
    *,
    args: HostAuditArgs,
    ssh_opts: list[str],
    remote_cmd: str,
    audit_log: Path,
    actor: str,
    retry_budget: dict[str, Any],
    lock: threading.Lock,
    log_lock: threading.Lock,
    overrides_dir: Path | None = None,
    vault_checksums: dict[str, str] | None = None,
    vault_dir: Path | None = None,
) -> dict[str, Any]:
    """Audit single host with retry for connection failures."""
    max_retries = AUDIT_MAX_RETRIES
    retry_delay = AUDIT_RETRY_DELAY_S  # Exponential backoff base

    logger.debug("Auditing host: %s", host)

    for attempt in range(max_retries):
        # Check batch timeout
        with lock:
            if time.time() > retry_budget["deadline"]:
                logger.debug("Batch timeout exceeded for %s", host)
                return {
                    "ts": utc_now_iso(),
                    "actor": actor,
                    "action": "host.audit",
                    "host": host,
                    "ok": False,
                    "error": "batch_timeout_exceeded",
                    "attempts": attempt,
                }

        if attempt > 0:
            logger.debug("Retry attempt %d/%d for %s", attempt + 1, max_retries, host)

        rc, out, err = run_ssh(host, remote_cmd, ssh_options=ssh_opts, timeout_s=args.timeout)

        # Check if retryable (connection errors)
        is_connection_error = rc != 0 and (
            "Connection refused" in err
            or "Connection timed out" in err
            or "Could not resolve hostname" in err
            or rc == 255  # SSH general error
        )

        if rc == 0 or not is_connection_error:
            # Success or non-retryable error
            if rc == 0:
                logger.debug("Host %s: audit successful", host)
            else:
                logger.debug("Host %s: non-retryable error (rc=%d)", host, rc)
            result = process_audit_result(
                host,
                rc=rc,
                out=out,
                err=err,
                args=args,
                audit_log=audit_log,
                actor=actor,
                overrides_dir=overrides_dir,
                vault_sha256=(vault_checksums.get(host) if vault_checksums else None),
                log_lock=log_lock,
            )
            vault_sha = result.get("observed", {}).get("vault_sha256")
            vault_present = result.get("observed", {}).get("vault_present")
            if vault_present and vault_sha and vault_dir:
                if not has_content_file(vault_sha, vault_dir):
                    vault_cmd = remote_read_file_script(args.vault_path)
                    v_rc, v_out, v_err = run_ssh(
                        host,
                        vault_cmd,
                        ssh_options=ssh_opts,
                        timeout_s=args.timeout,
                    )
                    if v_rc == 0:
                        stored_path = store_content_file(v_out, vault_sha, vault_dir)
                        result["observed"]["vault_file_path"] = str(stored_path)
                    else:
                        result["observed"]["vault_fetch_error"] = v_err.strip()
            result["attempts"] = attempt + 1
            return result

        # Retry with exponential backoff
        if attempt < max_retries - 1:
            delay = retry_delay * (2**attempt)
            logger.debug("Host %s: connection error, retrying in %ds", host, delay)
            time.sleep(delay)

    # Max retries exceeded
    logger.debug("Host %s: max retries exceeded", host)
    return {
        "ts": utc_now_iso(),
        "actor": actor,
        "action": "host.audit",
        "host": host,
        "ok": False,
        "error": "max_retries_exceeded",
        "attempts": max_retries,
        "stderr": err.strip(),
    }


def format_summary_table(summary: dict[str, Any], verbose: bool = False) -> str:
    """Format batch audit results as human-readable table."""
    results = summary["results"]
    total = summary["total"]
    successful = summary["successful"]
    failed = summary["failed"]
    unique_overrides = summary.get("unique_overrides", {})

    lines = []
    lines.append("\nSummary:")
    lines.append("=" * 60)
    lines.append(f"Total hosts:        {total}")
    lines.append(f"Successful:         {successful} ({100 * successful / total:.1f}%)")
    lines.append(f"Failed:             {failed} ({100 * failed / total:.1f}%)")

    # Categorize failures
    conn_failures = sum(
        1
        for r in results
        if not r.get("ok")
        and ("timeout" in r.get("error", "").lower() or "connection" in r.get("stderr", "").lower())
    )
    if failed > 0:
        lines.append(f"  - Connection:     {conn_failures}")
        lines.append(f"  - Other:          {failed - conn_failures}")

    lines.append("\nStatus by Host:")
    lines.append("-" * 60)

    for result in sorted(results, key=lambda r: r["host"]):
        host = result["host"]
        if result.get("ok"):
            obs = result.get("observed", {})
            override = obs.get("override_present", False)
            status = "✓"
            sha256 = obs.get("override_sha256", "")
            if override and sha256:
                detail = f"OVERRIDE [sha256: {sha256[:12]}...]"
            else:
                detail = "OVERRIDE" if override else "no-override"
            lines.append(f"{status} {host} ({detail})")

            if verbose:
                role = obs.get("role", "(unknown)")
                lines.append(f"    Role: {role}")
                if override:
                    meta = obs.get("override_meta", {})
                    lines.append(
                        f"    Override: mode={meta.get('mode')} "
                        f"owner={meta.get('owner')} size={meta.get('size')}"
                    )
                    if sha256:
                        lines.append(f"    SHA256: {sha256[:16]}...")
                lines.append(f"    Attempts: {result.get('attempts', 1)}")
        else:
            status = "✗"
            error = result.get("error", result.get("stderr", "unknown"))
            lines.append(f"{status} {host} ({error})")

    # Unique overrides section
    if unique_overrides:
        lines.append("\nUnique Override Contents:")
        lines.append("=" * 60)
        # Sort by number of hosts (descending) to show most common first
        sorted_overrides = sorted(
            unique_overrides.items(),
            key=lambda item: len(item[1][1]),
            reverse=True,
        )
        for idx, (sha, (content, hosts)) in enumerate(sorted_overrides, 1):
            host_count = len(hosts)

            if verbose:
                # Verbose mode: show file paths instead of inline content
                lines.append(
                    f"[{idx}] SHA256: {sha[:12]}... ({host_count} host{'s' if host_count != 1 else ''})"
                )
                lines.append(f"    Stored: ~/{AUDIT_DIR_NAME}/{OVERRIDES_DIR_NAME}/{sha[:12]}")
            else:
                # Non-verbose mode: show inline content
                lines.append(
                    f"[{idx}] SHA256: {sha[:12]}... ({host_count} host{'s' if host_count != 1 else ''})"
                )
                lines.append("--- content_start ---")
                lines.append(content.rstrip("\n"))
                lines.append("--- content_end ---")

            if idx < len(sorted_overrides):
                lines.append("")  # Blank line between entries

    return "\n".join(lines)


def cmd_host_audit_batch(hosts: list[str], args: HostAuditArgs) -> dict[str, Any]:
    """Audit multiple hosts in parallel."""
    actor = infer_actor()
    ssh_opts = build_ssh_options(args)
    audit_log = Path(args.audit_log) if args.audit_log else default_audit_log_path()
    overrides_dir = audit_log.parent / OVERRIDES_DIR_NAME
    vault_checksums = load_latest_vault_checksums(audit_log)
    vault_dir = audit_log.parent / VAULT_YAMLS_DIR_NAME

    remote_cmd = remote_audit_script(
        override_path=args.override_path,
        role_path=args.role_path,
        vault_path=args.vault_path,
        include_content=not args.no_content,
    )

    results = []
    lock = threading.Lock()
    log_lock = threading.Lock()
    retry_budget = {"deadline": time.time() + args.batch_timeout}

    # Determine if we should show progress bar
    show_progress = not args.json and not getattr(args, "quiet", False)
    completed = 0
    progress_start = time.monotonic()
    progress_label = format_progress_label(len(hosts), elapsed_s=0)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_host = {
            executor.submit(
                audit_single_host_with_retry,
                host,
                args=args,
                ssh_opts=ssh_opts,
                remote_cmd=remote_cmd,
                audit_log=audit_log,
                actor=actor,
                retry_budget=retry_budget,
                lock=lock,
                log_lock=log_lock,
                overrides_dir=overrides_dir,
                vault_checksums=vault_checksums,
                vault_dir=vault_dir,
            ): host
            for host in hosts
        }

        # Use click.progressbar in batch mode, nullcontext in JSON mode
        with (
            click.progressbar(
                length=len(hosts),
                label=progress_label,
                show_eta=True,
                show_percent=True,
                file=sys.stderr,
            )
            if show_progress
            else nullcontext()
        ) as bar:
            for future in as_completed(future_to_host):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    host = future_to_host[future]
                    results.append(
                        {
                            "host": host,
                            "ok": False,
                            "error": str(e),
                            "ts": utc_now_iso(),
                        }
                    )

                # Update progress bar (only if showing)
                if show_progress:
                    completed += 1
                    remaining = len(hosts) - completed
                    bar.label = format_progress_label(
                        remaining, elapsed_s=time.monotonic() - progress_start
                    )
                    bar.update(1)

    # Collect unique overrides by SHA256
    unique_overrides: dict[str, tuple[str, list[str]]] = {}
    for r in results:
        if r.get("ok") and r.get("observed", {}).get("override_present"):
            sha = r["observed"].get("override_sha256")
            content = r["observed"].get("override_contents_for_display", "")
            host = r["host"]
            if sha and content:
                if sha not in unique_overrides:
                    unique_overrides[sha] = (content, [])
                unique_overrides[sha][1].append(host)

    return {
        "results": results,
        "total": len(hosts),
        "successful": sum(1 for r in results if r.get("ok")),
        "failed": sum(1 for r in results if not r.get("ok")),
        "unique_overrides": unique_overrides,
    }


def format_single_host_output(result: dict[str, Any], args: HostAuditArgs) -> None:
    """Format and print output for a single host audit result."""
    host = result["host"]
    obs = result.get("observed", {})

    # Handle failed audits
    if not result.get("ok"):
        error = result.get("error", result.get("stderr", "unknown"))
        print(f"Host: {host}")
        print(f"Status: FAILED ({error})")
        if result.get("stderr"):
            print(f"stderr: {result['stderr']}")
        return

    override_present = obs.get("override_present", False)
    role_present = obs.get("role_present", False)
    content_hash = obs.get("override_sha256")
    vault_hash = obs.get("vault_sha256")
    vault_present = obs.get("vault_present")

    print(f"Host: {host}")
    if role_present:
        role = obs.get("role", "")
        print(f"Role: {role.strip() if role else '(empty)'}")
    else:
        print("Role: (missing or unreadable)")

    if override_present:
        meta = obs.get("override_meta") or {}
        print(f"Override: PRESENT at {args.override_path}")
        print(
            f"  mode={meta.get('mode')} owner={meta.get('owner')} "
            f"group={meta.get('group')} size={meta.get('size')} "
            f"mtime_epoch={meta.get('mtime_epoch')}"
        )
        if content_hash:
            print(f"  sha256={content_hash}")
        if args.no_content:
            print("  contents: (suppressed; use without --no-content to display)")
        else:
            content = obs.get("override_contents_for_display", "")
            if content:
                print("\n--- override contents ---")
                sys.stdout.write(content)
                if not content.endswith("\n"):
                    sys.stdout.write("\n")
                print("--- end override contents ---")
        if "override_file_path" in obs:
            print(f"  stored: {obs['override_file_path']}")
    else:
        print(f"Override: NOT PRESENT at {args.override_path}")
    if vault_present is None:
        print("Vault: UNKNOWN (no data)")
    elif vault_present:
        meta = obs.get("vault_meta") or {}
        print(f"Vault: PRESENT at {args.vault_path}")
        print(
            f"  mode={meta.get('mode')} owner={meta.get('owner')} "
            f"group={meta.get('group')} size={meta.get('size')} "
            f"mtime_epoch={meta.get('mtime_epoch')}"
        )
        if vault_hash:
            print(f"  sha256={vault_hash}")
        if "vault_file_path" in obs:
            print(f"  stored: {obs['vault_file_path']}")
        if "vault_fetch_error" in obs:
            print(f"  fetch_error: {obs['vault_fetch_error']}")
    else:
        print(f"Vault: NOT PRESENT at {args.vault_path}")


def cmd_host_audit(args: HostAuditArgs) -> None:
    """Audit command - handles both single host and batch mode."""
    start_time = time.time()
    ensure_host_or_file(args.host)
    quiet = getattr(args, "quiet", False)

    # Determine hosts to audit
    if is_host_file(args.host):
        host_file = Path(args.host)
        hosts = parse_host_list(host_file)
        is_batch = True
    else:
        hosts = [args.host]
        is_batch = False

    # Show progress message for batch mode
    if is_batch and not args.json and not quiet:
        print(f"Auditing {len(hosts)} hosts from {host_file} with {args.workers} workers...")

    # Always use batch logic (works for single host too, provides retry)
    summary = cmd_host_audit_batch(hosts, args)

    audit_log = Path(args.audit_log) if args.audit_log else default_audit_log_path()
    elapsed_seconds = time.time() - start_time

    # Output formatting depends on single vs batch mode
    if args.json:
        # JSON output: single host returns just the result, batch returns summary
        if is_batch:
            print(json.dumps(summary, indent=2, sort_keys=True))
            if summary["failed"] > 0:
                sys.exit(1)
        else:
            # For single host JSON, return just the result (not wrapped in summary)
            print(json.dumps(summary["results"][0], indent=2, sort_keys=True))
            if not summary["results"][0].get("ok", False):
                sys.exit(1)
    elif quiet:
        # Quiet mode: single-line output
        if is_batch:
            print(format_batch_quiet(summary, elapsed_seconds))
            if summary["failed"] > 0:
                sys.exit(1)
        else:
            print(format_single_host_quiet(summary["results"][0], elapsed_seconds))
            if not summary["results"][0].get("ok", False):
                sys.exit(1)
    elif is_batch:
        # Batch mode: show summary table
        print(format_summary_table(summary, verbose=args.verbose))
        print(f"\nAudit log: {audit_log}")

        # Check if any results have stored override files
        has_stored_files = any(
            r.get("observed", {}).get("override_file_path") for r in summary["results"]
        )
        if has_stored_files:
            overrides_dir = audit_log.parent / OVERRIDES_DIR_NAME
            print(f"Override files: {overrides_dir}")
    else:
        # Single host mode: show detailed output
        format_single_host_output(summary["results"][0], args)
        print(f"\nAudit log: {audit_log}")
