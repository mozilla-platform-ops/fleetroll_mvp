# Compact host configuration history

Fleetroll retains 30 days of compact configuration evidence alongside the latest
ten full observations per host and the last-success exception. Stable polling
extends span endpoints; material configuration changes, failures, writes, and
observation gaps preserve uncertainty boundaries. Reports read this history
without gathering data or modifying storage.

The timeline records role, applied repository/revision, configured/applied
override and vault checksums, dirty/success state, and effective worker identity.
Repeated Puppet runs, uptime, polling timestamps, and durations do not create a
new configuration. No vault/override contents, raw output, or credentials are
retained in compact descriptors.

A write records intent, not convergence. Failed writes may have changed the
host. Matching states cannot bridge known uncertainty, and coverage never extends
past a successful observation. Changes reverted entirely between observations
are undetectable. Migration cannot recover evidence already pruned from every
source; saved snapshots can supplement retained history.

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
initialization and `fleetroll data maintain` remove unreferenced descriptors. Space
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
