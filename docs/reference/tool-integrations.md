# Tool integrations

Use this inventory when changing Fleetroll's CLI, JSON, evidence semantics, or
storage. The initial survey covered repository scripts and agent instructions,
plus local relops-infra, ronin_puppet, pool-classifier, and worker_rehab_tool
checkouts. Add newly discovered consumers and their owners; private scripts and
operator sessions may have other dependencies.

## Interfaces and compatibility

| Support | Meaning |
| --- | --- |
| Supported | Documented interface; changes require compatibility review. External policy and availability remain the consumer's responsibility. |
| Best-effort | Convenience workflow or repository asset; check callers, but no independent versioned contract. |
| Unsupported | Internal interface or proposal with no stability or availability guarantee. |

No listed integration is deprecated. Prefer grouped CLI commands; retained flat
aliases follow the [CLI hierarchy](cli-command-hierarchy.md), with no new removal
schedule here. Parse JSON or `--hostname-only`, separate stdout from stderr, and
check exit status. Tables and diagnostic text are not stable parsing interfaces.

| Machine interface | Contract | Preserve |
| --- | --- | --- |
| Explicit pool comparison | Comparison v1: [rollout compare](../rollout-compare.md) | Candidate/baseline identity, shared window, observed versus attributed outcomes, exact role/name matching, query failures, source times and per-worker evidence. |
| Live worker diagnosis | Diagnosis v1: [host diagnose JSON](host-diagnose.md) | Collection success separate from health, valid exit 1 reports, full structured checks, upstream schema status and source times. Legacy evidence does not qualify for automatic zombie recovery. Medic integration is pending. |
| Host snapshot and worker evidence | Worker evidence v3: [monitor JSON](monitor-json.md), [historical identity](historical-worker-identity.md) | Host-keyed snapshots, full identity, source timestamps, current/historical distinction, lookup outcomes, pinned provenance. Follow mode uses different framing and is not a durable event stream. |
| Collection freshness | Freshness v2: [usage](../../README.md#data-freshness), [producer](../../fleetroll/commands/data_freshness.py) | Separate host/TC sources, required sources, coverage, overall status, failures. Without `--require-fresh`, successful reporting does not imply fresh data. |
| Hostname selection | Unversioned `host monitor --once --hostname-only` | One FQDN per line and documented filter semantics. Candidate selection does not authorize actions. |
| Filtered collection | Unversioned `gather all/host/tc --filter QUERY` or `--filter-file FILE.yaml`: [selectors](../monitor-filter-sort.md#gather-matching-hosts) | Database default versus explicit scope, frozen combined targets, recorded-state fallback, stderr counts, and no-match success without collection. |

Schema versions are independent of each other and the package version. Consumers
must explicitly accept versions they implement, tolerate additive fields, and stop
automated actions on unsupported versions or invalid required evidence. Record
required fields/enums, join keys, freshness policy, exit handling, and owner.

Fleetroll owns evidence collection and meaning; consumers own decisions, actions,
and outcome tracking. Joins must preserve producer identity, source timestamps,
and uncertainty. Full provisioner/pool/group/worker-ID identity differs from a
hostname; historical baseline equality also requires provenance. Keep missing,
unknown, failed, stale, and contradicted states distinct. Previous success cannot
hide a later failure; reject unknown values when a decision needs a known state.

Field removal/renaming, type or timestamp changes, identity-resolution changes,
and reinterpreted or newly required statuses need migration review. For incompatible
changes, update the contract and schema version, review affected consumers with
their owners, and document migration in release notes. Optional additions may keep
the version only if behavior is preserved. No fixed compatibility window is
promised; recheck integrations on upgrade.

Update producer examples and behavior tests. Consumer fixtures should cover
accepted/unsupported versions, missing evidence, lookup failures, and identity or
provenance mismatches. A future shared HTTP API needs explicit versioning,
authentication, authorization, errors, and freshness semantics before support;
local web routes and database tables do not provide that contract.

## Automated worker recovery

**Owner:** Release Operations recovery operators. **Support:** supported CLI and
versioned evidence; best-effort host lists and named filters.

The recovery tool uses local CLI subprocesses and recorded observations to find
silent Linux workers, triage clocks, request out-of-band resets, collect
diagnostics, and track repeat failures. It does not read SQLite/audit logs directly
or depend on a central HTTP API.

| Interface | Dependency |
| --- | --- |
| `fleetroll data freshness HOSTS_FILE --json --require-fresh` | Freshness v2, required host/TC sources, per-source and overall status, failures, exit status. Fleet coverage differs from per-host eligibility. |
| `fleetroll host monitor HOST_OR_FILE --once --json` | Host-keyed snapshot, matching `host` fields, worker evidence v3, explicit missing evidence, unchanged source timestamps. |
| `--filter-file configs/filters/moonshots-hung.yaml` | Host prefix, TC inactivity/data age over four hours, no note, hostname ordering. Selects candidates only. |
| `fleetroll gather tc HOST_OR_FILE --use-historical-identity` | Batch queries of exact retained identities, pinned provenance, successful observations and failed attempts. |
| `fleetroll host monitor HOST_OR_FILE --once --hostname-only --filter QUERY` | Observed Linux fleet membership for reporting. |
| `fleetroll gather host HOST` | Live audit for diagnostics; diagnostic text is not parsed. |
| `configs/host-lists/linux/all.list` | Expected inventory, candidate membership, fleet circuit-breaker denominator; requires a local checkout. |

Automatic recovery currently requires:

1. A complete host-collected historical identity with original timestamp and
   provenance, `contradiction_status: none_known`, and no uncertainty reasons.
   A later SSH failure may coexist without replacing or refreshing this baseline.
2. A successful historical TC lookup during this check. Both latest attempt and
   retained success must match all four identity fields and exact provenance, with
   `latest_lookup.successful_observation_ts == taskcluster.observation_ts`.
3. Ordered, timezone-aware lookup/observation timestamps, known `last_date_active`,
   and raw task state. Missing workers/activity, failed lookups, changed provenance,
   or running/pending tasks block recovery.

Consumer defaults are at least 65% fresh host/TC coverage, normally within a
15-minute loop; per-host identity age over four hours and at most seven days;
activity over four hours old at query start; and lookup evidence younger than both
the configured freshness limit and one hour. These are consumer policy, not
Fleetroll guarantees. Historical baselines have no automatic expiry.

The tool refreshes/validates TC evidence before each automatic batch and again
following clock triage immediately before resets; previews also refresh TC data.
It owns confirmation, inventory checks, the default 20% fleet circuit breaker,
recency exclusions, cooldowns, resets, and recovery verification. Evidence is a
snapshot, not a worker reservation or proof of recovery.

Review this consumer for changes to retention, contradictions, provenance equality,
lookup/observation linkage, activity timestamps, or raw task-state fields, even
when JSON shape is unchanged. Producer details remain in the contracts above.

## Other consumers

**Repository helpers and session launcher** — Fleetroll-maintained, best-effort
wrappers over supported CLI. `tools/gather-*.sh` collect observations;
`tools/monitor-*.sh` and `tools/start_fleetroll_tmux.py` launch views.
The tmux launcher uses `gather all --filter 'override=present'` scoped to the
Linux baseline for frequent collection, alongside whole-baseline collection.
`tools/list_bad_linux_hosts.sh` and `tools/list_hosts_by_override_branch.sh`
consume filtered hostname-only output. Check callers for alias, filter, host-list,
exit-code, or output changes; some still use flat aliases. Their space-separated
output has no separate API contract.

**Operator and coding-agent workflows** — Owned by Release Operations and the
Fleetroll maintainer; supported CLI, best-effort prose workflows. `AGENTS.md` and
sibling runbooks depend on recorded JSON, timestamps, filtering, refreshes,
mutation previews/confirmation, exit status, and verification that distinguishes
override writes from Puppet application. Update current instructions/examples
when these change. Historical incident notes may use superseded commands; health
flags do not grant mutation authority.

**Pool analysis and rollout correlation (proposed)** — Owned by Pool Classifier
and Fleetroll maintainers; unsupported proposal, with no implemented consumer
found in the surveyed checkout. Would join physical-host configuration/provenance
with TC outcomes and capacity using host-to-worker identity, freshness,
role/cohort context, and product links. Agree on a versioned interface before
implementation; proposed fields, centralized storage, and an authenticated read
API are not current promises.

Add new integrations before other applications rely on them.

## Storage and internals

No external SQLite or audit-log reader was identified. Direct database access,
internal Python helpers, and raw storage/audit schemas are unsupported external
interfaces. Repository web/TUI code, benchmarks, and tests must evolve with them.
Record discovered external readers here and decide support and migration explicitly.
