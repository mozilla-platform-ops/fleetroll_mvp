# Rollout report

The report compares master with each override within a role, using retained host
observations and audit events. It reads existing evidence; it does not gather,
deploy, remove overrides, advance a rollout, or write history.

## Evidence and attribution

SQLite keeps ten full observations per host plus the last successful observation
when needed. Collection also preserves a compact configuration timeline for
30 days, so ordinary snapshot pruning need not destroy rollout evidence. See
[compact history](configuration-history.md) for retention, continuity bounds,
provenance, and conservative handling of stricter queries and historical horizons.
The operation audit log alone cannot recover pruned convergence evidence.
Saved gather-host or host-monitor JSON supplied with repeatable
`--observations-file` options supplements retained history. Single observation objects, arrays,
gather `results` arrays, and monitor hostname-to-observation maps are accepted.
These are operator-provided evidence, read without importing into the database.

A configuration requires successful Puppet metadata, a clean applied revision,
matching role, and matching on-disk/applied override and vault checksums. Master
additionally requires the default upstream repository and either an explicit
master branch or an applied SHA found in retained master references. No override
file by itself does not prove master convergence.

Verified intervals end at successful observations, never at the current time.
The first interval starts no earlier than its successful Puppet timestamp and
the configured observation-gap bound. Consecutive matching observations bridge
at most that bound (one hour by default). Repeated Puppet runs do not move an
existing activation forward. A different applied revision creates a transition.
Failed observations, unverified state, and override/vault write attempts break
continuity. A write/remove event cannot establish convergence. Conflicting
observations at the same timestamp are unknown.

A failed latest host observation may be caused by a reboot, among other causes.
In the human report, failures within five minutes of the report end are shown as
warnings with a refresh suggestion. The worker is still unqueried, its current
configuration is unverified, and the report retains the error in JSON and exits
nonzero. A later successful gather is needed to establish current state.

