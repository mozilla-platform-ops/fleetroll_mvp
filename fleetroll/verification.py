"""Pure configuration verification; activity and operation authority stay separate."""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, TypeGuard

from .configuration_evidence import normalize_metadata, timestamp
from .constants import STALE_DATA_THRESHOLD_SECONDS

REQUIREMENTS = {"puppet": "succeeded", "revision": "match", "override": "match", "vault": "match"}
IDENTITY_KEYS = ("provisioner", "worker_type", "worker_group", "worker_id")


@dataclass(frozen=True)
class VerificationPolicy:
    source_max_age_s: int = STALE_DATA_THRESHOLD_SECONDS
    activity_window_s: int = 3600

    def __post_init__(self) -> None:
        for value in (self.source_max_age_s, self.activity_window_s):
            if type(value) is not int or value <= 0:
                raise ValueError("Verification policy windows must be positive integer seconds")


def freshness(value: Any, *, now: datetime, limit_s: int) -> dict[str, Any]:
    stamp = timestamp(value)
    age = (now - stamp).total_seconds() if stamp else None
    reason = "timestamp_invalid" if age is None else "timestamp_future" if age < 0 else None
    return {
        "state": "unknown" if age is None or age < 0 else "stale" if age >= limit_s else "fresh",
        "observation_ts": value,
        "age_seconds": age if age is not None and age >= 0 else None,
        "limit_s": limit_s,
        "reason": reason,
    }


def _full_sha(value: Any, *, revision: bool = False) -> TypeGuard[str]:
    pattern = r"[a-fA-F0-9]{40}(?:[a-fA-F0-9]{24})?" if revision else r"[a-fA-F0-9]{64}"
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def repository(value: Any) -> str | None:
    from .github import parse_github_repo_url

    if not isinstance(value, str):
        return None
    parsed = parse_github_repo_url(
        value.replace("ssh://git@ssh.github.com:443/", "https://github.com/")
    )
    return "/".join(parsed).lower() if parsed else None


def _component(
    observed_state: str,
    *,
    reasons: list[str],
    fresh: dict,
    provenance: dict,
    values: dict | None = None,
) -> dict[str, Any]:
    reasons = list(dict.fromkeys(reasons))
    return {
        "state": "unknown" if reasons else observed_state,
        "observed_state": observed_state,
        "reasons": reasons,
        "freshness": fresh,
        "observation_ts": fresh.get("observation_ts"),
        "provenance": provenance,
        "values": values or {},
    }


def _age_reasons(fresh: dict, *, source: str) -> list[str]:
    return [] if fresh["state"] == "fresh" else [f"{source}_{fresh['reason'] or fresh['state']}"]


