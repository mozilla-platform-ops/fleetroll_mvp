"""Queue counts must preserve pool scope and distinguish unknown from zero."""

from unittest.mock import Mock, call

import pytest
from fleetroll.queue_counts import collect_queue_counts, render_queue_counts


def observation(pool, *, provisioner="releng-hardware", status="ok"):
    return {
        "host": "worker",
        "ok": True,
        "observed": {
            "tc_worker_identity_status": status,
            "tc_worker_identity": {
                "provisionerId": provisioner,
                "workerType": pool,
                "workerGroup": "group",
                "workerId": "worker",
            },
        },
    }


def test_each_pool_queried_once_with_zero_and_partial_failure(tmp_path):
    status = {
        "selector": {"worker_type": None},
        "groups": [{"hosts": ["a", "b", "c", "unknown"]}],
    }
    queue = Mock()
    queue.taskQueueCounts.side_effect = [{"pendingTasks": 0}, RuntimeError("offline")]
    counts = collect_queue_counts(
        status,
        observations={
            "a": observation("pool"),
            "b": observation("pool"),
            "c": observation("pool", provisioner="other"),
            "unknown": observation("pool", status="unreadable"),
        },
        overrides_dir=tmp_path,
        queue=queue,
    )
    assert queue.taskQueueCounts.call_args_list == [
        call("other/pool"),
        call("releng-hardware/pool"),
    ]
    assert counts["scope"] == "whole_pool"
    assert counts["unresolved_hosts"] == ["unknown"]
    first, second = counts["pools"]
    assert first["pending_tasks"] == 0
    assert first["error"] is None
    assert first["checked_at"]
    assert second["pending_tasks"] is None
    assert second["error"] == "offline"
    output = "\n".join(render_queue_counts(counts))
    assert "whole pool" in output
    assert "other/pool: 0 pending" in output
    assert "releng-hardware/pool: unavailable" in output


@pytest.mark.parametrize("value", [None, -1, True, "12", 1.5])
def test_malformed_count_is_unavailable(tmp_path, value):
    queue = Mock()
    queue.taskQueueCounts.return_value = {"pendingTasks": value}
    counts = collect_queue_counts(
        {"selector": {"worker_type": "prov/pool"}, "groups": []},
        observations={},
        overrides_dir=tmp_path,
        queue=queue,
    )
    assert counts["pools"][0]["pending_tasks"] is None
    assert counts["pools"][0]["error"]


def test_no_resolved_pool_does_not_query(tmp_path):
    queue = Mock()
    counts = collect_queue_counts(
        {"selector": {"worker_type": None}, "groups": []},
        observations={},
        overrides_dir=tmp_path,
        queue=queue,
    )
    queue.taskQueueCounts.assert_not_called()
    assert "no resolved worker pools" in "\n".join(render_queue_counts(counts))