A task must start and finish inside one verified interval (a running task is
checked through the report's observation horizon). Tasks outside the requested
window, across transitions, with unresolved worker identity, or without enough
configuration evidence remain visible as exclusions. They never count as passes.
The report cannot detect a manual change reverted between two otherwise matching
observations. Coverage describes retained evidence, not proof that no unobserved
action ever occurred. Retention and log rotation may leave gaps.

## Comparisons and coverage

Hosts are selected by role, effective worker pool, or explicit names/file. A role
can span pools, but comparisons stay within role, provisioner, pool, and worker
group. Observed worker identities take precedence; explicit collection failure
blocks legacy fallback. Older records use the existing role/override mapping
with a warning.

Fleet override status shows the present, absent, and unknown override percentages
among hosts with confirmed membership based on their latest successful observation
at the report end. It uses the fleet-status grouping rules and includes hosts whose
Puppet application is unverified. Observation timestamps, failed gathers, and
unverified writes indicate when those recorded states may be stale. This split is
distinct from the verified historical cohorts used for task comparisons. Explicit
host selections describe only the selected hosts, not the whole fleet.

Current queue depth lists approximate pending task counts per effective worker
pool, fetched once per pool via Taskcluster `taskQueueCounts`. Each count includes
its fetch timestamp and covers the whole pool, including hosts outside the report
selection. These are live counts even with historical `--until`; they are separate
from historical task outcomes and cohort attribution. Pool selection uses the
recorded identities at the report horizon. Unknown identities and legacy mapping
are identified; failed queue lookups show unavailable, never zero, and do not
change the report exit status.

Master and each override SHA have separate summaries, with applied revision
distributions. Pairwise comparisons use their overlapping verified time range
inside the requested report window. Jobs are filtered to that same window on
both sides. The report shows rates and percentage-point differences, never an
automatic recommendation to advance. Multiple applied revisions, unknown
outcomes, query errors, low attribution coverage, or too few finished tasks
qualify the comparison as insufficient evidence. Defaults require 20 finished
tasks per side and 80% attribution coverage; these are configurable evidence
thresholds, not statistical significance tests.

Rates use completed + failed + exception as their denominator. Failed and
exception rates remain separate. Pending/running/unknown outcomes do not count
as successful work. Failures grouped by task name are observed patterns, not
root-cause classifications. Each host query retains up to 20 recent task runs,
so the tool does not claim complete historical coverage. A full recent-history
window always produces a truncation warning.

JSON has `schema_version`, `configuration_history`, `fleet_override_status`, `queue_counts`, `window`, `hosts`, `summary`, `comparisons`, `errors`,
and `warnings`. Host results retain jobs and per-job exclusion reasons. Summary
coverage reports fetched, in-window, classified, excluded, and unresolved task
counts, requested/queried hosts, and observation freshness. Query failures have
unknown task counts; they are never zero-job successes. Missing comparison
cohorts still produce useful standalone summaries.

`queue_counts` contains `scope: "whole_pool"`, `approximate: true`, `pools`,
`unresolved_hosts`, and `legacy_mapped_hosts`. Each pool has `worker_pool`,
`pending_tasks` (null when unavailable), `checked_at`, and `error`. The same
additive field is included in fleet-status JSON; schema version remains 1.

The human report also shows all retrieved in-window outcomes across configurations,
including unattributed runs, and counts the reasons those runs were excluded from
configuration attribution. These totals are not cohort results and do not establish
which configuration produced a run. They cover only retrieved runs: missing host
queries and the 20-run worker history limit can leave additional jobs out.

`configuration_history` exposes availability, schema version, retention cutoff,
and compact span count. Host intervals identify raw/compact sources, retained
maximum polling gaps, and retention cutoffs. `configuration_evidence` carries
pinned provenance and uncertainty reasons; `configuration_gaps` lists unknown
ranges inside the requested window. Human output gives the cutoff and span count;
Unverified configurations are grouped by reason with host counts. `--details`
lists affected hosts and prints one attribution summary per host. `-v` or `--verbose` prints
individual tasks and unknown ranges. Repeated host warnings and errors are grouped
by reason with compact hostname ranges; `--verbose` prints the individual messages.
These additive fields keep JSON schema version 1.

Exit codes: 0 = a report with no classified failures or query errors; 1 = a
classified failed/exception/unknown outcome or query error; 2 = invalid input or
unavailable local evidence. Insufficient evidence is explicitly reported even
when exit 0; exit 0 is not rollout approval.

## Usage

Run from the repository root after refreshing the relevant hosts:

```bash
uv run fleetroll rollout-report --role gecko_t_linux_2404_talos
uv run fleetroll rollout-report --worker-type gecko-t-linux-talos-2404 --json
uv run fleetroll rollout-report t-linux64-ms-016 t-linux64-ms-017
uv run fleetroll rollout-report --role gecko_t_linux_2404_talos \
  --since 2026-11-03T23:33:16Z --min-finished 10
```

The default window is the preceding 24 hours. Use `--until` for a fixed end.
The one-hour default gap bound can deliberately exclude jobs between snapshots
taken hours apart. `--max-observation-gap` controls that policy in seconds; a
larger value accepts longer inferred continuity between matching observations
and never fills transitions or contradicting evidence. It does not recover
missing snapshots. For saved evidence, for example:

```bash
uv run fleetroll rollout-report --role gecko_t_linux_2404_talos \
  --observations-file rollout-start.json --observations-file rollout-followup.json
```

Worker types default to the `releng-hardware` provisioner; specify
`provisioner/worker-type` for another provisioner. The former
`scripts/check_rollout_jobs.py` and `scripts/check_override_jobs.py` entry points
have been removed; use `fleetroll rollout-report`. No separate post-removal
mode is needed. Per-host JSON
now includes master and unverified hosts as well as overrides. Consumers of the
old deployment-only JSON should migrate to the versioned summary schema.