def _sources(
    record: dict, *, now: datetime, policy: VerificationPolicy
) -> tuple[dict, dict, list[str]]:
    observed = record.get("observed") or {}
    envelope = observed.get("configuration_evidence") or {}
    supported = type(envelope.get("schema_version")) is int and envelope["schema_version"] == 1
    attempt = envelope.get("attempt") or {}
    snapshot = envelope.get("snapshot") or {}
    reasons = []
    if observed.get("os_type") not in ("Linux", "Darwin"):
        reasons.append("unsupported_platform")
    if not supported:
        reasons.append("collection_schema_unavailable")
    if record.get("ok") is not True:
        reasons.append("latest_host_collection_failed" if record else "host_not_collected")
    if (
        attempt.get("status") not in ("completed", "partial")
        or type(attempt.get("ssh_rc")) is not int
        or attempt.get("ssh_rc") != 0
    ):
        reasons.append("configuration_collection_failed")
    if not isinstance(attempt.get("id"), str) or not attempt.get("id"):
        reasons.append("attempt_id_unavailable")
    clocks = {"host": freshness(record.get("ts"), now=now, limit_s=policy.source_max_age_s)}
    reasons += _age_reasons(clocks["host"], source="host")
    for key in ("started_at", "finished_at"):
        fresh = freshness(attempt.get(key), now=now, limit_s=policy.source_max_age_s)
        reasons += _age_reasons(fresh, source=f"attempt_{key}")
    start, finish = timestamp(attempt.get("started_at")), timestamp(attempt.get("finished_at"))
    if start and finish and start > finish:
        reasons.append("attempt_time_order_invalid")
    snap_start, snap_end = (
        timestamp(snapshot.get("started_at")),
        timestamp(snapshot.get("finished_at")),
    )
    clocks["capture"] = freshness(
        snapshot.get("started_at"), now=now, limit_s=policy.source_max_age_s
    )
    reasons += _age_reasons(clocks["capture"], source="capture")
    reasons += _age_reasons(
        freshness(snapshot.get("finished_at"), now=now, limit_s=policy.source_max_age_s),
        source="capture_finished_at",
    )
    if finish != timestamp(record.get("ts")):
        reasons.append("attempt_record_link_mismatch")
    if not snap_start or not snap_end or not 0 <= (snap_end - snap_start).total_seconds() <= 12:
        reasons.append("snapshot_time_order_invalid")
    if snapshot.get("method") != "double_read_v1":
        reasons.append("snapshot_method_unsupported")
    sources = {}
    for name in ("puppet", "override", "vault"):
        current = (envelope.get("components") or {}).get(name) or {}
        retained = current.get("status") not in ("present", "absent")
        source = (
            ((envelope.get("retained_components") or {}).get(name) or current)
            if retained
            else current
        )
        own = list(reasons)
        if source is not current:
            own.append("retained_evidence")
            own.append(f"latest_{name}_{current.get('status', 'not_collected')}")
            if isinstance(current.get("reason"), str):
                own.append(current["reason"])
        if source.get("attempt_id") != attempt.get("id"):
            own.append("component_attempt_mismatch")
        if source.get("status") not in ("present", "absent"):
            own.append(source.get("reason") or f"{name}_{source.get('status', 'not_collected')}")
        if source.get("consistency") != "stable":
            own.append("component_not_stable")
        fresh = freshness(source.get("observed_at"), now=now, limit_s=policy.source_max_age_s)
        clocks[name] = fresh
        own += _age_reasons(fresh, source=name)
        checked = freshness(source.get("confirmed_at"), now=now, limit_s=policy.source_max_age_s)
        own += _age_reasons(checked, source=f"{name}_confirmation")
        stamp, confirmed = (
            timestamp(source.get("observed_at")),
            timestamp(source.get("confirmed_at")),
        )
        if (
            stamp
            and confirmed
            and snap_start
            and snap_end
            and not snap_start <= stamp <= confirmed <= snap_end
        ):
            own.append("component_time_order_invalid")
        sources[name] = {
            "evidence": source,
            "reasons": own,
            "freshness": fresh,
            "latest_status": current.get("status", "not_collected"),
            "retained": source is not current,
        }
    return sources, clocks, reasons


def _puppet(source: dict, *, now: datetime) -> tuple[dict, dict]:
    evidence = source["evidence"]
    reasons = list(source["reasons"])
    validated = normalize_metadata(evidence) if evidence.get("status") == "present" else {}
    if validated.get("status") != "present":
        reasons.append(validated.get("reason") or "puppet_metadata_unavailable")
    if validated and evidence.get("values") != validated.get("values"):
        reasons.append("metadata_values_contradict_source")
    values = validated.get("values") or {}
    completed = timestamp(values.get("ts"))
    if completed is None or completed > now:
        reasons.append("puppet_completion_time_invalid")
    observed = timestamp(evidence.get("observed_at"))
    if completed is not None and observed is not None and completed > observed:
        reasons.append("puppet_completion_after_observation")
    if evidence.get("producer_schema_version") != validated.get("producer_schema_version"):
        reasons.append("producer_version_contradiction")
    state = (
        "succeeded"
        if values.get("success") is True
        else "failed"
        if values.get("success") is False
        else "unknown"
    )
    result = _component(
        state,
        reasons=reasons,
        fresh=source["freshness"],
        provenance={
            "attempt_id": evidence.get("attempt_id"),
            "path": evidence.get("path"),
            "producer_ts": values.get("ts"),
            "producer_schema_version": evidence.get("producer_schema_version"),
            "metadata_sha256": evidence.get("sha256"),
            "fields_present": evidence.get("fields_present"),
            "run": validated.get("run_provenance"),
            "collection_status": evidence.get("status"),
            "latest_collection_status": source["latest_status"],
            "retained": source["retained"],
        },
        values=values,
    )
    return result, validated


