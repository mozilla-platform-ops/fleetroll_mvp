# Rollout report

The report compares master with each override within a role, using retained host
observations and audit events. It reads existing evidence; it does not gather,
deploy, remove overrides, advance a rollout, or maintain a new history store.

## Evidence and attribution

SQLite normally keeps only ten observations per host. The operation audit log
does not contain full applied-configuration snapshots, so it cannot recover
pruned convergence evidence by itself. Save gather-host or host-monitor JSON
when beginning a rollout and supply it with repeatable `--observations-file`
options to supplement retained history. Single observation objects, arrays,
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

JSON has `schema_version`, `window`, `hosts`, `summary`, `comparisons`, `errors`,
and `warnings`. Host results retain jobs and per-job exclusion reasons. Summary
coverage reports fetched, in-window, classified, excluded, and unresolved task
counts, requested/queried hosts, and observation freshness. Query failures have
unknown task counts; they are never zero-job successes. Missing comparison
cohorts still produce useful standalone summaries.

Exit codes: 0 = a report with no classified failures or query errors; 1 = a
classified failed/exception/unknown outcome or query error; 2 = invalid input or
unavailable local evidence. Insufficient evidence is explicitly reported even
when exit 0; exit 0 is not rollout approval.

## Usage

Run from the repository root after refreshing the relevant hosts:

```bash
uv run python scripts/check_rollout_jobs.py --role gecko_t_linux_2404_talos
uv run python scripts/check_rollout_jobs.py --worker-type gecko-t-linux-talos-2404 --json
uv run python scripts/check_rollout_jobs.py t-linux64-ms-016 t-linux64-ms-017
uv run python scripts/check_rollout_jobs.py --role gecko_t_linux_2404_talos \
  --since 2026-11-03T23:33:16Z --min-finished 10
```

The default window is the preceding 24 hours. Use `--until` for a fixed end.
The one-hour default gap bound can deliberately exclude jobs between snapshots
taken hours apart. `--max-observation-gap` controls that policy in seconds; a
larger value accepts longer inferred continuity between matching observations
and never fills transitions or contradicting evidence. It does not recover
missing snapshots. For saved evidence, for example:

```bash
uv run python scripts/check_rollout_jobs.py --role gecko_t_linux_2404_talos \
  --observations-file rollout-start.json --observations-file rollout-followup.json
```

The old `scripts/check_override_jobs.py` entry point has been removed; update
existing invocations to `scripts/check_rollout_jobs.py`. No separate post-removal
mode is needed. Per-host JSON
now includes master and unverified hosts as well as overrides. Consumers of the
old deployment-only JSON should migrate to the versioned summary schema.
