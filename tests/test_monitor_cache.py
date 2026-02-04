"""Tests for monitor SHA info cache."""

from pathlib import Path
from tempfile import TemporaryDirectory

from fleetroll.commands.monitor.cache import (
    ShaInfoCache,
    find_vault_symlink,
    parse_override_file,
)


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
        assert result["branch"] == "disable_spotlight_2"


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
