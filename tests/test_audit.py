"""Tests for fleetroll/audit.py - audit logging and file storage."""

from __future__ import annotations

import json
from pathlib import Path

from fleetroll.audit import append_jsonl, process_audit_result, store_override_file
from fleetroll.cli_types import HostAuditArgs
from fleetroll.constants import CONTENT_SENTINEL


class TestAppendJsonl:
    """Tests for append_jsonl function."""

    def test_creates_file_if_not_exists(self, tmp_dir: Path):
        """Creates new file if it doesn't exist."""
        path = tmp_dir / "test.jsonl"
        append_jsonl(path, {"key": "value"})
        assert path.exists()

    def test_creates_parent_dirs(self, tmp_dir: Path):
        """Creates parent directories if they don't exist."""
        path = tmp_dir / "a" / "b" / "test.jsonl"
        append_jsonl(path, {"key": "value"})
        assert path.exists()

    def test_appends_to_existing(self, tmp_dir: Path):
        """Appends to existing file without overwriting."""
        path = tmp_dir / "test.jsonl"
        append_jsonl(path, {"first": 1})
        append_jsonl(path, {"second": 2})
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_valid_json_format(self, tmp_dir: Path):
        """Each line is valid JSON."""
        path = tmp_dir / "test.jsonl"
        record = {"key": "value", "number": 42}
        append_jsonl(path, record)
        parsed = json.loads(path.read_text().strip())
        assert parsed["key"] == "value"
        assert parsed["number"] == 42

    def test_keys_sorted(self, tmp_dir: Path):
        """JSON keys are sorted for consistent output."""
        path = tmp_dir / "test.jsonl"
        append_jsonl(path, {"z": 1, "a": 2, "m": 3})
        line = path.read_text().strip()
        # Keys should appear in sorted order
        assert line.index('"a"') < line.index('"m"') < line.index('"z"')

    def test_newline_terminated(self, tmp_dir: Path):
        """Each record is newline-terminated."""
        path = tmp_dir / "test.jsonl"
        append_jsonl(path, {"key": "value"})
        content = path.read_text()
        assert content.endswith("\n")


class TestStoreOverrideFile:
    """Tests for store_override_file function."""

    def test_stores_new_file(self, tmp_dir: Path):
        """Stores content in new file named by SHA prefix."""
        content = "test content"
        sha = "a" * 64  # Fake SHA
        result = store_override_file(content, sha, tmp_dir)
        assert result.exists()
        assert result.read_text() == content

    def test_filename_is_sha_prefix(self, tmp_dir: Path):
        """Filename is SHA256 prefix (12 chars by default)."""
        sha = "abcdef1234567890" + "0" * 48
        path = store_override_file("content", sha, tmp_dir)
        assert path.name == "abcdef123456"

    def test_returns_existing_if_same_content(self, tmp_dir: Path):
        """Returns existing file path if content matches (idempotent)."""
        content = "test content"
        sha = "b" * 64
        path1 = store_override_file(content, sha, tmp_dir)
        path2 = store_override_file(content, sha, tmp_dir)
        assert path1 == path2

    def test_extends_prefix_on_collision(self, tmp_dir: Path):
        """Extends SHA prefix when collision with different content."""
        # Create two files with same 12-char prefix but different content
        sha1 = "a" * 12 + "1" * 52
        sha2 = "a" * 12 + "2" * 52
        path1 = store_override_file("content1", sha1, tmp_dir)
        path2 = store_override_file("content2", sha2, tmp_dir)
        # Second file should have longer name due to collision
        assert len(path2.name) > len(path1.name)
        # Both files should exist with correct content
        assert path1.read_text() == "content1"
        assert path2.read_text() == "content2"

    def test_creates_directory_if_missing(self, tmp_dir: Path):
        """Creates overrides directory if it doesn't exist."""
        overrides_dir = tmp_dir / "overrides"
        sha = "c" * 64
        path = store_override_file("content", sha, overrides_dir)
        assert overrides_dir.exists()
        assert path.exists()

    def test_handles_empty_content(self, tmp_dir: Path):
        """Handles empty string content."""
        sha = "d" * 64
        path = store_override_file("", sha, tmp_dir)
        assert path.exists()
        assert path.read_text() == ""


