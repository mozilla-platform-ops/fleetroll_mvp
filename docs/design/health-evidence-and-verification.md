# Health evidence and verification

Design for `mvp-d0kx`. The vault gate in `mvp-p9w` is the first bounded change;
the component model below requires coherent collection (`mvp-257d`) followed by
the shared evaluator and presentation (`mvp-zjwr`). Proposed fields here are not
a shipped JSON contract.

## Problem and immediate decision

`PP_MATCH` compares an applied revision with a recorded expected revision and
checks Puppet success. `HEALTHY` additionally requires recorded Taskcluster
activity within one hour. It currently omits vault convergence. A successful
vault write can therefore leave a host showing `HEALTHY=Y` before Puppet uses it.
The name also overstates what revision and activity evidence establish.

Keep `PP_MATCH` and its filter semantics as revision comparison. Do not rename it
to `PP_HEALTH` or make it silently cover unrelated evidence. For Linux/macOS,
restrict existing positive `HEALTHY` results to equal, nonempty full deployed
`vault_sha256` and `puppet_vault_sha_applied` values. A mismatch changes `Y` to
`N`; missing/invalid evidence changes `Y` to `-`. Two absent checksums are unknown,
not equal evidence: current observations cannot distinguish a producer omitting
the applied field from explicitly reporting an absent vault. An explicit absent
vault with a contradictory deployed checksum is unknown as well. Windows keeps
its existing independent pool-hash comparison.

Full checksums mean 64 hexadecimal characters, compared case-insensitively.
Display prefixes, labels and mnemonics are not comparison inputs.

This patch only restricts existing positives. It does not reinterpret existing
`N`/unknown results, change freshness thresholds, add a new JSON health verdict,
or authorize automation. In particular, missing TC activity can still yield the
legacy `N`, and failed audits can still display historical successful rows.
Those limitations motivate the component model rather than being solved by the
vault comparison. Matching recorded checksums do not prove current live state.

## Component model

Use an independent, versioned `verification` object alongside existing worker
evidence. Start at `verification_schema_version=1`; do not change worker evidence
v3 or assume their versions move together. Build it once in a pure evaluator,
with an explicit evaluation time and policy, shared by JSON, query rows, TUI,
and web. Do not reverse-engineer formatted table strings.

| Component | States | Evidence and limits |
| --- | --- | --- |
| Puppet run | `succeeded`, `failed`, `unknown` | Validated, supported metadata for one completed run; wrapper exit is separate. Preserve run timestamp/identity, detailed exit, and provenance. |
| Revision | `match`, `mismatch`, `unknown` | Full applied revision versus the recorded expected reference, retaining repository, branch, fetch time, and source. |
| Override / vault | `match`, `mismatch`, `unknown`, `not_applicable` | Deployed versus same-run applied full checksums and explicit presence states; no equality from two nulls. Applicability requires a platform/role contract, never missing evidence. |
| TC lookup | `ok`, `missing_worker`, `failed`, `unknown` | Latest completed attempt, its exact identity, timestamp, and linkage to any retained successful observation. |
| TC activity | `recent`, `not_recent`, `unknown` | Recorded last-active time under an explicit window. Inactivity does not establish a broken worker, idleness, or a workload regression. |
| Workload | `acceptable`, `regressed`, `unknown` | Coverage-qualified Pool Classifier/cohort evidence under `mvp-6eri`; activity or a running process cannot substitute. |
| Freshness, per source | `fresh`, `stale`, `unknown` | Original timestamps and explicit policy for host collection, expected references, TC lookup/observation, and workload evidence. |

Each component carries `state`, machine-readable `reasons`, source observation
time, collection/attempt status, freshness, and source/run/identity provenance
where applicable. Preserve raw timestamps and values; render human ages separately.
Expected-reference age is different from the age of the applied Puppet run.
An old successful run can still be the latest applied configuration when a fresh
host observation confirms its files. Do not use Puppet run age as host freshness.

The derived signal is named **configuration verification**, not overall health.
Its states are `verified`, `not_verified`, and `unknown`. It covers a successful
Puppet run, matching revision and applicable file evidence, qualified by current
collection success and freshness. TC activity and workload remain separate.

- `verified`: every applicable requirement passes with usable evidence.
- `not_verified`: at least one usable component establishes failure/mismatch.
- `unknown`: a required component is unavailable, stale, unsupported,
  contradictory, or unlinked, with no usable failing component.

Within one component, contradictory evidence becomes unknown and retains the
contradiction reason; it must not pick whichever value permits verification.
Across components, a known failure remains visible even when another is unknown.
The aggregate exposes both failing and unknown requirements. Freshness never
rewrites a stored mismatch into a new current mismatch: retain its historical
state separately and mark its present eligibility unknown.

## Collection, coherence, and partial failures

Validate metadata schema version, types, timestamps, success/exit consistency,
and explicit field presence before deriving evidence. Preserve the difference
between absent, unreadable, malformed, unsupported, and not collected. Current
`process_audit_result` fills many unavailable applied fields with null; richer
collection status must precede interpreting null as an absent file.

