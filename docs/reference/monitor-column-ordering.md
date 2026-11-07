# Monitor Column Ordering

## Principle

Columns are ordered so that the most important diagnostic questions can be
answered by reading left to right, without skipping around.

## The two questions a column answers

Every column answers one of two questions:

1. **Is this data fresh / trustworthy?** — Liveness columns. If the answer is
   "no", the remaining columns may be misleading.
2. **Is this host in the right state?** — Correctness columns. Only meaningful
   once liveness is confirmed.

Liveness columns come first. Correctness columns follow.

## Puppet columns: `fresh? → want → got → agree?`

The four Puppet columns illustrate this directly:

```
PP_LAST | PP_EXP | PP_SHA | PP_MATCH
```

| Column     | Question            | Rationale |
|------------|---------------------|-----------|
| `PP_LAST`  | fresh?              | If stale or `FAIL`, the SHA columns are unreliable. Gate on this first. |
| `PP_EXP`   | what should it run? | The expected git SHA (branch HEAD or master). |
| `PP_SHA`   | what is it running? | The SHA actually applied on the host. |
| `PP_MATCH` | do they agree?      | Summary verdict: `Y` / `N` / `-`. |

When `PP_MATCH` is `N`, the eye naturally scans left: `PP_EXP` vs `PP_SHA`
reveals the divergence without hunting across the row.

## General ordering rules

1. **Liveness before correctness** — `PP_LAST` before the SHA triple.
2. **Summary verdict on the right** — `PP_MATCH` closes the group; it's the
   single most actionable signal but only makes sense after seeing EXP and SHA.
3. **Group related columns** — columns that together answer one question stay
   adjacent. Don't interleave columns from different questions.
4. **Identity columns anchor the left** — `HOST`, `OS`, `ROLE` come first
   because every row starts with "which host is this?"
5. **Rarely-used / wide columns drop first** — the `drop_order` list in
   `formatting.py` sheds columns when the terminal is narrow, removing the
   least diagnostic ones first (`NOTE`, `VLT_SHA`, `SHA`, `ROLE`, …).

## Compact override identity

In terminal and text tables, OVR_BCH grows to fit complete branch names. It shows
`branch checksum *wt`, omitting the old checksum parentheses. The `*wt` suffix
appears only when the override sets a Taskcluster worker pool and stays red.
The branch name, checksum abbreviation, and worker-pool marker remain visible. For example:

```text
feature-very-long-name-distinct 0000 *wt
```

Short values use only the width they need. Without branch metadata, the cell
shows the checksum and optional pool marker. Categorical colors remain keyed
to the complete override value; when a branch is present it gets that color
and the checksum stays plain. Filtering, sorting, JSON, and browser values
retain the complete original branch/checksum representation.

Checksum abbreviations start at four characters and expand to distinguish
known collisions, including collisions beyond the old eight-character prefix.
The comparison includes local override files and the loaded host observations
before filtering. Stored filename prefixes and full checksums for the same
override count as one identity. Known checksums remain registered for the
session, so filtering cannot shorten a prefix. Newly discovered collisions can
lengthen it. Uniqueness is relative to this known inventory.

## Compact vault identity

VLT_SHA shows `checksum mnemonic ↪ symlink-name`, omitting parentheses and the
symlink portion when unavailable. Checksum prefixes start at four characters
and expand for known collisions in the vault inventory, independently of
override checksums. Local vault filenames, symlink targets, and unfiltered
loaded observations participate; known identities remain registered across
filter changes. Complete mnemonic and symlink names stay visible, and the
mnemonic retains its categorical color. The `↪` marker distinguishes the
symlink name from the mnemonic. Text and TUI widths use the compact
representation. Original filter/sort/JSON/browser values remain unchanged.

## Compact uptime

The terminal monitor and text table show uptime in seven characters: a
three-character whole-unit label, a space, and a three-cell magnitude bar.
For example, `15h █▌░` represents an uptime of at least 15 hours and under
16 hours. Labels use seconds, minutes, hours, days, weeks (from 100 days),
then years (from 100 weeks), and saturate at `99y`. Missing uptime shows `-`
without a bar. Filtering, sorting, JSON, and browser values retain the original
duration precision.

| Uptime | Bar |
| --- | --- |
| Under 1 hour | `▎░░` |
| 1 hour to under 6 hours | `█░░` |
| 6 hours to under 1 day | `█▌░` |
| 1 day to under 7 days | `██░` |
| 7 days or more | `███` |

These bands are fixed across views. Blue shades get lighter for longer
uptimes on 256-color terminals; basic terminals use dim, normal, and bold
cyan. With color disabled, the bar still communicates magnitude. Neither
the bar nor its color indicates health or confirms that a worker is idle.

## Compact DATA ages

The terminal monitor and text table show compact whole-unit ages in
host-audit/Taskcluster order. Matching displayed ages show once: `5m/5m`
becomes `5m`, and `1h 02m/1h 58m` becomes `1h`. Ages under a minute use `<1m`.
Different displayed ages remain separate, such as `2m/45m`.

Each source uses its own freshness color: green under 5 minutes, yellow from
5 minutes to under 30 minutes, and red from 30 minutes onward. A shared age
uses the older source's color. The slash stays neutral, and missing or unknown
sources have no freshness color.

Missing source positions remain explicit (`5m/-`, `-/5m`); both missing
becomes `-`. An unobserved host keeps its `?` marker, such as `?/5m` or `?/-`.
Widths follow the compact values, so a view with matching ages can use just
four columns under the DATA header. Filtering, sorting, JSON, and browser
values keep the original age strings; DATA duration comparisons continue to
use the larger available source age.
