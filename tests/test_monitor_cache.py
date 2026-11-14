"""Tests for monitor SHA info cache."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fleetroll.commands.monitor.cache import (
    ShaInfoCache,
    find_vault_symlink,
    override_configuration_key,
    parse_override_file,
)


def test_override_configuration_ignores_only_presentation():
    first = 'PUPPET_REPO="https://example.com/repo.git"\nPUPPET_BRANCH=topic\nEMPTY=\n'
    second = (
        "# Generated for another host\n\n"
        "  EMPTY=''\nPUPPET_BRANCH='topic' # same branch\n"
        "\tPUPPET_REPO=https://example.com/repo.git\n"
    )
    assert override_configuration_key(first) == override_configuration_key(second)
    assert override_configuration_key(first) is not None


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("PUPPET_REPO=original", "PUPPET_REPO=other"),
        ("PUPPET_BRANCH=topic", "PUPPET_BRANCH=other"),
        ("", "WORKER_TYPE_OVERRIDE=other-pool"),
        ("PUPPET_MAIL=original@example.com", "PUPPET_MAIL=other@example.com"),
        ("", "UNKNOWN_SETTING=active"),
        ("PUPPET_BRANCH=topic", "export PUPPET_BRANCH=topic"),
        ("", "EMPTY="),
    ],
)
def test_override_configuration_preserves_every_setting(first, second):
    base = "COMMON_SETTING=same\n"
    assert override_configuration_key(base + first + "\n") != override_configuration_key(
        base + second + "\n"
    )


@pytest.mark.parametrize(
    "shell",
    [
        "PUPPET_BRANCH=$BRANCH",
        'PUPPET_BRANCH="${BRANCH}"',
        "PUPPET_BRANCH=$(touch should-never-exist)",
        "PUPPET_BRANCH=`echo topic`",
        "PUPPET_BRANCH=topic; echo other",
        "source other-settings",
        "if true; then\nPUPPET_BRANCH=topic\nfi",
        "PUPPET_BRANCH=topic\nPUPPET_BRANCH=other",
        "PUPPET_BRANCH=~user",
        "PUPPET_BRANCH={a,b}",
        "PUPPET_BRANCH='unterminated",
        'PUPPET_BRANCH="escaped\\value"',
    ],
)
def test_override_configuration_rejects_unknown_shell(shell):
    assert override_configuration_key(shell) is None


def test_override_configuration_distinguishes_literal_from_expansion():
    assert override_configuration_key("VALUE='$BRANCH'\n") is not None
    assert override_configuration_key('VALUE="$BRANCH"\n') is None
    assert override_configuration_key("VALUE=topic#literal\n") == override_configuration_key(
        "VALUE='topic#literal'\n"
    )


@pytest.mark.parametrize("full_filename", [True, False])
def test_override_color_key_cache_and_later_gather(tmp_path, full_filename):
    cache = ShaInfoCache(tmp_path, tmp_path / "vault")
    checksum = "test_override_checksum_long"
    assert cache.get_override_color_key(checksum) == "checksum:" + checksum
    filename = checksum if full_filename else checksum[:12]
    (tmp_path / filename).write_text("PUPPET_BRANCH=topic\n")
    expected = override_configuration_key("PUPPET_BRANCH=topic\n")
    assert cache.get_override_color_key(checksum) == expected
    # Immutable checksum-named files are parsed only once.
    (tmp_path / filename).unlink()
    assert cache.get_override_color_key(checksum) == expected


def test_override_color_key_falls_back_for_unreadable_or_dynamic_files(tmp_path):
    cache = ShaInfoCache(tmp_path, tmp_path / "vault")
    (tmp_path / "dynamic").write_text("PUPPET_BRANCH=$BRANCH\n")
    (tmp_path / "invalid").write_bytes(b"\xff")
    for checksum in ("dynamic", "invalid", "missing"):
        assert cache.get_override_color_key(checksum) == "checksum:" + checksum


@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_parse_override_literal_assignments(tmp_path, quote):
    override_file = tmp_path / "override"
    override_file.write_text(
        "# PUPPET_BRANCH=ignored\n"
        f"PUPPET_REPO={quote}https://github.com/aerickson/ronin_puppet-private.git{quote}\n"
        f"PUPPET_BRANCH={quote}20260922-harden_puppet{quote} # branch comment\n"
        f"WORKER_TYPE_OVERRIDE={quote}gecko-t-linux-talos-2404-relops-aje{quote}\n"
    )
    assert parse_override_file(override_file) == {
        "user": "aerickson",
        "repo": "ronin_puppet-private",
        "branch": "20260922-harden_puppet",
        "worker_type_override": "gecko-t-linux-talos-2404-relops-aje",
    }


@pytest.mark.parametrize(
    "assignment",
    ["'unterminated", "\"mismatched'", "$(echo branch)", "$BRANCH", "branch; echo other"],
)
def test_parse_override_rejects_nonliteral_branch(tmp_path, assignment):
    override_file = tmp_path / "override"
    override_file.write_text(f"PUPPET_BRANCH={assignment}\n")
    assert parse_override_file(override_file) is None


def test_parse_override_export_and_literal_hash(tmp_path):
    override_file = tmp_path / "override"
    override_file.write_text(
        "  export PUPPET_BRANCH=topic#literal  # comment\n  # WORKER_TYPE_OVERRIDE=ignored\n"
    )
    result = parse_override_file(override_file)
    assert result is not None
    assert result["branch"] == "topic#literal"
    assert result["worker_type_override"] is None


def test_parse_override_file_valid():
    """Test parsing a valid override file."""
    with TemporaryDirectory() as tmpdir:
        override_file = Path(tmpdir) / "test_override"
        override_file.write_text(
            """# Comment
