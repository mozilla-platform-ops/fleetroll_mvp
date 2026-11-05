"""FleetRoll CLI using Click."""

from __future__ import annotations

import logging
import shlex
import sys
from importlib.metadata import version
from pathlib import Path

import click

from .cli_hierarchy import install_hierarchy
from .cli_types import (
    DataFreshnessArgs,
    HostAuditArgs,
    HostHoldArgs,
    HostIdleArgs,
    HostMonitorArgs,
    HostRebootIfIdleArgs,
    HostRunPuppetArgs,
    HostSetOverrideArgs,
    HostSetVaultArgs,
    HostUnsetOverrideArgs,
    MaintainArgs,
    OverrideShowArgs,
    TcFetchArgs,
    VaultShowArgs,
    WebArgs,
)
from .commands import (
    cmd_data_freshness,
    cmd_gh_fetch,
    cmd_host_audit,
    cmd_host_hold,
    cmd_host_idle,
    cmd_host_monitor,
    cmd_host_reboot_if_idle,
    cmd_host_run_puppet,
    cmd_host_set,
    cmd_host_set_vault,
    cmd_host_unset,
    cmd_maintain,
    cmd_note_add,
    cmd_note_clear,
    cmd_override_show,
    cmd_show_notes,
    cmd_tc_fetch,
    cmd_vault_show,
    cmd_web,
)
from .exceptions import CommandFailureError, FleetRollError, UserError
from .ssh import audit_script_body, remote_windows_audit_script, windows_audit_script_body
from .utils import parse_worker_type, resolve_host_args

# Module logger
logger = logging.getLogger("fleetroll")


def setup_logging(debug: bool = False) -> None:
    """Configure logging for the CLI."""
    level = logging.DEBUG if debug else logging.WARNING
    logger.setLevel(level)
    if any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr
        for h in logger.handlers
    ):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)


# Common options that apply to all commands
def common_options(_func=None, *, timeout_default: int = 10):
    """Decorator to add common options to all commands.

    Can be used as @common_options or @common_options(timeout_default=600).
    """

    def decorator(func):
        func = click.option(
            "--ssh-option",
            multiple=True,
            help="Extra ssh options, e.g. '--ssh-option \"-J bastion\"' (repeatable).",
        )(func)
        func = click.option(
            "--connect-timeout",
            type=int,
            default=10,
            show_default=True,
            help="SSH connect timeout seconds.",
        )(func)
        func = click.option(
            "--timeout",
            type=int,
            default=timeout_default,
            show_default=True,
            help="Overall ssh command timeout seconds.",
        )(func)
        func = click.option(
            "--audit-log",
            type=click.Path(),
            help="Path to local JSONL audit log (default: ~/.fleetroll/audit.jsonl).",
        )(func)
        func = click.option(
            "--json",
            "json_output",
            is_flag=True,
            help="Emit machine-readable JSON to stdout.",
        )(func)
        return func

    if _func is not None:
        return decorator(_func)
    return decorator


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=version("fleetroll"), prog_name="fleetroll")
@click.option(
    "--debug",
    "-d",
    is_flag=True,
    help="Enable debug logging to stderr.",
)
@click.pass_context
def cli(ctx: click.Context, debug: bool):
    """FleetRoll MVP: audit/set/unset override files on hosts via SSH."""
    ctx.ensure_object(dict)
    ctx.obj["debug"] = debug
    setup_logging(debug=debug)


@cli.command("create-override")
@click.argument("source")
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Write to this file; never overwrite.",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    default=".",
    show_default=True,
    help="Directory for the generated filename; ignored with --output.",
)
def create_override(source: str, output: Path | None, output_dir: Path) -> None:
    """Create an override from a branch, PR number, or PR URL.

    Requires git and network access to verify the branch, plus gh for PR inputs.
    Output defaults to the current directory. Parent directories must exist.
    """
    from .commands.create_override import create_override as generate

    generate(source, output=output, output_dir=output_dir)