class TestProcessAuditResult:
    """Tests for process_audit_result function."""

    def test_successful_result(self, tmp_dir: Path, mock_args_audit: HostAuditArgs):
        """Processes successful SSH output correctly."""
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = f"""ROLE_PRESENT=1
ROLE=test-role
OVERRIDE_PRESENT=1
OVERRIDE_MODE=644
OVERRIDE_OWNER=root
OVERRIDE_GROUP=root
OVERRIDE_SIZE=100
OVERRIDE_MTIME=1704067200
{CONTENT_SENTINEL}
key=value
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            args=mock_args_audit,
            audit_log=audit_log,
            actor="test-actor",
        )
        assert result["ok"] is True
        assert result["host"] == "test.example.com"
        assert result["action"] == "host.audit"
        assert result["actor"] == "test-actor"

    def test_parses_role(self, tmp_dir: Path, mock_args_audit: HostAuditArgs):
        """Extracts role information from output."""
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = """ROLE_PRESENT=1
ROLE=production-webserver
OVERRIDE_PRESENT=0
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            args=mock_args_audit,
            audit_log=audit_log,
            actor="test-actor",
        )
        assert result["observed"]["role_present"] is True
        assert result["observed"]["role"] == "production-webserver"

    def test_parses_override_metadata(self, tmp_dir: Path, mock_args_audit: HostAuditArgs):
        """Extracts override metadata from output."""
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = f"""ROLE_PRESENT=0
OVERRIDE_PRESENT=1
OVERRIDE_MODE=755
OVERRIDE_OWNER=nobody
OVERRIDE_GROUP=nogroup
OVERRIDE_SIZE=256
OVERRIDE_MTIME=1700000000
{CONTENT_SENTINEL}
content
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            args=mock_args_audit,
            audit_log=audit_log,
            actor="test-actor",
        )
        meta = result["observed"]["override_meta"]
        assert meta["mode"] == "755"
        assert meta["owner"] == "nobody"
        assert meta["group"] == "nogroup"
        assert meta["size"] == "256"
        assert meta["mtime_epoch"] == "1700000000"

    def test_audit_log_excludes_override_contents(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs
    ):
        """Audit log stores sha only, not raw override contents."""
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = f"""ROLE_PRESENT=1
ROLE=test-role
OVERRIDE_PRESENT=1
OVERRIDE_MODE=644
OVERRIDE_OWNER=root
OVERRIDE_GROUP=root
OVERRIDE_SIZE=100
OVERRIDE_MTIME=1704067200
{CONTENT_SENTINEL}
secret=abc123
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            args=mock_args_audit,
            audit_log=audit_log,
            actor="test-actor",
        )

        assert result["observed"]["override_sha256"]
        # Now writes to host_observations.jsonl instead of audit.jsonl
        observations_log = tmp_dir / "host_observations.jsonl"
        log_record = json.loads(observations_log.read_text().strip())
        observed = log_record["observed"]
        assert "override_contents_for_display" not in observed
        assert "override_contents" not in observed

    def test_extracts_content_with_sentinel(self, tmp_dir: Path, mock_args_audit: HostAuditArgs):
        """Extracts content after sentinel marker."""
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        content = "line1\nline2\nline3\n"
        out = f"""OVERRIDE_PRESENT=1
ROLE_PRESENT=0
{CONTENT_SENTINEL}
{content}"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            args=mock_args_audit,
            audit_log=audit_log,
            actor="test-actor",
        )
        assert result["observed"]["override_contents_for_display"] == content

    def test_computes_sha256(self, tmp_dir: Path, mock_args_audit: HostAuditArgs):
        """Computes SHA256 of override content."""
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = f"""OVERRIDE_PRESENT=1
ROLE_PRESENT=0
{CONTENT_SENTINEL}
test content
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            args=mock_args_audit,
            audit_log=audit_log,
            actor="test-actor",
        )
        sha = result["observed"]["override_sha256"]
        assert sha is not None
        assert len(sha) == 64

    def test_failed_result(self, tmp_dir: Path, mock_args_audit: HostAuditArgs):
        """Handles failed SSH command."""
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        result = process_audit_result(
            "test.example.com",
            rc=255,
            out="",
            err="Connection refused",
            args=mock_args_audit,
            audit_log=audit_log,
            actor="test-actor",
        )
        assert result["ok"] is False
        assert result["ssh_rc"] == 255
        assert result["stderr"] == "Connection refused"

    def test_no_override(self, tmp_dir: Path, mock_args_audit: HostAuditArgs):
        """Handles output when no override file exists."""
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = """ROLE_PRESENT=1
ROLE=test-role
OVERRIDE_PRESENT=0
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            args=mock_args_audit,
            audit_log=audit_log,
            actor="test-actor",
        )
        assert result["observed"]["override_present"] is False
        assert result["observed"]["override_meta"] is None
        assert result["observed"]["override_sha256"] is None

    def test_writes_to_audit_log(self, tmp_dir: Path, mock_args_audit: HostAuditArgs):
        """Appends result to observations log file."""
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        process_audit_result(
            "test.example.com",
            rc=0,
            out="ROLE_PRESENT=0\nOVERRIDE_PRESENT=0\n",
            err="",
            args=mock_args_audit,
            audit_log=audit_log,
            actor="test-actor",
        )
        # Now writes to host_observations.jsonl instead of audit.jsonl
        observations_log = tmp_dir / "host_observations.jsonl"
        assert observations_log.exists()
        record = json.loads(observations_log.read_text().strip())
        assert record["host"] == "test.example.com"

    def test_writes_to_audit_log_with_lock(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, mocker
    ):
        """Appends result to observations log file while holding lock."""
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        mock_append = mocker.patch("fleetroll.audit.append_jsonl")
        log_lock = mocker.MagicMock()
        log_lock.__enter__.return_value = None
        log_lock.__exit__.return_value = None

        process_audit_result(
            "test.example.com",
            rc=0,
            out="ROLE_PRESENT=0\nOVERRIDE_PRESENT=0\n",
            err="",
            args=mock_args_audit,
            audit_log=audit_log,
            actor="test-actor",
            log_lock=log_lock,
        )

        log_lock.__enter__.assert_called_once()
        log_lock.__exit__.assert_called_once()
        mock_append.assert_called_once()
        args, _ = mock_append.call_args
        # Now writes to host_observations.jsonl instead of audit.jsonl
        observations_log = tmp_dir / "host_observations.jsonl"
        assert args[0] == observations_log
        assert args[1]["host"] == "test.example.com"

    def test_stores_override_file_when_dir_provided(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs
    ):
        """Stores override content to file when overrides_dir provided."""
        audit_log = tmp_dir / "audit.jsonl"
        overrides_dir = tmp_dir / "overrides"
        mock_args_audit.audit_log = str(audit_log)

        out = f"""OVERRIDE_PRESENT=1