PUPPET_REPO='https://github.com/rcurranmoz/ronin_puppet.git'
PUPPET_BRANCH='disable_spotlight_2'
PUPPET_MAIL='rcurran@mozilla.com'
"""
        )
        result = parse_override_file(override_file)
        assert result is not None
        assert result["user"] == "rcurranmoz"
        assert result["repo"] == "ronin_puppet"
        assert result["branch"] == "disable_spotlight_2"
        # No WORKER_TYPE_OVERRIDE line present
        assert result["worker_type_override"] is None


def test_parse_override_file_worker_type_override():
    """Test extracting an active WORKER_TYPE_OVERRIDE pool."""
    with TemporaryDirectory() as tmpdir:
        override_file = Path(tmpdir) / "test_override"
        override_file.write_text(
            """PUPPET_REPO="https://github.com/mozilla-platform-ops/ronin_puppet.git"
PUPPET_BRANCH="052226-linux-moonshots-perf-sudoers"
WORKER_TYPE_OVERRIDE='gecko-t-linux-talos-2404-bug2031822'
"""
        )
        result = parse_override_file(override_file)
        assert result is not None
        assert result["worker_type_override"] == "gecko-t-linux-talos-2404-bug2031822"


def test_parse_override_file_commented_worker_type_override():
    """Test that a commented-out WORKER_TYPE_OVERRIDE is ignored."""
    with TemporaryDirectory() as tmpdir:
        override_file = Path(tmpdir) / "test_override"
        override_file.write_text(
            """PUPPET_REPO="https://github.com/mozilla-platform-ops/ronin_puppet.git"
PUPPET_BRANCH="RELOPS-snmp-gw-pool-id-linux"
# WORKER_TYPE_OVERRIDE='gecko-t-linux-talos-1804-staging'
"""
        )
        result = parse_override_file(override_file)
        assert result is not None
        assert result["worker_type_override"] is None


def test_parse_override_file_double_quotes():
    """Test parsing override file with double quotes."""
    with TemporaryDirectory() as tmpdir:
        override_file = Path(tmpdir) / "test_override"
        override_file.write_text(
            """PUPPET_REPO="https://github.com/user123/repo.git"
