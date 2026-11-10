"""Read-only live health diagnosis; independent of idle/action eligibility."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..constants import SSH_TIMEOUT_EXIT_CODE
from ..exceptions import CommandFailureError, UserError
from ..ssh import build_ssh_options, remote_diagnose_script, run_ssh
from ..utils import expand_hostname, looks_like_host, utc_now_iso

if TYPE_CHECKING:
    from ..cli_types import HostDiagnoseArgs

_PREFIX = "FLEETROLL_DIAGNOSE_"


def _timestamp_valid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def _validate_health(health: dict[str, Any], *, exit_code: int) -> str | None:
    """Validate common health fields and independent upstream exit semantics."""
    checks = health.get("checks")
    if (
        type(health.get("ok")) is not bool
        or not isinstance(health.get("host"), str)
        or not health["host"]
        or not isinstance(health.get("state"), str)
        or not health["state"]
        or not _timestamp_valid(health.get("ts"))
        or not isinstance(checks, list)
        or not checks
    ):
        return "invalid_gwhc_report"
    for check in checks:
        if (
            not isinstance(check, dict)
            or not isinstance(check.get("name"), str)
            or not check["name"]
            or check.get("status") not in ("pass", "warn", "fail")
            or not isinstance(check.get("detail"), str)
            or not isinstance(check.get("data"), dict)
        ):
            return "invalid_gwhc_checks"
    healthy = not any(check["status"] == "fail" for check in checks)
    if health["ok"] != healthy or healthy != (exit_code == 0):
        return "inconsistent_gwhc_result"
    return None


def _pid_list_valid(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(type(pid) is int and pid > 0 for pid in value)
        and len(value) == len(set(value))
    )


def _validated_process_states(data: dict[str, Any]) -> dict[int, str] | None:
    """Return process states only when PID lists and process records agree."""
    zombies = data["zombie_pids"]
    processes = data["processes"]
    unverified = data.get("unverified_pids", [])
    if (
        not _pid_list_valid(zombies)
        or not _pid_list_valid(unverified)
        or not isinstance(processes, list)
    ):
        return None
    states = {}
    for process in processes:
        if (
            not isinstance(process, dict)
            or type(process.get("pid")) is not int
            or process["pid"] <= 0
            or type(process.get("ppid")) is not int
            or process["ppid"] < 0
            or not isinstance(process.get("state"), str)
            or process["state"] not in ("R", "S", "D", "Z", "T", "t", "X", "x", "K", "W", "P", "I")
            or process["pid"] in states
        ):
            return None
        states[process["pid"]] = process["state"]
    if set(zombies) != {pid for pid, state in states.items() if state == "Z"}:
        return None
    if set(unverified) & states.keys():
        return None
    return states


def _normalize_v1_zombie(checks: list[dict[str, Any]]) -> dict[str, str]:
    """Interpret explicit process evidence; summary state never supplies it."""
    unknown = {"status": "unknown", "reason": "invalid_zombie_evidence"}
    process_checks = [check for check in checks if check["name"] == "generic_worker_process"]
    if not process_checks:
        return {"status": "unknown", "reason": "missing_zombie_evidence"}
    if len(process_checks) != 1:
        return unknown
    check = process_checks[0]
    data = check["data"]
    if "zombie_pids" not in data or "processes" not in data:
        return {"status": "unknown", "reason": "missing_zombie_evidence"}
    states = _validated_process_states(data)
    if states is None or (data["zombie_pids"] and check["status"] != "fail"):
        return unknown
    if data["zombie_pids"]:
        return {"status": "present", "reason": "confirmed_zombie_processes"}
    # A failed process check or unreadable process cannot establish absence.
    unverified = (
        data.get("unverified_pids")
        or check["status"] == "fail"
        or not states
        or any(state in ("X", "x") for state in states.values())
    )
    return {
        "status": "unknown" if unverified else "absent",
        "reason": "unverified_process_inspection" if unverified else "verified_process_inspection",
    }


def _collection_error(markers: dict[str, str], *, ssh_rc: int) -> tuple[str | None, int | None]:
    if ssh_rc != 0:
        return ("ssh_timeout" if ssh_rc == SSH_TIMEOUT_EXIT_CODE else "ssh_failed"), None
    if markers.get("ERROR"):
        return markers["ERROR"], None
    if markers.get("OS_TYPE") != "Linux":
        return "unsupported_os", None
    if not _timestamp_valid(markers.get("CHECKED_AT")):
        return "check_timestamp_invalid", None
    try:
        return None, int(markers["GWHC_EXIT"])
    except (KeyError, ValueError):
        return "gwhc_exit_missing", None


def parse_diagnose_output(stdout: str, *, ssh_rc: int) -> dict[str, Any]:
    """Preserve unhealthy reports without mistaking them for collection failures."""
    envelope, separator, payload = stdout.partition(f"{_PREFIX}JSON_BEGIN=\n")
    markers = {}
    for line in envelope.splitlines():
        if line.startswith(_PREFIX) and "=" in line:
            key, value = line[len(_PREFIX) :].split("=", 1)
            markers[key] = value
    result: dict[str, Any] = {
        "schema_version": 2,
        "ok": False,
        "collection_status": "failed",
        "reason": None,
        "checked_at": markers.get("CHECKED_AT"),
        "os_type": markers.get("OS_TYPE"),
        "ssh_exit_code": ssh_rc,
        "gwhc_exit_code": None,
        "gwhc_schema_version": None,
        "contract_status": "unknown",
        "health": "unknown",
        "gwhc_state": None,
        "gwhc_ts": None,
        "reported_host": None,
        "checks": [],
        "gwhc": None,
        "zombie": {"status": "unknown", "reason": "diagnosis_unavailable"},
    }
    error, exit_code = _collection_error(markers, ssh_rc=ssh_rc)
    if error:
        result["reason"] = error
        return result
    result["gwhc_exit_code"] = exit_code
    try:
        health = json.loads(payload) if separator else None
    except ValueError:
        health = None
    if not isinstance(health, dict):
        result["reason"] = "invalid_gwhc_json" if exit_code in (0, 1) else "gwhc_execution_failed"
        return result
    # Keep structured evidence even if it cannot be interpreted or trusted.
    result["gwhc"] = health
    result["gwhc_schema_version"] = health.get("schema_version")
    if exit_code not in (0, 1):
        result["reason"] = "gwhc_execution_failed"
        return result
    versioned = "schema_version" in health
    if versioned and (type(health["schema_version"]) is not int or health["schema_version"] != 1):
        result.update(contract_status="unsupported", reason="unsupported_gwhc_schema")
        result["zombie"]["reason"] = "unsupported_gwhc_schema"
        return result
    result["contract_status"] = "supported" if versioned else "legacy"
    error = _validate_health(health, exit_code=exit_code)
    if error:
        result["reason"] = error
        return result
    result.update(
        ok=True,
        collection_status="completed",
        reason="diagnosis_completed",
        health="healthy" if health["ok"] else "unhealthy",
        gwhc_state=health["state"],
        gwhc_ts=health["ts"],
        reported_host=health["host"],
        checks=health["checks"],
        zombie=(
            _normalize_v1_zombie(health["checks"])
            if versioned
            else {"status": "unknown", "reason": "unversioned_gwhc_schema"}
        ),
    )
    return result


def cmd_host_diagnose(args: HostDiagnoseArgs) -> None:
    """Emit a live snapshot without persisting observations or changing the host."""
    if not looks_like_host(args.host):
        raise UserError("host diagnose requires exactly one hostname; host files are unsupported.")
    user, separator, hostname = args.host.rpartition("@")
    host = f"{user}@{expand_hostname(hostname)}" if separator else expand_hostname(args.host)
    try:
        ssh_rc, stdout, stderr = run_ssh(
            host,
            remote_diagnose_script(),
            ssh_options=build_ssh_options(args),
            timeout_s=args.timeout,
        )
        result = parse_diagnose_output(stdout, ssh_rc=ssh_rc)
        result["stderr"] = stderr.strip()
    except Exception as exc:
        result = parse_diagnose_output("", ssh_rc=1)
        result.update(reason="ssh_error", error=f"{type(exc).__name__}: {exc}", ssh_exit_code=None)
    result.update(host=host, ts=utc_now_iso())
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Host: {host}")
        print(f"Checked at: {result['checked_at'] or result['ts']}")
        print(f"Collection: {result['collection_status']}; health: {result['health']}")
        print(f"Reason: {result['reason']}; gwhc state: {result['gwhc_state']}")
        print(f"Zombie: {result['zombie']['status']} ({result['zombie']['reason']})")
        print("Snapshot only; diagnostic evidence does not authorize recovery.")
    if not result["ok"]:
        raise CommandFailureError
