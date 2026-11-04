# Rollout report

The report compares master with each override within a role, using retained host
observations and audit events. It reads existing evidence; it does not gather,
deploy, remove overrides, advance a rollout, or write history.

## Reading the terminal report

The default output shows a compact cohort table and one comparison per pair,
followed by evidence limits. Population headings use `role: provisioner/pool`;
`[worker-group]` appears when multiple groups occur in the report. Details and JSON
always retain the group. Hosts in the table are currently verified; task counts
use verified historical membership over the full report window. `Done` means
completed tasks; unknown outcomes get a column when present. Insufficient samples
and mixed revisions remain explicit even when there are no verified failures.

Comparisons use their own shared window, with finished tasks per side against the
minimum and attribution percentages beside the conclusion. Human statements are
symmetric: master or the override can have a higher observed failure or exception
rate. Equal rates are described as equal, even if both cohorts have failures.
Signed failure-rate differences are override minus master; positive means a
higher override rate, negative means a higher master rate. These are descriptions
of verified samples, not causal findings, statistical significance, or rollout
approval. No deployment/removal direction is inferred.

Evidence limits keep coverage, excluded failures/exceptions/unknown outcomes,
query errors, observation freshness, unverified configurations, and retrieval
limits visible. Known out-of-scope work remains distinct from unknown evidence.
Override file counts appear when they add information beyond verified cohorts;
file presence never establishes successful Puppet application.

Use `--details` for outcome breakdowns, SHA/revision distributions, precise
timestamps, queue lookup timestamps/errors, fleet membership diagnostics, failure
patterns, and per-host evidence. `--verbose` includes those details plus individual
tasks and unknown history ranges. Routine compact-history span/cutoff metadata is
shown in details rather than as a default warning. Consequential history warnings
remain visible. JSON evidence and exit-code semantics are unchanged.

Output wraps to terminal width (at most 120 columns) and uses labelled cohort rows
on narrow terminals. Long identifiers wrap without truncation. Redirected output
uses plain LF lines; TTY output uses explicit CRLF to remain aligned even when
newline translation is disabled, without modifying terminal settings.
Only the report title starts at the left margin. Sections and population headings
use two spaces, nested headings use four, and their bodies use a further two.
The same hierarchy applies to details, issues, and wrapped continuation lines.
Routine sample, attribution, mixed-revision, and retrieval-limit notes use neutral
text. Yellow marks observed failures/exceptions/unknown outcomes, missing evidence
that needs a refresh, and operational issues such as an empty/unavailable queue;
errors use red. The evidence limits remain visible regardless of color.
See the [display design](rollout-report-display.md) for layout and validation notes.

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

Role and pool selectors also scope historical evidence: `--role` assesses only
verified configurations in that role (keeping its pools separate), and
`--worker-type` assesses only the selected provisioner/pool. A host's previous
role or pool does not create a cohort or limit the selected population's sample.
Explicit host selections retain history across roles and pools.

Filtering happens after attribution against the full configuration timeline;
it never bridges a transition or observation gap. Only verified configurations
can establish that work is outside the selection. Unknown configurations remain
unknown evidence in the coverage denominator. Known out-of-scope runs are omitted
from cohort health, comparisons, failure patterns, and attribution coverage, with
a compact history note in the default output and population/task details under
`--details` or `--verbose`. All retrieved outcomes still include this work, with
known out-of-scope outcomes shown separately from unattributed outcomes.

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
Queue depth appears immediately below the report title; fetch timestamps and lookup
errors are in `--details` and JSON. Zero pending tasks are
highlighted in yellow with a warning that no new work is queued; existing running
tasks may still produce results.

Master and each override SHA have separate summaries, with applied revision
distributions. Pairwise comparisons use their overlapping verified time range
inside the requested report window. Jobs are filtered to that same window on
both sides. The report shows rates and percentage-point differences, never an
automatic recommendation to advance. Multiple applied revisions, unknown outcomes,
unresolved task queries, or too few finished tasks qualify the comparison as
insufficient evidence. The minimum sample defaults to 20 finished tasks per side.
Attribution coverage below 80% produces a warning, without vetoing a comparison;
`--min-coverage` configures this warning threshold. Neither threshold is a
statistical significance test.

The rollout assessment separates the observed result, sample sufficiency, and
attribution coverage. The cohort table and sample notes describe independent health
for master and each override, using verified tasks over the full report window. Health is
"Healthy in verified sample" when at least `--min-finished` tasks finished with no
failed, exception, or unknown outcomes. Otherwise it reports failures, exceptions,
unknown outcomes, or an insufficient sample. Observed failures and exceptions are
reported even when the sample is small. Mixed applied revisions carry a warning.
Current verified host counts are displayed beside task counts; a cohort with no
current hosts can still have a historical sample. Attribution and collection gaps
remain explicit warnings outside these sample-specific health statements.

