"""Compare matching workloads between candidate and baseline worker pools.

Runs once. Uses recorded host configuration and fetches recent Taskcluster jobs
live. Refresh host observations with `fr gather all` before running. Host
observations older than --max-observation-gap (default one hour) prevent worker
job queries; this command does not refresh host observations.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from copy import copy

import click

from . import rollout_report as report
from .rollout_progress import RolloutProgress
from .task_name_cache import enrich_task_names


def parse_args(argv=None, *, prog=None):
    parser = argparse.ArgumentParser(description=__doc__, prog=prog)
    parser.add_argument(
        "--candidate-pool", required=True, help="Candidate provisioner/pool or bare pool."
    )
    parser.add_argument(
        "--baseline-pool", required=True, help="Baseline provisioner/pool or bare pool."
    )
    matching = parser.add_mutually_exclusive_group()
    matching.add_argument(
        "--strip-task-name-prefix",
        default=None,
        help="Explicitly remove this literal prefix once from task names on either side for matching.",
    )
    matching.add_argument(
        "--exact-task-names",
        action="store_true",
        help="Disable the default Replay: prefix fallback; match exact task names only.",
    )
    report.add_report_options(parser)
    args = parser.parse_args(argv)
    for side in ("candidate", "baseline"):
        try:
            pool = report.parse_worker_type(getattr(args, f"{side}_pool"))
        except ValueError as exc:
            parser.error(f"--{side}-pool: {exc}")
        setattr(args, f"{side}_pool", pool)
    if args.candidate_pool == args.baseline_pool:
        parser.error("candidate and baseline pools must differ")
    if args.strip_task_name_prefix == "":
        parser.error("--strip-task-name-prefix must be nonempty")
    return report.validate_report_options(args, parser=parser)


def pool_report(args, *, pool, clients, progress=None):
    selected = copy(args)
    selected.hosts, selected.role = [], None
    selected.provisioner, selected.worker_type = pool
    return report.build_report(
        selected, clients=clients, progress=progress, include_task_names=False
    )


def jobs_for(side):
    return [job for host in side["hosts"] for job in host["jobs"] if job["in_window"]]


def rate_comparison(candidate, *, baseline, min_finished):
    reasons = []
    if any(side["finished"] < min_finished for side in (candidate, baseline)):
        reasons.append(f"insufficient finished sample; minimum {min_finished} per side")
    if any(side["outcomes"]["unknown"] for side in (candidate, baseline)):
        reasons.append("unknown task outcomes")
    differences = {
        key + "_difference_pp": (candidate[key] - baseline[key]) * 100
        if candidate[key] is not None and baseline[key] is not None
        else None
        for key in ("failure_rate", "exception_rate")
    }
    result = "same observed failure and exception rates"
    if (
        differences["failure_rate_difference_pp"] is not None
        and differences["failure_rate_difference_pp"] > 0
    ):
        result = "higher candidate failure rate observed"
    elif (
        differences["exception_rate_difference_pp"] is not None
        and differences["exception_rate_difference_pp"] > 0
    ):
        result = "higher candidate exception rate observed"
    elif any(value is not None and value < 0 for value in differences.values()):
        result = "lower candidate failure or exception rate observed"
    return {
        "candidate": candidate,
        "baseline": baseline,
        **differences,
        "status": "unavailable" if reasons else "comparable",
        "observed_result": "comparison unavailable" if reasons else result,
        "reasons": reasons,
    }


def match_task_groups(grouped, *, replay_fallback):
    """Match exact names first, then unique unmatched Replay/original pairs."""
    exact = grouped["candidate"].keys() & grouped["baseline"].keys()
    pairs = [(key, key, "exact") for key in sorted(exact)]
    if not replay_fallback:
        return pairs
    unmatched = {}
    for label, groups in grouped.items():
        normalized = {}
        for role, name in groups.keys() - exact:
            stripped = name.removeprefix("Replay: ")
            if stripped:
                normalized.setdefault((role, stripped), []).append((role, name))
        unmatched[label] = normalized
    for key in sorted(unmatched["candidate"].keys() & unmatched["baseline"].keys()):
        candidate, baseline = unmatched["candidate"][key], unmatched["baseline"][key]
        if len(candidate) == len(baseline) == 1:
            pairs.append((candidate[0], baseline[0], "replay_prefix"))
    return pairs


def compare(sides, *, args):
    """Keep raw outcomes distinct from verified, role/name-matched evidence."""
    grouped = {}
    summaries = {}
    errors = []
    warnings = [
        "Recorded host population only; up to 20 recent runs per worker, not a complete pool history.",
        "Task names and roles match; task parameters, revisions and workload proportions may differ.",
        "Observed outcome rates do not establish causation, significance or performance equivalence.",
    ]
    if args.strip_task_name_prefix:
        warnings.append(
            f"Matching removes the literal task-name prefix {args.strip_task_name_prefix!r} once."
        )
    for label, side in sides.items():
        jobs = jobs_for(side)
        verified = [job for job in jobs if job["configuration"]]
        groups = {}
        for job in verified:
            if job.get("name"):
                name = job["name"].removeprefix(args.strip_task_name_prefix or "")
                if name:
                    key = (job["configuration"]["role"], name)
                    groups.setdefault(key, []).append(job)
        grouped[label] = groups
        summary = side["summary"]
        summaries[label] = {
            "pool": side["selection"]["provisioner"] + "/" + side["selection"]["worker_type"],
            "requested_hosts": summary["requested_hosts"],
            "queried_hosts": summary["queried_hosts"],
            "observed": report.outcome_summary(jobs),
            "attributed": report.outcome_summary(verified),
            "unattributed": summary["unattributed_outcomes"],
            "coverage": summary["coverage"],
            "cohorts": summary["cohorts"],
            "task_names": {"known": sum(bool(job.get("name")) for job in jobs), "total": len(jobs)},
            "task_mix": [
                {
                    "role": role,
                    "name": name,
                    "original_names": sorted({job["name"] for job in tasks}),
                    **report.outcome_summary(tasks),
                }
                for (role, name), tasks in sorted(groups.items())
            ],
            "workers": [
                {
                    "host": host["host"],
                    "observation_ts": host["observation_ts"],
                    "error": host["error"],
                    "query_ok": host["query_ok"],
                    "observed": report.outcome_summary(
                        [job for job in host["jobs"] if job["in_window"]]
                    ),
                    "attributed": report.outcome_summary(
                        [job for job in host["jobs"] if job["in_window"] and job["configuration"]]
                    ),
                }
                for host in side["hosts"]
            ],
        }
        errors.extend(f"{label}: {error}" for error in side["errors"])
        name_errors = sum(bool(job.get("name_error")) for job in jobs)
        if name_errors:
            errors.append(f"{label}: {name_errors} task metadata name queries unavailable")
        warnings.extend(f"{label}: {warning}" for warning in side["warnings"])
        fraction = summary["coverage"]["fraction"]
        if fraction is not None and fraction < args.min_coverage / 100:
            warnings.append(
                f"{label}: configuration attribution {fraction:.1%} below {args.min_coverage:g}%"
            )

    pairs = match_task_groups(
        grouped, replay_fallback=not args.exact_task_names and args.strip_task_name_prefix is None
    )
    matched_keys = {
        "candidate": {candidate for candidate, _, _ in pairs},
        "baseline": {baseline for _, baseline, _ in pairs},
    }
    fallback_count = sum(method == "replay_prefix" for _, _, method in pairs)
    if fallback_count:
        warnings.append(
            f"Replay: prefix fallback used for {fallback_count} task-name pair(s); original names retained."
        )
    matched = {
        label: report.outcome_summary([job for key in matched_keys[label] for job in groups[key]])
        for label, groups in grouped.items()
    }
    assessment = rate_comparison(
        matched["candidate"], baseline=matched["baseline"], min_finished=args.min_finished
    )
    for label, summary in summaries.items():
        if (
            not summary["requested_hosts"]
            or summary["queried_hosts"] / summary["requested_hosts"] < args.min_host_coverage / 100
        ):
            assessment["reasons"].append(f"{label}: insufficient host query coverage")
    if any(
        job.get("query_error") or job.get("name_error")
        for side in sides.values()
        for job in jobs_for(side)
    ):
        assessment["reasons"].append("task queries unavailable; inspect errors")
    if not pairs:
        assessment["reasons"].append("no verified tasks with matching names and roles")
    if assessment["reasons"]:
        assessment.update(status="unavailable", observed_result="comparison unavailable")
    assessment["tasks"] = [
        {
            "role": candidate[0],
            "name": candidate[1].removeprefix("Replay: ")
            if method == "replay_prefix"
            else candidate[1],
            "candidate_name": candidate[1],
            "baseline_name": baseline[1],
            "match_method": method,
            **rate_comparison(
                report.outcome_summary(grouped["candidate"][candidate]),
                baseline=report.outcome_summary(grouped["baseline"][baseline]),
                min_finished=args.min_finished,
            ),
        }
        for candidate, baseline, method in pairs
    ]
    for label, side in sides.items():
        summaries[label]["unmatched_verified"] = report.outcome_summary(
            [
                job
                for job in jobs_for(side)
                if job["configuration"]
                and (
                    job["configuration"]["role"],
                    (job.get("name") or "").removeprefix(args.strip_task_name_prefix or ""),
                )
                not in matched_keys[label]
            ]
        )
    return {
        "schema_version": 1,
        "window": next(iter(sides.values()))["window"],
        "population_source": "recorded hosts",
        "task_matching": {
            "key": "Puppet role and task metadata name",
            "strip_prefix": args.strip_task_name_prefix,
            "replay_fallback": not args.exact_task_names and args.strip_task_name_prefix is None,
            "fallback_matches": fallback_count,
        },
        "sides": summaries,
        "comparison": assessment,
        "errors": errors,
        "warnings": warnings,
        "reports": sides,
    }


def concise_worker_error(message):
    """Remove SDK transport dumps from worker errors while retaining their category."""
    if re.search(r"errorCode:\s+ResourceNotFound\b", message):
        return "Taskcluster worker not found (ResourceNotFound)"
    lines = message.strip().splitlines()
    return lines[0] if lines else "unspecified worker error"


def comparison_errors(result, *, verbose=False):
    """Group equal worker errors per side; retain every original in JSON/verbose."""
    if verbose:
        return result["errors"]
    grouped = {}
    covered = set()
    for label, side in result["sides"].items():
        for worker in side["workers"]:
            if worker["error"]:
                original = f"{label}: {worker['host']}: {worker['error']}"
                if original in result["errors"]:
                    covered.add(original)
                    key = label, concise_worker_error(worker["error"])
                    grouped.setdefault(key, []).append(worker["host"])
    messages = []
    for (label, error), hosts in grouped.items():
        examples = ", ".join(hosts[:3])
        if len(hosts) > 3:
            examples += f", +{len(hosts) - 3} more"
        messages.append(f"{label}: {len(hosts)} worker(s): {error} ({examples})")
    messages.extend(message for message in result["errors"] if message not in covered)
    return messages


def print_assessment(result, *, output):
    """Lead with evidence limits and keep all observed problems visible."""
    pair = result["comparison"]
    print(f"  MATCHED VERIFIED COMPARISON: {pair['observed_result']}", file=output)
    for reason in pair["reasons"]:
        print(f"    Blocker: {reason}", file=output)
    for label, side in result["sides"].items():
        matched = pair[label]
        fraction = side["coverage"]["fraction"]
        coverage = f"{fraction:.1%}" if fraction is not None else "unknown"
        print(
            f"    {label.capitalize()}: {matched['finished']} matched finished; "
            f"{matched['outcomes']['failed']} failed, {matched['outcomes']['exception']} exception; "
            f"attribution {coverage}; workers {side['queried_hosts']}/{side['requested_hosts']}",
            file=output,
        )
        observed = side["observed"]["outcomes"]
        if any(observed[key] for key in ("failed", "exception", "unknown")):
            print(
                f"      All observed: {observed['failed']} failed, "
                f"{observed['exception']} exception, {observed['unknown']} unknown "
                "(includes unmatched and unattributed tasks)",
                file=output,
            )
    if pair["status"] == "comparable":
        for key in ("failure_rate_difference_pp", "exception_rate_difference_pp"):
            if pair[key] is not None:
                print(
                    f"    {key.replace('_', ' ')} (candidate - baseline): {pair[key]:+.2f}",
                    file=output,
                )


def comparison_notices(messages, *, verbose=False):
    """Quiet known boilerplate in text; preserve unfamiliar warnings by default."""
    warnings, notes = [], []
    hidden = False
    for message in messages:
        body = message.removeprefix("candidate: ").removeprefix("baseline: ")
        if body.startswith(
            (
                "Compact configuration history:",
                "Additional historical observations loaded from ",
                "Excluded jobs are unknown configuration evidence,",
                "Replay: prefix fallback used for ",
                "Matching removes the literal task-name prefix ",
            )
        ):
            if verbose:
                notes.append(message)
            else:
                hidden = True
        elif body.startswith(
            (
                "Recorded host population only;",
                "Task names and roles match;",
                "Observed outcome rates do not establish ",
            )
        ) or re.match(r"\d+ host\(s\) have a full 20-task window;", body):
            notes.append(message)
        else:
            warnings.append(message)
    notices = [("Warning", message) for message in warnings]
    notices.extend(("Note", message) for message in notes)
    if hidden:
        notices.append(("Note", "Use --verbose for configuration provenance and full caveats."))
    return notices


def style_comparison_line(value, *, original, assessment):
    """Use color for hierarchy and evidence severity, without implying rollout approval."""
    line = original.lstrip()
    if line.startswith("MATCHED VERIFIED COMPARISON:"):
        if assessment["status"] != "comparable":
            foreground = "yellow"
        elif assessment["observed_result"].startswith("higher candidate"):
            foreground = "red"
        else:
            foreground = "cyan"
        return click.style(value, fg=foreground, bold=True)
    if line.startswith(("◈", "CANDIDATE:", "BASELINE:", "WORKLOAD COMPARISONS")):
        return click.style(value, fg="cyan", bold=True)
    if line.startswith("Error:"):
        return click.style(value, fg="red", bold=True)
    if line.startswith(("Blocker:", "Warning:")):
        return click.style(value, fg="yellow", bold=True)
    if line.startswith(("Note:", "Window:", "Use --", "Observed results only;")):
        return click.style(value, dim=True)
    # Preserve neutral zero counts. Match explicit outcome labels, never SHAs,
    # host IDs, percentages, or unlabelled numbers in diagnostic text.
    if line.startswith(
        ("Candidate:", "Baseline:", "All observed:", "Observed:", "Attributed:", "Unattributed:")
    ):
        value = re.sub(
            r"\b[1-9]\d* (?:failed|exception|unknown)\b",
            lambda match: click.style(
                match[0], fg="yellow" if match[0].endswith("unknown") else "red", bold=True
            ),
            value,
        )
    return value


def print_comparison(result, *, details=False, verbose=False, color=None, width=None):
    # Use the report renderer's wrapping and TTY newline handling.
    from io import StringIO

    output = StringIO()
    if color is None:
        color = report.report_color_enabled()
    print("◈ FLEETROLL / ROLLOUT COMPARE\n", file=output)
    print_assessment(result, output=output)
    pair = result["comparison"]
    print(file=output)
    print(f"  Window: {result['window']['since']} → {result['window']['until']}", file=output)
    if result["task_matching"]["strip_prefix"]:
        print(
            f"  Task-name prefix removed for matching: {result['task_matching']['strip_prefix']!r}",
            file=output,
        )
    if result["task_matching"]["fallback_matches"]:
        print(
            f"  Replay: prefix fallback matched {result['task_matching']['fallback_matches']} task-name pair(s).",
            file=output,
        )
    for label, side in result["sides"].items():
        print(f"\n  {label.upper()}: {side['pool']}", file=output)
        print(
            f"    Workers queried: {side['queried_hosts']}/{side['requested_hosts']}", file=output
        )
        for kind in ("observed", "attributed", "unattributed"):
            counts = side[kind]["outcomes"]
            print(
                f"    {kind.capitalize()}: "
                + (
                    ", ".join(
                        f"{counts[key]} {key}"
                        for key in report.OUTCOMES
                        if counts[key] or details or verbose
                    )
                    or "no tasks"
                ),
                file=output,
            )
            rate = side[kind]["success_rate"]
            if rate is not None and (details or verbose):
                print(f"      Finished-task success rate: {rate:.2%}", file=output)
        coverage = side["coverage"]
        print(
            f"    Configuration attribution: {coverage['classified']}/{coverage['in_window']}",
            file=output,
        )
        matched = result["comparison"][label]
        print(f"    Matched verified tasks: {matched['finished']} finished", file=output)
        if matched["failure_rate"] is not None:
            print(
                f"      Failure rate: {matched['failure_rate']:.2%}; "
                f"exception rate: {matched['exception_rate']:.2%}",
                file=output,
            )
        if details or verbose:
            for cohort in side["cohorts"]:
                print(
                    f"    Configuration: {cohort['configuration']['branch']}; "
                    f"applied revisions: {cohort['applied_revisions']}",
                    file=output,
                )
            for line in report.render_queue_counts(
                result["reports"][label]["queue_counts"], color=False
            ):
                print(f"    {line}", file=output)
            for worker in side["workers"]:
                counts = worker["observed"]["outcomes"]
                print(
                    f"    {worker['host']}: {worker['observed']['finished']} finished; "
                    f"{counts['failed']} failed; {counts['exception']} exceptions; "
                    f"{counts['running']} running; {worker['attributed']['finished']} attributed; "
                    f"observed {worker['observation_ts']}",
                    file=output,
                )
                if worker["error"]:
                    error = worker["error"] if verbose else concise_worker_error(worker["error"])
                    print(f"      Error: {error}", file=output)
            for task in side["task_mix"]:
                print(
                    f"    Task mix: {task['role']} / {task['name']}: {task['finished']} finished; "
                    f"{task['outcomes']['failed']} failed; {task['outcomes']['exception']} exceptions",
                    file=output,
                )
    if details or verbose:
        print("\n  WORKLOAD COMPARISONS", file=output)
        for task in pair["tasks"]:
            print(f"    {task['role']} / {task['name']}: {task['observed_result']}", file=output)
            for label in ("candidate", "baseline"):
                counts = task[label]["outcomes"]
                print(
                    f"      {label}: {task[label]['finished']} finished; "
                    f"{counts['failed']} failed; {counts['exception']} exceptions",
                    file=output,
                )
    notices = comparison_notices(result["warnings"], verbose=verbose)
    if notices:
        print(file=output)
    previous_label = None
    for label, message in notices:
        if previous_label is not None and label != previous_label:
            print(file=output)
        print(f"  {label}: {message}", file=output)
        previous_label = label
    if result["errors"]:
        print(file=output)
    for message in comparison_errors(result, verbose=verbose):
        print(f"  Error: {message}", file=output)
    if result["errors"] and not verbose:
        print("  Use --details for every worker; --verbose or --json for full errors.", file=output)
    print("\n  Observed results only; not automatic rollout approval.", file=output)
    report.write_report_text(
        output.getvalue(),
        stream=sys.stdout,
        width=max(20, width or min(shutil.get_terminal_size(fallback=(100, 24)).columns, 120)),
        color=color,
        population_headings=set(),
        line_style=lambda value, *, original: style_comparison_line(
            value, original=original, assessment=pair
        ),
    )
    if verbose:
        for side in result["reports"].values():
            report.print_report(side, verbose=True, color=color, width=width)


def main(argv=None, *, prog=None):
    args = parse_args(argv, prog=prog)
    try:
        with RolloutProgress(enabled=not args.json) as progress:
            clients = report.create_tc_clients(report.load_tc_credentials())
            sides = {}
            for label in ("candidate", "baseline"):
                pool = getattr(args, label + "_pool")
                progress.update(f"{label.capitalize()} pool: {'/'.join(pool)}")

                def update(message, *, completed=None, total=None, detail=None, label=label):
                    progress.update(
                        f"{label.capitalize()}: {message}",
                        completed=completed,
                        total=total,
                        detail=detail,
                    )

                sides[label] = pool_report(args, pool=pool, clients=clients, progress=update)
            candidate_hosts = {host["host"] for host in sides["candidate"]["hosts"]}
            if candidate_hosts & {host["host"] for host in sides["baseline"]["hosts"]}:
                raise ValueError(
                    "candidate and baseline recorded host selections overlap; refresh observations"
                )
            metadata = enrich_task_names(
                [host for side in sides.values() for host in side["hosts"]],
                clients=clients,
                workers=args.metadata_workers,
                progress=progress.update,
                cache_path=None
                if args.no_task_name_cache
                else args.db.with_suffix(".task-names.sqlite3"),
            )
            for side in sides.values():
                report.refresh_report_summary(side, args=args)
            progress.update("Matching workloads and comparing outcomes")
            result = compare(sides, args=args)
            result["task_name_lookups"] = metadata
            result["warnings"].extend(metadata["warnings"])
    except (OSError, ValueError, TypeError, sqlite3.Error, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_comparison(result, details=args.details, verbose=args.verbose)
    return int(
        bool(result["errors"])
        or any(
            side["observed"]["outcomes"][key]
            for side in result["sides"].values()
            for key in ("failed", "exception", "unknown")
        )
    )
