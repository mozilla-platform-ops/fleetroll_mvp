"""Tests for fleetroll/commands/tc_fetch.py utility functions."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from fleetroll.commands.tc_fetch import (
    build_role_to_hosts_mapping,
    format_tc_fetch_quiet,
    get_host_roles_bulk,
    map_roles_to_worker_types,
    match_workers_to_hosts,
    strip_fqdn,
    tc_workers_file_path,
    write_scan_record,
    write_worker_record,
)
from fleetroll.utils import format_elapsed_time


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
        assert "SUCCESS" in result
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
        assert "WARNING" in result
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
        assert "WARNING" in result
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
        assert "SUCCESS" in result
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
        assert "✓" in result
        assert "SUCCESS" in result
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


class TestWriteScanRecord:
    """Tests for write_scan_record function."""

    def test_writes_valid_json_record(self):
        """Should write a scan record with all fields."""
        output = StringIO()
        write_scan_record(
            output,
            ts="2024-01-15T10:30:00Z",
            provisioner="aws-provisioner",
            worker_type="linux-compute",
            worker_count=5,
            requested_by_hosts=["host1", "host2"],
        )

        output.seek(0)
        line = output.read()
        record = json.loads(line.strip())

        assert record["type"] == "scan"
        assert record["ts"] == "2024-01-15T10:30:00Z"
        assert record["provisioner"] == "aws-provisioner"
        assert record["worker_type"] == "linux-compute"
        assert record["worker_count"] == 5
        assert record["requested_by_hosts"] == ["host1", "host2"]

    def test_empty_hosts_list(self):
        """Should handle empty requested_by_hosts."""
        output = StringIO()
        write_scan_record(
            output,
            ts="2024-01-15T10:30:00Z",
            provisioner="aws",
            worker_type="test",
            worker_count=0,
            requested_by_hosts=[],
        )

        output.seek(0)
        record = json.loads(output.read().strip())
        assert record["requested_by_hosts"] == []
        assert record["worker_count"] == 0

    def test_multiple_hosts(self):
        """Should handle multiple hosts in requested_by_hosts."""
        output = StringIO()
        write_scan_record(
            output,
            ts="2024-01-15T10:30:00Z",
            provisioner="gcp-provisioner",
            worker_type="windows-compute",
            worker_count=10,
            requested_by_hosts=["host1", "host2", "host3", "host4"],
        )

        output.seek(0)
        record = json.loads(output.read().strip())
        assert len(record["requested_by_hosts"]) == 4
        assert "host1" in record["requested_by_hosts"]
        assert "host4" in record["requested_by_hosts"]


class TestGetHostRolesBulk:
    """Tests for get_host_roles_bulk function."""

    def test_finds_roles_for_hosts(self, tmp_path):
        """Should return roles for hosts from audit log."""
        audit_log = tmp_path / "audit.jsonl"
        audit_log.write_text(
            '{"host":"host1","ts":"2024-01-15T10:00:00Z","observed":{"role":"gecko_t_linux"}}\n'
            '{"host":"host2","ts":"2024-01-15T11:00:00Z","observed":{"role":"gecko_t_win"}}\n'
        )

        result = get_host_roles_bulk({"host1", "host2"}, audit_log)

        assert result["host1"] == "gecko_t_linux"
        assert result["host2"] == "gecko_t_win"

    def test_uses_most_recent_role(self, tmp_path):
        """Should use most recent role when multiple records exist."""
        audit_log = tmp_path / "audit.jsonl"
        audit_log.write_text(
            '{"host":"host1","ts":"2024-01-15T10:00:00Z","observed":{"role":"old_role"}}\n'
            '{"host":"host1","ts":"2024-01-15T12:00:00Z","observed":{"role":"new_role"}}\n'
        )

        result = get_host_roles_bulk({"host1"}, audit_log)
        assert result["host1"] == "new_role"

    def test_returns_none_for_missing_hosts(self, tmp_path):
        """Should return None for hosts not in audit log."""
        audit_log = tmp_path / "audit.jsonl"
        audit_log.write_text(
            '{"host":"host1","ts":"2024-01-15T10:00:00Z","observed":{"role":"some_role"}}\n'
        )

        result = get_host_roles_bulk({"host1", "host2", "host3"}, audit_log)
        assert result["host1"] == "some_role"
        assert result["host2"] is None
        assert result["host3"] is None

    def test_ignores_records_without_role(self, tmp_path):
        """Should ignore audit records without role data."""
        audit_log = tmp_path / "audit.jsonl"
        audit_log.write_text(
            '{"host":"host1","ts":"2024-01-15T10:00:00Z","observed":{}}\n'
            '{"host":"host1","ts":"2024-01-15T11:00:00Z","observed":{"role":"valid_role"}}\n'
        )

        result = get_host_roles_bulk({"host1"}, audit_log)
        assert result["host1"] == "valid_role"

    def test_empty_audit_log(self, tmp_path):
        """Should return all None for empty audit log."""
        audit_log = tmp_path / "audit.jsonl"
        audit_log.write_text("")

        result = get_host_roles_bulk({"host1", "host2"}, audit_log)
        assert result["host1"] is None
        assert result["host2"] is None

    def test_filters_by_requested_hosts(self, tmp_path):
        """Should only process requested hosts, not all hosts in log."""
        audit_log = tmp_path / "audit.jsonl"
        audit_log.write_text(
            '{"host":"host1","ts":"2024-01-15T10:00:00Z","observed":{"role":"role1"}}\n'
            '{"host":"host2","ts":"2024-01-15T10:00:00Z","observed":{"role":"role2"}}\n'
            '{"host":"host3","ts":"2024-01-15T10:00:00Z","observed":{"role":"role3"}}\n'
        )

        result = get_host_roles_bulk({"host1", "host3"}, audit_log)
        assert result["host1"] == "role1"
        assert result["host3"] == "role3"
        # host2 should not be in result or should be None
        assert "host2" not in result or result.get("host2") is None

    def test_missing_timestamp(self, tmp_path):
        """Should ignore records without timestamp."""
        audit_log = tmp_path / "audit.jsonl"
        audit_log.write_text(
            '{"host":"host1","observed":{"role":"role_no_ts"}}\n'
            '{"host":"host1","ts":"2024-01-15T11:00:00Z","observed":{"role":"role_with_ts"}}\n'
        )

        result = get_host_roles_bulk({"host1"}, audit_log)
        assert result["host1"] == "role_with_ts"

    def test_nonexistent_audit_log(self, tmp_path):
        """Should handle nonexistent audit log gracefully."""
        audit_log = tmp_path / "nonexistent.jsonl"

        result = get_host_roles_bulk({"host1", "host2"}, audit_log)
        assert result["host1"] is None
        assert result["host2"] is None


class TestBuildRoleToHostsMapping:
    """Tests for build_role_to_hosts_mapping function."""

    def test_builds_inverted_mapping(self):
        """Should invert host-to-role mapping."""
        host_to_role = {
            "host1": "gecko_t_linux",
            "host2": "gecko_t_linux",
            "host3": "gecko_t_win",
        }

        result = build_role_to_hosts_mapping(host_to_role)

        assert result["gecko_t_linux"] == ["host1", "host2"]
        assert result["gecko_t_win"] == ["host3"]

    def test_ignores_none_roles(self):
        """Should skip hosts with None roles."""
        host_to_role = {
            "host1": "gecko_t_linux",
            "host2": None,
            "host3": "gecko_t_win",
        }

        result = build_role_to_hosts_mapping(host_to_role)

        assert result["gecko_t_linux"] == ["host1"]
        assert result["gecko_t_win"] == ["host3"]
        assert None not in result

    def test_empty_input(self):
        """Should return empty dict for empty input."""
        result = build_role_to_hosts_mapping({})
        assert result == {}

    def test_all_none_roles(self):
        """Should return empty dict when all roles are None."""
        host_to_role = {
            "host1": None,
            "host2": None,
        }

        result = build_role_to_hosts_mapping(host_to_role)
        assert result == {}


class TestMapRolesToWorkerTypes:
    """Tests for map_roles_to_worker_types function."""

    def test_maps_roles_to_worker_types(self):
        """Should map roles to worker types using lookup table."""
        role_to_hosts = {
            "gecko_t_linux": ["host1", "host2"],
            "gecko_t_win": ["host3"],
        }
        role_lookup = {
            "gecko_t_linux": ("releng-hardware", "gecko-t-linux"),
            "gecko_t_win": ("releng-hardware", "gecko-t-win"),
        }

        role_to_worker_type, worker_type_to_hosts, unmapped = map_roles_to_worker_types(
            role_to_hosts, role_lookup
        )

        assert role_to_worker_type["gecko_t_linux"] == ("releng-hardware", "gecko-t-linux")
        assert role_to_worker_type["gecko_t_win"] == ("releng-hardware", "gecko-t-win")
        assert worker_type_to_hosts[("releng-hardware", "gecko-t-linux")] == ["host1", "host2"]
        assert worker_type_to_hosts[("releng-hardware", "gecko-t-win")] == ["host3"]
        assert unmapped == []

    def test_auto_converts_worker_type(self):
        """Should convert role name to worker type when AUTO_under_to_dash is specified."""
        role_to_hosts = {
            "gecko_t_linux_large": ["host1"],
        }
        role_lookup = {
            "gecko_t_linux_large": ("releng-hardware", "AUTO_under_to_dash"),
        }

        role_to_worker_type, worker_type_to_hosts, unmapped = map_roles_to_worker_types(
            role_to_hosts, role_lookup
        )

        assert role_to_worker_type["gecko_t_linux_large"] == (
            "releng-hardware",
            "gecko-t-linux-large",
        )
        assert worker_type_to_hosts[("releng-hardware", "gecko-t-linux-large")] == ["host1"]

    def test_tracks_unmapped_roles(self):
        """Should track roles not in lookup table."""
        role_to_hosts = {
            "gecko_t_linux": ["host1", "host2"],
            "unknown_role": ["host3"],
            "another_unknown": ["host4", "host5", "host6"],
        }
        role_lookup = {
            "gecko_t_linux": ("releng-hardware", "gecko-t-linux"),
        }

        role_to_worker_type, worker_type_to_hosts, unmapped = map_roles_to_worker_types(
            role_to_hosts, role_lookup
        )

        assert "unknown_role" not in role_to_worker_type
        assert "another_unknown" not in role_to_worker_type
        assert len(unmapped) == 2
        assert ("unknown_role", 1) in unmapped
        assert ("another_unknown", 3) in unmapped

    def test_empty_input(self):
        """Should handle empty input."""
        role_to_worker_type, worker_type_to_hosts, unmapped = map_roles_to_worker_types({}, {})

        assert role_to_worker_type == {}
        assert worker_type_to_hosts == {}
        assert unmapped == []

    def test_combines_hosts_for_same_worker_type(self):
        """Should combine hosts when multiple roles map to same worker type."""
        role_to_hosts = {
            "role_a": ["host1", "host2"],
            "role_b": ["host3"],
        }
        role_lookup = {
            "role_a": ("provisioner", "worker-type"),
            "role_b": ("provisioner", "worker-type"),
        }

        role_to_worker_type, worker_type_to_hosts, unmapped = map_roles_to_worker_types(
            role_to_hosts, role_lookup
        )

        assert worker_type_to_hosts[("provisioner", "worker-type")] == ["host1", "host2", "host3"]


class TestMatchWorkersToHosts:
    """Tests for match_workers_to_hosts function."""

    def test_matches_workers_to_hosts(self):
        """Should match worker data to hosts by short hostname."""
        hosts = ["host1.example.com", "host2.example.com"]
        host_to_role = {
            "host1.example.com": "gecko_t_linux",
            "host2.example.com": "gecko_t_linux",
        }
        role_to_worker_type = {
            "gecko_t_linux": ("releng-hardware", "gecko-t-linux"),
        }
        worker_type_to_workers = {
            ("releng-hardware", "gecko-t-linux"): {
                "host1": {
                    "workerId": "host1",
                    "state": "running",
                    "lastDateActive": "2024-01-15T10:00:00Z",
                    "quarantineUntil": None,
                    "latestTask": {
                        "run": {
                            "started": "2024-01-15T09:00:00Z",
                            "resolved": "2024-01-15T09:30:00Z",
                        }
                    },
                },
                "host2": {
                    "workerId": "host2",
                    "state": "stopped",
                    "lastDateActive": "2024-01-14T10:00:00Z",
                    "quarantineUntil": None,
                    "latestTask": None,
                },
            }
        }

        records = match_workers_to_hosts(
            hosts,
            host_to_role=host_to_role,
            role_to_worker_type=role_to_worker_type,
            worker_type_to_workers=worker_type_to_workers,
            ts="2024-01-15T12:00:00Z",
        )

        assert len(records) == 2

        # Check first record
        record1 = records[0]
        assert record1["type"] == "worker"
        assert record1["ts"] == "2024-01-15T12:00:00Z"
        assert record1["host"] == "host1.example.com"
        assert record1["worker_id"] == "host1"
        assert record1["provisioner"] == "releng-hardware"
        assert record1["worker_type"] == "gecko-t-linux"
        assert record1["state"] == "running"
        assert record1["last_date_active"] == "2024-01-15T10:00:00Z"
        assert record1["task_started"] == "2024-01-15T09:00:00Z"
        assert record1["task_resolved"] == "2024-01-15T09:30:00Z"
        assert record1["quarantine_until"] is None

        # Check second record
        record2 = records[1]
        assert record2["host"] == "host2.example.com"
        assert record2["state"] == "stopped"
        assert record2["task_started"] is None
        assert record2["task_resolved"] is None

    def test_skips_hosts_without_role(self):
        """Should skip hosts with no role."""
        hosts = ["host1.example.com", "host2.example.com"]
        host_to_role = {
            "host1.example.com": "gecko_t_linux",
            "host2.example.com": None,
        }
        role_to_worker_type = {
            "gecko_t_linux": ("releng-hardware", "gecko-t-linux"),
        }
        worker_type_to_workers = {
            ("releng-hardware", "gecko-t-linux"): {
                "host1": {"workerId": "host1", "state": "running"},
            }
        }

        records = match_workers_to_hosts(
            hosts,
            host_to_role=host_to_role,
            role_to_worker_type=role_to_worker_type,
            worker_type_to_workers=worker_type_to_workers,
            ts="2024-01-15T12:00:00Z",
        )

        assert len(records) == 1
        assert records[0]["host"] == "host1.example.com"

    def test_skips_hosts_with_unmapped_role(self):
        """Should skip hosts whose role is not in worker type mapping."""
        hosts = ["host1.example.com"]
        host_to_role = {
            "host1.example.com": "unknown_role",
        }
        role_to_worker_type = {}
        worker_type_to_workers = {}

        records = match_workers_to_hosts(
            hosts,
            host_to_role=host_to_role,
            role_to_worker_type=role_to_worker_type,
            worker_type_to_workers=worker_type_to_workers,
            ts="2024-01-15T12:00:00Z",
        )

        assert len(records) == 0

    def test_skips_hosts_without_worker_data(self):
        """Should skip hosts with no matching worker data."""
        hosts = ["host1.example.com", "host2.example.com"]
        host_to_role = {
            "host1.example.com": "gecko_t_linux",
            "host2.example.com": "gecko_t_linux",
        }
        role_to_worker_type = {
            "gecko_t_linux": ("releng-hardware", "gecko-t-linux"),
        }
        worker_type_to_workers = {
            ("releng-hardware", "gecko-t-linux"): {
                "host1": {"workerId": "host1", "state": "running"},
                # host2 not in worker data
            }
        }

        records = match_workers_to_hosts(
            hosts,
            host_to_role=host_to_role,
            role_to_worker_type=role_to_worker_type,
            worker_type_to_workers=worker_type_to_workers,
            ts="2024-01-15T12:00:00Z",
        )

        assert len(records) == 1
        assert records[0]["host"] == "host1.example.com"

    def test_handles_missing_optional_fields(self):
        """Should handle missing optional fields in worker data."""
        hosts = ["host1.example.com"]
        host_to_role = {
            "host1.example.com": "gecko_t_linux",
        }
        role_to_worker_type = {
            "gecko_t_linux": ("releng-hardware", "gecko-t-linux"),
        }
        worker_type_to_workers = {
            ("releng-hardware", "gecko-t-linux"): {
                "host1": {
                    "workerId": "host1",
                    # Missing most fields
                },
            }
        }

        records = match_workers_to_hosts(
            hosts,
            host_to_role=host_to_role,
            role_to_worker_type=role_to_worker_type,
            worker_type_to_workers=worker_type_to_workers,
            ts="2024-01-15T12:00:00Z",
        )

        assert len(records) == 1
        record = records[0]
        assert record["state"] is None
        assert record["last_date_active"] is None
        assert record["task_started"] is None
        assert record["task_resolved"] is None
        assert record["quarantine_until"] is None

    def test_empty_hosts_list(self):
        """Should return empty list for empty hosts input."""
        records = match_workers_to_hosts(
            [],
            host_to_role={},
            role_to_worker_type={},
            worker_type_to_workers={},
            ts="2024-01-15T12:00:00Z",
        )

        assert records == []
