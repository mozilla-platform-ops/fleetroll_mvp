# Monitor Module Structure

The monitor command has been refactored into a well-organized package with clear separation of concerns.

## Module Overview

```
monitor/
├── __init__.py       # Public API (re-exports all functions)
├── types.py          # Constants (FLEETROLL_MASCOT, COLUMN_GUIDE_TEXT)
├── data.py           # Data layer (loading, filtering, aggregation)
├── formatting.py     # Text rendering (column layout, cell formatting)
├── display.py        # UI layer (curses display with colors)
└── entry.py          # Entry point (cmd_host_monitor orchestration)
```

## Dependency Hierarchy

```
types.py (no internal dependencies)
  ↓
data.py (imports: types, audit, humanhash)
  ↓
formatting.py (imports: types, data)
  ↓
display.py (imports: types, data, formatting)
  ↓
entry.py (imports: data, formatting, display, utils, exceptions)
  ↓
__init__.py (re-exports from all modules)
```

## Design Principles

### Separation of Concerns

- **Data layer** (`data.py`): Pure data transformations, no rendering logic
- **Formatting layer** (`formatting.py`): Text layout and rendering, no curses
- **UI layer** (`display.py`): Interactive curses display
- **Entry point** (`entry.py`): Orchestrates everything

### Testability

- **data.py**: Easily unit testable without mocking (pure functions)
- **formatting.py**: Testable without curses dependencies
- **display.py**: Harder to test (requires curses), but isolated
- **entry.py**: Integration point, tests can import individual layers

### Backward Compatibility

All public functions remain importable from `fleetroll.commands.monitor` via re-exports in `__init__.py`. No changes needed to existing code or tests.

## Key Functions by Module

### data.py
- `load_latest_records()` - Load audit records
- `load_tc_worker_data()` - Load TaskCluster worker data
- `build_row_values()` - Build row data from records
- `tail_audit_log()` - Stream new audit records
- `AuditLogTailer` - Non-blocking log tailer

### formatting.py
- `compute_columns_and_widths()` - Determine column layout
- `render_monitor_lines()` - Render header and rows
- `clip_cell()` - Truncate and pad cell text

### display.py
- `MonitorDisplay` - Curses UI with color, pagination, scrolling

### entry.py
- `cmd_host_monitor()` - Main command entry point
