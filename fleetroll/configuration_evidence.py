"""Versioned configuration collection evidence, independent of verification policy."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from .constants import SSH_TIMEOUT_EXIT_CODE

COMPONENTS = ("puppet", "override", "vault")
FILE_STATES = frozenset({"present", "absent", "unreadable", "invalid", "not_collected"})
METADATA_FIELDS = (
    "ts",
    "success",
    "exit_code",
    "duration_s",
    "git_sha",
    "git_repo",
    "git_branch",
    "git_dirty",
    "role",
    "override_sha",
    "vault_sha",
    "override_path",
    "vault_path",
    "run_id",
)


def new_attempt() -> dict[str, Any]:
    """Allocate before SSH, including attempts that never reach a remote collector."""
    return {"id": str(uuid.uuid4()), "started_at": collection_time(), "finished_at": None}


def collection_time() -> str:
    """Keep rapid retries distinct in the hostname/timestamp primary key."""
    return datetime.now(UTC).isoformat(timespec="microseconds")


def timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
        return stamp if stamp.utcoffset() is not None else None
    except ValueError:
        return None


def _checksum(value: Any, *, revision: bool = False) -> bool:
    pattern = r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})" if revision else r"[a-fA-F0-9]{64}"
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _finite_nonnegative(value: Any) -> bool:
    try:
        return type(value) in (int, float) and value >= 0 and math.isfinite(value)
    except OverflowError:
        return False


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_field")
        result[key] = value
    return result


def _finite_json_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite_json_number")
    return number


def unavailable_component(*, status: str = "not_collected", reason: str) -> dict[str, Any]:
    return {"status": status, "reason": reason, "observed_at": None, "consistency": "unknown"}


def _metadata_error(state: dict[str, Any]) -> str | None:
    if timestamp(state.get("ts")) is None or not _finite_nonnegative(state.get("duration_s")):
        return "invalid_run_timing"
    code, success = state.get("exit_code"), state.get("success")
    if type(code) is not int or not 0 <= code <= 255 or type(success) is not bool:
        return "invalid_run_result"
    if success != (code in (0, 2)):
        return "inconsistent_run_result"
    return _optional_metadata_error(state)


def _optional_metadata_error(state: dict[str, Any]) -> str | None:
    for name in ("override_sha", "vault_sha", "git_sha"):
        if state.get(name) is not None and not _checksum(state[name], revision=name == "git_sha"):
            return f"invalid_{name}"
    for name in ("git_repo", "git_branch", "role", "override_path", "vault_path", "run_id"):
        value = state.get(name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            return f"invalid_{name}"
    if state.get("git_dirty") is not None and type(state["git_dirty"]) is not bool:
        return "invalid_git_dirty"
    return None


def normalize_metadata(component: dict[str, Any]) -> dict[str, Any]:
    """Validate one producer object; never fill missing fields from legacy lines."""
    result = {**component, "producer_schema_version": None, "fields_present": [], "values": {}}
    try:
        encoded = component.get("content_b64")
        if not isinstance(encoded, str) or len(encoded) > 87_384:
            raise ValueError
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 65_536:
            raise ValueError
        state = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_float=_finite_json_number,
            parse_constant=_finite_json_number,
        )
        if not isinstance(state, dict):
            raise TypeError
        if hashlib.sha256(raw).hexdigest() != component.get("sha256"):
            raise ValueError
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return {**result, "status": "invalid", "reason": "invalid_metadata_json"}
    result.update(
        producer_schema_version=state.get("schema_version"),
        fields_present=sorted(state),
        raw=state,
        producer_ts=state.get("ts") if timestamp(state.get("ts")) else None,
    )
    version = state.get("schema_version")
    if type(version) is not int or version != 1:
        return {**result, "status": "unsupported", "reason": "unsupported_metadata_schema"}
    error = _metadata_error(state)
    if error:
        return {**result, "status": "invalid", "reason": error}
    result["values"] = {name: state[name] for name in METADATA_FIELDS if name in state}
    result["run_provenance"] = {
        "kind": "metadata_v1",
        "invocation_id": None,
        "exclusive": False,
        "reported_run_id": state.get("run_id"),
        "reason": "v1_has_no_invocation_identity_contract",
    }
    result["applied_files"] = {}
    for name in ("override", "vault"):
        field = f"{name}_sha"
        value = state.get(field)
        result["applied_files"][name] = {
            "status": "not_collected"
            if field not in state
            else "unknown"
            if value is None
            else "present",
            "sha256": value.lower() if isinstance(value, str) else None,
            # The v1 writer also emits null when hashing fails: null is not absence.
            "reason": "field_omitted"
            if field not in state
            else "v1_null_is_ambiguous"
            if value is None
            else None,
        }
    return result


def _normalize_component(
    value: Any,
    *,
    name: str,
    started: datetime,
    finished: datetime,
    attempt_id: str,
) -> dict[str, Any]:
    invalid = unavailable_component(status="invalid", reason="invalid_component_envelope")
    if not isinstance(value, dict) or value.get("status") not in FILE_STATES:
        return invalid
    result = copy.deepcopy(value)
    result["attempt_id"] = attempt_id
    if result.get("consistency") not in {"stable", "changed", "unknown"}:
        return invalid
    if result["status"] not in {"present", "absent"}:
        result["observed_at"] = None
        return result
    error = _file_component_error(result, started=started, finished=finished)
    if error:
        return {**invalid, "reason": error}
    if result["status"] == "present":
        result["sha256"] = result["sha256"].lower()
        if name == "puppet":
            return normalize_metadata(result)
    return result


def _file_component_error(
    result: dict[str, Any], *, started: datetime, finished: datetime
) -> str | None:
    observed = timestamp(result.get("observed_at"))
    confirmed = timestamp(result.get("confirmed_at"))
    if result["consistency"] != "stable" or not observed or not confirmed:
        return "snapshot_not_stable"
    if not started <= observed <= confirmed <= finished:
        return "invalid_component_timing"
    if result["status"] == "present":
        if not _checksum(result.get("sha256")) or not _fingerprint_valid(result.get("file")):
            return "invalid_file_fingerprint"
    elif result.get("sha256") is not None or result.get("content_b64") is not None:
        return "contradictory_absence"
    return None


def _fingerprint_valid(value: Any) -> bool:
    keys = {
        "device",
        "inode",
        "size",
        "mtime_s",
        "mtime_ns",
        "ctime_s",
        "ctime_ns",
        "mode",
        "uid",
        "gid",
    }
    if not isinstance(value, dict) or not keys.issubset(value):
        return False
    return (
        all(type(value[key]) is int for key in keys)
        and all(value[key] >= 0 for key in ("device", "inode", "size", "mode", "uid", "gid"))
        and 0 <= value["mode"] <= 0o7777
        and all(0 <= value[key] < 1_000_000_000 for key in ("mtime_ns", "ctime_ns"))
    )


def collect_evidence(
    info: dict[str, str],
    *,
    ssh_rc: int,
    attempt: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Parse a completed transport, preserving failures independently of old fields."""
    attempt = dict(attempt) if attempt else {"id": str(uuid.uuid4()), "started_at": None}
    attempt.update(finished_at=collection_time(), ssh_rc=ssh_rc)
    reason = error or (
        "ssh_timeout"
        if ssh_rc == SSH_TIMEOUT_EXIT_CODE
        else "ssh_failed"
        if ssh_rc
        else info.get("CONFIG_SNAPSHOT_ERROR")
    )
    if error in {"batch_timeout_exceeded", "ssh_error"}:
        attempt["ssh_rc"] = None
    if info.get("OS_TYPE") and info["OS_TYPE"] not in ("Linux", "Darwin"):
        reason = "unsupported_os"
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "attempt": attempt,
        "snapshot": {
            "consistency": "unknown",
            "method": None,
            "started_at": None,
            "finished_at": None,
        },
        "components": {},
        "retained_components": {},
    }
    components = evidence["components"]
    try:
        encoded = info.get("CONFIG_SNAPSHOT_JSON", "")
        if reason or not encoded:
            raise ValueError(reason or "not_collected")
        if len(encoded) > 131_072:
            raise ValueError("snapshot_too_large")
        source = json.loads(
            encoded,
            object_pairs_hook=_unique_object,
            parse_float=_finite_json_number,
            parse_constant=_finite_json_number,
        )
        if (
            not isinstance(source, dict)
            or type(source.get("schema_version")) is not int
            or source["schema_version"] != 1
        ):
            raise ValueError("unsupported_snapshot_schema")
        started, finished = (
            timestamp(source.get("started_at")),
            timestamp(source.get("finished_at")),
        )
        if not started or not finished or not 0 <= (finished - started).total_seconds() <= 12:
            raise ValueError("invalid_snapshot_timing")
        if source.get("method") != "double_read_v1" or source.get("consistency") not in {
            "stable",
            "changed",
            "unknown",
        }:
            raise ValueError("invalid_snapshot_contract")
        if not isinstance(source.get("components"), dict):
            raise TypeError("invalid_snapshot_components")
        evidence["snapshot"] = {
            key: source[key] for key in ("started_at", "finished_at", "method", "consistency")
        }
        for name in COMPONENTS:
            components[name] = _normalize_component(
                source["components"].get(name),
                name=name,
                started=started,
                finished=finished,
                attempt_id=attempt["id"],
            )
            components[name]["attempt_id"] = attempt["id"]
        stable = all(usable_component(component) for component in components.values())
        if source["consistency"] == "stable" and not all(
            c.get("consistency") == "stable" for c in components.values()
        ):
            raise ValueError("inconsistent_snapshot_contract")
        any_usable = any(usable_component(component) for component in components.values())
        status = (
            "completed"
            if stable and source["consistency"] == "stable"
            else "partial"
            if any_usable
            else "failed"
        )
        reason = (
            None
            if status == "completed"
            else (
                "snapshot_changed"
                if source["consistency"] == "changed"
                else next(
                    (c.get("reason") for c in components.values() if c.get("reason")),
                    "incomplete_snapshot",
                )
            )
        )
        attempt.update(status=status, reason=reason)
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        reason = reason or str(exc)
        evidence["components"] = {
            name: {**unavailable_component(reason=reason), "attempt_id": attempt["id"]}
            for name in COMPONENTS
        }
        attempt.update(status="failed", reason=reason)
    return evidence


def usable_component(component: Any) -> bool:
    return (
        isinstance(component, dict)
        and component.get("status") in {"present", "absent"}
        and component.get("consistency") == "stable"
        and timestamp(component.get("observed_at")) is not None
    )


def retain_components(current: dict[str, Any], *, previous: dict[str, Any] | None) -> None:
    """Carry at most three earlier components, never assembling a new snapshot."""
    current["retained_components"] = {}
    if not previous or previous.get("schema_version") != 1:
        return
    for name in COMPONENTS:
        if usable_component(current["components"].get(name)):
            continue
        candidate = previous.get("components", {}).get(name)
        if not usable_component(candidate):
            candidate = previous.get("retained_components", {}).get(name)
        if usable_component(candidate):
            current["retained_components"][name] = copy.deepcopy(candidate)
