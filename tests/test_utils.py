"""Tests for fleetroll/utils.py - pure utility functions."""

from __future__ import annotations

from pathlib import Path

import pytest
from fleetroll.exceptions import FleetRollError, UserError
from fleetroll.utils import (
    default_audit_log_path,
    ensure_host_or_file,
    ensure_parent_dir,
    format_host_preview,
    infer_actor,
    is_host_file,
    looks_like_host,
    natural_sort_key,
    parse_host_list,
    parse_kv_lines,
    sha256_hex,
    utc_now_iso,
)


class TestSha256Hex:
    """Tests for sha256_hex function."""

    def test_empty_bytes(self):
        """SHA256 of empty string is well-known value."""
        result = sha256_hex(b"")
        assert result == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_simple_string(self):
        """SHA256 of 'hello' is well-known value."""
        result = sha256_hex(b"hello")
        assert result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_returns_lowercase_hex(self):
        """Result should be lowercase hex string of 64 chars."""
        result = sha256_hex(b"test")
        assert result.islower()
        assert len(result) == 64

    def test_binary_data(self):
        """Should handle arbitrary binary data."""
        result = sha256_hex(bytes([0x00, 0xFF, 0x7F]))
        assert len(result) == 64


class TestNaturalSortKey:
    """Tests for natural_sort_key function."""

    def test_simple_numbers(self):
        """Should sort numbers naturally."""
        hosts = ["host10", "host2", "host1"]
        result = sorted(hosts, key=natural_sort_key)
        assert result == ["host1", "host2", "host10"]

    def test_mixed_alpha_numeric(self):
        """Should handle mixed alphanumeric strings."""
        hosts = ["t-linux-10", "t-linux-2", "t-linux-1"]
        result = sorted(hosts, key=natural_sort_key)
        assert result == ["t-linux-1", "t-linux-2", "t-linux-10"]

    def test_multiple_numbers(self):
        """Should handle multiple numbers in string."""
        hosts = ["host1-10", "host1-2", "host2-1"]
        result = sorted(hosts, key=natural_sort_key)
        assert result == ["host1-2", "host1-10", "host2-1"]

    def test_pure_alpha(self):
        """Should handle pure alphabetic strings."""
        hosts = ["charlie", "alpha", "bravo"]
        result = sorted(hosts, key=natural_sort_key)
        assert result == ["alpha", "bravo", "charlie"]

    def test_case_insensitive(self):
        """Should sort case-insensitively."""
        hosts = ["Host2", "host1", "HOST10"]
        result = sorted(hosts, key=natural_sort_key)
        assert result == ["host1", "Host2", "HOST10"]

    def test_leading_zeros(self):
        """Should handle leading zeros in numbers."""
        hosts = ["host001", "host10", "host2"]
        result = sorted(hosts, key=natural_sort_key)
        assert result == ["host001", "host2", "host10"]

    def test_fqdn_hostnames(self):
        """Should sort FQDN hostnames naturally."""
        hosts = [
            "host10.example.com",
            "host2.example.com",
            "host1.example.com",
        ]
        result = sorted(hosts, key=natural_sort_key)
        assert result == [
            "host1.example.com",
            "host2.example.com",
            "host10.example.com",
        ]


class TestParseKvLines:
    """Tests for parse_kv_lines function."""

    def test_empty_string(self):
        """Empty string returns empty dict."""
        assert parse_kv_lines("") == {}

    def test_single_line(self):
        """Single key=value line parsed correctly."""
        assert parse_kv_lines("KEY=value") == {"KEY": "value"}

    def test_multiple_lines(self):
        """Multiple lines parsed into dict."""
        result = parse_kv_lines("A=1\nB=2\nC=3")
        assert result == {"A": "1", "B": "2", "C": "3"}

    def test_lines_without_equals_ignored(self):
        """Lines without = are skipped."""
        result = parse_kv_lines("GOOD=yes\nBADLINE\nALSO_GOOD=ok")
        assert result == {"GOOD": "yes", "ALSO_GOOD": "ok"}

    def test_whitespace_stripped(self):
        """Whitespace around key and value is stripped."""
        result = parse_kv_lines("  KEY  =  value  ")
        assert result == {"KEY": "value"}

    def test_equals_in_value(self):
        """Only first = is used as delimiter."""
        result = parse_kv_lines("KEY=val=ue=with=equals")
        assert result == {"KEY": "val=ue=with=equals"}

    def test_empty_value(self):
        """Empty value after = is preserved."""
        result = parse_kv_lines("KEY=")
        assert result == {"KEY": ""}

    def test_duplicate_keys_last_wins(self):
        """When key appears multiple times, last value wins."""
        result = parse_kv_lines("KEY=first\nKEY=second")
        assert result == {"KEY": "second"}


