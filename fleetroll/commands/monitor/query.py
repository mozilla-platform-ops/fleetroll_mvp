"""Filter and sort query parsing for the monitor display."""

from __future__ import annotations

import operator as _op
from dataclasses import dataclass, field

from .data import parse_duration

# Columns whose values are time durations (for numeric comparison)
TIME_COLUMNS = frozenset({"pp_last", "tc_act", "uptime", "tc_j_sf"})

# All valid column names (used for validation)
KNOWN_COLUMNS = frozenset(
    {
        "host",
        "os",
        "role",
        "vlt_sha",
        "sha",
        "ovr_bch",  # alias for "sha" (display label "OVR_BCH")
        "ovr_sha",  # legacy alias
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
        "note",
    }
)

# Column aliases: display-label names → internal column key
COLUMN_ALIASES: dict[str, str] = {
    "ovr_bch": "sha",  # display label "OVR_BCH" → internal column "sha"
    "ovr_sha": "sha",  # legacy alias
}

# Operator tokens, longest-match first to avoid partial matches
_OPERATORS = (">=", "<=", "!=", ">", "<", "=", "~")


@dataclass
class FilterCondition:
    """A single filter condition: column op value."""

    column: str  # lowercase, e.g. "pp_last"
    op: str  # one of _OPERATORS
    value: str  # raw string as typed


@dataclass
class SortKey:
    """A single sort key with direction."""

    column: str  # lowercase
    direction: str  # "asc" or "desc"


@dataclass
class Query:
    """Parsed filter/sort query."""

    conditions: list[FilterCondition] = field(default_factory=list)
    sort_keys: list[SortKey] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.conditions and not self.sort_keys

    def has_sort(self) -> bool:
        return bool(self.sort_keys)


def parse_query(text: str) -> Query:
    """Parse a filter/sort query string into a Query object.

    Syntax: COLUMN OP VALUE [COLUMN OP VALUE ...] [sort:COL[:asc|desc][,...]]

    Examples:
        pp_last>20h
        healthy=n sort:tc_act:desc
        role~web sort:pp_last:desc,host
        pp_last>20h tc_act<2h sort:tc_act:desc,host
    """
    conditions: list[FilterCondition] = []
    sort_keys: list[SortKey] = []

    for token in text.strip().split():
        if not token:
            continue
        if token.lower().startswith("sort:"):
            sort_spec = token[5:]
            for part in sort_spec.split(","):
                if not part:
                    continue
                pieces = part.split(":")
                col = pieces[0].lower()
                col = COLUMN_ALIASES.get(col, col)
                direction = "asc"
                if len(pieces) > 1 and pieces[1].lower() in ("asc", "desc"):
                    direction = pieces[1].lower()
                if col:
                    sort_keys.append(SortKey(column=col, direction=direction))
        else:
            # Find operator by longest-match first
            op_found = None
            op_pos = -1
            for op in _OPERATORS:
                pos = token.find(op)
                if pos > 0:  # column must be non-empty (pos > 0, not >= 0)
                    if (
                        op_found is None
                        or pos < op_pos
                        or (pos == op_pos and len(op) > len(op_found))
                    ):
                        op_found = op
                        op_pos = pos
            if op_found is None:
                continue  # skip malformed token
            col = token[:op_pos].lower()
            col = COLUMN_ALIASES.get(col, col)
            val = token[op_pos + len(op_found) :]
            if col and val:
                conditions.append(FilterCondition(column=col, op=op_found, value=val))

    return Query(conditions=conditions, sort_keys=sort_keys)


def parse_query_safe(text: str) -> Query:
    """Parse query string, returning empty Query on any error."""
    if not text or not text.strip():
        return Query()
    try:
        return parse_query(text)
    except Exception:
        return Query()


