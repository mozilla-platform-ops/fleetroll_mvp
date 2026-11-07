# CLI command hierarchy

Resource groups replace the flat command list (implementation: mvp-bqod). This is a CLI naming and
discovery change; command behavior, argument meanings, defaults, output formats,
exit codes, confirmation requirements, and audit action names remain stable.
Both `fleetroll` and `fr` expose the same hierarchy.

| Legacy command | Canonical command |
| --- | --- |
| create-override | override create |
| show-override | override show |
| host-set-override | override set |
| host-unset-override | override unset |
| show-vault | vault show |
| host-set-vault | vault set |
| host-monitor | host monitor |
| host-diagnose | host diagnose |
| host-run-puppet | host run-puppet |
| host-reboot-if-idle | host reboot-if-idle |
| note-add | note add |
| note-clear | note clear |
| show-notes | note show |
| gather-host | gather host |
| gather-tc | gather tc |
| gather-gh | gather gh |
| gather | gather all |
| fleet-status | fleet status |
| rollout-report | rollout report |
| data-freshness | data freshness |
| maintain | data maintain |
| debug-host-script | debug host-script |
| web | web |

## Compatibility and help

Former flat names remain hidden compatibility aliases, without deprecation
warnings or a removal schedule. They use the same implementation and accept
existing flags, positional arguments, and option placement.
Resource group help lists canonical actions; top-level help lists
groups and `web`. A group invoked without arguments displays its help.

`gather` needs special compatibility routing because it is already a command.
Use `gather all HOST_OR_FILE` as the canonical combined action, while retaining
`gather HOST_OR_FILE` and its existing flags as shorthand. Explicit subcommands
`all`, `host`, `tc`, and `gh` take precedence. An ambiguously named host-list file
can be disambiguated with `./host`, for example; a hostname can use its FQDN.
`gather --help` shows group help and documents the legacy shorthand. Do not add
new inherited group options: existing action flags follow the action name in
canonical invocations. Existing global flags remain global.

Host collection actions (`gather all`, `gather host`, `gather tc`) also accept
`--filter QUERY` or `--filter-file FILE.yaml`, including through their legacy
spellings. With a filter, the positional host/file is optional and defaults to
recorded database hosts. Supplying a host/file restricts candidate scope. Bare
`gather --filter QUERY` routes to `gather all`. See
[gather selectors](../monitor-filter-sort.md#gather-matching-hosts) for frozen
selection, no-match behavior, output, and evidence limits.

## Maintainer checks

Keep compatibility routing at the CLI boundary. Update
README examples, current operational documentation, the override wrapper, and
user-facing command suggestions to canonical names. Keep historical records,
old bead descriptions, and tests intentionally exercising legacy names intact.

Test canonical help visibility and alias hiding; canonical and legacy argument
forwarding (including flags and host lists); combined gather dispatch, skip flags,
and ambiguous inputs; unchanged errors, JSON output and exit codes; and mutation
commands' dry-run/confirmation and worker type safety behavior through both names.
Run the full suite and `prek` after CLI changes.