class TestUtcNowIso:
    """Tests for utc_now_iso function."""

    def test_format_is_iso8601(self):
        """Result should be ISO 8601 format with UTC timezone."""
        result = utc_now_iso()
        assert "T" in result
        assert result.endswith("+00:00")

    def test_no_microseconds(self):
        """Result should not contain microseconds."""
        result = utc_now_iso()
        # Microseconds would appear as .NNNNNN before timezone
        assert "." not in result

    def test_reasonable_length(self):
        """Result should be reasonable length for ISO timestamp."""
        result = utc_now_iso()
        # Format: YYYY-MM-DDTHH:MM:SS+00:00 = 25 chars
        assert len(result) == 25


class TestIsHostFile:
    """Tests for is_host_file function."""

    def test_existing_file_returns_true(self, tmp_dir: Path):
        """Existing file path returns True."""
        host_file = tmp_dir / "hosts.txt"
        host_file.write_text("host1\n")
        assert is_host_file(str(host_file)) is True

    def test_nonexistent_returns_false(self, tmp_dir: Path):
        """Non-existent path returns False."""
        assert is_host_file(str(tmp_dir / "nonexistent.txt")) is False
        assert is_host_file(str(tmp_dir / "missing.list")) is True

    def test_directory_returns_false(self, tmp_dir: Path):
        """Directory path returns False."""
        assert is_host_file(str(tmp_dir)) is False

    def test_hostname_string_returns_false(self):
        """Plain hostname string returns False."""
        assert is_host_file("server.example.com") is False

    def test_user_at_hostname_returns_false(self):
        """user@hostname string returns False."""
        assert is_host_file("root@server.example.com") is False