def _file(name: str, *, source: dict, puppet: dict, validated: dict) -> dict:
    evidence = source["evidence"]
    applied = (validated.get("applied_files") or {}).get(name) or {}
    deployed_sha, applied_sha = evidence.get("sha256"), applied.get("sha256")
    reasons = list(source["reasons"]) + puppet["reasons"]
    state = "unknown"
    if (
        evidence.get("status") == "present"
        and _full_sha(deployed_sha)
        and applied.get("status") == "present"
        and _full_sha(applied_sha)
    ):
        state = "match" if deployed_sha.lower() == applied_sha.lower() else "mismatch"
    elif (
        evidence.get("status") == "absent"
        and applied.get("status") == "present"
        and _full_sha(applied_sha)
    ):
        state = "mismatch"
    else:
        reasons.append(applied.get("reason") or "file_comparison_unavailable")
    expected_path = (validated.get("values") or {}).get(f"{name}_path")
    if evidence.get("attempt_id") != puppet["provenance"].get("attempt_id"):
        reasons.append("file_run_attempt_mismatch")
        state = "unknown"
    if expected_path is not None and expected_path != evidence.get("path"):
        reasons.append("applied_path_contradiction")
    return _component(
        state,
        reasons=reasons,
        fresh=source["freshness"],
        provenance={
            "attempt_id": evidence.get("attempt_id"),
            "puppet_attempt_id": puppet["provenance"].get("attempt_id"),
            "path": evidence.get("path"),
            "applied_path": expected_path,
            "collection_status": evidence.get("status"),
            "latest_collection_status": source["latest_status"],
            "retained": source["retained"],
        },
        values={
            "deployed_status": evidence.get("status"),
            "deployed_sha256": deployed_sha,
            "applied_status": applied.get("status", "unknown"),
            "applied_sha256": applied_sha,
        },
    )


def _revision(
    puppet: dict,
    *,
    expected: dict | None,
    override: dict,
    now: datetime,
    policy: VerificationPolicy,
) -> dict:
    expected = expected or {}
    fresh = freshness(expected.get("ts"), now=now, limit_s=policy.source_max_age_s)
    reasons = (
        list(puppet["reasons"]) + override["reasons"] + _age_reasons(fresh, source="reference")
    )
    applied = puppet["values"]
    applied_sha, expected_sha = applied.get("git_sha"), expected.get("sha")
    state = "unknown"
    if _full_sha(applied_sha, revision=True) and _full_sha(expected_sha, revision=True):
        state = "match" if applied_sha.lower() == expected_sha.lower() else "mismatch"
    else:
        reasons.append("revision_unavailable")
    applied_repo = repository(applied.get("git_repo"))
    if not applied_repo or applied_repo != expected.get("repository"):
        reasons.append("repository_unresolved_or_contradictory")
    if applied.get("git_dirty") is not False:
        reasons.append(
            "applied_worktree_dirty"
            if applied.get("git_dirty") is True
            else "git_cleanliness_unavailable"
        )
    if not isinstance(expected.get("branch"), str) or not expected.get("branch"):
        reasons.append("expected_branch_unresolved")
    # A detached producer HEAD is normal; expected branch intent comes from the
    # exact deployed artifact/default contract, not the producer display branch.
    if expected.get("source") == "override_artifact":
        expected_override = expected.get("override_sha256")
        observed_override = override["evidence"].get("sha256")
        if (
            not _full_sha(expected_override)
            or not _full_sha(observed_override)
            or expected_override.lower() != observed_override.lower()
        ):
            reasons.append("reference_override_mismatch")
    elif (
        expected.get("source") != "default_master" or override["evidence"].get("status") != "absent"
    ):
        reasons.append("reference_source_unresolved")
    return _component(
        state,
        reasons=reasons,
        fresh=fresh,
        provenance={
            "expected": expected,
            "applied_repository": applied.get("git_repo"),
            "applied_branch": applied.get("git_branch"),
            "producer_ts": applied.get("ts"),
        },
        values={"applied_sha": applied_sha, "expected_sha": expected_sha},
    )


