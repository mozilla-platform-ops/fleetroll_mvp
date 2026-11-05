"""Classify completed per-host TC lookups without replacing successful evidence."""

from __future__ import annotations

from typing import Any

LOOKUP_ERRORS = {
    "identity_unresolved": "Worker identity could not be resolved; refresh host configuration.",
    "pool_query_failed": "Taskcluster worker pool query failed.",
    "worker_missing": "No worker matched the queried identity.",
    "worker_ambiguous": "Multiple workers matched the legacy worker ID.",
    "task_lookup_failed": "Latest task run could not be collected.",
}


def build_lookup_attempts(
    hosts: list[str],
    *,
    host_to_worker_type: dict[str, tuple[str, str]],
    observed_identities: dict[str, dict[str, str] | None],
    host_to_override_pool: dict[str, str | None],
    workers_by_pool: dict[tuple[str, str], list[dict[str, Any]]],
    failed_pools: set[tuple[str, str]],
    worker_records: list[dict[str, Any]],
    ts: str,
    started_ts: str,
) -> list[dict[str, Any]]:
    successes = {record["host"]: record for record in worker_records}
    attempts = []
    for host in hosts:
        pool = host_to_worker_type.get(host)
        observed = observed_identities.get(host)
        identity = {
            "provisioner": pool[0] if pool else None,
            "worker_type": pool[1] if pool else None,
            "worker_group": observed["workerGroup"] if pool and observed else None,
            "worker_id": (observed["workerId"] if observed else host.split(".")[0])
            if pool
            else None,
        }
        source = (
            "host_configuration"
            if host in observed_identities
            else "legacy_override"
            if pool and host_to_override_pool.get(host)
            else "legacy_role"
            if pool
            else "unknown"
        )
        if not pool:
            status = "identity_unresolved"
        elif pool in failed_pools:
            status = "pool_query_failed"
        elif host in successes:
            status = "success"
        else:
            matches = [
                worker
                for worker in workers_by_pool.get(pool, [])
                if worker.get("workerId") == identity["worker_id"]
                and (not observed or worker.get("workerGroup") == identity["worker_group"])
            ]
            if len(matches) > 1:
                status = "worker_ambiguous"
            elif matches:
                status = "task_lookup_failed"
            else:
                status = "worker_missing"
        attempts.append(
            {
                "host": host,
                "ts": ts,
                "started_ts": started_ts,
                "identity": identity,
                "resolution_source": source,
                "status": status,
                # Fixed messages deliberately exclude raw API exceptions, URLs and credentials.
                "error": LOOKUP_ERRORS.get(status),
                "successful_observation_ts": successes.get(host, {}).get("ts"),
            }
        )
    return attempts
