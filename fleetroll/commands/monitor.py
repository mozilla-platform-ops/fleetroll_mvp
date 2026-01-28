"""FleetRoll host monitor command implementation."""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
from collections.abc import Iterable
from curses import error as curses_error
from curses import wrapper as curses_wrapper
from importlib.metadata import version as get_version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..audit import iter_audit_records
from ..constants import TC_WORKERS_FILE_NAME
from ..exceptions import FleetRollError
from ..humanhash import humanize
from ..utils import (
    default_audit_log_path,
    ensure_host_or_file,
    is_host_file,
    parse_host_list,
)

if TYPE_CHECKING:
    from ..cli import Args

FLEETROLL_MASCOT = [
    "  ▄█████▄  ",
    " ▐▛▀▀▀▀▀▜▌ ",
    "▗▟█▄███▄█▙▖",
    "  ▀▘   ▀▘  ",
]

COLUMN_GUIDE_TEXT = """\
Column Guide (press q or Esc to close)

HOST      Hostname (FQDN suffix stripped if common)
ROLE      Puppet role assigned to host
OVR_SHA   Override file SHA256 hash
VLT_SHA   Vault file SHA256 hash
UPTIME    Host uptime since last boot
PP_LAST   Time since last puppet run (FAIL if failed)
TC_LAST   Time since TC worker was last active
TC_T_DUR  TC task duration (or time since start if in progress)
TC_QUAR   TC quarantine status (Y if quarantined)
DATA      Data freshness: audit_age/tc_age

APPLIED   Override applied by puppet
          Y = override present, puppet ran after, succeeded
          N = override present, puppet hasn't run or failed
          - = no override present

HEALTHY   Overall rollout health status
          Y = APPLIED and TC worker active (< 1 hour)
          N = not applied or TC worker stale
          - = no override present
"""


def record_matches(
    record: dict[str, Any],
    *,
    hosts: set[str],
    override_path: str,
    role_path: str,
    vault_path: str,
) -> bool:
    """Return True if record is a host audit for the requested host/path."""
    if record.get("action") != "host.audit":
        return False
    if record.get("host") not in hosts:
        return False
    if record.get("override_path") != override_path:
        return False
    if record.get("role_path") != role_path:
        return False
    if "vault_path" in record:
        record_vault = record.get("vault_path")
        if record_vault and record_vault != vault_path:
            return False
    return True


def strip_fqdn(hostname: str) -> str:
    """Strip FQDN to get short hostname."""
    return hostname.split(".")[0]


def detect_common_fqdn_suffix(hosts: list[str]) -> str | None:
    """Detect common FQDN suffix across all hosts.

    Returns the common suffix (e.g., '.test.releng.mdc1.mozilla.com') if all hosts
    share the same suffix, or None if hosts have different suffixes or no FQDN.
    """
    if not hosts:
        return None
    suffixes = set()
    for host in hosts:
        if "." in host:
            suffix = host[host.index(".") :]
            suffixes.add(suffix)
        else:
            return None  # No FQDN, can't strip
    if len(suffixes) == 1:
        return suffixes.pop()
    return None  # Multiple suffixes


def load_tc_worker_data(path: Path) -> dict[str, dict[str, Any]]:
    """Load TaskCluster worker data from JSONL file.

    Returns a dict mapping short hostname to worker data.
    If multiple records exist for the same host, uses most recent by ts.
    """
    if not path.exists():
        return {}

    host_data: dict[str, dict[str, Any]] = {}

    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if record.get("type") != "worker":
                        continue

                    host = record.get("host")
                    ts = record.get("ts")
                    if not host or not ts:
                        continue

                    # Use short hostname as key
                    short_host = strip_fqdn(host)

                    # Keep most recent record
                    if short_host not in host_data or ts > host_data[short_host].get("ts", ""):
                        host_data[short_host] = record

                except json.JSONDecodeError:
                    continue
    except Exception:
        return {}

    return host_data


def clip_cell(value: str, width: int) -> str:
    """Clip and pad a cell to width using ASCII ellipsis."""
    if width <= 0:
        return ""
    if len(value) <= width:
        return value.ljust(width)
    if width <= 3:
        return value[:width]
    return f"{value[: width - 3]}..."


def render_cell_text(col_name: str, value: str, width: int, *, include_marker: bool = True) -> str:
    """Render a padded cell for a column."""
    if include_marker and col_name == "role" and width >= 2:
        return clip_cell(f"# {value}", width)
    return clip_cell(value, width)


def render_row_cells(
    values: dict[str, str],
    *,
    columns: list[str],
    widths: dict[str, int],
    include_marker: bool = True,
) -> list[str]:
    """Render padded cell strings for a row."""
    return [
        render_cell_text(col, values[col], widths[col], include_marker=include_marker)
        for col in columns
    ]


def humanize_age(ts_value: str) -> str:
    """Return a humanized age string for an ISO timestamp."""
    if not ts_value or ts_value == "?":
        return "?"
    try:
        delta_s = age_seconds(ts_value)
        if delta_s is None:
            return ts_value
        buckets = [
            (60, "<1m ago"),
            (3 * 60, "<3m ago"),
            (5 * 60, "<5m ago"),
            (15 * 60, "<15m ago"),
            (30 * 60, "<30m ago"),
            (45 * 60, "<45m ago"),
            (60 * 60, "<1h ago"),
            (2 * 60 * 60, "<2h ago"),
            (4 * 60 * 60, "<4h ago"),
            (8 * 60 * 60, "<8h ago"),
            (12 * 60 * 60, "<12h ago"),
            (24 * 60 * 60, "<1d ago"),
            (2 * 24 * 60 * 60, "<2d ago"),
            (3 * 24 * 60 * 60, "<3d ago"),
            (7 * 24 * 60 * 60, "<1w ago"),
            (14 * 24 * 60 * 60, "<2w ago"),
            (30 * 24 * 60 * 60, "<1mo ago"),
            (90 * 24 * 60 * 60, "<3mo ago"),
            (180 * 24 * 60 * 60, "<6mo ago"),
            (365 * 24 * 60 * 60, "<1y ago"),
        ]
        for limit_s, label in buckets:
            if delta_s < limit_s:
                return label
        return ">=1y ago"
    except ValueError:
        return ts_value


def age_seconds(ts_value: str) -> int | None:
    """Return age in seconds for an ISO timestamp."""
    if not ts_value or ts_value == "?":
        return None
    try:
        parsed = dt.datetime.fromisoformat(ts_value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.UTC)
        now = dt.datetime.now(dt.UTC)
        return max(int((now - parsed).total_seconds()), 0)
    except ValueError:
        return None


def humanize_duration(seconds_value: int | None, *, min_unit: str = "s") -> str:
    """Return a humanized duration from seconds.

    Args:
        seconds_value: Duration in seconds
        min_unit: Minimum unit to display ("s" for seconds, "m" for minutes).
                  If "m" and value < 60s, shows "<1m" instead of seconds.
    """
    if seconds_value is None:
        return "-"
    seconds_value = max(seconds_value, 0)
    if seconds_value < 60:
        if min_unit == "m":
            return "<1m"
        return f"{seconds_value}s"
    minutes, seconds = divmod(seconds_value, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours:02d}h"


