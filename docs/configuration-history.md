# Compact host configuration history

Follow-up to the rollout report's conservative attribution model. Preserve
enough configuration evidence for normal reports after the rolling ten full
observations have been pruned. Do not increase the shared DB retention limit or
create a Taskcluster task-history store.

## Policy

- Retain compact configuration history for 30 days, using UTC wall-clock age.
- Preserve the existing latest-ten full-observation policy and last successful
  observation exception. Other SQLite table policies remain unchanged.
- A stable, continuously observed configuration extends its last-observed
  endpoint instead of appending another full snapshot every polling cycle.
- Changes to role, applied repository/revision, configured/applied override or
  vault hashes, dirty/success state, or effective worker identity are material.
  Uptime, polling time, durations, and ordinary repeated Puppet runs are not
  configuration changes. Preserve initial activation separately from the latest
  successful run timestamp.

## Evidence contract

Preserve first/last observation bounds, successful application bounds where
established, configuration identity, verification state, observation continuity
information, and reasons for uncertainty. Keep only allowlisted metadata and
hashes: no vault/override contents, raw command output, or credentials.

An override write/removal or vault write records changed intent and breaks
continuity; it never establishes Puppet convergence. Persist observation-derived
evidence atomically with observation insertion before pruning. Integrate relevant
operation events without changing the success/failure audit semantics. Failed
operations that may have partially changed a host must not imply unchanged state.

Failed observations, unverified/mismatching Puppet state, worker identity changes,
conflicting evidence, and excessive observation gaps create explicit boundaries.
Do not merge matching configurations across a known unknown interval, even when
their state hashes are identical on either side. Never extend coverage beyond a
successful observation or infer unseen manual actions. Changes that are made and
reverted entirely between observations remain fundamentally undetectable.

The compact representation must retain the continuity bounds needed to honor the
rollout report's configurable maximum observation gap. If a stricter query cannot
be supported from compact evidence, classify the affected span as unknown rather
than silently relaxing the requested bound. The schema may use bounded evidence
anchors, interval continuity metadata, or an equivalent representation; document
the tradeoff and test equivalence to raw history for supported queries.

Retain sufficient override descriptors and master/revision provenance for the
reader to work after referenced cache files or GitHub snapshots expire. Do not
relabel an old unknown revision as master merely because its override is absent.
Provenance must identify the source and observation/reference timestamps.

## Storage lifecycle

Use an additive, versioned, idempotent SQLite migration. Repeated observations
and replayed audit events are idempotent. Concurrent collection must not lose a
transition. Late or out-of-order contradictory evidence must conservatively
invalidate/split the affected coverage; it must not extend an interval through
uncertainty. Future source timestamps must not drive retention pruning.

Expire intervals whose last evidence predates the 30-day cutoff. Retain a bounded
anchor for an interval crossing that cutoff, and clip exposed report coverage to
the retained horizon. Preserve gaps at the boundary. Integrate cleanup with
existing maintenance and remove unreferenced configuration descriptors. Report
logical retention separately from physical database size; normal SQLite page
reuse and vacuum behavior still apply.

Bootstrap from existing retained observations and audit evidence where possible.
Missing historical state stays missing; migration cannot recover previously
pruned observations. Saved observation files remain a supported supplement.

## Report integration and validation

Read compact history in the rollout report, deduplicating overlap with full
observations and saved snapshots. Surface history coverage, retention cutoff,
unknown intervals, and provenance in human and JSON output. Collection owns
persistence; report execution remains read-only.

Tests must cover stable scans, ordinary Puppet reruns, manual changes observed
later, deployment/removal before convergence, failed collection, mismatches,
role/revision/identity changes, gaps, stricter query bounds, duplicate/conflicting
timestamps, late events, retention clipping, cache/ref expiry, migration/replay,
concurrent writers, and compact-versus-raw attribution equivalence.

Measure sustained stable polling and state-change/outage workloads for roughly
930 hosts. Report rows and bytes, indexes, write/maintenance cost, and query
latency. Stable polling should update bounded rows rather than grow with scan
count; storage should follow real transitions and uncertainty segments. The
earlier tens-of-MB estimate is a hypothesis, not an acceptance budget or promise.

## Implementation

Schema version 1 adds `configuration_history_meta`, `configuration_states`,
`configuration_spans`, and `configuration_boundaries`. Initialization takes the
SQLite writer lock before bootstrap. It imports the observations still available
in SQLite and legacy audit JSONL in timestamp order, including write attempts.
It cannot recreate missing observations. Subsequent initialization replays write
events idempotently to recover a boundary whose secondary persistence failed.
Collection can specify its custom audit path for replay.

