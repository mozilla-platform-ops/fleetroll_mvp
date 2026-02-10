#!/usr/bin/env python3
"""Benchmark SQLite vs JSONL storage approaches.

Compares performance metrics between SQLite (current) and JSONL (previous)
approaches for host_observations, tc_workers, and github_refs storage.

Metrics measured:
- Read latency (bulk and single-host queries)
- Write latency (single and batch inserts)
- Startup time (init + first bulk read)
- Memory usage (peak during bulk reads)

Usage:
    uv run python tools/bench_sqlite_vs_jsonl.py
"""

from __future__ import annotations

import json
import statistics
import tempfile
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fleetroll.db import (
    get_connection,
    get_latest_host_observations,
    init_db,
    insert_host_observation,
)


@dataclass
class BenchmarkResults:
    """Container for benchmark timing results."""

    approach: str
    write_single_ms: float
    write_batch_ms: float
    read_bulk_ms: float
    read_single_ms: float
    startup_ms: float
    memory_mb: float


def generate_host_observation(host: str, iteration: int) -> dict[str, Any]:
    """Generate realistic host_observation record (~800 bytes JSON)."""
    return {
        "host": host,
        "ts": f"2024-01-01T{iteration // 3600:02d}:{(iteration // 60) % 60:02d}:{iteration % 60:02d}+00:00",
        "ok": 1 if iteration % 3 != 0 else 0,  # 2/3 ok, 1/3 failures
        "observed": {
            "role": f"gecko_t_linux_talos_{host.split('.')[0][-3:]}",
            "override_sha256": f"override_sha_{iteration:08d}_{'x' * 48}",
            "vault_sha256": f"vault_sha_{iteration:08d}_{'y' * 51}",
            "git_sha": f"git_commit_sha_{iteration:08d}_{'z' * 32}",
            "git_branch": "main",
            "git_repo": "https://github.com/mozilla-platform-ops/ronin_puppet.git",
            "puppet_exit_code": 0 if iteration % 3 != 0 else 1,
            "puppet_duration_s": 100 + (iteration % 50),
            "metadata": {
                "os": "Linux",
                "version": "Ubuntu 22.04",
                "puppet_version": "7.24.0",
            },
        },
    }


def generate_test_hosts(count: int) -> list[str]:
    """Generate list of realistic hostnames."""
    return [f"t-linux-{i:04d}.test.releng.mdc2.mozilla.com" for i in range(1, count + 1)]


# =============================================================================
# JSONL Implementation (mirrors old approach)
# =============================================================================