def format_ts_with_age(ts_value: str) -> str:
    """Return timestamp with humanized age."""
    if not ts_value or ts_value == "?":
        return "?"
    return f"{ts_value} ({humanize_age(ts_value)})"


def build_ok_row_values(
    host: str,
    record: dict[str, Any],
    *,
    tc_data: dict[str, Any] | None = None,
    fqdn_suffix: str | None = None,
) -> dict[str, str]:
    """Build string values for an OK monitor row."""
    # Strip common FQDN suffix if provided
    display_host = host
    if fqdn_suffix and host.endswith(fqdn_suffix):
        display_host = host[: -len(fqdn_suffix)]

    observed = record.get("observed") or {}
    role_present = observed.get("role_present")
    role = observed.get("role") if role_present else "missing"
    override_present = observed.get("override_present")
    override_state = "present" if override_present else "absent"
    sha_full = observed.get("override_sha256") or ""
    vault_sha_full = observed.get("vault_sha256") or ""
    sha = sha_full[:12] if sha_full else "-"
    vault_sha = vault_sha_full[:12] if vault_sha_full else "-"
    if sha_full:
        sha = f"{sha} {humanize(sha_full, words=2)}"
    if vault_sha_full:
        vault_sha = f"{vault_sha} {humanize(vault_sha_full, words=2)}"
    meta = observed.get("override_meta") or {}
    mtime = meta.get("mtime_epoch") if override_present else "-"

    # Puppet fields
    puppet_last_run_epoch = observed.get("puppet_last_run_epoch")
    puppet_success = observed.get("puppet_success")

    # PP_LAST: time since last puppet run (relative to audit time) + FAIL indicator
    pp_last = "--"
    if puppet_last_run_epoch is not None:
        # Use audit timestamp for consistency with uptime (both are snapshots)
        audit_ts = record.get("ts")
        if audit_ts:
            try:
                audit_dt = dt.datetime.fromisoformat(audit_ts)
                audit_epoch = int(audit_dt.timestamp())
                pp_age_s = max(audit_epoch - puppet_last_run_epoch, 0)
                pp_last = humanize_duration(pp_age_s)
            except (ValueError, AttributeError):
                pp_last = "--"
        if puppet_success is False and pp_last != "--":
            pp_last = f"{pp_last} FAIL"

    # APPLIED: override present AND puppet ran after mtime AND succeeded
    applied = "-"
    if override_present:
        applied = "N"
        override_mtime_epoch = meta.get("mtime_epoch")
        if (
            override_mtime_epoch is not None
            and puppet_last_run_epoch is not None
            and puppet_success is True
        ):
            try:
                mtime_int = int(override_mtime_epoch)
                if puppet_last_run_epoch > mtime_int:
                    applied = "Y"
            except (ValueError, TypeError):
                pass

    # HEALTHY: applied AND TC_LAST < 1 hour
    healthy = "-"
    tc_last_s = None
    if tc_data:
        last_date_active = tc_data.get("last_date_active")
        scan_ts = tc_data.get("ts")
        if last_date_active and scan_ts:
            try:
                scan_dt = dt.datetime.fromisoformat(scan_ts)
                if scan_dt.tzinfo is None:
                    scan_dt = scan_dt.replace(tzinfo=dt.UTC)
                last_active_dt = dt.datetime.fromisoformat(last_date_active)
                if last_active_dt.tzinfo is None:
                    last_active_dt = last_active_dt.replace(tzinfo=dt.UTC)
                tc_last_s = max(int((scan_dt - last_active_dt).total_seconds()), 0)
            except (ValueError, AttributeError):
                pass

    if override_present:
        healthy = "N"
        if applied == "Y" and tc_last_s is not None and tc_last_s < 3600:
            healthy = "Y"

    # Add TaskCluster fields
    tc_quar = "-"
    tc_last = "-"
    tc_j_sf = "-"
    tc_ts_age = None

    if tc_data:
        # TC data scan age (for combined DATA column)
        tc_scan_ts = tc_data.get("ts")
        if tc_scan_ts:
            tc_ts_age = age_seconds(tc_scan_ts)

        # TC_QUAR: Quarantine status (only if quarantine time is in the future)
        quarantine_until = tc_data.get("quarantine_until")
        if quarantine_until:
            try:
                parsed = dt.datetime.fromisoformat(quarantine_until)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt.UTC)
                now = dt.datetime.now(dt.UTC)
                if parsed > now:
                    tc_quar = "YES"
            except (ValueError, AttributeError):
                pass

        # Calculate ages relative to scan time, not current time
        scan_ts = tc_data.get("ts")

        # TC_LAST: Last date active (at scan time)
        last_date_active = tc_data.get("last_date_active")
        if last_date_active and scan_ts:
            try:
                scan_dt = dt.datetime.fromisoformat(scan_ts)
                if scan_dt.tzinfo is None:
                    scan_dt = scan_dt.replace(tzinfo=dt.UTC)
                last_active_dt = dt.datetime.fromisoformat(last_date_active)
                if last_active_dt.tzinfo is None:
                    last_active_dt = last_active_dt.replace(tzinfo=dt.UTC)
                delta_s = max(int((scan_dt - last_active_dt).total_seconds()), 0)
                tc_last = humanize_duration(delta_s)
            except (ValueError, AttributeError):
                pass

        # TC_T_DUR: Task duration (completed) or time since start (in progress)
        task_started = tc_data.get("task_started")
        task_resolved = tc_data.get("task_resolved")
        if task_started and scan_ts:
            try:
                scan_dt = dt.datetime.fromisoformat(scan_ts)
                if scan_dt.tzinfo is None:
                    scan_dt = scan_dt.replace(tzinfo=dt.UTC)
                task_started_dt = dt.datetime.fromisoformat(task_started)
                if task_started_dt.tzinfo is None:
                    task_started_dt = task_started_dt.replace(tzinfo=dt.UTC)

                if task_resolved:
                    # Completed: show duration (resolved - started)
                    task_resolved_dt = dt.datetime.fromisoformat(task_resolved)
                    if task_resolved_dt.tzinfo is None:
                        task_resolved_dt = task_resolved_dt.replace(tzinfo=dt.UTC)
                    duration_s = max(int((task_resolved_dt - task_started_dt).total_seconds()), 0)
                    tc_j_sf = humanize_duration(duration_s)
                else:
                    # In progress: show time since start with trailing dash
                    start_age_s = max(int((scan_dt - task_started_dt).total_seconds()), 0)
                    tc_j_sf = f"{humanize_duration(start_age_s)} -"
            except (ValueError, AttributeError):
                pass

    # DATA: Combined audit/tc ages (use minutes as minimum unit)
    audit_age = age_seconds(record.get("ts", "?"))
    audit_str = humanize_duration(audit_age, min_unit="m") if audit_age is not None else "-"
    tc_str = humanize_duration(tc_ts_age, min_unit="m") if tc_ts_age is not None else "-"
    data = f"{audit_str}/{tc_str}"

    return {
        "status": "OK",
        "host": display_host,
        "uptime": humanize_duration(observed.get("uptime_s")),
        "override": override_state,
        "role": role,
        "sha": sha,
        "vlt_sha": vault_sha,
        "mtime": str(mtime),
        "err": "-",
        "tc_quar": tc_quar,
        "tc_last": tc_last,
        "tc_j_sf": tc_j_sf,
        "pp_last": pp_last,
        "applied": applied,
        "healthy": healthy,
        "data": data,
    }


