# Verify a rollout

Check three separate facts: the intended files reached the host, Puppet applied
the intended configuration successfully, and the worker is processing work.
A successful override write establishes only the first.

## Refresh and inspect

After deploying or removing an override, refresh host, Taskcluster, and GitHub
evidence for the affected population:

```bash
uv run fleetroll gather all configs/host-lists/linux/all.list
uv run fleetroll host monitor configs/host-lists/linux/all.list --once \
  --filter-file configs/filters/linux-2404.yaml
uv run fleetroll rollout report --role gecko_t_linux_2404_talos
```

Puppet execution follows the [worker lifecycle](puppet-execution-mechanisms.md),
not a universal 30-minute timer. Refresh again after Puppet runs. Compare the
observation timestamps with the change before relying on recorded state.

## Read the monitor

| Column | Meaning |
| --- | --- |
| `DATA` | Host-audit age / Taskcluster observation age |
| `PP_LAST` | Time from the recorded host audit to the last Puppet run; `FAIL` marks a reported failure |
| `PP_EXP` | Expected revision from the recorded GitHub branch reference (override branch or master) |
| `PP_SHA` | Revision reported by Puppet |
| `PP_MATCH` | `Y` when the full expected/applied revisions match and Puppet succeeded; `N` for mismatch/failure; `-` when comparison data is unavailable |
| `HEALTHY` | Legacy revision/activity signal, with an additional Linux/macOS vault gate: `Y` requires `PP_MATCH=Y`, recorded TC activity less than one hour old, and matching full deployed/applied vault checksums |

These checks apply to Linux/macOS hosts with or without overrides. Private
branches need local GitHub read access for `gather gh`; missing expected revisions
make `PP_EXP`, `PP_MATCH`, and `HEALTHY` unavailable. Windows uses a separate
[pool-hash comparison](windows-support.md).

On Linux/macOS, an otherwise positive result becomes `N` when the full vault
checksums differ, or `-` when either checksum is missing, malformed, or abbreviated.
The comparison is case-insensitive over 64 hexadecimal characters. Two missing
checksums do not prove that Puppet applied an absent vault. An explicit absent
vault flag also prevents a positive result. This gate restricts existing `Y`
results; existing revision/activity `N` and unavailable results keep their meaning.
`PP_MATCH` remains revision-only. Windows has no vault gate.

Older observations lacking `puppet_vault_sha_applied` now produce `-` instead of
`Y` when the other checks pass. Gather again after Puppet runs with a metadata
writer. The raw JSON fields and worker evidence schema v3 are unchanged. The
CLI table, TUI, web rows, and shared filters use the same gate. `healthy=n` excludes
unknown; use `healthy!=y` to review both negative and unknown rows, or `healthy=`
for unknown only. These filters select observations, not authorized actions.

`HEALTHY` remains a compact recorded revision/vault/activity signal. It does not
check override convergence, qualify historical rows as current, prove that the
latest TC lookup succeeded, enforce freshness, or assess task outcomes. Missing
TC activity can still produce the legacy `N`; that is not proof of worker failure.
The [health evidence design](design/health-evidence-and-verification.md) separates
those components and plans their migration; the new component model is not yet
implemented.
For automation, use [monitor JSON](reference/monitor-json.md) to check source freshness,
lookup status, full identity, provenance, and override application explicitly.

## Confirm application and outcomes

[Puppet metadata](reference/puppet-state-tracking.md) records the applied revision and
override/vault checksums. Override convergence requires matching deployed/applied
override checksums and a successful run; removal requires the reported override
to be absent. A matching Git revision alone does not prove file convergence.

The [rollout report](rollout-report.md) requires stronger configuration evidence
and attributes tasks only to verified intervals. It assesses master and overrides
independently and compares them when the samples permit. Inspect exclusions,
coverage, and task failures alongside the result; neither `HEALTHY=Y` nor report
exit 0 authorizes advancing a rollout.
