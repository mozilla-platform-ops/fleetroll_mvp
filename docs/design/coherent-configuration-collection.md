# Coherent configuration collection

Implementation design for mvp-257d, the collection phase of
[health evidence and verification](health-evidence-and-verification.md).
The supported envelope is specified in
[configuration collection evidence](../reference/configuration-evidence.md).

## Boundaries

Add `observed.configuration_evidence` alongside existing fields. It owns one
configuration capture and its component provenance, not a health or convergence
verdict. It does not change inventory, gwhc, TC identity, or worker-evidence v3.
The later mvp-zjwr evaluator must consume this contract explicitly.

Use Puppet's bundled Ruby, already required by the worker-identity collector, to
read metadata, override and vault within one bounded operation. Keep this
snapshot separate from older shell fields: those reads can represent different
times. No legacy YAML, command-line fields or audit-log checksum can fill a gap
in a new component. Legacy JSON parsing also chooses JSON as a whole source,
without borrowing individual null/missing fields from legacy KV lines.

## Capture

Read each file twice, retaining descriptor/path device, inode, size, ownership,
mode, nanosecond mtime/ctime, and full SHA-256. Check descriptor stability and
path identity during each read; then compare both passes across all three
sources. Read nonblocking regular files only. Distinguish definite ENOENT from
permission/IO errors and dangling links. Bound the Ruby operation to ten
seconds; bound metadata to 64 KiB, override to 1 MiB, and vault to 16 MiB.
Only metadata content leaves this collector; override/vault emit hashes and
stat data. Existing content-addressed artifact gathering remains separate.

Two agreeing reads are evidence of observed stability, not an atomic filesystem
transaction, a lock or proof that no change-and-restore happened between reads.
Capture consistency is `stable`, `changed`, or `unknown`. A changed component
becomes invalid for comparison; unavailable components never get a successful
observation time. Preserve the useful unaffected components, but do not label a
partial/changed group coherent. No retries inside the capture can hide a race.

## Provenance and compatibility

Validate producer metadata v1 and its required run timing/result fields. Preserve
optional field presence, raw values, and explicit nulls separately. An explicit
v1 applied checksum null is ambiguous: the reference writer uses null both for
absence and a failed hash. It cannot prove applied-file absence. No producer
change is needed for this delivery; future explicit applied presence/run-ID
semantics require a coordinated producer contract and supported version.

V1 has no invocation identity contract. Retain a reported additive `run_id` for
inspection, but do not treat it as qualified or exclusive execution proof.
One metadata document supplies every interpreted run field. Run completion time
and component collection time describe different events and remain separate.

Allocate a local UUID and microsecond UTC timestamps for every SSH attempt,
including retries, exhausted connections, local execution errors, and expired
batch budgets. The new gather record uses the same high-resolution completion
time to avoid overwriting quick retries in the host/timestamp primary key.
Remote capture times stay in their own clock domain. A transport failure cannot
validate a partial diagnostic payload.

Keep old flat fields and overall SSH-oriented `ok` behavior. Consumers inspect
the independent configuration attempt status. Missing new evidence in historical
rows means not collected; do not upgrade old rows or invent capture timestamps.
Windows remains unsupported by this new collector, with its legacy behavior
otherwise preserved.

## Retention

Persist the envelope in the existing observation JSON. Within the same SQLite
savepoint, carry forward at most one usable prior component per source when its
new component is unavailable. Store these under `retained_components`, with
their original observation/confirmation times and originating attempt IDs.
Do not merge them into current components, update their clocks, synthesize
coherence across attempts, or accumulate recursive history. The latest row thus
keeps useful component evidence through arbitrarily many partial gathers even
after ordinary full-row retention trims the source record. Existing successful
row retention and compact configuration history keep their own semantics.

## Verification

Exercise the real Ruby collector with isolated files for Linux/macOS, missing
and unreadable sources, nonregular/oversized files, races and timeout. Verify
typed metadata, optional field presence, unsupported versions, malformed results,
limited run provenance, and source isolation. Round-trip partial and failed
attempts through SQLite and monitor JSON, including rapid retries and more
failures than the ordinary retention window. Existing gather, rollout-history,
monitor/JSON and worker tests establish compatibility. Full suite and prek are
required before delivery. No live host mutation or producer deployment is needed.