def _identity(value: dict) -> dict | None:
    if not isinstance(value, dict):
        return None
    return (
        {key: value[key] for key in IDENTITY_KEYS}
        if all(isinstance(value.get(key), str) and value[key] for key in IDENTITY_KEYS)
        else None
    )


def _taskcluster(
    record: dict, *, tc: dict, lookup: dict, now: datetime, policy: VerificationPolicy
) -> tuple[dict, dict, dict]:
    observed = record.get("observed") or {}
    raw_identity = observed.get("tc_worker_identity") or {}
    if not isinstance(raw_identity, dict):
        raw_identity = {}
    host_identity = _identity(
        dict(
            zip(
                IDENTITY_KEYS,
                (
                    raw_identity.get(k)
                    for k in ("provisionerId", "workerType", "workerGroup", "workerId")
                ),
            )
        )
    )
    host_fresh = freshness(record.get("ts"), now=now, limit_s=policy.source_max_age_s)
    lookup_fresh = freshness(lookup.get("ts"), now=now, limit_s=policy.source_max_age_s)
    tc_fresh = freshness(tc.get("ts"), now=now, limit_s=policy.source_max_age_s)
    reasons = _age_reasons(lookup_fresh, source="tc_lookup") + _age_reasons(
        host_fresh, source="host"
    )
    if (
        record.get("ok") is not True
        or observed.get("tc_worker_identity_status") != "ok"
        or host_identity is None
    ):
        reasons.append("host_identity_unavailable")
    if lookup.get("resolution_source") != "host_configuration":
        reasons.append("lookup_identity_source_unqualified")
    lookup_identity, tc_identity = _identity(lookup.get("identity") or {}), _identity(tc)
    if host_identity is None or lookup_identity != host_identity:
        reasons.append("lookup_identity_mismatch_or_unknown")
    started, finished = timestamp(lookup.get("started_ts")), timestamp(lookup.get("ts"))
    if not started or not finished or not started <= finished <= now:
        reasons.append("lookup_time_order_invalid")
    status = lookup.get("status")
    if not isinstance(status, str):
        status = None
    lookup_state = (
        "ok"
        if status == "success"
        else "missing_worker"
        if status == "worker_missing"
        else "failed"
        if status
        in {"pool_query_failed", "worker_ambiguous", "task_lookup_failed", "identity_unresolved"}
        else "unknown"
    )
    if lookup_state == "unknown":
        reasons.append("lookup_unavailable")
    provenance = {
        "host_identity": host_identity,
        "lookup_identity": lookup_identity,
        "tc_identity": tc_identity,
        "lookup_status": status,
        "lookup_started_ts": lookup.get("started_ts"),
        "lookup_ts": lookup.get("ts"),
        "successful_observation_ts": lookup.get("successful_observation_ts"),
        "tc_observation_ts": tc.get("ts"),
        "resolution_source": lookup.get("resolution_source"),
    }
    lookup_result = _component(
        lookup_state, reasons=reasons, fresh=lookup_fresh, provenance=provenance
    )
    activity_reasons = list(reasons) + _age_reasons(tc_fresh, source="tc_observation")
    if status != "success":
        activity_reasons.append("latest_lookup_not_successful")
    if not lookup.get("successful_observation_ts") or lookup.get(
        "successful_observation_ts"
    ) != tc.get("ts"):
        activity_reasons.append("tc_observation_unlinked")
    if tc_identity is None or tc_identity != host_identity or tc_identity != lookup_identity:
        activity_reasons.append("tc_identity_mismatch_or_unknown")
    if tc.get("resolution_source") == "historical_worker_identity":
        activity_reasons.append("historical_tc_observation")
    tc_time = timestamp(tc.get("ts"))
    active_time = timestamp(tc.get("last_date_active"))
    active_fresh = freshness(tc.get("last_date_active"), now=now, limit_s=policy.activity_window_s)
    if (
        active_time is None
        or tc_time is None
        or finished is None
        or not active_time <= tc_time <= finished <= now
    ):
        activity_reasons.append("activity_time_order_invalid")
    raw_activity = (
        "recent"
        if active_fresh["state"] == "fresh"
        else "not_recent"
        if active_fresh["state"] == "stale"
        else "unknown"
    )
    if raw_activity == "unknown":
        activity_reasons.append("activity_timestamp_unavailable")
    activity = _component(
        raw_activity,
        reasons=activity_reasons,
        fresh=tc_fresh,
        provenance=provenance,
        values={"last_date_active": tc.get("last_date_active"), "activity_age": active_fresh},
    )
    return (
        lookup_result,
        activity,
        {"tc_lookup": lookup_fresh, "tc_observation": tc_fresh, "tc_activity": active_fresh},
    )


