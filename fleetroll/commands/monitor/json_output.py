"""Additive worker evidence for monitor JSON; no live queries or health verdicts."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ...constants import ROLE_TO_TASKCLUSTER, STALE_DATA_THRESHOLD_SECONDS
from .data import strip_fqdn

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ...data_provider import DataProvider
    from .cache import ShaInfoCache


def _freshness(ts: str | None, *, now: datetime) -> str:
    if not ts:
        return "unknown"
    try:
        stamp = datetime.fromisoformat(ts)
        age = (now - stamp).total_seconds()
    except (ValueError, TypeError):
        return "unknown"
    if age < 0:
        return "unknown"
    return "stale" if age >= STALE_DATA_THRESHOLD_SECONDS else "fresh"


def _match(expected: dict | None, actual: dict) -> str:
    if not expected or not all(expected.values()) or not all(actual.values()):
        return "unknown"
    return "match" if expected == actual else "mismatch"


def _unresolved_override(sha: str | None, *, sha_cache: ShaInfoCache) -> bool:
    """Distinguish absent/empty pool assignments from unsupported shell expressions."""
    if not sha:
        return True
    details = sha_cache.get_override_details(sha)
    if not details:
        return True
    if details.get("worker_type_override"):
        return False
    try:
        content = (sha_cache.overrides_dir / sha[:12]).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return True
    assignments = re.findall(
        r"^[ \t]*(?:export[ \t]+)?WORKER_TYPE_OVERRIDE=(.*)$", content, re.MULTILINE
    )
    # Empty values deliberately use the default pool. Nonliteral values are unknown.
    return any(not re.fullmatch(r"(?:''|\"\")?[ \t]*(?:#.*)?", value) for value in assignments)


def build_configured_worker_type(
    host: str,
    *,
    record: dict[str, Any] | None,
    sha_cache: ShaInfoCache,
    now: datetime,
) -> dict[str, Any]:
    """Resolve recorded intent and override convergence without identity fallback."""
    # gather_tc imports monitor.cache; defer to avoid a package initialization cycle.
    from ..gather_tc import map_roles_to_worker_types, resolve_host_worker_types

    observed = (record or {}).get("observed") or {}
    audit_status = "missing" if record is None else "ok" if record.get("ok") else "failed"
    ts = (record or {}).get("ts")
    role = observed.get("role")
    override_sha = observed.get("override_sha256")
    details = sha_cache.get_override_details(override_sha) if override_sha else None
    override_pool = details.get("worker_type_override") if details else None
    unresolved_override = observed.get("override_present") and _unresolved_override(
        override_sha, sha_cache=sha_cache
    )
    role_mapping, _, _ = map_roles_to_worker_types(
        {role: [host]} if isinstance(role, str) else {}, ROLE_TO_TASKCLUSTER
    )
    resolved = resolve_host_worker_types(
        [host],
        host_to_role={host: role},
        role_to_worker_type=role_mapping,
        host_to_override_pool={host: override_pool},
    ).get(host)
    if audit_status != "ok" or unresolved_override:
        resolved = None
    configured_pool = {"provisioner": resolved[0], "worker_type": resolved[1]} if resolved else None
    applied_sha = observed.get("puppet_override_sha_applied")
    application = "unknown"
    if (
        audit_status == "ok"
        and "puppet_override_sha_applied" in observed
        and (not observed.get("override_present") or override_sha)
    ):
        matches = (applied_sha or "") == (override_sha or "")
        application = "applied" if matches else "pending"
        if observed.get("puppet_success") is False:
            application = "failed"
        elif observed.get("puppet_success") is not True and matches:
            application = "unknown"
    return {
        **(configured_pool or {"provisioner": None, "worker_type": None}),
        "status": "resolved" if resolved else "unresolved",
        "reason": (
            "host_audit_" + audit_status
            if audit_status != "ok"
            else "override_metadata_unavailable"
            if unresolved_override
            else "unmapped_role"
            if not resolved
            else None
        ),
        "source": "override" if override_pool else "role_default" if resolved else "unknown",
        "observation_ts": ts,
        "host_audit_status": audit_status,
        "freshness": _freshness(ts, now=now),
        "override_sha256": override_sha,
        "application_status": application,
    }


def build_monitor_json_record(
    host: str,
    *,
    record: dict[str, Any] | None,
    tc_record: dict[str, Any] | None,
    sha_cache: ShaInfoCache,
    lookup_attempt: dict[str, Any] | None = None,
    historical_state: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Preserve raw audit fields and qualify each independent evidence source."""
    from ...worker_history import render_history

    now = now or datetime.now(UTC)
    result = dict(record or {"host": host})
    observed = (record or {}).get("observed") or {}
    audit_status = "missing" if record is None else "ok" if record.get("ok") else "failed"
    ts = (record or {}).get("ts")
    configured = build_configured_worker_type(host, record=record, sha_cache=sha_cache, now=now)
    configured_pool = (
        {key: configured[key] for key in ("provisioner", "worker_type")}
        if configured["status"] == "resolved"
        else None
    )
    identity = observed.get("tc_worker_identity") or {}
    host_identity = {
        "provisioner": identity.get("provisionerId"),
        "worker_type": identity.get("workerType"),
        "worker_group": identity.get("workerGroup"),
        "worker_id": identity.get("workerId"),
    }
    collection_status = observed.get("tc_worker_identity_status", "not_collected")
    valid_identity = (
        audit_status == "ok" and collection_status == "ok" and all(host_identity.values())
    )
    observed_identity = {
        **host_identity,
        "collection_status": collection_status,
        "status": "resolved" if valid_identity else "unresolved",
        "host_audit_status": audit_status,
        "observation_ts": ts,
        "freshness": _freshness(ts, now=now),
        "configured_pool_match": _match(
            configured_pool,
            {key: host_identity[key] for key in ("provisioner", "worker_type")},
        )
        if valid_identity
        else "unknown",
    }
    tc = tc_record or {}
    tc_identity = {
        key: tc.get(key) for key in ("provisioner", "worker_type", "worker_group", "worker_id")
    }
    attempt = lookup_attempt or {}
    attempt_identity = attempt.get("identity") or {}
    latest_lookup = {
        **attempt,
        "status": attempt.get("status", "unknown"),
        "ts": attempt.get("ts"),
        "identity": attempt.get("identity"),
        "freshness": _freshness(attempt.get("ts"), now=now),
        "configured_pool_match": _match(
            configured_pool,
            {key: attempt_identity.get(key) for key in ("provisioner", "worker_type")},
        ),
        "observed_identity_match": _match(
            host_identity if valid_identity else None, attempt_identity
        ),
    }
    taskcluster = {
        "identity": tc_identity,
        "observation_ts": tc.get("ts"),
        "last_date_active": tc.get("last_date_active"),
        "record_status": "stored_success" if tc_record else "missing",
        "latest_lookup_status": latest_lookup["status"],
        "latest_lookup": latest_lookup,
        "freshness": _freshness(tc.get("ts"), now=now),
        "configured_pool_match": _match(
            configured_pool, {key: tc_identity[key] for key in ("provisioner", "worker_type")}
        ),
        "observed_identity_match": _match(host_identity if valid_identity else None, tc_identity),
        "record": tc_record,
    }
    historical = render_history(historical_state, sha_cache=sha_cache, now=now)
    baseline_identity = (
        {key: historical[key] for key in tc_identity} if historical["status"] == "present" else None
    )
    taskcluster["historical_identity_match"] = _match(baseline_identity, tc_identity)
    latest_lookup["historical_identity_match"] = _match(baseline_identity, attempt_identity)
    for evidence, source in ((taskcluster, tc), (latest_lookup, attempt)):
        provenance = source.get("historical_baseline")
        evidence["historical_baseline"] = provenance
        evidence["historical_baseline_match"] = (
            "unknown"
            if not provenance or historical["status"] != "present"
            else "match"
            if provenance == historical["provenance"]
            else "mismatch"
        )
    result.update(
        worker_evidence_schema_version=3,
        configured_worker_type=configured,
        observed_worker_identity=observed_identity,
        historical_worker_identity=historical,
        taskcluster=taskcluster,
    )
    return result


