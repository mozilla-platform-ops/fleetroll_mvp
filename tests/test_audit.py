"""Tests for fleetroll/audit.py - audit logging and file storage."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from fleetroll.audit import (
    append_jsonl,
    iter_audit_records,
    process_audit_result,
    store_override_file,
)
from fleetroll.cli_types import HostAuditArgs
from fleetroll.constants import CONTENT_SENTINEL
from fleetroll.db import get_latest_host_observations


def _make_pp_state_json_line(**overrides) -> str:
    """Build a PP_STATE_JSON=<b64> line from a JSON state dict."""
    state = {
        "ts": "2024-01-25T12:34:56+00:00",
        "git_sha": "test" + "0" * 36,
        "git_repo": "https://github.com/example/repo.git",
        "git_branch": "main",
        "git_dirty": False,
        "override_sha": "fake" + "0" * 60,
        "vault_sha": "mock" + "0" * 60,
        "role": "gecko-t-linux-talos",
        "exit_code": 0,
        "duration_s": 100,
        "success": True,
    }
    state.update(overrides)
    json_str = json.dumps(state)
    b64_str = base64.b64encode(json_str.encode("utf-8")).decode("utf-8")
    return f"PP_STATE_JSON={b64_str}"


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

    def test_successful_result(self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn):
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
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["ok"] is True
        assert result["host"] == "test.example.com"
        assert result["action"] == "host.audit"
        assert result["actor"] == "test-actor"

    def test_parses_role(self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn):
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
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["role_present"] is True
        assert result["observed"]["role"] == "production-webserver"

    def test_parses_override_metadata(self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn):
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
            db_conn=db_conn,
            actor="test-actor",
        )
        meta = result["observed"]["override_meta"]
        assert meta["mode"] == "755"
        assert meta["owner"] == "nobody"
        assert meta["group"] == "nogroup"
        assert meta["size"] == "256"
        assert meta["mtime_epoch"] == "1700000000"

    def test_audit_log_excludes_override_contents(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
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
            db_conn=db_conn,
            actor="test-actor",
        )

        assert result["observed"]["override_sha256"]
        # Verify record was written to SQLite without sensitive fields
        latest, _ = get_latest_host_observations(db_conn, ["test.example.com"])
        log_record = latest["test.example.com"]
        observed = log_record["observed"]
        assert "override_contents_for_display" not in observed
        assert "override_contents" not in observed

    def test_extracts_content_with_sentinel(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
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
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["override_contents_for_display"] == content

    def test_computes_sha256(self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn):
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
            db_conn=db_conn,
            actor="test-actor",
        )
        sha = result["observed"]["override_sha256"]
        assert sha is not None
        assert len(sha) == 64

    def test_failed_result(self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn):
        """Handles failed SSH command."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        result = process_audit_result(
            "test.example.com",
            rc=255,
            out="",
            err="Connection refused",
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["ok"] is False
        assert result["ssh_rc"] == 255
        assert result["stderr"] == "Connection refused"

    def test_no_override(self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn):
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
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["override_present"] is False
        assert result["observed"]["override_meta"] is None
        assert result["observed"]["override_sha256"] is None

    def test_writes_to_audit_log(self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn):
        """Appends result to observations log file."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        process_audit_result(
            "test.example.com",
            rc=0,
            out="ROLE_PRESENT=0\nOVERRIDE_PRESENT=0\n",
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        # Verify record was written to SQLite
        latest, _ = get_latest_host_observations(db_conn, ["test.example.com"])
        assert "test.example.com" in latest
        record = latest["test.example.com"]
        assert record["host"] == "test.example.com"

    def test_writes_to_audit_log_with_lock(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn, mocker
    ):
        """Writes to SQLite while holding lock."""

        mock_insert = mocker.patch("fleetroll.db.insert_host_observation")
        log_lock = mocker.MagicMock()
        log_lock.__enter__.return_value = None
        log_lock.__exit__.return_value = None

        process_audit_result(
            "test.example.com",
            rc=0,
            out="ROLE_PRESENT=0\nOVERRIDE_PRESENT=0\n",
            err="",
            db_conn=db_conn,
            actor="test-actor",
            log_lock=log_lock,
        )

        log_lock.__enter__.assert_called_once()
        log_lock.__exit__.assert_called_once()
        mock_insert.assert_called_once()
        args, _ = mock_insert.call_args
        assert args[0] == db_conn
        assert args[1]["host"] == "test.example.com"

    def test_stores_override_file_when_dir_provided(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
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
            db_conn=db_conn,
            actor="test-actor",
            overrides_dir=overrides_dir,
        )
        assert "override_file_path" in result["observed"]
        stored_path = Path(result["observed"]["override_file_path"])
        assert stored_path.exists()
        assert stored_path.read_text() == "stored content\n"

    def test_parses_puppet_state(self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn):
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
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_last_run_epoch"] == 1706140800
        assert result["observed"]["puppet_success"] is True

    def test_parses_puppet_failure(self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn):
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
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_last_run_epoch"] == 1706140800
        assert result["observed"]["puppet_success"] is False

    def test_puppet_fields_none_when_missing(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
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
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_last_run_epoch"] is None
        assert result["observed"]["puppet_success"] is None

    def test_parses_all_new_puppet_fields(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """Extracts all new puppet state fields from JSON metadata."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = """ROLE_PRESENT=1
