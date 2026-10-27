"""Composable CLI operations for comparison data and retained evidence."""

import argparse
import sys
from os.path import commonpath
from pathlib import Path

lazy from research_repo_tools import archives, criterion, evidence
lazy from research_repo_tools.config import Config
lazy from research_repo_tools.files import _paths_alias, replace_many


def _write_stdout(output: bytes) -> None:
    # Real stdout preserves UTF-8 bytes even on Windows. Tests or callers
    # may provide text-only streams, which receive the decoded text.
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(output)
    else:
        sys.stdout.write(output.decode("utf-8"))


def _run_report_paths(settings: Config, payload: str, manifest: str, output: str | None) -> dict[str, str]:
    from research_repo_tools.publication import _publication_name

    if output is None:
        return {}
    paths = [settings.path(name).resolve() for name in (output, payload, manifest)]
    try:
        root = Path(commonpath([path.parent for path in paths]))
        names = [_publication_name(path.relative_to(root).as_posix()) for path in paths]
    except ValueError:
        # Existing CLI paths may span Windows drives or use names outside the
        # portable publication contract. Keep those outputs explicitly unlinked.
        return {}
    return dict(zip(("report_path", "payload_path", "manifest_path"), names, strict=True))


def add_commands(groups) -> None:
    from research_repo_tools.performance_workflows import add_command

    commands = groups.add_parser("performance", help="compare timings and verify retained evidence").add_subparsers(dest="action", required=True)
    add_command(commands, "assets")
    add_command(commands, "baseline")
    command_timing = commands.add_parser("command-time", help="acquire exploratory external-command wall timings, separate from Criterion")
    command_timing.add_argument("configuration")
    command_timing.add_argument("--output", required=True, help="absent root-relative JSON report path; existing reports are preserved")
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
    discover = commands.add_parser("discover", help="run configured native list commands and retain semantic Criterion IDs")
    discover.add_argument("configuration")
    discover.add_argument("--output", required=True)
    add_command(commands, "export")
    extract = commands.add_parser("extract", help="safely extract a tar/ZIP asset into an absent directory")
    extract.add_argument("archive")
    extract.add_argument("destination")
    extract.add_argument("--sha256")
    fetch = commands.add_parser("fetch", help="download an HTTPS asset and verify its required digest")
    fetch.add_argument("url")
    fetch.add_argument("destination")
    fetch.add_argument("--sha256", required=True)
    host = commands.add_parser("host", help="capture versioned host observations without compatibility policy")
    host.add_argument("--output", required=True)
    add_command(commands, "measure")
    profile = commands.add_parser("profile", help="capture configured profiling context and native Rust/Cargo declarations")
    profile.add_argument("configuration")
    profile.add_argument("--output", required=True)
    add_command(commands, "promote")
    publish = commands.add_parser("publish", help="publish a marked document section and figures from verified retained evidence")
    publish.add_argument("configuration", help="publication TOML path relative to the consumer root")
    publish.add_argument("--artifacts-only", action="store_true", help="render only configured figures and CSV without editing a document")
    for phase in ("baseline", "current"):
        publish.add_argument(f"--{phase}-release", help=f"independent {phase} release assertion")
        publish.add_argument(f"--{phase}-revision", help=f"independent full {phase} commit assertion")
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
    verify.add_argument("--measurement", help="independent common measurement policy for complete-run eligibility")
    saved = commands.add_parser("verify-saved", help="validate complete fresh and saved Criterion samples and exact raw bytes")
    saved.add_argument("configuration")
    saved.add_argument("--criterion-dir", help="root-relative Criterion output directory (for example the release CRITERION_HOME)")
    saved.add_argument("--saved-sample", help="actual safe saved sample name for this release")


