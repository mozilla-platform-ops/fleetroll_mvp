"""Shared validation of upstream gwhc evidence; no action eligibility."""

from __future__ import annotations

import base64
import json
import math
from datetime import UTC, datetime
from typing import Any

from .constants import SSH_TIMEOUT_EXIT_CODE

GWHC_STALE_SECONDS = 900
DISK_MIN_FREE_GIB = 5
DISK_MIN_FREE_PCT = 10

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


def parse_recorded_gwhc(info: dict[str, str], *, ssh_rc: int) -> dict[str, Any]:
    """Adapt the audit transport to the same validation used by live diagnosis."""
    error = info.get("GWHC_ERROR")
    if info.get("OS_TYPE") and info["OS_TYPE"] != "Linux":
        error = "unsupported_os"
    payload = ""
    try:
        raw = base64.b64decode(info.get("GWHC_STDOUT_B64", ""), validate=True)
        if len(raw) > 262144:
            error = "gwhc_output_too_large"
        else:
            payload = raw.decode("utf-8")
    except (ValueError, UnicodeError):
        error = "invalid_gwhc_encoding"
    if info.get("GWHC_EXIT") in ("124", "137"):
        error = "gwhc_timeout"
    if "GWHC_CHECKED_AT" not in info and not error:
        error = "not_collected"
    markers = {
        "CHECKED_AT": info.get("GWHC_CHECKED_AT", ""),
        "OS_TYPE": info.get("OS_TYPE", ""),
        "GWHC_EXIT": info.get("GWHC_EXIT", ""),
    }
    if error:
        markers["ERROR"] = error
    transport = "\n".join(f"{_PREFIX}{key}={value}" for key, value in markers.items())
    result = parse_diagnose_output(transport + f"\n{_PREFIX}JSON_BEGIN=\n" + payload, ssh_rc=ssh_rc)
    result["source"] = "sudo -n gwhc --json"
    result["stdout"] = payload
    result["gwhc_worker"] = normalize_worker(result)
    result["gwhc_disks"] = normalize_disks(result)
    return result


def _check(evidence: dict[str, Any], name: str) -> dict[str, Any] | None:
    if (
        evidence.get("schema_version") != 2
        or evidence.get("ok") is not True
        or evidence.get("contract_status") != "supported"
    ):
        return None
    checks = [c for c in evidence.get("checks", []) if c.get("name") == name]
    return checks[0] if len(checks) == 1 else None


def normalize_worker(evidence: dict[str, Any]) -> dict[str, str]:
    """Use verified generic-worker processes, independent of check health or TC."""
    unknown = {"status": "unknown", "reason": "process_evidence_unavailable"}
    check = _check(evidence, "generic_worker_process")
    if check is None:
        return unknown
    data = check["data"]
    if "processes" not in data or "zombie_pids" not in data:
        return unknown
    states = _validated_process_states(data)
    pids = data.get("generic_worker_pids")
    if states is None or not _pid_list_valid(pids):
        return unknown
    live = {pid for pid, state in states.items() if state not in ("Z", "X", "x")}
    if set(pids) != live or (not live and data.get("unverified_pids")):
        return unknown
    if live:
        return {"status": "running", "reason": "verified_live_process"}
    return {"status": "stopped", "reason": "no_live_process"}


def _number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def normalize_disks(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Retain path scope and units; absolute free space cannot supply percentages."""
    check = _check(evidence, "disk_free")
    if check is None:
        return []
    disks = []
    for path, values in sorted(check["data"].items()):
        if not path.startswith("/"):
            continue
        disk: dict[str, Any] = {
            "path": path,
            "free_gib": None,
            "free_pct": None,
            "total_gib": None,
            "status": "unknown",
        }
        if isinstance(values, dict) and not values.get("error"):
            free = values.get("free_gb")
            pct = values.get("free_pct")
            total = values.get("total_gb")
            valid = (
                _number(free)
                and (pct is None or (_number(pct) and pct <= 100))
                and (total is None or (_number(total) and total > 0 and free <= total))
            )
            if valid:
                disk.update(
                    free_gib=free,
                    free_pct=pct,
                    total_gib=total,
                    status="alert"
                    if free < DISK_MIN_FREE_GIB or (pct is not None and pct < DISK_MIN_FREE_PCT)
                    else "ok",
                )
        disks.append(disk)
    return disks


def recorded_signals(
    record: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    latest_failed: bool = False,
) -> dict[str, Any]:
    """Qualify retained evidence without refreshing its original observation time."""
    now = now or datetime.now(UTC)
    evidence = ((record or {}).get("observed") or {}).get("gwhc") or {}
    timestamps = [evidence.get("checked_at"), evidence.get("gwhc_ts"), (record or {}).get("ts")]
    ages = []
    for stamp in timestamps:
        if not isinstance(stamp, str) or not _timestamp_valid(stamp):
            break
        ages.append((now - datetime.fromisoformat(stamp)).total_seconds())
    age = max(ages) if len(ages) == 3 and min(ages) >= -60 else None
    freshness = (
        "unknown"
        if age is None
        else "stale"
        if age >= GWHC_STALE_SECONDS or latest_failed
        else "fresh"
    )
    usable = (record or {}).get("ok") is True and evidence.get("ok") is True
    worker = (
        normalize_worker(evidence)
        if usable
        else {"status": "unknown", "reason": "collection_unavailable"}
    )
    disks = normalize_disks(evidence) if usable else []
    disk_status = (
        "alert"
        if any(d["status"] == "alert" for d in disks)
        else "unknown"
        if not disks or any(d["status"] == "unknown" for d in disks)
        else "ok"
    )
    return {
        "schema_version": 1,
        "source": evidence.get("source", "sudo -n gwhc --json"),
        "observation_ts": evidence.get("gwhc_ts"),
        "checked_at": evidence.get("checked_at"),
        "collection_status": evidence.get("collection_status", "not_collected"),
        "reason": evidence.get("reason", "not_collected"),
        "freshness": freshness,
        "age_seconds": max(0, int(age)) if age is not None else None,
        "worker": worker,
        "disks": disks,
        "worker_status": worker["status"] if freshness == "fresh" else freshness,
        "disk_status": disk_status if freshness == "fresh" else freshness,
    }
