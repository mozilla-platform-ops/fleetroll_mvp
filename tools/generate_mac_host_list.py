#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Generate configs/host-lists/mac/<group>.list from mozilla-platform-ops/ronin_puppet inventory.d.

Fetches inventory YAML files via `gh api` (requires GitHub CLI, already authenticated),
parses all Mac groups, and writes one host list file per group.

Usage:
    uv run tools/generate_mac_host_list.py
    uv run tools/generate_mac_host_list.py --force
"""

from __future__ import annotations

import argparse
import base64
import datetime
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from natural_sort import natural_key

REPO = "mozilla-platform-ops/ronin_puppet"
INVENTORY_DIR = "inventory.d"
OUTPUT_DIR = Path("configs/host-lists/mac")
IGNORE_FILES = {"services.yaml"}


def fetch_inventory_listing() -> list[str]:
    """Fetch list of inventory YAML filenames from GitHub."""
    gh = shutil.which("gh") or "gh"
    result = subprocess.run(
        [gh, "api", f"repos/{REPO}/contents/{INVENTORY_DIR}", "--jq", ".[].name"],
        capture_output=True,
        text=True,
        check=True,
    )
    names = [n.strip() for n in result.stdout.splitlines() if n.strip()]
    return [n for n in names if n.endswith(".yaml") and n not in IGNORE_FILES]


def fetch_file_content(path: str) -> str:
    """Fetch and decode a file from GitHub via gh api."""
    gh = shutil.which("gh") or "gh"
    result = subprocess.run(
        [gh, "api", f"repos/{REPO}/contents/{path}", "--jq", ".content"],
        capture_output=True,
        text=True,
        check=True,
    )
    encoded = result.stdout.strip()
    return base64.b64decode(encoded.replace("\n", "")).decode("utf-8")


def parse_inventory(raw_yaml: str) -> list[dict]:
    """Parse inventory YAML and return the groups list."""
    data = yaml.safe_load(raw_yaml)
    return data.get("groups", [])


def generate_group_file(
    group: dict, *, inventory_name: str, generated_at: datetime.datetime
) -> str | None:
    """Build file content for one inventory group."""
    targets = group.get("targets") or []
    facts = group.get("facts") or {}
    puppet_role = facts.get("puppet_role", "")

    timestamp = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# #############################################################",
        "# THIS FILE IS AUTO-GENERATED. DO NOT EDIT MANUALLY.",
        f"# Generated: {timestamp}",
        f"# Source:    mozilla-platform-ops/ronin_puppet inventory.d/{inventory_name}",
        "# Regenerate: uv run tools/generate_mac_host_list.py",
        "# #############################################################",
        "",
        f"# inventory: {inventory_name}",
    ]

    if puppet_role:
        lines.append(f"# puppet_role: {puppet_role}")

    lines.append("")

    filtered = [t for t in targets if not str(t).endswith(".local")]
    if not filtered:
        return None

    sorted_targets = sorted(filtered, key=natural_key)
    lines.extend(str(t) for t in sorted_targets)

    lines.append("")

    return "\n".join(lines)


def main() -> None:
    """Fetch inventory files and write one .list file per group to OUTPUT_DIR."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if output files were updated within the last 60 minutes",
    )
    args = parser.parse_args()

    if not args.force and OUTPUT_DIR.exists():
        # Check the most recently modified .list file
        list_files = list(OUTPUT_DIR.glob("*.list"))
        if list_files:
            newest_mtime = max(f.stat().st_mtime for f in list_files)
            age_seconds = time.time() - newest_mtime
            if age_seconds < 3600:
                remaining = int((3600 - age_seconds) / 60)
                print(
                    f"Skipping: {OUTPUT_DIR}/*.list was updated {int(age_seconds / 60)}m ago "
                    f"(use --force to regenerate, or wait {remaining}m)",
                    file=sys.stderr,
                )
                return

    print(f"Fetching inventory listing from {REPO}/{INVENTORY_DIR}...", file=sys.stderr)
    try:
        filenames = fetch_inventory_listing()
    except subprocess.CalledProcessError as e:
        print(f"Error: gh api failed: {e.stderr}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(filenames)} inventory files: {', '.join(filenames)}", file=sys.stderr)

    generated_at = datetime.datetime.now(datetime.UTC)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total_hosts = 0
    total_groups = 0

    for filename in filenames:
        path = f"{INVENTORY_DIR}/{filename}"
        print(f"Fetching {path}...", file=sys.stderr)
        try:
            raw_yaml = fetch_file_content(path)
        except subprocess.CalledProcessError as e:
            print(f"Error: gh api failed for {path}: {e.stderr}", file=sys.stderr)
            sys.exit(1)

        groups = parse_inventory(raw_yaml)
        for group in groups:
            group_name = group.get("name", "unknown")
            content = generate_group_file(group, inventory_name=filename, generated_at=generated_at)
            out_path = OUTPUT_DIR / f"{group_name}.list"

            if content is None:
                if out_path.exists():
                    out_path.unlink()
                    print(f"  Removed {out_path} (empty after filtering)", file=sys.stderr)
                else:
                    print(f"  Skipped {group_name} (empty after filtering)", file=sys.stderr)
                continue

            out_path.write_text(content, encoding="utf-8")

            host_count = len(group.get("targets") or [])
            total_hosts += host_count
            total_groups += 1
            print(f"  Wrote {host_count} hosts to {out_path}", file=sys.stderr)

    print(
        f"Done: {total_groups} group files, {total_hosts} total hosts in {OUTPUT_DIR}/",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
