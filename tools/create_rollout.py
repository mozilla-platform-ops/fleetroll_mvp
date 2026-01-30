#!/usr/bin/env python3
"""Create a rollout plan file from an override file.

This tool generates a structured rollout plan with multiple stages
for deploying changes to production infrastructure.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from getpass import getuser
from pathlib import Path


def parse_override_file(override_path: Path) -> dict[str, str]:
    """Parse override file and extract key variables.

    Args:
        override_path: Path to the override file

    Returns:
        Dictionary with extracted variables (branch, repo, mail)
    """
    data = {}
    content = override_path.read_text()

    # Extract PUPPET_BRANCH
    if match := re.search(r"PUPPET_BRANCH=['\"]?([^'\"]+)['\"]?", content):
        data["branch"] = match.group(1)

    # Extract PUPPET_REPO
    if match := re.search(r"PUPPET_REPO=['\"]?([^'\"]+)['\"]?", content):
        data["repo"] = match.group(1)

    # Extract PUPPET_MAIL
    if match := re.search(r"PUPPET_MAIL=['\"]?([^'\"]+)['\"]?", content):
        data["mail"] = match.group(1)

    return data


def show_preview(
    *,
    rollout_path: Path,
    branch: str,
    override_rel: str,
    vault_rel: str | None,
    repo: str | None,
    mail: str | None,
    user: str,
) -> None:
    """Display preview of the rollout plan that will be created."""
    print("=" * 60)
    print("Rollout Plan Preview")
    print("=" * 60)
    print()
    print(f"Will create: {rollout_path}")
    print()
    print("Metadata:")
    print(f"  Branch:        {branch}")
    print(f"  Created by:    {user}")
    print(f"  Date:          {datetime.now(tz=UTC).strftime('%Y-%m-%d')}")
    print(f"  Override file: {override_rel}")
    if vault_rel:
        print(f"  Vault file:    {vault_rel}")
    if repo:
        print(f"  Puppet repo:   {repo}")
    if mail:
        print(f"  Puppet email:  {mail}")
    print()
    print("Stages:")
    print("  1. Canary (small test sets)")
    print("  2. Broader canary")
    print("  3. Production rollout (batch 1)")
    print("  4. Production rollout (remaining)")
    print("  + Rollback section")
    print()
    print("=" * 60)
    print()


def create_rollout_file(
    *,
    rollout_path: Path,
    branch: str,
    override_rel: str,
    vault_rel: str | None,
    repo: str | None,
    user: str,
) -> None:
    """Create the rollout plan file.

    Args:
        rollout_path: Path where rollout file will be created
        branch: Branch name
        override_rel: Relative path to override file
        vault_rel: Relative path to vault file (optional)
        repo: Puppet repo URL (optional)
        user: Username creating the rollout
    """
    lines = [
        branch,
        "",
        f"Rollout plan created by: {user}",
        f"Date: {datetime.now(tz=UTC).strftime('%Y-%m-%d')}",
        f"Branch: {branch}",
        f"Override: {override_rel}",
    ]

    if vault_rel:
        lines.append(f"Vault: {vault_rel}")

    if repo:
        lines.append(f"Puppet repo: {repo}")

    lines.extend(
        [
            "",
            "/// Stage 1: Canary (small test set)",
            "",
            "# Deploy to initial canary set",
            (
                f"uv run fleetroll host-set-override --from-file {override_rel} "
                "configs/host-lists/canary-1804-and-2404-set-1.list"
            ),
            "",
            "# Monitor for issues, then proceed to next canary set",
            (
                f"uv run fleetroll host-set-override --from-file {override_rel} "
                "configs/host-lists/canary-1804-and-2404-set-2.list"
            ),
            "",
            "/// Stage 2: Broader canary",
            "",
            "# Deploy to all canary hosts",
            (
                f"uv run fleetroll host-set-override --from-file {override_rel} "
                "configs/host-lists/canary-all.list"
            ),
            "",
            "/// Stage 3: Production rollout (batch 1)",
            "",
            "# Deploy to first batch of production hosts",
            (
                f"uv run fleetroll host-set-override --from-file {override_rel} "
                "configs/host-lists/1804-and-2404-batch1.list"
            ),
            "",
            "/// Stage 4: Production rollout (remaining)",
            "",
            "# Deploy to all 1804 and 2404 hosts",
            (
                f"uv run fleetroll host-set-override --from-file {override_rel} "
                "configs/host-lists/1804-and-2404.list"
            ),
            "",
            "/// Rollback (if needed)",
            "",
            "# Remove override from all hosts",
            "# uv run fleetroll host-remove-override configs/host-lists/all.list",
        ]
    )

    rollout_path.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a rollout plan file from an override file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s configs/overrides/my-feature.override
  %(prog)s configs/overrides/my-feature.override configs/vault.yml
  %(prog)s --yes configs/overrides/my-feature.override  # Skip confirmation
        """,
    )
    parser.add_argument(
        "override_file",
        type=Path,
        help="Path to the override file",
    )
    parser.add_argument(
        "vault_file",
        type=Path,
        nargs="?",
        help="Path to vault.yml file (optional)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )

    args = parser.parse_args()

    # Resolve to absolute paths
    override_file = args.override_file.resolve()
    vault_file = args.vault_file.resolve() if args.vault_file else None

    # Validate override file exists
    if not override_file.exists():
        print(f"Error: Override file not found: {override_file}", file=sys.stderr)
        return 1

    # Validate vault file exists (if provided)
    if vault_file and not vault_file.exists():
        print(f"Error: Vault file not found: {vault_file}", file=sys.stderr)
        return 1

    # Parse override file
    override_data = parse_override_file(override_file)
    if "branch" not in override_data:
        print("Error: Could not extract PUPPET_BRANCH from override file", file=sys.stderr)
        return 1

    branch = override_data["branch"]
    repo = override_data.get("repo")
    mail = override_data.get("mail")

    # Determine paths
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    rollout_dir = project_root / "configs" / "rollouts"
    rollout_dir.mkdir(parents=True, exist_ok=True)

    # Create rollout filename
    date_str = datetime.now(tz=UTC).strftime("%m%d%y")
    rollout_path = rollout_dir / f"rollout-{date_str}-{branch}.txt"

    # Check if file already exists
    if rollout_path.exists():
        print(f"Error: Rollout file already exists: {rollout_path}", file=sys.stderr)
        return 1

    # Get relative paths
    override_rel = str(override_file.relative_to(project_root))
    vault_rel = str(vault_file.relative_to(project_root)) if vault_file else None

    # Show preview
    user = getuser()
    show_preview(
        rollout_path=rollout_path,
        branch=branch,
        override_rel=override_rel,
        vault_rel=vault_rel,
        repo=repo,
        mail=mail,
        user=user,
    )

    # Ask for confirmation (unless --yes flag)
    if not args.yes:
        response = input("Create this rollout plan? (y/n) ").strip().lower()
        if response not in ("y", "yes"):
            print("Aborted.")
            return 0

    # Create the rollout file
    create_rollout_file(
        rollout_path=rollout_path,
        branch=branch,
        override_rel=override_rel,
        vault_rel=vault_rel,
        repo=repo,
        user=user,
    )

    print()
    print(f"✓ Created rollout plan: {rollout_path}")
    print()
    print("Next steps:")
    print("  1. Review and customize the rollout plan")
    print("  2. Execute commands from the rollout plan stage by stage")
    print("  3. Monitor rollout health (RO_HEALTH column) between stages")
    print()
    print("Note: The rollout file is a plan/checklist - execute commands manually")
    print("      and verify success before proceeding to the next stage.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
