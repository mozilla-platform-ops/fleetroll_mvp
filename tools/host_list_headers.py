"""Shared header helpers for generated host list files."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

VOLATILE_HEADER_PREFIXES = ("# Generated:", "# Source revision:")


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


def local_source_revision(*, cwd: Path | None = None) -> str:
    """Return a compact local git source description for generated headers."""
    repo = cwd or Path.cwd()
    branch = _run_git(["branch", "--show-current"], cwd=repo)
    commit = _run_git(["rev-parse", "--short", "HEAD"], cwd=repo)
    dirty = bool(_run_git(["status", "--porcelain"], cwd=repo))

    parts = []
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


def remote_source_revision(*, repo: str, branch: str | None, commit: str | None) -> str:
    """Return a compact remote source description for generated headers."""
    parts = [f"repo={repo}"]
    if branch:
        parts.append(f"branch={branch}")
    if commit:
        parts.append(f"commit={commit[:12]}")
    return " ".join(parts)


def strip_volatile_header_lines(content: str) -> list[str]:
    """Return file lines excluding header metadata that should not force rewrites."""
    return [line for line in content.splitlines() if not line.startswith(VOLATILE_HEADER_PREFIXES)]


def content_changed(existing: str, new: str) -> bool:
    """Compare generated files while ignoring volatile source/timestamp headers."""
    return strip_volatile_header_lines(existing) != strip_volatile_header_lines(new)


def write_if_changed(path: Path, content: str) -> bool:
    """Write content only when non-volatile generated output changed."""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if not content_changed(existing, content):
            return False
    path.write_text(content, encoding="utf-8")
    return True
