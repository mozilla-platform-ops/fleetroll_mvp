"""Taskcluster API client wrapper."""

import json
import taskcluster
from pathlib import Path
from typing import List, Dict, Any, Optional
import requests
import logging

logger = logging.getLogger(__name__)


class TaskclusterClient:
    """Wrapper for Taskcluster API operations."""

    def __init__(self, root_url: str, credentials_path: str = "~/.tc_token"):
        """
        Initialize Taskcluster client.

        Args:
            root_url: Taskcluster root URL (e.g., https://firefox-ci-tc.services.mozilla.com)
            credentials_path: Path to credentials JSON file
        """
        self.root_url = root_url
        self.queue_v1_base = f"{root_url}/api/queue/v1"
        self.graphql_url = f"{root_url}/graphql"
        self.credentials = self._load_credentials(credentials_path)
        self.queue = taskcluster.Queue(
            {
                "rootUrl": root_url,
                "credentials": self.credentials,
            }
        )

    def _load_credentials(self, credentials_path: str) -> Dict[str, str]:
        """Load Taskcluster credentials from file."""
        path = Path(credentials_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(
                f"Credentials file not found at {path}. "
                "Create ~/.tc_token with clientId and accessToken."
            )

        with open(path) as f:
            creds = json.load(f)

        if "clientId" not in creds or "accessToken" not in creds:
            raise ValueError(
                "Credentials file must contain 'clientId' and 'accessToken'"
            )

        return creds

    def get_quarantine_details_graphql(
        self,
        provisioner_id: str,
        worker_type: str,
        worker_group: str,
        worker_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get quarantine details for a worker using GraphQL API.

        Args:
            provisioner_id: Provisioner ID
            worker_type: Worker type
            worker_group: Worker group
            worker_id: Worker ID

        Returns:
            Quarantine details dict with updatedAt, clientId, quarantineUntil, quarantineInfo
            Returns None if worker not found or not quarantined
        """
        worker_pool_id = f"{provisioner_id}/{worker_type}"

        headers = {"content-type": "application/json"}

        payload = {
            "operationName": "ViewWorker",
            "variables": {
                "provisionerId": provisioner_id,
                "workerType": worker_type,
                "workerGroup": worker_group,
                "workerId": worker_id,
            },
            "query": """
                query ViewWorker($provisionerId: String!, $workerType: String!, $workerGroup: String!, $workerId: ID!) {
                  worker(
                    provisionerId: $provisionerId
                    workerType: $workerType
                    workerGroup: $workerGroup
                    workerId: $workerId
                  ) {
                    workerId
                    workerGroup
                    quarantineUntil
                    quarantineDetails {
                      updatedAt
                      clientId
                      quarantineUntil
                      quarantineInfo
                      __typename
                    }
                    __typename
                  }
                }
            """,
        }

        # Remove None values from variables
        variables = payload.get("variables")
        if isinstance(variables, dict):
            payload["variables"] = {k: v for k, v in variables.items() if v is not None}

        try:
            response = requests.post(self.graphql_url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

            if "data" in data and "worker" in data["data"]:
                worker_data = data["data"]["worker"]
                quarantine_details = worker_data.get("quarantineDetails")
                return quarantine_details
            return None
        except requests.exceptions.HTTPError as e:
            # Log the actual error response for debugging
            try:
                error_data = response.json()
                logger.warning(
                    f"Failed to fetch quarantine details for {worker_id}: {e}"
                )
                logger.debug(f"GraphQL error response: {error_data}")
            except:
                logger.warning(
                    f"Failed to fetch quarantine details for {worker_id}: {e}"
                )
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch quarantine details for {worker_id}: {e}")
            return None

    def list_quarantined_workers(
        self, provisioner_id: str, worker_type: str, fetch_details: bool = False
    ) -> List[Dict[str, Any]]:
        """
        List all quarantined workers for a worker type.

        Args:
            provisioner_id: Provisioner ID
            worker_type: Worker type
            fetch_details: If True, fetch detailed worker info including quarantine reason

        Returns:
            List of worker objects with workerId, workerGroup, quarantineDetails, etc.
        """
        workers = []
        continuation_token = None

        while True:
            query = {"quarantined": "true"}
            if continuation_token:
                query["continuationToken"] = continuation_token

            response = self.queue.listWorkers(provisioner_id, worker_type, query=query)

            workers.extend(response.get("workers", []))

            continuation_token = response.get("continuationToken")
            if not continuation_token:
                break

        # If fetch_details is True, get quarantine details via GraphQL
        if fetch_details:
            for worker in workers:
                worker_id = worker.get("workerId")
                worker_group = worker.get("workerGroup")

                # Fetch quarantine details via GraphQL
                quarantine_details_list = self.get_quarantine_details_graphql(
                    provisioner_id, worker_type, worker_group, worker_id
                )

                if quarantine_details_list and isinstance(
                    quarantine_details_list, list
                ):
                    # quarantineDetails is an array of history entries
                    # Get the most recent entry (last in the list)
                    if len(quarantine_details_list) > 0:
                        latest_details = quarantine_details_list[-1]
                        # Store the full quarantine details history
                        worker["quarantineDetailsHistory"] = quarantine_details_list
                        # Store the latest entry for convenience
                        worker["quarantineDetails"] = latest_details
                        # Extract the quarantineInfo (the reason) for easy access
                        worker["quarantineInfo"] = latest_details.get(
                            "quarantineInfo", ""
                        )
                    else:
                        worker["quarantineInfo"] = ""
                else:
                    worker["quarantineInfo"] = ""

        return workers

    def list_all_workers(
        self, provisioner_id: str, worker_type: str
    ) -> List[Dict[str, Any]]:
        """
        List all workers for a worker type.

        Returns:
            List of worker objects with workerId, workerGroup, etc.
        """
        workers = []
        continuation_token = None

        while True:
            query = {}
            if continuation_token:
                query["continuationToken"] = continuation_token

            response = self.queue.listWorkers(provisioner_id, worker_type, query=query)

            workers.extend(response.get("workers", []))

            continuation_token = response.get("continuationToken")
            if not continuation_token:
                break

        return workers

    def get_worker_group(
        self, provisioner_id: str, worker_type: str, worker_id: str
    ) -> str:
        """
        Determine worker group for a specific worker.

        Args:
            provisioner_id: Provisioner ID
            worker_type: Worker type
            worker_id: Worker ID to find

        Returns:
            Worker group string

        Raises:
            ValueError: If worker not found or multiple worker groups exist
        """
        # Try to find the worker in the list
        workers = self.list_all_workers(provisioner_id, worker_type)

        for worker in workers:
            if worker.get("workerId") == worker_id:
                worker_group = worker.get("workerGroup")
                if worker_group:
                    return worker_group
                raise ValueError(f"Worker {worker_id} found but has no workerGroup set")

        # Fallback: enumerate worker groups
        worker_groups = set()
        for worker in workers:
            wg = worker.get("workerGroup")
            if wg:
                worker_groups.add(wg)

        if len(worker_groups) == 1:
            return list(worker_groups)[0]
        elif len(worker_groups) == 0:
            raise ValueError(
                f"Cannot determine workerGroup for {worker_id}: no workers found"
            )
        else:
            raise ValueError(
                f"Cannot determine workerGroup for {worker_id}: "
                f"multiple groups exist: {worker_groups}"
            )

    def quarantine_worker(
        self,
        provisioner_id: str,
        worker_type: str,
        worker_group: str,
        worker_id: str,
        quarantine_message: str,
    ):
        """
        Quarantine a worker.

        Args:
            provisioner_id: Provisioner ID
            worker_type: Worker type
            worker_group: Worker group
            worker_id: Worker ID
            quarantine_message: Quarantine reason/message
        """
        payload = {
            "quarantineUntil": taskcluster.fromNow("10 years"),
            "quarantineInfo": quarantine_message,
        }
        self.queue.quarantineWorker(
            provisioner_id, worker_type, worker_group, worker_id, payload
        )

    def unquarantine_worker(
        self,
        provisioner_id: str,
        worker_type: str,
        worker_group: str,
        worker_id: str,
        reason: str = "unquarantined",
    ):
        """
        Unquarantine a worker by setting quarantine time in the past.

        Args:
            provisioner_id: Provisioner ID
            worker_type: Worker type
            worker_group: Worker group
            worker_id: Worker ID
            reason: Reason for unquarantining (default: "unquarantined")
        """
        payload = {
            "quarantineUntil": taskcluster.fromNow("-1 year"),
            "quarantineInfo": reason,
        }
        self.queue.quarantineWorker(
            provisioner_id, worker_type, worker_group, worker_id, payload
        )

    def get_worker_details(
        self,
        provisioner_id: str,
        worker_type: str,
        worker_group: str,
        worker_id: str,
    ) -> Dict[str, Any]:
        """
        Get worker details including recent tasks.

        Args:
            provisioner_id: Provisioner ID
            worker_type: Worker type
            worker_group: Worker group
            worker_id: Worker ID

        Returns:
            Worker details dict with 'recentTasks' array
        """
        url = (
            f"{self.queue_v1_base}/provisioners/{provisioner_id}"
            f"/worker-types/{worker_type}/workers/{worker_group}/{worker_id}"
        )

        response = requests.get(url)
        response.raise_for_status()
        return response.json()

    def get_pending_queue_count(
        self,
        provisioner_id: str,
        worker_type: str,
    ) -> int:
        """
        Get the number of pending tasks in the queue for a workerType.

        Args:
            provisioner_id: Provisioner ID
            worker_type: Worker type

        Returns:
            Number of pending tasks, or 0 if unable to fetch
        """
        url = f"{self.queue_v1_base}/pending/{provisioner_id}/{worker_type}"

        try:
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            return data.get("pendingTasks", 0)
        except Exception as e:
            logger.warning(f"Failed to fetch pending queue count: {e}")
            return 0

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Get task status.

        Args:
            task_id: Task ID

        Returns:
            Task status dict or None if not found
        """
        url = f"{self.queue_v1_base}/task/{task_id}/status"

        try:
            response = requests.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException:
            return None

    # similar to get_quarantine_details_graphql(), but for tasks
    def get_task_details_graphql(self, task_id: str) -> Optional[Dict[str, Any]]:
        # curl 'https://firefox-ci-tc.services.mozilla.com/graphql' \
        #   -X POST \
        #   -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:146.0) Gecko/20100101 Firefox/146.0' \
        #   -H 'Accept: */*' \
        #   -H 'Accept-Language: en-US,en;q=0.5' \
        #   -H 'Accept-Encoding: gzip, deflate, br, zstd' \
        #   -H 'Referer: https://firefox-ci-tc.services.mozilla.com/' \
        #   -H 'content-type: application/json' \
        #   -H 'Authorization: Bearer REDACTED_TC_TOKEN==' \
        #   -H 'Origin: https://firefox-ci-tc.services.mozilla.com' \
        #   -H 'DNT: 1' \
        #   -H 'Connection: keep-alive' \
        #   -H 'Cookie: connect.sid=s%3AtZGJzk4_CZiRu4LC3tgdNohnxcvCJTS9.6%2F8Al9cWTyaIficKhbAqCrtuGcZR%2FM3412gJAFkuoFw' \
        #   -H 'Sec-Fetch-Dest: empty' \
        #   -H 'Sec-Fetch-Mode: cors' \
        #   -H 'Sec-Fetch-Site: same-origin' \
        #   -H 'Priority: u=4' \
        #   --data-raw $'{"operationName":"Task","variables":{"taskId":"Gc-_ljbsSXSeniAX7DzS_g","artifactsConnection":{"limit":1000},"dependentsConnection":{"limit":25},"taskActionsFilter":{"kind":{"$in":["task","hook"]},"context":{"$not":{"$size":0}}}},"query":"query Task($taskId: ID\041, $artifactsConnection: PageConnection, $dependentsConnection: PageConnection, $taskActionsFilter: JSON) {\\n  task(taskId: $taskId) {\\n    taskId\\n    taskGroupId\\n    retries\\n    created\\n    deadline\\n    expires\\n    priority\\n    taskQueueId\\n    schedulerId\\n    projectId\\n    tags\\n    requires\\n    scopes\\n    routes\\n    payload\\n    extra\\n    dependencies\\n    metadata {\\n      name\\n      description\\n      owner\\n      source\\n      __typename\\n    }\\n    status {\\n      state\\n      retriesLeft\\n      runs {\\n        taskId\\n        runId\\n        state\\n        reasonCreated\\n        reasonResolved\\n        scheduled\\n        started\\n        resolved\\n        workerGroup\\n        workerId\\n        takenUntil\\n        artifacts(connection: $artifactsConnection) {\\n          ...Artifacts\\n          __typename\\n        }\\n        __typename\\n      }\\n      __typename\\n    }\\n    taskActions(filter: $taskActionsFilter) {\\n      actions\\n      variables\\n      version\\n      __typename\\n    }\\n    decisionTask {\\n      scopes\\n      __typename\\n    }\\n    __typename\\n  }\\n  dependents(taskId: $taskId, connection: $dependentsConnection) {\\n    pageInfo {\\n      hasNextPage\\n      hasPreviousPage\\n      cursor\\n      previousCursor\\n      nextCursor\\n      __typename\\n    }\\n    edges {\\n      node {\\n        taskId\\n        status {\\n          state\\n          __typename\\n        }\\n        metadata {\\n          name\\n          __typename\\n        }\\n        __typename\\n      }\\n      __typename\\n    }\\n    __typename\\n  }\\n}\\n\\nfragment Artifacts on ArtifactsConnection {\\n  pageInfo {\\n    hasNextPage\\n    hasPreviousPage\\n    cursor\\n    previousCursor\\n    nextCursor\\n    __typename\\n  }\\n  edges {\\n    node {\\n      name\\n      contentType\\n      __typename\\n    }\\n    __typename\\n  }\\n  __typename\\n}"}'
        pass

    def get_worker_quarantine_info(
        self,
        provisioner_id: str,
        worker_type: str,
        worker_id: str,
    ) -> tuple[bool, Optional[str]]:
        """
        Get current quarantine state and message for a worker.

        Args:
            provisioner_id: Provisioner ID
            worker_type: Worker type
            worker_id: Worker ID

        Returns:
            Tuple of (is_quarantined, quarantine_message)
        """
        # First, get worker_group
        try:
            worker_group = self.get_worker_group(provisioner_id, worker_type, worker_id)
        except ValueError as e:
            logger.debug(f"Worker {worker_id} not found: {e}")
            return False, None

        # Fetch detailed quarantine info using GraphQL
        quarantine_details = self.get_quarantine_details_graphql(
            provisioner_id, worker_type, worker_group, worker_id
        )

        if not quarantine_details:
            # No quarantine details means not quarantined
            return False, None

        # Extract quarantine message from details
        message = ""
        if isinstance(quarantine_details, list) and len(quarantine_details) > 0:
            # quarantineDetails is an array, get the most recent
            latest_details = quarantine_details[-1]
            message = latest_details.get("quarantineInfo", "")
        elif isinstance(quarantine_details, dict):
            # quarantineDetails is a single object
            message = quarantine_details.get("quarantineInfo", "")

        logger.debug(f"Worker {worker_id}: quarantine_message='{message}'")

        # Worker is quarantined if we got quarantine details
        return True, message
