# Tool integrations

This inventory records tools and agents that consume Fleetroll data or invoke
its commands. Use it when changing CLI behavior, JSON schemas, evidence semantics,
or storage. External integrations are described by function so their requirements
remain useful across tool names and implementations.

The initial survey covered repository scripts and agent instructions, plus local
checkouts of relops-infra, ronin_puppet, pool-classifier, and worker_rehab_tool.
It found the consumers below and a proposed pool-analysis integration; it is not
an exhaustive inventory of private scripts or operator sessions. Add a record
when another consumer is identified, including its owner and contact route.

## Support decisions and compatibility

| Decision | Meaning |
| --- | --- |
| Supported | A documented Fleetroll interface whose behavior must be considered when changing the producer. This does not guarantee an external tool's policy or availability. |
| Best-effort | A convenience workflow or repository-specific asset; check its callers when changing it, but it has no independently versioned compatibility contract. |
| Unsupported | An internal interface or proposed dependency that consumers must not assume is stable or available. |
| Deprecated | Still available with a documented migration and removal policy. No integration below currently has this decision. |

Prefer canonical grouped CLI commands. Existing flat aliases are retained as
described in [CLI command hierarchy](cli-command-hierarchy.md); this inventory
does not introduce a removal schedule. Human-readable tables and diagnostic text
are for people, not stable parsing interfaces. Use JSON or `--hostname-only`
where provided. Keep machine output on stdout separate from stderr diagnostics,
and check the command's exit status.

Monitor worker evidence currently uses `worker_evidence_schema_version: 3`;
freshness reports separately use `schema_version: 2`. These versions are not
interchangeable and are independent of the Fleetroll package version. Consumers
should explicitly accept the versions they implement, tolerate additive fields,
and stop automated actions on unsupported versions or invalid required evidence.

For incompatible schema or semantic changes, update the relevant contract and
schema version, review every affected entry here, and document the consumer
migration in release notes. Coordinate with the recorded contact before retiring
an interface or changing evidence meaning. No fixed compatibility window is
promised. Recheck working integrations when upgrading; a package upgrade alone
does not establish consumer compatibility.

## Shared application contract

Fleetroll owns collection and the meaning of its reported evidence. Consuming
applications own their decisions, thresholds, actions, and outcome tracking.
Applications may combine evidence from several producers, but must preserve each
producer's identity, source timestamp, and uncertainty rather than presenting a
join as a new observation.

The supported machine interfaces today are CLI output contracts:

