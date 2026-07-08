"""Shared header helpers for generated host list files."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

VOLATILE_HEADER_PREFIXES = ("# Source revision:",)
SOURCE_REVISION_PREFIX = "# Source revision:"


def _run_git(args: list[str], *, cwd: Path) -> str | None:
    git = shutil.which("git")
    if git is None:
        return None

    result = subprocess.run(
        [git, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def local_source_revision(*, cwd: Path | None = None, repo: str | None = None) -> str:
    """Return a compact local git source description for generated headers."""
    repo_path = cwd or Path.cwd()
    branch = _run_git(["branch", "--show-current"], cwd=repo_path)
    commit = _run_git(["rev-parse", "--short", "HEAD"], cwd=repo_path)
    dirty = bool(_run_git(["status", "--porcelain", "--untracked-files=no"], cwd=repo_path))

    parts = []
    if repo:
        parts.append(f"repo={repo}")
    if branch:
        parts.append(f"branch={branch}")
    else:
        parts.append("branch=detached")
    if commit:
        parts.append(f"commit={commit}")
    if dirty:
        parts.append("dirty")
    else:
        parts.append("clean")
    return " ".join(parts)


def source_revision_from_content(content: str) -> str | None:
    """Extract source revision metadata from generated file content."""
    for line in content.splitlines():
        if line.startswith(SOURCE_REVISION_PREFIX):
            return line.removeprefix(SOURCE_REVISION_PREFIX).strip() or None
    return None


def source_revision_from_file(path: Path) -> str | None:
    """Extract source revision metadata from a generated file."""
    return source_revision_from_content(path.read_text(encoding="utf-8"))


def strip_volatile_header_lines(content: str) -> list[str]:
    """Return file lines excluding header metadata that should not force rewrites."""
    return [line for line in content.splitlines() if not line.startswith(VOLATILE_HEADER_PREFIXES)]


def content_changed(existing: str, new: str) -> bool:
    """Compare generated files while ignoring volatile source revision headers."""
    return strip_volatile_header_lines(existing) != strip_volatile_header_lines(new)


def write_if_changed(path: Path, content: str, *, force: bool = False) -> bool:
    """Write content when forced or when non-volatile generated output changed."""
    if force:
        path.write_text(content, encoding="utf-8")
        return True
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if not content_changed(existing, content):
            return False
    path.write_text(content, encoding="utf-8")
    return True
