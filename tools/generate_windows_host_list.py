#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Generate configs/host-lists/windows/all.list from mozilla-platform-ops/worker-images pools.yml.

Fetches pools.yml via `gh api` (requires GitHub CLI, already authenticated),
parses all Windows pools, and writes a host list file with pool grouping comments.
Known-BAD hosts are included with annotating comments.

Usage:
    uv run tools/generate_windows_host_list.py
    uv run tools/generate_windows_host_list.py --force
"""

from __future__ import annotations

import argparse
import base64
import datetime
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO = "mozilla-platform-ops/worker-images"
POOLS_PATH = "provisioners/windows/MDC1Windows/pools.yml"
OUTPUT_PATH = Path("configs/host-lists/windows/all.list")


def fetch_pools_yaml() -> str:
    """Fetch pools.yml content from GitHub via gh api."""
    gh = shutil.which("gh") or "gh"
    result = subprocess.run(
        [gh, "api", f"repos/{REPO}/contents/{POOLS_PATH}", "--jq", ".content"],
        capture_output=True,
        text=True,
        check=True,
    )
    encoded = result.stdout.strip()
    # GitHub returns base64 with newlines; strip them before decoding
    return base64.b64decode(encoded.replace("\n", "")).decode("utf-8")


def parse_known_bad_comments(raw_yaml: str) -> dict[str, str]:
    """Extract Known-BAD hosts with their preceding ## comments from raw YAML text.

    Returns a dict mapping hostname -> comment string (or empty string if none).
    """
    # Find the Known-BAD block (everything after "Known-BAD:")
    m = re.search(r"^Known-BAD:\s*\n(.*)", raw_yaml, re.MULTILINE | re.DOTALL)
    if not m:
        return {}

    block = m.group(1)
    comments: dict[str, str] = {}
    pending_comments: list[str] = []

    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            # Accumulate comment lines (strip leading "## ")
            comment_text = stripped.lstrip("#").strip()
            if comment_text:
                pending_comments.append(comment_text)
        elif stripped.startswith("- "):
            host = stripped[2:].strip()
            comments[host] = "; ".join(pending_comments) if pending_comments else ""
            pending_comments = []
        else:
            # Non-comment, non-entry line (e.g., hw class key like "nuc13:") resets
            pending_comments = []

    return comments


def natural_sort_key(text: str) -> list[int | str]:
    """Natural sort key: sorts t-nuc12-* before nuc13-*, then numerically within each."""

    def convert(part: str) -> int | str:
        return int(part) if part.isdigit() else part.lower()

    # Prefix t-nuc12 with "0" and nuc13 with "1" to force correct group order
    if text.startswith("t-nuc12"):
        prefix = "0"
    elif text.startswith("nuc13"):
        prefix = "1"
    else:
        prefix = "9"

    return [prefix] + [convert(c) for c in re.split(r"(\d+)", text)]


def generate_host_list(raw_yaml: str, generated_at: datetime.datetime | None = None) -> str:
    """Parse pools.yml and return the host list file content."""
    data = yaml.safe_load(raw_yaml)
    pools = data.get("pools", [])
    known_bad_comments = parse_known_bad_comments(raw_yaml)

    # Collect known-bad hostnames (from parsed YAML structure)
    known_bad_hosts: set[str] = set()
    known_bad_section = data.get("Known-BAD", {})
    for nodes in known_bad_section.values():
        if isinstance(nodes, list):
            for node in nodes:
                if node:
                    known_bad_hosts.add(str(node))

    # Collect all nodes per pool (deduplication across pools via seen set)
    seen: set[str] = set()
    pool_entries: list[tuple[str, str, list[str]]] = []  # (pool_name, domain_suffix, [nodes])

    for pool in pools:
        pool_name = pool.get("name", "unknown")
        domain_suffix = pool.get("domain_suffix", "")
        nodes = pool.get("nodes") or []
        pool_nodes = []
        for node in nodes:
            node = str(node)
            if node not in seen:
                seen.add(node)
                pool_nodes.append(node)
        if pool_nodes:
            pool_entries.append((pool_name, domain_suffix, pool_nodes))

    # Collect known-bad hosts that aren't already in any pool
    extra_bad: list[str] = [h for h in known_bad_hosts if h not in seen]

    if generated_at is None:
        generated_at = datetime.datetime.now(datetime.UTC)
    timestamp = generated_at.strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        "# #############################################################",
        "# THIS FILE IS AUTO-GENERATED. DO NOT EDIT MANUALLY.",
        f"# Generated: {timestamp}",
        "# Source:    mozilla-platform-ops/worker-images pools.yml",
        "# Regenerate: uv run tools/generate_windows_host_list.py",
        "# #############################################################",
        "",
    ]

    for pool_name, domain_suffix, nodes in pool_entries:
        lines.append(f"# pool: {pool_name}")
        if domain_suffix:
            lines.append(f"# fqdn: {domain_suffix}")
        sorted_nodes = sorted(nodes, key=natural_sort_key)
        for node in sorted_nodes:
            if node in known_bad_hosts:
                reason = known_bad_comments.get(node, "see pools.yml Known-BAD")
                lines.append(f"# known-bad: {reason}")
            lines.append(node)
        lines.append("")

    # Add known-bad hosts that don't appear in any pool (orphaned entries)
    if extra_bad:
        lines.append("# known-bad hosts not in any pool")
        for node in sorted(extra_bad, key=natural_sort_key):
            reason = known_bad_comments.get(node, "see pools.yml Known-BAD")
            lines.append(f"# known-bad: {reason}")
            lines.append(node)
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    """Fetch pools.yml and write configs/host-lists/windows/all.list."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if the output file was updated within the last 60 minutes",
    )
    args = parser.parse_args()

    if not args.force and OUTPUT_PATH.exists():
        age_seconds = time.time() - OUTPUT_PATH.stat().st_mtime
        if age_seconds < 3600:
            remaining = int((3600 - age_seconds) / 60)
            print(
                f"Skipping: {OUTPUT_PATH} was updated {int(age_seconds / 60)}m ago "
                f"(use --force to regenerate, or wait {remaining}m)",
                file=sys.stderr,
            )
            return

    print(f"Fetching {POOLS_PATH} from {REPO}...", file=sys.stderr)
    try:
        raw_yaml = fetch_pools_yaml()
    except subprocess.CalledProcessError as e:
        print(f"Error: gh api failed: {e.stderr}", file=sys.stderr)
        sys.exit(1)

    content = generate_host_list(raw_yaml, generated_at=datetime.datetime.now(datetime.UTC))

    output = OUTPUT_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")

    # Count non-comment, non-blank lines
    host_lines = [ln for ln in content.splitlines() if ln and not ln.startswith("#")]
    print(f"Wrote {len(host_lines)} hosts to {output}", file=sys.stderr)


if __name__ == "__main__":
    main()
