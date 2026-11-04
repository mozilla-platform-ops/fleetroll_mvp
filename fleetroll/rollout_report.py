"""Compare recent Taskcluster outcomes on master and each override within a role."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import math
import os
import re
import sqlite3
import sys
import textwrap
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import click

from .audit import iter_audit_records
from .commands.fleet_status import build_report as build_fleet_status
from .configuration_history import read_evidence, state_payload
from .constants import DEFAULT_TC_PROVISIONER, OVERRIDES_DIR_NAME
from .db import get_db_path
from .queue_counts import collect_queue_counts, render_queue_counts
from .rollout_history import (
    WRITE_ACTIONS,
    Configuration,
    Interval,
    WorkerIdentity,
    build_history,
    classify_run,
    parse_timestamp,
    verified_configuration,
    worker_identity,
)
from .taskcluster import create_tc_clients, load_tc_credentials
from .utils import default_audit_log_path, is_host_file, parse_host_list, parse_worker_type

OUTCOMES = ("completed", "failed", "exception", "running", "pending", "unknown")
RECENT_TASK_LIMIT = 20
SCHEMA_VERSION = 1


def iso(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def cohort_id(config: Configuration) -> str:
    return json.dumps(config.cohort, separators=(",", ":"))


def configuration_json(config: Configuration) -> dict[str, Any]:
    return {
        "id": cohort_id(config),
        "role": config.role,
        "repository": config.repository,
        "branch": config.branch,
        "override_sha256": config.override_sha,
        "git_sha": config.git_sha,
        "vault_sha256": config.vault_sha,
        "scope": list(config.scope),
        "kind": "override" if config.override_sha else "master",
    }


def load_evidence(
    db_path: Path,
    *,
    audit_log: Path,
    until: dt.datetime,
    observation_files: list[Path] | None = None,
    history_metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], set[tuple[str, str]], list[str]]:
    """Read retained observations and audit events without initializing/writing the DB."""
    records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    warnings: list[str] = []
    # URI mode=ro also prevents accidentally creating a database for a typo.
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        for (data,) in conn.execute("SELECT data FROM host_observations"):
            record = json.loads(data)
            stamp = parse_timestamp(record.get("ts"))
            if stamp and stamp <= until:
                records[record["host"]].append(record)
        refs = {
            (f"{owner}/{repo}", sha)
            for owner, repo, sha, stamp in conn.execute(
                "SELECT owner, repo, sha, ts FROM github_refs WHERE branch='master'"
            )
            if (parsed := parse_timestamp(stamp)) and parsed <= until
        }
        compact, metadata = read_evidence(conn, until=until)
        if history_metadata is not None:
            history_metadata.update(metadata)
        for record in compact:
            records[record["host"]].append(record)
        if metadata["available"]:
            warnings.append(
                f"Compact configuration history: {metadata['spans']} spans; "
                f"retention cutoff {metadata['cutoff']}. Unsupported continuity remains unknown."
            )
    finally:
        conn.close()
    for path in observation_files or []:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(snapshot, dict):
            if isinstance(snapshot.get("host"), str):
                snapshot = [snapshot]
            elif "results" in snapshot:
                snapshot = snapshot["results"]
            else:
                snapshot = list(snapshot.values())
        if not isinstance(snapshot, list):
            raise TypeError(f"{path}: expected gather-host or host-monitor observation JSON")
        for entry in snapshot:
            stamp = parse_timestamp(entry.get("ts")) if isinstance(entry, dict) else None
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("host"), str)
                or not isinstance(entry.get("observed"), dict)
                or stamp is None
            ):
                raise ValueError(f"{path}: invalid host observation record")
            if stamp <= until:
                records[entry["host"]].append(
                    {key: value for key, value in entry.items() if not key.startswith("_")}
                )
        warnings.append(f"Additional historical observations loaded from {path}.")
    aliases: dict[str, list[str]] = defaultdict(list)
    for host in records:
        aliases[host.split(".")[0]].append(host)
    if not audit_log.exists():
        warnings.append("Audit log unavailable; configuration history may have additional gaps.")
    for record in iter_audit_records(audit_log):
        if not isinstance(record, dict):
            continue
        if record.get("action") not in {"host.audit", *WRITE_ACTIONS}:
            continue
        host, stamp = record.get("host"), parse_timestamp(record.get("ts"))
        if not isinstance(host, str) or stamp is None or stamp > until:
            continue
        targets = [host] if host in records else aliases.get(host, [])
        for target in targets:
            records[target].append({**record, "host": target})
    # Audit and DB copies of the same observation are common.
    for host, entries in records.items():
        entries = merge_compact_observations(
            entries, overrides_dir=audit_log.parent / OVERRIDES_DIR_NAME, master_refs=refs
        )
        unique = {json.dumps(entry, sort_keys=True): entry for entry in entries}
        records[host] = sorted(unique.values(), key=lambda entry: parse_timestamp(entry["ts"]))
    return dict(records), refs, warnings


def merge_compact_observations(entries, *, overrides_dir, master_refs):
    """Deduplicate matching raw/compact endpoints without hiding contradictions."""
    from .rollout_history import applied_time

    groups = defaultdict(list)
    events = []
    pinned = {
        json.dumps(state_payload(entry), sort_keys=True): entry["_configuration_proof"]
        for entry in entries
        if entry.get("_configuration_proof") and not entry.get("_compact_invalidated")
    }
    for entry in entries:
        if entry.get("action", "host.audit") != "host.audit":
            events.append(entry)
            continue
        state_key = json.dumps(state_payload(entry), sort_keys=True)
        if "_compact_unverified" not in entry and state_key in pinned:
            # This adds descriptor proof, never observation continuity. Recent
            # raw anchors remain useful when override caches/master refs expire.
            entry = {**entry, "_configuration_proof": pinned[state_key]}
        key = (
            parse_timestamp(entry["ts"]),
            state_key,
            applied_time(entry.get("observed") or {}),
        )
        groups[key].append(entry)
    for copies in groups.values():
        compact = [item for item in copies if "_compact_unverified" in item]
        raw = [item for item in copies if "_compact_unverified" not in item]
        if not compact:
            events.extend(raw)
            continue
        retained = compact[-1]
        if raw:
            if retained.get("_compact_invalidated"):
                events.extend([*raw, retained])
                continue
            if (
                not retained.get("_configuration_proof")
                and verified_configuration(
                    raw[-1], overrides_dir=overrides_dir, master_refs=master_refs
                )[0]
            ):
                events.extend(raw)
                continue
            retained = {
                **raw[-1],
                **{key: value for key, value in retained.items() if key.startswith("_")},
            }
        events.append(retained)
    return events


def history_gaps(intervals, *, since, until):
    gaps = []
    cursor = since
    for interval in intervals:
        if interval.end < since or interval.start > until:
            continue
        if interval.start > cursor:
            gaps.append({"start": iso(cursor), "end": iso(min(interval.start, until))})
        cursor = max(cursor, interval.end)
    if cursor < until:
        gaps.append({"start": iso(cursor), "end": iso(until)})
    return gaps


def latest_observation(records: list[dict[str, Any]]) -> dict[str, Any]:
    audits = [record for record in records if record.get("action", "host.audit") == "host.audit"]
    return audits[-1] if audits else {}


def last_known_role(records: list[dict[str, Any]]) -> str | None:
    return next(
        (
            role
            for record in reversed(records)
            if (role := (record.get("observed") or {}).get("role"))
        ),
        None,
    )


def select_hosts(
    records: dict[str, list[dict[str, Any]]],
    *,
    role: str | None,
    worker_type: str | None,
    provisioner: str,
    requested: list[str],
    overrides_dir: Path,
    worker_group: str,
) -> tuple[list[str], list[str]]:
    selected: set[str] = set()
    errors: list[str] = []
    if requested:
        for name in requested:
            matches = [
                host
                for host in records
                if host == name or ("." not in name and host.split(".")[0] == name)
            ]
            if len(matches) == 1:
                selected.add(matches[0])
            else:
                errors.append(f"{name}: host is unknown or short name is ambiguous")
    else:
        for host, entries in records.items():
            if role:
                if last_known_role(entries) == role:
                    selected.add(host)
                continue
            # Failed latest observations still belong in the requested population;
            # old identity is used only for selection, never for querying/attribution.
            for entry in reversed(entries):
                identity, _ = worker_identity(
                    entry, overrides_dir=overrides_dir, worker_group=worker_group
                )
                if identity:
                    if (identity.provisioner, identity.pool) == (provisioner, worker_type):
                        selected.add(host)
                    break
    return sorted(selected), errors


def fetch_task_run(
    clients: Any, ref: dict[str, Any], *, identity: WorkerIdentity
) -> dict[str, Any]:
    task_id = ref.get("taskId") if isinstance(ref, dict) else None
    run_id = ref.get("runId") if isinstance(ref, dict) else None
    job = {
        "task_id": task_id,
        "run_id": run_id,
        "outcome": "unknown",
        "started": None,
        "resolved": None,
        "name": None,
        "query_error": None,
    }
    try:
        if not isinstance(task_id, str) or type(run_id) is not int:
            raise ValueError("invalid recent-task reference")
        status = clients.queue.status(task_id).get("status") or {}
        run = next((item for item in status.get("runs", []) if item.get("runId") == run_id), None)
        if run is None:
            raise ValueError("task run missing from Queue status")
        if (
            status.get("provisionerId") != identity.provisioner
            or status.get("workerType") != identity.pool
            or run.get("workerGroup") != identity.worker_group
            or run.get("workerId") != identity.worker_id
        ):
            raise ValueError("Queue task run does not match the queried worker identity")
        outcome = run.get("state", "unknown")
        job.update(
            started=run.get("started"),
            resolved=run.get("resolved"),
            outcome=outcome if outcome in OUTCOMES else "unknown",
        )
        if job["outcome"] in {"failed", "exception", "unknown"}:
            task = clients.queue.task(task_id)
            job["name"] = (task.get("metadata") or {}).get("name")
    except Exception as exc:
        job["query_error"] = str(exc)
    return job


def attribute_job(
    job: dict[str, Any],
    *,
    intervals: list[Interval],
    identity: WorkerIdentity,
    since: dt.datetime,
    until: dt.datetime,
) -> dict[str, Any]:
    started, resolved = parse_timestamp(job.get("started")), parse_timestamp(job.get("resolved"))
    end = resolved or (until if job["outcome"] in {"running", "pending"} else None)
    result = {**job, "configuration": None, "exclusion_reason": None, "in_window": True}
    if started and (started < since or started > until or (end and end > until)):
        result.update(in_window=False, exclusion_reason="outside report window")
        return result
    if job.get("query_error"):
        result["exclusion_reason"] = "task query/identity unresolved"
        return result
    config, reason = classify_run(intervals, started=started, ended=end)
    if config and config.identity != identity:
        config, reason = None, "historical worker identity differs from queried identity"
    result["configuration"] = configuration_json(config) if config else None
    result["exclusion_reason"] = reason
    return result


def collect_host(
    host: str,
    *,
    records: list[dict[str, Any]],
    master_refs: set[tuple[str, str]],
    overrides_dir: Path,
    since: dt.datetime,
    until: dt.datetime,
    max_gap: dt.timedelta,
    worker_group: str,
    clients: Any,
) -> dict[str, Any]:
    latest = latest_observation(records)
    stamp = parse_timestamp(latest.get("ts"))
    intervals = build_history(
        records,
        overrides_dir=overrides_dir,
        master_refs=master_refs,
        max_gap=max_gap,
        worker_group=worker_group,
    )
    current, reason = verified_configuration(
        latest, overrides_dir=overrides_dir, master_refs=master_refs, worker_group=worker_group
    )
    if stamp is None or until - stamp > max_gap:
        current, reason = None, "latest host observation is stale or missing"
    # A write event after the latest observation invalidates its current state.
    if any(
        entry.get("action") in WRITE_ACTIONS
        and (event_time := parse_timestamp(entry.get("ts")))
        and (stamp is None or event_time > stamp)
        for entry in records
    ):
        current, reason = None, "configuration write after latest observation; gather-host required"
    if current and not any(
        item.end == stamp and item.configuration == current for item in intervals
    ):
        current, reason = (
            None,
            "latest observation has conflicting or unverified transition evidence",
        )
    result: dict[str, Any] = {
        "host": host,
        "recorded_host": host,
        "role": last_known_role(records),
        "observation_ts": latest.get("ts"),
        "observation_age_s": (until - stamp).total_seconds() if stamp else None,
        "configuration": configuration_json(current) if current else None,
        "configuration_reason": reason,
        "intervals": [
            {
                "start": iso(item.start),
                "end": iso(item.end),
                "configuration": configuration_json(item.configuration),
                "source": item.source,
                "max_observation_gap_s": item.max_gap_s,
                "retention_cutoff": iso(item.retention_cutoff) if item.retention_cutoff else None,
            }
            for item in intervals
            if item.end >= since and item.start <= until
        ],
        "history_window": 0,
        "history_may_be_truncated": False,
        "query_ok": False,
        "jobs": [],
        "error": None,
        "warnings": [],
        "configuration_evidence": [
            {
                "observation_ts": entry["ts"],
                "provenance": (entry.get("_configuration_proof") or {}).get("provenance"),
                "unknown_reason": entry.get("_compact_reason"),
            }
            for entry in records
            if "_compact_unverified" in entry
        ],
        "configuration_gaps": history_gaps(intervals, since=since, until=until),
    }
    identity, identity_reason = worker_identity(
        latest, overrides_dir=overrides_dir, worker_group=worker_group
    )
    if identity is None or stamp is None or until - stamp > max_gap:
        result["error"] = identity_reason or "latest host observation is stale or missing"
        return result
    result["scope"] = [result["role"], identity.provisioner, identity.pool, identity.worker_group]
    if identity.legacy:
        result["warnings"].append(
            "Legacy role/override worker identity fallback; refresh gather-host."
        )
    try:
        worker = clients.worker_manager.getWorker(
            identity.provisioner, identity.pool, identity.worker_group, identity.worker_id
        )
        refs = worker.get("recentTasks") or []
        if not isinstance(refs, list):
            raise TypeError("worker recentTasks is not a list")
        # Deduplicate runs without dropping unresolved malformed references.
        unique = {json.dumps(ref, sort_keys=True): ref for ref in refs}
        refs = list(unique.values())[:RECENT_TASK_LIMIT]
        result["history_window"] = len(refs)
        result["history_may_be_truncated"] = len(refs) >= RECENT_TASK_LIMIT
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(fetch_task_run, clients, ref, identity=identity) for ref in refs
            ]
            for future in concurrent.futures.as_completed(futures):
                result["jobs"].append(
                    attribute_job(
                        future.result(),
                        intervals=intervals,
                        identity=identity,
                        since=since,
                        until=until,
                    )
                )
        result["jobs"].sort(key=lambda job: (job.get("started") or "", job.get("task_id") or ""))
        result["query_ok"] = True
    except Exception as exc:
        result["error"] = str(exc)
    return result


def outcome_summary(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(job["outcome"] for job in jobs)
    finished = sum(counts[key] for key in ("completed", "failed", "exception"))
    return {
        "jobs": len(jobs),
        "outcomes": {key: counts[key] for key in OUTCOMES},
        "finished": finished,
        "rate_denominator": finished,
        "success_rate": counts["completed"] / finished if finished else None,
        "failure_rate": counts["failed"] / finished if finished else None,
        "exception_rate": counts["exception"] / finished if finished else None,
    }


def coverage(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [job for job in jobs if job["in_window"]]
    classified = [job for job in candidates if job["configuration"] is not None]
    return {
        "fetched": len(jobs),
        "in_window": len(candidates),
        "outside_window": len(jobs) - len(candidates),
        "classified": len(classified),
        "excluded": len(candidates) - len(classified),
        "unresolved": sum(bool(job.get("query_error")) for job in candidates),
        "fraction": len(classified) / len(candidates) if candidates else None,
        "exclusion_reasons": dict(
            Counter(job["exclusion_reason"] for job in candidates if not job["configuration"])
        ),
    }


def summarize(hosts: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    patterns: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}
    for host in hosts:
        configs = [item["configuration"] for item in host["intervals"]]
        if host["configuration"]:
            configs.append(host["configuration"])
        for config in configs:
            groups.setdefault(
                config["id"],
                {
                    "configuration": config,
                    "current_hosts": set(),
                    "contributing_hosts": set(),
                    "jobs": [],
                    "revisions": Counter(),
                },
            )
        if host["configuration"]:
            groups[host["configuration"]["id"]]["current_hosts"].add(host["host"])
        for job in host["jobs"]:
            config = job["configuration"]
            if not config or not job["in_window"]:
                continue
            group = groups[config["id"]]
            group["jobs"].append(job)
            group["contributing_hosts"].add(host["host"])
            group["revisions"][config["git_sha"]] += 1
            if job["outcome"] in {"failed", "exception", "unknown"}:
                name = job["name"] or str(job["task_id"])
                pattern = patterns.setdefault(
                    (tuple(config["scope"]), name),
                    {
                        "name": name,
                        "scope": config["scope"],
                        "cohorts": Counter(),
                        "hosts": set(),
                        "example_task": job["task_id"],
                    },
                )
                pattern["cohorts"][config["id"]] += 1
                pattern["hosts"].add(host["host"])
    cohorts = [
        {
            "configuration": group["configuration"],
            "current_hosts": sorted(group["current_hosts"]),
            "contributing_hosts": sorted(group["contributing_hosts"]),
            "applied_revisions": dict(group["revisions"]),
            **outcome_summary(group["jobs"]),
        }
        for _, group in sorted(groups.items())
    ]
    for pattern in patterns.values():
        pattern["cohorts"] = dict(pattern["cohorts"])
        pattern["hosts"] = sorted(pattern["hosts"])
        pattern["seen_on_master"] = any(
            groups[key]["configuration"]["kind"] == "master" for key in pattern["cohorts"]
        )
    jobs = [job for host in hosts for job in host["jobs"]]
    return {
        "requested_hosts": len(hosts),
        "queried_hosts": sum(host["query_ok"] for host in hosts),
        "unverified_hosts": [host["host"] for host in hosts if not host["configuration"]],
        "empty_task_hosts": [
            host["host"] for host in hosts if host["query_ok"] and not host["history_window"]
        ],
        "truncated_hosts": [host["host"] for host in hosts if host["history_may_be_truncated"]],
        "coverage": coverage(jobs),
        "all_observed_outcomes": outcome_summary([job for job in jobs if job["in_window"]]),
        "unattributed_outcomes": outcome_summary(
            [job for job in jobs if job["in_window"] and job["configuration"] is None]
        ),
        "cohorts": cohorts,
        "failure_patterns": list(patterns.values()),
    }


def comparison_jobs(hosts, *, config_id, start, end):
    return [
        job
        for host in hosts
        for job in host["jobs"]
        if job["configuration"]
        and job["configuration"]["id"] == config_id
        and (stamp := parse_timestamp(job["started"]))
        and start <= stamp <= end
        and (resolved := parse_timestamp(job["resolved"]))
        and resolved <= end
    ]


def cohort_candidates(hosts, *, config_id, start, end):
    """Keep unclassified runs on potentially contributing hosts in the denominator."""
    candidates = []
    for host in hosts:
        ids = {item["configuration"]["id"] for item in host["intervals"]}
        if host["configuration"]:
            ids.add(host["configuration"]["id"])
        if config_id not in ids:
            continue
        for job in host["jobs"]:
            if not job["in_window"]:
                continue
            config = job["configuration"]
            if config and config["id"] != config_id:
                continue
            stamp = parse_timestamp(job["started"])
            if stamp is None or start <= stamp <= end:
                candidates.append(job)
    return candidates


def assess_host_queries(queried: int, *, requested: int, minimum: float) -> dict[str, Any]:
    """Allow partial host evidence, while retaining an explicit population bound."""
    fraction = queried / requested if requested else None
    reasons, warnings = [], []
    if fraction is None or fraction < minimum:
        reasons.append(
            f"host query coverage {percentage(fraction)} below required {percentage(minimum)}"
        )
    if queried < requested:
        warnings.append(
            f"{requested - queried}/{requested} hosts not queried; their task counts are unknown"
        )
    return {
        "queried_hosts": queried,
        "requested_hosts": requested,
        "fraction": fraction,
        "minimum_fraction": minimum,
        "reasons": reasons,
        "warnings": warnings,
    }


def assess_attribution(fraction: float | None, *, threshold: float) -> dict[str, Any]:
    limited = fraction is None or fraction < threshold
    return {
        "status": "limited" if limited else "meets threshold",
        "fraction": fraction,
        "warning_threshold": threshold,
        "warnings": [
            f"configuration attribution coverage {percentage(fraction)} "
            f"below warning threshold {percentage(threshold)}"
        ]
        if limited
        else [],
    }


def observed_comparison_result(master, *, override, comparable: bool) -> str:
    """Describe the verified sample without claiming significance or rollout approval."""
    if not comparable:
        return "comparison unavailable"
    if override["failure_rate"] > master["failure_rate"]:
        return "higher override failure rate observed"
    if override["exception_rate"] > master["exception_rate"]:
        return "higher override exception rate observed"
    return "no regression observed in verified tasks"


def unattributed_failures(hosts, *, start, end):
    """Keep failed/exception runs fully inside the shared window visible as unknown evidence."""
    return [
        {"host": host["host"], **job}
        for host in hosts
        for job in host["jobs"]
        if job["in_window"]
        and job["configuration"] is None
        and job["outcome"] in {"failed", "exception"}
        and (started := parse_timestamp(job["started"])) is not None
        and (resolved := parse_timestamp(job["resolved"])) is not None
        and start <= started <= resolved <= end
    ]


def compare_cohorts(
    hosts, *, summary, since, until, min_finished, min_coverage, min_host_coverage=0.8
):
    comparisons = []
    by_scope = defaultdict(list)
    for group in summary["cohorts"]:
        by_scope[tuple(group["configuration"]["scope"])].append(group)
    for scope, groups in sorted(by_scope.items()):
        master = next(
            (group for group in groups if group["configuration"]["kind"] == "master"), None
        )
        overrides = [group for group in groups if group["configuration"]["kind"] == "override"]
        if master is None or not overrides:
            comparisons.append(
                {
                    "scope": list(scope),
                    "status": "unavailable",
                    "observed_result": "comparison unavailable",
                    "sample_status": "unavailable",
                    "reasons": ["master or override cohort absent"],
                    "window": None,
                }
            )
            continue
        for override in overrides:
            configs = [master["configuration"]["id"], override["configuration"]["id"]]
            ranges = [
                [
                    item
                    for host in hosts
                    for item in host["intervals"]
                    if item["configuration"]["id"] == config_id
                ]
                for config_id in configs
            ]
            if any(not items for items in ranges):
                comparisons.append(
                    {
                        "scope": list(scope),
                        "master": configs[0],
                        "override": configs[1],
                        "status": "unavailable",
                        "observed_result": "comparison unavailable",
                        "sample_status": "unavailable",
                        "reasons": ["no verified intervals in the requested window"],
                        "window": None,
                    }
                )
                continue
            start = max(
                since,
                *(
                    min(dt.datetime.fromisoformat(item["start"]) for item in items)
                    for items in ranges
                ),
            )
            end = min(
                until,
                *(
                    max(dt.datetime.fromisoformat(item["end"]) for item in items)
                    for items in ranges
                ),
            )
            reasons = []
            if start >= end:
                comparisons.append(
                    {
                        "scope": list(scope),
                        "master": configs[0],
                        "override": configs[1],
                        "status": "unavailable",
                        "observed_result": "comparison unavailable",
                        "sample_status": "unavailable",
                        "reasons": ["no overlapping verified time window"],
                        "window": None,
                    }
                )
                continue
            pair_jobs = [
                comparison_jobs(hosts, config_id=config_id, start=start, end=end)
                for config_id in configs
            ]
            stats = [outcome_summary(jobs) for jobs in pair_jobs]
            relevant = [
                host
                for host in hosts
                if host.get("scope") == list(scope)
                or (host["role"] == scope[0] and not host.get("scope"))
                or any(item["configuration"]["scope"] == list(scope) for item in host["intervals"])
            ]
            candidates = [
                job
                for host in relevant
                for job in host["jobs"]
                if job["in_window"]
                and ((stamp := parse_timestamp(job["started"])) is None or start <= stamp <= end)
            ]
            pair_coverage = coverage(candidates)
            side_coverage = [
                coverage(cohort_candidates(hosts, config_id=config_id, start=start, end=end))
                for config_id in configs
            ]
            attribution = assess_attribution(pair_coverage["fraction"], threshold=min_coverage)
            host_coverage = assess_host_queries(
                sum(host["query_ok"] for host in relevant),
                requested=len(relevant),
                minimum=min_host_coverage,
            )
            reasons.extend(host_coverage["reasons"])
            warnings = list(host_coverage["warnings"])
            unverified = sum(not host["configuration"] for host in relevant)
            if unverified:
                warnings.append(f"{unverified} current host configuration(s) unverified")
            for label, jobs, stat, evidence in zip(
                ("master", "override"), pair_jobs, stats, side_coverage, strict=True
            ):
                if stat["finished"] < min_finished:
                    reasons.append(f"{label}: fewer than {min_finished} finished tasks")
                if evidence["fraction"] is None or evidence["fraction"] < min_coverage:
                    attribution["status"] = "limited"
                    attribution["warnings"].append(
                        f"{label}: configuration attribution coverage "
                        f"{percentage(evidence['fraction'])} below warning threshold {percentage(min_coverage)}"
                    )
                if len({job["configuration"]["git_sha"] for job in jobs}) > 1:
                    reasons.append(f"{label}: multiple applied revisions in the comparison")
            warnings.extend(attribution["warnings"])
            excluded_failures = unattributed_failures(relevant, start=start, end=end)
            for outcome, label in (("failed", "failure"), ("exception", "exception")):
                count = sum(job["outcome"] == outcome for job in excluded_failures)
                if count:
                    warnings.append(
                        f"{count} unattributed {label}{'' if count == 1 else 's'} "
                        "inside the comparison window"
                    )
            if any(job.get("query_error") or job["outcome"] == "unknown" for job in candidates):
                reasons.append("unresolved task queries or outcomes")
            differences = {}
            for rate in ("success_rate", "failure_rate", "exception_rate"):
                left, right = stats[0][rate], stats[1][rate]
                differences[f"{rate}_difference_pp"] = (
                    (right - left) * 100 if left is not None and right is not None else None
                )
            comparisons.append(
                {
                    "scope": list(scope),
                    "master": configs[0],
                    "override": configs[1],
                    "window": {"since": iso(start), "until": iso(end)},
                    "master_results": stats[0],
                    "override_results": stats[1],
                    "coverage": pair_coverage,
                    "master_coverage": side_coverage[0],
                    "override_coverage": side_coverage[1],
                    "host_query_coverage": host_coverage,
                    "sample_status": "sufficient"
                    if all(stat["finished"] >= min_finished for stat in stats)
                    else "insufficient",
                    "minimum_finished": min_finished,
                    "observed_result": observed_comparison_result(
                        stats[0], override=stats[1], comparable=not reasons
                    ),
                    "attribution": attribution,
                    "unattributed_failures": excluded_failures,
                    "status": "insufficient evidence" if reasons else "comparable",
                    "reasons": reasons,
                    "warnings": warnings,
                    **differences,
                }
            )
    return comparisons


def evidence_status(summary, *, min_finished, min_coverage, min_host_coverage=0.8):
    host_coverage = assess_host_queries(
        summary["queried_hosts"],
        requested=summary["requested_hosts"],
        minimum=min_host_coverage,
    )
    reasons = list(host_coverage["reasons"])
    warnings = list(host_coverage["warnings"])
    if summary["unverified_hosts"]:
        warnings.append(
            f"{len(summary['unverified_hosts'])} current host configuration(s) unverified"
        )
    fraction = summary["coverage"]["fraction"]
    attribution = assess_attribution(fraction, threshold=min_coverage)
    warnings.extend(attribution["warnings"])
    small_sample = not summary["cohorts"] or any(
        group["finished"] < min_finished for group in summary["cohorts"]
    )
    if small_sample:
        reasons.append(f"fewer than {min_finished} finished tasks in at least one cohort")
    if summary["coverage"]["unresolved"]:
        reasons.append("unresolved task queries")
    return {
        "status": "insufficient evidence" if reasons else "sample available",
        "sample_status": "insufficient" if small_sample else "sufficient",
        "minimum_finished": min_finished,
        "attribution": attribution,
        "reasons": reasons,
        "warnings": warnings,
        "host_query_coverage": host_coverage,
    }


def percentage(value):
    return f"{value * 100:.1f}%" if value is not None else "—"


def configuration_label(config):
    if config["kind"] == "master":
        return "master"
    return f"{config['branch']} ({config['override_sha256'][:12]})"


def report_color_enabled():
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"


def compact_host_names(hosts):
    """Compress consecutive numeric hostnames without combining distinct domains."""
    groups = defaultdict(set)
    plain = set()
    for host in hosts:
        match = re.fullmatch(r"(.*?)(\d+)(\..*)?", host)
        if match:
            prefix, digits, suffix = match.groups()
            groups[(prefix, suffix or "", len(digits))].add(int(digits))
        else:
            plain.add(host)
    formatted = sorted(plain)
    for (prefix, suffix, width), numbers in sorted(groups.items()):
        runs = []
        for number in sorted(numbers):
            if runs and number == runs[-1][1] + 1:
                runs[-1][1] = number
            else:
                runs.append([number, number])
        parts = [
            f"{start:0{width}d}-{end:0{width}d}" if start != end else f"{start:0{width}d}"
            for start, end in runs
        ]
        formatted.append(f"{prefix}[{', '.join(parts)}]{suffix}")
    return ", ".join(formatted)


def print_issues(report, *, style, verbose=False):
    known_hosts = {host["host"] for host in report["hosts"]}
    recent_failed = {
        f"{host['host']}: {host['error']}"
        for host in report["hosts"]
        if host["error"] == "latest host observation failed"
        and host["observation_age_s"] is not None
        and 0 <= host["observation_age_s"] <= 300
    }
    groups = defaultdict(list)
    for level, messages in (("warning", report["warnings"]), ("error", report["errors"])):
        for message in messages:
            host, separator, reason = message.partition(": ")
            if message in recent_failed:
                level_for_message = "warning"
                reason += "; recent failure may be a reboot. Worker jobs were not queried; refresh gather-host."
            else:
                level_for_message = level
            if separator and host in known_hosts:
                groups[(level_for_message, reason)].append(host)
            else:
                label = "⚠ WARNING" if level_for_message == "warning" else "✕ ERROR"
                print(
                    f"{style(label, fg='yellow' if level_for_message == 'warning' else 'red', bold=True)}: {message}"
                )
    for (level, reason), hosts in groups.items():
        label = "⚠ WARNING" if level == "warning" else "✕ ERROR"
        prefix = style(label, fg="yellow" if level == "warning" else "red", bold=True)
        if verbose:
            for host in hosts:
                print(f"{prefix}: {host}: {reason}")
        else:
            print(f"{prefix}: {len(hosts)} host(s): {reason}")
            print(
                textwrap.fill(
                    compact_host_names(hosts),
                    width=100,
                    initial_indent="  ",
                    subsequent_indent="  ",
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )


def print_observed_outcomes(summary):
    """Show all retrieved outcomes separately from configuration attribution."""
    observed = summary["all_observed_outcomes"]
    counts = observed["outcomes"]
    print(f"  All observed in-window runs: {observed['jobs']} (across configurations)")
    print(
        f"    Completed: {counts['completed']}  ·  Failed: {counts['failed']}  ·  "
        f"Exceptions: {counts['exception']}"
    )
    print(
        f"    Running: {counts['running']}  ·  Pending: {counts['pending']}  ·  "
        f"Unknown: {counts['unknown']}"
    )
    attributed = {
        outcome: sum(group["outcomes"][outcome] for group in summary["cohorts"])
        for outcome in OUTCOMES
    }
    print(f"  Attributed runs: {summary['coverage']['classified']}")
    print(
        f"    Completed: {attributed['completed']}  ·  Failed: {attributed['failed']}  ·  "
        f"Exceptions: {attributed['exception']}"
    )
    print(
        f"    Running: {attributed['running']}  ·  Pending: {attributed['pending']}  ·  "
        f"Unknown: {attributed['unknown']}"
    )
    print("  These outcomes include runs whose configuration could not be attributed.")
    reasons = summary["coverage"]["exclusion_reasons"]
    if reasons:
        print("  Excluded from configuration attribution:")
        for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], str(item[0]))):
            print(f"    {count}: {reason or 'reason unavailable'}")


def fleet_override_status(records, *, selected, role, worker_type, overrides_dir):
    """Summarize last successful observed override state at the report horizon."""
    latest, successful, writes = {}, {}, {}
    for host, entries in records.items():
        for entry in entries:
            action = entry.get("action", "host.audit")
            if action == "host.audit":
                latest[host] = entry
                if entry.get("ok"):
                    successful[host] = entry
            elif action in WRITE_ACTIONS:
                stamp = parse_timestamp(entry.get("ts"))
                if stamp is None:
                    continue
                key = host.split(".")[0].lower()
                if key not in writes or stamp > writes[key]:
                    writes[key] = stamp
    return build_fleet_status(
        hosts=list(records) if role or worker_type else selected,
        latest=latest,
        successful=successful,
        writes=writes,
        role=role,
        worker_type=worker_type,
        overrides_dir=overrides_dir,
        scoped=not (role or worker_type),
    )


def print_fleet_override_status(status):
    print(f"  Matching hosts with recorded membership: {status['matching_hosts']}")
    for state, label in (
        ("present", "Override present"),
        ("absent", "No override"),
        ("unknown", "Override unknown"),
    ):
        value = status["override_totals"][state]
        percent = f"{value['percentage']:.1f}%" if value["percentage"] is not None else "—"
        print(f"  {label}: {value['count']} ({percent})")
    print("  Latest successful observations; override presence does not prove Puppet applied it.")
    observed = status["observation_range"]
    print(f"  Observed: {observed['earliest'] or 'unknown'} to {observed['latest'] or 'unknown'}")
    print(f"  Membership unresolved: {len(status['unresolved_hosts'])}")
    print(
        f"  Newer failed gathers: {len(status['newer_failed_gathers'])}; "
        f"writes awaiting verification: {len(status['unverified_writes'])}"
    )


def print_unverified_hosts(report, *, details):
    """Summarize configuration uncertainty and optionally list affected hosts."""
    errors = set(report["errors"])
    grouped = defaultdict(list)
    for host in report["hosts"]:
        reason = host["configuration_reason"]
        if reason and f"{host['host']}: {reason}" not in errors:
            grouped[reason].append(host["host"])
    if not grouped:
        return
    print("\nUnverified host configurations:")
    for reason, names in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        print(f"  {len(names)} host(s): {reason}")
        if details:
            for name in sorted(names):
                print(f"    {name}")
    if not details:
        print("  Use --details or --verbose for host names.")


def print_comparison_assessment(comparison, *, style, label=None):
    result = comparison["observed_result"]
    qualified = comparison["status"] != "comparable" or result.startswith("higher")
    symbol = "⚠" if qualified else "◆"
    if label:
        print(f"  {label}")
    print(
        "  "
        + style(
            f"{symbol}  {result.capitalize()}",
            fg="yellow" if qualified else "cyan",
            bold=True,
        )
    )
    for key, name in (("master_results", "Master"), ("override_results", "Override")):
        if key in comparison:
            outcomes = comparison[key]["outcomes"]
            print(
                f"    {name}: {outcomes['completed']} completed, {outcomes['failed']} failed, "
                f"{outcomes['exception']} exceptions"
            )
    if "minimum_finished" in comparison:
        print(
            f"    Shared-window sample: {comparison['sample_status']}; "
            f"minimum {comparison['minimum_finished']} finished tasks per side"
        )
    attribution = comparison.get("attribution") or {}
    if attribution.get("status") == "limited":
        print(
            style(
                f"    ⚠ Attribution coverage below {percentage(attribution['warning_threshold'])}: "
                f"master {percentage(comparison['master_coverage']['fraction'])}; "
                f"override {percentage(comparison['override_coverage']['fraction'])}; "
                f"overall {percentage(attribution['fraction'])}",
                fg="yellow",
            )
        )
    for reason in comparison["reasons"]:
        print(f"    • {reason}")
    for warning in comparison.get("warnings", []):
        if warning not in attribution.get("warnings", []):
            print(style(f"    ⚠ {warning}", fg="yellow"))


def print_assessment(report, *, style):
    evidence = report["summary"]["evidence"]
    comparisons = report["comparisons"]
    labels = {
        group["configuration"]["id"]: configuration_label(group["configuration"])
        for group in report["summary"]["cohorts"]
    }
    for comparison in comparisons:
        print_comparison_assessment(
            comparison,
            style=style,
            label=(
                f"{labels.get(comparison.get('override'), 'Standalone population')} · "
                f"{' / '.join(comparison['scope'])}"
            )
            if len(comparisons) > 1
            else None,
        )
    if not comparisons:
        print("  Comparison unavailable")
    shown_warnings = {warning for comp in comparisons for warning in comp.get("warnings", [])}
    if any(comp.get("attribution") for comp in comparisons):
        shown_warnings.update(evidence["attribution"]["warnings"])
    for warning in evidence.get("warnings", []):
        if warning not in shown_warnings:
            print(style(f"   ⚠ {warning}", fg="yellow"))
    shown_reasons = {reason for comp in comparisons for reason in comp["reasons"]}
    for reason in evidence["reasons"]:
        if reason not in shown_reasons:
            print(f"   Report-wide evidence limit: {reason}")
    excluded = report["summary"]["unattributed_outcomes"]["outcomes"]
    if excluded["failed"] or excluded["exception"]:
        print(
            style(
                f"   ⚠ Full report window: unattributed failed runs: {excluded['failed']}; "
                f"exceptions: {excluded['exception']}. Shared-window counts shown above",
                fg="yellow",
            )
        )
    host_coverage = evidence.get("host_query_coverage")
    if host_coverage:
        print(
            f"   Host query coverage: {percentage(host_coverage['fraction'])}; "
            f"minimum {percentage(host_coverage['minimum_fraction'])}"
        )
    print("   Observed results only; not automatic rollout approval.")


def print_unattributed_failure_details(comparison):
    for job in comparison.get("unattributed_failures", []):
        print(
            f"  Unattributed {job['outcome']}: {job['host']} · "
            f"https://firefox-ci-tc.services.mozilla.com/tasks/{job['task_id']}"
        )


def print_report(report, *, details=False, verbose=False, color=None):
    if color is None:
        color = report_color_enabled()

    def style(value, **kwargs):
        return click.style(str(value), **kwargs) if color else str(value)

    def heading(title):
        print("\n" + style(f"━━━  {title.upper()}  ━━━", fg="cyan", bold=True))

    summary = report["summary"]
    labels = {
        group["configuration"]["id"]: configuration_label(group["configuration"])
        for group in summary["cohorts"]
    }
    print(style("◈  FLEETROLL  /  ROLLOUT REPORT", fg="cyan", bold=True))
    print(f"   {report['window']['since']}  →  {report['window']['until']}")
    if "queue_counts" in report:
        heading("Current queue")
        for line in render_queue_counts(report["queue_counts"], color=color, include_heading=False):
            print(f"  {line}")
    heading("Rollout assessment")
    print_assessment(report, style=style)
    heading("Fleet override status")
    print_fleet_override_status(report["fleet_override_status"])
    heading("Coverage")
    print(f"  Hosts queried: {summary['queried_hosts']}/{summary['requested_hosts']}")
    evidence = summary["coverage"]
    fraction = evidence["fraction"]
    if fraction is not None:
        filled = round(max(0, min(1, fraction)) * 20)
        bar = "█" * filled + "░" * (20 - filled)
        print("  " + style(bar, fg="cyan"))
    print(
        f"  Configuration coverage: {evidence['classified']}/{evidence['in_window']} "
        f"in-window recent runs ({percentage(evidence['fraction'])}); "
        f"{evidence['excluded']} excluded, {evidence['unresolved']} unresolved."
    )
    stamps = [host["observation_ts"] for host in report["hosts"] if host["observation_ts"]]
    if stamps:
        print(f"  Host observations: {min(stamps)} to {max(stamps)}")
    heading("Observed outcomes")
    print_observed_outcomes(summary)
    heading("Cohorts")
    print("  Hosts are current; task counts use verified historical membership.")
    for scope in sorted({tuple(group["configuration"]["scope"]) for group in summary["cohorts"]}):
        print("\n" + style("  " + " / ".join(scope), bold=True))
        for group in summary["cohorts"]:
            config = group["configuration"]
            if tuple(config["scope"]) != scope:
                continue
            label = configuration_label(config)
            outcomes = group["outcomes"]
            print("\n  " + style(label, bold=True))
            print(
                f"    Hosts: {len(group['current_hosts'])}  ·  Finished: {group['finished']}  ·  "
                f"Success: {percentage(group['success_rate'])}"
            )
            print(
                f"    Completed: {outcomes['completed']}  ·  Failed: {outcomes['failed']}  ·  "
                f"Exceptions: {outcomes['exception']}"
            )
            print(
                f"    Running: {outcomes['running']}  ·  Pending: {outcomes['pending']}  ·  "
                f"Unknown: {outcomes['unknown']}"
            )
            revisions = {sha[:12]: count for sha, count in group["applied_revisions"].items()}
            print(f"    Applied revisions (classified tasks): {revisions or 'no classified tasks'}")
            print(
                f"    Failure rate: {percentage(group['failure_rate'])}; "
                f"exception rate: {percentage(group['exception_rate'])}"
            )
    if report["comparisons"]:
        heading("Comparisons")
    for comparison in report["comparisons"]:
        label = labels.get(comparison.get("override"), "standalone population")
        print(
            "\n  "
            + style(
                f"{label}  ·  {comparison['status'].upper()}",
                fg="yellow" if comparison["status"] == "insufficient evidence" else "cyan",
                bold=True,
            )
        )
        print(f"  {' / '.join(comparison['scope'])}")
        print(f"  Observed result: {comparison['observed_result']}")
        if comparison["window"]:
            print(
                f"  Shared window: {comparison['window']['since']} to {comparison['window']['until']}"
            )
            left, right = comparison["master_results"], comparison["override_results"]
            print(
                f"  Shared-window finished tasks: master {left['finished']}, "
                f"override {right['finished']}; failure rates: "
                f"{percentage(left['failure_rate'])} / {percentage(right['failure_rate'])}"
            )
            print(
                "  Attribution coverage: master "
                f"{percentage(comparison['master_coverage']['fraction'])}, override "
                f"{percentage(comparison['override_coverage']['fraction'])}"
            )
            delta = comparison["failure_rate_difference_pp"]
            print(
                f"  Override minus master failure rate: {delta:+.2f} percentage points"
                if delta is not None
                else "  Failure-rate difference unavailable"
            )
        for reason in comparison["reasons"]:
            print(f"  {reason}")
        for warning in comparison.get("warnings", []):
            print(f"  ⚠ {warning}")
        if details or verbose:
            print_unattributed_failure_details(comparison)
    for pattern in summary["failure_patterns"]:
        print(f"\nFailure pattern: {pattern['name']}; {len(pattern['hosts'])} host(s)")
        counts = {labels[key]: count for key, count in pattern["cohorts"].items()}
        print(f"  Cohort counts: {counts}; observed on master: {pattern['seen_on_master']}")
        print(f"  https://firefox-ci-tc.services.mozilla.com/tasks/{pattern['example_task']}")
    print_unverified_hosts(report, details=details or verbose)
    if details or verbose:
        heading("Host details")
    for host in report["hosts"]:
        if details or verbose:
            if not verbose:
                jobs = host["jobs"]
                classified = sum(job["configuration"] is not None for job in jobs)
                in_window = sum(job["in_window"] for job in jobs)
                print(
                    f"{host['host']}: {classified}/{in_window} in-window runs classified; "
                    f"{len(host['configuration_gaps'])} unknown configuration range(s); "
                    f"observed {host['observation_ts']}"
                )
                continue
            print(f"\n{host['host']} observed {host['observation_ts']}")
            for gap in host["configuration_gaps"]:
                print(f"  Unknown configuration: {gap['start']} to {gap['end']}")
            for job in host["jobs"]:
                print(
                    f"  {job['outcome']}: {job['task_id']} run {job['run_id']}: "
                    f"{job['exclusion_reason'] or 'classified'}"
                )
    if report["warnings"] or report["errors"]:
        heading("Notes & issues")
    print_issues(report, style=style, verbose=verbose)
    print("\n  Rates use completed + failed + exception; running/pending/unknown are not passes.")


def cli_timestamp(value: str) -> dt.datetime:
    stamp = parse_timestamp(value)
    if stamp is None:
        raise argparse.ArgumentTypeError("expected an ISO 8601 timestamp")
    return stamp


def parse_args(argv=None, *, prog=None):
    parser = argparse.ArgumentParser(description=__doc__, prog=prog)
    parser.add_argument("hosts", nargs="*", help="Hostnames or one host-list file.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--role", help="Compare master with each override in this Puppet role.")
    selection.add_argument(
        "--worker-type",
        help="Worker type or provisioner/worker-type (default provisioner: releng-hardware).",
    )
    parser.add_argument(
        "--provisioner",
        default=None,
        help="Provisioner for a bare worker type (default releng-hardware).",
    )
    parser.add_argument(
        "--worker-group",
        default="mdc1",
        help="Legacy fallback group (observed identities take precedence).",
    )
    parser.add_argument("--db", type=Path, default=get_db_path())
    parser.add_argument("--audit-log", type=Path, default=default_audit_log_path())
    parser.add_argument(
        "--observations-file",
        type=Path,
        action="append",
        default=[],
        help="Additional saved gather-host/host-monitor JSON evidence (repeatable, read-only).",
    )
    parser.add_argument(
        "--since", type=cli_timestamp, help="ISO 8601 start, default: 24 hours before --until."
    )
    parser.add_argument("--until", type=cli_timestamp, help="ISO 8601 end, default: now.")
    parser.add_argument(
        "--max-observation-gap",
        type=float,
        default=3600,
        help="Maximum observation age/continuity gap in seconds (default 3600).",
    )
    parser.add_argument(
        "--min-finished",
        type=int,
        default=20,
        help="Minimum finished tasks per comparison side (default 20).",
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=80,
        help="Attribution coverage warning threshold percent (default 80); does not veto comparison.",
    )
    parser.add_argument(
        "--min-host-coverage",
        type=float,
        default=80,
        help="Minimum successfully queried host percent (default 80); missing hosts remain warnings.",
    )
    parser.add_argument(
        "--workers", type=int, default=4, help="Concurrent host queries (1-20, default 4)."
    )
    parser.add_argument(
        "--details", action="store_true", help="Print a compact per-host attribution summary."
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print individual task results and configuration gaps.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.hosts) + bool(args.role) + bool(args.worker_type) != 1:
        parser.error("pass exactly one of hostnames/file, --role, or --worker-type")
    if args.provisioner is not None and not args.worker_type:
        parser.error("--provisioner requires --worker-type")
    if args.worker_type:
        try:
            provisioner, pool = parse_worker_type(
                args.worker_type, default_provisioner=args.provisioner or DEFAULT_TC_PROVISIONER
            )
        except ValueError as exc:
            parser.error(f"--worker-type: {exc}")
        if (
            "/" in args.worker_type
            and args.provisioner is not None
            and args.provisioner != provisioner
        ):
            parser.error("--provisioner conflicts with --worker-type")
        args.provisioner, args.worker_type = provisioner, pool
    else:
        args.provisioner = DEFAULT_TC_PROVISIONER
    if (
        not 1 <= args.workers <= 20
        or not math.isfinite(args.max_observation_gap)
        or not 0 < args.max_observation_gap <= 366 * 86400
        or args.min_finished < 1
        or not 0 <= args.min_coverage <= 100
        or not 0 <= args.min_host_coverage <= 100
    ):
        parser.error("invalid workers, observation gap, minimum finished, or coverage threshold")
    now = dt.datetime.now(dt.UTC)
    until: dt.datetime = args.until or now
    if until > now:
        parser.error("--until must be a valid timestamp no later than now")
    since: dt.datetime = args.since or until - dt.timedelta(days=1)
    if since >= until:
        parser.error("--since must be a valid timestamp before --until")
    args.until, args.since = until, since
    return args


def main(argv=None, *, prog=None) -> int:
    args = parse_args(argv, prog=prog)
    overrides_dir = args.audit_log.parent / OVERRIDES_DIR_NAME
    history_metadata: dict[str, Any] = {}
    try:
        records, refs, warnings = load_evidence(
            args.db,
            audit_log=args.audit_log,
            until=args.until,
            observation_files=args.observations_file,
            history_metadata=history_metadata,
        )
        requested = (
            parse_host_list(Path(args.hosts[0]))
            if len(args.hosts) == 1 and is_host_file(args.hosts[0])
            else args.hosts
        )
        selected, errors = select_hosts(
            records,
            role=args.role,
            worker_type=args.worker_type,
            provisioner=args.provisioner,
            requested=requested,
            overrides_dir=overrides_dir,
            worker_group=args.worker_group,
        )
        if not selected:
            raise ValueError("no matching recorded hosts; refresh gather-host or check selection")
        clients = create_tc_clients(load_tc_credentials())
    except (OSError, ValueError, TypeError, sqlite3.Error, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    hosts = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                collect_host,
                host,
                records=records[host],
                master_refs=refs,
                overrides_dir=overrides_dir,
                since=args.since,
                until=args.until,
                max_gap=dt.timedelta(seconds=args.max_observation_gap),
                worker_group=args.worker_group,
                clients=clients,
            ): host
            for host in selected
        }
        for future in concurrent.futures.as_completed(futures):
            hosts.append(future.result())
            if sys.stderr.isatty():
                print(
                    f"\rChecking workers {len(hosts)}/{len(selected)}",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
    if sys.stderr.isatty():
        print(file=sys.stderr)
    hosts.sort(key=lambda host: host["host"])
    for host in hosts:
        if host["error"]:
            errors.append(f"{host['host']}: {host['error']}")
        for job in host["jobs"]:
            if job.get("query_error"):
                errors.append(f"{host['host']}: task {job['task_id']}: {job['query_error']}")
        warnings.extend(f"{host['host']}: {warning}" for warning in host["warnings"])
    summary = summarize(hosts)
    summary["evidence"] = evidence_status(
        summary,
        min_finished=args.min_finished,
        min_coverage=args.min_coverage / 100,
        min_host_coverage=args.min_host_coverage / 100,
    )
    if summary["truncated_hosts"]:
        warnings.append(
            f"{len(summary['truncated_hosts'])} host(s) have a full 20-task window; earlier jobs may be omitted."
        )
    if summary["coverage"]["excluded"]:
        warnings.append("Excluded jobs are unknown configuration evidence, not successful work.")
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "configuration_history": history_metadata,
        "fleet_override_status": fleet_override_status(
            records,
            selected=selected,
            role=args.role,
            worker_type=f"{args.provisioner}/{args.worker_type}" if args.worker_type else None,
            overrides_dir=overrides_dir,
        ),
        "window": {"since": iso(args.since), "until": iso(args.until)},
        "hosts": hosts,
        "summary": summary,
        "comparisons": compare_cohorts(
            hosts,
            summary=summary,
            since=args.since,
            until=args.until,
            min_finished=args.min_finished,
            min_coverage=args.min_coverage / 100,
            min_host_coverage=args.min_host_coverage / 100,
        ),
        "errors": errors,
        "warnings": warnings,
    }
    successful = {
        host: entry
        for host, entries in records.items()
        for entry in entries
        if entry.get("action", "host.audit") == "host.audit" and entry.get("ok")
    }
    report["queue_counts"] = collect_queue_counts(
        report["fleet_override_status"],
        observations=successful,
        overrides_dir=overrides_dir,
        queue=clients.queue,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report, details=args.details, verbose=args.verbose)
    failed = any(
        group["outcomes"][outcome]
        for group in summary["cohorts"]
        for outcome in ("failed", "exception", "unknown")
    )
    return int(bool(errors or failed))
