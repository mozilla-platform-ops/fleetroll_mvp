"""Data loading, filtering, and aggregation for monitor command."""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...audit import iter_audit_records
from ...constants import DEFAULT_GITHUB_REPO, HOST_OBSERVATIONS_FILE_NAME
from ...humanhash import humanize
from ...utils import natural_sort_key

if TYPE_CHECKING:
    from .cache import ShaInfoCache


def record_matches(
    record: dict[str, Any],
    *,
    hosts: set[str],
) -> bool:
    """Return True if record is a host audit for the requested host."""
    if record.get("action") != "host.audit":
        return False
    return record.get("host") in hosts


def strip_fqdn(hostname: str) -> str:
    """Strip FQDN to get short hostname."""
    return hostname.split(".")[0]


def get_host_sort_key(
    hostname: str,
    *,
    sort_field: str,
    latest: dict[str, dict[str, Any]],
    latest_ok: dict[str, dict[str, Any]] | None = None,
) -> tuple:
    """Generate sort key for a host based on sort field.

    Args:
        hostname: Hostname to generate key for
        sort_field: Sort field ("host", "role", or "ovr_sha")
        latest: Dictionary of latest records by hostname
        latest_ok: Dictionary of last successful records (fallback for failed audits)

    Returns:
        Sort key tuple for stable multi-level sorting
    """
    if sort_field == "host":
        return (natural_sort_key(hostname),)

    # Use same fallback logic as build_row_values
    host_data = latest.get(hostname, {})

    # If current record failed, fall back to last_ok (matches display logic)
    if host_data and not host_data.get("ok"):
        if latest_ok:
            last_ok_data = latest_ok.get(hostname)
            if last_ok_data and last_ok_data.get("ok"):
                host_data = last_ok_data

    observed = host_data.get("observed", {}) if host_data else {}

    if sort_field == "role":
        # Extract role from observed data (same logic as build_row_values)
        role_present = observed.get("role_present")
        role = observed.get("role", "") if role_present else "missing"

        # Sort hosts with roles first (0), then hosts without roles (1)
        # Treat special values ("-", "?", "missing", "") as no role
        # This puts hosts without roles at the end
        has_role = 0 if (role and role not in ("-", "?", "missing")) else 1

        return (has_role, role, natural_sort_key(hostname))

    if sort_field == "ovr_sha":
        # Extract override SHA from observed data
        sha_full = observed.get("override_sha256") or ""

        # Sort hosts with overrides first (0), then hosts without overrides (1)
        has_override = 0 if sha_full else 1

        return (has_override, sha_full, natural_sort_key(hostname))

    # Default to hostname if unknown sort field
    return (natural_sort_key(hostname),)


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