def evaluate_verification(
    record: dict[str, Any] | None,
    *,
    now: datetime,
    expected_reference: dict | None = None,
    tc_record: dict | None = None,
    lookup_attempt: dict | None = None,
    policy: VerificationPolicy | None = None,
) -> dict[str, Any]:
    """Evaluate every component at one explicit UTC instant without IO or mutation."""
    if now.utcoffset() is None:
        raise ValueError("Verification requires a timezone-aware evaluation time")
    now = now.astimezone(UTC)
    policy = policy or VerificationPolicy()
    record = record or {}
    sources, clocks, global_reasons = _sources(record, now=now, policy=policy)
    puppet, validated = _puppet(sources["puppet"], now=now)
    components = {
        "puppet": puppet,
        "revision": _revision(
            puppet,
            expected=expected_reference,
            override=sources["override"],
            now=now,
            policy=policy,
        ),
    }
    for name in ("override", "vault"):
        components[name] = _file(name, source=sources[name], puppet=puppet, validated=validated)
    components["tc_lookup"], components["activity"], tc_clocks = _taskcluster(
        record, tc=tc_record or {}, lookup=lookup_attempt or {}, now=now, policy=policy
    )
    clocks.update(reference=components["revision"]["freshness"], **tc_clocks)
    components["workload"] = _component(
        "unknown",
        reasons=["pool_classifier_not_integrated"],
        fresh=freshness(None, now=now, limit_s=policy.source_max_age_s),
        provenance={"source": "pool_classifier"},
    )
    clocks["workload"] = components["workload"]["freshness"]
    failing = [name for name in REQUIREMENTS if components[name]["state"] in ("failed", "mismatch")]
    unknown = [name for name in REQUIREMENTS if components[name]["state"] == "unknown"]
    envelope = (record.get("observed") or {}).get("configuration_evidence") or {}
    coherent = (envelope.get("snapshot") or {}).get("consistency") == "stable" and (
        envelope.get("attempt") or {}
    ).get("status") == "completed"
    if not coherent:
        global_reasons.append("current_snapshot_incomplete")
        unknown.append("snapshot")
    state = "not_verified" if failing else "unknown" if unknown or global_reasons else "verified"
    return {
        "schema_version": 1,
        "evaluated_at": now.isoformat(),
        "policy": asdict(policy),
        "configuration": {
            "state": state,
            "failing_requirements": failing,
            "unknown_requirements": unknown,
            "reasons": list(dict.fromkeys(global_reasons)),
            "supported_platform": (record.get("observed") or {}).get("os_type")
            in ("Linux", "Darwin"),
        },
        "components": components,
        "freshness": clocks,
        "collection_attempt": copy.deepcopy(envelope.get("attempt")),
        "snapshot": copy.deepcopy(envelope.get("snapshot")),
        "operation_authority": "not_granted",
    }
