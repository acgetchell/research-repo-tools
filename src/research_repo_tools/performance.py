"""Composable CLI operations for comparison data and retained evidence."""

import argparse
import sys

from research_repo_tools import archives, criterion, evidence
from research_repo_tools.config import Config
from research_repo_tools.files import _paths_alias, replace_many


def _write_stdout(output: bytes) -> None:
    # Real stdout preserves UTF-8 bytes even on Windows. Tests or callers
    # may provide text-only streams, which receive the decoded text.
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(output)
    else:
        sys.stdout.write(output.decode("utf-8"))


def add_commands(groups) -> None:
    from research_repo_tools.performance_workflows import add_command

    commands = groups.add_parser("performance", help="compare timings and verify retained evidence").add_subparsers(dest="action", required=True)
    add_command(commands, "assets")
    add_command(commands, "baseline")
    compare = commands.add_parser("compare", help="write deterministic comparison JSON from two Criterion sample trees")
    compare.add_argument("baseline")
    compare.add_argument("current")
    compare.add_argument("--baseline-sample", default="new")
    compare.add_argument("--current-sample", default="new")
    compare.add_argument("--output")
    compare.add_argument("--format", choices=("csv", "json", "markdown"), default="json")
    compare.add_argument("--statistic", choices=("mean", "median"), default="median")
    compare.add_argument("--unit", choices=("ms", "ns", "s", "us"), default="ns")
    add_command(commands, "convert")
    add_command(commands, "export")
    extract = commands.add_parser("extract", help="safely extract a tar/ZIP asset into an absent directory")
    extract.add_argument("archive")
    extract.add_argument("destination")
    extract.add_argument("--sha256")
    fetch = commands.add_parser("fetch", help="download an HTTPS asset and verify its required digest")
    fetch.add_argument("url")
    fetch.add_argument("destination")
    fetch.add_argument("--sha256", required=True)
    add_command(commands, "measure")
    add_command(commands, "promote")
    publish = commands.add_parser("publish", help="publish a marked document section and figures from verified retained evidence")
    publish.add_argument("configuration", help="publication TOML path relative to the consumer root")
    mode = publish.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="validate and return 1 when outputs differ; write nothing")
    mode.add_argument("--preview", action="store_true", help="validate and print candidate diffs; write nothing")
    add_command(commands, "release-draft")
    add_command(commands, "release-upload")
    render = commands.add_parser("render", help="render verified retained comparison data without measuring")
    render.add_argument("payload")
    render.add_argument("manifest")
    render.add_argument("--output")
    verify = commands.add_parser("verify", help="verify an evidence envelope and its exact payload digest")
    verify.add_argument("payload")
    verify.add_argument("manifest")


def run(args: argparse.Namespace, settings: Config) -> int:
    from research_repo_tools import performance_workflows

    if args.action in performance_workflows.COMMANDS:
        return performance_workflows.run(args, settings)
    if args.action == "publish":
        from research_repo_tools.publication import preview_publication, publish_publication
        from research_repo_tools.publication_config import load_publication

        plan = load_publication(settings.root, args.configuration)
        if args.preview:
            _write_stdout(preview_publication(plan).encode("utf-8"))
        elif args.check:
            for path in plan.changed_paths:
                print(f"Stale publication: {path.relative_to(plan.root).as_posix()}")
            return int(bool(plan.changed_paths))
        else:
            changed = publish_publication(plan)
            for path in changed:
                print(f"Updated {path.relative_to(plan.root).as_posix()}")
            if not changed:
                print("Publication is already current.")
    elif args.action == "extract":
        archives.extract_archive(settings.path(args.archive), settings.path(args.destination), expected_sha256=args.sha256)
    elif args.action == "fetch":
        archives.download_asset(args.url, settings.path(args.destination), expected_sha256=args.sha256)
    elif args.action == "verify":
        result = evidence.load_evidence(settings.path(args.payload), settings.path(args.manifest))
        print(f"Verified envelope and payload SHA-256: {result.payload_schema}")
    else:
        if args.action == "compare":
            roots = (settings.path(args.baseline), settings.path(args.current))
            baseline = criterion.collect_sample(roots[0], args.baseline_sample, statistic=args.statistic, unit=args.unit)
            current = criterion.collect_sample(roots[1], args.current_sample, statistic=args.statistic, unit=args.unit)
            comparison = criterion.compare_samples(baseline, current)
            if not comparison.comparisons:
                raise ValueError("no common Criterion benchmarks; inspect the selected sample directories")
            output = (
                criterion.render_comparison(comparison).encode("utf-8")
                if args.format == "markdown"
                else criterion.serialize_comparison_csv(comparison)
                if args.format == "csv"
                else criterion.serialize_comparison(comparison)
            )
            if args.output:
                destination = settings.path(args.output).resolve()
                if any(_paths_alias(ancestor, root) for root in roots for ancestor in (destination, *destination.parents)):
                    raise ValueError("comparison output must be outside both Criterion input roots")
        else:
            retained = evidence.load_evidence(settings.path(args.payload), settings.path(args.manifest))
            if retained.payload_schema != criterion.COMPARISON_SCHEMA:
                raise ValueError(f"render requires {criterion.COMPARISON_SCHEMA}; use the consumer adapter for {retained.payload_schema}")
            output = criterion.render_comparison(criterion.parse_comparison(retained.payload)).encode("utf-8")
            if args.output and any(_paths_alias(settings.path(args.output), settings.path(path)) for path in (args.payload, args.manifest)):
                raise ValueError("render output must be distinct from retained evidence inputs")
        if args.output:
            replace_many({settings.path(args.output): output})
        else:
            _write_stdout(output)
    return 0
