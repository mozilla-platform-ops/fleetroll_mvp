"""TaskCluster API integration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests

from .exceptions import FleetRollError


class TaskClusterCredentials:
    """TaskCluster API credentials."""

    def __init__(self, client_id: str, access_token: str):
        self.client_id = client_id
        self.access_token = access_token


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


def fetch_workers(
    provisioner: str,
    worker_type: str,
    _credentials: TaskClusterCredentials,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Fetch workers for a given provisioner/workerType using GraphQL API.

    Args:
        provisioner: The provisioner ID (e.g., "releng-hardware")
        worker_type: The worker type (e.g., "gecko-t-linux-talos-1804")
        credentials: TaskCluster credentials

    Returns:
        List of worker dicts with workerId, state, quarantineUntil, latestTask, etc.

    Raises:
        FleetRollError: If the API request fails
    """
    graphql_url = "https://firefox-ci-tc.services.mozilla.com/graphql"

    # GraphQL query - simplified to only use required variables
    query = """query ViewWorkers($provisionerId: String!, $workerType: String!, $workersConnection: PageConnection) {
  workers(
    provisionerId: $provisionerId
    workerType: $workerType
    connection: $workersConnection
  ) {
    pageInfo {
      hasNextPage
      nextCursor
    }
    edges {
      node {
        workerId
        workerGroup
        latestTask {
          run {
            taskId
            runId
            started
            resolved
            state
          }
        }
        firstClaim
        quarantineUntil
        lastDateActive
        state
        capacity
        providerId
        workerPoolId
      }
    }
  }
}"""

    workers = []
    cursor = None

    try:
        while True:
            variables = {
                "provisionerId": provisioner,
                "workerType": worker_type,
                "workersConnection": {"limit": 1000},
            }

            if cursor:
                variables["workersConnection"]["cursor"] = cursor

            payload = {
                "operationName": "ViewWorkers",
                "variables": variables,
                "query": query,
            }

            headers = {
                "Content-Type": "application/json",
                "Accept": "*/*",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:147.0) Gecko/20100101 Firefox/147.0",
                "Origin": "https://firefox-ci-tc.services.mozilla.com",
                "Referer": "https://firefox-ci-tc.services.mozilla.com/",
            }

            if verbose:
                print("\n[DEBUG] GraphQL Request:")
                print(f"  URL: {graphql_url}")
                print(f"  Variables: {json.dumps(variables, indent=2)}")
                print(f"  Headers: {json.dumps(headers, indent=2)}")

            response = requests.post(graphql_url, json=payload, headers=headers, timeout=30)

            # Check for HTTP errors
            if response.status_code != 200:
                try:
                    error_data = response.json()
                    raise FleetRollError(
                        f"GraphQL API returned {response.status_code}: {error_data}"
                    )
                except ValueError:
                    raise FleetRollError(
                        f"GraphQL API returned {response.status_code}: {response.text}"
                    )

            data = response.json()

            # Check if we have any data at all
            if "data" not in data or not data["data"]:
                if "errors" in data:
                    raise FleetRollError(f"GraphQL errors: {data['errors']}")
                raise FleetRollError("No data returned from GraphQL API")

            # GraphQL can return partial data with errors (e.g., deleted tasks)
            # We'll use whatever data we got and log errors if verbose
            if "errors" in data:
                if verbose:
                    print(
                        f"\n[WARNING] GraphQL returned {len(data['errors'])} errors (using partial data):"
                    )
                    for error in data["errors"][:3]:  # Show first 3 errors
                        print(f"  - {error.get('message', 'Unknown error')}")
                    if len(data["errors"]) > 3:
                        print(f"  ... and {len(data['errors']) - 3} more errors")

            workers_data = data.get("data", {}).get("workers", {})
            edges = workers_data.get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                if node:  # Skip null nodes
                    workers.append(node)

            page_info = workers_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break

            cursor = page_info.get("nextCursor")
            if not cursor:
                break

        return workers

    except requests.exceptions.RequestException as e:
        raise FleetRollError(f"Failed to fetch workers from GraphQL API: {e}")
    except Exception as e:
        raise FleetRollError(f"Failed to fetch workers from TaskCluster API: {e}")
