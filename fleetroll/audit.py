"""FleetRoll audit logging functions."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .constants import CONTENT_PREFIX_LEN, CONTENT_PREFIX_STEP, CONTENT_SENTINEL
from .exceptions import FleetRollError
from .utils import ensure_parent_dir, parse_kv_lines, sha256_hex, utc_now_iso


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append a JSON record to a JSONL file."""
    ensure_parent_dir(path)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def iter_audit_records(path: Path) -> Iterable[dict[str, Any]]:
    """Yield JSONL records from the audit log, skipping invalid lines."""
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return


def store_content_file(content: str, sha256: str, target_dir: Path) -> Path:
    """
    Store content to file named by SHA prefix (no extension).
    Returns path to stored file.
    Handles collisions by extending prefix length.
    Thread-safe via atomic write-then-rename.
    """
    ensure_parent_dir(target_dir / "dummy")  # Ensure target_dir exists

    prefix_len = CONTENT_PREFIX_LEN
    while prefix_len <= 64:
        filename = sha256[:prefix_len]
        target_path = target_dir / filename

        # Check if file already exists
        if target_path.exists():
            # Read and compare content
            existing_content = target_path.read_text(encoding="utf-8", errors="replace")
            if existing_content == content:
                # Same content, return existing path (idempotent)
                return target_path
            # Collision: different content, extend prefix
            prefix_len += CONTENT_PREFIX_STEP
            continue

        # File doesn't exist, write it atomically
        # Create temp file in same directory for atomic rename
        fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix=".tmp_", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            # Atomic rename
            Path(temp_path).rename(target_path)
            return target_path
        except Exception:
            # Clean up temp file on error
            Path(temp_path).unlink(missing_ok=True)
            raise

    # Should never reach here (would need 64-char collision)
    raise FleetRollError(f"Unable to store content file: too many collisions for SHA {sha256}")


def store_override_file(content: str, sha256: str, overrides_dir: Path) -> Path:
    """Store override content to file named by SHA prefix (no extension)."""
    return store_content_file(content, sha256, overrides_dir)


def load_latest_vault_checksums(path: Path) -> dict[str, str]:
    """Return latest vault sha256 per host from audit log."""
    latest: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("action") != "host.set_vault":
                    continue
                if not record.get("ok"):
                    continue
                host = record.get("host")
                params = record.get("parameters") or {}
                sha = params.get("sha256")
                if host and sha:
                    latest[host] = sha
    except FileNotFoundError:
        return {}
    return latest


def has_content_file(sha256: str, target_dir: Path) -> bool:
    """Return True if target_dir contains content matching sha256."""
    if not target_dir.exists():
        return False
    prefix = sha256[:12]
    for entry in target_dir.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        if len(name) < 12 or not sha256.startswith(name):
            continue
        if not name.startswith(prefix):
            continue
        content = entry.read_bytes()
        if sha256_hex(content) == sha256:
            return True
    return False


def process_audit_result(
    host: str,
    *,
    rc: int,
    out: str,
    err: str,
    audit_log: Path,
    actor: str,
    overrides_dir: Path | None = None,
    vault_sha256: str | None = None,
    vault_present: bool | None = None,
    vault_meta: dict[str, str] | None = None,
    log_lock: threading.Lock | None = None,
) -> dict[str, Any]:
    """Process audit SSH result into structured dict."""
    # Split content if present
    sentinel = CONTENT_SENTINEL
    content = ""
    header = out
    if sentinel in out:
        header, content = out.split(sentinel + "\n", 1)

    info = parse_kv_lines(header)
    override_present = info.get("OVERRIDE_PRESENT") == "1"
    vault_present = info.get("VLT_PRESENT") == "1" if "VLT_PRESENT" in info else vault_present
    role_present = info.get("ROLE_PRESENT") == "1"
    uptime_s = None
    uptime_raw = info.get("UPTIME_S")
    if uptime_raw:
        try:
            uptime_s = int(uptime_raw)
        except ValueError:
            uptime_s = None

    # Parse puppet state
    puppet_last_run_epoch = None
    puppet_last_run_raw = info.get("PP_LAST_RUN_EPOCH")
    if puppet_last_run_raw:
        try:
            puppet_last_run_epoch = int(puppet_last_run_raw)
        except ValueError:
            puppet_last_run_epoch = None

    puppet_success = None
    puppet_success_raw = info.get("PP_SUCCESS")
    if puppet_success_raw is not None:
        puppet_success = puppet_success_raw == "1"

    # Compute content hash if we got content
    content_bytes = content.encode("utf-8", "replace")
    content_hash = sha256_hex(content_bytes) if content and override_present else None

    # Store override file if requested
    stored_path = None
    if content and override_present and overrides_dir and content_hash:
        stored_path = store_override_file(content, content_hash, overrides_dir)

    if vault_present is None:
        vault_present = info.get("VLT_PRESENT") == "1"

    if vault_meta is None and vault_present:
        vault_meta = {
            "mode": info.get("VLT_MODE"),
            "owner": info.get("VLT_OWNER"),
            "group": info.get("VLT_GROUP"),
            "size": info.get("VLT_SIZE"),
            "mtime_epoch": info.get("VLT_MTIME"),
        }

    result: dict[str, Any] = {
        "ts": utc_now_iso(),
        "actor": actor,
        "action": "host.audit",
        "host": host,
        "ok": (rc == 0),
        "ssh_rc": rc,
        "stderr": err.strip(),
        "observed": {
            "role_present": role_present,
            "role": info.get("ROLE") if role_present else None,
            "override_present": override_present,
            "override_meta": (
                {
                    "mode": info.get("OVERRIDE_MODE"),
                    "owner": info.get("OVERRIDE_OWNER"),
                    "group": info.get("OVERRIDE_GROUP"),
                    "size": info.get("OVERRIDE_SIZE"),
                    "mtime_epoch": info.get("OVERRIDE_MTIME"),
                }
                if override_present
                else None
            ),
            "override_sha256": content_hash,
            "vault_present": vault_present,
            "vault_meta": vault_meta,
            "vault_sha256": info.get("VLT_SHA256") or vault_sha256,
            "uptime_s": uptime_s,
            "puppet_last_run_epoch": puppet_last_run_epoch,
            "puppet_success": puppet_success,
        },
    }

    # Store contents in result dict for display purposes (not persisted to audit log).
    if content and override_present:
        result["observed"]["override_contents_for_display"] = content

    # Add stored file path if we stored the file
    if stored_path:
        result["observed"]["override_file_path"] = str(stored_path)

    log_record = dict(result)
    log_record["observed"] = dict(result["observed"])
    log_record["observed"].pop("override_contents_for_display", None)
    log_record["observed"].pop("override_contents", None)

    # Write observation to host_observations.jsonl instead of audit.jsonl
    from .constants import HOST_OBSERVATIONS_FILE_NAME

    observations_log = audit_log.parent / HOST_OBSERVATIONS_FILE_NAME

    if log_lock:
        with log_lock:
            append_jsonl(observations_log, log_record)
    else:
        append_jsonl(observations_log, log_record)
    return result
