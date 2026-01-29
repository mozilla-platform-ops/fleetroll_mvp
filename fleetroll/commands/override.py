"""FleetRoll override content commands."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..constants import OVERRIDES_DIR_NAME
from ..exceptions import UserError
from ..humanhash import humanize
from ..utils import default_audit_log_path, sha256_hex

if TYPE_CHECKING:
    from ..cli_types import OverrideShowArgs


def resolve_override_path(sha_prefix: str, *, overrides_dir: Path) -> Path:
    """Resolve an override file by SHA prefix in the overrides directory."""
    if not overrides_dir.exists():
        raise UserError(f"Overrides directory not found: {overrides_dir}")

    matches: list[Path] = []
    for entry in overrides_dir.iterdir():
        if entry.is_symlink():
            continue
        if entry.is_file() and entry.name.startswith(sha_prefix):
            matches.append(entry)

    if not matches:
        raise UserError(f"No override file found for prefix: {sha_prefix}")
    if len(matches) > 1:
        choices = ", ".join(sorted(p.name for p in matches))
        raise UserError(f"Ambiguous prefix '{sha_prefix}', matches: {choices}")
    return matches[0]


def resolve_override_humanhash(human_hash: str, *, overrides_dir: Path) -> Path:
    """Resolve an override file by 2-word humanhash."""
    if not overrides_dir.exists():
        raise UserError(f"Overrides directory not found: {overrides_dir}")

    matches: list[tuple[Path, str]] = []
    for entry in overrides_dir.iterdir():
        if entry.is_symlink():
            continue
        if not entry.is_file():
            continue
        content = entry.read_bytes()
        sha = sha256_hex(content)
        if humanize(sha, words=2) == human_hash:
            matches.append((entry, sha))

    if not matches:
        raise UserError(f"No override file found for human-hash: {human_hash}")
    if len(matches) > 1:
        choices = "\n".join(
            f"- {sha[:12]} ({path.name})"
            for path, sha in sorted(matches, key=lambda item: (item[1], item[0].name))
        )
        raise UserError(f"Ambiguous human-hash '{human_hash}', matches:\n{choices}")
    return matches[0][0]


def cmd_override_show(args: OverrideShowArgs) -> None:
    """Print stored override contents by SHA prefix."""
    audit_log = Path(args.audit_log) if args.audit_log else default_audit_log_path()
    overrides_dir = audit_log.parent / OVERRIDES_DIR_NAME
    try:
        override_path = resolve_override_path(args.sha_prefix, overrides_dir=overrides_dir)
    except UserError as exc:
        if "No override file found for prefix" not in str(exc):
            raise
        override_path = resolve_override_humanhash(args.sha_prefix, overrides_dir=overrides_dir)
    print(override_path.read_text(encoding="utf-8", errors="replace"), end="")
