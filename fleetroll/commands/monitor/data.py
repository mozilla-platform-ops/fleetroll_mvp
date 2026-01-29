"""Data loading, filtering, and aggregation for monitor command."""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ...audit import iter_audit_records
from ...constants import HOST_OBSERVATIONS_FILE_NAME
from ...humanhash import humanize


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


def load_latest_records(
    path: Path, *, hosts: list[str], override_path: str, role_path: str, vault_path: str
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Load the latest matching audit record per host.

    Reads from host_observations.jsonl if it exists, otherwise falls back
    to audit.jsonl for backward compatibility.
    """
    host_set = set(hosts)
    latest: dict[str, dict[str, Any]] = {}
    latest_ok: dict[str, dict[str, Any]] = {}

    # Try new observations file first
    observations_log = path.parent / HOST_OBSERVATIONS_FILE_NAME
    read_path = observations_log if observations_log.exists() else path

    for record in iter_audit_records(read_path):
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
    """Yield matching audit records appended to the log.

    Reads from host_observations.jsonl if it exists, otherwise falls back
    to audit.jsonl for backward compatibility.
    """
    host_set = set(hosts)
    file_obj = None
    inode = None
    position = 0

    # Try new observations file first
    observations_log = path.parent / HOST_OBSERVATIONS_FILE_NAME
    read_path = observations_log if observations_log.exists() else path

    while True:
        try:
            stat = read_path.stat()
        except FileNotFoundError:
            time.sleep(poll_interval_s)
            continue

        if inode != stat.st_ino:
            if file_obj:
                file_obj.close()
            file_obj = read_path.open("r", encoding="utf-8")
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
    """Non-blocking tailer for the audit log.

    Reads from host_observations.jsonl if it exists, otherwise falls back
    to audit.jsonl for backward compatibility.
    """

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
        # Try new observations file first
        observations_log = path.parent / HOST_OBSERVATIONS_FILE_NAME
        self.path = observations_log if observations_log.exists() else path
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
