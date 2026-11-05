# Host notes

Notes attach investigation or maintenance context to a host without SSH. They
use an append-only JSONL log: clearing notes hides earlier entries without
deleting their history.

## Add, read, and clear

```bash
uv run fleetroll note add worker.example.test "Investigating network failures"
uv run fleetroll note show worker.example.test --limit 5
uv run fleetroll note show worker.example.test --json
uv run fleetroll note clear worker.example.test --reason "Investigation complete"
uv run fleetroll note show worker.example.test --include-cleared
```

Use an FQDN when adding or clearing notes. `show` returns entries in log order
(oldest first); `--limit N` selects the most recent N entries. By default it
shows only additions after the last clear. `--include-cleared` includes earlier
notes and clear records. JSON output is an array for `show`, or the written
record for `add` and `clear`.

All three actions accept `--notes-file PATH`. The default is `data/notes.jsonl`
under the project root when one is found, otherwise `~/.fleetroll/notes.jsonl`.
Notes are local metadata; they do not change worker state.

## Monitor display

`NOTE` shows the latest nonempty note, prefixed with `(N)` when several notes
remain since the last clear. Long text may be truncated in the table; use
`note show` to read it. Filter with `note=` for hosts without notes or `note!=`
for hosts with notes. See [filtering](monitor-filter-sort.md).

## Record format

```json
{
  "action": "host.note_add",
  "actor": "operator",
  "host": "worker.example.test",
  "note": "Investigating network failures",
  "ts": "2026-11-04T12:00:00+00:00"
}
```

A clear record uses `action: host.note_clear`, the same host/actor/timestamp
fields, and an optional `reason`. The actor is inferred from the environment.
Records are interpreted in append order. Storage and display behavior live in
[fleetroll/notes.py](../fleetroll/notes.py); command output is implemented in
[fleetroll/commands/note.py](../fleetroll/commands/note.py).
