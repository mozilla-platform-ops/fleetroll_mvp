"""Tests for fleetroll/commands/tc_fetch.py utility functions."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from fleetroll.commands.tc_fetch import (
    format_elapsed_time,
    format_tc_fetch_quiet,
    strip_fqdn,
    tc_workers_file_path,
    write_worker_record,
)


class TestFormatElapsedTime:
    """Tests for format_elapsed_time function."""

    def test_seconds_only(self):
        """Format time with only seconds."""
        assert format_elapsed_time(0) == "0s"
        assert format_elapsed_time(1) == "1s"
        assert format_elapsed_time(45) == "45s"
        assert format_elapsed_time(59) == "59s"

    def test_minutes_and_seconds(self):
        """Format time with minutes and seconds."""
        assert format_elapsed_time(60) == "1m00s"
        assert format_elapsed_time(61) == "1m01s"
        assert format_elapsed_time(125) == "2m05s"
        assert format_elapsed_time(599) == "9m59s"

    def test_hours_minutes_seconds(self):
        """Format time with hours, minutes, and seconds."""
        assert format_elapsed_time(3600) == "1h00m00s"
        assert format_elapsed_time(3661) == "1h01m01s"
        assert format_elapsed_time(7325) == "2h02m05s"
        assert format_elapsed_time(36000) == "10h00m00s"

    def test_float_seconds_truncated(self):
        """Float seconds should be truncated to int."""
        assert format_elapsed_time(45.7) == "45s"
        assert format_elapsed_time(125.9) == "2m05s"


class TestStripFqdn:
    """Tests for strip_fqdn function."""

    def test_fqdn_with_multiple_dots(self):
        """Strip fully qualified domain name."""
        assert strip_fqdn("t-linux64-ms-016.test.releng.mdc1.mozilla.com") == "t-linux64-ms-016"
        assert strip_fqdn("server.example.com") == "server"
        assert strip_fqdn("host.subdomain.domain.tld") == "host"

    def test_short_hostname(self):
        """Short hostname without dots returns as-is."""
        assert strip_fqdn("localhost") == "localhost"
        assert strip_fqdn("server") == "server"

    def test_hostname_with_single_dot(self):
        """Hostname with single dot strips domain."""
        assert strip_fqdn("host.com") == "host"


class TestTcWorkersFilePath:
    """Tests for tc_workers_file_path function."""

    def test_returns_path_object(self):
        """Should return a Path object."""
        result = tc_workers_file_path()
        assert isinstance(result, Path)

    def test_contains_expected_components(self):
        """Path should contain .fleetroll and filename."""
        result = tc_workers_file_path()
        assert ".fleetroll" in result.parts
        assert result.name == "taskcluster_workers.jsonl"

    def test_in_home_directory(self):
        """Path should be in user's home directory."""
        result = tc_workers_file_path()
        assert str(result).startswith(str(Path.home()))


class TestFormatTcFetchQuiet:
    """Tests for format_tc_fetch_quiet function."""

    def test_success_no_warnings(self):
        """Format output with no warnings shows success symbol."""
        result = format_tc_fetch_quiet(
            worker_count=5,
            scan_count=2,
            warnings=[],
            elapsed_seconds=30,
        )
        assert "✓" in result
        assert "5 worker(s)" in result
        assert "2 scan(s)" in result
        assert "30s" in result
        assert "timeout" not in result  # No warning text when warnings list is empty

    def test_with_warnings(self):
        """Format output with warnings shows warning symbol."""
        result = format_tc_fetch_quiet(
            worker_count=3,
            scan_count=1,
            warnings=["timeout", "api error"],
            elapsed_seconds=60,
        )
        assert "⚠" in result
        assert "3 worker(s)" in result
        assert "1 scan(s)" in result
        assert "timeout, api error" in result
        assert "1m00s" in result

    def test_single_warning(self):
        """Format output with single warning."""
        result = format_tc_fetch_quiet(
            worker_count=10,
            scan_count=5,
            warnings=["rate limit"],
            elapsed_seconds=45,
        )
        assert "⚠" in result
        assert "rate limit" in result

    def test_zero_counts(self):
        """Handle zero workers or scans."""
        result = format_tc_fetch_quiet(
            worker_count=0,
            scan_count=0,
            warnings=[],
            elapsed_seconds=5,
        )
        assert "✓" in result
        assert "0 worker(s)" in result
        assert "0 scan(s)" in result

    def test_singular_vs_plural(self):
        """Uses (s) suffix for both singular and plural."""
        result = format_tc_fetch_quiet(
            worker_count=1,
            scan_count=1,
            warnings=[],
            elapsed_seconds=10,
        )
        assert "1 worker(s)" in result
        assert "1 scan(s)" in result


class TestWriteWorkerRecord:
    """Tests for write_worker_record function."""

    def test_writes_json_record(self):
        """Should write a valid JSON record with all fields."""
        output = StringIO()
        write_worker_record(
            output,
            ts="2024-01-15T10:30:00Z",
            host="worker1.example.com",
            worker_id="i-12345",
            provisioner="aws-provisioner",
            worker_type="linux-compute",
            state="running",
            last_date_active="2024-01-15T10:25:00Z",
            task_started="2024-01-15T10:20:00Z",
            task_resolved="2024-01-15T10:28:00Z",
            quarantine_until=None,
        )

        output.seek(0)
        line = output.read()
        record = json.loads(line.strip())

        assert record["type"] == "worker"
        assert record["ts"] == "2024-01-15T10:30:00Z"
        assert record["host"] == "worker1.example.com"
        assert record["worker_id"] == "i-12345"
        assert record["provisioner"] == "aws-provisioner"
        assert record["worker_type"] == "linux-compute"
        assert record["state"] == "running"
        assert record["last_date_active"] == "2024-01-15T10:25:00Z"
        assert record["task_started"] == "2024-01-15T10:20:00Z"
        assert record["task_resolved"] == "2024-01-15T10:28:00Z"
        assert record["quarantine_until"] is None

    def test_handles_null_optional_fields(self):
        """Should handle None values for optional fields."""
        output = StringIO()
        write_worker_record(
            output,
            ts="2024-01-15T10:30:00Z",
            host="worker2.example.com",
            worker_id="i-67890",
            provisioner="gcp-provisioner",
            worker_type="windows-compute",
            state=None,
            last_date_active=None,
            task_started=None,
            task_resolved=None,
            quarantine_until=None,
        )

        output.seek(0)
        line = output.read()
        record = json.loads(line.strip())

        assert record["state"] is None
        assert record["last_date_active"] is None
        assert record["task_started"] is None
        assert record["task_resolved"] is None
        assert record["quarantine_until"] is None
