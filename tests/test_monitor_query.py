"""Tests for the monitor filter/sort query engine."""

from __future__ import annotations

from fleetroll.commands.monitor.data import parse_duration
from fleetroll.commands.monitor.query import (
    FilterCondition,
    Query,
    SortKey,
    apply_conditions,
    apply_query,
    apply_sort,
    migrate_legacy_empty_syntax,
    normalize_for_filter,
    parse_query,
    parse_query_safe,
    row_matches_condition,
    tokenize_for_highlight,
    validate_query,
)

# ---------------------------------------------------------------------------
# parse_duration
# ---------------------------------------------------------------------------


def test_parse_duration_filter_style():
    assert parse_duration("20h") == 72000
    assert parse_duration("5m") == 300
    assert parse_duration("1d") == 86400
    assert parse_duration("30s") == 30
    assert parse_duration("1h30m") == 5400
    assert parse_duration("1h 30m") == 5400


def test_parse_duration_humanize_output():
    assert parse_duration("2h 30m") == 9000
    assert parse_duration("1d 04h") == 100800
    assert parse_duration("1d 00h") == 86400
    assert parse_duration("3m") == 180


def test_parse_duration_lt_prefix():
    # "<Xunit" returns X*unit - 1 to reflect "less than X"
    assert parse_duration("<1m") == 59
    assert parse_duration("<5m") == 299
    assert parse_duration("<1h") == 3599


def test_parse_duration_fail_suffix():
    # pp_last can have " FAIL" appended
    assert parse_duration("30m FAIL") == 1800
    assert parse_duration("2h 30m FAIL") == 9000


def test_parse_duration_inprogress_suffix():
    # tc_j_sf can have " -" appended (in-progress task)
    assert parse_duration("5m -") == 300


def test_parse_duration_unknown_values():
    assert parse_duration("-") is None
    assert parse_duration("?") is None
    assert parse_duration("--") is None
    assert parse_duration("") is None
    assert parse_duration(None) is None  # type: ignore[arg-type]


def test_parse_duration_unparseable():
    assert parse_duration("abc") is None
    assert parse_duration("missing") is None


# ---------------------------------------------------------------------------
# parse_query — conditions
# ---------------------------------------------------------------------------


def test_parse_query_single_condition():
    q = parse_query("pp_last>20h")
    assert len(q.conditions) == 1
    cond = q.conditions[0]
    assert cond.column == "pp_last"
    assert cond.op == ">"
    assert cond.value == "20h"
    assert q.sort_keys == []


def test_parse_query_multi_conditions():
    q = parse_query("pp_last>20h tc_act<2h")
    assert len(q.conditions) == 2
    assert q.conditions[0].column == "pp_last"
    assert q.conditions[1].column == "tc_act"


def test_parse_query_string_ops():
    q = parse_query("role~web")
    assert q.conditions[0].op == "~"
    assert q.conditions[0].value == "web"

    q2 = parse_query("healthy=n")
    assert q2.conditions[0].op == "="

    q3 = parse_query("healthy!=y")
    assert q3.conditions[0].op == "!="


def test_parse_query_gte_lte_ops():
    q = parse_query("pp_last>=1h")
    assert q.conditions[0].op == ">="
    q2 = parse_query("tc_act<=30m")
    assert q2.conditions[0].op == "<="


def test_parse_query_case_insensitive_column():
    q = parse_query("PP_LAST>20h")
    assert q.conditions[0].column == "pp_last"


# ---------------------------------------------------------------------------
# parse_query — sort
# ---------------------------------------------------------------------------


def test_parse_query_sort_single():
    q = parse_query("sort:tc_act")
    assert len(q.sort_keys) == 1
    assert q.sort_keys[0].column == "tc_act"
    assert q.sort_keys[0].direction == "asc"


def test_parse_query_sort_with_direction():
    q = parse_query("sort:tc_act:desc")
    assert q.sort_keys[0].direction == "desc"

    q2 = parse_query("sort:pp_last:asc")
    assert q2.sort_keys[0].direction == "asc"


def test_parse_query_sort_multi_column():
    q = parse_query("sort:pp_last:desc,host")
    assert len(q.sort_keys) == 2
    assert q.sort_keys[0].column == "pp_last"
    assert q.sort_keys[0].direction == "desc"
    assert q.sort_keys[1].column == "host"
    assert q.sort_keys[1].direction == "asc"


