"""Interpret bounded Puppet metadata separately from wrapper/SSH success."""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import math
import re
from typing import Any

_PREFIX = "FLEETROLL_PUPPET_"
_FIELDS = frozenset({"BEFORE", "AFTER", "START", "END", "WRAPPER_EXIT"})
_MARKER_RE = re.compile(r"(?m)^FLEETROLL_PUPPET_[A-Z_]+=[^\r\n]*\r?$|^EXIT=\d+\r?$")


def strip_run_markers(stdout: str) -> str:
    """Hide machine protocol lines from the human-readable wrapper output."""
    return _MARKER_RE.sub("", stdout).strip()


def unknown_puppet_observation(*, reason: str) -> dict[str, Any]:
    """Missing evidence never means Puppet made no changes."""
    return {
        "wrapper_exit": None,
        "puppet_exit": None,
        "puppet_outcome": "unknown",
        "changes_applied": None,
        "outcome_reason": reason,
        "puppet_state_ts": None,
        "outcome_source": None,
        "run_started_epoch": None,
        "run_finished_epoch": None,
    }


def _parse_run_fields(stdout: str) -> tuple[dict[str, str], int, int, int]:
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        if not line.startswith(_PREFIX):
            continue
        key, sep, value = line[len(_PREFIX) :].partition("=")
        if not sep or key not in _FIELDS or key in fields:
            raise ValueError("invalid_run_evidence")
        fields[key] = value
    if fields.keys() != _FIELDS:
        raise ValueError("missing_run_evidence")
    try:
        wrapper_exit = int(fields["WRAPPER_EXIT"])
        started = int(fields["START"])
        ended = int(fields["END"])
    except ValueError:
        raise ValueError("invalid_run_evidence") from None
    if not 0 <= wrapper_exit <= 255 or started < 0 or ended < started:
        raise ValueError("invalid_run_evidence")
    return fields, wrapper_exit, started, ended


def parse_run_observation(stdout: str) -> dict[str, Any]:
    """Validate metadata collected around one remote wrapper invocation.

    Schema v1 has no invocation identifier. Require changed metadata and a run
    contained in the remote time window; retain that limited provenance instead
    of treating the wrapper's normalized exit code as Puppet's detailed code.
    """
    observed = unknown_puppet_observation(reason="missing_run_evidence")
    try:
        fields, wrapper_exit, started, ended = _parse_run_fields(stdout)
    except ValueError as exc:
        observed["outcome_reason"] = str(exc)
        return observed
    observed.update(wrapper_exit=wrapper_exit, run_started_epoch=started, run_finished_epoch=ended)
    before, after = fields["BEFORE"], fields["AFTER"]
    if before == "unavailable" or after in {"unavailable", "missing", ""}:
        observed["outcome_reason"] = "metadata_unavailable"
        return observed
    if before == after:
        observed["outcome_reason"] = "metadata_unchanged"
        return observed
    try:
        state = json.loads(base64.b64decode(after, validate=True))
        if not isinstance(state, dict) or type(state.get("schema_version")) is not int:
            raise ValueError
        if state["schema_version"] != 1:
            raise ValueError
        code, success, duration = (
            state.get("exit_code"),
            state.get("success"),
            state.get("duration_s"),
        )
        if type(code) is not int or not 0 <= code <= 255 or type(success) is not bool:
            raise ValueError
        if success != (code in {0, 2}):
            raise ValueError
        if type(duration) not in {int, float} or not math.isfinite(duration) or duration < 0:
            raise ValueError
        timestamp = state.get("ts")
        if not isinstance(timestamp, str):
            raise TypeError
        completed = dt.datetime.fromisoformat(timestamp)
        if completed.tzinfo is None:
            raise ValueError
        completed_s = completed.timestamp()
    except (ValueError, TypeError, OverflowError, binascii.Error, UnicodeDecodeError):
        observed["outcome_reason"] = "invalid_metadata"
        return observed
    # The producer timestamps completion and measures duration in whole seconds.
    # Allow one second for that rounding, but never accept a prior completion.
    if not started <= completed_s < ended + 1 or completed_s - duration < started - 1:
        observed["outcome_reason"] = "metadata_outside_run_window"
        return observed
    observed.update(
        puppet_exit=code,
        puppet_outcome={0: "unchanged", 2: "changed"}.get(code, "failed"),
        changes_applied=bool(code & 2) if code in {0, 2, 4, 6} else None,
        outcome_reason=None,
        puppet_state_ts=timestamp,
        outcome_source="run_window_metadata_v1",
    )
    return observed
