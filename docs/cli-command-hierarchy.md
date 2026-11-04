# CLI command hierarchy

Proposed resource groups replace the flat command list. This is a CLI naming and
discovery change; command behavior, argument meanings, defaults, output formats,
exit codes, confirmation requirements, and audit action names remain stable.
Both `fleetroll` and `fr` expose the same hierarchy.

| Current command | Canonical command |
| --- | --- |
| create-override | override create |
| show-override | override show |
| host-set-override | override set |
| host-unset-override | override unset |
| show-vault | vault show |
| host-set-vault | vault set |
| host-monitor | host monitor |
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

Retain all former flat names as hidden compatibility aliases, without deprecation
warnings or a removal schedule. Aliases must invoke the same implementation and
accept existing flags, positional arguments, and option placement. Avoid mutating
a shared Click command's hidden/name settings in a way that hides the canonical
command too. Resource group help lists canonical actions; top-level help lists
groups and `web`. A group invoked without arguments displays its help.

`gather` needs special compatibility routing because it is already a command.
Use `gather all HOST_OR_FILE` as the canonical combined action, while retaining
`gather HOST_OR_FILE` and its existing flags as shorthand. Explicit subcommands
`all`, `host`, `tc`, and `gh` take precedence. An ambiguously named host-list file
can be disambiguated with `./host`, for example; a hostname can use its FQDN.
`gather --help` shows group help and documents the legacy shorthand. Do not add
new inherited group options: existing action flags follow the action name in
canonical invocations. Existing global flags remain global.

## Implementation and validation

Reuse existing callbacks, with compatibility routing at the CLI boundary. Update
README examples, current operational documentation, the override wrapper, and
user-facing command suggestions to canonical names. Keep historical records,
old bead descriptions, and tests intentionally exercising legacy names intact.

Test canonical help visibility and alias hiding; canonical and legacy argument
forwarding (including flags and host lists); combined gather dispatch, skip flags,
and ambiguous inputs; unchanged errors, JSON output and exit codes; and mutation
commands' dry-run/confirmation and worker type safety behavior through both names.
Run the full suite and prek before marking the implementation complete.