def build_row_values(
    host: str,
    record: dict[str, Any] | None,
    *,
    last_ok: dict[str, Any] | None = None,
    tc_data: dict[str, Any] | None = None,
    fqdn_suffix: str | None = None,
) -> dict[str, str]:
    """Build string values for a monitor row."""
    # Strip common FQDN suffix if provided
    display_host = host
    if fqdn_suffix and host.endswith(fqdn_suffix):
        display_host = host[: -len(fqdn_suffix)]

    if record is None:
        tc_str = "-"
        if tc_data and tc_data.get("ts"):
            tc_str = humanize_duration(age_seconds(tc_data.get("ts")), min_unit="m")
        return {
            "status": "UNK",
            "host": display_host,
            "uptime": "?",
            "override": "?",
            "role": "?",
            "sha": "?",
            "vlt_sha": "?",
            "mtime": "?",
            "err": "?",
            "tc_quar": "-",
            "tc_last": "-",
            "tc_j_sf": "-",
            "pp_last": "?",
            "applied": "?",
            "healthy": "?",
            "data": f"?/{tc_str}",
        }

    if not record.get("ok"):
        err = record.get("error") or record.get("stderr") or "error"
        if last_ok and last_ok.get("ok"):
            values = build_ok_row_values(host, last_ok, tc_data=tc_data, fqdn_suffix=fqdn_suffix)
            values["status"] = "FAIL"
            values["err"] = err
            return values
        tc_str = "-"
        if tc_data and tc_data.get("ts"):
            tc_str = humanize_duration(age_seconds(tc_data.get("ts")), min_unit="m")
        audit_age = age_seconds(record.get("ts", "?"))
        audit_str = humanize_duration(audit_age, min_unit="m") if audit_age is not None else "-"
        return {
            "status": "FAIL",
            "host": display_host,
            "uptime": "-",
            "override": "-",
            "role": "-",
            "sha": "-",
            "vlt_sha": "-",
            "mtime": "-",
            "err": err,
            "tc_quar": "-",
            "tc_last": "-",
            "tc_j_sf": "-",
            "pp_last": "-",
            "applied": "-",
            "healthy": "-",
            "data": f"{audit_str}/{tc_str}",
        }

    return build_ok_row_values(host, record, tc_data=tc_data, fqdn_suffix=fqdn_suffix)


def resolve_last_ok_ts(
    record: dict[str, Any] | None, *, last_ok: dict[str, Any] | None
) -> str | None:
    """Resolve the timestamp to use for LAST_OK field coloring."""
    if record is None:
        return None
    if record.get("ok"):
        return record.get("ts")
    if last_ok and last_ok.get("ok"):
        return last_ok.get("ts")
    return record.get("ts")


def compute_columns_and_widths(
    *,
    hosts: list[str],
    latest: dict[str, dict[str, Any]],
    latest_ok: dict[str, dict[str, Any]] | None = None,
    tc_data: dict[str, dict[str, Any]] | None = None,
    max_width: int,
    cap_widths: bool = True,
    sep_len: int = 1,
    fqdn_suffix: str | None = None,
) -> tuple[list[str], dict[str, int]]:
    """Compute columns and widths that fit within max_width."""
    columns = [
        "host",
        "role",
        "vlt_sha",
        "sha",
        "uptime",
        "pp_last",
        "tc_last",
        "tc_j_sf",
        "tc_quar",
        "data",
        "applied",
        "healthy",
    ]
    labels = {
        "host": "HOST",
        "uptime": "UPTIME",
        "role": "ROLE",
        "sha": "OVR_SHA",
        "vlt_sha": "VLT_SHA",
        "tc_quar": "TC_QUAR",
        "tc_last": "TC_LAST",
        "tc_j_sf": "TC_T_DUR",
        "pp_last": "PP_LAST",
        "applied": "APPLIED",
        "healthy": "HEALTHY",
        "data": "DATA",
    }
    caps = {
        "host": 80,
        "uptime": 16,
        "role": 40,
        "sha": 12,
        "vlt_sha": 12,
        "tc_quar": 8,
        "tc_last": 12,
        "tc_j_sf": 20,
        "pp_last": 12,
        "applied": 7,
        "healthy": 7,
        "data": 12,
    }
    if cap_widths and max_width > 0:
        caps["host"] = min(caps["host"], max_width)
        caps["role"] = min(80, max_width)
    widths = {col: len(labels[col]) for col in columns}
    tc_data = tc_data or {}
    for host in hosts:
        short_host = strip_fqdn(host)
        values = build_row_values(
            host,
            latest.get(host),
            last_ok=latest_ok.get(host) if latest_ok else None,
            tc_data=tc_data.get(short_host),
            fqdn_suffix=fqdn_suffix,
        )
        for col in columns:
            widths[col] = max(widths[col], len(values[col]))
    if cap_widths:
        for col in columns:
            widths[col] = min(widths[col], caps[col])

    if max_width <= 0:
        return columns, widths

    drop_order = [
        "vlt_sha",
        "sha",
        "role",
        "uptime",
        "tc_quar",
        "tc_j_sf",
        "tc_last",
        "data",
    ]
    while True:
        separators = (len(columns) - 1) * sep_len
        total = sum(widths[col] for col in columns) + separators
        if total <= max_width:
            return columns, widths
        for drop in drop_order:
            if drop in columns and len(columns) > 2:
                columns.remove(drop)
                widths.pop(drop, None)
                break
        else:
            break

    if "host" in widths and max_width > 0:
        fixed = sum(widths[col] for col in columns if col != "host")
        separators = (len(columns) - 1) * sep_len
        host_width = max_width - fixed - separators
        if cap_widths:
            widths["host"] = min(caps["host"], max(host_width, 0))
        else:
            widths["host"] = max(host_width, 0)
    return columns, widths


def format_monitor_row(
    host: str,
    record: dict[str, Any] | None,
    *,
    last_ok: dict[str, Any] | None = None,
    tc_data: dict[str, Any] | None = None,
    columns: list[str],
    widths: dict[str, int],
    col_sep: str = " ",
    fqdn_suffix: str | None = None,
) -> str:
    """Format a single table row for a host."""
    values = build_row_values(
        host, record, last_ok=last_ok, tc_data=tc_data, fqdn_suffix=fqdn_suffix
    )
    parts = [clip_cell(values[col], widths[col]) for col in columns]
    return col_sep.join(parts)


