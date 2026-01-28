"""Tests for show-vault command."""

from __future__ import annotations

from pathlib import Path

import pytest
from fleetroll.cli import Args
from fleetroll.commands.vault import cmd_vault_show, resolve_vault_humanhash
from fleetroll.exceptions import UserError
from fleetroll.humanhash import humanize
from fleetroll.utils import sha256_hex


def _write_vault(vault_dir: Path, content: str) -> str:
    data = content.encode("utf-8")
    sha = sha256_hex(data)
    path = vault_dir / sha[:12]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha


def test_vault_show_humanhash(tmp_dir: Path, mocker) -> None:
    vault_dir = tmp_dir / ".fleetroll" / "vault_yamls"
    sha = _write_vault(vault_dir, "vault: hello\n")
    h = humanize(sha, words=2)
    args = Args(sha_prefix=h, audit_log=str(tmp_dir / ".fleetroll" / "audit.jsonl"))

    captured = []
    mocker.patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0]))

    cmd_vault_show(args)

    assert captured[0].startswith("vault:")


def test_vault_show_humanhash_ambiguous(tmp_dir: Path, monkeypatch) -> None:
    vault_dir = tmp_dir / ".fleetroll" / "vault_yamls"
    _write_vault(vault_dir, "one\n")
    _write_vault(vault_dir, "two\n")

    monkeypatch.setattr("fleetroll.commands.vault.humanize", lambda *_a, **_k: "same-hash")

    with pytest.raises(UserError) as excinfo:
        resolve_vault_humanhash("same-hash", vault_dir=vault_dir)

    assert "Ambiguous human-hash" in str(excinfo.value)
