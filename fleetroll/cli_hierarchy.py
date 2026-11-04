"""Canonical resource groups and compatibility aliases for the CLI."""

from __future__ import annotations

from copy import copy

import click

COMMAND_PATHS = {
    "create-override": ("override", "create"),
    "show-override": ("override", "show"),
    "host-set-override": ("override", "set"),
    "host-unset-override": ("override", "unset"),
    "show-vault": ("vault", "show"),
    "host-set-vault": ("vault", "set"),
    "host-monitor": ("host", "monitor"),
    "host-idle": ("host", "idle"),
    "host-run-puppet": ("host", "run-puppet"),
    "host-reboot-if-idle": ("host", "reboot-if-idle"),
    "note-add": ("note", "add"),
    "note-clear": ("note", "clear"),
    "show-notes": ("note", "show"),
    "gather-host": ("gather", "host"),
    "gather-tc": ("gather", "tc"),
    "gather-gh": ("gather", "gh"),
    "gather": ("gather", "all"),
    "fleet-status": ("fleet", "status"),
    "rollout-report": ("rollout", "report"),
    "data-freshness": ("data", "freshness"),
    "maintain": ("data", "maintain"),
    "debug-host-script": ("debug", "host-script"),
}

GROUP_HELP = {
    "override": "Create, inspect, deploy, and remove Puppet overrides.",
    "vault": "Inspect and deploy vault files.",
    "host": "Monitor hosts, check activity, run Puppet, and request reboots.",
    "note": "Add, show, and clear host notes.",
    "gather": (
        "Collect host state, Taskcluster data, and GitHub references. "
        "Use 'gather all HOST_OR_FILE' for combined collection. "
        "Legacy 'gather HOST_OR_FILE [OPTIONS]' is also supported. "
        "The names all, host, tc, and gh are reserved actions; use ./host "
        "for a file named host, or a FQDN for an ambiguous hostname."
    ),
    "fleet": "Report fleet configuration status.",
    "rollout": "Report rollout outcomes.",
    "data": "Inspect freshness and maintain local data.",
    "debug": "Inspect diagnostic scripts.",
}


class GatherGroup(click.Group):
    """Route existing gather invocations to the combined action."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and args[0] not in {*self.commands, "--help", "-h"}:
            args = ["all", *args]
        return super().parse_args(ctx, args)


def install_hierarchy(cli: click.Group) -> None:
    """Reuse callbacks and parameters, copying command metadata for each path."""
    original = dict(cli.commands)
    groups: dict[str, click.Group] = {}
    for name, help_text in GROUP_HELP.items():
        group_class = GatherGroup if name == "gather" else click.Group
        group = group_class(name=name, help=help_text, no_args_is_help=True)
        groups[name] = group
        cli.add_command(group)
    for legacy, (group_name, action) in COMMAND_PATHS.items():
        command = original[legacy]
        canonical = copy(command)
        canonical.name = action
        canonical.hidden = False
        groups[group_name].add_command(canonical)
        # gather's old spelling is handled by GatherGroup rather than an alias.
        if legacy != "gather":
            command.hidden = True