class TestParseHostList:
    """Tests for parse_host_list function."""

    def test_valid_hosts(self, tmp_dir: Path):
        """File with valid hosts returns list."""
        f = tmp_dir / "hosts.txt"
        f.write_text("host1.example.com\nhost2.example.com\n")
        result = parse_host_list(f)
        assert result == ["host1.example.com", "host2.example.com"]

    def test_comments_ignored(self, tmp_dir: Path):
        """Lines starting with # are ignored."""
        f = tmp_dir / "hosts.txt"
        f.write_text("# comment\nhost1\n# another comment\nhost2\n")
        result = parse_host_list(f)
        assert result == ["host1", "host2"]

    def test_blank_lines_ignored(self, tmp_dir: Path):
        """Empty lines are ignored."""
        f = tmp_dir / "hosts.txt"
        f.write_text("host1\n\n\nhost2\n")
        result = parse_host_list(f)
        assert result == ["host1", "host2"]

    def test_empty_file_raises(self, tmp_dir: Path):
        """Empty file raises FleetRollError."""
        f = tmp_dir / "hosts.txt"
        f.write_text("")
        with pytest.raises(FleetRollError, match="No valid hosts found"):
            parse_host_list(f)

    def test_missing_file_raises(self, tmp_dir: Path):
        """Missing file raises FleetRollError."""
        f = tmp_dir / "missing.list"
        with pytest.raises(FleetRollError, match="Host list file not found"):
            parse_host_list(f)

    def test_comments_only_raises(self, tmp_dir: Path):
        """File with only comments raises FleetRollError."""
        f = tmp_dir / "hosts.txt"
        f.write_text("# only comments\n# no hosts\n")
        with pytest.raises(FleetRollError, match="No valid hosts found"):
            parse_host_list(f)

    def test_whitespace_stripped(self, tmp_dir: Path):
        """Leading/trailing whitespace on hosts is stripped."""
        f = tmp_dir / "hosts.txt"
        f.write_text("  host1  \n  host2  \n")
        result = parse_host_list(f)
        assert result == ["host1", "host2"]

    def test_no_trailing_newline(self, tmp_dir: Path):
        """File without trailing newline still works."""
        f = tmp_dir / "hosts.txt"
        f.write_text("host1\nhost2")
        result = parse_host_list(f)
        assert result == ["host1", "host2"]

    def test_fqdn_directive_appends_suffix(self, tmp_dir: Path):
        """Short hostnames get the fqdn suffix appended."""
        f = tmp_dir / "hosts.list"
        f.write_text("# fqdn: .example.com\nhost1\nhost2\n")
        result = parse_host_list(f)
        assert result == ["host1.example.com", "host2.example.com"]

    def test_fqdn_directive_skips_fqdn_hosts(self, tmp_dir: Path):
        """Hosts that already contain a dot are left unchanged."""
        f = tmp_dir / "hosts.list"
        f.write_text("# fqdn: .example.com\nhost1.other.com\nhost2.other.com\n")
        result = parse_host_list(f)
        assert result == ["host1.other.com", "host2.other.com"]

    def test_fqdn_directive_mixed(self, tmp_dir: Path):
        """Mix of short and FQDN hosts: only short ones get the suffix."""
        f = tmp_dir / "hosts.list"
        f.write_text("# fqdn: .example.com\nshorthost\nfull.other.com\n")
        result = parse_host_list(f)
        assert result == ["shorthost.example.com", "full.other.com"]

    def test_fqdn_directive_without_leading_dot(self, tmp_dir: Path):
        """Suffix without leading dot is normalized to include one."""
        f = tmp_dir / "hosts.list"
        f.write_text("# fqdn: example.com\nhost1\n")
        result = parse_host_list(f)
        assert result == ["host1.example.com"]

    def test_no_fqdn_directive_unchanged(self, tmp_dir: Path):
        """Without fqdn directive, existing behavior is unaffected."""
        f = tmp_dir / "hosts.list"
        f.write_text("host1\nhost2.example.com\n")
        result = parse_host_list(f)
        assert result == ["host1", "host2.example.com"]


class TestLooksLikeHost:
    """Tests for looks_like_host function."""

    def test_hostname(self):
        assert looks_like_host("server.example.com") is True

    def test_short_hostname(self):
        assert looks_like_host("server1") is True

    def test_user_at_hostname(self):
        assert looks_like_host("root@server.example.com") is True

    def test_ipv4(self):
        assert looks_like_host("192.168.1.10") is True

    def test_ipv6(self):
        assert looks_like_host("2001:db8::1") is True

    def test_bracketed_ipv6(self):
        assert looks_like_host("[2001:db8::1]") is True

    def test_invalid(self):
        assert looks_like_host("not a host") is False


class TestEnsureHostOrFile:
    """Tests for ensure_host_or_file function."""

    def test_non_host_missing_file_raises(self):
        with pytest.raises(UserError, match="HOST_OR_FILE"):
            ensure_host_or_file("not/a/host")

    def test_non_host_existing_file_ok(self, tmp_dir: Path):
        host_file = tmp_dir / "hosts.list"
        host_file.write_text("host1\n")
        ensure_host_or_file(str(host_file))

    def test_host_like_missing_file_ok(self):
        ensure_host_or_file("missing.example.com")

    def test_list_suffix_requires_existing_file(self, tmp_dir: Path):
        with pytest.raises(UserError, match="Host list file not found"):
            ensure_host_or_file(str(tmp_dir / "missing.list"))


