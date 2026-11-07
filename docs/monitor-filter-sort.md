# Filter and sort hosts

Use the same query language in the monitor's `/` bar, CLI `--filter`, named
filters, and the [web host grid](web-interface.md).

```bash
uv run fleetroll host monitor hosts.list --once \
  --filter "pp_last>20h sort:tc_act:desc,host"
uv run fleetroll host monitor hosts.list --once --hostname-only \
  --filter-file configs/filters/moonshots-hung.yaml
```

## Query syntax

Conditions have the form `column<operator>value`, with no internal spaces.
Separate conditions with spaces; all must match. Names and string comparisons
are case-insensitive. Values cannot contain whitespace.

| Operator | Meaning |
| --- | --- |
| `=`, `!=` | Equal / not equal |
| `~` | Contains |
| `>`, `<`, `>=`, `<=` | Ordered comparison |

Duration columns (`pp_last`, `tc_act`, `uptime`, `tc_t_dur`) accept values such as
`20h`, `5m`, `1d`, and `1h30m`. Other values compare as strings. `os=L|M` matches
either OS; `os!=L|M` excludes both. Pipe alternatives apply only to `=` and `!=`.

Use `col=` for missing data and `col!=` for present data. Display sentinels `-`
and `--` count as empty; `?` remains a literal unknown value (`pp_sha=?`).
An empty pipe alternative also matches missing data: `role=|?|missing` includes
empty roles, unknown roles, and the literal `missing` used when no role file exists.
`role!=|?|missing` excludes all three states.
For `data`, presence means at least one parseable host or TC age; `?/-` and `-/-`
are empty. Duration comparisons use the larger available age, so `data>30m`
means at least one observed age exceeds 30 minutes. Separate `data.a`/`data.t`
queries are not supported.

Sort with `sort:column[:asc|desc][,column...]` anywhere in the query. Ascending
is the default; columns take priority left to right. Duration sorting uses
seconds, string sorting ignores case, and missing/unknown values sort last in
both directions.

```text
healthy=n sort:tc_act:desc
os=L pp_last>20h sort:pp_last:desc,host
ovr_bch~resolverd wt_ovr=y
note= sort:host
```

Supported fields: `host`, `os`, `role`, `vlt_sha`, `ovr_bch`, `uptime`, `pp_last`,
`pp_sha`, `pp_exp`, `pp_match`, `tc_act`, `tc_t_dur`, `tc_quar`, `data`, `healthy`,
`pool`, `wt_ovr`, and `note`. Aliases are `sha`/`ovr_sha` for `ovr_bch`,
`tc_j_sf` for `tc_t_dur`, and `health` for `healthy`. Override branch values may
include checksum and worker-type annotations; use `~` for a branch substring.
The `os` field identifies OS family (`L` Linux, `M` macOS, `W` Windows), not
Ubuntu release. Fleetroll does not currently collect an OS-version field.
The saved `linux-1804` and `linux-2404` filters infer assignment from the last
observed role, which can be retained after a failed gather. They do not verify
the installed Ubuntu version or evidence freshness. Run these views against
`configs/host-lists/linux/all_moonshots.list` and inspect evidence age; the
`linux-unobserved` view includes hosts with no recorded role, but does not include
hosts that retain a historical role. Future baseline coverage reporting will
account separately for stale evidence and roles outside the saved views.

## Interactive monitor

| Key | Action |
| --- | --- |
| `/` | Edit the current query |
| `Enter`, `Esc` | Apply / cancel an edit |
| `Ctrl-U` | Clear the input line |
| `↑`, `↓` while editing | Browse query history |
| `\` | Clear the active query |
| `f` | Open the saved/recent filter picker |

The header shows the active query. Quick filters remain independent: `o` toggles
overrides only, and `O` cycles OS selection; both combine with the query using
AND. `s` cycles the built-in sort unless the query supplies `sort:`. Clearing the
query leaves quick filters intact. Recent queries persist in
`~/.fleetroll/filter_history`.

## Saved filters and picker

Store one filter per `configs/filters/*.yaml` file, relative to the working
directory. Its filename stem is its display name; entries sort alphabetically.

```yaml
query: os=L role~talos
description: Linux Talos hosts
```

`query` is required and nonempty; `description` is optional (not displayed by the
TUI picker). The loader creates a missing directory and skips malformed files
with warnings. The picker reloads saved filters each time it opens.

The Saved and Recent tabs show names/queries and newest-first history. Opening
selects the active query when found, preferring Saved; otherwise it starts at
Saved's first row. A dot marks the active query.

| Picker key | Action |
| --- | --- |
| `←`, `→` | Select Saved / Recent |
| `↑`, `↓`, `k`, `j` | Move within the active tab |
| `PgUp`, `PgDn` | Move ten rows |
| `Home`, `G` | First / last row |
| Printable characters, Backspace, Ctrl-U | Edit the substring search across names and queries |
| `Enter` | Apply selection, promote it in history, and close; empty matches flash a message |
| `Esc`, `f` | Close |

Search resets both cursors; tab switches preserve them while open. Search and
cursor positions are not saved across opens. The footer counts matching/total
rows. Small terminals show a resize prompt. Edit YAML files directly to create,
rename, or remove saved filters; the picker is read-only.

Parsing/sorting live in `fleetroll/commands/monitor/query.py`; named filters and
the picker live alongside it in `named_filters.py` and `filters_popup.py`.
