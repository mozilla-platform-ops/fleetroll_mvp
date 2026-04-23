# Host-Monitor Filter Picker Popup

Modal picker for the `host-monitor` TUI that lets users browse and apply named
filters and recent filter history from one overlay. Opens with `F` (shift-f).

Implements bead `mvp-6olr`. Depends on `mvp-5mt` (filter history persistence),
which is already landed.

## Goals

- Browse saved queries and recent history without leaving the monitor.
- Apply a selected query with one keypress.
- Keep scope tight: **v1 is read-only**. No save / rename / delete inside the
  popup. Users manage `configs/filters/` by editing files directly. This
  eliminates key-routing collisions between search-as-you-type and mutation
  commands.

## Storage

### Named filters

One YAML file per filter in `configs/filters/` (git-tracked, shareable).
Layout matches `configs/host-lists/`.

```yaml
# configs/filters/prod-talos.yaml
query: os=L role~talos
description: Linux talos hosts
```

Fields:

- `query` (required) — the filter expression applied when selected.
- `description` (optional) — shown as a second line / tooltip-style hint in
  the picker (future; v1 may just ignore it).

**Display name = filename stem.** No `name:` field in the YAML. The file
`prod-talos.yaml` displays as `prod-talos`. Rationale: reversible, unambiguous,
and avoids the foot-gun of filename and in-file name disagreeing. If a
`display_name:` ever becomes needed, it can be added later without breaking
existing files.

Sort order: alphabetical by filename stem.

`configs/filters/` is auto-created on startup if it does not exist (empty dir
is fine — the Saved pane just shows nothing).

### Recent history

Reuses `~/.fleetroll/filter_history` from `mvp-5mt`. No format change.

Order: most-recent first.

## UI

### Opening

- Key `F` (shift-f) toggles the popup open.
- On open, the **Saved** tab is active. Cursor lands on the first row (or on
  the last-used cursor position if the popup has been opened before this
  session — see "Cursor memory" below).

### Layout

One tab visible at a time (not side-by-side). Queries can be long, and one
pane gives the query string full horizontal room.

```
┌─ Filters ──────────────────────────────────┐
│  Saved  Recent                             │
│                                            │
│ › prod-talos      os=L role~talos        ▲ │
│   linux-workers   os=L role~builder        │
│   staging         env=staging            ▼ │
│                                            │
└─ find: prod ──────────────────────── 1/3 ──┘
```

- **Tab strip** (top row of popup body). Active tab is rendered with
  `A_REVERSE` (or the existing selection-accent color pair). Inactive tabs
  are plain text. Separator is two spaces. Mixed case, no brackets.
  Styled after modern TUI tabs (e.g. `claude` status line).
- **Rows**: two columns per row — the display name on the left, the query on
  the right. The cursor (`›` or reverse-video band) marks the current row.
- **Scroll indicators**: `▲` / `▼` glyphs on the right edge when the pane is
  clipped at the top / bottom, matching the main monitor display.
- **Bottom border status**:
  - Left: `find: <query>` when a search filter is active, otherwise blank
    (border dashes fill the space).
  - Right: `m/n` where `m` is the row count matching the current search filter
    in the **active** tab and `n` is the tab's total row count.
  - On an empty filtered result, the status momentarily flashes
    `no matches` (see key bindings below).

### Viewport sizing

- Popup height clamps to `screen_h - 6`.
- Popup width clamps to `screen_w - 4` (some reasonable margin; final number
  picked during implementation).
- If `screen_h < ~10`, show a "resize terminal" fallback message instead of
  the popup body.

### Key bindings (inside popup)

| Key(s) | Action |
|---|---|
| `Esc` | Close popup. One press, always — no two-step clear-then-close. |
| `F` | Close popup (symmetric with open). |
| `Enter` | Apply selected query and close. If the active pane's filtered list is empty, flash `no matches` in the bottom border for ~2s; don't close. |
| `←` / `→` | Switch tabs. Cursor position remembered per tab. |
| `↑` / `↓`, `j` / `k` | Move selection within active tab. |
| `PgUp` / `PgDn` | Jump one viewport-page. |
| `Home` / `G` | Jump to first / last row. |
| Printable chars | Append to the search filter (see below). |
| `Backspace` | Delete last character from the search filter. |
| `Ctrl-U` | Clear the search filter. |

**Search filter** is a single shared string that narrows both tabs by
substring match against display name + query text. When the filter changes:

- Each tab's cursor is reset to row 0 of its filtered list.
- Each tab's viewport scrolls to top.
- Switching tabs preserves each tab's (post-filter) cursor until the filter
  changes again.

The filter string persists while the popup is open and is discarded when the
popup closes.

### Cursor memory

- Cursor position is remembered per tab for the life of the popup (so
  left/right back-and-forth doesn't reset position).
- Cursor position is **not** persisted across popup open/close in v1. Reopening
  always lands on Saved, row 0. Can revisit if users want it.

## Applying a query

Selecting a row and pressing `Enter`:

1. Sets the monitor's current query to the row's `query` string.
2. Appends to filter history via the existing `dedupe_append` path (so
   selecting from Recent promotes it to the top).
3. Closes the popup.

## Code touch points

New files:

- `fleetroll/commands/monitor/named_filters.py`
  - `load_named_filters(configs_dir: Path) -> list[NamedFilter]`
  - `NamedFilter` dataclass with `name`, `query`, `description`
  - Creates `configs_dir` if it doesn't exist.
  - Skips malformed YAML with a log warning; doesn't crash the monitor.

- `fleetroll/commands/monitor/filters_popup.py`
  - `draw_filters_popup(stdscr, state: FiltersPopupState, ...) -> None`
  - `FiltersPopupState` holds: active tab, per-tab cursor + viewport, search
    filter string, transient message + expiry.
  - Pure helper `compute_popup_viewport(selected, viewport_start,
    visible_rows, total_rows) -> int` for unit-testable scroll math.
  - Pure helper for applying the search filter to a list of rows.

Edits:

- `fleetroll/commands/monitor/display.py` — popup state, key routing while
  popup is open, draw invocation, apply-on-Enter wiring.
- `fleetroll/commands/monitor/types.py` — add `F` to `HELP_KEYBINDINGS`.
- `fleetroll/commands/monitor/entry.py` — load named filters at startup
  (before curses wrapper).

Tests:

- `tests/test_named_filters.py` — loader: missing dir auto-created, malformed
  file skipped, sort order, display-name derivation.
- `tests/test_filters_popup.py` — pure helpers: viewport math, search
  narrowing, `m/n` counter, per-tab cursor memory under tab switches.
- `tests/tui/test_filters_popup.py` — tmux end-to-end: `F` opens, `Enter`
  applies and closes, `Esc` closes, typing narrows, `←`/`→` switches tabs
  and preserves cursor, empty-match flash message.

## Out of scope (v1)

- Save / rename / delete from within the popup.
- `description:` rendered as a second line per row (file format supports it;
  rendering is future work).
- Sticky section headers (not applicable with one-tab-at-a-time layout).
- Persisting cursor position across popup opens.
- Fuzzy match (v1 is substring).
- Multi-select / bulk-apply.
