# Rollout report

Compare master and overrides within a role using verified configuration history
and recent Taskcluster outcomes. The report reads evidence and queries TC; it
does not gather host state, change overrides, advance a rollout, or write history.

Use `rollout compare-pools` to compare matching workloads between an explicit
candidate and baseline worker pool. Both commands run once; regular gathers
supply their configuration evidence. By default, a host observation over one
hour old prevents that worker's live job query entirely. Refresh with
`fr gather all HOST_OR_FILE` using the same database before running either report.
Taskcluster-only gathers do not refresh host configuration.
Historical cutoffs inside compacted spans can instead produce
`historical_detail_unavailable`, even with regular successful gathers. The error
shows retained endpoints and the maximum polling gap; it does not establish an
outage. See [historical evidence limits and recovery](reference/configuration-history.md).

## Run a report

Refresh relevant hosts and references first, then select by role, worker pool,
or explicit hostnames/file:

```bash
uv run fleetroll gather all configs/host-lists/linux/all.list
uv run fleetroll rollout report --role gecko_t_linux_2404_talos
uv run fleetroll rollout report --worker-type gecko-t-linux-talos-2404 --json
uv run fleetroll rollout report t-linux64-ms-016 t-linux64-ms-017
uv run fleetroll rollout report --role gecko_t_linux_2404_talos \
  --since 2026-11-03T23:33:16Z --min-finished 10
```

Bare worker types default to `releng-hardware`; use `provisioner/worker-type`
for another provisioner. Local TC credentials are required for worker/task queries.
No separate post-removal mode is needed. This command replaces the removed
`scripts/check_rollout_jobs.py` and `scripts/check_override_jobs.py` entry points;
consumers of their deployment-only JSON must migrate to the versioned summary.

| Option | Default and effect |
| --- | --- |
| `--since`, `--until` | Preceding 24 hours, ending now; use `--until` for a fixed historical horizon |
| `--max-observation-gap` | 3,600 seconds; maximum age/continuity bound between configuration observations |
| `--min-finished` | 20 finished tasks per comparison side and for independent cohort health |
| `--min-coverage` | 80% task attribution warning threshold; does not veto comparison. 0 suppresses threshold warnings for measured coverage |
| `--min-host-coverage` | 80% successfully queried hosts; applies within each comparison's role/pool/group |
| `--metadata-workers` | 12 concurrent task-name requests (1–20), independent of `--workers` |
| `--no-task-name-cache` | Fetch names without reading or writing the shared task-name cache |
| `--details` | Revisions, outcomes, queue timestamps/errors, membership, failure patterns, per-host evidence |
| `--verbose` / `-v` | Details plus individual tasks, unknown history ranges, and individual diagnostics |

Repeat `--observations-file` to supplement retained history without importing it:

```bash
uv run fleetroll rollout report --role gecko_t_linux_2404_talos \
  --observations-file rollout-start.json --observations-file rollout-followup.json
```

Accepted files contain single observations, arrays, gather `results` arrays, or
monitor hostname-to-observation maps. A larger gap bound accepts longer inferred
continuity; it cannot restore missing snapshots or bridge contradictory state.

## Read the result

The default report shows whole-pool queue depth, populations labelled
`role: provisioner/pool`, cohort health, shared-window comparisons, and evidence
limits. Worker group appears in headings when needed to distinguish groups and
is always retained in details/JSON. Current host counts require verified current
configuration; task counts use verified history over the report window.
`Done` means completed tasks. File-presence percentages are separate from
verified cohort membership and appear when they add information.

Master and each override have independent health assessments, including when
there is no control cohort. “Healthy in verified sample” requires the minimum
finished sample with no failed, exception, or unknown outcomes. Small samples do
not hide observed failures. Mixed applied revisions carry a warning. A cohort
with no current hosts may still have historical tasks.

Each pair uses its overlapping verified time range. Comparisons show finished
tasks against the per-side minimum, failure/exception rates, signed differences,
and attribution. Positive differences mean a higher override rate; negative means
a higher master rate. Equal rates can coexist with failures in both cohorts.
Rates describe samples, not causation, statistical significance, or approval to
advance/remove an override.