@cli.command("gather-host")
@click.argument("host", metavar="HOST_OR_FILE...", nargs=-1, required=True)
@common_options
@click.option(
    "--no-content",
    is_flag=True,
    help="Do not print override contents (still prints presence + metadata).",
)
@click.option(
    "--workers",
    type=int,
    default=10,
    show_default=True,
    help="Number of parallel workers for batch mode.",
)
@click.option(
    "--batch-timeout",
    type=int,
    default=600,
    show_default=True,
    help="Overall timeout for batch operations in seconds.",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Show detailed per-host results in batch mode.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Single-line output.",
)
def gather_host(
    host: tuple[str, ...],
    ssh_option: tuple[str, ...],
    connect_timeout: int,
    timeout: int,
    audit_log: str | None,
    json_output: bool,
    no_content: bool,
    workers: int,
    batch_timeout: int,
    verbose: bool,
    quiet: bool,
):
    """Audit one or more hosts (role + override presence + optionally contents).

    HOST_OR_FILE can be one or more hostnames, user@hostname values, or a file
    containing hosts (one per line for batch mode). A host file cannot be mixed
    with bare hostnames.
    """
    if verbose and quiet:
        raise click.UsageError("--verbose and --quiet are mutually exclusive")

    hosts, host_file = resolve_host_args(host)
    args = HostAuditArgs(
        host=str(host_file) if host_file else hosts[0],
        ssh_option=list(ssh_option) if ssh_option else None,
        connect_timeout=connect_timeout,
        timeout=timeout,
        audit_log=audit_log,
        json=json_output,
        no_content=no_content,
        workers=workers,
        batch_timeout=batch_timeout,
        verbose=verbose,
        quiet=quiet,
        hosts=hosts,
        host_file=host_file,
    )
    cmd_host_audit(args)


