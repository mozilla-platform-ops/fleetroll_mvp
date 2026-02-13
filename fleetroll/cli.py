"""FleetRoll CLI using Click."""

from __future__ import annotations

import logging
import shlex
import sys
from importlib.metadata import version

import click

from .cli_types import (
    HostAuditArgs,
    HostMonitorArgs,
    HostSetOverrideArgs,
    HostSetVaultArgs,
    HostUnsetOverrideArgs,
    MaintainArgs,
    OverrideShowArgs,
    TcFetchArgs,
    VaultShowArgs,
)
from .commands import (
    cmd_gh_fetch,
    cmd_host_audit,
    cmd_host_monitor,
    cmd_host_set,
    cmd_host_set_vault,
    cmd_host_unset,
    cmd_maintain,
    cmd_override_show,
    cmd_tc_fetch,
    cmd_vault_show,
)
from .exceptions import CommandFailureError, FleetRollError, UserError
from .ssh import audit_script_body

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
def common_options(func):
    """Decorator to add common options to all commands."""
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
        default=10,
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


@cli.command("host-audit")
@click.argument("host", metavar="HOST_OR_FILE")
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
def host_audit(
    host: str,
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
    """Audit a host (role + override presence + optionally contents).

    HOST_OR_FILE can be a hostname, user@hostname, or a file containing hosts
    (one per line for batch mode).
    """
    if verbose and quiet:
        raise click.UsageError("--verbose and --quiet are mutually exclusive")

    args = HostAuditArgs(
        host=host,
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
def host_monitor(
    host: str,
    audit_log: str | None,
    json_output: bool,
    once: bool,
    sort: str,
):
    """Monitor the latest audit record for a host (follows the audit log)."""
    args = HostMonitorArgs(
        host=host,
        audit_log=audit_log,
        json=json_output,
        once=once,
        sort=sort.lower(),
    )
    cmd_host_monitor(args)


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
    help="Wrap output as a 'sh -c' command (ssh-ready).",
)
def debug_host_script(
    no_content: bool,
    wrap: bool,
):
    """Print the remote host audit script used by host-audit."""
    body = audit_script_body(include_content=not no_content)
    if wrap:
        print("sh -c " + shlex.quote(body))
    else:
        print(body)


@cli.command("host-set-override")
@click.argument("host", metavar="HOST_OR_FILE")
@common_options
@click.option(
    "--workers",
    type=int,
    default=10,
    show_default=True,
    help="Number of parallel workers for batch mode.",
)
@click.option(
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
    default="root",
    show_default=True,
    help="File group.",
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
def host_set_override(
    host: str,
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
):
    """Set the override file on a host (atomic write).

    Contents must be provided via --from-file.
    """
    args = HostSetOverrideArgs(
        host=host,
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
    )
    cmd_host_set(args)


@cli.command("host-set-vault")
@click.argument("host", metavar="HOST_OR_FILE")
@common_options
@click.option(
    "--workers",
    type=int,
    default=10,
    show_default=True,
    help="Number of parallel workers for batch mode.",
)
@click.option(
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
    default="root",
    show_default=True,
    help="File group.",
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
def host_set_vault(
    host: str,
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
):
    """Set the vault.yaml file on a host (atomic write)."""
    args = HostSetVaultArgs(
        host=host,
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
    )
    cmd_host_set_vault(args)


@cli.command("host-unset-override")
@click.argument("host", metavar="HOST_OR_FILE")
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
def host_unset_override(
    host: str,
    ssh_option: tuple[str, ...],
    connect_timeout: int,
    timeout: int,
    audit_log: str | None,
    json_output: bool,
    workers: int,
    no_backup: bool,
    reason: str | None,
    confirm: bool,
):
    """Remove the override file from a host."""
    args = HostUnsetOverrideArgs(
        host=host,
        ssh_option=list(ssh_option) if ssh_option else None,
        connect_timeout=connect_timeout,
        timeout=timeout,
        audit_log=audit_log,
        json=json_output,
        workers=workers,
        no_backup=no_backup,
        reason=reason,
        confirm=confirm,
    )
    cmd_host_unset(args)


@cli.command("tc-fetch")
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
def tc_fetch(host: str, verbose: int, quiet: bool):
    """Fetch TaskCluster worker data for hosts."""
    if verbose and quiet:
        raise click.UsageError("--verbose and --quiet are mutually exclusive")

    args = TcFetchArgs(host=host, verbose=verbose, quiet=quiet)
    cmd_tc_fetch(args)


@cli.command("gh-fetch")
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
def gh_fetch(override_delay: bool, quiet: bool):
    """Fetch GitHub branch refs for puppet repos."""
    cmd_gh_fetch(override_delay=override_delay, quiet=quiet)


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
