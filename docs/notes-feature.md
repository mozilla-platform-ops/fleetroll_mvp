# Notes Feature

## Problem

Users need a way to annotate hosts with context notes for operational tracking:
- Investigation notes ("Investigating network issues")
- Maintenance notes ("Scheduled for hardware upgrade")
- Status notes ("Waiting for reboot")
- General observations ("Performance degraded after last rollout")

Currently there's no way to attach freeform text annotations to hosts.

## Solution

Add a notes system that stores host-specific annotations locally without requiring SSH operations.

### Design Principles

1. **Local-only operations** - No SSH to remote hosts (notes are metadata about hosts, not on hosts)
2. **Append-only log** - Immutable audit trail of all notes
3. **Simple JSONL storage** - Following fleetroll's existing patterns
4. **Single-host operations** - Keep it simple (batch mode can be added later)
5. **Monitor integration** - Show latest note in monitor display for quick context

## Architecture

### Storage

**File**: `~/.fleetroll/notes.jsonl`

**Format**: JSONL (JSON Lines), one record per line

**Record Structure**:
```json
{
  "ts": "2026-01-28T12:34:56+00:00",
  "actor": "username",
  "action": "host.note_add",
  "host": "hostname.example.com",
  "note": "Note text here"
}
```

### Commands

#### `note-add` - Add a Note

Add a note to a single host (local operation, no SSH).

```bash
# Basic usage
fleetroll note-add HOSTNAME "Note text here"

# With JSON output
fleetroll note-add HOSTNAME "Note text" --json

# Override notes log location
fleetroll note-add HOSTNAME "Note text" --audit-log /path/to/notes.jsonl
```

**Arguments**:
- `HOSTNAME` - Target hostname
- `NOTE_TEXT` - The note text (quoted if contains spaces)

**Options**:
- `--audit-log PATH` - Override notes log location (default: `~/.fleetroll/notes.jsonl`)
- `--json` - Output JSON format

**Output** (human-readable):
```
Added note to hostname.example.com: "Note text here"
```

**Output** (JSON):
```json
{
  "ts": "2026-01-28T12:34:56+00:00",
  "actor": "username",
  "action": "host.note_add",
  "host": "hostname.example.com",
  "note": "Note text here"
}
```

#### `show-notes` - View Notes

Show all notes for a host in chronological order (oldest first).

```bash
# Show all notes for a host
fleetroll show-notes HOSTNAME

# Show only 5 most recent notes
fleetroll show-notes HOSTNAME --limit 5

# JSON output
fleetroll show-notes HOSTNAME --json
```

**Arguments**:
- `HOSTNAME` - Target hostname

**Options**:
- `--limit N` - Show only the N most recent notes
- `--audit-log PATH` - Override notes log location
- `--json` - Output JSON format

**Output** (human-readable):
```
Notes for hostname.example.com:

2026-01-28 12:34:56 (aerickson)
  Investigating network issues

2026-01-28 14:22:10 (aerickson)
  Applied temporary network fix

2 notes total
```

**Output** (JSON):
```json
[
  {
    "ts": "2026-01-28T12:34:56+00:00",
    "actor": "aerickson",
    "action": "host.note_add",
    "host": "hostname.example.com",
    "note": "Investigating network issues"
  },
  {
    "ts": "2026-01-28T14:22:10+00:00",
    "actor": "aerickson",
    "action": "host.note_add",
    "host": "hostname.example.com",
    "note": "Applied temporary network fix"
  }
]
```

### Monitor Integration

The `monitor` command displays the most recent note for each host in a new `LATEST_NOTE` column.

**Display Format**:
- Truncated to ~40 characters for table display
- Full note visible via `show-notes` command
- Color coding:
  - Gray/default: No notes
  - Yellow: Notes present (indicates attention/context available)

**Example Monitor Output**:
```
HOST                    ROLE              LATEST_NOTE
t-linux64-ms-238        gecko-t-talos     Investigating network issues...
t-linux64-ms-239        gecko-t-talos     -
```

## Implementation Files

### New Files

- **`fleetroll/notes.py`** - Core notes utilities
  - `default_notes_log_path()` - Return `~/.fleetroll/notes.jsonl`
  - `append_note()` - Add note record
  - `iter_notes()` - Iterate notes with optional host filter
  - `load_latest_notes()` - Get most recent note per host (for monitor)

- **`fleetroll/commands/note.py`** - Command implementations
  - `cmd_note_add()` - Add note command
  - `cmd_show_notes()` - Show notes command

### Modified Files

- **`fleetroll/cli.py`** - Add command decorators
- **`fleetroll/commands/__init__.py`** - Export new commands
- **`fleetroll/commands/monitor.py`** - Integrate notes into display
- **`fleetroll/constants.py`** - Add `NOTES_FILE_NAME = "notes.jsonl"`

## Usage Examples

### Basic Note Tracking

```bash
# Add a note about an investigation
fleetroll note-add host1.example.com "Investigating high CPU usage"

# Add follow-up notes
fleetroll note-add host1.example.com "Found rogue process, killed it"
fleetroll note-add host1.example.com "Monitoring for 24h to ensure stability"

# View all notes
fleetroll show-notes host1.example.com

# View only last 2 notes
fleetroll show-notes host1.example.com --limit 2
```

### Integration with Workflows

```bash
# Before maintenance
fleetroll note-add host1.example.com "Starting maintenance window"

# Perform operations
fleetroll host-set-override host1.example.com new-config.yaml

# After maintenance
fleetroll note-add host1.example.com "Maintenance complete, monitoring"

# Check status in monitor
fleetroll monitor hosts.list
# (Will show latest note in LATEST_NOTE column)
```

### Investigation Tracking

```bash
# Document investigation steps
fleetroll note-add problematic-host "Worker quarantined, investigating"
fleetroll note-add problematic-host "Disk at 98%, cleaning up logs"
fleetroll note-add problematic-host "Disk cleaned, worker back online"

# Review full investigation history
fleetroll show-notes problematic-host
```

## Future Enhancements (Out of Scope)

- **Batch mode** - Add same note to multiple hosts from file
- **Note editing** - Modify or delete existing notes
- **Note categories/tags** - Structured metadata (e.g., `--category investigation`)
- **Search** - Find notes by text content across all hosts
- **Export** - Generate reports from notes
- **Attachments** - Link notes to specific rollouts or audit records

## Testing Strategy

### Unit Tests (`tests/test_notes.py`)
- `append_note()` creates correct record structure
- `iter_notes()` filters by host correctly
- `load_latest_notes()` returns most recent per host
- Handle missing/empty notes file gracefully

### Command Tests (`tests/test_commands_note.py`)
- `cmd_note_add()` writes to file and outputs correctly
- `cmd_show_notes()` displays notes chronologically
- `--limit` flag works correctly
- `--json` output format is valid

### Integration Tests
- Monitor displays latest notes correctly
- Notes survive file appends (concurrent writes)
- Notes file created automatically in `~/.fleetroll/`

## Verification Checklist

After implementation:

- [ ] `fleetroll note-add testhost "test"` creates `~/.fleetroll/notes.jsonl`
- [ ] Multiple notes append correctly (no overwrites)
- [ ] `fleetroll show-notes testhost` displays all notes chronologically
- [ ] `fleetroll show-notes testhost --limit 2` shows only 2 most recent
- [ ] `fleetroll show-notes --json` produces valid JSON
- [ ] Monitor command shows latest note in new column
- [ ] Monitor updates when new notes are added (polling)
- [ ] Different hosts have isolated notes (filtering works)
- [ ] Actor is correctly inferred from environment
