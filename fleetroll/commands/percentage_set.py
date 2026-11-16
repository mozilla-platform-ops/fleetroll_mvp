"""Execute role-percentage additions through the existing override worker."""

from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..constants import BACKUP_TIME_FORMAT
from ..exceptions import CommandFailureError
from ..percentage_deployment import describe_percentage_plan, percentage_preflight
from ..ssh import remote_set_script
from ._auto_audit import _maybe_auto_audit
from .override_safety import check_worker_type
from .set import format_set_line, set_override_for_host

if TYPE_CHECKING:
    from ..cli_types import HostSetOverrideArgs


def cmd_percentage_set(
    args: HostSetOverrideArgs,
    *,
    data: bytes,
    source: str,
    actor: str,
    audit_log: Path,
    ssh_opts: list[str],
) -> None:
    plan = args.percentage_plan
    assert plan is not None  # noqa: S101 - internal dispatch invariant
    report = describe_percentage_plan(plan)
    checks = check_worker_type(data, hosts=plan.additions)
    incompatible = any(not check["compatible"] for check in checks)
    blocked = (
        "worker_type_incompatible" if incompatible and not args.allow_worker_type_mismatch else None
    )
    if args.confirm and blocked is None:
        blocked = percentage_preflight(plan)
    results = []
    if args.confirm and blocked is None and plan.additions:
        suffix = datetime.now(UTC).strftime(BACKUP_TIME_FORMAT)
        lock = threading.Lock()
        hosts = {h.host: h for h in plan.scope}
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [
                executor.submit(
                    set_override_for_host,
                    host,
                    args=args,
                    ssh_opts=ssh_opts,
                    remote_cmd=remote_set_script(
                        mode=args.mode,
                        owner=args.owner,
                        group=args.group,
                        backup=not args.no_backup,
                        backup_suffix=suffix,
                        expected_role=hosts[host].role,
                        desired_checksum=plan.checksum,
                    ),
                    data=data,
                    actor=actor,
                    audit_log=audit_log,
                    content_hash=plan.checksum,
                    backup_suffix=suffix,
                    source=source,
                    log_lock=lock,
                )
                for host in plan.additions
            ]
            results = [future.result() for future in as_completed(futures)]
        results.sort(key=lambda r: r["host"])
        with redirect_stdout(sys.stderr) if args.json else nullcontext():
            _maybe_auto_audit([r["host"] for r in results], args, audit_log)
    successful = sum(bool(r["ok"]) for r in results)
    matching = len(report["recorded_matching"]) + successful
    denominator = report["denominator"]
    failed = sum(not r["ok"] for r in results)
    payload = {
        "schema_version": 1,
        "action": plan.action,
        "dry_run": not args.confirm,
        "status": "blocked"
        if blocked
        else "failed"
        if failed
        else "completed"
        if args.confirm and results
        else report["status"],
        "plan": report,
        "blocked_reason": blocked,
        "results": results,
        "worker_type_checks": checks,
        "allow_worker_type_mismatch": args.allow_worker_type_mismatch,
        "failed": failed,
        "deployment": {
            "source": source,
            "sha256": plan.checksum,
            "mode": args.mode,
            "owner": args.owner,
            "group": args.group,
            "backup": not args.no_backup,
            "reason": args.reason,
            "validate": args.validate,
        },
        "refresh_requested": bool(results) and not args.no_audit,
        "achieved_coverage": {
            "matching_count": matching,
            "denominator": denominator,
            "percent": 100 * matching / denominator if denominator else None,
            "basis": "recorded_existing_plus_successful_file_operations",
        },
        "puppet_convergence": "not_checked",
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("PERCENTAGE DEPLOYMENT" if args.confirm else "DRY RUN: no hosts will be changed")
        print(
            f"Role: {plan.role}; source: {plan.source}; status: {report['status']}\n"
            f"Population: {denominator}; current matching: {len(report['recorded_matching'])}; "
            f"target: {plan.target_percent}% = {report['target_count']} host(s)\n"
            f"Override SHA256: {plan.checksum}"
        )
        print(
            f"Source: {source}; mode={args.mode} owner={args.owner} group={args.group}; "
            f"backup={not args.no_backup}; validation={args.validate}; reason={args.reason or '-'}"
        )
        for check in checks:
            print(
                f"Worker type {check['host']}: requested={check['requested']} "
                f"expected={check['expected']} compatible={check['compatible']} "
                f"observed_at={check['observed_at']} reason={check['reason'] or '-'}"
            )
        for key in ("recorded_matching", "additions", "controls", "excluded", "unknown_membership"):
            print(f"{key}: {report[key]}")
        for host in report["scope"]:
            print(
                f"  {host['host']} role={host['role']} observed={host['observation_ts']} "
                f"freshness={host['freshness']} latest_ok={host['latest_attempt_ok']} "
                f"latest_attempt={host['latest_attempt_ts']} checksum={host['checksum']}"
            )
        if blocked:
            print(f"BLOCKED: {blocked}")
        for result in results:
            print(format_set_line(result))
        print(f"Coverage estimate: {matching}/{denominator}; Puppet convergence not checked.")
        if not args.confirm:
            print("Use --confirm to execute; --save-plan/--from-plan freeze a reviewed selection.")
    if args.confirm and (blocked or payload["failed"]):
        raise CommandFailureError
