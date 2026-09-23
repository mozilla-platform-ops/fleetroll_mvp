"""SHA info caching for monitor display."""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast


def _literal_assignment(content: str, *, name: str) -> str | None:
    """Read a literal shell assignment without evaluating shell expressions."""
    pattern = (
        rf"^[ \t]*(?:export[ \t]+)?{re.escape(name)}="
        r"(?:'([^'\n]*)'|\"([^\"\n$`\\]*)\"|([^\s'\"$`\\;|&<>()]+))"
        r"(?:[ \t]+#[^\n]*)?[ \t]*$"
    )
    match = re.search(pattern, content, re.MULTILINE)
    if match is None:
        return None
    return next(value for value in match.groups() if value is not None) or None


def parse_override_file(path: Path) -> dict[str, str | None] | None:
    """Parse override file to extract user/branch/repo/worker-pool info.

    Args:
        path: Path to override file

    Returns:
        Dict with "user", "repo", "branch", and "worker_type_override" keys,
        or None on error. User and repo will be None if repo is not a GitHub
        URL. worker_type_override will be None if the file has no active
        (uncommented) WORKER_TYPE_OVERRIDE line.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    # Extract PUPPET_REPO and PUPPET_BRANCH
    repo_url = _literal_assignment(content, name="PUPPET_REPO")
    branch = _literal_assignment(content, name="PUPPET_BRANCH")

    if not branch:
        return None

    # Extract username and repo name from GitHub URL if available
    # e.g., https://github.com/rcurranmoz/ronin_puppet.git -> rcurranmoz, ronin_puppet
    user = None
    repo_name = None
    if repo_url:
        user_match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", repo_url)
        if user_match:
            user = user_match.group(1)
            repo_name = user_match.group(2)

    worker_type_override = _literal_assignment(content, name="WORKER_TYPE_OVERRIDE")

    return {
        "user": user,
        "repo": repo_name,
        "branch": branch,
        "worker_type_override": worker_type_override,
    }


def find_vault_symlink(sha256: str, vault_dir: Path) -> str | None:
    """Find symlink name pointing to the given SHA.

    Args:
        sha256: Full SHA256 hash or first 12 characters
        vault_dir: Directory containing vault files and symlinks

    Returns:
        Symlink name or None if not found
    """
    if not vault_dir.exists():
        return None

    # Use first 12 chars for comparison (matches display format)
    sha_prefix = sha256[:12]

    try:
        for item in vault_dir.iterdir():
            if item.is_symlink():
                try:
                    target = item.readlink()
                    # Target might be relative or just the filename
                    target_name = target.name if hasattr(target, "name") else str(target)
                    if target_name.startswith(sha_prefix):
                        return item.name
                except (OSError, ValueError):
                    continue
    except OSError:
        return None

    return None


class ShaInfoCache:
    """Cache for SHA256 to human-readable info mappings."""

    def __init__(self, overrides_dir: Path, vault_dir: Path) -> None:
        """Initialize cache with directory paths.

        Args:
            overrides_dir: Directory containing override files
            vault_dir: Directory containing vault files and symlinks
        """
        self.overrides_dir = overrides_dir
        self.vault_dir = vault_dir
        self.override_cache: dict[str, dict[str, str | None]] = {}
        self.vault_cache: dict[str, str] = {}

    def load_all(self) -> None:
        """Pre-populate caches by scanning directories."""
        # Load override files
        if self.overrides_dir.exists():
            try:
                for item in self.overrides_dir.iterdir():
                    if item.is_file():
                        sha = item.name
                        info = parse_override_file(item)
                        if info:
                            self.override_cache[sha] = info
            except OSError:
                pass

        # Load vault symlinks
        if self.vault_dir.exists():
            try:
                for item in self.vault_dir.iterdir():
                    if item.is_symlink():
                        try:
                            target = item.readlink()
                            target_name = target.name if hasattr(target, "name") else str(target)
                            # Store by full SHA name and 12-char prefix
                            self.vault_cache[target_name] = item.name
                            if len(target_name) >= 12:
                                self.vault_cache[target_name[:12]] = item.name
                        except (OSError, ValueError):
                            continue
            except OSError:
                pass

    def get_override_info(self, sha256: str) -> str:
        """Get human-readable override info for a SHA.

        Args:
            sha256: Full SHA256 hash

        Returns:
            Branch name or "-" if not found
        """
        if not sha256:
            return "-"

        # Check cache with full SHA and 12-char prefix
        sha_prefix = sha256[:12]
        info = self.override_cache.get(sha256) or self.override_cache.get(sha_prefix)

        if not info:
            # Try on-demand lookup
            override_file = self.overrides_dir / sha_prefix
            if override_file.exists():
                info = parse_override_file(override_file)
                if info:
                    self.override_cache[sha_prefix] = info

        if info:
            # branch is always str (never None) because parse_override_file returns
            # None if branch parsing fails, so when info exists, branch is always set
            return cast("str", info["branch"])
        return "-"

    def get_override_details(self, sha256: str) -> dict[str, str | None] | None:
        """Get full override details (user, repo, branch) for a SHA.

        Args:
            sha256: Full SHA256 hash

        Returns:
            Dict with "user", "repo", and "branch" keys, or None if not found
        """
        if not sha256:
            return None

        # Check cache with full SHA and 12-char prefix
        sha_prefix = sha256[:12]
        info = self.override_cache.get(sha256) or self.override_cache.get(sha_prefix)

        if not info:
            # Try on-demand lookup
            override_file = self.overrides_dir / sha_prefix
            if override_file.exists():
                info = parse_override_file(override_file)
                if info:
                    self.override_cache[sha_prefix] = info

        return info

    def get_vault_info(self, sha256: str) -> str:
        """Get human-readable vault info (symlink name) for a SHA.

        Args:
            sha256: Full SHA256 hash

        Returns:
            Symlink name or "-" if not found
        """
        if not sha256:
            return "-"

        # Check cache with full SHA and 12-char prefix
        sha_prefix = sha256[:12]
        symlink = self.vault_cache.get(sha256) or self.vault_cache.get(sha_prefix)

        if not symlink:
            # Try on-demand lookup
            symlink = find_vault_symlink(sha_prefix, self.vault_dir)
            if symlink:
                self.vault_cache[sha_prefix] = symlink

        return symlink or "-"