def load_github_refs(path: Path) -> dict[str, dict[str, Any]]:
    """Load GitHub ref data from JSONL file.

    Returns a dict mapping 'owner/repo:branch' to the latest branch_ref record.
    If multiple records exist for the same branch, uses most recent by ts.
    """
    if not path.exists():
        return {}

    ref_data: dict[str, dict[str, Any]] = {}

    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if record.get("type") != "branch_ref":
                        continue

                    owner = record.get("owner")
                    repo = record.get("repo")
                    branch = record.get("branch")
                    ts = record.get("ts")
                    if not owner or not repo or not branch or not ts:
                        continue

                    # Use 'owner/repo:branch' as key
                    key = f"{owner}/{repo}:{branch}"

                    # Keep most recent record
                    if key not in ref_data or ts > ref_data[key].get("ts", ""):
                        ref_data[key] = record

                except json.JSONDecodeError:
                    continue
    except Exception:
        return {}

    return ref_data


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
    sha_cache: ShaInfoCache | None = None,
    github_refs: dict[str, dict[str, Any]] | None = None,
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

    # Append human-readable info to SHA columns
    if sha_cache:
        if sha_full:
            ovr_info = sha_cache.get_override_info(sha_full)
            if ovr_info != "-":
                sha = f"{sha} ({ovr_info})"
        if vault_sha_full:
            vlt_info = sha_cache.get_vault_info(vault_sha_full)
            if vlt_info != "-":
                vault_sha = f"{vault_sha} ({vlt_info})"
    meta = observed.get("override_meta") or {}
    mtime = meta.get("mtime_epoch") if override_present else "-"

    # Puppet fields
    puppet_last_run_epoch = observed.get("puppet_last_run_epoch")
    puppet_success = observed.get("puppet_success")
    puppet_state_ts = observed.get("puppet_state_ts")
    puppet_git_sha = observed.get("puppet_git_sha")

    # PP_SHA: 7-char truncated git SHA
    pp_sha = "-"
    if puppet_git_sha:
        pp_sha = puppet_git_sha[:7]

    # PP_LAST: time since last puppet run (relative to audit time) + FAIL indicator
    # Prefer puppet_state_ts (new) over puppet_last_run_epoch (old) for backward compat
    pp_last = "--"

    if puppet_state_ts:
        # New path: use puppet_state_ts
        audit_ts = record.get("ts")
        if audit_ts:
            try:
                audit_dt = dt.datetime.fromisoformat(audit_ts)
                puppet_dt = dt.datetime.fromisoformat(puppet_state_ts)
                if puppet_dt.tzinfo is None:
                    puppet_dt = puppet_dt.replace(tzinfo=dt.UTC)
                if audit_dt.tzinfo is None:
                    audit_dt = audit_dt.replace(tzinfo=dt.UTC)
                pp_age_s = max(int((audit_dt - puppet_dt).total_seconds()), 0)
                pp_last = humanize_duration(pp_age_s)
            except (ValueError, AttributeError):
                pp_last = "--"
    elif puppet_last_run_epoch is not None:
        # Fallback path: use puppet_last_run_epoch
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

    # PP_EXP: expected puppet SHA from GitHub branch data
    # PP_MATCH: SHA-based comparison against GitHub branch data
    pp_match = "-"
    pp_exp = "-"
    puppet_git_sha = observed.get("puppet_git_sha")

    if github_refs:
        # Determine expected branch
        ref_key = None
        if override_present and sha_cache:
            details = sha_cache.get_override_details(sha_full)
            if details and details.get("user") and details.get("repo") and details.get("branch"):
                ref_key = f"{details['user']}/{details['repo']}:{details['branch']}"
        else:
            # No override: check against default repo master
            ref_key = f"{DEFAULT_GITHUB_REPO}:master"

        if ref_key:
            ref_record = github_refs.get(ref_key)
            if ref_record:
                github_sha = ref_record.get("sha", "")
                if github_sha:
                    pp_exp = github_sha[:7]
                if puppet_git_sha:
                    if puppet_git_sha == github_sha and puppet_success is True:
                        pp_match = "Y"
                    else:
                        pp_match = "N"
            # else: branch not in github_refs, fall through to "-"

    # HEALTHY: pp_match AND TC_ACT < 1 hour
    healthy = "-"
    tc_act_s = None
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
                tc_act_s = max(int((scan_dt - last_active_dt).total_seconds()), 0)
            except (ValueError, AttributeError):
                pass

    # HEALTHY: PP_MATCH=Y AND TC_ACT < 1 hour
    # (Now applies to both override and non-override hosts since PP_MATCH can be computed for both)
    if pp_match == "-":
        healthy = "-"
    else:
        healthy = "N"
        if pp_match == "Y" and tc_act_s is not None and tc_act_s < 3600:
            healthy = "Y"

    # Add TaskCluster fields
    tc_quar = "-"
    tc_act = "-"
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

        # TC_ACT: Last date active (at scan time)
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
                tc_act = humanize_duration(delta_s)
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

    uptime_s = observed.get("uptime_s")
    uptime: str = humanize_duration(uptime_s if isinstance(uptime_s, int) else None)

    # Dict values are strings at runtime (validated by tests), but type checker
    # sees some as potentially Any due to dict.get() returning Any type
    return {  # type: ignore[invalid-return-type]
        "status": "OK",
        "host": display_host,
        "uptime": uptime,
        "override": override_state,
        "role": role,
        "sha": sha,
        "vlt_sha": vault_sha,
        "mtime": str(mtime),
        "err": "-",
        "tc_quar": tc_quar,
        "tc_act": tc_act,
        "tc_j_sf": tc_j_sf,
        "pp_last": pp_last,
        "pp_sha": pp_sha,
        "pp_exp": pp_exp,
        "pp_match": pp_match,
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
    sha_cache: ShaInfoCache | None = None,
    github_refs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Build string values for a monitor row."""
    # Strip common FQDN suffix if provided
    display_host = host
    if fqdn_suffix and host.endswith(fqdn_suffix):
        display_host = host[: -len(fqdn_suffix)]

    if record is None:
        tc_str = "-"
        if tc_data:
            tc_ts = tc_data.get("ts")
            if tc_ts and isinstance(tc_ts, str):
                tc_str = humanize_duration(age_seconds(tc_ts), min_unit="m")
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
            "tc_act": "-",
            "tc_j_sf": "-",
            "pp_last": "?",
            "pp_sha": "?",
            "pp_exp": "?",
            "pp_match": "?",
            "healthy": "?",
            "data": f"?/{tc_str}",
        }

    if not record.get("ok"):
        err = record.get("error") or record.get("stderr") or "error"
        if last_ok and last_ok.get("ok"):
            values = build_ok_row_values(
                host,
                last_ok,
                tc_data=tc_data,
                fqdn_suffix=fqdn_suffix,
                sha_cache=sha_cache,
                github_refs=github_refs,
            )
            values["status"] = "FAIL"
            values["err"] = err
            return values
        tc_str = "-"
        if tc_data:
            tc_ts = tc_data.get("ts")
            if tc_ts and isinstance(tc_ts, str):
                tc_str = humanize_duration(age_seconds(tc_ts), min_unit="m")
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
            "tc_act": "-",
            "tc_j_sf": "-",
            "pp_last": "-",
            "pp_sha": "-",
            "pp_exp": "-",
            "pp_match": "-",
            "healthy": "-",
            "data": f"{audit_str}/{tc_str}",
        }

    return build_ok_row_values(
        host,
        record,
        tc_data=tc_data,
        fqdn_suffix=fqdn_suffix,
        sha_cache=sha_cache,
        github_refs=github_refs,
    )


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
    path: Path, *, hosts: list[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Load the latest matching audit record per host.

    Reads from host_observations.jsonl.
    """
    host_set = set(hosts)
    latest: dict[str, dict[str, Any]] = {}
    latest_ok: dict[str, dict[str, Any]] = {}

    # Read from observations log
    observations_log = path.parent / HOST_OBSERVATIONS_FILE_NAME

    for record in iter_audit_records(observations_log):
        if record_matches(record, hosts=host_set):
            latest[record["host"]] = record
            if record.get("ok"):
                latest_ok[record["host"]] = record
    return latest, latest_ok


def tail_audit_log(
    path: Path,
    *,
    hosts: list[str],
    start_at_end: bool = False,
    poll_interval_s: float = 0.5,
) -> Iterable[dict[str, Any]]:
    """Yield matching audit records appended to the log.

    Reads from host_observations.jsonl.
    """
    host_set = set(hosts)
    file_obj = None
    inode = None
    position = 0

    # Read from observations log
    observations_log = path.parent / HOST_OBSERVATIONS_FILE_NAME

    while True:
        try:
            stat = observations_log.stat()
        except FileNotFoundError:
            time.sleep(poll_interval_s)
            continue

        if inode != stat.st_ino:
            if file_obj:
                file_obj.close()
            file_obj = observations_log.open("r", encoding="utf-8")
            inode = stat.st_ino
            position = stat.st_size if start_at_end else 0
            file_obj.seek(position)
        elif stat.st_size < position:
            if file_obj:
                position = stat.st_size if start_at_end else 0
                file_obj.seek(position)

        if not file_obj:
            time.sleep(poll_interval_s)
            continue

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
        if record_matches(record, hosts=host_set):
            yield record


class AuditLogTailer:
    """Non-blocking tailer for the audit log.

    Reads from host_observations.jsonl.
    """

    def __init__(
        self,
        path: Path,
        *,
        hosts: list[str],
        start_at_end: bool = False,
    ) -> None:
        # Read from observations log
        observations_log = path.parent / HOST_OBSERVATIONS_FILE_NAME
        self.path = observations_log
        self.host_set = set(hosts)
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
            if record_matches(record, hosts=self.host_set):
                return record
