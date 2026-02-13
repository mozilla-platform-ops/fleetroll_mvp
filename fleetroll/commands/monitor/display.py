"""Curses-based UI display for monitor command."""

from __future__ import annotations

import datetime as dt
import sqlite3
import time
from curses import error as curses_error
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cache import ShaInfoCache

from ...utils import get_log_file_size
from .curses_colors import CursesColors
from .data import (
    age_seconds,
    build_row_values,
    detect_common_fqdn_suffix,
    get_host_sort_key,
    humanize_duration,
    load_github_refs_from_db,
    load_tc_worker_data_from_db,
    resolve_last_ok_ts,
    strip_fqdn,
)
from .formatting import render_row_cells
from .header_renderer import HeaderInfo, HeaderRenderer
from .help_popup import draw_help_popup
from .types import cycle_os_filter


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
        db_conn: sqlite3.Connection,
        github_refs: dict[str, dict[str, Any]],
        sha_cache: ShaInfoCache | None = None,
    ) -> None:
        self.stdscr = stdscr
        self.hosts = hosts
        # Show just the filename if it's a path, to save space in the status line
        self.host_source = Path(host_source).name if "/" in host_source else host_source
        self.latest = latest
        self.latest_ok = latest_ok
        self.tc_data = tc_data
        self.db_conn = db_conn
        self.github_refs = github_refs
        self.sha_cache = sha_cache
        self._tc_poll_time = 0.0
        self._github_poll_time = 0.0
        self._sha_cache_poll_time = 0.0
        self.offset = 0
        self.col_offset = 0
        self.page_step = 1
        self.max_offset = 0
        self.max_col_offset = 0
        timestamps = [r.get("ts") for r in latest.values() if r.get("ts")]
        self.last_updated = max(timestamps, default=None) if timestamps else None
        self.fqdn_suffix = detect_common_fqdn_suffix(hosts)
        self.show_help = False
        self.sort_field = "host"  # Current sort field: "host" or "role"
        self.show_only_overrides = False  # Filter to show only hosts with overrides
        self.os_filter: str | None = None  # OS filter: None=all, "L"=Linux, "M"=macOS
        self.colors = CursesColors(stdscr)
        self.curses_mod = self.colors.curses_mod
        self.header_renderer = HeaderRenderer(safe_addstr=self.safe_addstr, colors=self.colors)
        self.log_size_warnings = self._check_log_sizes()

    def safe_addstr(self, row: int, col: int, text: str, attr: int = 0) -> None:
        try:
            if attr:
                self.stdscr.addstr(row, col, text, attr)
            else:
                self.stdscr.addstr(row, col, text)
        except curses_error:
            return

    def handle_key(self, key: int, *, draw: bool = True) -> bool:
        """Handle a keypress. Returns True if we should exit.

        Args:
            key: The key code from getch()
            draw: Whether to redraw immediately (default True)
        """
        # Handle help popup dismissal - any key closes help (except '?' which opens it)
        if self.show_help:
            # Don't dismiss on '?' to avoid immediate close when opening
            # Don't dismiss on -1 (timeout/no key pressed)
            if key != ord("?") and key != -1:
                self.show_help = False
                if draw:
                    # Fast refresh from cached screen instead of expensive redraw
                    self.stdscr.touchwin()
                    self.stdscr.refresh()
            return False  # Don't process the key that closed help

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
        elif key in (ord("s"), ord("S")):
            # Cycle between host -> role -> ovr_sha sort
            if self.sort_field == "host":
                self.sort_field = "role"
            elif self.sort_field == "role":
                self.sort_field = "ovr_sha"
            else:
                self.sort_field = "host"
            self.offset = 0  # Reset to first page on sort change
            self.needs_redraw = True
            self.draw_screen()
        elif key == ord("o"):
            # Toggle override filter
            self.show_only_overrides = not self.show_only_overrides
            self.offset = 0  # Reset to first page on filter change
            self.draw_screen()
        elif key == ord("O"):
            # Cycle OS filter
            self.os_filter = cycle_os_filter(self.os_filter)
            self.offset = 0  # Reset to first page on filter change
            self.draw_screen()
        return False

    def _check_log_sizes(self) -> list[str]:
        """Check log file sizes and return warnings for files over threshold.

        Returns:
            List of warning strings, e.g., ["audit: 120M", "obs: 105M"]
        """
        warn_threshold_mb = 100
        bytes_per_mb = 1024 * 1024

        warnings = []
        fleetroll_dir = Path.home() / ".fleetroll"

        # Check audit.jsonl
        audit_path = fleetroll_dir / "audit.jsonl"
        audit_size_mb = get_log_file_size(audit_path) / bytes_per_mb
        if audit_size_mb >= warn_threshold_mb:
            warnings.append(f"audit: {audit_size_mb:.0f}M")

        # Check SQLite database
        from ...db import get_db_path

        db_file = get_db_path()
        db_size_mb = get_log_file_size(db_file) / bytes_per_mb
        if db_size_mb >= warn_threshold_mb:
            warnings.append(f"db: {db_size_mb:.0f}M")

        return warnings

    def poll_tc_data(self) -> bool:
        """Check if TC data changed and reload if needed. Returns True if reloaded."""
        now = time.monotonic()
        if now - self._tc_poll_time < 5.0:
            return False
        self._tc_poll_time = now
        # Commit to end any stale read transaction and see latest writes
        self.db_conn.commit()
        new_data = load_tc_worker_data_from_db(self.db_conn, hosts=self.hosts)
        if new_data != self.tc_data:
            self.tc_data = new_data
            self._touch_updated()
            return True
        return False

    def poll_github_data(self) -> bool:
        """Check if GitHub refs changed and reload if needed. Returns True if reloaded."""
        now = time.monotonic()
        if now - self._github_poll_time < 5.0:
            return False
        self._github_poll_time = now
        # Commit to end any stale read transaction and see latest writes
        self.db_conn.commit()
        new_data = load_github_refs_from_db(self.db_conn)
        if new_data != self.github_refs:
            self.github_refs = new_data
            self._touch_updated()
            return True
        return False

    def _touch_updated(self) -> None:
        """Update last_updated timestamp to current UTC time."""
        self.last_updated = dt.datetime.now(dt.UTC).isoformat()

    def poll_sha_cache(self) -> bool:
        """Re-scan override/vault dirs if SHA cache is stale. Returns True if changed."""
        if self.sha_cache is None:
            return False
        now = time.monotonic()
        if now - self._sha_cache_poll_time < 30.0:
            return False
        self._sha_cache_poll_time = now
        old_overrides = dict(self.sha_cache.override_cache)
        old_vault = dict(self.sha_cache.vault_cache)
        self.sha_cache.override_cache.clear()
        self.sha_cache.vault_cache.clear()
        self.sha_cache.load_all()
        changed = (
            self.sha_cache.override_cache != old_overrides
            or self.sha_cache.vault_cache != old_vault
        )
        if changed:
            self._touch_updated()
        return changed

    def update_record(self, record: dict[str, Any]) -> None:
        self.latest[record["host"]] = record
        if record.get("ok"):
            self.latest_ok[record["host"]] = record
        self._touch_updated()

    def _compute_screen_metrics(self, *, host_count: int | None = None) -> dict[str, Any]:
        """Compute screen dimensions and pagination metrics.

        Args:
            host_count: Number of hosts to display (defaults to self.hosts length)

        Returns:
            Dictionary containing:
            - height: Terminal height
            - width: Terminal width
            - usable_width: Width minus 1 for margin
            - page_size: Number of host rows per page
            - total_pages: Total pagination pages
            - current_page: Current page number (1-indexed)
            - updated: Human-readable last updated time
        """
        if host_count is None:
            host_count = len(self.hosts)
        height, width = self.stdscr.getmaxyx()
        usable_width = max(width - 1, 0)
        page_size = max(height - 2, 0)
        self.page_step = max(page_size, 1)
        self.max_offset = max(host_count - page_size, 0)
        self.offset = min(self.offset, self.max_offset)
        total_pages = max((host_count + self.page_step - 1) // self.page_step, 1)
        current_page = min(((self.offset + self.page_step - 1) // self.page_step) + 1, total_pages)
        updated_age = age_seconds(self.last_updated) if self.last_updated else None
        updated = humanize_duration(updated_age) if updated_age is not None else "never"

        return {
            "height": height,
            "width": width,
            "usable_width": usable_width,
            "page_size": page_size,
            "total_pages": total_pages,
            "current_page": current_page,
            "updated": updated,
        }

    def _compute_column_widths(
        self,
        sorted_hosts: list[str],
    ) -> tuple[list[str], dict[str, str], dict[str, int]]:
        """Compute column labels and widths based on host data.

        Args:
            sorted_hosts: List of hostnames in display order

        Returns:
            Tuple of (all_columns, labels_dict, widths_dict) where:
            - all_columns is the canonical list of column names
            - labels_dict maps column name to header label
            - widths_dict maps column name to computed width
        """
        all_columns = [
            "host",
            "os",
            "role",
            "vlt_sha",
            "sha",
            "uptime",
            "pp_last",
            "pp_sha",
            "pp_exp",
            "pp_match",
            "tc_act",
            "tc_j_sf",
            "tc_quar",
            "data",
            "healthy",
        ]

        labels = {
            "host": "HOST",
            "uptime": "UPTIME",
            "role": "ROLE",
            "os": "OS",
            "sha": "OVR_SHA",
            "vlt_sha": "VLT_SHA",
            "tc_quar": "TC_QUAR",
            "tc_act": "TC_ACT",
            "tc_j_sf": "TC_T_DUR",
            "pp_last": "PP_LAST",
            "pp_exp": "PP_EXP",
            "pp_sha": "PP_SHA",
            "pp_match": "PP_MATCH",
            "healthy": "HEALTHY",
            "data": "DATA",
        }

        # Add sort indicator to active column
        sort_field_to_column = {
            "host": "host",
            "role": "role",
            "ovr_sha": "sha",
        }
        active_column = sort_field_to_column.get(self.sort_field)
        if active_column and active_column in labels:
            labels[active_column] = labels[active_column] + " *"

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
                sha_cache=self.sha_cache,
                github_refs=self.github_refs,
            )
            for col in all_columns:
                widths[col] = max(widths[col], len(values[col]))

        # Add padding for role/sha columns
        for col in ("role", "sha", "vlt_sha"):
            if col in widths:
                widths[col] += 2

        return all_columns, labels, widths

    def _compute_visible_columns(
        self,
        all_columns: list[str],
        *,
        widths: dict[str, int],
        usable_width: int,
    ) -> tuple[list[str], str]:
        """Determine which columns fit on screen with horizontal scrolling.

        Args:
            all_columns: Complete list of column names
            widths: Column name to width mapping
            usable_width: Available screen width

        Returns:
            Tuple of (visible_columns, scroll_indicator) where:
            - visible_columns: List of column names to display (includes frozen "host")
            - scroll_indicator: String showing scroll arrows and position
        """
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
            scroll_indicator = (
                f" [{arrows.strip()} {first_visible_idx}-{last_visible_idx}/{total_scrollable}]"
            )

        return columns, scroll_indicator

    def _compute_row_render_data(
        self,
        host: str,
    ) -> dict[str, Any]:
        """Compute all data needed to render a single host row.

        Args:
            host: Hostname to render

        Returns:
            Dictionary containing:
            - values: Cell values from build_row_values()
            - ts_value: Timestamp for coloring DATA column
            - tc_ts_value: TC timestamp for coloring
            - tc_worker_data: TC worker data dict
            - uptime_s: Uptime in seconds for coloring
            - tc_last_s: TC last active in seconds for coloring
            - tc_task_state: TaskCluster task state for coloring TC_T_DUR
            - pp_age_s: Puppet age in seconds for coloring
            - pp_failed: Whether puppet run failed
        """
        short_host = strip_fqdn(host)
        tc_worker_data = self.tc_data.get(short_host)
        values = build_row_values(
            host,
            self.latest.get(host),
            last_ok=self.latest_ok.get(host),
            tc_data=tc_worker_data,
            fqdn_suffix=self.fqdn_suffix,
            sha_cache=self.sha_cache,
            github_refs=self.github_refs,
        )
        ts_value = resolve_last_ok_ts(self.latest.get(host), last_ok=self.latest_ok.get(host))
        tc_ts_value = tc_worker_data.get("ts") if tc_worker_data else None
        uptime_value = values.get("uptime")
        uptime_s = None
        if uptime_value and uptime_value not in ("-", "?"):
            observed = (self.latest_ok.get(host) or self.latest.get(host) or {}).get("observed", {})
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

    def _get_sort_key(self, hostname: str) -> tuple:
        """Extract sort key for a host based on current sort field.

        Returns tuple for stable multi-level sorting.
        Always includes hostname as secondary sort.
        """
        return get_host_sort_key(
            hostname, sort_field=self.sort_field, latest=self.latest, latest_ok=self.latest_ok
        )

    def _has_overrides(self, hostname: str) -> bool:
        """Check if a host has override configuration.

        Args:
            hostname: Hostname to check

        Returns:
            True if host has overrides, False otherwise
        """
        short_host = strip_fqdn(hostname)
        tc_worker_data = self.tc_data.get(short_host)
        values = build_row_values(
            hostname,
            self.latest.get(hostname),
            last_ok=self.latest_ok.get(hostname),
            tc_data=tc_worker_data,
            fqdn_suffix=self.fqdn_suffix,
            sha_cache=self.sha_cache,
            github_refs=self.github_refs,
        )
        sha_value = values.get("sha", "")
        return sha_value not in ("-", "?", "")

    def _get_host_os(self, hostname: str) -> str:
        """Get the OS abbreviation for a host.

        Args:
            hostname: Hostname to check

        Returns:
            OS abbreviation ("L", "M", "W", etc.)
        """
        short_host = strip_fqdn(hostname)
        tc_worker_data = self.tc_data.get(short_host)
        values = build_row_values(
            hostname,
            self.latest.get(hostname),
            last_ok=self.latest_ok.get(hostname),
            tc_data=tc_worker_data,
            fqdn_suffix=self.fqdn_suffix,
            sha_cache=self.sha_cache,
            github_refs=self.github_refs,
        )
        return values["os"]

    def _draw_host_row(
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
            render_data: Pre-computed render data from _compute_row_render_data()
            columns: Ordered list of columns to display
            widths: Column name to width mapping
            color_maps: Categorical color mappings from _prepare_categorical_colors()
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
                self.safe_addstr(row, col, cell[1:])
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
                        self.safe_addstr(row, col, rest)
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
                        self.safe_addstr(row, col, prefix)
                        col += len(prefix)
                        self.safe_addstr(row, col, humanhash, marker_attr)
                        col += len(humanhash)
                        self.safe_addstr(row, col, info_part)
                        col += len(info_part)
                        if padding:
                            self.safe_addstr(row, col, padding)
                            col += len(padding)
                    else:
                        # No info - use original logic
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

    def draw_screen(self) -> None:
        self.stdscr.erase()

        # Sort and filter hosts
        all_hosts_sorted = sorted(self.hosts, key=self._get_sort_key)
        sorted_hosts = all_hosts_sorted
        if self.show_only_overrides:
            sorted_hosts = [h for h in sorted_hosts if self._has_overrides(h)]
        if self.os_filter is not None:
            sorted_hosts = [h for h in sorted_hosts if self._get_host_os(h) == self.os_filter]

        # Compute screen metrics with filtered host count
        metrics = self._compute_screen_metrics(host_count=len(sorted_hosts))

        # Compute column configuration
        all_columns, labels, widths = self._compute_column_widths(sorted_hosts)

        # Determine visible columns
        columns, scroll_indicator = self._compute_visible_columns(
            all_columns, widths=widths, usable_width=metrics["usable_width"]
        )

        # Draw headers
        # Pass filtered count if any filter is active
        filtered_count = (
            len(sorted_hosts) if (self.show_only_overrides or self.os_filter is not None) else None
        )
        header_info = HeaderInfo(
            sort_field=self.sort_field,
            show_only_overrides=self.show_only_overrides,
            os_filter=self.os_filter,
            fqdn_suffix=self.fqdn_suffix,
            host_source=self.host_source,
            total_hosts=len(self.hosts),
            log_size_warnings=self.log_size_warnings,
        )
        header_rows = self.header_renderer.draw_top_header(
            header_info=header_info,
            total_pages=metrics["total_pages"],
            current_page=metrics["current_page"],
            scroll_indicator=scroll_indicator,
            updated=metrics["updated"],
            usable_width=metrics["usable_width"],
            filtered_host_count=filtered_count,
        )
        self.header_renderer.draw_column_header(
            labels=labels, columns=columns, widths=widths, header_row=header_rows
        )

        # Adjust page size if using two-line header
        page_size = metrics["page_size"]
        if header_rows == 2:
            page_size = max(page_size - 1, 0)

        # Prepare categorical colors
        color_maps = self.colors.prepare_categorical_colors(
            all_hosts_sorted,
            latest=self.latest,
            latest_ok=self.latest_ok,
            tc_data=self.tc_data,
            fqdn_suffix=self.fqdn_suffix,
            sha_cache=self.sha_cache,
            github_refs=self.github_refs,
        )

        host_slice = (
            sorted_hosts[self.offset :]
            if page_size <= 0
            else sorted_hosts[self.offset : self.offset + page_size]
        )

        # Draw host rows
        for idx, host in enumerate(host_slice, start=1):
            row = idx + header_rows
            if row >= metrics["height"]:
                break
            render_data = self._compute_row_render_data(host)
            self._draw_host_row(
                row, render_data=render_data, columns=columns, widths=widths, color_maps=color_maps
            )

        # Refresh main screen first, then draw help popup on top
        if self.show_help:
            self.stdscr.noutrefresh()
            draw_help_popup(self.stdscr, self.curses_mod, color_enabled=self.colors.color_enabled)
            self.curses_mod.doupdate()
        else:
            self.stdscr.refresh()