@cli.command("host-monitor")
@click.argument("host", metavar="HOST_OR_FILE")
@click.option(
    "--audit-log",
    type=click.Path(),
    help="Path to local JSONL audit log (default: ~/.fleetroll/audit.jsonl).",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit machine-readable JSON to stdout.",
)
@click.option(
    "--once",
    is_flag=True,
    help="Print the latest record and exit (no follow).",
)
@click.option(
    "--sort",
    type=click.Choice(["host", "role"], case_sensitive=False),
    default="host",
    help="Sort order for display (default: host).",
)
@click.option(
    "--filter",
    "filter_query",
    default=None,
    help='Filter/sort query (e.g. "pp_last>20h sort:tc_act:desc").',
)
@click.option(
    "--filter-file",
    "filter_file",
    type=click.Path(exists=True),
    default=None,
    help="Load filter query from a named filter YAML file (configs/filters/*.yaml).",
)
@click.option(
    "--hostname-only",
    is_flag=True,
    help="Print one FQDN per line (requires --once).",
)
def host_monitor(
    host: str,
    audit_log: str | None,
    json_output: bool,
    once: bool,
    sort: str,
    filter_query: str | None,
    filter_file: str | None,
    hostname_only: bool,
):
    """Monitor the latest audit record for a host (follows the audit log)."""
    import yaml

    if filter_query and filter_file:
        raise click.UsageError("--filter and --filter-file are mutually exclusive")
    if filter_file:
        try:
            data = yaml.safe_load(click.open_file(filter_file).read())
        except Exception as exc:
            raise click.UsageError(f"could not load filter file: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("query"), str):
            raise click.UsageError(f"filter file missing 'query' field: {filter_file}")
        filter_query = data["query"].strip()
    if hostname_only and not once:
        raise click.UsageError("--hostname-only requires --once")
    if hostname_only and json_output:
        raise click.UsageError("--hostname-only and --json are mutually exclusive")
    args = HostMonitorArgs(
        host=host,
        audit_log=audit_log,
        json=json_output,
        once=once,
        sort=sort.lower(),
        filter=filter_query,
        hostname_only=hostname_only,
    )
    cmd_host_monitor(args)


@cli.command("fleet-status")
@click.argument("hosts_file", required=False, type=click.Path(exists=True, dir_okay=False))
@click.option("--role", help="Puppet role to select.")
@click.option(
    "--worker-type",
    help="Worker type or provisioner/worker-type (default provisioner: releng-hardware).",
)
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
@click.option("--verbose", "-v", is_flag=True, help="Show host lists in the report.")
def fleet_status(
    hosts_file: str | None,
    role: str | None,
    worker_type: str | None,
    json_output: bool,
    verbose: bool,
):
    """Summarize recorded override status for a role or worker pool."""
    from .commands.fleet_status import run_fleet_status

    if bool(role) == bool(worker_type):
        raise click.UsageError("Specify exactly one of --role or --worker-type")
    if worker_type:
        try:
            worker_type = "/".join(parse_worker_type(worker_type))
        except ValueError as exc:
            raise click.UsageError(f"--worker-type: {exc}") from exc
    run_fleet_status(
        hosts_file=hosts_file,
        role=role,
        worker_type=worker_type,
        json_output=json_output,
        verbose=verbose,
    )


@cli.command(
    "rollout-report",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    add_help_option=False,
)
@click.pass_context
def rollout_report(ctx: click.Context):
    """Compare recent Taskcluster outcomes across rollout configurations."""
    from .rollout_report import main

    ctx.exit(main(ctx.args, prog=ctx.command_path))


@cli.command("show-override")
@click.argument("sha_prefix")
@click.option(
    "--audit-log",
    type=click.Path(),
    help="Path to local JSONL audit log (default: ~/.fleetroll/audit.jsonl).",
)
def override_show(sha_prefix: str, audit_log: str | None):
    """Show stored override contents by SHA prefix."""
    args = OverrideShowArgs(
        sha_prefix=sha_prefix,
        audit_log=audit_log,
    )
    cmd_override_show(args)


@cli.command("show-vault")
@click.argument("sha_prefix")
@click.option(
    "--audit-log",
    type=click.Path(),
    help="Path to local JSONL audit log (default: ~/.fleetroll/audit.jsonl).",
)
def vault_show(sha_prefix: str, audit_log: str | None):
    """Show stored vault contents by SHA prefix or humanhash."""
    args = VaultShowArgs(
        sha_prefix=sha_prefix,
        audit_log=audit_log,
    )
    cmd_vault_show(args)


@cli.command("debug-host-script")
@click.option(
    "--no-content",
    is_flag=True,
    help="Do not include override contents in the script output.",
)
@click.option(
    "--wrap",
    is_flag=True,
    help="Wrap output as a 'sh -c' command (ssh-ready); for --windows, outputs encoded PowerShell command.",
)
@click.option(
    "--windows",
    is_flag=True,
    help="Show the Windows PowerShell audit script instead of the Unix shell script.",
)
def debug_host_script(
    no_content: bool,
    wrap: bool,
    windows: bool,
):
    """Print the remote host audit script used by gather host."""
    if windows:
        if wrap:
            print(remote_windows_audit_script())
        else:
            print(windows_audit_script_body())
    else:
        body = audit_script_body(include_content=not no_content)
        if wrap:
            print("sh -c " + shlex.quote(body))
        else:
            print(body)


@cli.command("host-set-override")
@click.argument("host", metavar="HOST_OR_FILE...", nargs=-1, required=True)
@common_options
@click.option(
    "--workers",
    type=int,
    default=10,
    show_default=True,
    help="Number of parallel workers for batch mode.",
)
@click.option(
    "-f",
    "--from-file",
    type=click.Path(exists=True),
    help="Read override contents from a local file.",
)
@click.option(
    "--no-validate",
    is_flag=True,
    hidden=True,
    help="Skip local override syntax validation.",
)
@click.option(
    "--mode",
    default="0644",
    show_default=True,
    help="File mode.",
)
@click.option(
    "--owner",
    default="root",
    show_default=True,
    help="File owner user.",
)
@click.option(
    "--group",
    default="auto",
    show_default=True,
    help="File group. 'auto' uses wheel on macOS, root on Linux.",
)
@click.option(
    "--no-backup",
    is_flag=True,
    help="Do not create a backup of existing override file.",
)
@click.option(
    "--reason",
    help="Optional reason string for audit log.",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Apply the changes. Without this flag, a summary is printed and the command exits.",
)
@click.option(
    "--ignore-state",
    is_flag=True,
    help="Push to all hosts even if state data shows the correct SHA is already present.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing override even if one already exists on the host.",
)
@click.option(
    "--no-audit",
    is_flag=True,
    help="Skip the automatic gather host run after the override is written.",
)
@click.option(
    "--allow-worker-type-mismatch",
    is_flag=True,
    help="Bypass worker type compatibility checks, including unknown host roles.",
)
def host_set_override(
    host: tuple[str, ...],
    ssh_option: tuple[str, ...],
    connect_timeout: int,
    timeout: int,
    audit_log: str | None,
    json_output: bool,
    workers: int,
    from_file: str | None,
    no_validate: bool,
    mode: str,
    owner: str,
    group: str,
    no_backup: bool,
    reason: str | None,
    confirm: bool,
    ignore_state: bool,
    force: bool,
    no_audit: bool,
    allow_worker_type_mismatch: bool,
):
    """Set the override file on one or more hosts (atomic write).

    HOST_OR_FILE can be one or more hostnames, user@hostname values, or a file
    containing hosts (one per line). Contents must be provided via --from-file.
    """
    hosts, host_file = resolve_host_args(host)
    args = HostSetOverrideArgs(
        hosts=hosts,
        host_file=host_file,
        ssh_option=list(ssh_option) if ssh_option else None,
        connect_timeout=connect_timeout,
        timeout=timeout,
        audit_log=audit_log,
        json=json_output,
        workers=workers,
        from_file=from_file,
        validate=(not no_validate),
        mode=mode,
        owner=owner,
        group=group,
        no_backup=no_backup,
        reason=reason,
        confirm=confirm,
        ignore_state=ignore_state,
        force=force,
        no_audit=no_audit,
        allow_worker_type_mismatch=allow_worker_type_mismatch,
    )
    cmd_host_set(args)


@cli.command("host-set-vault")
@click.argument("host", metavar="HOST_OR_FILE...", nargs=-1, required=True)
@common_options
@click.option(
    "--workers",
    type=int,
    default=10,
    show_default=True,
    help="Number of parallel workers for batch mode.",
)
@click.option(
    "-f",
    "--from-file",
    type=click.Path(exists=True),
    help="Read vault contents from a local file.",
)
@click.option(
    "--no-validate",
    is_flag=True,
    hidden=True,
    help="Skip local YAML syntax validation.",
)
@click.option(
    "--mode",
    default="0640",
    show_default=True,
    help="File mode.",
)
@click.option(
    "--owner",
    default="root",
    show_default=True,
    help="File owner user.",
)
@click.option(
    "--group",
    default="auto",
    show_default=True,
    help="File group. 'auto' uses wheel on macOS, root on Linux.",
)
@click.option(
    "--no-backup",
    is_flag=True,
    help="Do not create a backup of existing vault file.",
)
@click.option(
    "--reason",
    help="Optional reason string for audit log.",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Apply the changes. Without this flag, a summary is printed and the command exits.",
)
@click.option(
    "--ignore-state",
    is_flag=True,
    help="Push to all hosts even if state data shows the correct SHA is already present.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing vault even if one already exists on the host.",
)
@click.option(
    "--no-audit",
    is_flag=True,
    help="Skip the automatic gather host run after the vault is written.",
)
def host_set_vault(
    host: tuple[str, ...],
    ssh_option: tuple[str, ...],
    connect_timeout: int,
    timeout: int,
    audit_log: str | None,
    json_output: bool,
    workers: int,
    from_file: str | None,
    no_validate: bool,
    mode: str,
    owner: str,
    group: str,
    no_backup: bool,
    reason: str | None,
    confirm: bool,
    ignore_state: bool,
    force: bool,
    no_audit: bool,
):
    """Set the vault.yaml file on one or more hosts (atomic write).

    HOST_OR_FILE can be one or more hostnames, user@hostname values, or a file
    containing hosts (one per line).
    """
    hosts, host_file = resolve_host_args(host)
    args = HostSetVaultArgs(
        hosts=hosts,
        host_file=host_file,
        ssh_option=list(ssh_option) if ssh_option else None,
        connect_timeout=connect_timeout,
        timeout=timeout,
        audit_log=audit_log,
        json=json_output,
        workers=workers,
        from_file=from_file,
        validate=(not no_validate),
        mode=mode,
        owner=owner,
        group=group,
        no_backup=no_backup,
        reason=reason,
        confirm=confirm,
        ignore_state=ignore_state,
        force=force,
        no_audit=no_audit,
    )
    cmd_host_set_vault(args)


@cli.command("host-unset-override")
@click.argument("host", metavar="HOST_OR_FILE...", nargs=-1, required=True)
@common_options
@click.option(
    "--workers",
    type=int,
    default=10,
    show_default=True,
    help="Number of parallel workers for batch mode.",
)
@click.option(
    "--no-backup",
    is_flag=True,
    help="Do not create a backup before removing.",
)
@click.option(
    "--reason",
    help="Optional reason string for audit log.",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Apply the changes. Without this flag, a summary is printed and the command exits.",
)
@click.option(
    "--no-audit",
    is_flag=True,
    help="Skip the automatic gather host run after the override is removed.",
)
def host_unset_override(
    host: tuple[str, ...],
    ssh_option: tuple[str, ...],
    connect_timeout: int,
    timeout: int,
    audit_log: str | None,
    json_output: bool,
    workers: int,
    no_backup: bool,
    reason: str | None,
    confirm: bool,
    no_audit: bool,
):
    """Remove the override file from one or more hosts.

    HOST_OR_FILE can be one or more hostnames, user@hostname values, or a file
    containing hosts (one per line).
    """
    hosts, host_file = resolve_host_args(host)
    args = HostUnsetOverrideArgs(
        hosts=hosts,
        host_file=host_file,
        ssh_option=list(ssh_option) if ssh_option else None,
        connect_timeout=connect_timeout,
        timeout=timeout,
        audit_log=audit_log,
        json=json_output,
        workers=workers,
        no_backup=no_backup,
        reason=reason,
        confirm=confirm,
        no_audit=no_audit,
    )
    cmd_host_unset(args)


@cli.command("host-run-puppet")
@click.argument("host", metavar="HOST_OR_FILE...", nargs=-1, required=True)
@common_options(timeout_default=600)
@click.option(
    "--workers",
    type=int,
    default=10,
    show_default=True,
    help="Number of parallel workers for batch mode.",
)
@click.option(
    "--reason",
    help="Optional reason string for audit log.",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Apply the changes. Without this flag, a summary is printed and the command exits.",
)
@click.option(
    "--no-audit",
    is_flag=True,
    help="Skip the automatic gather host run after puppet completes.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress per-host puppet output; show progress bar + summary only. Implied by --json.",
)
def host_run_puppet(
    host: tuple[str, ...],
    ssh_option: tuple[str, ...],
    connect_timeout: int,
    timeout: int,
    audit_log: str | None,
    json_output: bool,
    workers: int,
    reason: str | None,
    confirm: bool,
    no_audit: bool,
    quiet: bool,
):
    """SSH to one or more hosts and run sudo run-puppet.sh, then refresh audit data.

    HOST_OR_FILE can be one or more hostnames, user@hostname values, or a file
    containing hosts (one per line).
    """
    hosts, host_file = resolve_host_args(host)
    args = HostRunPuppetArgs(
        hosts=hosts,
        host_file=host_file,
        ssh_option=list(ssh_option) if ssh_option else None,
        connect_timeout=connect_timeout,
        timeout=timeout,
        audit_log=audit_log,
        json=json_output,
        workers=workers,
        reason=reason,
        confirm=confirm,
        no_audit=no_audit,
        quiet=quiet,
    )
    cmd_host_run_puppet(args)


@cli.command("host-idle")
@click.argument("host", metavar="HOST")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
@click.option("--timeout", type=click.IntRange(min=1), default=30, show_default=True)
@click.option("--connect-timeout", type=click.IntRange(min=1), default=10, show_default=True)
@click.option("--ssh-option", multiple=True, help="Extra ssh option (repeatable).")
def host_idle(
    host: str,
    json_output: bool,
    timeout: int,
    connect_timeout: int,
    ssh_option: tuple[str, ...],
) -> None:
    """Check one Linux host's live worker state and Puppet activity.

    A snapshot only: this does not reserve the worker or start Puppet.
    Known idle and working results exit 0; unknown or failed checks exit 1.
    """
    cmd_host_idle(
        HostIdleArgs(
            host=host,
            json=json_output,
            timeout=timeout,
            connect_timeout=connect_timeout,
            ssh_option=list(ssh_option) or None,
        )
    )


@cli.command("host-reboot-if-idle")
@click.argument("host", metavar="HOST")
@common_options(timeout_default=30)
@click.option(
    "--confirm",
    is_flag=True,
    help="Run the live gwhc check and request reboot when it reports IDLE.",
)
def host_reboot_if_idle(
    host: str,
    ssh_option: tuple[str, ...],
    connect_timeout: int,
    timeout: int,
    audit_log: str | None,
    json_output: bool,
    confirm: bool,
) -> None:
    """Request reboot for one Linux host only when live gwhc reports IDLE.

    This best-effort operator command does not quarantine or recheck the worker.
    """
    args = HostRebootIfIdleArgs(
        host=host,
        ssh_option=list(ssh_option) if ssh_option else None,
        connect_timeout=connect_timeout,
        timeout=timeout,
        audit_log=audit_log,
        json=json_output,
        confirm=confirm,
    )
    cmd_host_reboot_if_idle(args)


@cli.command("gather-tc")
@click.argument("host", metavar="HOST_OR_FILE")
@click.option(
    "--verbose",
    "-v",
    count=True,
    help="Show verbose output (use -vv for very verbose, includes raw API responses)",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Single-line output.",
)
def gather_tc(host: str, verbose: int, quiet: bool):
    """Fetch TaskCluster worker data for hosts."""
    if verbose and quiet:
        raise click.UsageError("--verbose and --quiet are mutually exclusive")

    args = TcFetchArgs(host=host, verbose=verbose, quiet=quiet)
    cmd_tc_fetch(args)


@cli.command("gather-gh")
@click.option(
    "--override-delay",
    is_flag=True,
    help="Fetch regardless of throttle interval.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Single-line output.",
)
def gather_gh(override_delay: bool, quiet: bool):
    """Fetch GitHub branch refs for puppet repos."""
    cmd_gh_fetch(override_delay=override_delay, quiet=quiet)


@cli.command("gather")
@click.argument("host", metavar="HOST_OR_FILE")
@common_options
@click.option(
    "--workers",
    type=int,
    default=10,
    show_default=True,
    help="Number of parallel workers for gather host batch mode.",
)
@click.option(
    "--batch-timeout",
    type=int,
    default=600,
    show_default=True,
    help="Overall timeout for gather host batch operations in seconds.",
)
@click.option(
    "--verbose",
    "-v",
    count=True,
    help="Verbose output (use -vv for very verbose in gather tc).",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Single-line output for all sub-commands.",
)
@click.option(
    "--skip-host",
    is_flag=True,
    help="Skip gather host (SSH host introspection).",
)
@click.option(
    "--skip-tc",
    is_flag=True,
    help="Skip gather tc (TaskCluster worker data).",
)
@click.option(
    "--skip-gh",
    is_flag=True,
    help="Skip gather gh (GitHub branch refs).",
)
def gather(
    host: str,
    ssh_option: tuple[str, ...],
    connect_timeout: int,
    timeout: int,
    audit_log: str | None,
    json_output: bool,
    workers: int,
    batch_timeout: int,
    verbose: int,
    quiet: bool,
    skip_host: bool,
    skip_tc: bool,
    skip_gh: bool,
):
    """Run gather host, gather tc, and gather gh in sequence.

    HOST_OR_FILE can be a hostname, user@hostname, or a file containing hosts
    (one per line). Passed to gather host and gather tc; gather gh does not
    use it.

    Use --skip-host / --skip-tc / --skip-gh to omit individual steps.
    """
    if not skip_host:
        host_args = HostAuditArgs(
            host=host,
            ssh_option=list(ssh_option) if ssh_option else None,
            connect_timeout=connect_timeout,
            timeout=timeout,
            audit_log=audit_log,
            json=json_output,
            no_content=False,
            workers=workers,
            batch_timeout=batch_timeout,
            verbose=verbose >= 1,
            quiet=quiet,
        )
        cmd_host_audit(host_args)

    if not skip_tc:
        tc_args = TcFetchArgs(host=host, verbose=verbose, quiet=quiet)
        cmd_tc_fetch(tc_args)

    if not skip_gh:
        cmd_gh_fetch(override_delay=False, quiet=quiet)


@cli.command("maintain")
@click.option(
    "--audit-log",
    type=click.Path(),
    help="Path to FleetRoll directory (default: ~/.fleetroll/).",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Actually apply changes (without this, shows dry-run).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Rotate logs regardless of size threshold.",
)
def maintain(audit_log: str | None, confirm: bool, force: bool):
    """Maintain FleetRoll data files to prevent unbounded growth.

    Rotates audit log file and compacts SQLite database.
    Archives current logs with timestamp suffix and starts fresh.
    Does NOT backfill - new logs start empty on next write.
    Only rotates files >= 100 MB threshold (unless --force is used).
    """
    args = MaintainArgs(audit_log=audit_log, confirm=confirm, force=force)
    try:
        cmd_maintain(args)
    except (FleetRollError, UserError) as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)


