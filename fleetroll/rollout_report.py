"""Compare recent Taskcluster outcomes on master and each override within a role."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .audit import iter_audit_records
from .constants import DEFAULT_TC_PROVISIONER, OVERRIDES_DIR_NAME
from .db import get_db_path
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
from .utils import default_audit_log_path, is_host_file, parse_host_list

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
                records[entry["host"]].append(entry)
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
        unique = {json.dumps(entry, sort_keys=True): entry for entry in entries}
        records[host] = sorted(unique.values(), key=lambda entry: parse_timestamp(entry["ts"]))
    return dict(records), refs, warnings


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


def compare_cohorts(hosts, *, summary, since, until, min_finished, min_coverage):
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
            for label, jobs, stat, evidence in zip(
                ("master", "override"), pair_jobs, stats, side_coverage, strict=True
            ):
                if stat["finished"] < min_finished:
                    reasons.append(f"{label}: fewer than {min_finished} finished tasks")
                if evidence["fraction"] is None or evidence["fraction"] < min_coverage:
                    reasons.append(f"{label}: insufficient configuration attribution coverage")
                if len({job["configuration"]["git_sha"] for job in jobs}) > 1:
                    reasons.append(f"{label}: multiple applied revisions in the comparison")
            if pair_coverage["fraction"] is None or pair_coverage["fraction"] < min_coverage:
                reasons.append("insufficient configuration attribution coverage")
            if any(not host["query_ok"] or not host["configuration"] for host in relevant):
                reasons.append("host queries or current configuration evidence incomplete")
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
                    "status": "insufficient evidence" if reasons else "comparable",
                    "reasons": reasons,
                    **differences,
                }
            )
    return comparisons


def evidence_status(summary, *, min_finished, min_coverage):
    reasons = []
    if summary["queried_hosts"] < summary["requested_hosts"]:
        reasons.append("host query coverage incomplete (missing task counts unknown)")
    if summary["unverified_hosts"]:
        reasons.append("some current host configurations are unverified")
    fraction = summary["coverage"]["fraction"]
    if fraction is None or fraction < min_coverage:
        reasons.append("insufficient configuration attribution coverage")
    if not summary["cohorts"] or any(
        group["finished"] < min_finished for group in summary["cohorts"]
    ):
        reasons.append(f"fewer than {min_finished} finished tasks in at least one cohort")
    if summary["coverage"]["unresolved"]:
        reasons.append("unresolved task queries")
    return {
        "status": "insufficient evidence" if reasons else "sample available",
        "reasons": reasons,
    }


def percentage(value):
    return f"{value * 100:.1f}%" if value is not None else "—"


def configuration_label(config):
    if config["kind"] == "master":
        return "master"
    return f"{config['branch']} ({config['override_sha256'][:12]})"


def print_report(report, *, details=False):
    summary = report["summary"]
    labels = {
        group["configuration"]["id"]: configuration_label(group["configuration"])
        for group in summary["cohorts"]
    }
    print(f"Rollout report: {report['window']['since']} to {report['window']['until']}")
    print(f"Hosts queried: {summary['queried_hosts']}/{summary['requested_hosts']}")
    evidence = summary["coverage"]
    print(
        f"Configuration coverage: {evidence['classified']}/{evidence['in_window']} "
        f"in-window recent runs ({percentage(evidence['fraction'])}); "
        f"{evidence['excluded']} excluded, {evidence['unresolved']} unresolved."
    )
    print("Rates use completed + failed + exception; running/pending/unknown are not passes.")
    print(
        "Host counts below are current membership; task totals use verified historical membership."
    )
    print(f"Evidence: {summary['evidence']['status']} (not automatic rollout approval)")
    for reason in summary["evidence"]["reasons"]:
        print(f"  {reason}")
    stamps = [host["observation_ts"] for host in report["hosts"] if host["observation_ts"]]
    if stamps:
        print(f"Host observation timestamps: {min(stamps)} to {max(stamps)}")
    print("\nROLE / PROVISIONER / POOL / WORKER GROUP")
    for scope in sorted({tuple(group["configuration"]["scope"]) for group in summary["cohorts"]}):
        print("\n" + " / ".join(scope))
        print(
            f"{'CONFIGURATION':<48} {'HOSTS':>5} {'DONE':>5} {'FAIL':>5} "
            f"{'EXC':>4} {'RUN':>4} {'PEND':>4} {'UNK':>4} {'SUCCESS':>8}"
        )
        for group in summary["cohorts"]:
            config = group["configuration"]
            if tuple(config["scope"]) != scope:
                continue
            label = configuration_label(config)
            outcomes = group["outcomes"]
            print(
                f"{label:<48} {len(group['current_hosts']):>5} {outcomes['completed']:>5} "
                f"{outcomes['failed']:>5} {outcomes['exception']:>4} {outcomes['running']:>4} "
                f"{outcomes['pending']:>4} {outcomes['unknown']:>4} "
                f"{percentage(group['success_rate']):>8}"
            )
            revisions = {sha[:12]: count for sha, count in group["applied_revisions"].items()}
            print(f"  Applied revisions (classified tasks): {revisions or 'no classified tasks'}")
            print(
                f"  Finished: {group['finished']}; failure rate: {percentage(group['failure_rate'])}; "
                f"exception rate: {percentage(group['exception_rate'])}"
            )
    for comparison in report["comparisons"]:
        label = labels.get(comparison.get("override"), "standalone population")
        print(f"\nComparison {' / '.join(comparison['scope'])}: {label}: {comparison['status']}")
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
    for pattern in summary["failure_patterns"]:
        print(f"\nFailure pattern: {pattern['name']}; {len(pattern['hosts'])} host(s)")
        counts = {labels[key]: count for key, count in pattern["cohorts"].items()}
        print(f"  Cohort counts: {counts}; observed on master: {pattern['seen_on_master']}")
        print(f"  https://firefox-ci-tc.services.mozilla.com/tasks/{pattern['example_task']}")
    for host in report["hosts"]:
        if host["configuration_reason"]:
            print(f"\nUnverified {host['host']}: {host['configuration_reason']}")
        if details:
            print(f"\n{host['host']} observed {host['observation_ts']}")
            for job in host["jobs"]:
                print(
                    f"  {job['outcome']}: {job['task_id']} run {job['run_id']}: "
                    f"{job['exclusion_reason'] or 'classified'}"
                )
    for warning in report["warnings"]:
        print(f"WARNING: {warning}")
    for error in report["errors"]:
        print(f"ERROR: {error}")


def cli_timestamp(value: str) -> dt.datetime:
    stamp = parse_timestamp(value)
    if stamp is None:
        raise argparse.ArgumentTypeError("expected an ISO 8601 timestamp")
    return stamp


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hosts", nargs="*", help="Hostnames or one host-list file.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--role", help="Compare master with each override in this Puppet role.")
    selection.add_argument(
        "--worker-type", help="Select an effective worker pool; keep roles separate."
    )
    parser.add_argument("--provisioner", default=DEFAULT_TC_PROVISIONER)
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
        help="Minimum classified recent-run coverage percent (default 80).",
    )
    parser.add_argument(
        "--workers", type=int, default=4, help="Concurrent host queries (1-20, default 4)."
    )
    parser.add_argument(
        "--details", action="store_true", help="Print per-host task attribution details."
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.hosts) + bool(args.role) + bool(args.worker_type) != 1:
        parser.error("pass exactly one of hostnames/file, --role, or --worker-type")
    if args.provisioner != DEFAULT_TC_PROVISIONER and not args.worker_type:
        parser.error("--provisioner requires --worker-type")
    if (
        not 1 <= args.workers <= 20
        or not math.isfinite(args.max_observation_gap)
        or not 0 < args.max_observation_gap <= 366 * 86400
        or args.min_finished < 1
        or not 0 <= args.min_coverage <= 100
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


def main(argv=None) -> int:
    args = parse_args(argv)
    overrides_dir = args.audit_log.parent / OVERRIDES_DIR_NAME
    try:
        records, refs, warnings = load_evidence(
            args.db,
            audit_log=args.audit_log,
            until=args.until,
            observation_files=args.observations_file,
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
        summary, min_finished=args.min_finished, min_coverage=args.min_coverage / 100
    )
    if summary["truncated_hosts"]:
        warnings.append(
            f"{len(summary['truncated_hosts'])} host(s) have a full 20-task window; earlier jobs may be omitted."
        )
    if summary["coverage"]["excluded"]:
        warnings.append("Excluded jobs are unknown configuration evidence, not successful work.")
    report = {
        "schema_version": SCHEMA_VERSION,
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
        ),
        "errors": errors,
        "warnings": warnings,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report, details=args.details)
    failed = any(
        group["outcomes"][outcome]
        for group in summary["cohorts"]
        for outcome in ("failed", "exception", "unknown")
    )
    return int(bool(errors or failed))