Missing cohorts, inadequate samples, mixed revisions, unknown outcomes,
unresolved queries, or insufficient host-query coverage can make a comparison
unavailable. The report names blockers and suggests `--min-finished` for a small
sample. Lowering that minimum does not remove other blockers.

Task attribution and host-query coverage answer different questions. Low task
attribution warns but does not veto an otherwise comparable sample. The report
shows percentages and a `--min-coverage` hint when any measured overall/side
coverage is below the threshold. Unavailable coverage and observed failures remain
visible even with threshold warnings disabled. Six unqueried hosts out of 132
meet the default host-query threshold (95.5% queried), but errors still cause a
nonzero exit. Unverified current state does not invalidate verified historical work.

Unattributed failures/exceptions entirely within a pair's shared window appear
beside the comparison, with task links under details. Report-window exclusions
are shown separately. Unknown configurations never enter cohort rates or count
as passes. Rates use completed + failed + exception as denominator; pending,
running, and unknown outcomes are not successful work. Task-name patterns are
observations, not root-cause classifications.

## Task names

In-window task rows include workload names for successes as well as failures.
Each previously unseen task ID requires a separate Queue task-definition request;
requests run concurrently and repeated runs of one task share one lookup. Normal
`gather tc` does not collect names and makes no additional requests for this feature.

Names share the `rollout compare-pools` SQLite sidecar beside `--db` (normally
`~/.fleetroll/fleetroll.task-names.sqlite3`). Entries expire after 30 days; retention
is capped at 50,000 names across service roots. Both commands reuse cached names,
including names of failed tasks, while outcomes and worker evidence refresh live.
`--no-task-name-cache` bypasses reads and writes. Cache errors warn and fall back
to live lookups.

Name lookup failures leave `name: null` and set `name_error`; they warn without
changing task outcomes, attribution, or the report exit status. Failed lookups
are retried on the next invocation. Tasks with unresolved query/identity errors,
outside the report window, or in known out-of-scope configurations are not enriched.

## Evidence and selection

[Compact configuration history](reference/configuration-history.md) preserves 30 days of
evidence alongside ten full snapshots per host and the last-success exception.
The operation log alone cannot recover pruned convergence evidence. Retention,
log rotation, and missing saved snapshots may leave gaps.

A verified configuration requires successful Puppet metadata, a clean applied
revision, matching role, and matching deployed/applied override and vault checksums.
Master also requires the default upstream repository and either an explicit master
branch or an applied revision found in retained master references. Override
absence alone does not prove master convergence.

Verified intervals end at successful observations, never at now. Initial coverage
starts no earlier than the successful Puppet timestamp and observation-gap bound.
Matching observations bridge only within that bound. Repeated Puppet runs do not
move activation forward; revision changes create transitions. Failed collections
provide no configuration evidence and do not themselves break continuity, but the
gap between successful observations remains bounded. Unverified state,
writes/removals, and same-timestamp conflicts break continuity.
Unobserved changes reverted between scans are undetectable.

A task must start and finish within one verified interval; a running task is
checked through the observation horizon. Tasks crossing transitions, outside the
window, or lacking identity/configuration proof are excluded explicitly. The
report retrieves at most 20 recent runs per host; a full retrieval window warns
of truncation. Query failures have unknown task counts, never zero-job successes.

Observed worker identity takes precedence. Explicit collection failure blocks
legacy fallback; older records use role/override mapping with a warning.
Comparisons stay within role, provisioner, pool, and worker group. `--role` and
`--worker-type` also scope verified historical configurations; explicit host
selection retains history across roles/pools.

Selection is applied after attribution against the full timeline, so it cannot
bridge transitions. Verified out-of-scope work is excluded from selected cohort
health, comparisons, patterns, coverage, and failure exit status. Unknown
configuration remains in the coverage denominator. Details retain omitted
populations/tasks; all-retrieved outcomes distinguish known out-of-scope work from
unknown evidence. Explicit selections describe only selected hosts, not the fleet.

Latest host failures within five minutes of report end receive a human warning
and refresh suggestion because a reboot may be involved. They remain unqueried,
unverified, and errors in JSON/exit status. Refresh successfully to establish state.