@cli.command("data-freshness")
@click.argument("hosts_file", metavar="HOSTS_FILE", required=False)
@click.option(
    "--all",
    "-a",
    "all_hosts",
    is_flag=True,
    help="Check all hosts ever seen in the database (mutually exclusive with HOSTS_FILE).",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit machine-readable JSON to stdout.",
)
@click.option(
    "--require-fresh",
    is_flag=True,
    help="Exit 1 when either source does not meet the freshness policy.",
)
@click.option(
    "--stale-threshold",
    type=int,
    default=None,
    help=f"Override stale threshold in seconds (default: {3600}).",
)
@click.option(
    "--min-fresh-pct",
    type=int,
    default=80,
    show_default=True,
    help="Minimum percentage of hosts that must have fresh data from each source.",
)
def data_freshness(
    hosts_file: str | None,
    all_hosts: bool,
    json_output: bool,
    require_fresh: bool,
    stale_threshold: int | None,
    min_fresh_pct: int,
):
    """Report host and Taskcluster data freshness status.

    Reports host audit and Taskcluster observation freshness independently.
    A successful report exits 0 regardless of freshness; --require-fresh turns
    the combined freshness policy into an assertion suitable for automation.

    Specify either HOSTS_FILE (a list of expected hosts) or --all (every host
    in the database). Exactly one is required.
    """
    if hosts_file and all_hosts:
        raise click.UsageError("HOSTS_FILE and --all are mutually exclusive.")
    if not hosts_file and not all_hosts:
        raise click.UsageError("Specify either HOSTS_FILE or --all.")
    args = DataFreshnessArgs(
        hosts_file=hosts_file,
        all_hosts=all_hosts,
        stale_threshold=stale_threshold,
        min_fresh_pct=min_fresh_pct,
        json=json_output,
        require_fresh=require_fresh,
    )
    cmd_data_freshness(args)


