# Fleetroll documentation

Start with the [project README](../README.md) for installation, collection,
overrides, and common commands. These pages cover workflows and their evidence
contracts in more detail.

## Operating Fleetroll

| Task | Guide |
| --- | --- |
| Find and order hosts | [Filter, sort, and saved filters](monitor-filter-sort.md) |
| Record operational context | [Host notes](notes-feature.md) |
| Check configuration and task outcomes | [Verify a rollout](rollout-health-verification.md), [rollout report](rollout-report.md), [compare pools](rollout-compare.md) |
| Understand when Puppet runs | [Puppet execution in Ronin](puppet-execution-mechanisms.md) |
| Inspect Windows workers | [Windows hosts](windows-support.md) |
| Use the browser host grid | [Web interface](web-interface.md) |
| Request an idle-worker reboot | [Reboot behavior and limits](reboot-if-idle.md) |
| Prepare and publish a release | [Release workflow](release-notes/README.md) |

## Reference

| Topic | Reference |
| --- | --- |
| Commands and aliases | [CLI hierarchy](reference/cli-command-hierarchy.md) |
| Override deployment checks | [Worker type safety](reference/override-worker-type-safety.md) |
| Puppet run fields and collector paths | [Puppet metadata](reference/puppet-state-tracking.md) |
| Taskcluster identity selection | [Worker identity collection](reference/worker-identity.md) |
| Machine-readable host and worker evidence | [Monitor JSON v3](reference/monitor-json.md) |
| Live worker health diagnostics | [Host diagnosis JSON v1](reference/host-diagnose.md) |
| Retained identity for unreachable hosts | [Historical worker identity](reference/historical-worker-identity.md) |
| Configuration timelines and storage measurements | [Compact configuration history](reference/configuration-history.md) |
| Consumers and compatibility expectations | [Tool integrations](reference/tool-integrations.md) |
| Monitor layout rationale | [Column ordering](reference/monitor-column-ordering.md) |

## Designs and historical material

`design/` contains proposals, not supported command/API contracts:

- [Client/server architecture](design/client-server-design.md)
- [Tart fleet integration](design/tart-fleet-integration.md)

`archive/` retains dated planning and review context, not current task status:

- [April 2026 backlog review](archive/20260427-beads-review.md)
- [May 2026 development plan](archive/20260501-dev-plan.md)
- [March 2026 client/server review](archive/client-server-design-review.md)
- [SQLite migration benchmarks](archive/sqlite-migration-benchmarks.md)

Published [release notes](release-notes/v0.3.0.md) retain their historical meaning.
The [presentation](project_presentation/README.md) is an overview, not a command
reference.

## Editing these docs

Keep practical guides here, exact behavior/contracts in `reference/`, proposals
in `design/`, and dated records in `archive/`. Give each topic one authoritative
home and link to it from related workflows. Use current grouped CLI names and
check examples against `--help` and the implementation. Explain evidence limits
where they affect a decision; keep task lists and completion status in Beads.