### Independent task identity proof

A successful audit with complete Puppet/file evidence can remain useful when
historical worker identity collection was unavailable. Attribution first uses
the normal host configuration history. If that cannot cover a task, Fleetroll
can combine independent Puppet history with Queue's identity for that exact run.
This requires a fresh, successful, nonlegacy host identity for querying and a
unique integer run ID whose Queue provisioner, worker type, group, and worker ID
all match. Current pool membership alone does not establish historical identity.
Known identity contradictions, ambiguous/invalid collection, writes, unverified
Puppet/files, excessive gaps, and transitions remain barriers.

`query_identity_evidence` names the host association source explicitly:
`host_collected_configuration` or `legacy_role_override_mapping`, with its
observation timestamp and full identity. Configured identity is not proof of the
current worker process's running identity. Queue evidence establishes ownership
of the exact historical run. The combined method retains its configured host
association under `host_association`; broader configured/runtime identity rules
remain separate design work (`mvp-dx2b`).

The resulting identity interval covers only that task's start/end, while
`puppet_interval` reports the independently supported configuration range.
It does not fill other gaps or upgrade current host status. Failed overall
collections remain absence of new evidence; normalized partial fields from a
failed collection are not a new trusted snapshot. Unknown vault presence and
absent-vault/applied-vault contradictions are unverified as well.

New compact descriptors retain independent Puppet proof. Some old identity-only
refusals can be strictly revalidated from retained endpoints and available
artifacts, within retained write-history coverage. Missing proof inputs stay
unknown. This does not recover unsupported interior historical cutoffs.
See [compact history](reference/configuration-history.md) for retention behavior
and the [design/case validation](design/partial-rollout-attribution.md) for the
November 5 ms-017 example, including the expired original Queue task.

Coverage/cohort counts can increase when previously excluded runs gain complete
independent proof. Original outcomes remain unchanged: an attributed exception
is still an exception. JSON stays at version 1 with additive provenance fields;
consumers should inspect the attribution method and interval rather than assume
every task used host-collected identity throughout its Puppet interval. No
attribution result authorizes a host operation or rollout advance.

## File coverage and live queue depth

Fleet override percentages describe present/absent/unknown files among hosts with
confirmed membership, using the latest successful observation at report end.
They include unverified Puppet application; failed gathers, timestamps, and
unverified writes qualify that recorded state. This is separate from task cohorts.

Queue counts are approximate whole-pool pending counts fetched once per effective
pool through TC `taskQueueCounts`, including hosts outside the selection. They
are live even for historical `--until`. Pool resolution uses recorded identity at
the report horizon, identifying unresolved/legacy cases. Failures show unavailable,
not zero, and do not change exit status. Zero queued work warns that no new work
is pending; running tasks can still finish. Details/JSON retain fetch timestamps.

## JSON contract (version 1)

Top-level fields are `schema_version`, `configuration_history`,
`fleet_override_status`, `queue_counts`, `window`, `selection`, `hosts`, `summary`,
`comparisons`, `errors`, `warnings`, and `task_name_lookups`. Additive fields below retain version 1.

