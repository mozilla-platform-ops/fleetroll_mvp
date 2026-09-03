"""TaskCluster API integration."""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

import taskcluster

from .exceptions import FleetRollError

logger = logging.getLogger(__name__)
TASK_STATUS_WORKERS = 20
TC_REQUEST_TIMEOUT_SECONDS = 30


class _TimeoutSession(requests.Session):
    """Requests session that prevents Taskcluster SDK calls from hanging."""

    def request(self, *args: Any, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", TC_REQUEST_TIMEOUT_SECONDS)
        return super().request(*args, **kwargs)


@dataclass(frozen=True)
class TaskClusterCredentials:
    """TaskCluster API credentials."""

    client_id: str
    access_token: str


@dataclass(frozen=True)
class TaskClusterClients:
    """Authenticated Taskcluster REST clients used during a collection."""

    queue: Any
    worker_manager: Any


@dataclass(frozen=True)
class WorkerFetchResult:
    """Workers returned by REST plus non-fatal task-status failures."""

    workers: list[dict[str, Any]]
    status_errors: int


def load_tc_credentials() -> TaskClusterCredentials:
    """Load TaskCluster credentials from file.

    Looks for credentials in this order:
    1. File path from TC_TOKEN environment variable
    2. ~/.tc_token

    Returns:
        TaskClusterCredentials object

    Raises:
        FleetRollError: If credentials file not found or invalid
    """
    token_file = os.environ.get("TC_TOKEN")
    if token_file:
        cred_path = Path(token_file)
    else:
        cred_path = Path.home() / ".tc_token"

    if not cred_path.exists():
        raise FleetRollError(
            f"TaskCluster credentials not found at {cred_path}. "
            f"Create a JSON file with clientId and accessToken fields, "
            f"or set TC_TOKEN environment variable to the file path."
        )

    try:
        with cred_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise FleetRollError(f"Invalid JSON in {cred_path}: {e}")
    except Exception as e:
        raise FleetRollError(f"Failed to read {cred_path}: {e}")

    client_id = data.get("clientId")
    access_token = data.get("accessToken")

    if not client_id or not access_token:
        raise FleetRollError(
            f"Missing clientId or accessToken in {cred_path}. File must contain both fields."
        )

    return TaskClusterCredentials(client_id=client_id, access_token=access_token)


def create_tc_clients(credentials: TaskClusterCredentials) -> TaskClusterClients:
    """Create authenticated Queue and Worker Manager REST clients."""
    options = {
        "rootUrl": "https://firefox-ci-tc.services.mozilla.com",
        "credentials": {
            "clientId": credentials.client_id,
            "accessToken": credentials.access_token,
        },
    }
    return TaskClusterClients(
        queue=taskcluster.Queue(options, session=_TimeoutSession()),
        worker_manager=taskcluster.WorkerManager(options, session=_TimeoutSession()),
    )


def _find_run(status_response: dict[str, Any], run_id: int) -> dict[str, Any] | None:
    """Extract one run from a Queue status response."""
    status = status_response.get("status") or {}
    for run in status.get("runs") or []:
        if run.get("runId") == run_id:
            return {
                "started": run.get("started"),
                "resolved": run.get("resolved"),
                "state": run.get("state"),
            }
    return None


def _fetch_task_run(queue: Any, task_key: tuple[str, int]) -> dict[str, Any]:
    """Fetch one latest task run through the Queue REST API."""
    task_id, run_id = task_key
    run = _find_run(queue.status(task_id), run_id)
    if run is None:
        raise FleetRollError(f"Task {task_id} has no run {run_id}")
    return run


def fetch_workers(
    provisioner: str,
    *,
    worker_type: str,
    clients: TaskClusterClients,
    worker_ids: set[str],
    cached_task_runs: dict[tuple[str, int], dict[str, Any]] | None = None,
    verbose: bool = False,
) -> WorkerFetchResult:
    """Fetch requested workers and their latest task runs through REST.

    Args:
        provisioner: The provisioner ID (e.g., "releng-hardware")
        worker_type: The worker type (e.g., "gecko-t-linux-talos-1804")
        clients: Authenticated Taskcluster REST clients
        worker_ids: Short worker IDs requested by the caller
        cached_task_runs: Previously resolved runs keyed by ``(taskId, runId)``
        verbose: Print REST pagination and task-status diagnostics

    Returns:
        Requested worker records and the count of non-fatal status failures

    Raises:
        FleetRollError: If the worker-list request fails
    """
    workers: list[dict[str, Any]] = []
    query: dict[str, Any] = {"limit": 1000}
    try:
        while True:
            response = clients.worker_manager.listWorkers(
                provisioner,
                worker_type,
                query=query,
            )
            page_workers = response.get("workers") or []
            workers.extend(
                dict(worker) for worker in page_workers if worker.get("workerId") in worker_ids
            )
            continuation_token = response.get("continuationToken")
            if verbose:
                print(
                    f"\n[DEBUG] REST worker page: {len(page_workers)} worker(s), "
                    f"continuation={bool(continuation_token)}"
                )
            if not continuation_token:
                break
            query = {"limit": 1000, "continuationToken": continuation_token}
    except Exception as e:
        raise FleetRollError(
            f"Failed to list workers through REST for {provisioner}/{worker_type}: {e}"
        ) from e

    cached_task_runs = cached_task_runs or {}
    task_keys: set[tuple[str, int]] = set()
    for worker in workers:
        latest_task = worker.get("latestTask") or {}
        task_id = latest_task.get("taskId")
        run_id = latest_task.get("runId")
        if isinstance(task_id, str) and isinstance(run_id, int):
            task_keys.add((task_id, run_id))

    runs_by_key = {key: cached_task_runs[key] for key in task_keys if key in cached_task_runs}
    uncached_keys = task_keys - runs_by_key.keys()
    status_errors = 0
    if uncached_keys:
        with ThreadPoolExecutor(
            max_workers=min(TASK_STATUS_WORKERS, len(uncached_keys))
        ) as executor:
            future_to_key = {
                executor.submit(_fetch_task_run, clients.queue, key): key for key in uncached_keys
            }
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    runs_by_key[key] = future.result()
                except Exception as e:
                    status_errors += 1
                    log = logger.warning if verbose else logger.debug
                    log("Failed to fetch Taskcluster status for %s/%s: %s", *key, e)

    for worker in workers:
        latest_task = worker.get("latestTask")
        if not isinstance(latest_task, dict):
            continue
        task_id = latest_task.get("taskId")
        run_id = latest_task.get("runId")
        if isinstance(task_id, str) and isinstance(run_id, int):
            run = runs_by_key.get((task_id, run_id))
            if run is not None:
                latest_task = dict(latest_task)
                latest_task["run"] = run
                worker["latestTask"] = latest_task

    return WorkerFetchResult(workers=workers, status_errors=status_errors)


def fetch_worker_type_names(
    provisioner: str,
    _credentials: TaskClusterCredentials,
) -> list[str]:
    """Fetch all worker type names for a provisioner via the TC queue REST API.

    Args:
        provisioner: The provisioner ID (e.g., "releng-hardware")
        _credentials: TaskCluster credentials (reserved for future auth use)

    Returns:
        List of worker type name strings

    Raises:
        FleetRollError: If the API request fails
    """
    base_url = "https://firefox-ci-tc.services.mozilla.com"
    url = f"{base_url}/api/queue/v1/provisioners/{provisioner}/worker-types"
    headers = {"Accept": "application/json"}

    worker_types = []
    continuation_token = None

    try:
        while True:
            params: dict[str, str] = {}
            if continuation_token:
                params["continuationToken"] = continuation_token

            response = requests.get(url, headers=headers, params=params, timeout=30)

            if response.status_code != 200:
                raise FleetRollError(
                    f"TC queue API returned {response.status_code} for {url}: {response.text}"
                )

            data = response.json()
            for entry in data.get("workerTypes", []):
                name = entry.get("workerType")
                if name:
                    worker_types.append(name)

            continuation_token = data.get("continuationToken")
            if not continuation_token:
                break

        return worker_types

    except requests.exceptions.RequestException as e:
        raise FleetRollError(f"Failed to fetch worker types from TC queue API: {e}")
