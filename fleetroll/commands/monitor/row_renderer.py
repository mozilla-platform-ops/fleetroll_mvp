"""Row rendering for the monitor display."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .curses_colors import CursesColors

from .data import age_seconds, build_row_values, resolve_last_ok_ts, strip_fqdn
from .formatting import render_row_cells


class RowRenderer:
    """Renders individual host rows for the monitor display.

    This class encapsulates the complex row rendering logic, including:
    - Computing all render data (timestamps, colors, states)
    - Column-specific coloring (DATA, TC_ACT, PP_LAST, etc.)
    - Categorical coloring (role markers, SHA prefixes, vault humanhash)
    """

    def __init__(
        self,
        *,
        safe_addstr: Callable[[int, int, str, int], None],
        colors: CursesColors,
    ) -> None:
        """Initialize the row renderer.

        Args:
            safe_addstr: Callable to write text to screen (row, col, text, attr)
            colors: CursesColors instance for color attributes
        """
        self.safe_addstr = safe_addstr
        self.colors = colors

    def compute_row_render_data(
        self,
        host: str,
        *,
        latest: dict[str, dict[str, Any]],
        latest_ok: dict[str, dict[str, Any]],
        tc_data: dict[str, dict[str, Any]],
        fqdn_suffix: str | None,
        sha_cache,
        github_refs: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute all data needed to render a single host row.

        Args:
            host: Hostname to render
            latest: Latest audit records by hostname
            latest_ok: Latest successful audit records by hostname
            tc_data: TaskCluster worker data by short hostname
            fqdn_suffix: Optional common FQDN suffix
            sha_cache: Optional SHA cache
            github_refs: GitHub reference data

        Returns:
            Dictionary containing:
            - values: Cell values from build_row_values()
            - ts_value: Timestamp for coloring DATA column
            - tc_ts_value: TC timestamp for coloring
            - tc_worker_data: TC worker data dict
            - uptime_s: Uptime in seconds for coloring
            - tc_act_s: TC last active in seconds for coloring
            - tc_task_state: TaskCluster task state for coloring TC_T_DUR
            - pp_age_s: Puppet age in seconds for coloring
            - pp_failed: Whether puppet run failed
        """
        short_host = strip_fqdn(host)
        tc_worker_data = tc_data.get(short_host)
        values = build_row_values(
            host,
            latest.get(host),
            last_ok=latest_ok.get(host),
            tc_data=tc_worker_data,
            fqdn_suffix=fqdn_suffix,
            sha_cache=sha_cache,
            github_refs=github_refs,
        )
        ts_value = resolve_last_ok_ts(latest.get(host), last_ok=latest_ok.get(host))
        tc_ts_value = tc_worker_data.get("ts") if tc_worker_data else None
        uptime_value = values.get("uptime")
        uptime_s = None
        if uptime_value and uptime_value not in ("-", "?"):
            observed = (latest_ok.get(host) or latest.get(host) or {}).get("observed", {})
            uptime_s = observed.get("uptime_s")

        # Calculate TC_ACT in seconds for coloring
        tc_act_s = None
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
                    tc_act_s = max(int((scan_dt - last_active_dt).total_seconds()), 0)
                except (ValueError, AttributeError):
                    pass

        # Extract TC task state for coloring TC_T_DUR
        tc_task_state = tc_worker_data.get("task_state") if tc_worker_data else None

        # Calculate puppet data for coloring (relative to audit time)
        host_record = latest_ok.get(host) or latest.get(host) or {}
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

        return {
            "host": host,
            "values": values,
            "ts_value": ts_value,
            "tc_ts_value": tc_ts_value,
            "tc_worker_data": tc_worker_data,
            "uptime_s": uptime_s,
            "tc_act_s": tc_act_s,
            "tc_task_state": tc_task_state,
            "pp_age_s": pp_age_s,
            "pp_failed": pp_success is False,
        }

    def draw_host_row(
        self,
        row: int,
        *,
        render_data: dict[str, Any],
        columns: list[str],
        widths: dict[str, int],
        color_maps: dict[str, dict[str, int]],
    ) -> None:
        """Draw a single host row with appropriate coloring.

        Args:
            row: Screen row number to draw at
            render_data: Pre-computed render data from compute_row_render_data()
            columns: Ordered list of columns to display
            widths: Column name to width mapping
            color_maps: Categorical color mappings (sha, vlt_sha, role)
        """
        values = render_data["values"]
        ts_value = render_data["ts_value"]
        tc_ts_value = render_data["tc_ts_value"]
        uptime_s = render_data["uptime_s"]
        tc_act_s = render_data["tc_act_s"]
        tc_task_state = render_data["tc_task_state"]
        pp_age_s = render_data["pp_age_s"]
        pp_failed = render_data["pp_failed"]

        sha_colors = color_maps["sha"]
        vlt_sha_colors = color_maps["vlt_sha"]
        role_colors = color_maps["role"]

        row_cells = render_row_cells(values, columns=columns, widths=widths)
        col = 0
        for col_name, cell in zip(columns, row_cells):
            if col_name != columns[0]:
                self.safe_addstr(row, col, " | ", 0)
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
                attr = self.colors.last_ok_attr(max_age_s)
                self.safe_addstr(row, col, cell, attr)
                col += len(cell)
                continue
            if col_name == "tc_act":
                # Apply color based on TC last active time
                attr = self.colors.tc_act_attr(tc_act_s)
                self.safe_addstr(row, col, cell, attr)
                col += len(cell)
                continue
            if col_name == "pp_last":
                attr = self.colors.pp_last_attr(pp_age_s, failed=pp_failed)
                self.safe_addstr(row, col, cell, attr)
                col += len(cell)
                continue
            if col_name == "pp_match":
                attr = self.colors.pp_match_attr(values.get("pp_match", "-"))
                self.safe_addstr(row, col, cell, attr)
                col += len(cell)
                continue
            if col_name == "healthy":
                attr = self.colors.ro_health_attr(values.get("healthy", "-"))
                self.safe_addstr(row, col, cell, attr)
                col += len(cell)
                continue
            if col_name == "tc_quar":
                attr = self.colors.tc_quar_attr(values.get("tc_quar", "-"))
                self.safe_addstr(row, col, cell, attr)
                col += len(cell)
                continue
            if col_name == "tc_j_sf":
                # Color TC_T_DUR based on task completion state
                attr = self.colors.tc_j_sf_attr(tc_task_state)
                self.safe_addstr(row, col, cell, attr)
                col += len(cell)
                continue
            if col_name == "uptime":
                attr = self.colors.uptime_attr(uptime_s)
            else:
                attr = 0
            if col_name == "role" and cell.startswith("# "):
                marker_attr = role_colors.get(values.get("role", ""), 0)
                self.safe_addstr(row, col, "#", marker_attr)
                col += 1
                self.safe_addstr(row, col, cell[1:], 0)
                col += len(cell) - 1
            elif col_name == "sha":
                # OVR_SHA: Color the 8-char SHA prefix
                full_value = values.get("sha", "")
                if full_value not in ("-", "?") and len(full_value) >= 8:
                    marker_attr = sha_colors.get(values.get("sha", ""), 0)
                    sha_prefix = cell[:8]
                    rest = cell[8:]
                    self.safe_addstr(row, col, sha_prefix, marker_attr)
                    col += len(sha_prefix)
                    if rest:
                        self.safe_addstr(row, col, rest, 0)
                        col += len(rest)
                else:
                    self.safe_addstr(row, col, cell, attr)
                    col += len(cell)
            elif col_name == "vlt_sha":
                # VLT_SHA: Color the humanhash (unchanged)
                full_value = values.get("vlt_sha", "")
                width = widths.get("vlt_sha", 0)
                if (
                    full_value
                    and full_value not in ("-", "?")
                    and " " in full_value
                    and len(full_value) <= width
                ):
                    marker_attr = vlt_sha_colors.get(values.get("vlt_sha", ""), 0)
                    # Find the humanhash position (before the parenthesis if present)
                    # Format: "SHA humanhash (info)" or "SHA humanhash"
                    paren_idx = full_value.find(" (")
                    if paren_idx != -1:
                        # Has info in parentheses - find space before humanhash
                        before_paren = full_value[:paren_idx]
                        split_idx = before_paren.rfind(" ")
                        prefix = full_value[: split_idx + 1]
                        humanhash = before_paren[split_idx + 1 :]
                        info_part = full_value[paren_idx:]
                        padding = " " * (width - len(full_value))
                        self.safe_addstr(row, col, prefix, 0)
                        col += len(prefix)
                        self.safe_addstr(row, col, humanhash, marker_attr)
                        col += len(humanhash)
                        self.safe_addstr(row, col, info_part, 0)
                        col += len(info_part)
                        if padding:
                            self.safe_addstr(row, col, padding, 0)
                            col += len(padding)
                    else:
                        # No info - use original logic
                        split_idx = full_value.rfind(" ")
                        prefix = full_value[: split_idx + 1]
                        suffix = full_value[split_idx + 1 :]
                        padding = " " * (width - len(full_value))
                        self.safe_addstr(row, col, prefix, 0)
                        col += len(prefix)
                        self.safe_addstr(row, col, suffix, marker_attr)
                        col += len(suffix)
                        if padding:
                            self.safe_addstr(row, col, padding, 0)
                            col += len(padding)
                else:
                    self.safe_addstr(row, col, cell, attr)
                    col += len(cell)
            else:
                self.safe_addstr(row, col, cell, attr)
                col += len(cell)