class JSONLStorage:
    """Minimal JSONL storage implementation matching old approach."""

    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.host_obs_file = storage_dir / "host_observations.jsonl"

    def write_host_observation(self, record: dict[str, Any]) -> None:
        """Append record to JSONL file."""
        with open(self.host_obs_file, "a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")

    def read_latest_host_observations(
        self, hosts: list[str]
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """Read all lines, filter by hosts, return latest per host.

        Mimics the old approach: full file scan, parse all lines,
        filter to requested hosts, keep latest per host.
        """
        if not self.host_obs_file.exists():
            return {}, {}

        host_set = set(hosts)
        latest: dict[str, dict[str, Any]] = {}
        latest_ok: dict[str, dict[str, Any]] = {}

        # Full scan through all lines (old approach)
        with open(self.host_obs_file) as f:
            for line in f:
                record = json.loads(line.strip())
                host = record["host"]

                if host not in host_set:
                    continue

                # Keep latest per host (timestamp comparison)
                if host not in latest or record["ts"] > latest[host]["ts"]:
                    latest[host] = record

                # Track latest ok=1
                if record.get("ok") and (
                    host not in latest_ok or record["ts"] > latest_ok[host]["ts"]
                ):
                    latest_ok[host] = record

        return latest, latest_ok


# =============================================================================
# Benchmark Functions
# =============================================================================


def benchmark_jsonl_writes(
    storage: JSONLStorage,
    records: list[dict[str, Any]],
    *,
    iterations: int = 5,
) -> tuple[float, float]:
    """Benchmark JSONL write performance.

    Returns:
        Tuple of (single_write_ms, batch_write_ms)
    """
    # Single write benchmark
    single_times = []
    for _ in range(iterations):
        record = records[0]
        start = time.perf_counter()
        storage.write_host_observation(record)
        elapsed = time.perf_counter() - start
        single_times.append(elapsed * 1000)

    # Batch write benchmark (write all records sequentially)
    batch_times = []
    for _ in range(iterations):
        # Clear file for clean measurement
        storage.host_obs_file.unlink(missing_ok=True)
        start = time.perf_counter()
        for record in records:
            storage.write_host_observation(record)
        elapsed = time.perf_counter() - start
        batch_times.append(elapsed * 1000)

    return statistics.median(single_times), statistics.median(batch_times)


def benchmark_jsonl_reads(
    storage: JSONLStorage,
    all_hosts: list[str],
    *,
    iterations: int = 5,
) -> tuple[float, float]:
    """Benchmark JSONL read performance.

    Returns:
        Tuple of (bulk_read_ms, single_read_ms)
    """
    # Bulk read benchmark (all hosts)
    bulk_times = []
    for _ in range(iterations):
        start = time.perf_counter()
        storage.read_latest_host_observations(all_hosts)
        elapsed = time.perf_counter() - start
        bulk_times.append(elapsed * 1000)

    # Single host read benchmark
    single_times = []
    single_host = [all_hosts[0]]
    for _ in range(iterations):
        start = time.perf_counter()
        storage.read_latest_host_observations(single_host)
        elapsed = time.perf_counter() - start
        single_times.append(elapsed * 1000)

    return statistics.median(bulk_times), statistics.median(single_times)


def benchmark_jsonl_startup(
    storage_dir: Path,
    all_hosts: list[str],
    *,
    iterations: int = 3,
) -> float:
    """Benchmark JSONL startup time (init + first bulk read).

    Returns:
        Median startup time in milliseconds
    """
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        storage = JSONLStorage(storage_dir)
        storage.read_latest_host_observations(all_hosts)
        elapsed = time.perf_counter() - start
        times.append(elapsed * 1000)

    return statistics.median(times)


def benchmark_jsonl_memory(storage: JSONLStorage, all_hosts: list[str]) -> float:
    """Measure peak memory during bulk read.

    Returns:
        Peak memory usage in MB
    """
    tracemalloc.start()
    storage.read_latest_host_observations(all_hosts)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak / (1024 * 1024)


def benchmark_sqlite_writes(
    db_path: Path,
    records: list[dict[str, Any]],
    *,
    iterations: int = 5,
) -> tuple[float, float]:
    """Benchmark SQLite write performance.

    Returns:
        Tuple of (single_write_ms, batch_write_ms)
    """
    # Single write benchmark
    single_times = []
    for _ in range(iterations):
        conn = get_connection(db_path)
        try:
            record = records[0]
            start = time.perf_counter()
            insert_host_observation(conn, record)
            conn.commit()
            elapsed = time.perf_counter() - start
            single_times.append(elapsed * 1000)
        finally:
            conn.close()

    # Batch write benchmark (all records in one transaction)
    batch_times = []
    for _ in range(iterations):
        # Clear table for clean measurement
        conn = get_connection(db_path)
        try:
            conn.execute("DELETE FROM host_observations")
            conn.commit()
        finally:
            conn.close()

        conn = get_connection(db_path)
        try:
            start = time.perf_counter()
            for record in records:
                insert_host_observation(conn, record)
            conn.commit()
            elapsed = time.perf_counter() - start
            batch_times.append(elapsed * 1000)
        finally:
            conn.close()

    return statistics.median(single_times), statistics.median(batch_times)


def benchmark_sqlite_reads(
    db_path: Path,
    all_hosts: list[str],
    *,
    iterations: int = 5,
) -> tuple[float, float]:
    """Benchmark SQLite read performance.

    Returns:
        Tuple of (bulk_read_ms, single_read_ms)
    """
    conn = get_connection(db_path)
    try:
        # Bulk read benchmark (all hosts)
        bulk_times = []
        for _ in range(iterations):
            start = time.perf_counter()
            get_latest_host_observations(conn, all_hosts)
            elapsed = time.perf_counter() - start
            bulk_times.append(elapsed * 1000)

        # Single host read benchmark
        single_times = []
        single_host = [all_hosts[0]]
        for _ in range(iterations):
            start = time.perf_counter()
            get_latest_host_observations(conn, single_host)
            elapsed = time.perf_counter() - start
            single_times.append(elapsed * 1000)

        return statistics.median(bulk_times), statistics.median(single_times)
    finally:
        conn.close()


def benchmark_sqlite_startup(
    db_path: Path,
    all_hosts: list[str],
    *,
    iterations: int = 3,
) -> float:
    """Benchmark SQLite startup time (init + connection + first bulk read).

    Returns:
        Median startup time in milliseconds
    """
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        init_db(db_path)
        conn = get_connection(db_path)
        try:
            get_latest_host_observations(conn, all_hosts)
        finally:
            conn.close()
        elapsed = time.perf_counter() - start
        times.append(elapsed * 1000)

    return statistics.median(times)


def benchmark_sqlite_memory(db_path: Path, all_hosts: list[str]) -> float:
    """Measure peak memory during bulk read.

    Returns:
        Peak memory usage in MB
    """
    conn = get_connection(db_path)
    try:
        tracemalloc.start()
        get_latest_host_observations(conn, all_hosts)
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return peak / (1024 * 1024)
    finally:
        conn.close()


# =============================================================================
# Main Benchmark Runner
# =============================================================================


def run_benchmarks(
    *,
    num_hosts: int = 679,
    records_per_host: int = 10,
) -> tuple[BenchmarkResults, BenchmarkResults]:
    """Run all benchmarks for both JSONL and SQLite.

    Args:
        num_hosts: Number of hosts to simulate (default: 679, full fleet scale)
        records_per_host: Number of records per host (default: 10, matches retention)

    Returns:
        Tuple of (jsonl_results, sqlite_results)
    """
    print(f"Generating test data: {num_hosts} hosts x {records_per_host} records/host...")
    hosts = generate_test_hosts(num_hosts)
    all_records = [
        generate_host_observation(host, i) for host in hosts for i in range(records_per_host)
    ]

    total_records = len(all_records)
    sample_size = len(json.dumps(all_records[0]))
    print(f"  Total: {total_records} records (~{sample_size} bytes each)")
    print()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # =============================================================================
        # JSONL Benchmarks
        # =============================================================================
        print("Benchmarking JSONL approach...")

        jsonl_dir = tmpdir_path / "jsonl"
        jsonl_storage = JSONLStorage(jsonl_dir)

        # Populate with test data
        print("  Populating JSONL storage...")
        for record in all_records:
            jsonl_storage.write_host_observation(record)

        print("  Measuring writes...")
        jsonl_single_write, jsonl_batch_write = benchmark_jsonl_writes(
            jsonl_storage, all_records[:100]
        )

        print("  Measuring reads...")
        jsonl_bulk_read, jsonl_single_read = benchmark_jsonl_reads(jsonl_storage, hosts)

        print("  Measuring startup...")
        jsonl_startup = benchmark_jsonl_startup(jsonl_dir, hosts)

        print("  Measuring memory...")
        jsonl_memory = benchmark_jsonl_memory(jsonl_storage, hosts)

        jsonl_results = BenchmarkResults(
            approach="JSONL",
            write_single_ms=jsonl_single_write,
            write_batch_ms=jsonl_batch_write,
            read_bulk_ms=jsonl_bulk_read,
            read_single_ms=jsonl_single_read,
            startup_ms=jsonl_startup,
            memory_mb=jsonl_memory,
        )
        print("  ✓ JSONL benchmarks complete")
        print()

        # =============================================================================
        # SQLite Benchmarks
        # =============================================================================
        print("Benchmarking SQLite approach...")

        sqlite_db = tmpdir_path / "sqlite" / "test.db"
        sqlite_db.parent.mkdir(parents=True, exist_ok=True)
        init_db(sqlite_db)

        # Populate with test data
        print("  Populating SQLite storage...")
        conn = get_connection(sqlite_db)
        try:
            for record in all_records:
                insert_host_observation(conn, record)
            conn.commit()
        finally:
            conn.close()

        print("  Measuring writes...")
        sqlite_single_write, sqlite_batch_write = benchmark_sqlite_writes(
            sqlite_db, all_records[:100]
        )

        print("  Measuring reads...")
        sqlite_bulk_read, sqlite_single_read = benchmark_sqlite_reads(sqlite_db, hosts)

        print("  Measuring startup...")
        sqlite_startup = benchmark_sqlite_startup(sqlite_db, hosts)

        print("  Measuring memory...")
        sqlite_memory = benchmark_sqlite_memory(sqlite_db, hosts)

        sqlite_results = BenchmarkResults(
            approach="SQLite",
            write_single_ms=sqlite_single_write,
            write_batch_ms=sqlite_batch_write,
            read_bulk_ms=sqlite_bulk_read,
            read_single_ms=sqlite_single_read,
            startup_ms=sqlite_startup,
            memory_mb=sqlite_memory,
        )
        print("  ✓ SQLite benchmarks complete")
        print()

    return jsonl_results, sqlite_results


def print_results(jsonl: BenchmarkResults, sqlite: BenchmarkResults) -> None:
    """Print formatted comparison table."""

    def speedup(jsonl_val: float, sqlite_val: float) -> str:
        """Calculate and format speedup factor."""
        if jsonl_val == 0 or sqlite_val == 0:
            return "n/a"
        ratio = jsonl_val / sqlite_val
        if ratio > 1:
            return f"{ratio:.1f}x faster"
        return f"{1 / ratio:.1f}x slower"

    print("=" * 90)
    print("BENCHMARK RESULTS")
    print("=" * 90)
    print()
    print(f"{'Metric':<25} {'JSONL':<20} {'SQLite':<20} {'Difference':<25}")
    print("-" * 90)

    metrics = [
        ("Write (single)", "write_single_ms", "ms"),
        ("Write (batch 100)", "write_batch_ms", "ms"),
        ("Read (bulk, 679 hosts)", "read_bulk_ms", "ms"),
        ("Read (single host)", "read_single_ms", "ms"),
        ("Startup time", "startup_ms", "ms"),
        ("Memory (bulk read)", "memory_mb", "MB"),
    ]

    for label, attr, unit in metrics:
        jsonl_val = getattr(jsonl, attr)
        sqlite_val = getattr(sqlite, attr)
        diff = speedup(jsonl_val, sqlite_val)

        print(f"{label:<25} {jsonl_val:>8.2f} {unit:<11} {sqlite_val:>8.2f} {unit:<11} {diff:<25}")

    print("=" * 90)


def main() -> None:
    """Run benchmarks and print results."""
    print()
    print("SQLite vs JSONL Performance Benchmark")
    print("=" * 90)
    print()

    jsonl_results, sqlite_results = run_benchmarks()
    print_results(jsonl_results, sqlite_results)

    print()
    print("Notes:")
    print("  - All measurements use median of multiple iterations")
    print("  - JSONL: append writes, full-scan reads (mirrors old approach)")
    print("  - SQLite: indexed writes with retention, indexed reads")
    print("  - Test scale: 679 hosts x 10 records = 6,790 total records")
    print()


def benchmark_real_jsonl_file(jsonl_path: Path, sample_hosts: list[str]) -> None:
    """Benchmark read performance against real JSONL file.

    Args:
        jsonl_path: Path to actual JSONL file
        sample_hosts: List of hosts to query
    """
    if not jsonl_path.exists():
        print(f"File not found: {jsonl_path}")
        return

    # Get file stats
    file_size_mb = jsonl_path.stat().st_size / (1024 * 1024)
    with open(jsonl_path) as f:
        num_lines = sum(1 for _ in f)

    print("\nReal-world JSONL Performance Test")
    print("=" * 90)
    print(f"File: {jsonl_path.name}")
    print(f"Size: {file_size_mb:.1f} MB")
    print(f"Records: {num_lines:,}")
    print()

    # Create temporary JSONL storage pointing to real file
    storage = JSONLStorage(jsonl_path.parent)
    storage.host_obs_file = jsonl_path

    # Benchmark bulk read (all hosts)
    print("Benchmarking bulk read (all hosts)...")
    bulk_times = []
    for i in range(3):
        start = time.perf_counter()
        latest, latest_ok = storage.read_latest_host_observations(sample_hosts)
        elapsed = time.perf_counter() - start
        bulk_times.append(elapsed * 1000)
        print(f"  Run {i + 1}: {elapsed * 1000:.1f} ms (found {len(latest)} hosts)")

    median_bulk = statistics.median(bulk_times)

    # Benchmark single host read
    print("\nBenchmarking single-host read...")
    single_times = []
    single_host = [sample_hosts[0]] if sample_hosts else ["nonexistent.host"]
    for i in range(5):
        start = time.perf_counter()
        storage.read_latest_host_observations(single_host)
        elapsed = time.perf_counter() - start
        single_times.append(elapsed * 1000)

    median_single = statistics.median(single_times)

    # Memory measurement
    print("\nMeasuring memory usage...")
    tracemalloc.start()
    storage.read_latest_host_observations(sample_hosts)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    memory_mb = peak / (1024 * 1024)

    print("\n" + "=" * 90)
    print("RESULTS (Real JSONL File)")
    print("=" * 90)
    print(f"Bulk read (all hosts):    {median_bulk:>8.1f} ms")
    print(f"Single host read:         {median_single:>8.1f} ms")
    print(f"Memory (bulk read):       {memory_mb:>8.1f} MB")
    print("=" * 90)
    print()
    print(f"At this scale ({num_lines:,} records, {file_size_mb:.1f} MB):")
    print(f"  - Every monitor update would scan all {num_lines:,} records")
    print(f"  - Single-host lookups require full file scan ({median_single:.1f} ms)")
    print(f"  - Memory footprint is {memory_mb:.1f} MB per bulk read")
    print()
    print("SQLite comparison (from synthetic benchmarks):")
    print("  - Single host read: ~0.02 ms (constant time, regardless of total records)")
    print("  - Memory: ~0.06 MB (constant, regardless of total records)")
    print(f"  - Speedup: ~{median_single / 0.02:.0f}x faster for single-host lookups")
    print()


if __name__ == "__main__":
    import sys

    # Check for --real flag to benchmark against actual JSONL files
    if "--real" in sys.argv:
        print()
        print("SQLite vs JSONL Performance Benchmark (Real Data)")
        print("=" * 90)
        print()

        # Get list of hosts from current SQLite database for fair comparison
        from pathlib import Path

        db_path = Path.home() / ".fleetroll" / "fleetroll.db"
        if db_path.exists():
            conn = get_connection(db_path)
            try:
                # Get unique hosts from database
                rows = conn.execute(
                    "SELECT DISTINCT host FROM host_observations LIMIT 679"
                ).fetchall()
                hosts = [row[0] for row in rows]
                print(f"Using {len(hosts)} hosts from current database for queries\n")
            finally:
                conn.close()
        else:
            hosts = generate_test_hosts(679)
            print("Using synthetic host list (database not found)\n")

        # Benchmark against real JSONL files
        jsonl_path = Path.home() / ".fleetroll" / "host_observations.jsonl"
        if jsonl_path.exists():
            benchmark_real_jsonl_file(jsonl_path, hosts)

        # Also test against the largest historical file if it exists
        historical = Path.home() / ".fleetroll" / "host_observations.jsonl.20260209-210738"
        if historical.exists():
            print("\n" + "=" * 90)
            print("HISTORICAL PEAK (Before Migration)")
            print("=" * 90)
            benchmark_real_jsonl_file(historical, hosts)

    else:
        main()
        print()
        print("💡 TIP: Run with --real flag to benchmark against actual JSONL files:")
        print("   uv run python tools/bench_sqlite_vs_jsonl.py --real")
        print()
