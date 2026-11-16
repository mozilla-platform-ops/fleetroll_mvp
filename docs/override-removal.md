# Remove overrides from a recorded population

`fleetroll override unset` accepts the monitor's `--filter QUERY` and
`--filter-file FILE.yaml` selectors. Without host arguments, candidates come from
the local database. An explicit host, several hosts, or a host-list file bounds
the candidate population. Selection uses the [shared query language](monitor-filter-sort.md)
and does not discover inventory or refresh observations.

```bash
# Preview the exact branch population within the Moonshot baseline.
uv run fleetroll override unset configs/host-lists/linux/all.list \
  --filter 'ovr_bch=my-test-branch'

# Preview and save the exact hosts and full checksums for later confirmation.
uv run fleetroll override unset configs/host-lists/linux/all.list \
  --filter 'ovr_bch=my-test-branch' --save-plan /tmp/remove-my-test-branch.json

# Review the saved population again, then execute that population without reselecting.
uv run fleetroll override unset --from-plan /tmp/remove-my-test-branch.json
uv run fleetroll override unset --from-plan /tmp/remove-my-test-branch.json \
  --confirm --reason 'test branch merged'
```

`ovr_bch=...` is case-insensitive exact branch matching; `ovr_bch~...` can select
several branches. `ovr_checksum=FULL_CHECKSUM` selects exact file content.
Every selected host and full expected checksum appears in the preview; there
is no truncated host list. Different files on one branch keep distinct expected
checksums. Hosts with absent or unknown overrides remain visible when the query
matches them. No managed host is contacted or changed during a preview, and no
write audit record is created. `--save-plan` writes only the requested local file
and refuses to overwrite it.

You can also run the filter directly with `--confirm`. Each invocation evaluates
its query anew: a previous preview does not bind a later filter invocation.
Use `--save-plan` and `--from-plan` when execution must use exactly the reviewed
hosts/checksums, even if membership or recorded files subsequently change.
`--from-plan` excludes host/filter arguments; it never reselects. `--save-plan`
is preview-only and requires a filter. These modes use the existing `--confirm`,
`--no-backup`, `--no-audit`, SSH, worker, and reason options.

## Evidence and freshness

Selection and expected checksums come from one SQLite read snapshot. As in the
monitor, a failed latest gather can retain the last successful observation.
Previews preserve the source observation timestamp, latest attempt timestamp
and success status, branch, presence, checksum, and OS. Evidence older than
the shared one-hour staleness threshold is labeled stale; missing, invalid,
timezone-less, and future observation timestamps are unknown.

There is no age-based exclusion or automatic plan expiry. Old observations and
failed latest gathers stay visible, with their original timestamps. Selection
can therefore reflect an old role or branch; review its age and refresh using
`gather host` when needed. Freshness does not replace the live checksum check.
Freshness labels are recomputed when a saved plan is viewed/executed; its source
timestamps and selection remain fixed. Saved plans are local operator files,
not authenticated approvals, inventory records, or evidence of current state.

Unknown presence, missing/invalid full checksums for present files, contradictory
absent-file/checksum evidence, and unsupported/unknown OS block that host without
SSH. Linux and macOS are supported. A blocked host is reported as a per-host
failure; other eligible hosts can proceed.

## Execution and limits

For each eligible host, the existing unset worker builds a checked removal
script. It detects Linux/macOS paths live and runs file checks as root through
`sudo -n`, so sudo failure cannot become an absent-file success. Symlinks and
nonregular/unreadable files are refused. A present file must match the expected
full SHA256 before backup and again immediately before removal. Mismatched or
unavailable content is preserved. An already-absent file is an idempotent no-op;
a plan that expected absence refuses a newly present file.

Backups are enabled by default and retain the existing timestamp suffix and
30-backup cap. Each blocked or attempted host gets a write audit result with
its expected checksum and selection provenance. Connection failures and changed
overrides are independent failures, preserving successful results from other
hosts. Attempted hosts, including failed attempts, are refreshed through the
existing automatic `gather host` path unless `--no-audit` is supplied. Refresh
failure emits a warning and retains the file-operation result.

The checksum check and unlink are separate filesystem operations. A concurrent
writer can still replace a file between the final check and unlink; this is not
an atomic compare-and-delete or a fleet transaction. Coordinate concurrent
override writes. A file removal result also does not prove Puppet has run or
converged afterward. Automatic refresh records observations; this command does
not evaluate Puppet convergence.

Unfiltered explicit host/list invocations preserve their existing behavior and
output shapes, including their unchecked removal script. Add a filter such as
`--filter 'override=present'` to use recorded-checksum protection on explicit
targets.

## JSON and exit status

Population modes emit one JSON document with `--json`; refresh output goes to
stderr. Preview output has `dry_run: true`, `action`, `status`, and `plan`, plus
backup/reason settings. The plan description includes candidate/selected counts,
scope, normalized query, creation time, freshness threshold, and all targets
with timestamps, freshness labels, and `blocked_reason`.

`--save-plan` writes plan schema version 1, action `host.unset_override`, scope,
normalized query, creation time, candidate count, and targets. It omits derived
freshness/blocked labels so they can be recomputed. Use that file for
`--from-plan`; the enriched preview JSON is not the saved-plan input format.
Unsupported versions/actions, duplicate hosts, invalid destinations, unknown
fields, and invalid field types are rejected before SSH.

Execution output has `schema_version: 1`, `dry_run: false`, `action`, `plan`,
`results`, `failed`, `status`, `refresh_requested`, and
`puppet_convergence: "not_checked"`. Each result retains normal audit fields,
plus selection provenance, expected checksum, any observed live checksum,
and a machine-readable status: `removed`, `absent`, `blocked`,
`checksum_mismatch`, `unavailable`, `unsupported_os`, or
`verification_unavailable` (including SSH/protocol failures). Details and SSH
exit status remain in `error`, `stderr`, and `ssh_rc`.

Exit 0 means preview produced, all selected file operations succeeded, or an
empty selection (`status: "no_matches"`). Exit 1 means at least one blocked or
failed host. Invalid inputs/plans or conflicting options exit 2. Successful
removal is distinct from subsequent Puppet convergence.
