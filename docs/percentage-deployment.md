# Deploy an override to a percentage of a role

`fleetroll override set --role ROLE --target-percent PERCENT --from-file FILE`
expands total deployed-file coverage of a recorded Puppet role. It keeps hosts
already on the exact requested full override checksum and selects deterministic
additions. It does not execute Puppet or establish convergence.

```bash
# Preview expansion from existing 10% canaries to 50% of recorded Talos hosts.
uv run fleetroll override set configs/host-lists/linux/all.list \
  --role gecko_t_linux_2404_talos --target-percent 50 \
  --from-file candidate.override

# Save the exact population and additions, then execute that reviewed plan.
uv run fleetroll override set configs/host-lists/linux/all.list \
  --role gecko_t_linux_2404_talos --target-percent 50 \
  --from-file candidate.override --save-plan /tmp/talos-50.json
uv run fleetroll override set --from-plan /tmp/talos-50.json \
  --from-file candidate.override --confirm --reason 'expand canaries to 50%'

# Expand a recorded netperf population from 50% to 100%.
uv run fleetroll override set configs/host-lists/linux/all.list \
  --role gecko_t_linux_2404_netperf --target-percent 100 \
  --from-file netperf.override --confirm
```

Without a host argument, candidates are locally recorded hosts. Explicit hosts
or one host-list file bound the scope using the same resolution and SQLite read
snapshot as monitor/gather selection. Default previews contact no hosts and
create no write audit events. They show the complete scope, source timestamps,
latest attempt status, checksum, role, target count, existing matches, additions,
eligible controls, and exclusions. `--save-plan` writes only the requested new
local file, refusing an overwrite.

## Denominator, rounding, and evidence

The denominator is the number of scoped hosts whose last successful recorded
role matches `--role`, ignoring case. Stale observations, failed latest gathers,
and hosts ineligible for additions remain in that denominator. Unknown or missing
role evidence appears separately in `unknown_membership`; it cannot be assigned
to a role or silently counted as zero fleet membership. Other known roles appear
in `outside_role`. Neither a database selection nor a supplied host list proves
inventory completeness. Review the unknown bucket and scope before interpreting
100% coverage.

The target is `ceil(denominator * target_percent / 100)`. Percentages must be
finite numbers from 0 through 100. For 20 hosts, 10% is two hosts and 50% is ten:
with two exact matches, expansion selects eight additions. For three hosts, 50%
rounds up to two. Existing canaries need not be first in hostname order; they
always count toward the total when their recorded full checksum matches.

Additions require a successful latest host gather, a valid observation timestamp
no older than the shared one-hour staleness threshold, Linux/macOS, and explicit
override absence without a contradictory checksum. Invalid, future, or
timezone-less timestamps are unknown and ineligible. Conflicting overrides,
unknown presence, missing checksums, unsupported OS, stale data, and failed
gathers are listed with reasons. No age cutoff removes a host from the known-role
denominator. Existing exact matches are counted from recorded evidence even when
stale or retained after a failure, with their ages/status still visible.

Recent recorded override writes invalidate older file observations. When such
writes could affect the role's remaining target, the report uses
`verification_required` and selects no new additions. Refresh the affected hosts
with `gather host` before expanding. A strictly newer observation is required;
write and observation timestamps in the same whole second are ambiguous. This
prevents a failed or skipped refresh, including `--no-audit`, from causing a new
wave based on old absent-file evidence. It reuses retained configuration-write
boundaries and adds no task-history or inventory store.

Eligible additions sort by natural full hostname, with the literal hostname as
a final deterministic tie-breaker. The first needed hosts are selected; other
eligible absent hosts remain controls. Excluded hosts stay separate from those
controls. With insufficient eligible hosts, confirmation refuses the wave
before any SSH or writes. If the target is already met, nothing is written.
If it is exceeded, `target_exceeded` is reported; reducing a percentage,
including zero, never removes overrides.

## Preview and execution consistency

Direct `--confirm` invocations recompute their role population. `--save-plan` and
`--from-plan` freeze the population and additions across preview/confirmation.
`--from-plan` excludes role, percentage, host, and save-plan arguments, but still
requires `--from-file` with exactly the saved checksum. Plans carry expectations,
not override contents or credentials. Normal mode/owner/group/reason and SSH
options are supplied on execution, and previews expose those settings.

Before writing, Fleetroll rechecks recorded roles, OS, override state, and latest
attempt status within the frozen scope, and requires fresh current evidence for
additions. Changed controls or existing-match evidence block the wave; freshly
recorded progress of selected additions onto the desired checksum is allowed.
New hosts outside a saved scope do not expand that saved plan. Retrying the same
saved additions can return `already_current` without selecting replacement hosts.

Each remote operation checks the live Puppet role and existing override. It
verifies incoming bytes against the planned full checksum, applies the normal
owner/group/mode, and installs a complete file through a same-directory POSIX
hard link. That install cannot overwrite a destination created concurrently.
An existing identical override is a no-op; a different file, symlink, directory,
unreadable state, missing role, or changed role fails that host. No conflicting
file is replaced, so additions create no backup file. `--force` and
`--ignore-state` are rejected in percentage mode; ordinary explicit deployment
retains those options and its existing behavior.

The existing local override validators, worker-type compatibility checks and
explicit `--allow-worker-type-mismatch` bypass still apply. Existing per-host
deployment audit records gain percentage/selection provenance. Successful,
failed, and uncertain SSH attempts are refreshed through the normal automatic
`gather host` path unless `--no-audit` is supplied. Refresh failures remain
warnings; file-operation failures remain visible and produce nonzero exit status.
Concurrent role changes after the final role check, changes to existing cohort
members not yet recorded, and concurrent independent percentage plans remain
outside a fleet transaction. Coordinate simultaneous rollouts.

## JSON and exit status

Percentage mode emits a single JSON document with `--json`, including during
automatic refresh; refresh text goes to stderr. Other explicit deployment output
contracts are unchanged. The version-1 envelope contains `action`, `dry_run`,
`status`, `plan`, `deployment`, `worker_type_checks`, the explicit bypass flag,
`blocked_reason`, `results`, `failed`, `refresh_requested`, `achieved_coverage`,
and `puppet_convergence: "not_checked"`.

The plan describes its schema/action, creation time, source, role, target
percentage/checksum, all scoped host evidence, denominator, target count,
recorded matches, additions, controls, exclusions, unknown membership, outside
roles, and writes awaiting verification. Scope entries preserve source/attempt
timestamps; `pending_write_ts` is the latest retained override-write timestamp in
UTC Unix seconds. Freshness labels are computed at display time, while the saved
selection is evaluated at plan creation time. The saved-plan file omits derived
report fields; use the file written by `--save-plan`, not the enriched envelope,
as `--from-plan` input. Unsupported versions/actions, extra fields, duplicate
hosts, invalid types, and additions inconsistent with the saved evidence are
rejected before SSH.

`achieved_coverage` is an estimate based on recorded existing matches plus
successful operations in this invocation. Its `basis` explicitly says
`recorded_existing_plus_successful_file_operations`; it is neither fresh live
coverage of every member nor Puppet verification. Partial failures report the
lower resulting estimate and all per-host outcomes. Existing matches retain
their evidence timestamps in the plan.

Exit 0 means a preview was produced, eligible file operations succeeded, no role
hosts matched, or no additions were needed. A preview may show blocked or
insufficient evidence without executing. Exit 1 means confirmation was blocked
or at least one operation failed. Invalid options, files, or plans exit 2.
