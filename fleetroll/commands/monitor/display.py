"""Curses-based UI display for monitor command."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from curses import error as curses_error
from importlib.metadata import version as get_version
from pathlib import Path
from typing import Any

from .data import (
    age_seconds,
    build_row_values,
    detect_common_fqdn_suffix,
    humanize_duration,
    load_tc_worker_data,
    resolve_last_ok_ts,
    strip_fqdn,
)
from .formatting import clip_cell, render_row_cells
from .types import COLUMN_GUIDE_TEXT, FLEETROLL_MASCOT


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

    def _compute_screen_metrics(self) -> dict[str, Any]:
        """Compute screen dimensions and pagination metrics.

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
        height, width = self.stdscr.getmaxyx()
        usable_width = max(width - 1, 0)
        page_size = max(height - 2, 0)
        self.page_step = max(page_size, 1)
        self.max_offset = max(len(self.hosts) - page_size, 0)
        self.offset = min(self.offset, self.max_offset)
        total_pages = max((len(self.hosts) + self.page_step - 1) // self.page_step, 1)
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

        return all_columns, labels, widths

    def _prepare_categorical_colors(
        self,
        sorted_hosts: list[str],
    ) -> dict[str, dict[str, int]]:
        """Build color maps for categorical columns (role, sha, vlt_sha).

        Args:
            sorted_hosts: List of hostnames to analyze

        Returns:
            Dictionary mapping column name to color map:
            {
                "sha": {value: curses_attr, ...},
                "vlt_sha": {value: curses_attr, ...},
                "role": {value: curses_attr, ...}
            }
        """
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

        return {
            "sha": sha_colors,
            "vlt_sha": vlt_sha_colors,
            "role": role_colors,
        }

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
            scroll_indicator = f" [{arrows.strip()} cols {first_visible_idx}-{last_visible_idx}/{total_scrollable}]"

        return columns, scroll_indicator

    def _draw_column_header(
        self,
        *,
        labels: dict[str, str],
        columns: list[str],
        widths: dict[str, int],
    ) -> None:
        """Render the column header labels with separators.

        Args:
            labels: Column name to label text mapping
            columns: Ordered list of columns to display
            widths: Column name to width mapping
        """
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

    def _draw_top_header(
        self,
        *,
        total_pages: int,
        current_page: int,
        scroll_indicator: str,
        updated: str,
        usable_width: int,
    ) -> None:
        """Render the top information banner with metadata.

        Args:
            total_pages: Total number of pagination pages
            current_page: Current page number (1-indexed)
            scroll_indicator: Column scroll status text
            updated: Human-readable last update time
            usable_width: Available screen width
        """
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
        )
        ts_value = resolve_last_ok_ts(self.latest.get(host), last_ok=self.latest_ok.get(host))
        tc_ts_value = tc_worker_data.get("ts") if tc_worker_data else None
        uptime_value = values.get("uptime")
        uptime_s = None
        if uptime_value and uptime_value not in ("-", "?"):
            observed = (self.latest_ok.get(host) or self.latest.get(host) or {}).get("observed", {})
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
            "tc_last_s": tc_last_s,
            "pp_age_s": pp_age_s,
            "pp_failed": pp_success is False,
        }

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
        tc_last_s = render_data["tc_last_s"]
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
                attr = self.pp_last_attr(pp_age_s, failed=pp_failed)
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

    def draw_screen(self) -> None:
        self.stdscr.erase()

        # Compute screen metrics
        metrics = self._compute_screen_metrics()
        sorted_hosts = sorted(self.hosts)

        # Compute column configuration
        all_columns, labels, widths = self._compute_column_widths(sorted_hosts)

        # Determine visible columns
        columns, scroll_indicator = self._compute_visible_columns(
            all_columns, widths=widths, usable_width=metrics["usable_width"]
        )

        # Draw headers
        self._draw_top_header(
            total_pages=metrics["total_pages"],
            current_page=metrics["current_page"],
            scroll_indicator=scroll_indicator,
            updated=metrics["updated"],
            usable_width=metrics["usable_width"],
        )
        self._draw_column_header(labels=labels, columns=columns, widths=widths)

        # Prepare categorical colors
        color_maps = self._prepare_categorical_colors(sorted_hosts)

        host_slice = (
            sorted_hosts[self.offset :]
            if metrics["page_size"] <= 0
            else sorted_hosts[self.offset : self.offset + metrics["page_size"]]
        )

        # Draw host rows
        for idx, host in enumerate(host_slice, start=1):
            row = idx + 1
            if row >= metrics["height"]:
                break
            render_data = self._compute_row_render_data(host)
            self._draw_host_row(
                row, render_data=render_data, columns=columns, widths=widths, color_maps=color_maps
            )

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
