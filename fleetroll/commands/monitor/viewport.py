"""Horizontal clipping shared by table headers and colored row spans."""

from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass

WriteSpan = Callable[[int, int, str, int], None]


@dataclass(frozen=True)
class HorizontalViewport:
    frozen_width: int
    usable_width: int
    content_width: int
    offset: int
    columns: tuple[tuple[str, int, int], ...]
    scroll_positions: tuple[int, ...]

    @classmethod
    def for_columns(
        cls,
        columns: list[str],
        *,
        widths: dict[str, int],
        usable_width: int,
        offset: int,
    ) -> "HorizontalViewport":
        scrollable = [col for col in columns if col != "host"]
        frozen_width = widths["host"] + (3 if scrollable else 0)
        content_width = sum(widths[col] for col in scrollable) + 3 * max(len(scrollable) - 1, 0)
        available = max(usable_width - frozen_width, 0)
        max_offset = max(content_width - available, 0) if available else 0
        positions = {0, max_offset}
        column_bounds = []
        start = 0
        for col in scrollable:
            end = start + widths[col]
            column_bounds.append((col, start, end))
            if start <= max_offset:
                positions.add(start)
                if widths[col] > available:
                    # Oversized cells need overlapping views; ordinary cells
                    # retain one jump per column, including their separator.
                    positions.update(
                        range(
                            start + max(available - 1, 1),
                            min(end, max_offset + 1),
                            max(available - 1, 1),
                        )
                    )
            start = end + 3
        return cls(
            frozen_width=frozen_width,
            usable_width=max(usable_width, 0),
            content_width=content_width,
            offset=min(max(offset, 0), max_offset),
            columns=tuple(column_bounds),
            scroll_positions=tuple(sorted(positions)),
        )

    @property
    def width(self) -> int:
        return max(self.usable_width - self.frozen_width, 0)

    @property
    def max_offset(self) -> int:
        return max(self.content_width - self.width, 0) if self.width else 0

    @property
    def next_offset(self) -> int:
        index = min(
            bisect_right(self.scroll_positions, self.offset), len(self.scroll_positions) - 1
        )
        return self.scroll_positions[index]

    @property
    def previous_offset(self) -> int:
        return self.scroll_positions[max(bisect_left(self.scroll_positions, self.offset) - 1, 0)]

    @property
    def indicator(self) -> str:
        if not self.max_offset:
            return ""
        arrows = ("◀" if self.offset else "") + ("▶" if self.offset < self.max_offset else "")
        visible = [
            i
            for i, (_, start, end) in enumerate(self.columns)
            if start < self.offset + self.width and end > self.offset
        ]
        if not visible:
            return ""
        return f" [{arrows} {visible[0] + 1}-{visible[-1] + 1}/{len(self.columns)}]"

    def wrap(self, write: WriteSpan) -> WriteSpan:
        """Clip virtual table spans, retaining their color and frozen HOST."""

        def draw(row: int, col: int, text: str, attr: int = 0) -> None:
            # A span may cross the frozen boundary (e.g. a header or separator).
            frozen_end = min(col + len(text), self.frozen_width, self.usable_width)
            frozen_start = max(col, 0)
            if frozen_start < frozen_end:
                write(row, frozen_start, text[frozen_start - col : frozen_end - col], attr)
            start = max(col, self.frozen_width + self.offset)
            end = min(col + len(text), self.frozen_width + self.offset + self.width)
            if start < end:
                write(row, start - self.offset, text[start - col : end - col], attr)

        return draw
