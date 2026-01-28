"""Tests for TaskCluster integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fleetroll.exceptions import FleetRollError
from fleetroll.taskcluster import (
    TaskClusterCredentials,
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

    @patch("fleetroll.taskcluster.requests.post")
    def test_fetch_workers_success(self, mock_post):
        """Successfully fetch workers from GraphQL API."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "workers": {
                    "edges": [
                        {
                            "node": {
                                "workerId": "test-worker-01",
                                "workerGroup": "releng-hardware",
                                "state": "running",
                                "lastDateActive": "2026-01-27T00:00:00Z",
                                "quarantineUntil": None,
                                "latestTask": {
                                    "run": {
                                        "started": "2026-01-27T00:00:00Z",
                                        "resolved": "2026-01-27T00:10:00Z",
                                        "state": "completed",
                                    }
                                },
                            }
                        }
                    ],
                    "pageInfo": {"hasNextPage": False},
                }
            }
        }
        mock_post.return_value = mock_response

        creds = TaskClusterCredentials("test-client", "test-token")
        result = fetch_workers("releng-hardware", "gecko-t-linux-talos-1804", creds)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["workerId"] == "test-worker-01"
        assert result[0]["state"] == "running"
        assert result[0]["latestTask"]["run"]["started"] == "2026-01-27T00:00:00Z"

        # Verify GraphQL API was called correctly
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "https://firefox-ci-tc.services.mozilla.com/graphql"
        assert call_args[1]["headers"]["Content-Type"] == "application/json"
        assert "Authorization" not in call_args[1]["headers"]  # GraphQL endpoint is public
        assert call_args[1]["json"]["variables"]["provisionerId"] == "releng-hardware"
        assert call_args[1]["json"]["variables"]["workerType"] == "gecko-t-linux-talos-1804"

    @patch("fleetroll.taskcluster.requests.post")
    def test_fetch_workers_partial_data_with_errors(self, mock_post):
        """Handle partial data when some tasks are deleted."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "workers": {
                    "edges": [
                        {
                            "node": {
                                "workerId": "test-worker-01",
                                "state": "running",
                            }
                        },
                        {
                            "node": {
                                "workerId": "test-worker-02",
                                "state": "stopped",
                            }
                        },
                    ],
                    "pageInfo": {"hasNextPage": False},
                }
            },
            "errors": [
                {
                    "message": "Task does not exist",
                    "path": ["workers", "edges", 0, "node", "latestTask"],
                }
            ],
        }
        mock_post.return_value = mock_response

        creds = TaskClusterCredentials("test-client", "test-token")
        result = fetch_workers("releng-hardware", "gecko-t-linux-talos-1804", creds)

        # Should still return the workers despite errors
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["workerId"] == "test-worker-01"
        assert result[1]["workerId"] == "test-worker-02"

    @patch("fleetroll.taskcluster.requests.post")
    def test_fetch_workers_api_error(self, mock_post):
        """Raise error when API request fails."""
        import requests

        mock_post.side_effect = requests.exceptions.RequestException("API Error")

        creds = TaskClusterCredentials("test-client", "test-token")
        with pytest.raises(FleetRollError, match="Failed to fetch workers"):
            fetch_workers("releng-hardware", "gecko-t-linux-talos-1804", creds)
