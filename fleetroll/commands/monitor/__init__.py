"""FleetRoll host monitor command implementation."""

from __future__ import annotations

# Re-export from data module
from .data import (
    AuditLogTailer,
    age_seconds,
    build_ok_row_values,
    build_row_values,
    detect_common_fqdn_suffix,
    format_ts_with_age,
    humanize_age,
    humanize_duration,
    load_latest_records,
    load_tc_worker_data,
    record_matches,
    resolve_last_ok_ts,
    strip_fqdn,
    tail_audit_log,
)

# Re-export from display module
from .display import MonitorDisplay

# Re-export from entry module
from .entry import cmd_host_monitor

# Re-export from formatting module
from .formatting import (
    clip_cell,
    compute_columns_and_widths,
    format_monitor_row,
    render_cell_text,
    render_monitor_lines,
    render_row_cells,
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
