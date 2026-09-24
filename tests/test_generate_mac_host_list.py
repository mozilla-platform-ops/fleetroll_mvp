"""Behavioral tests for the macOS inventory list generator."""

from __future__ import annotations

import sys
from pathlib import Path

from tools import generate_mac_host_list as generator


def test_custom_inventory_skips_comments_and_deduplicates(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    inventory = tmp_path / "checkout" / "inventory.d"
    inventory.mkdir(parents=True)
    (inventory / "mac.yaml").write_text(
        """groups:
  - name: gecko-t-osx
    facts:
      puppet_role: gecko_t_osx
    targets:
      - mac-10.example.test
      # - mac-disabled.example.test
      - mac-2.example.test
      - mac-2.example.test
      - "# mac-quoted-comment.example.test"
      - mac-3.local
""",
        encoding="utf-8",
    )
    output = tmp_path / "output"
    monkeypatch.setattr(generator, "OUTPUT_DIR", output)
    monkeypatch.setattr(generator, "local_source_revision", lambda **_kwargs: "test revision")
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate_mac_host_list.py", "--inventory-path", str(inventory)],
    )

    generator.main()

    content = (output / "gecko-t-osx.list").read_text(encoding="utf-8")
    hosts = [line for line in content.splitlines() if line and not line.startswith("#")]
    assert hosts == ["mac-2.example.test", "mac-10.example.test"]
    assert "2 total hosts" in capsys.readouterr().err


def test_inventory_path_environment_default_and_flag_precedence(
    monkeypatch, tmp_path: Path
) -> None:
    env_inventory = tmp_path / "env" / "inventory.d"
    flag_inventory = tmp_path / "flag" / "inventory.d"
    env_inventory.mkdir(parents=True)
    flag_inventory.mkdir(parents=True)
    (env_inventory / "mac.yaml").write_text(
        "groups:\n  - name: env-group\n    targets: [env.example.test]\n", encoding="utf-8"
    )
    (flag_inventory / "mac.yaml").write_text(
        "groups:\n  - name: flag-group\n    targets: [flag.example.test]\n", encoding="utf-8"
    )
    output = tmp_path / "output"
    monkeypatch.setattr(generator, "OUTPUT_DIR", output)
    monkeypatch.setattr(generator, "local_source_revision", lambda **_kwargs: "test revision")
    monkeypatch.setenv("FLEETROLL_INVENTORY_PATH", str(env_inventory))

    monkeypatch.setattr(sys, "argv", ["generate_mac_host_list.py"])
    generator.main()
    assert (output / "env-group.list").exists()

    monkeypatch.setattr(
        sys,
        "argv",
        ["generate_mac_host_list.py", "--inventory-path", str(flag_inventory), "--force"],
    )
    generator.main()
    assert (output / "flag-group.list").exists()
