"""Composable CLI operations for comparison data and retained evidence."""

import argparse
import sys

from research_repo_tools import archives, criterion, evidence
from research_repo_tools.config import Config
from research_repo_tools.files import _paths_alias, replace_many


def add_commands(groups) -> None:
    commands = groups.add_parser("performance", help="compare timings and verify retained evidence").add_subparsers(dest="action", required=True)
    compare = commands.add_parser("compare", help="write deterministic comparison JSON from two Criterion sample trees")
    compare.add_argument("baseline")
    compare.add_argument("current")
    compare.add_argument("--baseline-sample", default="new")
    compare.add_argument("--current-sample", default="new")
    compare.add_argument("--output")
    compare.add_argument("--statistic", choices=("mean", "median"), default="median")
    compare.add_argument("--unit", choices=("ms", "ns", "s", "us"), default="ns")
    extract = commands.add_parser("extract", help="safely extract a tar/ZIP asset into an absent directory")
    extract.add_argument("archive")
    extract.add_argument("destination")
    extract.add_argument("--sha256")
    fetch = commands.add_parser("fetch", help="download an HTTPS asset and verify its required digest")
    fetch.add_argument("url")
    fetch.add_argument("destination")
    fetch.add_argument("--sha256", required=True)
    render = commands.add_parser("render", help="render verified retained comparison data without measuring")
    render.add_argument("payload")
    render.add_argument("manifest")
    render.add_argument("--output")
    verify = commands.add_parser("verify", help="verify an evidence envelope and its exact payload digest")
    verify.add_argument("payload")
    verify.add_argument("manifest")


def run(args: argparse.Namespace, settings: Config) -> int:
    if args.action == "extract":
        archives.extract_archive(settings.path(args.archive), settings.path(args.destination), expected_sha256=args.sha256)
    elif args.action == "fetch":
        archives.download_asset(args.url, settings.path(args.destination), expected_sha256=args.sha256)
    elif args.action == "verify":
        result = evidence.load_evidence(settings.path(args.payload), settings.path(args.manifest))
        print(f"Verified envelope and payload SHA-256: {result.payload_schema}")
    else:
        if args.action == "compare":
            baseline = criterion.collect_sample(settings.path(args.baseline), args.baseline_sample, statistic=args.statistic, unit=args.unit)
            current = criterion.collect_sample(settings.path(args.current), args.current_sample, statistic=args.statistic, unit=args.unit)
            comparison = criterion.compare_samples(baseline, current)
            if not comparison.comparisons:
                raise ValueError("no common Criterion benchmarks; inspect the selected sample directories")
            output = criterion.serialize_comparison(comparison)
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
            # Real stdout preserves UTF-8 bytes even on Windows. Tests or callers
            # may provide text-only streams, which receive the decoded text.
            if hasattr(sys.stdout, "buffer"):
                sys.stdout.buffer.write(output)
            else:
                sys.stdout.write(output.decode("utf-8"))
    return 0
