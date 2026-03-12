# Monitor Filter & Sort

## Problem

The host-monitor TUI shows all hosts with fixed sort options (`s` key cycles
host/role/sha). There's no way to filter by column values or sort by arbitrary
columns, making it hard to surface problematic hosts (e.g. puppet runs older
than 20h, unhealthy hosts sorted by TC activity).

## Design Goals

- Fast to type in a TUI context
- Composable: filter + sort in one expression
- Works in both interactive TUI and `--once` (CLI arg) mode
- Coexists with existing quick-keys (`o`, `O`, `s`) without replacing them

---

## Query Language

Terse filter-bar syntax: space-separated conditions, `sort:` sigil for ordering.

### Filter conditions

```
COLUMN OP VALUE
```

No spaces required around the operator. Multiple conditions are implicit AND.

| Operator | Meaning |
|----------|---------|
| `>`      | greater than |
| `<`      | less than |
| `>=`     | greater than or equal |
| `<=`     | less than or equal |
| `=`      | exact match (case-insensitive) |
| `!=`     | not equal |
| `~`      | substring / contains (case-insensitive) |

### Values

- **Time**: `20h`, `5m`, `1d`, `30s`, `1h30m` — parsed to seconds for comparison
  against time columns (PP_LAST, TC_ACT, UPTIME, TC_T_DUR)
- **String**: unquoted for simple values (`web`, `n`, `yes`)
- **Case-insensitive** throughout

### Sort

```
sort:COLUMN
sort:COLUMN:asc
sort:COLUMN:desc
sort:COL1:desc,COL2,COL3:asc
```

- Direction defaults to **ascending** if omitted
- Time columns: ascending = shortest duration first (most recently active)
- Multiple columns: left-to-right priority (primary, secondary, ...)
- `sort:` can appear anywhere in the expression

### Examples

```
pp_last>20h
healthy=n sort:tc_act:desc
role~web sort:pp_last:desc,host
host~win sort:role,host
tc_quar=yes
pp_match=n sort:pp_last:desc
pp_last>20h tc_act<2h sort:tc_act:desc,host
```

### DATA column

DATA is a composite field showing `audit_age/tc_age`. Filtering uses the
**max** of the two ages (same logic as its color coding):

```
data>30m       # either side is older than 30m
```

Sub-field access (if needed in future): `data.a` (audit), `data.t` (tc).

---

## TUI Interaction

### Filter bar

Opened with `/` at the bottom of the screen:

```
Filter: pp_last>20h sort:tc_act:desc█
```

| Key | Action |
|-----|--------|
| `/` | Open filter bar (pre-filled with current query if any) |
| `Enter` | Apply query, return to monitor |
| `Esc` | Cancel edit, restore previous query |
| `Ctrl+U` | Clear input line while editing |
| `\` | Clear active `/` query entirely (no editing mode needed) |

### Status line

The hard status line at the top always reflects active state:

```
fleetroll | 42 hosts | pp_last>20h sort:tc_act:desc | ? help
```

When no query is active:

```
fleetroll | 42 hosts | ? help
```

The full query string is shown (not just "filter active") so the user can see
what's applied at a glance before pressing `/` to edit.

---

## Coexistence with Existing Quick-Keys

`o`, `O`, and `s` remain unchanged. They operate as independent filter/sort
state that is **ANDed** with the `/` query. They are not merged into the query
string.

| Key | Behavior |
|-----|---------|
| `o` | Toggle override-only filter (unchanged) |
| `O` | Cycle OS filter: None → L → M → W → None (unchanged) |
| `s` | Cycle sort: host → role → sha (unchanged, **unless** the active `/` query contains a `sort:` clause, in which case `s` is a no-op) |
| `/` | Open full query bar |
| `\` | Clear `/` query only; `o`/`O` state is unaffected |

The three filter systems are independent:

```
[o: override] AND [O: Linux] AND [/: pp_last>20h sort:tc_act:desc]
```

---

## CLI (`--once` mode)

```bash
fleetroll host-monitor hosts.txt --once --filter "pp_last>20h sort:tc_act:desc"
```

The `--filter` arg accepts the same query syntax. Results are filtered/sorted
before rendering the single output.

---

## Implementation Notes

### Parsing

A simple hand-rolled tokenizer is sufficient:

1. Split on whitespace
2. Each token is either a `sort:...` clause or a `COL OP VALUE` condition
3. For conditions, detect the operator by scanning for `>`, `<`, `=`, `~`, `!=`
4. Column names are case-insensitive; normalize to lowercase for lookup

### Time comparison

All time columns store humanized strings (e.g. `"1d 2h"`, `"<5m"`). To
compare against a filter value:

1. Parse filter value (`20h` → 72000 seconds)
2. Parse column string back to seconds using an inverse of `humanize_duration`
3. Handle `<Xm` prefix (treat as `X - 1` seconds for `<` comparisons, or `0`
   for `>` comparisons)

The existing `humanize_duration` function should be accompanied by a
`parse_duration(s) -> int | None` inverse.

### Sort

`build_row_values()` returns `dict[str, str]`. For sorting:

- Time columns: convert to seconds before comparing (same parse_duration logic)
- String columns: case-insensitive lexicographic
- Missing/unknown values (`"-"`, `"?"`, `""`): sort last regardless of direction

### Column name aliases (optional, future)

Short aliases could be supported: `pp` → `pp_last`, `act` → `tc_act`,
`health` → `healthy`. Not required for initial implementation.