PUPPET_BRANCH="my-branch"
"""
        )
        result = parse_override_file(override_file)
        assert result is not None
        assert result["user"] == "user123"
        assert result["repo"] == "repo"
        assert result["branch"] == "my-branch"


def test_parse_override_file_missing_repo():
    """Test parsing override file missing PUPPET_REPO returns branch anyway."""
    with TemporaryDirectory() as tmpdir:
        override_file = Path(tmpdir) / "test_override"
        override_file.write_text("PUPPET_BRANCH='branch'\n")
        result = parse_override_file(override_file)
        assert result is not None
        assert result["branch"] == "branch"
        assert result["user"] is None
        assert result["repo"] is None


def test_parse_override_file_missing_branch():
    """Test parsing override file missing PUPPET_BRANCH."""
    with TemporaryDirectory() as tmpdir:
        override_file = Path(tmpdir) / "test_override"
        override_file.write_text("PUPPET_REPO='https://github.com/user/repo.git'\n")
        result = parse_override_file(override_file)
        assert result is None


def test_parse_override_file_non_github_url():
    """Test parsing override file with non-GitHub URL returns branch anyway."""
    with TemporaryDirectory() as tmpdir:
        override_file = Path(tmpdir) / "test_override"
        override_file.write_text(
            """PUPPET_REPO='https://example.com/repo.git'
PUPPET_BRANCH='my-branch'
"""
        )
        result = parse_override_file(override_file)
        assert result is not None
        assert result["branch"] == "my-branch"
        assert result["user"] is None


def test_parse_override_file_no_repo():
    """Test parsing override file with no PUPPET_REPO still works."""
    with TemporaryDirectory() as tmpdir:
        override_file = Path(tmpdir) / "test_override"
        override_file.write_text("PUPPET_BRANCH='branch'\n")
        result = parse_override_file(override_file)
        assert result is not None
        assert result["branch"] == "branch"
        assert result["user"] is None


def test_parse_override_file_not_exists():
    """Test parsing non-existent override file."""
    result = parse_override_file(Path("/nonexistent/file"))
    assert result is None


def test_parse_override_file_commented_out_repo():
    """Test that commented-out PUPPET_REPO lines are ignored."""
    with TemporaryDirectory() as tmpdir:
        override_file = Path(tmpdir) / "test_override"
        override_file.write_text(
            '# PUPPET_REPO="https://github.com/mozilla-platform-ops/ronin_puppet.git"\n'
            'PUPPET_REPO="https://github.com/aerickson/ronin_puppet.git"\n'
            'PUPPET_BRANCH="my-fork-branch"\n'
        )
        result = parse_override_file(override_file)
        assert result is not None
        assert result["user"] == "aerickson"
        assert result["branch"] == "my-fork-branch"


def test_find_vault_symlink_exists():
    """Test finding a vault symlink that exists."""
    with TemporaryDirectory() as tmpdir:
        vault_dir = Path(tmpdir)
        # Create a target file
        target = vault_dir / "abc123456789"  # pragma: allowlist secret
        target.write_text("dummy vault content")
        # Create a symlink
        symlink = vault_dir / "prod-db"
        symlink.symlink_to(target.name)

        result = find_vault_symlink("abc123456789", vault_dir)
        assert result == "prod-db"


def test_find_vault_symlink_prefix_match():
    """Test finding symlink with 12-char SHA prefix."""
    with TemporaryDirectory() as tmpdir:
        vault_dir = Path(tmpdir)
        target = vault_dir / "abc123456789abcd"  # pragma: allowlist secret
        target.write_text("dummy vault content")
        symlink = vault_dir / "staging"
        symlink.symlink_to(target.name)

        # Should match with just the first 12 chars
        result = find_vault_symlink("abc123456789", vault_dir)
        assert result == "staging"


def test_find_vault_symlink_not_found():
    """Test finding symlink that doesn't exist."""
    with TemporaryDirectory() as tmpdir:
        vault_dir = Path(tmpdir)
        target = vault_dir / "abc123456789"  # pragma: allowlist secret
        target.write_text("dummy vault content")
        symlink = vault_dir / "prod"
        symlink.symlink_to(target.name)

        result = find_vault_symlink("xyz987654321", vault_dir)
        assert result is None


