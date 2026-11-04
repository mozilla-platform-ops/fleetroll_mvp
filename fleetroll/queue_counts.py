"""Live, whole-pool queue depth alongside recorded fleet evidence."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import click

import taskcluster

from .rollout_history import worker_identity
from .taskcluster import _TimeoutSession


def collect_queue_counts(
    status: dict[str, Any],
    *,
    observations: dict[str, dict],
    overrides_dir: Path,
    queue: Any = None,
) -> dict[str, Any]:
    """Query each effective pool once; failures are unknown, never zero."""
    pools = set()
    selected_pool = status["selector"]["worker_type"]
    if selected_pool:
        pools.add(selected_pool)
    unresolved, legacy = [], []
    for group in status["groups"]:
        for host in group["hosts"]:
            identity, _ = worker_identity(observations[host], overrides_dir=overrides_dir)
            if identity:
                pools.add(f"{identity.provisioner}/{identity.pool}")
                if identity.legacy:
                    legacy.append(host)
            else:
                unresolved.append(host)
    if queue is None and pools:
        # Queue counts are public; fleet-status needs no local TC credentials.
        queue = taskcluster.Queue(
            {"rootUrl": "https://firefox-ci-tc.services.mozilla.com"},
            session=_TimeoutSession(),
        )
    results = []
    for pool in sorted(pools):
        pending, error = None, None
        try:
            response = queue.taskQueueCounts(pool)
            value = response.get("pendingTasks")
            if type(value) is not int or value < 0:
                raise ValueError("invalid pendingTasks in Taskcluster response")
            pending = value
        except Exception as exc:
            error = str(exc)
        results.append(
            {
                "worker_pool": pool,
                "pending_tasks": pending,
                "checked_at": dt.datetime.now(dt.UTC).isoformat(),
                "error": error,
            }
        )
    return {
        "scope": "whole_pool",
        "approximate": True,
        "pools": results,
        "unresolved_hosts": sorted(unresolved),
        "legacy_mapped_hosts": sorted(legacy),
    }


def render_queue_counts(
    counts: dict[str, Any], *, color: bool = False, include_heading: bool = True
) -> list[str]:
    lines = (
        ["Current queue (approximate pending tasks; whole pool)"]
        if include_heading
        else ["Approximate pending tasks; whole pool"]
    )
    for pool in counts["pools"]:
        pending = pool["pending_tasks"]
        value = f"{pending:,} pending" if pending is not None else "unavailable"
        warning = pending == 0 or pending is None
        line = f"  {pool['worker_pool']}: {value}"
        if pending == 0:
            line = f"  ⚠ {pool['worker_pool']}: {value} — no new work queued"
        if color:
            line = click.style(line, fg="yellow" if warning else "cyan", bold=True)
        lines.append(line)
        lines.append(f"    Checked: {pool['checked_at']}")
        if pool["error"]:
            lines.append(f"    {pool['error']}")
    if not counts["pools"]:
        lines.append("  Unavailable: no resolved worker pools.")
    if counts["unresolved_hosts"]:
        lines.append(f"  Worker pool unresolved: {len(counts['unresolved_hosts'])} host(s)")
    if counts["legacy_mapped_hosts"]:
        lines.append(f"  Legacy pool mapping used: {len(counts['legacy_mapped_hosts'])} host(s)")
    return lines
