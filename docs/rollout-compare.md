# Compare rollout pools

`fleetroll rollout compare` compares a candidate pool with an explicit baseline:

```bash
uv run fleetroll rollout compare \
  --candidate-pool releng-hardware/gecko-t-linux-talos-2404-tc-v115 \
  --baseline-pool releng-hardware/gecko-t-linux-talos-2404
```

Bare pool names default to `releng-hardware`. The two pools must differ.
One candidate worker and many candidate workers use the same interface.
Use `--details` for per-worker and task-mix details, `--verbose` for underlying
report evidence and individual runs, or `--json` for machine-readable output.
Interactive terminals show phase labels, a spinner, elapsed time, and worker/task
completion counts on stderr. Candidate and baseline checks are labelled separately;
task-name lookups and workload comparison have their own phases. Progress is
silent in JSON mode, redirected stderr, and `TERM=dumb` terminals.
After at least one second and one completion, counted phases also show average
completions per second and an approximate remaining time. Task-name rates count
completed network lookups (including errors), excluding cached/reused names;
worker rates count processed hosts. Estimates reset for each phase and disappear
when no work remains. They are not counts of underlying HTTP attempts or retries.

Text output starts with the matched comparison result, every unavailability
reason, matched finished counts, attribution, and successfully queried workers.
Observed failures/exceptions/unknowns are highlighted even outside the matched
sample. Rate differences appear in text only when the comparison is available;
JSON retains the full computed values and status. Default outcome rows omit zero
counts and repeated success percentages; `--details` retains the full breakdown.
Repeated worker errors are grouped per side with up to three example hosts.
`--details` lists every worker with concise errors; `--verbose` and `--json`
preserve full SDK diagnostics. Other query errors remain individually visible.
Known methodological caveats and truncated task windows appear as dim `Note:`
lines. Compact configuration history, saved-observation provenance, and repeated
exclusion/name-matching explanations appear only with `--verbose`; `--details`
keeps the concise notices. Attribution warnings, missing evidence, cache/query
warnings, and errors remain visible by default. JSON retains every original
warning, and these presentation choices do not change assessments or exit codes.

Interactive text uses cyan headings, amber unavailable comparisons/blockers and
unknown outcomes, and red errors/nonzero failures or exceptions. A higher
candidate failure/exception rate uses a red verdict; equal or lower observed
rates use neutral cyan rather than a green approval signal. Routine notes are
dimmed; zero outcomes remain uncolored. Color supplements the text labels and is
disabled for redirected stdout, `NO_COLOR` (including an empty value), and
`TERM=dumb`. JSON never includes terminal styling. Styling follows wrapping so
colored and plain reports retain the same layout.

Task names are cached separately from host evidence in a SQLite sidecar beside
the selected `--db` (normally `~/.fleetroll/fleetroll.task-names.sqlite3`). The
cache stores only task ID, service root URL, name, and fetch timestamp. Entries
expire after 30 days and retention is capped at 50,000 entries across service
roots. Failed lookups are retried on the next comparison. Cache failures warn
and fall back to live lookups; they do not discard comparison results. Use
`--no-task-name-cache` to bypass cache reads and writes. Names already fetched
for failed/exception tasks in the current report are reused.

Task-name requests use `--metadata-workers` (1–20, default 12), independently of
`--workers` for host checks. Progress shows the completed/total network lookups,
cached and reused names, and lookup errors. Worker progress distinguishes
processed workers from successful queries. JSON adds `task_name_lookups` with
`cached`, `reused`, `fetched`, `failed`, and cache `warnings`. Worker populations,
task outcomes, and queue depths still refresh live on every invocation.

## Population and evidence

The population is hosts identifiable in Fleetroll's recorded observations, using
the same membership rules as `rollout report --worker-type`. This is not an
enumeration of every worker registered with Taskcluster. Refresh host observations
and GitHub references before comparing. Both reports use one fixed `--since` /
`--until` window (default preceding 24 hours), the same sample/coverage controls,
and live Taskcluster worker/task queries. Historical `--until` also uses the live
recent-task list, so earlier runs may have fallen out of retrieval.

Selection uses the last recorded resolvable identity. Failed or stale latest
observations remain visible as query failures. Selected host populations must be
disjoint; overlap is rejected. Only historical configurations in the selected
pool enter that side's attribution. Pool moves cannot bridge configuration
transitions. All existing configuration verification rules remain in force;
partial identity evidence work is tracked separately in mvp-nvpj.

Every side shows observed, attributed and unattributed outcomes, queried/selected
worker counts, configuration coverage, cohorts/revisions, and task mix. Individual
workers remain available in details/JSON, including query failures. Observed
outcomes retain failures even when configuration cannot be attributed. At most
20 recent task runs are queried per host; this is a recent sample, not complete
history or a throughput measurement. Queue depths retain whole-pool scope and
their check timestamps in the underlying reports.

## Comparison meaning

The comparison first matches configuration-attributed tasks with the same exact
Taskcluster metadata name and Puppet role on both sides. For unmatched groups,
it tries removing one literal `Replay: ` prefix from either side. The fallback
requires a unique pair with the same role; exact matches take precedence, and
tasks are never reused across pairs. Names are fetched for
successful tasks as well as failures; name-query failures preserve outcome and
configuration evidence but make the comparison unavailable. Tasks with unique
names and verified tasks without names do not enter matched rates. Raw and
attributed totals remain visible separately.

Original names remain in task evidence and each task-mix entry. Fallback use is
reported in text and `task_matching`, and each pair records its source names and
`match_method` (`exact` or `replay_prefix`). Use `--exact-task-names` to disable
the automatic fallback. The mutually exclusive `--strip-task-name-prefix PREFIX`
option explicitly normalizes a different literal prefix once on both sides
before matching, replacing the automatic policy. Stripping a label does not
establish identical task inputs.

The default minimum is 20 finished tasks per side across shared names; each
name also has its own sample status and rates in JSON. Low attribution warns.
Insufficient matched samples, unavailable task queries, inadequate host-query coverage
or unknown outcomes make comparison unavailable. Running/pending tasks are not
passes. Signed differences are candidate minus baseline in percentage points.
Equal task names do not imply identical inputs, builds, runtime versions or task
parameters. Aggregate matched rates retain each side's observed workload weights;
per-name rates and task counts expose workload imbalance. Rates describe observed
samples, without significance tests or claims about causation, performance, or
approval to expand a rollout.

## JSON and exit status

Comparison JSON has its own `schema_version: 1`, independent of report JSON.
It contains `window`, `population_source`, `sides`, `comparison`, `errors`,
`warnings`, `task_matching`, and `reports` (the two full versioned rollout reports).
Each side contains pool identity, worker counts, observed/attributed/unattributed
outcome summaries, configuration coverage, cohorts, task-name coverage, task mix,
unmatched verified outcomes and per-worker results. `comparison` contains both
matched summaries, per-role/name comparisons, signed rate differences, status,
observed result and unavailability reasons. Evidence details and source timestamps
remain in `reports`; consumers should accept additive fields.

Exit 0 means no observed failures/exceptions/unknown outcomes or query errors;
an insufficient sample can still exit 0. Exit 1 reports such outcomes or query
errors, including unattributed failures. Exit 2 means invalid input, absent
recorded membership, overlapping populations, or unavailable local evidence.
No exit status authorizes host changes.