def test_parse_query_combined():
    q = parse_query("healthy=n sort:tc_act:desc")
    assert len(q.conditions) == 1
    assert q.conditions[0].column == "healthy"
    assert len(q.sort_keys) == 1
    assert q.sort_keys[0].column == "tc_act"
    assert q.sort_keys[0].direction == "desc"


def test_parse_query_empty():
    q = parse_query("")
    assert q.is_empty()

    q2 = parse_query("   ")
    assert q2.is_empty()


def test_parse_query_safe_invalid():
    # Should not raise; returns empty Query
    q = parse_query_safe("!!!")
    assert isinstance(q, Query)


def test_query_is_empty_and_has_sort():
    q = Query()
    assert q.is_empty()
    assert not q.has_sort()

    q2 = parse_query("sort:tc_act")
    assert not q2.is_empty()
    assert q2.has_sort()

    q3 = parse_query("healthy=n")
    assert not q3.is_empty()
    assert not q3.has_sort()


# ---------------------------------------------------------------------------
# row_matches_condition
# ---------------------------------------------------------------------------


def _row(**kwargs) -> dict[str, str]:
    defaults = {
        "host": "test-host-001",
        "os": "L",
        "role": "gecko_t_linux",
        "healthy": "Y",
        "pp_last": "30m",
        "tc_act": "10m",
        "uptime": "2d 04h",
        "tc_j_sf": "5m",
        "pp_match": "Y",
        "tc_quar": "-",
        "data": "5m/10m",
        "sha": "-",
        "vlt_sha": "-",
        "pp_sha": "-",
        "pp_exp": "-",
        "note": "",
    }
    defaults.update(kwargs)
    return defaults


def test_row_matches_time_gt():
    row = _row(pp_last="2h 30m")
    cond = FilterCondition(column="pp_last", op=">", value="1h")
    assert row_matches_condition(row, cond)


def test_row_not_matches_time_gt():
    row = _row(pp_last="30m")
    cond = FilterCondition(column="pp_last", op=">", value="1h")
    assert not row_matches_condition(row, cond)


def test_row_matches_time_lt():
    row = _row(tc_act="10m")
    cond = FilterCondition(column="tc_act", op="<", value="1h")
    assert row_matches_condition(row, cond)


def test_row_matches_string_eq():
    row = _row(healthy="Y")
    cond = FilterCondition(column="healthy", op="=", value="y")  # case-insensitive
    assert row_matches_condition(row, cond)


def test_row_not_matches_string_eq():
    row = _row(healthy="N")
    cond = FilterCondition(column="healthy", op="=", value="y")
    assert not row_matches_condition(row, cond)


def test_row_matches_substring():
    row = _row(role="gecko_t_win")
    cond = FilterCondition(column="role", op="~", value="win")
    assert row_matches_condition(row, cond)


def test_row_not_matches_substring():
    row = _row(role="gecko_t_linux")
    cond = FilterCondition(column="role", op="~", value="win")
    assert not row_matches_condition(row, cond)


def test_row_unknown_value_no_match_time():
    row = _row(pp_last="-")
    cond = FilterCondition(column="pp_last", op=">", value="1m")
    assert not row_matches_condition(row, cond)


def test_row_fail_suffix_stripped():
    # pp_last with FAIL suffix should still be parseable
    row = _row(pp_last="2h 30m FAIL")
    cond = FilterCondition(column="pp_last", op=">", value="1h")
    assert row_matches_condition(row, cond)


def test_row_data_column_uses_max():
    # data="5m/2h 30m" → max is 150 minutes = 9000s
    row = _row(data="5m/2h 30m")
    cond_gt = FilterCondition(column="data", op=">", value="1h")
    assert row_matches_condition(row, cond_gt)

    cond_lt = FilterCondition(column="data", op="<", value="10m")
    assert not row_matches_condition(row, cond_lt)


def test_row_data_column_unknown():
    row = _row(data="-/-")
    cond = FilterCondition(column="data", op=">", value="1m")
    assert not row_matches_condition(row, cond)


# ---------------------------------------------------------------------------
# apply_conditions
# ---------------------------------------------------------------------------


def test_apply_conditions_filters_rows():
    rows = [
        _row(host="host-001", pp_last="30m"),
        _row(host="host-002", pp_last="2h 30m"),
        _row(host="host-003", pp_last="45m"),
    ]
    conditions = [FilterCondition(column="pp_last", op=">", value="1h")]
    result = apply_conditions(rows, conditions)
    assert len(result) == 1
    assert result[0]["host"] == "host-002"