def render_monitor_lines(
    *,
    hosts: list[str],
    latest: dict[str, dict[str, Any]],
    latest_ok: dict[str, dict[str, Any]] | None = None,
    tc_data: dict[str, dict[str, Any]] | None = None,
    max_width: int,
    cap_widths: bool = True,
    col_sep: str = " ",
    start: int = 0,
    limit: int | None = None,
    fqdn_suffix: str | None = None,
) -> tuple[str, list[str]]:
    """Render monitor header + lines in sorted host order."""
    sorted_hosts = sorted(hosts)
    tc_data = tc_data or {}
    columns, widths = compute_columns_and_widths(
        hosts=sorted_hosts,
        latest=latest,
        latest_ok=latest_ok,
        tc_data=tc_data,
        max_width=max_width,
        cap_widths=cap_widths,
        sep_len=len(col_sep),
        fqdn_suffix=fqdn_suffix,
    )
    labels = {
        "host": "HOST",
        "uptime": "UPTIME",
        "role": "ROLE",
        "sha": "OVR_SHA",
        "vlt_sha": "VLT_SHA",
        "tc_quar": "TC_QUAR",
        "tc_last": "TC_LAST",
        "tc_j_sf": "TC_T_DUR",
        "pp_last": "PP_LAST",
        "applied": "APPLIED",
        "healthy": "HEALTHY",
        "data": "DATA",
    }
    header_parts = [clip_cell(labels[col], widths[col]) for col in columns]
    header = col_sep.join(header_parts)

    if limit is None:
        host_slice = sorted_hosts[start:]
    else:
        host_slice = sorted_hosts[start : start + limit]
    lines = [
        format_monitor_row(
            host,
            latest.get(host),
            last_ok=latest_ok.get(host) if latest_ok else None,
            tc_data=tc_data.get(strip_fqdn(host)),
            columns=columns,
            widths=widths,
            col_sep=col_sep,
            fqdn_suffix=fqdn_suffix,
        )
        for host in host_slice
    ]
    return header, lines


