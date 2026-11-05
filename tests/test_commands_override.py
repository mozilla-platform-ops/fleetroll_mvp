"""Tests for fleetroll/commands/override.py - show-override command."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fleetroll.cli_types import OverrideShowArgs
from fleetroll.commands.override import cmd_override_show, resolve_override_humanhash
from fleetroll.exceptions import UserError
from fleetroll.humanhash import humanize
from fleetroll.utils import sha256_hex


def _show(query: str, audit: Path):
    return subprocess.run(
        [sys.executable, "-m", "fleetroll", "override", "show", query, "--audit-log", str(audit)],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_override(overrides_dir: Path, content: str) -> str:
    data = content.encode("utf-8")
    sha = sha256_hex(data)
    path = overrides_dir / sha[:12]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha


def test_override_show_humanhash(tmp_dir: Path, mocker) -> None:
    overrides_dir = tmp_dir / ".fleetroll" / "overrides"
    sha = _write_override(overrides_dir, "hello\n")
    h = humanize(sha, words=2)
    args = OverrideShowArgs(sha_prefix=h, audit_log=str(tmp_dir / ".fleetroll" / "audit.jsonl"))

    captured = []
    mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

    cmd_override_show(args)

    assert captured[0].startswith("hello")


def test_override_show_humanhash_ambiguous(tmp_dir: Path, monkeypatch) -> None:
    overrides_dir = tmp_dir / ".fleetroll" / "overrides"
    _write_override(overrides_dir, "one\n")
    _write_override(overrides_dir, "two\n")

    monkeypatch.setattr("fleetroll.commands.override.humanize", lambda *_a, **_k: "same-hash")

    with pytest.raises(UserError) as excinfo:
        resolve_override_humanhash("same-hash", overrides_dir=overrides_dir)

    assert "Ambiguous human-hash" in str(excinfo.value)


def test_show_reports_missing_directory_and_missing_file(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    result = _show("absent", audit)
    assert result.returncode == 2
    assert "Overrides directory not found" in result.stderr

    (tmp_path / "overrides").mkdir()
    result = _show("absent", audit)
    assert result.returncode == 2
    assert "No override file found" in result.stderr


def test_show_refuses_ambiguous_prefix_without_humanhash_fallback(tmp_path: Path) -> None:
    directory = tmp_path / "overrides"
    directory.mkdir()
    (directory / "test_one").write_text("first")
    (directory / "test_two").write_text("second")
    result = _show("test_", tmp_path / "audit.jsonl")
    assert result.returncode == 2
    assert "Ambiguous prefix" in result.stderr
    assert "test_one" in result.stderr
    assert "test_two" in result.stderr
    assert not result.stdout


def test_show_excludes_symlinks_for_prefix_and_humanhash(tmp_path: Path) -> None:
    directory = tmp_path / "overrides"
    directory.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("linked content")
    (directory / "linked").symlink_to(outside)
    for query in ("linked", humanize(sha256_hex(outside.read_bytes()), words=2)):
        result = _show(query, tmp_path / "audit.jsonl")
        assert result.returncode == 2
        assert "No override file found" in result.stderr
        assert not result.stdout


def test_prefix_match_takes_precedence_over_humanhash(tmp_path: Path) -> None:
    directory = tmp_path / "overrides"
    directory.mkdir()
    sha = _write_override(directory, "other content")
    query = humanize(sha, words=2)
    (directory / query).write_text("prefix content")
    result = _show(query, tmp_path / "audit.jsonl")
    assert result.returncode == 0
    assert result.stdout == "prefix content"


def test_humanhash_lookup_read_error_does_not_select_another_file(
    tmp_path: Path, monkeypatch
) -> None:
    directory = tmp_path / "overrides"
    directory.mkdir()
    broken = directory / "unreadable"
    broken.write_text("content")
    _write_override(directory, "unrelated content")
    original = Path.read_bytes

    def read_bytes(path):
        if path == broken:
            raise PermissionError("cannot read candidate")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    with pytest.raises(PermissionError, match="cannot read candidate"):
        resolve_override_humanhash("missing-hash", overrides_dir=directory)