Record host collection attempt ID/start/end/status, per-component collection
status, and run identity separately. Read deployed files and metadata as one
bounded snapshot, detect changes during capture, and return unknown for affected
comparisons on races. Store the Puppet producer's run ID when available. Metadata
v1 has none: mark legacy run provenance explicitly; temporal bounds cannot prove
which concurrent invocation wrote it. Do not assemble an apparently coherent run
from a new timestamp, old checksums, and fallback success fields.

A failed host gather records the failed attempt without refreshing the retained
successful snapshot. A partial gather retains useful component evidence with its
own time and status, without stamping unavailable components as newly observed.
The same rule applies to TC: latest attempt and retained success stay separate,
with exact identity and successful-observation linkage. Never borrow an old pool
registration after a role/identity change as if it were a current lookup.

Evaluate all source ages against one UTC evaluation time. Begin with the existing
one-hour source-staleness default (`STALE_DATA_THRESHOLD_SECONDS`) and one-hour
activity window, label thresholds explicitly, and allow policy overrides through
the versioned contract. DATA coloring turns red at 30 minutes; that display
threshold is not the source-staleness policy. Invalid,
timezone-less, or future timestamps are unknown; do not clamp them to age zero.
Specify inclusive/exclusive boundaries in implementation and tests.

Inventory membership, selected population, unobserved hosts, and denominators
remain under `mvp-xs2c`. Per-host verification cannot prove fleet completeness.

## Automation prerequisites

`HEALTHY`, `PP_MATCH`, configuration verification, and recent activity do not
authorize a reboot, quarantine change, rollout advance, or override removal.
Consumers must require their operation-specific evidence and explicit authority.

Before disruptive action, validate supported producer versions, all required
components, source ages, latest collection attempts, full worker identity and
provenance, and absence of unresolved contradictions. Unknown blocks the action.
Refresh and revalidate immediately before acting, preserve the reviewed target
scope, and audit the evidence/policy and decision. Fleetwide freshness percentages
cannot substitute for per-host eligibility.

Safe reboot additionally requires authoritative current-boot/current-worker task
evidence, effective quarantine, and a fresh post-quarantine idle check; a recorded
configuration match is insufficient (`mvp-fw8r`). Distinguish reboot requested
from verified recovery. Rollout advancement needs attributable cohort samples,
coverage and workload policy (`mvp-osyq`, `mvp-6eri`), plus its approval rules.

## Migration and delivery

1. **Vault correction (`mvp-p9w`):** narrow Linux/macOS `HEALTHY=Y` as above;
   preserve `PP_MATCH`, column names and JSON raw fields. Update TUI help, web
   tooltip and operator docs. `healthy=n` still excludes unknown; `healthy!=y`
   includes negative and unknown rows. Neither selects authorized recovery targets.
2. **Coherent collection:** add typed presence/collection/run provenance without
   changing historical timestamps. Retain compatibility for old observations,
   treating missing new evidence as unknown. This enables valid absent/absent and
   platform applicability decisions later.
3. **Shared component evaluator and presentation:** publish the versioned contract,
   expose separate convergence/activity/freshness/reasons in JSON and query fields,
   and add compact TUI/web representations. Preserve Windows semantics until it
   has a separately tested platform contract. Keep legacy `HEALTHY` as a labeled
   compatibility signal during migration; do not silently redefine it again.
4. **Consumer adoption:** inventory saved queries, scripts and external consumers;
   migrate decisions to explicit required components and operation policy before
   considering legacy-signal removal. Coordinate recovery consumer changes with
   its owner through [tool integrations](../reference/tool-integrations.md).

The repository's `unhealthy-stale.yaml` uses `healthy=n`; its description must say
it excludes unknown. The surveyed `worker_rehab_tool` checkout has no direct
`HEALTHY`/`pp_match` consumer; its documented worker-evidence and freshness contracts
remain authoritative. Recheck other consumers at implementation time.

## Validation

For the immediate gate, cover equal/different full hashes (including shared
prefixes), old observations with missing applied fields, null/empty/malformed
values, absent/contradictory presence, failed Puppet runs, unavailable expected
revisions, no-override and override hosts, Linux/macOS, and Windows exclusion.
Verify shared query/TUI/web behavior, `healthy=n` versus `healthy!=y`, retained
historical timestamps after a failed gather, and unchanged raw monitor JSON.

For the component model, test missing/stale/future/boundary timestamps, coherent
and raced file/run captures, unsupported producer versions, partial collection,
new failure after retained success, mismatched worker identities, unlinked lookup
attempts, unavailable workload coverage, and mixed known-failure/unknown states.
Consumer tests must reject every missing prerequisite, quarantine expiry/failure,
and evidence changes between selection and action; replay retained snapshots to
verify migration parity across CLI/TUI/web and JSON contracts.