@cli.command("note-add")
@click.argument("hostname")
@click.argument("note_text")
@click.option(
    "--notes-file",
    type=click.Path(),
    help="Path to notes JSONL file (default: data/notes.jsonl).",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit machine-readable JSON to stdout.",
)
def note_add(hostname: str, note_text: str, notes_file: str | None, json_output: bool):
    """Add a note for a host."""
    cmd_note_add(hostname, note_text, notes_file=notes_file, json_output=json_output)


@cli.command("note-clear")
@click.argument("hostname")
@click.option(
    "--reason",
    help="Optional reason for clearing notes.",
)
@click.option(
    "--notes-file",
    type=click.Path(),
    help="Path to notes JSONL file (default: data/notes.jsonl).",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit machine-readable JSON to stdout.",
)
def note_clear(hostname: str, reason: str | None, notes_file: str | None, json_output: bool):
    """Clear notes for a host (appends a tombstone record)."""
    cmd_note_clear(hostname, reason=reason, notes_file=notes_file, json_output=json_output)


@cli.command("show-notes")
@click.argument("hostname")
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Maximum number of notes to show (most recent).",
)
@click.option(
    "--notes-file",
    type=click.Path(),
    help="Path to notes JSONL file (default: data/notes.jsonl).",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit machine-readable JSON to stdout.",
)
@click.option(
    "--include-cleared",
    "-i",
    is_flag=True,
    help="Show all records including clear tombstones.",
)
def show_notes(
    hostname: str,
    limit: int | None,
    notes_file: str | None,
    json_output: bool,
    include_cleared: bool,
):
    """Show notes for a host."""
    cmd_show_notes(
        hostname,
        limit=limit,
        notes_file=notes_file,
        json_output=json_output,
        include_cleared=include_cleared,
    )


