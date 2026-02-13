"""Header rendering for the monitor display."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version as get_version
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .curses_colors import CursesColors

from .formatting import clip_cell, render_row_cells
from .types import os_filter_label


@dataclass
class HeaderInfo:
    """Display state parameters for header rendering.

    This dataclass bundles the display-state parameters needed for header rendering
    to avoid long parameter lists.

    Attributes:
        sort_field: Current sort field ("host", "role", or "ovr_sha")
        show_only_overrides: Whether override filter is active
        os_filter: OS filter value (None, "L", or "M")
        fqdn_suffix: Optional common FQDN suffix
        host_source: Source of host list (filename or identifier)
        total_hosts: Total number of hosts in the source
        log_size_warnings: List of log size warning strings
    """

    sort_field: str
    show_only_overrides: bool
    os_filter: str | None
    fqdn_suffix: str | None
    host_source: str
    total_hosts: int
    log_size_warnings: list[str]


class HeaderRenderer:
    """Renders header sections (top banner and column labels) for the monitor display.

    This class encapsulates the complex header rendering logic, including:
    - Two-line vs single-line layout decisions
    - Colored sections (fleetroll brand, data sections, warnings)
    - Sort indicators and filter status
    - Pagination and scroll indicators
    """

    def __init__(
        self,
        *,
        safe_addstr: Callable[[int, int, str, int], None],
        colors: CursesColors,
    ) -> None:
        """Initialize the header renderer.

        Args:
            safe_addstr: Callable to write text to screen (row, col, text, attr)
            colors: CursesColors instance for color attributes
        """
        self.safe_addstr = safe_addstr
        self.colors = colors

    def draw_column_header(
        self,
        *,
        labels: dict[str, str],
        columns: list[str],
        widths: dict[str, int],
        header_row: int = 1,
    ) -> None:
        """Render the column header labels with separators.

        Args:
            labels: Column name to label text mapping
            columns: Ordered list of columns to display
            widths: Column name to width mapping
            header_row: Row number for the column header (default 1)
        """
        # Colored asterisk attribute (yellow to stand out from magenta headers)
        asterisk_attr = self.colors.curses_mod.color_pair(2) if self.colors.color_enabled else 0

        header_parts = render_row_cells(
            labels, columns=columns, widths=widths, include_marker=False
        )
        header_line = " | ".join(header_parts)
        if " | " in header_line:
            parts = header_line.split(" | ")
            col = 0
            for idx, part in enumerate(parts):
                if idx:
                    self.safe_addstr(header_row, col, " | ", 0)
                    col += 3
                # Check if this part contains the sort indicator (strip padding first)
                if " *" in part:
                    # Find position of " *" and split there
                    asterisk_pos = part.find(" *")
                    base_part = part[:asterisk_pos]
                    padding = part[asterisk_pos + 2 :]  # Everything after " *"
                    self.safe_addstr(header_row, col, base_part, self.colors.attrs.column_attr)
                    col += len(base_part)
                    self.safe_addstr(header_row, col, " *", asterisk_attr)
                    col += 2
                    if padding:
                        self.safe_addstr(header_row, col, padding, self.colors.attrs.column_attr)
                        col += len(padding)
                else:
                    self.safe_addstr(header_row, col, part, self.colors.attrs.column_attr)
                    col += len(part)
        # Single column case
        elif " *" in header_line:
            asterisk_pos = header_line.find(" *")
            base_line = header_line[:asterisk_pos]
            padding = header_line[asterisk_pos + 2 :]
            self.safe_addstr(header_row, 0, base_line, self.colors.attrs.column_attr)
            self.safe_addstr(header_row, len(base_line), " *", asterisk_attr)
            if padding:
                self.safe_addstr(
                    header_row, len(base_line) + 2, padding, self.colors.attrs.column_attr
                )
        else:
            self.safe_addstr(header_row, 0, header_line, self.colors.attrs.column_attr)

    def render_header_line(
        self,
        text: str,
        *,
        row: int,
        is_right_side: bool = False,
        start_col: int = 0,
        log_size_warnings: list[str],
    ) -> None:
        """Render a single header line with appropriate coloring.

        Args:
            text: The text to render
            row: The row number to render on
            is_right_side: Whether this is the right side (data) section
            start_col: Starting column position (for right-alignment)
            log_size_warnings: List of log size warning strings
        """
        if is_right_side:
            # Right side: color fqdn=/source= sections
            right_start = "fqdn=" if "fqdn=" in text else "source="
            if right_start in text:
                # Handle log warnings on the right side
                if log_size_warnings and "⚠ Large logs:" in text:
                    # Split: warning | data
                    if " | " in text:
                        warning_part, data_part = text.split(" | ", 1)
                        col = start_col
                        # Write warning in yellow
                        if warning_part:
                            self.safe_addstr(row, col, warning_part, self.colors.attrs.warning_attr)
                            col += len(warning_part)
                            self.safe_addstr(row, col, " | ", 0)
                            col += 3
                        # Write data part
                        if right_start in data_part:
                            before_data, after_data = data_part.split(right_start, 1)
                            self.safe_addstr(row, col, before_data, 0)
                            col += len(before_data)
                            self.safe_addstr(
                                row, col, right_start, self.colors.attrs.header_data_attr
                            )
                            col += len(right_start)
                            self.safe_addstr(
                                row, col, after_data, self.colors.attrs.header_data_attr
                            )
                        else:
                            self.safe_addstr(row, col, data_part, 0)
                    else:
                        self.safe_addstr(row, start_col, text, 0)
                else:
                    # No warning, just color the data section
                    left_part, right_part = text.rsplit(right_start, 1)
                    self.safe_addstr(row, start_col, left_part, 0)
                    self.safe_addstr(
                        row,
                        start_col + len(left_part),
                        right_start,
                        self.colors.attrs.header_data_attr,
                    )
                    self.safe_addstr(
                        row,
                        start_col + len(left_part) + len(right_start),
                        right_part,
                        self.colors.attrs.header_data_attr,
                    )
            else:
                self.safe_addstr(row, start_col, text, 0)
        # Left side: color "fleetroll X.Y.Z"
        elif text.startswith("fleetroll"):
            colon_pos = text.find(":")
            if colon_pos > 0:
                fleetroll_with_version = text[:colon_pos]
            else:
                fleetroll_with_version = "fleetroll"
            self.safe_addstr(row, 0, fleetroll_with_version, self.colors.attrs.fleetroll_attr)
            self.safe_addstr(
                row, len(fleetroll_with_version), text[len(fleetroll_with_version) :], 0
            )
        else:
            self.safe_addstr(row, 0, text, 0)

    def draw_top_header(
        self,
        *,
        header_info: HeaderInfo,
        total_pages: int,
        current_page: int,
        scroll_indicator: str,
        updated: str,
        usable_width: int,
        filtered_host_count: int | None = None,
    ) -> int:
        """Render the top information banner with metadata.

        Args:
            header_info: Display state parameters
            total_pages: Total number of pagination pages
            current_page: Current page number (1-indexed)
            scroll_indicator: Column scroll status text
            updated: Human-readable last update time
            usable_width: Available screen width
            filtered_host_count: Optional filtered host count

        Returns:
            Number of rows used by the header (1 or 2)
        """
        try:
            ver = get_version("fleetroll")
        except Exception:
            ver = "?"
        left = f"fleetroll {ver} [? for help] sort={header_info.sort_field}"
        if header_info.show_only_overrides:
            left = f"{left}, filter=overrides"
        os_label = os_filter_label(header_info.os_filter)
        if os_label is not None:
            left = f"{left}, os={os_label}"
        if total_pages > 1:
            # Add page indicator with up/down arrows
            arrows = ""
            if current_page > 1:
                arrows += "▲ "
            if current_page < total_pages:
                arrows += "▼"
            page_indicator = f" [{arrows.strip()} {current_page}/{total_pages}]"
            left = f"{left}{page_indicator}"
        if scroll_indicator:
            left = f"{left}{scroll_indicator}"

        # Build right section with optional log size warning
        fqdn_part = f"fqdn={header_info.fqdn_suffix}, " if header_info.fqdn_suffix else ""
        if filtered_host_count is not None:
            hosts_display = f"{filtered_host_count}/{header_info.total_hosts}"
        else:
            hosts_display = str(header_info.total_hosts)
        right = (
            f"{fqdn_part}source={header_info.host_source}, hosts={hosts_display}, updated={updated}"
        )
        if header_info.log_size_warnings:
            warnings_text = ", ".join(header_info.log_size_warnings)
            right = f"⚠ Large logs: {warnings_text} (run 'fleetroll maintain') | {right}"
        # Determine if we need two-line mode
        use_two_lines = usable_width > 0 and len(left) + 1 + len(right) > usable_width

        if use_two_lines:
            # Two-line mode: left on row 0, right on row 1 (right-aligned)
            # Truncate left only if it exceeds usable_width on its own
            if len(left) > usable_width:
                left = clip_cell(left, usable_width).rstrip()
            # Truncate right only if it exceeds usable_width on its own
            if len(right) > usable_width:
                right = clip_cell(right, usable_width).rstrip()
            # Render left on row 0, right on row 1 (right-aligned)
            self.render_header_line(left, row=0, log_size_warnings=header_info.log_size_warnings)
            right_start_col = max(usable_width - len(right), 0)
            self.render_header_line(
                right,
                row=1,
                is_right_side=True,
                start_col=right_start_col,
                log_size_warnings=header_info.log_size_warnings,
            )
            return 2

        # Single-line mode: fit both on one line
        if usable_width > 0:
            padding = max(usable_width - len(left) - len(right), 1)
            header = f"{left}{' ' * padding}{right}"
        else:
            header = left

        # Render single-line header on row 0
        if header.startswith("fleetroll"):
            # Color "fleetroll X.Y.Z" (find first colon to know where version ends)
            colon_pos = header.find(":")
            if colon_pos > 0:
                fleetroll_with_version = header[:colon_pos]
            else:
                fleetroll_with_version = "fleetroll"
            self.safe_addstr(0, 0, fleetroll_with_version, self.colors.attrs.fleetroll_attr)
            header_offset = len(fleetroll_with_version)
            # Handle warning section separately if present
            if header_info.log_size_warnings and "⚠ Large logs:" in header:
                # Split into left part, warning, and data part
                middle = header[header_offset:]  # After "fleetroll X.Y.Z"
                if " | " in middle:
                    warning_part, data_part = middle.split(" | ", 1)
                    # Find where data starts (fqdn= or source=)
                    right_start = "fqdn=" if "fqdn=" in data_part else "source="
                    if right_start in data_part:
                        before_data, after_data = data_part.split(right_start, 1)
                        col = header_offset
                        # Write left part before warning
                        if warning_part:
                            self.safe_addstr(0, col, before_data, 0)
                            col += len(before_data)
                        # Write warning in yellow
                        warning_text = warning_part.strip()
                        if warning_text:
                            self.safe_addstr(0, col, warning_text, self.colors.attrs.warning_attr)
                            col += len(warning_text)
                            self.safe_addstr(0, col, " | ", 0)
                            col += 3
                        # Write data part in header color
                        self.safe_addstr(0, col, right_start, self.colors.attrs.header_data_attr)
                        col += len(right_start)
                        self.safe_addstr(0, col, after_data, self.colors.attrs.header_data_attr)
                    else:
                        self.safe_addstr(0, header_offset, middle, 0)
                else:
                    self.safe_addstr(0, header_offset, middle, 0)
            else:
                # No warning, original logic
                right_start = "fqdn=" if "fqdn=" in header else "source="
                if right_start in header:
                    left_part, right_part = header[header_offset:].rsplit(right_start, 1)
                    self.safe_addstr(0, header_offset, left_part, 0)
                    self.safe_addstr(
                        0,
                        header_offset + len(left_part),
                        right_start,
                        self.colors.attrs.header_data_attr,
                    )
                    self.safe_addstr(
                        0,
                        header_offset + len(left_part) + len(right_start),
                        right_part,
                        self.colors.attrs.header_data_attr,
                    )
                else:
                    self.safe_addstr(0, header_offset, header[header_offset:], 0)
        else:
            self.safe_addstr(0, 0, header, 0)
        return 1
