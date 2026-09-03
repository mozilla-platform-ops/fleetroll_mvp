"""Tests for TaskCluster integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fleetroll.exceptions import FleetRollError
from fleetroll.taskcluster import (
    TaskClusterClients,
    fetch_workers,
    load_tc_credentials,
)


class TestLoadTCCredentials:
    """Tests for loading TaskCluster credentials."""

    def test_load_from_default_path(self, tmp_path: Path, monkeypatch):
        """Load credentials from default ~/.tc_token path."""
        cred_file = tmp_path / ".tc_token"
        cred_file.write_text(json.dumps({"clientId": "test-client", "accessToken": "test-token"}))
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TC_TOKEN", raising=False)

        creds = load_tc_credentials()
        assert creds.client_id == "test-client"
        assert creds.access_token == "test-token"

    def test_load_from_env_path(self, tmp_path: Path, monkeypatch):
        """Load credentials from TC_TOKEN environment variable path."""
        cred_file = tmp_path / "custom_token"
        cred_file.write_text(
            json.dumps({"clientId": "custom-client", "accessToken": "custom-token"})
        )
        monkeypatch.setenv("TC_TOKEN", str(cred_file))

        creds = load_tc_credentials()
        assert creds.client_id == "custom-client"
        assert creds.access_token == "custom-token"

    def test_missing_file(self, tmp_path: Path, monkeypatch):
        """Raise error when credentials file doesn't exist."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TC_TOKEN", raising=False)

        with pytest.raises(FleetRollError, match="TaskCluster credentials not found"):
            load_tc_credentials()

    def test_invalid_json(self, tmp_path: Path, monkeypatch):
        """Raise error when credentials file contains invalid JSON."""
        cred_file = tmp_path / ".tc_token"
        cred_file.write_text("not json")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TC_TOKEN", raising=False)

        with pytest.raises(FleetRollError, match="Invalid JSON"):
            load_tc_credentials()

    def test_missing_client_id(self, tmp_path: Path, monkeypatch):
        """Raise error when clientId is missing."""
        cred_file = tmp_path / ".tc_token"
        cred_file.write_text(json.dumps({"accessToken": "test-token"}))
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TC_TOKEN", raising=False)

        with pytest.raises(FleetRollError, match="Missing clientId or accessToken"):
            load_tc_credentials()

    def test_missing_access_token(self, tmp_path: Path, monkeypatch):
        """Raise error when accessToken is missing."""
        cred_file = tmp_path / ".tc_token"
        cred_file.write_text(json.dumps({"clientId": "test-client"}))
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TC_TOKEN", raising=False)

        with pytest.raises(FleetRollError, match="Missing clientId or accessToken"):
            load_tc_credentials()


class TestFetchWorkers:
    """Tests for fetching workers from TaskCluster API."""

    def test_fetch_workers_paginates_and_enriches_latest_task(self):
        """List workers through REST and enrich requested workers with Queue status."""
        worker_manager = MagicMock()
        worker_manager.listWorkers.side_effect = [
            {
                "workers": [
                    {
                        "workerId": "test-worker-01",
                        "workerGroup": "mdc1",
                        "state": "running",
                        "latestTask": {"taskId": "task_01", "runId": 0},
                    },
                    {"workerId": "unrequested-worker", "workerGroup": "mdc1"},
                ],
                "continuationToken": "next-page",
            },
            {
                "workers": [{"workerId": "test-worker-02", "workerGroup": "mdc1"}],
            },
        ]
        queue = MagicMock()
        queue.status.return_value = {
            "status": {
                "runs": [
                    {
                        "runId": 0,
                        "started": "2026-01-27T00:00:00Z",
                        "resolved": "2026-01-27T00:10:00Z",
                        "state": "completed",
                    }
                ]
            }
        }

        result = fetch_workers(
            "releng-hardware",
            worker_type="gecko-t-linux-talos-1804",
            clients=TaskClusterClients(queue=queue, worker_manager=worker_manager),
            worker_ids={"test-worker-01", "test-worker-02"},
        )

        assert [worker["workerId"] for worker in result.workers] == [
            "test-worker-01",
            "test-worker-02",
        ]
        assert result.workers[0]["latestTask"]["run"]["state"] == "completed"
        assert result.status_errors == 0
        assert worker_manager.listWorkers.call_args_list[1].kwargs["query"] == {
            "limit": 1000,
            "continuationToken": "next-page",
        }
        queue.status.assert_called_once_with("task_01")

    def test_fetch_workers_reuses_cached_resolved_task(self):
        """Avoid a Queue status request for an unchanged resolved task."""
        worker_manager = MagicMock()
        worker_manager.listWorkers.return_value = {
            "workers": [
                {
                    "workerId": "test-worker-01",
                    "workerGroup": "mdc1",
                    "latestTask": {"taskId": "task_cached", "runId": 1},
                }
            ]
        }
        queue = MagicMock()
        cached_run = {
            "started": "2026-01-27T00:00:00Z",
            "resolved": "2026-01-27T00:10:00Z",
            "state": "completed",
        }

        result = fetch_workers(
            "releng-hardware",
            worker_type="gecko-t-linux-talos-1804",
            clients=TaskClusterClients(queue=queue, worker_manager=worker_manager),
            worker_ids={"test-worker-01"},
            cached_task_runs={("task_cached", 1): cached_run},
        )

        assert result.workers[0]["latestTask"]["run"] == cached_run
        queue.status.assert_not_called()

    def test_fetch_workers_preserves_worker_when_status_fails(self):
        """A per-task error must not discard otherwise-fresh worker data."""
        worker_manager = MagicMock()
        worker_manager.listWorkers.return_value = {
            "workers": [
                {
                    "workerId": "test-worker-01",
                    "workerGroup": "mdc1",
                    "lastDateActive": "2026-01-27T00:00:00Z",
                    "latestTask": {"taskId": "task_missing", "runId": 0},
                }
            ]
        }
        queue = MagicMock()
        queue.status.side_effect = RuntimeError("task expired")

        result = fetch_workers(
            "releng-hardware",
            worker_type="gecko-t-linux-talos-1804",
            clients=TaskClusterClients(queue=queue, worker_manager=worker_manager),
            worker_ids={"test-worker-01"},
        )

        assert len(result.workers) == 1
        assert result.workers[0]["lastDateActive"] == "2026-01-27T00:00:00Z"
        assert "run" not in result.workers[0]["latestTask"]
        assert result.status_errors == 1

    def test_fetch_workers_api_error(self):
        """Raise an actionable error when the worker listing fails."""
        worker_manager = MagicMock()
        worker_manager.listWorkers.side_effect = RuntimeError("API Error")

        with pytest.raises(FleetRollError, match="Failed to list workers through REST"):
            fetch_workers(
                "releng-hardware",
                worker_type="gecko-t-linux-talos-1804",
                clients=TaskClusterClients(queue=MagicMock(), worker_manager=worker_manager),
                worker_ids={"test-worker-01"},
            )