def test_find_vault_symlink_dir_not_exists():
    """Test finding symlink in non-existent directory."""
    result = find_vault_symlink("abc123", Path("/nonexistent"))
    assert result is None


def test_sha_info_cache_override_info():
    """Test cache returns override info correctly."""
    with TemporaryDirectory() as tmpdir:
        overrides_dir = Path(tmpdir) / "overrides"
        overrides_dir.mkdir()
        vault_dir = Path(tmpdir) / "vaults"
        vault_dir.mkdir()

        # Create override file
        override_file = overrides_dir / "abc123456789"  # pragma: allowlist secret
        override_file.write_text(
            """PUPPET_REPO='https://github.com/testuser/puppet.git'
PUPPET_BRANCH='test-branch'
"""
        )

        cache = ShaInfoCache(overrides_dir, vault_dir)
        cache.load_all()

        result = cache.get_override_info("abc123456789")
        assert result == "test-branch"


def test_sha_info_cache_override_fallback():
    """Test cache returns dash for missing override."""
    with TemporaryDirectory() as tmpdir:
        overrides_dir = Path(tmpdir) / "overrides"
        overrides_dir.mkdir()
        vault_dir = Path(tmpdir) / "vaults"
        vault_dir.mkdir()

        cache = ShaInfoCache(overrides_dir, vault_dir)
        cache.load_all()

        result = cache.get_override_info("nonexistent")
        assert result == "-"


def test_sha_info_cache_vault_info():
    """Test cache returns vault symlink name correctly."""
    with TemporaryDirectory() as tmpdir:
        overrides_dir = Path(tmpdir) / "overrides"
        overrides_dir.mkdir()
        vault_dir = Path(tmpdir) / "vaults"
        vault_dir.mkdir()

        # Create vault symlink
        target = vault_dir / "xyz987654321"
        target.write_text("vault content")
        symlink = vault_dir / "production"
        symlink.symlink_to(target.name)

        cache = ShaInfoCache(overrides_dir, vault_dir)
        cache.load_all()

        result = cache.get_vault_info("xyz987654321")
        assert result == "production"


def test_sha_info_cache_vault_fallback():
    """Test cache returns dash for missing vault symlink."""
    with TemporaryDirectory() as tmpdir:
        overrides_dir = Path(tmpdir) / "overrides"
        overrides_dir.mkdir()
        vault_dir = Path(tmpdir) / "vaults"
        vault_dir.mkdir()

        cache = ShaInfoCache(overrides_dir, vault_dir)
        cache.load_all()

        result = cache.get_vault_info("nonexistent")
        assert result == "-"


def test_sha_info_cache_lazy_lookup():
    """Test cache does lazy lookup for SHAs not in initial load."""
    with TemporaryDirectory() as tmpdir:
        overrides_dir = Path(tmpdir) / "overrides"
        overrides_dir.mkdir()
        vault_dir = Path(tmpdir) / "vaults"
        vault_dir.mkdir()

        cache = ShaInfoCache(overrides_dir, vault_dir)
        cache.load_all()

        # Create override file after initial load (using 12-char prefix that matches lookup)
        override_file = overrides_dir / "late12345678"
        override_file.write_text(
            """PUPPET_REPO='https://github.com/lateuser/repo.git'
PUPPET_BRANCH='late-branch'
"""
        )

        # Should find it via lazy lookup
        result = cache.get_override_info("late12345678abcdef")
        assert result == "late-branch"


def test_sha_info_cache_empty_sha():
    """Test cache handles empty SHA gracefully."""
    with TemporaryDirectory() as tmpdir:
        overrides_dir = Path(tmpdir) / "overrides"
        overrides_dir.mkdir()
        vault_dir = Path(tmpdir) / "vaults"
        vault_dir.mkdir()

        cache = ShaInfoCache(overrides_dir, vault_dir)
        cache.load_all()

        assert cache.get_override_info("") == "-"
        assert cache.get_vault_info("") == "-"