def validate_query(query: Query, text: str) -> str | None:
    """Return an error message string if the query has issues, or None if valid.

    Checks:
    - Non-empty text that produced an empty query (syntax unrecognized)
    - Unknown column names in conditions or sort keys
    """
    if text.strip() and query.is_empty():
        return "query unrecognized — check syntax"
    for cond in query.conditions:
        if cond.column not in KNOWN_COLUMNS:
            return f"unknown column: {cond.column}"
    for sk in query.sort_keys:
        if sk.column not in KNOWN_COLUMNS:
            return f"unknown sort column: {sk.column}"
    return None


def _get_data_seconds(row: dict[str, str]) -> int | None:
    """Extract max(audit_secs, tc_secs) from the composite DATA column."""
    raw = row.get("data", "")
    if "/" in raw:
        left, right = raw.split("/", 1)
        a = parse_duration(left.strip())
        b = parse_duration(right.strip())
        vals = [v for v in (a, b) if v is not None]
        return max(vals) if vals else None
    return parse_duration(raw)


_OP_FNS = {
    ">": _op.gt,
    "<": _op.lt,
    ">=": _op.ge,
    "<=": _op.le,
    "=": _op.eq,
    "!=": _op.ne,
}


def _compare_numeric(col_secs: int, op: str, flt_secs: int) -> bool:
    fn = _OP_FNS.get(op)
    return fn(col_secs, flt_secs) if fn is not None else False


def _compare_string(col_value: str, op: str, filter_value: str) -> bool:
    cv = col_value.lower()
    fv = filter_value.lower()
    if op == "~":
        return fv in cv
    fn = _OP_FNS.get(op)
    return fn(cv, fv) if fn is not None else False


def row_matches_condition(row: dict[str, str], cond: FilterCondition) -> bool:
    """Return True if the row matches the filter condition."""
    if cond.column == "data":
        col_secs = _get_data_seconds(row)
        if col_secs is None:
            return False
        flt_secs = parse_duration(cond.value)
        if flt_secs is not None:
            return _compare_numeric(col_secs, cond.op, flt_secs)
        return _compare_string(row.get("data", ""), cond.op, cond.value)

    col_value = row.get(cond.column, "")
    if cond.column in TIME_COLUMNS:
        col_secs = parse_duration(col_value)
        if col_secs is None:
            return False
        flt_secs = parse_duration(cond.value)
        if flt_secs is not None:
            return _compare_numeric(col_secs, cond.op, flt_secs)

    return _compare_string(col_value, cond.op, cond.value)


def apply_conditions(
    rows: list[dict[str, str]], conditions: list[FilterCondition]
) -> list[dict[str, str]]:
    """Filter rows to those matching all conditions."""
    if not conditions:
        return rows
    return [r for r in rows if all(row_matches_condition(r, c) for c in conditions)]


class _Rev:
    """Reverse-comparison wrapper for strings (enables desc sort in multi-key sort)."""

    __slots__ = ("val",)

    def __init__(self, val: str) -> None:
        self.val = val

    def __lt__(self, other: _Rev) -> bool:
        return self.val > other.val

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _Rev):
            return NotImplemented
        return self.val == other.val

    def __le__(self, other: _Rev) -> bool:
        return self.val >= other.val

    def __gt__(self, other: _Rev) -> bool:
        return self.val < other.val

    def __ge__(self, other: _Rev) -> bool:
        return self.val <= other.val

    def __hash__(self) -> int:
        return hash(self.val)


def _row_sort_tuple(row: dict[str, str], sort_keys: list[SortKey]) -> tuple:
    """Build a sort tuple for a row. Unknown values always sort last."""
    parts: list = []
    for sk in sort_keys:
        raw = row.get(sk.column, "")
        unknown = raw in ("-", "?", "--", "")
        if sk.column in TIME_COLUMNS or sk.column == "data":
            if sk.column == "data":
                secs = _get_data_seconds(row)
            else:
                secs = parse_duration(raw)
            if secs is None:
                unknown = True
                secs = 0
            numeric_val = secs if sk.direction == "asc" else -secs
            parts.append((1 if unknown else 0, numeric_val))
        else:
            str_val = raw.lower()
            if sk.direction == "asc":
                parts.append((1 if unknown else 0, str_val))
            else:
                parts.append((1 if unknown else 0, _Rev(str_val)))
    return tuple(parts)


