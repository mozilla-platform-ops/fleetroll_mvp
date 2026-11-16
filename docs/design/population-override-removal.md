# Population override removal design

Implementation design for `mvp-3vc.1`. Operator behavior and JSON are documented
in [override removal](../override-removal.md).

Use `selection.select_hosts` and the shared monitor query engine for population
resolution. Extend `HostSelection` with the source latest/latest-successful
records and selected rows from the same read transaction. Build removal
expectations from that snapshot, preserving the monitor's failed-gather fallback
and original timestamps. Do not reload the database between selection and
expectation construction.

`override_removal.py` owns typed targets, versioned saved plans, validation,
freshness labels, and serialization. A target binds one host to one expected
full deployed override checksum, or explicit absence. Unknown evidence blocks
that target. Optional saved plans preserve the preview across invocations;
direct confirmed filters intentionally reselect. Plans freeze targets and file
expectations, not transport, backup, reason, or automatic-refresh options.

Keep execution, audit writing, and per-host formatting in `commands/unset.py`.
Reuse the existing per-host worker with an optional checked target. Population
execution provides a consistent envelope for zero, one, or many hosts while
legacy explicit invocations retain their output contracts. Refresh attempted
hosts and preserve individual failures. Route refresh stdout to stderr for
population JSON output.

`ssh.remote_unset_script` accepts an optional expected checksum, enabling a
checked script through the same per-host entry point. The checked script runs
all checks as root, detects supported platform paths, refuses symlinks and
unavailable content, verifies the full checksum before backup and immediately
before removal, and treats absence as a no-op. It cannot atomically compare and
unlink against uncooperative concurrent writers; document that remaining race.

This design introduces no inventory, source-commit provenance, rollout state
machine, Puppet execution, or convergence evaluator. `mvp-t8yk` can reuse plans
and execution after defining how source commits select exact deployed contents.