| Area | Fields and semantics |
| --- | --- |
| Selection | Role or provisioner/pool selectors; null for explicit hosts. Per-host `out_of_scope_history` retains omitted `intervals`, `jobs`, and verified current `configuration`; ordinary fields serve the selected assessment. |
| Host evidence | Jobs and exclusion reasons; intervals with raw/compact sources, maximum polling gaps, cutoffs; `configuration_evidence` with pinned provenance/reasons; `configuration_gaps` for unknown ranges. Gaps describe the full timeline, not known out-of-scope work. |
| Task metadata | Job `name` and `name_error` are nullable strings, separate from `query_error`. `task_name_lookups` counts unique task IDs: `cached`, `reused` (names already present in rows), `fetched`, `failed`, and cache `warnings`. |
| Task identity and attribution | Host `query_identity_evidence` records the configured/legacy query association and timestamp, or null when querying is unavailable. Job `identity_evidence` is null on failed/unresolved Queue checks; otherwise it records `source: queue.status`, task/run IDs, and all four native identity fields. `attribution_evidence` is null for exclusions or has method `host_configuration_history` / `queue_run_identity_and_puppet_history`. The combined method includes Queue evidence, `host_association`, `puppet_interval`, and the task-scoped supported `interval`. |
| Coverage | Fetched, selected in-window, classified, excluded, unresolved tasks; requested/queried hosts and observation freshness. `out_of_scope` counts known omitted in-window work; `outside_window` counts retrieved work beyond time bounds. |
| Summary | Cohorts, applied revisions, outcome totals/rates, `evidence` sample/attribution fields. `all_observed_outcomes` includes all retrieved in-window work; `out_of_scope_outcomes` is known omitted work; `unattributed_outcomes` is unknown configuration only. |
| Cohort health | `health.status`, `sample_status`, `minimum_finished`, `basis: "verified tasks in report window"`, `warnings` |
| Comparisons | `observed_result`, `sample_status`, `minimum_finished`, `attribution`, `unattributed_failures`; assessment/comparison `warnings` and `host_query_coverage` include queried/requested counts and observed/minimum fractions. |
| Attribution | `status` (`limited` or `meets threshold`), `fraction`, `warning_threshold`, `warnings`; independent of comparison `status` |
| History metadata | Availability, schema version, retention cutoff, compact span count, and `historical_cutoffs` with host, cutoff, span endpoints, maximum polling gap, verification/invalidation state, and proof provenance. Per-host results also expose their crossing spans, requested `max_observation_gap_s`, and `observation_status`: `fresh`, `stale`, `missing`, `failed`, or `historical_detail_unavailable`. |
| Proof provenance | Compact `configuration_evidence` adds `proof_version` and `worker_identity_reason`. Internal proof versions 1/2 are independent of report schema v1; cutoff `puppet_verified` distinguishes stored Puppet-only proof from full `verified` proof. Read-time revalidation does not backfill those stored flags. Sources can include `revalidated_compact`; task-qualified intervals carry `identity_source: queue.status`. |
| Queue counts | `scope: whole_pool`, `approximate: true`, `pools`, `unresolved_hosts`, `legacy_mapped_hosts`. Each pool has `worker_pool`, nullable `pending_tasks`, `checked_at`, `error`. Also present in fleet-status JSON. |

Unavailable comparisons use `observed_result: "comparison unavailable"` and
`sample_status: "unavailable"`. The legacy JSON result strings for comparable
samples remain “no regression observed in verified tasks”, “higher override
failure rate observed”, or “higher override exception rate observed”. The first
includes higher master rates; inspect both rates and signed differences for
direction. A `comparable` status can carry low-attribution warnings.

All-retrieved outcome totals in details/JSON do not establish configuration
attribution or complete history. Missing host queries and the 20-run limit can
omit additional jobs. Check [tool integration requirements](reference/tool-integrations.md)
when changing schemas or evidence meaning.

## Exit status

| Code | Meaning |
| --- | --- |
| 0 | No classified failures or query errors; evidence can still be insufficient |
| 1 | Classified failed/exception/unknown outcome or query error |
| 2 | Invalid input or unavailable local evidence |

Exit 0 is not rollout approval. Classified failures outside the selection do not
affect its exit code; query errors remain errors.

## Rendering notes for maintainers

Wrap identifiers without truncation to terminal width (at most 120 columns);
use labelled rows on narrow terminals. Only the title starts at column zero:
sections/populations indent two spaces, nested headings four, and bodies two
further, including wrapped diagnostics. Neutral text carries sample, attribution,
mixed-revision, and retrieval-limit notes. Yellow marks observed failures/unknown
outcomes, missing data needing refresh, and operational queue issues; errors use red.

Use LF when redirected and explicit CRLF on TTYs, including progress lines, so
output aligns with ONLCR/OPOST disabled. Do not change terminal settings. Validate
normal/narrow tmux rendering, a PTY with newline translation disabled, redirected
output, and unchanged terminal flags. Renderer checks should use identical report
fixtures and cover evidence gaps, multiple populations, long names, details, and
JSON invariance. This reproduces a cause of drift without identifying which
process changed a user's terminal state (work tracked by `mvp-fgja`, `mvp-ooy5`).