def run(args: argparse.Namespace, settings: Config) -> int:
    from research_repo_tools import performance_workflows

    if args.action in performance_workflows.COMMANDS:
        return performance_workflows.run(args, settings)
    if args.action == "command-time":
        from research_repo_tools.command_timing import publish_command_timing, summarize_commands

        _write_stdout(summarize_commands(publish_command_timing(settings.root, args.configuration, args.output)).encode("utf-8"))
        return 0
    if args.action == "discover":
        from research_repo_tools.criterion_inventory import discover_inventory, load_discovery, serialize_inventory
        from research_repo_tools.publication import _path

        output = _path(settings.root, args.output)
        if _paths_alias(output, _path(settings.root, args.configuration)):
            raise ValueError("inventory output must be distinct from configuration")
        if output.exists():
            raise ValueError("inventory output already exists; select a new path to preserve evidence")
        inventory = discover_inventory(settings.root, load_discovery(settings.root, args.configuration))
        replace_many({output: serialize_inventory(inventory)})
    elif args.action == "verify-saved":
        from research_repo_tools.criterion_baselines import load_saved_sample, validate_saved_sample
        from research_repo_tools.criterion_inventory import _directory

        plan = load_saved_sample(settings.root, args.configuration, saved_sample=args.saved_sample, criterion_dir=args.criterion_dir)
        result = validate_saved_sample(
            _directory(settings.root, plan.criterion_dir),
            plan.saved_sample,
            plan.policy,
            fresh_sample=plan.fresh_sample,
            raw_files=plan.raw_files,
            storage_paths=plan.storage_paths,
        )
        print(f"Verified {len(result.cases)} complete saved Criterion benchmarks")
    elif args.action in {"host", "profile"}:
        from research_repo_tools.files import _validate_distinct_paths
        from research_repo_tools.host_metadata import capture_host, capture_profile, serialize_host
        from research_repo_tools.publication import _path

        output = _path(settings.root, args.output)
        if args.action == "profile":
            import tomllib

            raw = tomllib.loads(_path(settings.root, args.configuration).read_text(encoding="utf-8"))
            protected = [args.configuration, *(raw[key] for key in ("rust-toolchain", "cargo-manifest") if isinstance(raw.get(key), str))]
            if isinstance(raw.get("measurement"), str):
                from research_repo_tools.measurement import load_measurement
                from research_repo_tools.publication import _inventory

                selected = load_measurement(settings.root, raw["measurement"])
                protected.extend((raw["measurement"], *_inventory(settings.root, selected.sources), *_inventory(settings.root, selected.harness)))
            protected = list(dict.fromkeys(protected))
            _validate_distinct_paths((output, *(_path(settings.root, name) for name in protected)))
            data = capture_profile(settings.root, args.configuration)
        else:
            data = serialize_host(capture_host(settings.root))
        replace_many({output: data})
    elif args.action == "publish":
        from research_repo_tools.publication import preview_publication, publish_publication
        from research_repo_tools.publication_config import load_publication

        identities = {}
        for phase in ("baseline", "current"):
            release, revision = getattr(args, f"{phase}_release"), getattr(args, f"{phase}_revision")
            if (release is None) != (revision is None):
                raise ValueError(f"--{phase}-release and --{phase}-revision must be supplied together")
            if release is not None:
                identities[phase] = (release, revision)
        plan = load_publication(settings.root, args.configuration, identities=identities, artifacts_only=args.artifacts_only)
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
        if args.measurement:
            from research_repo_tools.common_measurement import validate_run_policy
            from research_repo_tools.measurement import load_measurement

            validate_run_policy(result, load_measurement(settings.root, args.measurement))
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
            from research_repo_tools.complete_runs import RUN_SCHEMAS, render_run

            if retained.payload_schema not in {criterion.COMPARISON_SCHEMA, *RUN_SCHEMAS}:
                raise ValueError(f"render requires {criterion.COMPARISON_SCHEMA}; use the consumer adapter for {retained.payload_schema}")
            output = (
                render_run(retained, **_run_report_paths(settings, args.payload, args.manifest, args.output))
                if retained.payload_schema in RUN_SCHEMAS
                else criterion.render_comparison(criterion.parse_comparison(retained.payload)).encode("utf-8")
            )
            if args.output and any(_paths_alias(settings.path(args.output), settings.path(path)) for path in (args.payload, args.manifest)):
                raise ValueError("render output must be distinct from retained evidence inputs")
        if args.output:
            replace_many({settings.path(args.output): output})
        else:
            _write_stdout(output)
    return 0