class TestInferActor:
    """Tests for infer_actor function."""

    def test_fleetroll_actor_priority(self, monkeypatch: pytest.MonkeyPatch):
        """FLEETROLL_ACTOR takes priority over other env vars."""
        monkeypatch.setenv("FLEETROLL_ACTOR", "test-actor")
        monkeypatch.setenv("SUDO_USER", "sudo-user")
        monkeypatch.setenv("USER", "regular-user")
        assert infer_actor() == "test-actor"

    def test_sudo_user_fallback(self, monkeypatch: pytest.MonkeyPatch):
        """SUDO_USER is used when FLEETROLL_ACTOR not set."""
        monkeypatch.delenv("FLEETROLL_ACTOR", raising=False)
        monkeypatch.setenv("SUDO_USER", "sudo-user")
        monkeypatch.setenv("USER", "regular-user")
        assert infer_actor() == "sudo-user"

    def test_user_fallback(self, monkeypatch: pytest.MonkeyPatch):
        """USER is used when FLEETROLL_ACTOR and SUDO_USER not set."""
        monkeypatch.delenv("FLEETROLL_ACTOR", raising=False)
        monkeypatch.delenv("SUDO_USER", raising=False)
        monkeypatch.setenv("USER", "regular-user")
        assert infer_actor() == "regular-user"

    def test_unknown_fallback(self, monkeypatch: pytest.MonkeyPatch):
        """Returns 'unknown' when no env vars are set."""
        monkeypatch.delenv("FLEETROLL_ACTOR", raising=False)
        monkeypatch.delenv("SUDO_USER", raising=False)
        monkeypatch.delenv("USER", raising=False)
        assert infer_actor() == "unknown"


class TestDefaultAuditLogPath:
    """Tests for default_audit_log_path function."""

    def test_returns_path_in_home(self, monkeypatch: pytest.MonkeyPatch):
        """Returns path under home directory."""
        monkeypatch.setenv("HOME", "/home/testuser")
        result = default_audit_log_path()
        assert str(result) == "/home/testuser/.fleetroll/audit.jsonl"

    def test_returns_path_object(self):
        """Returns a Path object."""
        result = default_audit_log_path()
        assert isinstance(result, Path)


class TestEnsureParentDir:
    """Tests for ensure_parent_dir function."""

    def test_creates_parent_if_missing(self, tmp_dir: Path):
        """Creates parent directories if they don't exist."""
        target = tmp_dir / "a" / "b" / "c" / "file.txt"
        ensure_parent_dir(target)
        assert target.parent.exists()

    def test_idempotent_if_exists(self, tmp_dir: Path):
        """Does not raise if parent already exists."""
        target = tmp_dir / "existing" / "file.txt"
        target.parent.mkdir(parents=True)
        ensure_parent_dir(target)  # Should not raise
        assert target.parent.exists()

    def test_handles_single_level(self, tmp_dir: Path):
        """Works with single level of nesting."""
        target = tmp_dir / "file.txt"
        ensure_parent_dir(target)
        assert target.parent.exists()


class TestFormatHostPreview:
    """Tests for format_host_preview function."""

    def test_shows_all_hosts_when_at_limit(self):
        """Shows all hosts when count equals limit."""
        hosts = ["host1", "host2", "host3", "host4", "host5"]
        result = format_host_preview(hosts, limit=5)
        assert result == [
            "  - host1",
            "  - host2",
            "  - host3",
            "  - host4",
            "  - host5",
        ]

    def test_shows_all_hosts_when_below_limit(self):
        """Shows all hosts when count is below limit."""
        hosts = ["host1", "host2", "host3"]
        result = format_host_preview(hosts, limit=5)
        assert result == [
            "  - host1",
            "  - host2",
            "  - host3",
        ]

    def test_shows_first_n_plus_overflow_when_above_limit(self):
        """Shows first N hosts plus overflow message when count exceeds limit."""
        hosts = [f"host{i}" for i in range(1, 11)]
        result = format_host_preview(hosts, limit=5)
        assert result == [
            "  - host1",
            "  - host2",
            "  - host3",
            "  - host4",
            "  - host5",
            "  ... and 5 more hosts",
        ]

    def test_singular_overflow_message(self):
        """Uses singular 'host' when only one host is hidden."""
        hosts = ["host1", "host2", "host3", "host4", "host5", "host6"]
        result = format_host_preview(hosts, limit=5)
        assert result == [
            "  - host1",
            "  - host2",
            "  - host3",
            "  - host4",
            "  - host5",
            "  ... and 1 more host",
        ]

    def test_empty_list(self):
        """Returns empty list for empty input."""
        result = format_host_preview([], limit=5)
        assert result == []

    def test_single_host(self):
        """Works with single host."""
        result = format_host_preview(["host1"], limit=5)
        assert result == ["  - host1"]
