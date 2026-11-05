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


def _validate_legacy_health(health: dict[str, Any], *, exit_code: int) -> str | None:
    """Recognize the existing unversioned report for diagnostics only."""
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
        "schema_version": 1,
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
    if "schema_version" in health:
        result.update(contract_status="unsupported", reason="unsupported_gwhc_schema")
        result["zombie"]["reason"] = "unsupported_gwhc_schema"
        return result
    result["contract_status"] = "legacy"
    error = _validate_legacy_health(health, exit_code=exit_code)
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
        zombie={"status": "unknown", "reason": "unversioned_gwhc_schema"},
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
        print(f"Zombie: unknown ({result['zombie']['reason']})")
        print("Snapshot only; diagnostic evidence does not authorize recovery.")
    if not result["ok"]:
        raise CommandFailureError