Each cohort is assessed independently, including an all-master or all-override
population without controls. No rollout direction is needed. Equal failure rates
can yield no observed regression in a comparison while both cohorts correctly
report failures in their own health statements.

With a comparable sample the human report describes whether either cohort has a
higher failure or exception rate, or whether the observed rates are equal. The JSON
`observed_result` retains the existing directional values: "no regression observed
in verified tasks", "higher override failure rate observed", or "higher override
exception rate observed". The first of those JSON values includes cases where master
has a higher rate; consumers needing direction should inspect both cohorts' rates
and signed differences. These are descriptive results, not causal findings or
statistical significance claims. When
sample or other comparison requirements fail, it reports "Comparison unavailable"
with the blockers. A missing master or override also makes comparison unavailable.
Low attribution remains explicit with the actual percentages and warning threshold,
using neutral text so it does not compete visually with observed failures.

Unattributed failures and exceptions whose start and finish both lie inside the
shared comparison window are highlighted alongside the result. Their host/task
links are available with `--details` or `--verbose`. Report-wide unattributed
outcomes are shown separately so failures before convergence or outside the shared
window are not presented as failures in the comparison. Excluded outcomes remain
unknown configuration evidence and do not enter either cohort's rates.

Partial host observations do not automatically veto the assessment. At least
80% of selected hosts must be successfully queried by default, configurable with
`--min-host-coverage`. Comparisons apply this threshold within their role/pool/group
scope. Missing host queries and unverified current configurations remain explicit
warnings; tasks enter comparisons only through verified historical intervals.
Thus six unavailable hosts out of 132 pass the host-query threshold (95.5%), while
task-attribution coverage is qualified independently. Current configuration warnings
do not invalidate otherwise verified historical work. Collection errors remain in
JSON and retain their nonzero exit status even when the available sample is comparable.
The assessment and comparison JSON include `warnings` and `host_query_coverage`
with queried/requested counts, the observed fraction, and the minimum fraction.
Human-readable blockers state the measured coverage and required threshold.

Comparison JSON adds `observed_result`, `sample_status`, `minimum_finished`,
`attribution`, and `unattributed_failures`. `attribution` contains `status`
("limited" or "meets threshold"), `fraction`, `warning_threshold`, and `warnings`;
it qualifies rather than determines the comparison `status`. Unavailable
comparisons have `observed_result: "comparison unavailable"` and
`sample_status: "unavailable"`. The summary's `evidence` includes sample and
attribution fields, and `summary.unattributed_outcomes` counts all retrieved
in-window runs excluded from configuration attribution. These additive fields
retain schema version 1. A `comparable` status may now carry low-attribution
warnings; consumers should inspect attribution separately from sample sufficiency.
Each `summary.cohorts` entry also includes `health` with `status`, `sample_status`,
`minimum_finished`, `basis: "verified tasks in report window"`, and `warnings`.

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

The additive `selection` object identifies the requested role or provisioner/pool
(null selectors for explicit hosts). For role/pool selections, each host's
`out_of_scope_history` retains omitted `intervals`, `jobs`, and any verified current
`configuration` outside the selection; its normal intervals/jobs/configuration are
used for the selected assessment. Configuration gaps still describe uncertainty in
the full timeline, not known out-of-scope intervals. `summary.out_of_scope_outcomes`
counts omitted in-window runs. Coverage `in_window` is the selected candidate count,
including unknown evidence; `out_of_scope` counts known omitted in-window runs.
`fetched` includes both, plus `outside_window` runs. `all_observed_outcomes` includes
all retrieved in-window runs, while `unattributed_outcomes` includes only unknown
configuration evidence. These additive fields retain schema version 1. Classified
failures outside the selection do not affect the selected report's exit code;
query errors remain errors.

`queue_counts` contains `scope: "whole_pool"`, `approximate: true`, `pools`,
`unresolved_hosts`, and `legacy_mapped_hosts`. Each pool has `worker_pool`,
`pending_tasks` (null when unavailable), `checked_at`, and `error`. The same
additive field is included in fleet-status JSON; schema version remains 1.

With `--details`, the human report also shows all retrieved in-window outcomes across configurations,
including unattributed runs, and counts the reasons those runs were excluded from
configuration attribution. These totals are not cohort results and do not establish
which configuration produced a run. They cover only retrieved runs: missing host
queries and the 20-run worker history limit can leave additional jobs out.

`configuration_history` exposes availability, schema version, retention cutoff,
and compact span count. Host intervals identify raw/compact sources, retained
maximum polling gaps, and retention cutoffs. `configuration_evidence` carries
pinned provenance and uncertainty reasons; `configuration_gaps` lists unknown
ranges inside the requested window. Detailed human output gives the cutoff and span count;
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
