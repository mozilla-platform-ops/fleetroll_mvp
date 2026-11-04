"""Reproduce compact-history storage/latency measurements on synthetic hosts.

Run with uv run python scripts/benchmark_configuration_history.py.
Only temporary databases are written; no managed hosts or user DB are accessed.
"""

# ruff: noqa: INP001

from __future__ import annotations

import argparse
import datetime as dt
import json
import tempfile
import time
from pathlib import Path

from fleetroll.configuration_history import cleanup, read_evidence
from fleetroll.db import compact_database, get_connection, init_db, insert_host_observation
from fleetroll.rollout_history import build_history
from fleetroll.rollout_report import load_evidence


def record(host, *, stamp, applied, revision, failed):
    return {
        "host": host,
        "ts": stamp.isoformat(),
        "action": "host.audit",
        "ok": not failed,
        "observed": {
            "role": "gecko_t_linux_2404_talos",
            "puppet_role": "gecko_t_linux_2404_talos",
            "override_present": False,
            "override_sha256": None,
            "puppet_override_sha_applied": None,
            "vault_present": False,
            "puppet_success": True,
            "puppet_git_dirty": False,
            "puppet_git_repo": "https://github.com/mozilla-platform-ops/ronin_puppet.git",
            "puppet_git_branch": "master",
            "puppet_git_sha": revision,
            "puppet_state_ts": applied.isoformat(),
            "tc_worker_identity_status": "ok",
            "tc_worker_identity": {
                "provisionerId": "releng-hardware",
                "workerType": "test-pool",
                "workerGroup": "test-group",
                "workerId": host.split(".")[0],
            },
            # Approximate 1.4 KB normal raw records, deliberately not compacted.
            "output": "synthetic non-configuration data " * 18,
        },
    }


def measure(path, *, hosts, scans, changing):
    init_db(path)
    conn = get_connection(path)
    until = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    since = until - dt.timedelta(minutes=3 * (scans - 1))
    checkpoints = []
    raw_bytes = 0
    start = time.perf_counter()
    for scan in range(scans):
        stamp = since + dt.timedelta(minutes=scan * 3)
        # Four-hour revisions and an hourly 12-minute collection outage.
        revision = f"test_revision_{scan // 80 if changing else 0}"
        failed = changing and 12 <= scan % 20 < 16
        for index in range(hosts):
            item = record(
                f"host-{index:04d}.example.com",
                stamp=stamp,
                applied=stamp,  # repeated successful Puppet runs do not create states
                revision=revision,
                failed=failed,
            )
            raw_bytes += len(json.dumps(item))
            insert_host_observation(conn, item)
            conn.commit()  # matches process_audit_result, not a single giant transaction
        if scan in {0, 59, scans - 1}:
            checkpoints.append(
                {
                    "scan": scan + 1,
                    "spans": conn.execute("SELECT count(*) FROM configuration_spans").fetchone()[0],
                    "descriptors": conn.execute(
                        "SELECT count(*) FROM configuration_states"
                    ).fetchone()[0],
                }
            )
    write_s = time.perf_counter() - start
    start = time.perf_counter()
    compact, _ = read_evidence(conn, until=until)
    compact_read_s = time.perf_counter() - start
    start = time.perf_counter()
    evidence, refs, _ = load_evidence(path, audit_log=path.parent / "audit.jsonl", until=until)
    interval_count = sum(
        len(
            build_history(
                entries,
                overrides_dir=path.parent / "overrides",
                master_refs=refs,
                max_gap=dt.timedelta(hours=1),
            )
        )
        for entries in evidence.values()
    )
    report_s = time.perf_counter() - start
    counts = {
        table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
        for table in [
            "host_observations",
            "configuration_spans",
            "configuration_states",
            "configuration_boundaries",
        ]
    }
    # SQLite dbstat reports payload + indexes separately from logical JSON sizes.
    pages = dict(conn.execute("SELECT name,sum(pgsize) FROM dbstat GROUP BY name"))
    logical_bytes = conn.execute(
        "SELECT sum(length(data)+coalesce(length(proof),0)) FROM configuration_states"
    ).fetchone()[0]
    indexes = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name LIKE 'configuration_%'"
        )
    ]
    start = time.perf_counter()
    cleanup(conn)
    conn.commit()
    cleanup_s = time.perf_counter() - start
    start = time.perf_counter()
    cleanup(conn, now=until + dt.timedelta(days=31))
    conn.commit()
    expiry_s = time.perf_counter() - start
    expired_rows = conn.execute("SELECT count(*) FROM configuration_spans").fetchone()[0]
    conn.close()
    start = time.perf_counter()
    before, after = compact_database(path)
    vacuum_s = time.perf_counter() - start
    return {
        "workload": "changing/outage" if changing else "stable",
        "hosts": hosts,
        "scans": scans,
        "observations": hosts * scans,
        "simulated_hours": (scans - 1) / 20,
        "raw_json_bytes_processed": raw_bytes,
        "write_s": round(write_s, 3),
        "ms_per_observation": round(write_s * 1000 / (hosts * scans), 3),
        "checkpoints": checkpoints,
        "rows": counts,
        "logical_descriptor_bytes": logical_bytes,
        "sqlite_allocated_bytes": dict(pages),
        "indexes": indexes,
        "compact_records_read": len(compact),
        "compact_read_s": round(compact_read_s, 3),
        "report_evidence_and_intervals_s": round(report_s, 3),
        "intervals": interval_count,
        "cleanup_s": round(cleanup_s, 3),
        "expiry_s": round(expiry_s, 3),
        "spans_after_expiry": expired_rows,
        "vacuum_s": round(vacuum_s, 3),
        "physical_bytes_before_after_vacuum": [before, after],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hosts", type=int, default=930)
    parser.add_argument("--scans", type=int, default=480)
    args = parser.parse_args()
    if args.hosts <= 0 or not 1 <= args.scans <= 14400:
        parser.error("hosts must be positive; scans must be 1..14400 (30 days)")
    with tempfile.TemporaryDirectory(prefix="fleetroll-history-benchmark-") as directory:
        for changing in [False, True]:
            path = Path(directory) / ("changing.db" if changing else "stable.db")
            print(
                json.dumps(measure(path, hosts=args.hosts, scans=args.scans, changing=changing)),
                flush=True,
            )


if __name__ == "__main__":
    main()