ROLE_PRESENT=0
{CONTENT_SENTINEL}
stored content
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            args=mock_args_audit,
            audit_log=audit_log,
            actor="test-actor",
            overrides_dir=overrides_dir,
        )
        assert "override_file_path" in result["observed"]
        stored_path = Path(result["observed"]["override_file_path"])
        assert stored_path.exists()
        assert stored_path.read_text() == "stored content\n"

    def test_parses_puppet_state(self, tmp_dir: Path, mock_args_audit: HostAuditArgs):
        """Extracts puppet last run and success status from output."""
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = """ROLE_PRESENT=1
ROLE=test-role
OVERRIDE_PRESENT=0
PP_LAST_RUN_EPOCH=1706140800
PP_SUCCESS=1
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            args=mock_args_audit,
            audit_log=audit_log,
            actor="test-actor",
        )
        assert result["observed"]["puppet_last_run_epoch"] == 1706140800
        assert result["observed"]["puppet_success"] is True

    def test_parses_puppet_failure(self, tmp_dir: Path, mock_args_audit: HostAuditArgs):
        """Extracts puppet failure status from output."""
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = """ROLE_PRESENT=0
OVERRIDE_PRESENT=0
PP_LAST_RUN_EPOCH=1706140800
PP_SUCCESS=0
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            args=mock_args_audit,
            audit_log=audit_log,
            actor="test-actor",
        )
        assert result["observed"]["puppet_last_run_epoch"] == 1706140800
        assert result["observed"]["puppet_success"] is False

    def test_puppet_fields_none_when_missing(self, tmp_dir: Path, mock_args_audit: HostAuditArgs):
        """Puppet fields are None when not in output."""
        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = """ROLE_PRESENT=0
OVERRIDE_PRESENT=0
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            args=mock_args_audit,
            audit_log=audit_log,
            actor="test-actor",
        )
        assert result["observed"]["puppet_last_run_epoch"] is None
        assert result["observed"]["puppet_success"] is None