def build_monitor_json_snapshot(
    hosts: list[str],
    *,
    latest: dict,
    tc_data: dict,
    sha_cache: ShaInfoCache,
    lookup_attempts: dict | None = None,
    historical_states: dict | None = None,
) -> dict[str, dict[str, Any]]:
    now = datetime.now(UTC)
    return {
        host: build_monitor_json_record(
            host,
            record=latest.get(host),
            tc_record=tc_data.get(strip_fqdn(host)),
            sha_cache=sha_cache,
            lookup_attempt=(lookup_attempts or {}).get(host),
            historical_state=(historical_states or {}).get(host),
            now=now,
        )
        for host in hosts
    }


def follow_monitor_json(
    hosts: list[str],
    *,
    provider: DataProvider,
    initial: dict,
    sha_cache: ShaInfoCache,
) -> Iterator[dict[str, Any]]:
    """Emit complete host objects on host/TC changes and freshness transitions."""
    previous = initial
    while True:
        latest, _ = provider.load_latest_records(hosts=hosts)
        tc_data = provider.load_tc_workers(hosts=hosts)
        lookup_attempts = provider.load_tc_lookup_attempts(hosts=hosts)
        historical_states = provider.load_worker_history(hosts=hosts)
        # Re-read metadata so newly collected override content is visible in follow mode.
        sha_cache.override_cache.clear()
        current = build_monitor_json_snapshot(
            hosts,
            latest=latest,
            tc_data=tc_data,
            sha_cache=sha_cache,
            lookup_attempts=lookup_attempts,
            historical_states=historical_states,
        )
        for host, record in current.items():
            if record != previous.get(host):
                yield record
        previous = current
        time.sleep(0.5)
