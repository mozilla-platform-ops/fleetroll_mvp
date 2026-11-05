# Worker identity collection

`gather host` records `tc_worker_identity_status` and `tc_worker_identity` in
Linux/macOS observations. The identity contains only `provisionerId`,
`workerType`, `workerGroup`, and `workerId`. This is configured identity, not
evidence that a worker process is healthy or that a deployed override was applied.

The remote collector uses Puppet's bundled Ruby at
`/opt/puppetlabs/puppet/bin/ruby` to parse JSON or safe-load YAML on the host.
Credentials, full configuration, and parser error details are never returned.
Missing Ruby, unreadable or malformed configuration, and ambiguous identities
are recorded explicitly without failing the rest of the host audit.

Configuration selection follows the managed launch paths in ronin_puppet:

- Running generic-worker/start-worker arguments take precedence, including
  `--config PATH`, `--config=PATH`, and runner positional configuration paths.
  Relative paths are rejected because their working directory is unknown.
- Shell-wrapper paths are fallback hints only when no running worker/runner
  executable supplies a configuration. Stale wrapper paths cannot override active
  selections; missing, unreadable, invalid, or unresolved active configurations
  fail collection without wrapper fallback.
- When no running configuration is found on Linux, use `/etc/start-worker.yml`,
  which the managed desktop launcher passes to start-worker.
- On macOS, when no running configuration is found, inspect `ProgramArguments`
  in the managed launch plists: `net.generic.worker` (LaunchAgents),
  `com.mozilla.genericworker` (LaunchDaemons), and `org.mozilla.worker-runner`
  (LaunchDaemons or per-user LaunchAgents). This handles legacy
  `/etc/generic-worker.config`, multiuser `/etc/generic-worker/config`, and
  worker-runner configurations under `/opt/worker` without choosing a leftover
  file by existence alone.
- Runner YAML supplies identity through `provider.workerPoolID`, `workerGroup`,
  and `workerID`. Direct worker configuration supplies the four identity fields.
  All selected active configurations must agree on identity, as must selected
  wrapper configurations when used as fallback. Custom launch arrangements
  require collector support.

`gather tc` prefers the observed identity and matches `(workerGroup, workerId)`
within the observed provisioner/pool. `WORKER_TYPE_OVERRIDE` is deployment intent:
a disagreement produces a warning and the query uses the observed configuration.
No role naming convention or pool whitelist is needed for new observations.

For compatibility, observations without the status field retain the old
role/override resolution, with a warning to refresh host observations. This also
preserves existing Windows collection. Explicit identity failures block that
fallback. Duplicate worker IDs across groups cannot be resolved arbitrarily for
legacy observations.

Per-host fallback warnings, pool disagreements, and unresolved/missing worker
diagnostics require `gather tc -v`. Quiet mode prints status, collected/requested
counts, and elapsed time in at most 80 visible characters, with a short verbose
hint when needed. A failed collection still exits
nonzero; quiet mode does not repeat the summary as a separate error line.

Unresolved identities, missing workers, pool API failures, and task status failures
produce a nonzero collection exit, including in quiet mode. Successful hosts are
committed even during partial failure. A failed host never receives a new TC
observation timestamp. Monitor health, stale-data presentation, and automation
semantics remain the separate design task mvp-d0kx.

Validation includes unknown roles, pool migrations, full worker identities,
legacy fallback, configuration/override disagreement, explicit collection errors,
credential filtering, and partial-success timestamp preservation. Live checks
on 2026-11-04 verified Linux Wayland hosts 001–003 and macOS R8/M4 host identity
collection.
