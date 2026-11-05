# Rollout report

Compare master and overrides within a role using verified configuration history
and recent Taskcluster outcomes. The report reads evidence and queries TC; it
does not gather host state, change overrides, advance a rollout, or write history.

## Run a report

Refresh relevant hosts and references first, then select by role, worker pool,
or explicit hostnames/file:

```bash
uv run fleetroll gather all configs/host-lists/linux/2404.list
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
move activation forward; revision changes create transitions. Failed observations,
unverified state, writes/removals, and same-timestamp conflicts break continuity.
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
`comparisons`, `errors`, and `warnings`. Additive fields below retain version 1.

| Area | Fields and semantics |
| --- | --- |
| Selection | Role or provisioner/pool selectors; null for explicit hosts. Per-host `out_of_scope_history` retains omitted `intervals`, `jobs`, and verified current `configuration`; ordinary fields serve the selected assessment. |
| Host evidence | Jobs and exclusion reasons; intervals with raw/compact sources, maximum polling gaps, cutoffs; `configuration_evidence` with pinned provenance/reasons; `configuration_gaps` for unknown ranges. Gaps describe the full timeline, not known out-of-scope work. |
| Coverage | Fetched, selected in-window, classified, excluded, unresolved tasks; requested/queried hosts and observation freshness. `out_of_scope` counts known omitted in-window work; `outside_window` counts retrieved work beyond time bounds. |
| Summary | Cohorts, applied revisions, outcome totals/rates, `evidence` sample/attribution fields. `all_observed_outcomes` includes all retrieved in-window work; `out_of_scope_outcomes` is known omitted work; `unattributed_outcomes` is unknown configuration only. |
| Cohort health | `health.status`, `sample_status`, `minimum_finished`, `basis: "verified tasks in report window"`, `warnings` |
| Comparisons | `observed_result`, `sample_status`, `minimum_finished`, `attribution`, `unattributed_failures`; assessment/comparison `warnings` and `host_query_coverage` include queried/requested counts and observed/minimum fractions. |
| Attribution | `status` (`limited` or `meets threshold`), `fraction`, `warning_threshold`, `warnings`; independent of comparison `status` |
| History metadata | Availability, schema version, retention cutoff, compact span count |
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