| Contract | Version and authoritative definition | Required behavior at the boundary |
| --- | --- | --- |
| Host snapshot and worker evidence | Worker evidence v3; [monitor JSON](monitor-json.md) and [historical identity](historical-worker-identity.md) | Preserve host-keyed snapshot framing, full worker identity, source timestamps, current versus historical evidence, lookup outcomes, and pinned provenance. Follow output has different framing and is not a durable event stream. |
| Collection freshness | Freshness v2; [freshness reporting](../README.md#data-freshness) and [producer](../fleetroll/commands/data_freshness.py) | Report host and TC sources separately, required sources, coverage, overall status, and failures. Without `--require-fresh`, successful reporting does not imply fresh data. |
| Hostname selection | Unversioned documented `host monitor --once --hostname-only` CLI | Emit one FQDN per line; preserve documented filter semantics. Lists select candidates, not action permissions. |

For every consumer, record accepted schema versions, required fields and enum
values, identity/join keys, freshness policy, command exit handling, and owner.
Treat full provisioner/pool/group/worker-ID identity as distinct from a hostname
or short worker label. An exact historical baseline includes provenance, not
just equal identity strings. Missing, unknown, failed, stale, and contradicted
evidence are distinct states; a previous success must not hide a later failure.
Consumers must reject unknown values where a decision requires a known state.

Removing or renaming fields, changing types or timestamp meaning, changing
identity resolution, and reinterpreting status values are incompatible changes.
Adding a status that consumers must understand also requires migration review.
An additive optional field can remain within the existing version only when it
preserves existing behavior. Producer changes must update contract examples and
behavior tests; consumers need fixtures for accepted versions, missing evidence,
failed lookups, mismatched identities/provenance, and unsupported versions.

If a shared HTTP API is introduced for external applications, define its own
versioning, authentication, authorization, errors, and freshness behavior before
declaring it supported. Reuse the documented evidence semantics where applicable;
do not silently equate a local web route or database table with this external
contract. New application integrations belong in this inventory before other
applications rely on them.

## Automated worker recovery tool

**Owner:** Release Operations operators of the recovery workflow.
**Contact:** coordinate through the Fleetroll maintainer (`aerickson`) using a
[repository issue](https://github.com/mozilla-platform-ops/fleetroll_mvp/issues)
until a separate integration owner/contact is recorded.
**Decision:** supported CLI and versioned evidence contracts; external recovery
policy and actions remain the consuming tool's responsibility. Host-list paths
and named filter files are best-effort repository assets.

A recovery tool can identify silent Linux hardware workers, repair a clock over
SSH where appropriate, reset selected machines through an out-of-band management
interface, collect post-recovery diagnostics, and track repeated failures. Its
Fleetroll integration uses local CLI subprocesses and recorded observations;
it does not read SQLite or the audit log directly or depend on a central HTTP API.

| Interface | Use and required contract |
| --- | --- |
| `fleetroll data freshness HOSTS_FILE --json --require-fresh` | Validate freshness schema v2, required host and TC sources, per-source and overall status, failure list, and exit status. Fleet coverage is separate from individual recovery eligibility. |
| `fleetroll host monitor HOST_OR_FILE --once --json` | Read a host-keyed snapshot with matching `host` fields and worker evidence v3, including explicit missing evidence. Producing the snapshot does not refresh collection timestamps. |
| `--filter-file configs/filters/moonshots-hung.yaml` | Select candidates using the checked-in filter: host prefix, TC inactivity over four hours, data age over four hours, no note, and hostname ordering. Selection alone does not authorize recovery. |
| `fleetroll gather tc HOST_OR_FILE --use-historical-identity` | Query the exact retained provisioner, pool, group, and worker ID in one batch, pinning historical provenance and recording both successful observations and failed attempts. |
| `fleetroll host monitor HOST_OR_FILE --once --hostname-only --filter QUERY` | Obtain observed Linux fleet membership for reporting without parsing a table. |
| `fleetroll gather host HOST` | Collect a live host audit to append to a diagnostic report. Diagnostic text is not parsed as an automation contract. |
| `configs/host-lists/linux/all.list` and `all_moonshots.list` | Supply expected inventory, bound candidate membership, and provide the population for a fleet reset circuit breaker. These paths require a local repository checkout. |

The consumer currently requires the following evidence before automatic recovery:

1. A complete historical identity collected successfully from host configuration,
   with its original timestamp and provenance, `contradiction_status: none_known`,
   and no uncertainty reasons. A failed latest SSH audit may coexist with this
   baseline; it must not silently replace or refresh it.
2. A new successful historical-identity TC lookup during this recovery check.
   Both the latest attempt and retained successful observation must match all
   four identity fields and the exact baseline provenance. The attempt's
   `successful_observation_ts` must link to the retained observation timestamp.
3. Ordered, timezone-aware lookup and observation timestamps, a known
   `last_date_active`, and the raw worker record's task state. Missing workers,
   lookup failures, missing activity, provenance changes, or running/pending tasks
   block recovery rather than proving inactivity.

The current consumer policy requires at least 65% fresh host and TC coverage,
normally within a 15-minute loop interval. Per-host identity age must exceed
four hours and be at most seven days; activity must be over four hours old at
query start. Lookup evidence must be younger than the configured freshness limit
and under one hour. These are consumer defaults, not Fleetroll retention or
health guarantees. Fleetroll retains historical baselines without automatic expiry.

The tool refreshes and validates TC evidence before each automatic batch and again
after clock triage, immediately before requesting resets. Preview also refreshes
TC data. It owns confirmation, inventory checks, the default 20% fleet circuit
breaker, recency exclusions, cooldowns, reset execution, and recovery verification.
An evidence check is a snapshot, not a worker reservation or proof of recovery.

See [monitor JSON](monitor-json.md) and
[historical worker identity](historical-worker-identity.md) for producer semantics.
Changes to identity retention, contradiction handling, provenance equality,
lookup/observation linkage, activity timestamps, or raw task-state fields require
review of this consumer even if the outer JSON shape stays unchanged.

## Repository shell helpers and session launcher

**Owner/contact:** Fleetroll maintainer (`aerickson`), through repository issues.
**Decision:** best-effort wrappers over supported CLI commands.

`tools/gather-*.sh` collect host and TC observations;
`tools/monitor-*.sh` and `tools/start_fleetroll_tmux.py` launch operator views.
`tools/list_bad_linux_hosts.sh` and `tools/list_hosts_by_override_branch.sh`
consume filtered hostname-only output for candidate lists and rollout targeting.
Some helpers still invoke retained flat aliases. Check these callers during any
alias, filtering, host-list, exit-code, or output change. Their space-separated
output is a convenience format, not a separately versioned API. A candidate list
does not establish that a host is safe to reset or mutate.

## Operator and coding-agent workflows

**Owner/contact:** Release Operations and Fleetroll maintainer (`aerickson`),
through repository issues.
**Decision:** supported documented CLI behavior; best-effort prose workflows.

Repository `AGENTS.md` and sibling operational runbooks direct agents to inspect
recorded monitor JSON first, gather when fresh evidence is needed, preview
mutations, and verify the resulting observations. They depend on observation
timestamps, filtering, dry-run/confirmation behavior, exit statuses, and the
distinction between writing an override and Puppet successfully applying it.
Update current instructions and command examples when these interfaces change.
Historical incident notes may describe superseded commands and are not a current
contract. No agent should infer mutation authority from a JSON health flag alone.

## Pool analysis and rollout correlation (proposed)

**Owner/contact:** Pool Classifier and Fleetroll maintainers; coordinate via the
Fleetroll maintainer (`aerickson`) until a dedicated contact is recorded.
**Decision:** unsupported as a deployed integration; design intent only.

The sibling Pool Classifier design proposes joining physical-host state and
configuration provenance with Taskcluster workload outcomes and capacity signals.
It calls for stable host-to-worker identity, observation freshness, role and
configuration/cohort context, and links between products. The survey found a
proposal, not an implemented Fleetroll consumer in that checkout. Do not promise
all proposed fields, centralized persistence, or a shared authenticated read API
as part of today's contract. Agree on a versioned interface before implementation.

## Storage and internal interfaces

No external direct SQLite or audit-log reader was identified in this survey.
Direct access to `~/.fleetroll/fleetroll.db`, internal Python helpers, and raw
storage schemas is unsupported as an external compatibility contract. Repository
web/TUI code, benchmarks, and tests use internal helpers and must evolve with
them. Audit logging remains an operator record; its presence does not make its
raw schema a versioned integration API. Record any discovered external reader
here and decide its support and migration path explicitly.