def test_apply_conditions_multiple_all_must_match():
    rows = [
        _row(host="host-001", healthy="Y", pp_last="30m"),
        _row(host="host-002", healthy="N", pp_last="2h 30m"),
        _row(host="host-003", healthy="Y", pp_last="2h 30m"),
    ]
    conditions = [
        FilterCondition(column="healthy", op="=", value="y"),
        FilterCondition(column="pp_last", op=">", value="1h"),
    ]
    result = apply_conditions(rows, conditions)
    assert len(result) == 1
    assert result[0]["host"] == "host-003"


def test_apply_conditions_empty_returns_all():
    rows = [_row(host="host-001"), _row(host="host-002")]
    result = apply_conditions(rows, [])
    assert len(result) == 2


# ---------------------------------------------------------------------------
# apply_sort
# ---------------------------------------------------------------------------


def test_apply_sort_time_asc():
    rows = [
        _row(host="host-b", tc_act="2h 30m"),
        _row(host="host-a", tc_act="30m"),
    ]
    result = apply_sort(rows, [SortKey(column="tc_act", direction="asc")])
    assert result[0]["host"] == "host-a"
    assert result[1]["host"] == "host-b"


def test_apply_sort_time_desc():
    rows = [
        _row(host="host-b", tc_act="2h 30m"),
        _row(host="host-a", tc_act="30m"),
    ]
    result = apply_sort(rows, [SortKey(column="tc_act", direction="desc")])
    assert result[0]["host"] == "host-b"
    assert result[1]["host"] == "host-a"


def test_apply_sort_unknown_last_regardless_of_direction():
    rows = [
        _row(host="host-unknown", tc_act="-"),
        _row(host="host-known", tc_act="30m"),
    ]
    for direction in ("asc", "desc"):
        result = apply_sort(rows, [SortKey(column="tc_act", direction=direction)])
        assert result[-1]["host"] == "host-unknown", f"unknown should be last for {direction}"


def test_apply_sort_string_asc():
    rows = [
        _row(host="host-c", role="web"),
        _row(host="host-a", role="api"),
        _row(host="host-b", role="db"),
    ]
    result = apply_sort(rows, [SortKey(column="role", direction="asc")])
    assert [r["role"] for r in result] == ["api", "db", "web"]


def test_apply_sort_string_desc():
    rows = [
        _row(host="host-c", role="web"),
        _row(host="host-a", role="api"),
        _row(host="host-b", role="db"),
    ]
    result = apply_sort(rows, [SortKey(column="role", direction="desc")])
    assert [r["role"] for r in result] == ["web", "db", "api"]


def test_apply_sort_multi_key():
    rows = [
        _row(host="host-b", healthy="N", tc_act="30m"),
        _row(host="host-a", healthy="N", tc_act="2h"),
        _row(host="host-c", healthy="Y", tc_act="10m"),
    ]
    result = apply_sort(
        rows,
        [SortKey(column="healthy", direction="asc"), SortKey(column="tc_act", direction="desc")],
    )
    # healthy=N sorts before Y (N < Y lexicographically)
    assert result[0]["healthy"] == "N"
    assert result[1]["healthy"] == "N"
    # within N, tc_act desc: 2h before 30m
    assert result[0]["host"] == "host-a"
    assert result[1]["host"] == "host-b"
    assert result[2]["healthy"] == "Y"


def test_apply_sort_no_keys_returns_unchanged():
    rows = [_row(host="host-b"), _row(host="host-a")]
    result = apply_sort(rows, [])
    assert result[0]["host"] == "host-b"


# ---------------------------------------------------------------------------
# apply_query (combined)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# validate_query
# ---------------------------------------------------------------------------


def test_validate_query_valid_returns_none():
    q = parse_query("pp_last>20h sort:tc_act:desc")
    assert validate_query(q, "pp_last>20h sort:tc_act:desc") is None


def test_validate_query_empty_text_returns_none():
    assert validate_query(Query(), "") is None
    assert validate_query(Query(), "   ") is None


def test_validate_query_nonempty_text_empty_query():
    # Unparseable text → query is empty → error
    q = parse_query_safe("pp_last>")  # no value
    assert validate_query(q, "pp_last>") is not None
    assert "syntax" in validate_query(q, "pp_last>").lower()


def test_validate_query_unknown_column_condition():
    q = parse_query("foo>20h")
    err = validate_query(q, "foo>20h")
    assert err is not None
    assert "foo" in err


def test_validate_query_unknown_sort_column():
    q = parse_query("sort:badcol")
    err = validate_query(q, "sort:badcol")
    assert err is not None
    assert "badcol" in err