ROLE=test-role
OVERRIDE_PRESENT=0
PP_STATE_TS=2024-01-25T12:34:56Z
PP_LAST_RUN_EPOCH=1706187296
PP_SUCCESS=1
PP_GIT_SHA=abc123def456
PP_GIT_REPO=https://github.com/example/repo.git
PP_GIT_BRANCH=main
PP_GIT_DIRTY=0
PP_OVERRIDE_SHA_APPLIED=def789abc012
PP_VAULT_SHA_APPLIED=ghi345jkl678
PP_ROLE=gecko-t-linux-talos
PP_EXIT_CODE=2
PP_DURATION_S=145
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        # String fields
        assert result["observed"]["puppet_state_ts"] == "2024-01-25T12:34:56Z"
        assert result["observed"]["puppet_git_sha"] == "abc123def456"  # pragma: allowlist secret
        assert result["observed"]["puppet_git_repo"] == "https://github.com/example/repo.git"
        assert result["observed"]["puppet_git_branch"] == "main"
        assert (
            result["observed"]["puppet_override_sha_applied"]
            == "def789abc012"  # pragma: allowlist secret
        )
        assert (
            result["observed"]["puppet_vault_sha_applied"]
            == "ghi345jkl678"  # pragma: allowlist secret
        )
        assert result["observed"]["puppet_role"] == "gecko-t-linux-talos"
        # Integer fields
        assert result["observed"]["puppet_last_run_epoch"] == 1706187296
        assert result["observed"]["puppet_exit_code"] == 2
        assert result["observed"]["puppet_duration_s"] == 145
        # Boolean fields
        assert result["observed"]["puppet_success"] is True
        assert result["observed"]["puppet_git_dirty"] is False

    def test_parses_git_dirty_true(self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn):
        """Correctly parses git_dirty when true."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = """ROLE_PRESENT=0