@cli.command("web")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", default=23847, show_default=True, help="TCP port.")
@click.option("--dev", is_flag=True, help="Enable CORS for Vite dev server.")
def web(host: str, port: int, dev: bool) -> None:
    """Start the fleetroll web interface."""
    args = WebArgs(host=host, port=port, dev=dev)
    cmd_web(host=args.host, port=args.port, dev=args.dev)


@click.group("hold")
def host_hold() -> None:
    """Show, set, or clear one Linux host's operator hold."""


def _hold_action(operation: str) -> click.Command:
    """Build hold actions with shared transport options and explicit write confirmation."""

    @click.command(
        name=operation, help=f"{operation.capitalize()} operator hold on one Linux host."
    )
    @click.argument("host", metavar="HOST")
    @common_options(timeout_default=30)
    def command(
        host: str,
        ssh_option: tuple[str, ...],
        connect_timeout: int,
        timeout: int,
        audit_log: str | None,
        json_output: bool,
        confirm: bool = False,
        reason: str | None = None,
    ) -> None:
        cmd_host_hold(
            HostHoldArgs(
                host=host,
                operation=operation,
                ssh_option=list(ssh_option) or None,
                connect_timeout=connect_timeout,
                timeout=timeout,
                audit_log=audit_log,
                json=json_output,
                confirm=confirm,
                reason=reason,
            )
        )

    if operation != "show":
        command = click.option(
            "--confirm", is_flag=True, help="Apply the change; otherwise preview without SSH."
        )(command)
        command = click.option("--reason", help="Optional reason recorded in the audit log.")(
            command
        )
    return command


for _operation in ("show", "set", "clear"):
    host_hold.add_command(_hold_action(_operation))

install_hierarchy(cli)["host"].add_command(host_hold)


def main():
    """Main entry point for the CLI."""
    try:
        cli()
    except CommandFailureError as e:
        # Command already printed its error message, just exit
        sys.exit(e.rc)
    except UserError as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(e.rc)
    except FleetRollError as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(2)
    except KeyboardInterrupt:
        click.echo("ERROR: Interrupted", err=True)
        sys.exit(130)