def test_validate_query_known_columns_no_error():
    for col in ("host", "role", "pp_last", "tc_act", "healthy", "data", "note"):
        q = parse_query(f"{col}~x")
        assert validate_query(q, f"{col}~x") is None


def test_apply_query_filter_then_sort():
    rows = [
        _row(host="host-a", pp_last="30m", tc_act="2h"),
        _row(host="host-b", pp_last="2h 30m", tc_act="45m"),
        _row(host="host-c", pp_last="3h", tc_act="10m"),
    ]
    q = parse_query("pp_last>1h sort:tc_act:desc")
    result = apply_query(rows, q)
    assert len(result) == 2
    hosts = [r["host"] for r in result]
    # tc_act desc: 45m (host-b) > 10m (host-c)
    assert hosts == ["host-b", "host-c"]


# ---------------------------------------------------------------------------
# tokenize_for_highlight
# ---------------------------------------------------------------------------


def _types(text: str) -> list[tuple[str, str]]:
    """Return (text_chunk, token_type) pairs for a query string."""
    spans = tokenize_for_highlight(text)
    return [(text[s:e], t) for s, e, t in spans]


def test_highlight_empty():
    assert tokenize_for_highlight("") == []


def test_highlight_simple_condition_valid_column():
    result = _types("pp_last>20h")
    assert ("pp_last", "column_ok") in result
    assert (">", "op") in result
    assert ("20h", "value") in result


def test_highlight_simple_condition_invalid_column():
    result = _types("bogus>20h")
    assert ("bogus", "column_bad") in result
    assert (">", "op") in result
    assert ("20h", "value") in result


def test_highlight_incomplete_token_is_plain():
    # No operator yet — still typing
    result = _types("pp_last")
    assert result == [("pp_last", "plain")]


def test_highlight_sort_keyword():
    result = _types("sort:pp_last:desc")
    types_only = [t for _, t in result]
    assert "sort_kw" in types_only
    assert "sort_col_ok" in types_only
    assert "sort_dir" in types_only


def test_highlight_sort_unknown_column():
    result = _types("sort:bogus:asc")
    assert ("bogus", "sort_col_bad") in result
    assert ("asc", "sort_dir") in result


def test_highlight_multi_token():
    result = _types("pp_last>20h role~web")
    chunk_types = dict(result)
    assert chunk_types["pp_last"] == "column_ok"
    assert chunk_types[">"] == "op"
    assert chunk_types["20h"] == "value"
    assert chunk_types["role"] == "column_ok"
    assert chunk_types["~"] == "op"
    assert chunk_types["web"] == "value"


def test_highlight_spans_cover_full_text():
    text = "pp_last>20h sort:role:desc"
    spans = tokenize_for_highlight(text)
    # Reconstruct text from spans and verify no gaps
    reconstructed = "".join(text[s:e] for s, e, _ in spans)
    assert reconstructed == text


# ---------------------------------------------------------------------------
# Pipe-separated value lists (os=M|L)
# ---------------------------------------------------------------------------


def test_pipe_eq_matches_any():
    row = _row(os="M")
    cond = FilterCondition(column="os", op="=", value="M|L")
    assert row_matches_condition(row, cond)


def test_pipe_eq_matches_second():
    row = _row(os="L")
    cond = FilterCondition(column="os", op="=", value="M|L")
    assert row_matches_condition(row, cond)


def test_pipe_eq_excludes_non_match():
    row = _row(os="W")
    cond = FilterCondition(column="os", op="=", value="M|L")
    assert not row_matches_condition(row, cond)


def test_pipe_neq_excludes_all_listed():
    row = _row(os="M")
    cond = FilterCondition(column="os", op="!=", value="M|L")
    assert not row_matches_condition(row, cond)


def test_pipe_neq_passes_unlisted():
    row = _row(os="W")
    cond = FilterCondition(column="os", op="!=", value="M|L")
    assert row_matches_condition(row, cond)


def test_highlight_pipe_value_list():
    result = _types("os=M|L")
    assert ("os", "column_ok") in result
    assert ("=", "op") in result
    assert ("M", "value") in result
    assert ("|", "op") in result
    assert ("L", "value") in result


def test_highlight_pipe_spans_cover_full_text():
    text = "os=M|L"
    spans = tokenize_for_highlight(text)
    reconstructed = "".join(text[s:e] for s, e, _ in spans)
    assert reconstructed == text


