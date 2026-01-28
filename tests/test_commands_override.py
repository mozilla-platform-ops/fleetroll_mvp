"""Tests for fleetroll/commands/override.py - show-override command."""

from __future__ import annotations

from pathlib import Path

import pytest
from fleetroll.cli import Args
from fleetroll.commands.override import cmd_override_show, resolve_override_humanhash
from fleetroll.exceptions import UserError
from fleetroll.humanhash import humanize
from fleetroll.utils import sha256_hex


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
    args = Args(sha_prefix=h, audit_log=str(tmp_dir / ".fleetroll" / "audit.jsonl"))

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