Observation insertion uses a savepoint around both the compact update and raw
snapshot insertion/pruning. Existing transaction ownership remains with the
caller. Concurrent writers serialize through SQLite; an observation replay is
idempotent. Conflicting endpoint evidence or a late write inside a compacted span
invalidates that span and records a barrier. This deliberately discards coverage
whose interior anchors can no longer be reconstructed. An independently observed
configuration after the barrier can establish new coverage.

Descriptors hold allowlisted state and pinned configuration proof, including
override checksum/branch/repository and master-reference provenance. Repository
URLs are normalized to exclude embedded authentication, and worker identity keeps
only its four identity fields. No override/vault content, output, or credentials
are copied. Pinned descriptors can also verify overlapping raw observations of
the same state after caches expire; this does not grant observation continuity.
The first successful application remains the activation anchor; ordinary Puppet
reruns update only the latest application bound.

Matching states within one hour extend a span's last endpoint and maximum
observed polling gap. Larger gaps create another span. Reports may bridge spans
only when their requested gap bound and the actual retained evidence permit it.
Within a compacted span, a query bound at least as large as its maximum polling
gap has the same attribution as raw observations, except where independently
recorded uncertainty intervenes. A stricter bound cannot recover discarded
interior anchors: its interior remains unknown. Raw/saved observations can
restore supported subintervals. Endpoint evidence remains available, but the
last endpoint of an unsupported compact span grants no backward extrapolation.

Similarly, a historical `--until` inside a compact span cannot use the later
endpoint as an observation at that historical horizon. Coverage stops at the
last independently available endpoint at or before the horizon. Saved snapshots
can restore this detail. These conservative limitations are intentional; reports
never invent intermediate observation timestamps.

Retention uses collection/maintenance UTC time, never future source timestamps.
Future observations are excluded from compact storage. A future operation event
is bounded to first receipt time, with its source timestamp retained as its
idempotency key. Expiry removes old spans/boundaries; crossing spans retain one
first endpoint anchor and expose coverage clipped to the current cutoff. Normal
initialization and `fleetroll maintain` remove unreferenced descriptors. Space
remains reusable until SQLite vacuum reclaims it.

Reports open SQLite in read-only mode and never initialize or migrate it. JSON
adds `configuration_history` metadata, interval source/gap/cutoff fields, and
per-host `configuration_evidence` provenance plus `configuration_gaps` covering
unknown portions of the requested window. Human output includes the retention
cutoff and span count, with individual unknown ranges under `--verbose`. Saved
files remain independent evidence and are not imported into SQLite.

## Reproducible measurements

Run `uv run python scripts/benchmark_configuration_history.py`. Defaults are
930 hosts and 480 three-minute scans (23.95 simulated hours), 446,400 observations
per workload, approximately 1,354 bytes per raw record. Every observation commits
separately through `insert_host_observation`, matching collection's transaction
pattern. The second workload changes applied revision every four hours and has
a 12-minute collection failure every hour. These are synthetic local SQLite
measurements; they exclude SSH, Taskcluster requests, and network latency.

The script reports rows, descriptor payload bytes, table/index page allocation,
collection time, evidence/interval query latency, logical expiry, and vacuum.
Physical size includes the unchanged latest-ten raw snapshots. Expiry is measured
by advancing the cleanup clock 31 days, followed by vacuum. No real user database
or host is accessed.

On the development Mac (Python 3.14, November 2026), the stable workload took
79.4 seconds (0.178 ms per observation), and remained at 930 spans and 930
descriptors at scans 1, 60, and 480. History tables and indexes occupied 1,720,320
bytes, including 1,036,950 logical descriptor bytes. Reading compact endpoints
took 0.013 seconds; loading evidence and constructing all host intervals took
0.274 seconds. Routine cleanup was below 0.001 seconds; expiry took 0.006 seconds.

The changing/outage workload took 95.3 seconds (0.214 ms per observation), with
50,220 spans and 11,160 descriptors after 480 scans. History tables and indexes
occupied 27,971,584 bytes, including 9,508,320 logical descriptor bytes.
Compact reading took 0.638 seconds and evidence loading plus interval construction
took 4.02 seconds. Routine cleanup took 0.017 seconds and expiry 0.275 seconds.
Both workloads retained 9,300 full observations. Vacuum after simulated expiry
took 0.10 and 0.12 seconds respectively and left about 19.8 MB of database pages,
primarily the retained raw snapshots. These figures describe this workload, not
a fixed storage budget; repeated failures and transitions increase storage.