OVERRIDE_PRESENT=0
PP_GIT_DIRTY=1
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_git_dirty"] is True

    def test_new_puppet_fields_none_when_missing(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """All new puppet fields are None when not in output."""

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
            db_conn=db_conn,
            actor="test-actor",
        )
        # String fields
        assert result["observed"]["puppet_state_ts"] is None
        assert result["observed"]["puppet_git_sha"] is None
        assert result["observed"]["puppet_git_repo"] is None
        assert result["observed"]["puppet_git_branch"] is None
        assert result["observed"]["puppet_override_sha_applied"] is None
        assert result["observed"]["puppet_vault_sha_applied"] is None
        assert result["observed"]["puppet_role"] is None
        # Integer fields
        assert result["observed"]["puppet_exit_code"] is None
        assert result["observed"]["puppet_duration_s"] is None
        # Boolean fields
        assert result["observed"]["puppet_git_dirty"] is None

    def test_puppet_integer_parsing_error_handling(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """Handles invalid integer values gracefully."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = """ROLE_PRESENT=0
OVERRIDE_PRESENT=0
PP_EXIT_CODE=invalid
PP_DURATION_S=not_a_number
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_exit_code"] is None
        assert result["observed"]["puppet_duration_s"] is None

    def test_backward_compatibility_old_format(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """Old format without PP_STATE_TS still works."""

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
            db_conn=db_conn,
            actor="test-actor",
        )
        # Old fields still work
        assert result["observed"]["puppet_last_run_epoch"] == 1706140800
        assert result["observed"]["puppet_success"] is True
        # New fields are None
        assert result["observed"]["puppet_state_ts"] is None
        assert result["observed"]["puppet_git_sha"] is None


class TestProcessAuditResultJsonPath:
    """Tests for JSON state parsing (base64-encoded PP_STATE_JSON)."""

    def test_parses_json_state_all_fields(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """Complete JSON state parses all 12 puppet fields correctly."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        json_line = _make_pp_state_json_line()
        out = f"""ROLE_PRESENT=1
ROLE=test-role
OVERRIDE_PRESENT=0
{json_line}
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        # Verify all fields
        assert result["observed"]["puppet_state_ts"] == "2024-01-25T12:34:56+00:00"
        assert result["observed"]["puppet_git_sha"] == "test" + "0" * 36
        assert result["observed"]["puppet_git_repo"] == "https://github.com/example/repo.git"
        assert result["observed"]["puppet_git_branch"] == "main"
        assert result["observed"]["puppet_git_dirty"] is False
        assert result["observed"]["puppet_override_sha_applied"] == "fake" + "0" * 60
        assert result["observed"]["puppet_vault_sha_applied"] == "mock" + "0" * 60
        assert result["observed"]["puppet_role"] == "gecko-t-linux-talos"
        assert result["observed"]["puppet_exit_code"] == 0
        assert result["observed"]["puppet_duration_s"] == 100
        assert result["observed"]["puppet_success"] is True
        assert result["observed"]["puppet_last_run_epoch"] == 1706186096

    def test_json_state_ts_converts_to_epoch(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """JSON state timestamp converts to epoch correctly."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        json_line = _make_pp_state_json_line(ts="2024-01-25T12:34:56+00:00")
        out = f"""ROLE_PRESENT=0
OVERRIDE_PRESENT=0
{json_line}
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_last_run_epoch"] == 1706186096

    def test_json_state_success_false(self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn):
        """JSON state with success=false parses correctly."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        json_line = _make_pp_state_json_line(success=False)
        out = f"""ROLE_PRESENT=0
OVERRIDE_PRESENT=0
{json_line}
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_success"] is False

    def test_json_state_git_dirty_true(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """JSON state with git_dirty=true parses correctly."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        json_line = _make_pp_state_json_line(git_dirty=True)
        out = f"""ROLE_PRESENT=0
OVERRIDE_PRESENT=0
{json_line}
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_git_dirty"] is True

    def test_json_state_null_optional_fields(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """JSON state with null optional fields returns None for those fields."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        json_line = _make_pp_state_json_line(
            git_sha=None,
            git_repo=None,
            git_branch=None,
            override_sha=None,
            vault_sha=None,
        )
        out = f"""ROLE_PRESENT=0
OVERRIDE_PRESENT=0
{json_line}
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_git_sha"] is None
        assert result["observed"]["puppet_git_repo"] is None
        assert result["observed"]["puppet_git_branch"] is None
        assert result["observed"]["puppet_override_sha_applied"] is None
        assert result["observed"]["puppet_vault_sha_applied"] is None

    def test_json_state_invalid_base64_graceful(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """Corrupt base64 results in all puppet fields being None."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = """ROLE_PRESENT=0
OVERRIDE_PRESENT=0
PP_STATE_JSON=not_valid_base64!!!
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_state_ts"] is None
        assert result["observed"]["puppet_git_sha"] is None
        assert result["observed"]["puppet_success"] is None

    def test_json_state_invalid_json_graceful(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """Valid base64 but invalid JSON results in all puppet fields being None."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        # Base64 of "not json at all"
        bad_b64 = base64.b64encode(b"not json at all").decode("utf-8")
        out = f"""ROLE_PRESENT=0
OVERRIDE_PRESENT=0
PP_STATE_JSON={bad_b64}
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_state_ts"] is None
        assert result["observed"]["puppet_git_sha"] is None
        assert result["observed"]["puppet_success"] is None

    def test_json_state_invalid_ts_no_epoch(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """Invalid timestamp preserves puppet_state_ts but puppet_last_run_epoch is None."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        json_line = _make_pp_state_json_line(ts="invalid-timestamp")
        out = f"""ROLE_PRESENT=0
OVERRIDE_PRESENT=0
{json_line}
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_state_ts"] == "invalid-timestamp"
        assert result["observed"]["puppet_last_run_epoch"] is None

    def test_json_state_takes_priority_over_kv(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """When both PP_STATE_JSON and KV lines present, JSON values win."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        json_line = _make_pp_state_json_line(
            success=True,
            git_sha="json_sha",
            exit_code=0,
        )
        out = f"""ROLE_PRESENT=0
OVERRIDE_PRESENT=0
{json_line}
PP_SUCCESS=0
PP_GIT_SHA=kv_sha
PP_EXIT_CODE=99
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        # JSON values win
        assert result["observed"]["puppet_success"] is True
        assert result["observed"]["puppet_git_sha"] == "json_sha"
        assert result["observed"]["puppet_exit_code"] == 0


class TestBackwardCompatibilityKvFields:
    """Tests for KV fallback parsing when PP_STATE_JSON not present."""

    def test_kv_fallback_all_string_fields(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """All string KV fields parse correctly."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = """ROLE_PRESENT=0
OVERRIDE_PRESENT=0
PP_STATE_TS=2024-01-25T12:34:56Z
PP_GIT_SHA=kv_git_sha
PP_GIT_REPO=https://github.com/kv/repo.git
PP_GIT_BRANCH=kv-branch
PP_OVERRIDE_SHA_APPLIED=kv_override_sha
PP_VAULT_SHA_APPLIED=kv_vault_sha
PP_ROLE=kv-role
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_state_ts"] == "2024-01-25T12:34:56Z"
        assert result["observed"]["puppet_git_sha"] == "kv_git_sha"
        assert result["observed"]["puppet_git_repo"] == "https://github.com/kv/repo.git"
        assert result["observed"]["puppet_git_branch"] == "kv-branch"
        assert result["observed"]["puppet_override_sha_applied"] == "kv_override_sha"
        assert result["observed"]["puppet_vault_sha_applied"] == "kv_vault_sha"
        assert result["observed"]["puppet_role"] == "kv-role"

    def test_kv_fallback_integer_fields(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """Integer KV fields parse correctly."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = """ROLE_PRESENT=0
OVERRIDE_PRESENT=0
PP_EXIT_CODE=42
PP_DURATION_S=300
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_exit_code"] == 42
        assert result["observed"]["puppet_duration_s"] == 300

    def test_kv_fallback_boolean_fields(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """Boolean KV fields (0/1) parse correctly."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = """ROLE_PRESENT=0
OVERRIDE_PRESENT=0
PP_SUCCESS=1
PP_GIT_DIRTY=0
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_success"] is True
        assert result["observed"]["puppet_git_dirty"] is False

    def test_kv_fallback_invalid_integers_are_none(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """Invalid integer values result in None."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        out = """ROLE_PRESENT=0
OVERRIDE_PRESENT=0
PP_EXIT_CODE=not_an_int
PP_DURATION_S=also_not_int
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        assert result["observed"]["puppet_exit_code"] is None
        assert result["observed"]["puppet_duration_s"] is None

    def test_json_overrides_kv_for_every_field(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """JSON values take priority over KV values for all fields."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        json_line = _make_pp_state_json_line(
            ts="2024-01-01T00:00:00Z",
            git_sha="json_sha",
            git_repo="json_repo",
            git_branch="json_branch",
            git_dirty=True,
            override_sha="json_override",
            vault_sha="json_vault",
            role="json_role",
            exit_code=1,
            duration_s=50,
            success=False,
        )
        out = f"""ROLE_PRESENT=0
OVERRIDE_PRESENT=0
{json_line}
PP_STATE_TS=kv_ts
PP_GIT_SHA=kv_sha
PP_GIT_REPO=kv_repo
PP_GIT_BRANCH=kv_branch
PP_GIT_DIRTY=0
PP_OVERRIDE_SHA_APPLIED=kv_override
PP_VAULT_SHA_APPLIED=kv_vault
PP_ROLE=kv_role
PP_EXIT_CODE=99
PP_DURATION_S=999
PP_SUCCESS=1
"""
        result = process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )
        # All JSON values win
        assert result["observed"]["puppet_state_ts"] == "2024-01-01T00:00:00Z"
        assert result["observed"]["puppet_git_sha"] == "json_sha"
        assert result["observed"]["puppet_git_repo"] == "json_repo"
        assert result["observed"]["puppet_git_branch"] == "json_branch"
        assert result["observed"]["puppet_git_dirty"] is True
        assert result["observed"]["puppet_override_sha_applied"] == "json_override"
        assert result["observed"]["puppet_vault_sha_applied"] == "json_vault"
        assert result["observed"]["puppet_role"] == "json_role"
        assert result["observed"]["puppet_exit_code"] == 1
        assert result["observed"]["puppet_duration_s"] == 50
        assert result["observed"]["puppet_success"] is False


class TestIterAuditRecords:
    """Tests for iter_audit_records function."""

    def test_reads_valid_jsonl(self, tmp_dir: Path):
        """Reads valid JSONL records correctly."""
        path = tmp_dir / "test.jsonl"
        append_jsonl(path, {"key1": "value1"})
        append_jsonl(path, {"key2": "value2"})
        append_jsonl(path, {"key3": "value3"})

        records = list(iter_audit_records(path))
        assert len(records) == 3
        assert records[0]["key1"] == "value1"
        assert records[1]["key2"] == "value2"
        assert records[2]["key3"] == "value3"

    def test_skips_invalid_lines(self, tmp_dir: Path):
        """Skips invalid JSON lines gracefully."""
        path = tmp_dir / "test.jsonl"
        path.write_text('{"valid": 1}\ninvalid json line\n{"also_valid": 2}\nmore junk\n')

        records = list(iter_audit_records(path))
        assert len(records) == 2
        assert records[0]["valid"] == 1
        assert records[1]["also_valid"] == 2

    def test_returns_empty_for_missing_file(self, tmp_dir: Path):
        """Returns empty iterator for nonexistent file."""
        path = tmp_dir / "nonexistent.jsonl"
        records = list(iter_audit_records(path))
        assert len(records) == 0

    def test_skips_blank_lines(self, tmp_dir: Path):
        """Skips blank lines in JSONL file."""
        path = tmp_dir / "test.jsonl"
        path.write_text('{"key1": "value1"}\n\n{"key2": "value2"}\n  \n{"key3": "value3"}\n')

        records = list(iter_audit_records(path))
        assert len(records) == 3
        assert records[0]["key1"] == "value1"
        assert records[1]["key2"] == "value2"
        assert records[2]["key3"] == "value3"


class TestJsonlRoundTrip:
    """Tests for JSONL write/read round-trip."""

    def test_write_read_roundtrip(self, tmp_dir: Path):
        """Write and read back a full audit record."""
        path = tmp_dir / "roundtrip.jsonl"
        record = {
            "host": "test.example.com",
            "ts": "2024-01-25T12:34:56+00:00",
            "ok": True,
            "observed": {
                "role_present": True,
                "role": "test-role",
                "puppet_state_ts": "2024-01-25T12:30:00+00:00",
                "puppet_success": True,
            },
        }

        append_jsonl(path, record)
        records = list(iter_audit_records(path))

        assert len(records) == 1
        assert records[0] == record

    def test_multiple_records_roundtrip(self, tmp_dir: Path):
        """Write and read back multiple records."""
        path = tmp_dir / "multi.jsonl"
        records_to_write = [{"host": f"host{i}", "value": i, "ok": True} for i in range(5)]

        for record in records_to_write:
            append_jsonl(path, record)

        records_read = list(iter_audit_records(path))

        assert len(records_read) == 5
        for i, record in enumerate(records_read):
            assert record["host"] == f"host{i}"
            assert record["value"] == i
            assert record["ok"] is True


class TestEndToEndJsonState:
    """End-to-end tests for JSON state processing."""

    def test_full_flow_json_state_to_observations(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """Full flow: SSH output with PP_STATE_JSON → observations file."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        json_line = _make_pp_state_json_line()
        out = f"""ROLE_PRESENT=1
ROLE=test-role
OVERRIDE_PRESENT=0
{json_line}
"""
        process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
        )

        # Read back from SQLite
        latest, _ = get_latest_host_observations(db_conn, ["test.example.com"])

        assert len(latest) == 1
        record = latest["test.example.com"]
        assert record["host"] == "test.example.com"
        obs = record["observed"]
        assert obs["puppet_state_ts"] == "2024-01-25T12:34:56+00:00"
        assert obs["puppet_git_sha"] == "test" + "0" * 36
        assert obs["puppet_success"] is True
        # override_contents_for_display should not be in observations file
        assert "override_contents_for_display" not in obs

    def test_full_flow_multiple_hosts(self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn):
        """Process multiple hosts and verify all records in observations file."""

        audit_log = tmp_dir / "audit.jsonl"
        mock_args_audit.audit_log = str(audit_log)

        for i in range(3):
            json_line = _make_pp_state_json_line(role=f"role-{i}")
            out = f"""ROLE_PRESENT=1
ROLE=test-role-{i}
OVERRIDE_PRESENT=0
{json_line}
"""
            process_audit_result(
                f"host{i}.example.com",
                rc=0,
                out=out,
                err="",
                db_conn=db_conn,
                actor="test-actor",
            )

        # Read back from SQLite
        hosts = [f"host{i}.example.com" for i in range(3)]
        latest, _ = get_latest_host_observations(db_conn, hosts)

        assert len(latest) == 3
        for i in range(3):
            host = f"host{i}.example.com"
            record = latest[host]
            assert record["host"] == host
            assert record["observed"]["puppet_role"] == f"role-{i}"

    def test_full_flow_with_override_storage(
        self, tmp_dir: Path, mock_args_audit: HostAuditArgs, db_conn
    ):
        """Process with overrides_dir and verify both JSONL record and stored file."""

        audit_log = tmp_dir / "audit.jsonl"
        overrides_dir = tmp_dir / "overrides"
        mock_args_audit.audit_log = str(audit_log)

        json_line = _make_pp_state_json_line()
        out = f"""ROLE_PRESENT=1
ROLE=test-role
OVERRIDE_PRESENT=1
OVERRIDE_MODE=644
OVERRIDE_OWNER=root
OVERRIDE_GROUP=root
OVERRIDE_SIZE=100
OVERRIDE_MTIME=1704067200
{json_line}
{CONTENT_SENTINEL}
test content
"""
        process_audit_result(
            "test.example.com",
            rc=0,
            out=out,
            err="",
            db_conn=db_conn,
            actor="test-actor",
            overrides_dir=overrides_dir,
        )

        # Verify SQLite record
        latest, _ = get_latest_host_observations(db_conn, ["test.example.com"])
        assert len(latest) == 1
        record = latest["test.example.com"]
        assert "override_file_path" in record["observed"]

        # Verify stored override file
        stored_path = Path(record["observed"]["override_file_path"])
        assert stored_path.exists()
        assert stored_path.read_text() == "test content\n"
