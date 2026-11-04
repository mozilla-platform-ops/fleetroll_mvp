"""Check override worker types against recorded host roles without running shell code."""

from __future__ import annotations

import re
from typing import Any

from ..constants import ROLE_TO_TASKCLUSTER
from ..db import get_connection, get_db_path, get_latest_host_observations
from ..exceptions import UserError


def check_worker_type(data: bytes, *, hosts: list[str]) -> list[dict[str, Any]]:
    """Return per-host checks; an empty list means no worker type was specified.

    Only standalone literal assignments can be checked. Other active references
    require the explicit bypass, rather than being silently treated as absent.
    """
    requested = None
    parse_error = None
    assignment = re.compile(
        r"(?:export\s+)?WORKER_TYPE_OVERRIDE=(?:'([a-zA-Z0-9-]+)'|"
        r'"([a-zA-Z0-9-]+)"|([a-zA-Z0-9-]+))(?:\s+#.*)?'
    )
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("#") or not re.search(r"\bWORKER_TYPE_OVERRIDE\b", line):
            continue
        match = assignment.fullmatch(line)
        if match is None:
            parse_error = "worker type cannot be determined from a literal assignment"
        else:
            requested = next(value for value in match.groups() if value is not None)
    if requested is None and parse_error is None:
        return []

    observations: dict[str, dict[str, Any]] = {}
    db_error = None
    try:
        conn = get_connection(get_db_path())
        try:
            _, observations = get_latest_host_observations(conn, hosts)
        finally:
            conn.close()
    except Exception as exc:
        db_error = f"could not read recorded host roles: {exc}"

    checks = []
    for host in hosts:
        record = observations.get(host, {})
        role = record.get("observed", {}).get("role")
        mapping = ROLE_TO_TASKCLUSTER.get(role)
        expected = mapping[1] if mapping else None
        if expected == "AUTO_under_to_dash":
            expected = role.replace("_", "-")
        reason = parse_error or db_error
        if reason is None:
            if expected is None:
                reason = "unknown role or worker type mapping"
            elif requested != expected and not (
                requested
                and requested.startswith(expected + "-")
                and len(requested) > len(expected) + 1
            ):
                reason = "worker type does not match the host's base worker type"
        checks.append(
            {
                "host": host,
                "role": role,
                "observed_at": record.get("ts"),
                "expected": expected,
                "requested": requested,
                "compatible": reason is None,
                "reason": reason,
            }
        )
    return checks


def enforce_worker_type(checks: list[dict[str, Any]], *, bypass: bool) -> None:
    """Block the whole batch before any remote writes if a check fails."""
    failed = [check for check in checks if not check["compatible"]]
    if failed and not bypass:
        details = "\n".join(
            f"  {check['host']}: {check['reason']}; expected={check['expected']}, "
            f"requested={check['requested']}, observed_at={check['observed_at']}"
            for check in failed
        )
        raise UserError(
            f"Worker type compatibility check failed:\n{details}\n"
            "Refresh observations with gather host if needed, or use "
            "--allow-worker-type-mismatch for an intentional exception."
        )