def load_latest_records(
    path: Path, *, hosts: list[str], override_path: str, role_path: str, vault_path: str
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Load the latest matching audit record per host."""
    host_set = set(hosts)
    latest: dict[str, dict[str, Any]] = {}
    latest_ok: dict[str, dict[str, Any]] = {}
    for record in iter_audit_records(path):
        if record_matches(
            record,
            hosts=host_set,
            override_path=override_path,
            role_path=role_path,
            vault_path=vault_path,
        ):
            latest[record["host"]] = record
            if record.get("ok"):
                latest_ok[record["host"]] = record
    return latest, latest_ok


def tail_audit_log(
    path: Path,
    *,
    hosts: list[str],
    override_path: str,
    role_path: str,
    vault_path: str,
    start_at_end: bool = False,
    poll_interval_s: float = 0.5,
) -> Iterable[dict[str, Any]]:
    """Yield matching audit records appended to the log."""
    host_set = set(hosts)
    file_obj = None
    inode = None
    position = 0

    while True:
        try:
            stat = path.stat()
        except FileNotFoundError:
            time.sleep(poll_interval_s)
            continue

        if inode != stat.st_ino:
            if file_obj:
                file_obj.close()
            file_obj = path.open("r", encoding="utf-8")
            inode = stat.st_ino
            position = stat.st_size if start_at_end else 0
            file_obj.seek(position)
        elif stat.st_size < position:
            position = stat.st_size if start_at_end else 0
            file_obj.seek(position)

        line = file_obj.readline()
        if not line:
            position = file_obj.tell()
            time.sleep(poll_interval_s)
            continue

        position = file_obj.tell()
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record_matches(
            record,
            hosts=host_set,
            override_path=override_path,
            role_path=role_path,
            vault_path=vault_path,
        ):
            yield record


class AuditLogTailer:
    """Non-blocking tailer for the audit log."""

    def __init__(
        self,
        path: Path,
        *,
        hosts: list[str],
        override_path: str,
        role_path: str,
        vault_path: str,
        start_at_end: bool = False,
    ) -> None:
        self.path = path
        self.host_set = set(hosts)
        self.override_path = override_path
        self.role_path = role_path
        self.vault_path = vault_path
        self.start_at_end = start_at_end
        self.file_obj = None
        self.inode = None
        self.position = 0

    def poll(self) -> dict[str, Any] | None:
        """Return one matching record if available; otherwise None."""
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return None

        if self.inode != stat.st_ino:
            if self.file_obj:
                self.file_obj.close()
            self.file_obj = self.path.open("r", encoding="utf-8")
            self.inode = stat.st_ino
            self.position = stat.st_size if self.start_at_end else 0
            self.file_obj.seek(self.position)
        elif stat.st_size < self.position:
            self.position = stat.st_size if self.start_at_end else 0
            self.file_obj.seek(self.position)

        while True:
            line = self.file_obj.readline()
            if not line:
                self.position = self.file_obj.tell()
                return None
            self.position = self.file_obj.tell()
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record_matches(
                record,
                hosts=self.host_set,
                override_path=self.override_path,
                role_path=self.role_path,
                vault_path=self.vault_path,
            ):
                return record


class MonitorDisplay:
    """Curses-based monitor display with encapsulated state."""

    def __init__(
        self,
        stdscr,
        *,
        hosts: list[str],
        host_source: str,
        latest: dict[str, dict[str, Any]],
        latest_ok: dict[str, dict[str, Any]],
        tc_data: dict[str, dict[str, Any]],
        tc_workers_path: Path,
    ) -> None:
        self.stdscr = stdscr
        self.hosts = hosts
        self.host_source = host_source
        self.latest = latest
        self.latest_ok = latest_ok
        self.tc_data = tc_data
        self.tc_workers_path = tc_workers_path
        self.tc_file_mtime = None
        self.offset = 0
        self.col_offset = 0
        self.page_step = 1
        self.max_offset = 0
        self.max_col_offset = 0
        self.last_updated = max(
            (r.get("ts") for r in latest.values() if r.get("ts")),
            default=None,
        )
        self.fqdn_suffix = detect_common_fqdn_suffix(hosts)
        self.show_help = False
        self.curses_mod = None
        self.color_enabled = False
        self.fleetroll_attr = 0
        self.header_data_attr = 0
        self.column_attr = 0
        self._init_curses()
        self._update_tc_mtime()

    def _init_curses(self) -> None:
        try:
            import curses

            self.curses_mod = curses
            curses.curs_set(0)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_CYAN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
                curses.init_pair(3, curses.COLOR_MAGENTA, -1)
                curses.init_pair(4, curses.COLOR_GREEN, -1)
                curses.init_pair(5, curses.COLOR_YELLOW, -1)
                curses.init_pair(6, curses.COLOR_RED, -1)
                curses.init_pair(7, curses.COLOR_BLUE, -1)
                curses.init_pair(8, curses.COLOR_CYAN, -1)
                curses.init_pair(9, curses.COLOR_GREEN, -1)
                curses.init_pair(10, curses.COLOR_MAGENTA, -1)
                curses.init_pair(11, curses.COLOR_YELLOW, -1)
                curses.init_pair(12, curses.COLOR_RED, -1)
                curses.init_pair(13, curses.COLOR_YELLOW, -1)
                curses.init_pair(14, curses.COLOR_MAGENTA, -1)
                curses.init_pair(15, curses.COLOR_WHITE, -1)
                curses.init_pair(16, curses.COLOR_BLACK, -1)
                curses.init_pair(17, curses.COLOR_YELLOW, curses.COLOR_BLACK)
                curses.init_pair(18, curses.COLOR_WHITE, curses.COLOR_BLACK)
                self.color_enabled = True
            self.fleetroll_attr = curses.A_BOLD | (
                curses.color_pair(1) if self.color_enabled else 0
            )
            self.header_data_attr = curses.color_pair(2) if self.color_enabled else 0
            self.column_attr = curses.A_BOLD | (curses.color_pair(3) if self.color_enabled else 0)
        except curses_error:
            return

    def safe_addstr(self, row: int, col: int, text: str, attr: int = 0) -> None:
        try:
            if attr:
                self.stdscr.addstr(row, col, text, attr)
            else:
                self.stdscr.addstr(row, col, text)
        except curses_error:
            return

    def threshold_color_attr(self, seconds_value: int | None, thresholds: tuple[int, int]) -> int:
        """Color by thresholds: green if < thresholds[0], yellow if < thresholds[1], red otherwise.

        Args:
            seconds_value: Value in seconds
            thresholds: (green_threshold, yellow_threshold) in seconds

        Returns:
            Color attribute for curses
        """
        if not self.color_enabled:
            return 0
        if seconds_value is None:
            return 0
        green_max, yellow_max = thresholds
        if seconds_value < green_max:
            return self.curses_mod.color_pair(4)  # GREEN
        if seconds_value < yellow_max:
            return self.curses_mod.color_pair(5)  # YELLOW
        return self.curses_mod.color_pair(6)  # RED

    def uptime_attr(self, seconds_value: int | None) -> int:
        """Color uptime: green < 1h, yellow < 6h, red >= 6h."""
        return self.threshold_color_attr(seconds_value, (60 * 60, 6 * 60 * 60))

    def last_ok_attr(self, seconds_value: int | None) -> int:
        """Color last_ok age: green < 5m, yellow < 30m, red >= 30m."""
        return self.threshold_color_attr(seconds_value, (5 * 60, 30 * 60))

    def tc_last_attr(self, seconds_value: int | None) -> int:
        """Color TC last active: green < 5m, yellow < 1h, red >= 1h."""
        return self.threshold_color_attr(seconds_value, (5 * 60, 60 * 60))

    def pp_last_attr(self, seconds_value: int | None, *, failed: bool = False) -> int:
        """Color PP_LAST: green < 1h (success), yellow < 6h (success), red >= 6h or failed."""
        if not self.color_enabled:
            return 0
        if failed:
            return self.curses_mod.color_pair(6)  # RED
        return self.threshold_color_attr(seconds_value, (60 * 60, 6 * 60 * 60))

    def applied_attr(self, value: str) -> int:
        """Color APPLIED: green=Y, yellow=N, gray=-."""
        if not self.color_enabled:
            return 0
        if value == "Y":
            return self.curses_mod.color_pair(4)  # GREEN
        if value == "N":
            return self.curses_mod.color_pair(5)  # YELLOW
        return 0  # gray/default for "-"

    def healthy_attr(self, value: str) -> int:
        """Color HEALTHY: green=Y, red=N, gray=-."""
        if not self.color_enabled:
            return 0
        if value == "Y":
            return self.curses_mod.color_pair(4)  # GREEN
        if value == "N":
            return self.curses_mod.color_pair(6)  # RED
        return 0  # gray/default for "-"

    def tc_quar_attr(self, value: str) -> int:
        """Color TC_QUAR: red=YES (quarantined), gray=-."""
        if not self.color_enabled:
            return 0
        if value == "YES":
            return self.curses_mod.color_pair(6)  # RED
        return 0  # gray/default for "-"

    def build_color_map(
        self,
        values: Iterable[str],
        *,
        palette: list[int],
        base_attr: int = 0,
        seed: int = 0,
    ) -> dict[str, int]:
        if not self.color_enabled:
            return {}
        ordered = sorted(values)
        mapping: dict[str, int] = {}
        for idx, value in enumerate(ordered):
            base = palette[(idx + seed) % len(palette)] | base_attr
            attr = base
            if idx >= len(palette) // 2:
                attr = base | self.curses_mod.A_REVERSE
            mapping[value] = attr
        return mapping

    def handle_key(self, key: int) -> bool:
        # Handle help popup dismissal
        if self.show_help:
            if key in (27, ord("q"), ord("Q")):  # 27 = Escape
                self.show_help = False
                self.draw_screen()
            return False  # Ignore other keys while help is shown

        if key in (ord("q"), ord("Q")):
            return True
        if not self.curses_mod:
            return False
        if key == ord("?"):
            self.show_help = True
            self.draw_screen()
            return False
        if key in (self.curses_mod.KEY_ENTER, ord("\n"), ord("\r")):
            self.draw_screen()
            return False
        if key in (self.curses_mod.KEY_UP, ord("k")):
            self.offset = max(self.offset - self.page_step, 0)
            self.draw_screen()
        elif key in (self.curses_mod.KEY_DOWN, ord("j")):
            self.offset = min(self.offset + self.page_step, self.max_offset)
            self.draw_screen()
        elif key in (self.curses_mod.KEY_PPAGE,):
            self.offset = max(self.offset - self.page_step, 0)
            self.draw_screen()
        elif key in (self.curses_mod.KEY_NPAGE,):
            self.offset = min(self.offset + self.page_step, self.max_offset)
            self.draw_screen()
        elif key in (self.curses_mod.KEY_LEFT, ord("h")):
            self.col_offset = max(self.col_offset - 1, 0)
            self.draw_screen()
        elif key in (self.curses_mod.KEY_RIGHT, ord("l")):
            self.col_offset = min(self.col_offset + 1, self.max_col_offset)
            self.draw_screen()
        elif key == ord("r"):
            # Reload will be handled later if needed
            self.draw_screen()
        return False

    def _update_tc_mtime(self) -> None:
        """Update stored mtime of TC workers file."""
        try:
            stat = self.tc_workers_path.stat()
            self.tc_file_mtime = stat.st_mtime
        except FileNotFoundError:
            self.tc_file_mtime = None

    def poll_tc_data(self) -> bool:
        """Check if TC data file changed and reload if needed. Returns True if reloaded."""
        try:
            stat = self.tc_workers_path.stat()
            current_mtime = stat.st_mtime
        except FileNotFoundError:
            if self.tc_file_mtime is not None:
                self.tc_data = {}
                self.tc_file_mtime = None
                return True
            return False

        if self.tc_file_mtime is None or current_mtime != self.tc_file_mtime:
            self.tc_data = load_tc_worker_data(self.tc_workers_path)
            self.tc_file_mtime = current_mtime
            return True
        return False

    def update_record(self, record: dict[str, Any]) -> None:
        self.latest[record["host"]] = record
        if record.get("ok"):
            self.latest_ok[record["host"]] = record
        self.last_updated = record.get("ts", "unknown")

    def draw_screen(self) -> None:
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()
        usable_width = max(width - 1, 0)
        page_size = max(height - 2, 0)
        self.page_step = max(page_size, 1)
        self.max_offset = max(len(self.hosts) - page_size, 0)
        self.offset = min(self.offset, self.max_offset)
        sorted_hosts = sorted(self.hosts)
        total_pages = max((len(self.hosts) + self.page_step - 1) // self.page_step, 1)
        current_page = min(((self.offset + self.page_step - 1) // self.page_step) + 1, total_pages)
        updated_age = age_seconds(self.last_updated) if self.last_updated else None
        updated = humanize_duration(updated_age) if updated_age is not None else "never"

        # Compute all columns without dropping any (moved up to calculate scroll indicator)
        all_columns = [
            "host",
            "role",
            "vlt_sha",
            "sha",
            "uptime",
            "pp_last",
            "tc_last",
            "tc_j_sf",
            "tc_quar",
            "data",
            "applied",
            "healthy",
        ]

        # Calculate widths for all columns
        labels = {
            "host": "HOST",
            "uptime": "UPTIME",
            "role": "ROLE",
            "sha": "OVR_SHA",
            "vlt_sha": "VLT_SHA",
            "tc_quar": "TC_QUAR",
            "tc_last": "TC_LAST",
            "tc_j_sf": "TC_T_DUR",
            "pp_last": "PP_LAST",
            "applied": "APPLIED",
            "healthy": "HEALTHY",
            "data": "DATA",
        }

        widths = {col: len(labels[col]) for col in all_columns}
        for host in sorted_hosts:
            short_host = strip_fqdn(host)
            tc_worker_data = self.tc_data.get(short_host)
            values = build_row_values(
                host,
                self.latest.get(host),
                last_ok=self.latest_ok.get(host),
                tc_data=tc_worker_data,
                fqdn_suffix=self.fqdn_suffix,
            )
            for col in all_columns:
                widths[col] = max(widths[col], len(values[col]))

        # Add padding for role/sha columns
        for col in ("role", "sha", "vlt_sha"):
            if col in widths:
                widths[col] += 2

        # Determine visible columns with horizontal scrolling
        # HOST is frozen (always visible), other columns scroll
        frozen_col = "host"
        scrollable_cols = [c for c in all_columns if c != frozen_col]

        # Calculate space available for scrollable columns
        frozen_width = widths[frozen_col]
        available_width = max(usable_width - frozen_width - 3, 0)  # 3 for " | "

        # Determine which scrollable columns fit
        visible_scrollable = []
        running_width = 0
        for col_idx, col in enumerate(scrollable_cols[self.col_offset :], start=self.col_offset):
            col_width = widths[col]
            separator_width = 3 if visible_scrollable else 0
            if running_width + col_width + separator_width <= available_width:
                visible_scrollable.append(col)
                running_width += col_width + separator_width
            else:
                break

        # Calculate max col offset
        self.max_col_offset = max(len(scrollable_cols) - len(visible_scrollable), 0)
        self.col_offset = min(self.col_offset, self.max_col_offset)

        # Rebuild visible columns with correct offset
        visible_scrollable = []
        running_width = 0
        for col in scrollable_cols[self.col_offset :]:
            col_width = widths[col]
            separator_width = 3 if visible_scrollable else 0
            if running_width + col_width + separator_width <= available_width:
                visible_scrollable.append(col)
                running_width += col_width + separator_width
            else:
                break

        columns = [frozen_col] + visible_scrollable

        # Add scroll indicator to header if needed
        scroll_indicator = ""
        if self.max_col_offset > 0:
            first_visible_idx = self.col_offset + 1
            last_visible_idx = self.col_offset + len(visible_scrollable)
            total_scrollable = len(scrollable_cols)
            arrows = ""
            if self.col_offset > 0:
                arrows += "◀ "
            if self.col_offset < self.max_col_offset:
                arrows += "▶"
            scroll_indicator = f" [{arrows.strip()} cols {first_visible_idx}-{last_visible_idx}/{total_scrollable}]"

        # Render top header with scroll indicator
        left = "fleetroll: host-monitor [? for help]"
        if total_pages > 1:
            left = f"{left}, page={current_page}/{total_pages}"
        if scroll_indicator:
            left = f"{left}{scroll_indicator}"
        fqdn_part = f"fqdn={self.fqdn_suffix}, " if self.fqdn_suffix else ""
        right = f"{fqdn_part}source={self.host_source}, hosts={len(self.hosts)}, updated={updated}"
        if usable_width > 0:
            if len(left) + 1 + len(right) > usable_width:
                left_max = max(usable_width - len(right) - 1, 0)
                left = clip_cell(left, left_max).rstrip()
            padding = max(usable_width - len(left) - len(right), 1)
            header = f"{left}{' ' * padding}{right}"
        else:
            header = left
        if header.startswith("fleetroll"):
            self.safe_addstr(0, 0, "fleetroll", self.fleetroll_attr)
            # Find where the right-side data starts (fqdn= or source=)
            right_start = "fqdn=" if "fqdn=" in header else "source="
            if right_start in header:
                left_part, right_part = header[9:].rsplit(right_start, 1)
                self.safe_addstr(0, 9, left_part)
                self.safe_addstr(0, 9 + len(left_part), right_start, self.header_data_attr)
                self.safe_addstr(
                    0,
                    9 + len(left_part) + len(right_start),
                    right_part,
                    self.header_data_attr,
                )
            else:
                self.safe_addstr(0, 9, header[9:])
        else:
            self.safe_addstr(0, 0, header)

        # Render column header
        header_parts = render_row_cells(
            labels, columns=columns, widths=widths, include_marker=False
        )
        header_line = " | ".join(header_parts)
        if " | " in header_line:
            parts = header_line.split(" | ")
            col = 0
            for idx, part in enumerate(parts):
                if idx:
                    self.safe_addstr(1, col, " | ")
                    col += 3
                self.safe_addstr(1, col, part, self.column_attr)
                col += len(part)
        else:
            self.safe_addstr(1, 0, header_line, self.column_attr)

        host_slice = (
            sorted_hosts[self.offset :]
            if page_size <= 0
            else sorted_hosts[self.offset : self.offset + page_size]
        )
        sha_values = set()
        vlt_sha_values = set()
        role_values = set()
        for host in sorted_hosts:
            short_host = strip_fqdn(host)
            tc_worker_data = self.tc_data.get(short_host)
            values = build_row_values(
                host,
                self.latest.get(host),
                last_ok=self.latest_ok.get(host),
                tc_data=tc_worker_data,
                fqdn_suffix=self.fqdn_suffix,
            )
            sha = values.get("sha", "")
            vlt_sha = values.get("vlt_sha", "")
            role = values.get("role", "")
            if sha and sha not in ("-", "?"):
                sha_values.add(sha)
            if vlt_sha and vlt_sha not in ("-", "?"):
                vlt_sha_values.add(vlt_sha)
            if role and role not in ("-", "?", "missing"):
                role_values.add(role)
        sha_palette = [
            self.curses_mod.color_pair(7),  # blue
            self.curses_mod.color_pair(8),  # cyan
            self.curses_mod.color_pair(9),  # green
            self.curses_mod.color_pair(10),  # magenta
            self.curses_mod.color_pair(11),  # yellow
            self.curses_mod.color_pair(1),  # cyan (header color but ok for SHA)
            self.curses_mod.color_pair(2),  # yellow
            self.curses_mod.color_pair(3),  # magenta
        ]
        role_palette = [
            self.curses_mod.color_pair(12),  # red
            self.curses_mod.color_pair(13),  # yellow
            self.curses_mod.color_pair(14),  # magenta
            self.curses_mod.color_pair(7),  # blue
            self.curses_mod.color_pair(8),  # cyan
            self.curses_mod.color_pair(9),  # green
            self.curses_mod.color_pair(10),  # magenta
            self.curses_mod.color_pair(11),  # yellow
        ]
        sha_colors = self.build_color_map(sha_values, palette=sha_palette, seed=0)
        vlt_sha_colors = self.build_color_map(vlt_sha_values, palette=sha_palette, seed=4)
        role_colors = self.build_color_map(
            role_values, palette=role_palette, base_attr=self.curses_mod.A_BOLD, seed=0
        )

        for idx, host in enumerate(host_slice, start=1):
            row = idx + 1
            if row >= height:
                break
            short_host = strip_fqdn(host)
            tc_worker_data = self.tc_data.get(short_host)
            values = build_row_values(
                host,
                self.latest.get(host),
                last_ok=self.latest_ok.get(host),
                tc_data=tc_worker_data,
                fqdn_suffix=self.fqdn_suffix,
            )
            ts_value = resolve_last_ok_ts(self.latest.get(host), last_ok=self.latest_ok.get(host))
            tc_ts_value = tc_worker_data.get("ts") if tc_worker_data else None
            uptime_value = values.get("uptime")
            uptime_s = None
            if uptime_value and uptime_value not in ("-", "?"):
                observed = (self.latest_ok.get(host) or self.latest.get(host) or {}).get(
                    "observed", {}
                )
                uptime_s = observed.get("uptime_s")

            # Calculate TC_LAST in seconds for coloring
            tc_last_s = None
            if tc_worker_data:
                last_date_active = tc_worker_data.get("last_date_active")
                scan_ts = tc_worker_data.get("ts")
                if last_date_active and scan_ts:
                    try:
                        scan_dt = dt.datetime.fromisoformat(scan_ts)
                        if scan_dt.tzinfo is None:
                            scan_dt = scan_dt.replace(tzinfo=dt.UTC)
                        last_active_dt = dt.datetime.fromisoformat(last_date_active)
                        if last_active_dt.tzinfo is None:
                            last_active_dt = last_active_dt.replace(tzinfo=dt.UTC)
                        tc_last_s = max(int((scan_dt - last_active_dt).total_seconds()), 0)
                    except (ValueError, AttributeError):
                        pass
            row_cells = render_row_cells(values, columns=columns, widths=widths)
            col = 0
            for col_name, cell in zip(columns, row_cells):
                if col_name != columns[0]:
                    self.safe_addstr(row, col, " | ")
                    col += 3
                if col_name == "data":
                    # Color DATA based on the older of the two ages
                    audit_age_s = age_seconds(ts_value) if ts_value else None
                    tc_age_s = age_seconds(tc_ts_value) if tc_ts_value else None
                    # Use the older (larger) age for coloring
                    if audit_age_s is not None and tc_age_s is not None:
                        max_age_s = max(audit_age_s, tc_age_s)
                    elif audit_age_s is not None:
                        max_age_s = audit_age_s
                    elif tc_age_s is not None:
                        max_age_s = tc_age_s
                    else:
                        max_age_s = None
                    attr = self.last_ok_attr(max_age_s)
                    self.safe_addstr(row, col, cell, attr)
                    col += len(cell)
                    continue
                if col_name == "tc_last":
                    # Apply color based on TC last active time
                    attr = self.tc_last_attr(tc_last_s)
                    self.safe_addstr(row, col, cell, attr)
                    col += len(cell)
                    continue
                if col_name == "pp_last":
                    # Get puppet data for coloring (relative to audit time)
                    host_record = self.latest_ok.get(host) or self.latest.get(host) or {}
                    observed = host_record.get("observed", {})
                    pp_epoch = observed.get("puppet_last_run_epoch")
                    pp_success = observed.get("puppet_success")
                    pp_age_s = None
                    if pp_epoch is not None:
                        audit_ts = host_record.get("ts")
                        if audit_ts:
                            try:
                                audit_dt = dt.datetime.fromisoformat(audit_ts)
                                audit_epoch = int(audit_dt.timestamp())
                                pp_age_s = max(audit_epoch - pp_epoch, 0)
                            except (ValueError, AttributeError):
                                pass
                    attr = self.pp_last_attr(pp_age_s, failed=(pp_success is False))
                    self.safe_addstr(row, col, cell, attr)
                    col += len(cell)
                    continue
                if col_name == "applied":
                    attr = self.applied_attr(values.get("applied", "-"))
                    self.safe_addstr(row, col, cell, attr)
                    col += len(cell)
                    continue
                if col_name == "healthy":
                    attr = self.healthy_attr(values.get("healthy", "-"))
                    self.safe_addstr(row, col, cell, attr)
                    col += len(cell)
                    continue
                if col_name == "tc_quar":
                    attr = self.tc_quar_attr(values.get("tc_quar", "-"))
                    self.safe_addstr(row, col, cell, attr)
                    col += len(cell)
                    continue
                if col_name == "uptime":
                    attr = self.uptime_attr(uptime_s)
                else:
                    attr = 0
                if col_name == "role" and cell.startswith("# "):
                    marker_attr = role_colors.get(values.get("role", ""), 0)
                    self.safe_addstr(row, col, "#", marker_attr)
                    col += 1
                    self.safe_addstr(row, col, cell[1:])
                    col += len(cell) - 1
                elif col_name in ("sha", "vlt_sha"):
                    full_value = values.get(col_name, "")
                    width = widths.get(col_name, 0)
                    if (
                        full_value
                        and full_value not in ("-", "?")
                        and " " in full_value
                        and len(full_value) <= width
                    ):
                        marker_attr = (
                            sha_colors.get(values.get("sha", ""), 0)
                            if col_name == "sha"
                            else vlt_sha_colors.get(values.get("vlt_sha", ""), 0)
                        )
                        split_idx = full_value.rfind(" ")
                        prefix = full_value[: split_idx + 1]
                        suffix = full_value[split_idx + 1 :]
                        padding = " " * (width - len(full_value))
                        self.safe_addstr(row, col, prefix)
                        col += len(prefix)
                        self.safe_addstr(row, col, suffix, marker_attr)
                        col += len(suffix)
                        if padding:
                            self.safe_addstr(row, col, padding)
                            col += len(padding)
                    else:
                        self.safe_addstr(row, col, cell, attr)
                        col += len(cell)
                else:
                    self.safe_addstr(row, col, cell, attr)
                    col += len(cell)

        # Draw help popup if active
        if self.show_help:
            self.draw_help_popup()

        self.stdscr.refresh()

    def draw_help_popup(self) -> None:
        """Draw a centered help popup with the column guide."""
        if not self.curses_mod:
            return

        height, width = self.stdscr.getmaxyx()

        # Combine mascot, version, and help text
        try:
            ver = get_version("fleetroll")
        except Exception:
            ver = "?"
        version_line = f"fleetroll v{ver}"
        mascot_with_version = FLEETROLL_MASCOT + [version_line]
        all_lines = mascot_with_version + [""] + COLUMN_GUIDE_TEXT.strip().split("\n")
        mascot_line_count = len(mascot_with_version)

        # Add padding: 2 chars horizontal, 1 line vertical
        h_pad = 2
        v_pad = 1

        # Calculate popup dimensions
        content_width = max(len(line) for line in all_lines)
        popup_width = content_width + 2 + (h_pad * 2)  # 2 for borders, h_pad on each side
        popup_height = len(all_lines) + 2 + (v_pad * 2)  # 2 for borders, v_pad top and bottom

        # Center the popup
        start_y = max((height - popup_height) // 2, 0)
        start_x = max((width - popup_width) // 2, 0)

        # Clip to screen
        if start_y + popup_height > height:
            popup_height = max(height - start_y, 3)
        if start_x + popup_width > width:
            popup_width = max(width - start_x, 10)

        # Get attributes for popup (black on white - pair 18)
        popup_attr = (
            self.curses_mod.color_pair(18) if self.color_enabled else self.curses_mod.A_REVERSE
        )
        # Yellow text on white background (color pair 17)
        mascot_attr = (
            self.curses_mod.color_pair(17) if self.color_enabled else self.curses_mod.A_REVERSE
        )

        # Draw top border
        top_border = "┌" + "─" * (popup_width - 2) + "┐"
        self.safe_addstr(start_y, start_x, top_border, popup_attr)

        # Track current row
        current_row = start_y + 1

        # Draw top padding lines
        for _ in range(v_pad):
            if current_row >= height - 1:
                break
            blank_line = "│" + " " * (popup_width - 2) + "│"
            self.safe_addstr(current_row, start_x, blank_line, popup_attr)
            current_row += 1

        # Draw content lines with side borders
        max_content_rows = popup_height - 2 - (v_pad * 2)
        for i, line in enumerate(all_lines[:max_content_rows]):
            if current_row >= height - 1:
                break
            # Pad line to fill popup width with horizontal padding
            # Center mascot lines
            if i < mascot_line_count:
                padding_needed = content_width - len(line)
                left_pad = padding_needed // 2
                right_pad = padding_needed - left_pad
                padded = " " * h_pad + " " * left_pad + line + " " * right_pad + " " * h_pad
            else:
                padded = " " * h_pad + line.ljust(content_width) + " " * h_pad
            if len(padded) > popup_width - 2:
                padded = padded[: popup_width - 2]

            # Draw borders in normal attr, content in mascot attr if mascot line
            self.safe_addstr(current_row, start_x, "│", popup_attr)
            if i < mascot_line_count:
                self.safe_addstr(current_row, start_x + 1, padded, mascot_attr)
            else:
                self.safe_addstr(current_row, start_x + 1, padded, popup_attr)
            self.safe_addstr(current_row, start_x + popup_width - 1, "│", popup_attr)
            current_row += 1

        # Draw bottom padding lines
        for _ in range(v_pad):
            if current_row >= height - 1:
                break
            blank_line = "│" + " " * (popup_width - 2) + "│"
            self.safe_addstr(current_row, start_x, blank_line, popup_attr)
            current_row += 1

        # Draw bottom border
        if current_row < height:
            bottom_border = "└" + "─" * (popup_width - 2) + "┘"
            self.safe_addstr(current_row, start_x, bottom_border, popup_attr)


def cmd_host_monitor(args: Args) -> None:
    """Monitor the latest audit record for hosts by tailing the audit log."""
    ensure_host_or_file(args.host)
    if is_host_file(args.host):
        host_file = Path(args.host)
        hosts = parse_host_list(host_file)
        host_source = str(host_file)
    else:
        hosts = [args.host]
        host_source = args.host

    audit_log = Path(args.audit_log) if args.audit_log else default_audit_log_path()

    if args.once and not audit_log.exists():
        raise FleetRollError(f"Audit log not found: {audit_log}")

    latest, latest_ok = load_latest_records(
        audit_log,
        hosts=hosts,
        override_path=args.override_path,
        role_path=args.role_path,
        vault_path=args.vault_path,
    )

    # Load TaskCluster worker data
    tc_workers_path = Path.home() / ".fleetroll" / TC_WORKERS_FILE_NAME
    tc_data = load_tc_worker_data(tc_workers_path)

    if args.once:
        if args.json:
            payload = {host: latest.get(host) for host in hosts}
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            header, lines = render_monitor_lines(
                hosts=hosts,
                latest=latest,
                latest_ok=latest_ok,
                tc_data=tc_data,
                max_width=0,
                cap_widths=False,
                col_sep="  ",
            )
            print(header)
            for line in lines:
                print(line)
        return

    if args.json or not sys.stdout.isatty():
        if args.json:
            payload = {host: latest.get(host) for host in hosts}
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            columns, widths = compute_columns_and_widths(
                hosts=hosts,
                latest=latest,
                latest_ok=latest_ok,
                tc_data=tc_data,
                max_width=0,
                cap_widths=False,
                sep_len=2,
            )
            header, lines = render_monitor_lines(
                hosts=hosts,
                latest=latest,
                latest_ok=latest_ok,
                tc_data=tc_data,
                max_width=0,
                cap_widths=False,
                col_sep="  ",
            )
            print(header)
            for line in lines:
                print(line)
        for record in tail_audit_log(
            audit_log,
            hosts=hosts,
            override_path=args.override_path,
            role_path=args.role_path,
            vault_path=args.vault_path,
            start_at_end=True,
        ):
            if args.json:
                print(json.dumps(record, sort_keys=True))
            else:
                if record.get("ok"):
                    latest_ok[record["host"]] = record
                host = record["host"]
                short_host = strip_fqdn(host)
                print(
                    format_monitor_row(
                        host,
                        record,
                        last_ok=latest_ok.get(host),
                        tc_data=tc_data.get(short_host),
                        columns=columns,
                        widths=widths,
                        col_sep="  ",
                    )
                )
        return

    def curses_main(stdscr) -> None:
        stdscr.nodelay(True)
        stdscr.timeout(200)
        display = MonitorDisplay(
            stdscr,
            hosts=hosts,
            host_source=host_source,
            latest=latest,
            latest_ok=latest_ok,
            tc_data=tc_data,
            tc_workers_path=tc_workers_path,
        )
        display.draw_screen()
        tailer = AuditLogTailer(
            audit_log,
            hosts=hosts,
            override_path=args.override_path,
            role_path=args.role_path,
            vault_path=args.vault_path,
            start_at_end=True,
        )
        while True:
            key = stdscr.getch()
            if display.handle_key(key):
                return
            record = tailer.poll()
            if record:
                display.update_record(record)
                display.draw_screen()
            if display.poll_tc_data():
                display.draw_screen()

    curses_wrapper(curses_main)
