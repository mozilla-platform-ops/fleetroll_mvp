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
