"""Text rendering and layout for monitor command."""

from __future__ import annotations

from typing import Any

from .data import build_row_values, strip_fqdn


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
        "healthy": "RO_HEALTH",
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
    """Render monitor header + lines in the provided host order."""
    tc_data = tc_data or {}
    columns, widths = compute_columns_and_widths(
        hosts=hosts,
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
        "healthy": "RO_HEALTH",
        "data": "DATA",
    }
    header_parts = [clip_cell(labels[col], widths[col]) for col in columns]
    header = col_sep.join(header_parts)

    if limit is None:
        host_slice = hosts[start:]
    else:
        host_slice = hosts[start : start + limit]
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
