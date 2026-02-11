"""FleetRoll audit logging functions."""

from __future__ import annotations

import base64
import contextlib
import datetime
import json
import os
import sqlite3
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
    db_conn: sqlite3.Connection,
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
    os_type = info.get("OS_TYPE")
    uptime_s = None
    uptime_raw = info.get("UPTIME_S")
    if uptime_raw:
        try:
            uptime_s = int(uptime_raw)
        except ValueError:
            uptime_s = None

    # Parse puppet state from base64-encoded JSON
    puppet_state_ts = None
    puppet_git_sha = None
    puppet_git_repo = None
    puppet_git_branch = None
    puppet_override_sha_applied = None
    puppet_vault_sha_applied = None
    puppet_role = None
    puppet_last_run_epoch = None
    puppet_exit_code = None
    puppet_duration_s = None
    puppet_success = None
    puppet_git_dirty = None

    pp_state_json_b64 = info.get("PP_STATE_JSON")
    if pp_state_json_b64:
        try:
            # Decode base64 and parse JSON
            pp_state_json = base64.b64decode(pp_state_json_b64).decode("utf-8")
            pp_state = json.loads(pp_state_json)

            # Extract fields from JSON
            puppet_state_ts = pp_state.get("ts")
            puppet_git_sha = pp_state.get("git_sha")
            puppet_git_repo = pp_state.get("git_repo")
            puppet_git_branch = pp_state.get("git_branch")
            puppet_override_sha_applied = pp_state.get("override_sha")
            puppet_vault_sha_applied = pp_state.get("vault_sha")
            puppet_role = pp_state.get("role")
            puppet_exit_code = pp_state.get("exit_code")
            puppet_duration_s = pp_state.get("duration_s")
            puppet_success = pp_state.get("success")
            puppet_git_dirty = pp_state.get("git_dirty")

            # Convert timestamp to epoch
            if puppet_state_ts:
                try:
                    ts_dt = datetime.datetime.fromisoformat(puppet_state_ts)
                    puppet_last_run_epoch = int(ts_dt.timestamp())
                except (ValueError, AttributeError):
                    pass

        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            # If parsing fails, leave all fields as None
            pass

    # Backward compatibility: Fall back to old format fields if new format not present
    if puppet_state_ts is None and "PP_STATE_TS" in info:
        puppet_state_ts = info["PP_STATE_TS"]

    if puppet_last_run_epoch is None and "PP_LAST_RUN_EPOCH" in info:
        with contextlib.suppress(ValueError, TypeError):
            puppet_last_run_epoch = int(info["PP_LAST_RUN_EPOCH"])

    if puppet_success is None and "PP_SUCCESS" in info:
        with contextlib.suppress(ValueError, TypeError):
            puppet_success = info["PP_SUCCESS"] == "1"

    if puppet_git_sha is None and "PP_GIT_SHA" in info:
        puppet_git_sha = info["PP_GIT_SHA"]

    if puppet_git_repo is None and "PP_GIT_REPO" in info:
        puppet_git_repo = info["PP_GIT_REPO"]

    if puppet_git_branch is None and "PP_GIT_BRANCH" in info:
        puppet_git_branch = info["PP_GIT_BRANCH"]

    if puppet_git_dirty is None and "PP_GIT_DIRTY" in info:
        with contextlib.suppress(ValueError, TypeError):
            puppet_git_dirty = info["PP_GIT_DIRTY"] == "1"

    if puppet_override_sha_applied is None and "PP_OVERRIDE_SHA_APPLIED" in info:
        puppet_override_sha_applied = info["PP_OVERRIDE_SHA_APPLIED"]

    if puppet_vault_sha_applied is None and "PP_VAULT_SHA_APPLIED" in info:
        puppet_vault_sha_applied = info["PP_VAULT_SHA_APPLIED"]

    if puppet_role is None and "PP_ROLE" in info:
        puppet_role = info["PP_ROLE"]

    if puppet_exit_code is None and "PP_EXIT_CODE" in info:
        with contextlib.suppress(ValueError, TypeError):
            puppet_exit_code = int(info["PP_EXIT_CODE"])

    if puppet_duration_s is None and "PP_DURATION_S" in info:
        with contextlib.suppress(ValueError, TypeError):
            puppet_duration_s = int(info["PP_DURATION_S"])

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
            "mode": info.get("VLT_MODE") or "",
            "owner": info.get("VLT_OWNER") or "",
            "group": info.get("VLT_GROUP") or "",
            "size": info.get("VLT_SIZE") or "",
            "mtime_epoch": info.get("VLT_MTIME") or "",
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
            "os_type": os_type,
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
            "puppet_state_ts": puppet_state_ts,
            "puppet_last_run_epoch": puppet_last_run_epoch,
            "puppet_success": puppet_success,
            "puppet_git_sha": puppet_git_sha,
            "puppet_git_repo": puppet_git_repo,
            "puppet_git_branch": puppet_git_branch,
            "puppet_git_dirty": puppet_git_dirty,
            "puppet_override_sha_applied": puppet_override_sha_applied,
            "puppet_vault_sha_applied": puppet_vault_sha_applied,
            "puppet_role": puppet_role,
            "puppet_exit_code": puppet_exit_code,
            "puppet_duration_s": puppet_duration_s,
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

    # Write observation to SQLite
    from .db import insert_host_observation

    if log_lock:
        with log_lock:
            insert_host_observation(db_conn, log_record)
            db_conn.commit()
    else:
        insert_host_observation(db_conn, log_record)
        db_conn.commit()
    return result