def apply_sort(rows: list[dict[str, str]], sort_keys: list[SortKey]) -> list[dict[str, str]]:
    """Sort rows by the given sort keys."""
    if not sort_keys:
        return rows
    return sorted(rows, key=lambda r: _row_sort_tuple(r, sort_keys))


def apply_query(rows: list[dict[str, str]], query: Query) -> list[dict[str, str]]:
    """Apply filter conditions then sort to a list of row dicts."""
    rows = apply_conditions(rows, query.conditions)
    rows = apply_sort(rows, query.sort_keys)
    return rows


def tokenize_for_highlight(text: str) -> list[tuple[int, int, str]]:
    """Tokenize a query string into (start, end, token_type) spans for highlighting.

    Token types:
      "column_ok"    — recognized column name (before operator)
      "column_bad"   — unrecognized column name (before operator)
      "op"           — comparison operator
      "value"        — value after operator
      "sort_kw"      — the "sort:" prefix
      "sort_col_ok"  — recognized sort column
      "sort_col_bad" — unrecognized sort column
      "sort_dir"     — sort direction (asc/desc)
      "plain"        — separators, incomplete tokens, whitespace
    """
    spans: list[tuple[int, int, str]] = []
    pos = 0
    while pos < len(text):
        # Skip whitespace (leave unhighlighted)
        ws_start = pos
        while pos < len(text) and text[pos] == " ":
            pos += 1
        if pos > ws_start:
            spans.append((ws_start, pos, "plain"))
        if pos >= len(text):
            break

        # Find end of whitespace-delimited token
        tok_start = pos
        while pos < len(text) and text[pos] != " ":
            pos += 1
        token = text[tok_start:pos]

        if token.lower().startswith("sort:"):
            spans.append((tok_start, tok_start + 5, "sort_kw"))
            cur = tok_start + 5
            rest = token[5:]
            parts = rest.split(",")
            for i, part in enumerate(parts):
                if i > 0:
                    spans.append((cur, cur + 1, "plain"))  # comma
                    cur += 1
                if not part:
                    continue
                pieces = part.split(":", 1)
                col_text = pieces[0]
                col_type = "sort_col_ok" if col_text.lower() in KNOWN_COLUMNS else "sort_col_bad"
                spans.append((cur, cur + len(col_text), col_type))
                cur += len(col_text)
                if len(pieces) > 1:
                    spans.append((cur, cur + 1, "plain"))  # colon
                    cur += 1
                    dir_text = pieces[1]
                    dir_type = "sort_dir" if dir_text.lower() in ("asc", "desc") else "plain"
                    spans.append((cur, cur + len(dir_text), dir_type))
                    cur += len(dir_text)
        else:
            # Find operator by longest-match first
            op_found = None
            op_pos_in_tok = -1
            for op in _OPERATORS:
                p = token.find(op)
                if p > 0:
                    if (
                        op_found is None
                        or p < op_pos_in_tok
                        or (p == op_pos_in_tok and len(op) > len(op_found))
                    ):
                        op_found = op
                        op_pos_in_tok = p
            if op_found is None:
                # Incomplete token (still typing) — leave plain
                spans.append((tok_start, pos, "plain"))
            else:
                col_text = token[:op_pos_in_tok]
                col_type = "column_ok" if col_text.lower() in KNOWN_COLUMNS else "column_bad"
                spans.append((tok_start, tok_start + op_pos_in_tok, col_type))
                spans.append(
                    (tok_start + op_pos_in_tok, tok_start + op_pos_in_tok + len(op_found), "op")
                )
                val_start = tok_start + op_pos_in_tok + len(op_found)
                if val_start < pos:
                    spans.append((val_start, pos, "value"))
    return spans