# ---------------------------------------------------------------------------
# normalize_for_filter
# ---------------------------------------------------------------------------


def test_normalize_for_filter_empty_sentinels():
    assert normalize_for_filter("-") == ""
    assert normalize_for_filter("") == ""
    assert normalize_for_filter("--") == ""


def test_normalize_for_filter_question_mark_preserved():
    assert normalize_for_filter("?") == "?"


def test_normalize_for_filter_real_values_unchanged():
    assert normalize_for_filter("gecko_t_linux") == "gecko_t_linux"
    assert normalize_for_filter("Y") == "Y"


# ---------------------------------------------------------------------------
# Empty-match syntax: col= and col!=
# ---------------------------------------------------------------------------


def test_parse_query_empty_value_eq_accepted():
    q = parse_query("note=")
    assert len(q.conditions) == 1
    cond = q.conditions[0]
    assert cond.column == "note"
    assert cond.op == "="
    assert cond.value == ""


def test_parse_query_empty_value_neq_accepted():
    q = parse_query("note!=")
    assert len(q.conditions) == 1
    assert q.conditions[0].op == "!="
    assert q.conditions[0].value == ""


def test_parse_query_empty_value_other_ops_ignored():
    assert parse_query("note>").is_empty()
    assert parse_query("note<").is_empty()
    assert parse_query("note~").is_empty()


def test_row_matches_empty_eq_dash_sentinel():
    assert row_matches_condition(_row(note="-"), FilterCondition("note", "=", ""))
    assert row_matches_condition(_row(note=""), FilterCondition("note", "=", ""))
    assert row_matches_condition(_row(note="--"), FilterCondition("note", "=", ""))


def test_row_matches_empty_eq_rejects_real_value():
    assert not row_matches_condition(_row(note="some text"), FilterCondition("note", "=", ""))


def test_row_matches_empty_neq():
    assert row_matches_condition(_row(note="some text"), FilterCondition("note", "!=", ""))
    assert not row_matches_condition(_row(note="-"), FilterCondition("note", "!=", ""))


def test_row_matches_empty_eq_question_mark_not_empty():
    assert not row_matches_condition(_row(pp_sha="?"), FilterCondition("pp_sha", "=", ""))
    assert row_matches_condition(_row(pp_sha="?"), FilterCondition("pp_sha", "=", "?"))


def test_row_matches_empty_eq_dash_column():
    # sha defaults to "-" in _row
    assert row_matches_condition(_row(), FilterCondition("sha", "=", ""))
    assert row_matches_condition(_row(), FilterCondition("pp_sha", "=", ""))


def test_legacy_col_eq_dash_matches_nothing():
    # note=- is a literal match against "-"; after normalization the stored
    # value becomes "" so it never equals the literal "-" filter value.
    assert not row_matches_condition(_row(note="-"), FilterCondition("note", "=", "-"))
    assert not row_matches_condition(_row(note=""), FilterCondition("note", "=", "-"))


def test_substring_does_not_match_dash_sentinel():
    # note~ on a "-" sentinel row should not match (normalized to "")
    assert not row_matches_condition(_row(note="-"), FilterCondition("note", "~", "-"))


def test_full_filter_empty_note():
    rows = [_row(note=""), _row(note="-"), _row(note="has text")]
    result = apply_conditions(rows, [FilterCondition("note", "=", "")])
    assert len(result) == 2
    assert all(r["note"] in ("", "-") for r in result)


# ---------------------------------------------------------------------------
# migrate_legacy_empty_syntax
# ---------------------------------------------------------------------------


def test_migrate_legacy_note_eq_dash():
    assert migrate_legacy_empty_syntax("note=-") == "note="


def test_migrate_legacy_note_neq_dash():
    assert migrate_legacy_empty_syntax("note!=-") == "note!="


def test_migrate_legacy_combined():
    assert (
        migrate_legacy_empty_syntax("tc_act>4h data>4h note=- sort:host:asc")
        == "tc_act>4h data>4h note= sort:host:asc"
    )


def test_migrate_legacy_no_change_when_not_legacy():
    assert migrate_legacy_empty_syntax("note=") == "note="
    assert migrate_legacy_empty_syntax("role~web") == "role~web"
    assert migrate_legacy_empty_syntax("pp_last>20h") == "pp_last>20h"


def test_migrate_legacy_does_not_mangle_mid_token():
    # role=-dev has "-dev" as value, not a standalone "-"
    assert migrate_legacy_empty_syntax("role=-dev") == "role=-dev"
