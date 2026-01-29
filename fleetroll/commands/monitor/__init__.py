"""FleetRoll host monitor command implementation."""

from __future__ import annotations

# Temporary compatibility shim - re-export everything from _monitor_impl.py
from .._monitor_impl import (
    AuditLogTailer,
    MonitorDisplay,
    age_seconds,
    build_ok_row_values,
    build_row_values,
    clip_cell,
    cmd_host_monitor,
    compute_columns_and_widths,
    detect_common_fqdn_suffix,
    format_monitor_row,
    format_ts_with_age,
    humanize_age,
    humanize_duration,
    load_latest_records,
    load_tc_worker_data,
    record_matches,
    render_cell_text,
    render_monitor_lines,
    render_row_cells,
    resolve_last_ok_ts,
    strip_fqdn,
    tail_audit_log,
)

__all__ = [
    "AuditLogTailer",
    "MonitorDisplay",
    "age_seconds",
    "build_ok_row_values",
    "build_row_values",
    "clip_cell",
    "cmd_host_monitor",
    "compute_columns_and_widths",
    "detect_common_fqdn_suffix",
    "format_monitor_row",
    "format_ts_with_age",
    "humanize_age",
    "humanize_duration",
    "load_latest_records",
    "load_tc_worker_data",
    "record_matches",
    "render_cell_text",
    "render_monitor_lines",
    "render_row_cells",
    "resolve_last_ok_ts",
    "strip_fqdn",
    "tail_audit_log",
]
