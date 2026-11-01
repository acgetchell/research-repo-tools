"""Public CLI composition for complete configured performance workflows."""

import argparse
import tempfile
from collections.abc import Callable
from pathlib import Path

lazy from research_repo_tools import criterion, evidence, release_assets
lazy from research_repo_tools.config import Config
lazy from research_repo_tools.files import _validate_distinct_paths
lazy from research_repo_tools.measurement import MeasurementConfig, load_measurement, measure_checkout, resolve_revision
lazy from research_repo_tools.performance_reports import render_report
lazy from research_repo_tools.publication import _inventory, _path
lazy from research_repo_tools.release_pairs import resolve_pair
lazy from research_repo_tools.releases import published_releases

COMMANDS = {"assets", "baseline", "convert", "export", "measure", "prepare", "promote", "readme", "release", "release-draft", "release-upload"}


def add_command(commands, name: str) -> None:
    help_text = {
        "assets": "compare authenticated published release assets without measuring",
        "baseline": "measure a checked-out tag and package a shared release baseline",
        "convert": "convert verified historical CSV into new shared evidence paths",
        "export": "export verified comparison evidence as CSV",
        "measure": "measure configured benchmarks in read-only exports or authorized worktrees",
        "prepare": "save independent release assertions under ignored scratch storage",
        "promote": "promote/archive new evidence or rerender the retained current report offline",
        "readme": "publish README from retained evidence and independent prepared or explicit assertions",
        "release": "measure a prepared pair and retain/promote its report; publish README separately",
        "release-draft": "require an existing mutable stable GitHub draft release",
        "release-upload": "attach verified inert bytes, reuse identical retries, and optionally publish",
    }
    command = commands.add_parser(name, help=help_text[name])
    if name in {"baseline", "convert", "measure", "prepare", "promote", "readme", "release"}:
        command.add_argument("configuration")
    if name in {"prepare", "release"}:
        command.add_argument("--allow-git-mutations", action="store_true", help="authorize only requested tag fetching or configured temporary worktrees")
    if name in {"measure", "release"}:
        command.add_argument("--checkpoint", help="ignored root-relative checkpoint directory; schema 5 only")
        command.add_argument("--progress", choices=("human", "json", "none"), default="human", help="advisory status on stderr")
        command.add_argument("--progress-cadence", type=float, default=10.0, help="heartbeat interval in seconds, at least 1")
        command.add_argument("--resume", action="store_true", help="validate and reuse completed units from --checkpoint")
    if name == "prepare":
        command.add_argument("--fetch-tags", action="store_true", help="fetch repository tags before selection; also requires --allow-git-mutations")
    if name == "readme":
        for phase in ("baseline", "current"):
            command.add_argument(f"--{phase}-release")
            command.add_argument(f"--{phase}-revision")
        mode = command.add_mutually_exclusive_group()
        mode.add_argument("--check", action="store_true")
        mode.add_argument("--preview", action="store_true")
    if name in {"assets", "measure"}:
        command.add_argument("current", nargs="?", default="")
        command.add_argument("baseline", nargs="?", default="")
        command.add_argument("--order", choices=("published", "version"), default="published")
        command.add_argument("--repository", required=name == "assets")
    if name == "assets":
        command.add_argument("--asset-template", required=True, help="exact filename with one {tag} placeholder")
        command.add_argument("--legacy-configuration", help="explicit historical archive metadata layout")
    if name == "measure":
        command.add_argument("--allow-git-mutations", action="store_true", help="permit temporary worktree creation/removal for measurement")
        command.add_argument("--backend", choices=("export", "worktree"), help="source isolation backend; must agree with complete declaration")
        command.add_argument("--mode", choices=("current-vs-latest", "explicit", "infer-release"))
    if name in {"assets", "convert", "measure"}:
        command.add_argument("--payload", required=True)
        command.add_argument("--manifest", required=True)
        command.add_argument("--report")
    if name == "baseline":
        command.add_argument("tag")
        command.add_argument("output")
    if name in {"export", "convert"}:
        command.add_argument("source_payload")
        command.add_argument("source_manifest")
    if name == "export":
        command.add_argument("output")
    if name == "promote":
        command.add_argument("--payload")
        command.add_argument("--manifest")
        mode = command.add_mutually_exclusive_group()
        mode.add_argument("--check", action="store_true")
        mode.add_argument("--preview", action="store_true")
    if name in {"release-draft", "release-upload"}:
        command.add_argument("repository")
        command.add_argument("tag")
    if name == "release-upload":
        command.description = (
            "Without --target, capture the current remote tag commit for this invocation. "
            "This cannot detect tag movement before invocation or bind an earlier build. "
            "To bind an expected commit, run release-draft --commit before the build "
            "and pass its trusted JSON output with --target."
        )
        command.add_argument("asset")
        command.add_argument("--publish", action="store_true", help="publish only after attachment verification and a fresh mutable-draft check")
        command.add_argument("--target", help="trusted JSON target captured by release-draft --commit; repository/tag must match")
    if name == "release-draft":
        command.add_argument("--commit", help="expected full tag commit; enables verified target output")
        command.add_argument("--expected-title", help="assert an exact release title, for example the tag")
        command.add_argument("--output", help="write target JSON here; otherwise print JSON when --commit is supplied")


def _outputs(
    args: argparse.Namespace, settings: Config, retained: evidence.Evidence, *, inputs: tuple[str, ...] = (), verify: Callable[[], None] | None = None
) -> None:
    root = settings.root
    targets = tuple(_path(root, name) for name in (args.payload, args.manifest, *([args.report] if args.report else [])))
    _validate_distinct_paths((*targets, *(_path(root, name) for name in inputs)))
    reports = {}
    if args.report:
        from research_repo_tools.complete_runs import RUN_SCHEMAS, render_run

        report = (
            render_run(retained, report_path=args.report, payload_path=args.payload, manifest_path=args.manifest)
            if retained.payload_schema in RUN_SCHEMAS
            else render_report(retained)
        )
        reports[_path(root, args.report)] = report
    evidence.publish_evidence(retained, targets[0], targets[1], reports=reports, **({"verify": verify} if verify is not None else {}))


def _asset_pair(args: argparse.Namespace, settings: Config) -> evidence.Evidence:
    if bool(args.current) != bool(args.baseline):
        raise ValueError("current and baseline tags must be provided together")
    releases = () if args.current else published_releases(settings.root, repository=args.repository)
    pair = resolve_pair(
        "explicit" if args.current else "published-latest",
        package_tag=args.current or "v0.0.0",
        releases=releases,
        order=args.order,
        current=args.current or None,
        baseline=args.baseline or None,
    )
    if args.asset_template.count("{tag}") != 1 or "{" in args.asset_template.replace("{tag}", "") or "}" in args.asset_template.replace("{tag}", ""):
        raise ValueError("asset template requires exactly one {tag} placeholder and no other braces")
    samples = {}
    sources = {}
    legacy = _path(settings.root, args.legacy_configuration).read_bytes() if args.legacy_configuration else None
    with tempfile.TemporaryDirectory(prefix="research-release-comparison-") as temporary:
        for side, tag in (("baseline", pair.baseline), ("current", pair.current)):
            archive = Path(temporary) / f"{side}.tar.gz"
            release_assets.download_release_asset(settings.root, args.repository, tag, args.asset_template.replace("{tag}", tag), archive)
            samples[side], sources[side] = release_assets.read_baseline(archive, legacy_configuration=legacy, expected_tag=tag)
            if dict(sources[side].context).get("release") != tag:
                raise ValueError(f"{side} asset provenance does not identify the requested release")
    comparison = criterion.compare_samples(samples["baseline"], samples["current"])
    if not comparison.comparisons:
        raise ValueError("release assets have no common benchmark rows")
    return evidence.Evidence(criterion.serialize_comparison(comparison), criterion.COMPARISON_SCHEMA, tuple(sources.items()))


def run(args: argparse.Namespace, settings: Config) -> int:
    root = settings.root
    if args.action in {"prepare", "readme", "release"}:
        from research_repo_tools.performance import _write_stdout
        from research_repo_tools.performance_release import load_performance_release, load_readme_plan, measure_release, prepare_performance
        from research_repo_tools.publication import preview_publication, publish_publication

        workflow = load_performance_release(root, args.configuration)
        if args.action == "prepare":
            record = prepare_performance(root, workflow, fetch_tags=args.fetch_tags, allow_git_mutations=args.allow_git_mutations)
            _write_stdout(
                (
                    f"Current: {record.pair.current} {record.current_revision}\nBaseline: {record.pair.baseline} {record.baseline_revision}\n"
                    f"Saved preparation: {workflow.preparation}\n"
                ).encode("utf-8")
            )
        elif args.action == "release":
            measure_release(
                root, workflow, allow_git_mutations=args.allow_git_mutations, checkpoint=args.checkpoint, resume=args.resume, progress=_progress(args)
            )
            print("Retained measurement and report. README publication is a separate step.")
        else:
            values = [getattr(args, f"{phase}_{field}") for phase in ("current", "baseline") for field in ("release", "revision")]
            if any(value is not None for value in values) and not all(value is not None for value in values):
                raise ValueError("README publication requires all four explicit release/revision assertions")
            identities = {"current": (values[0], values[1]), "baseline": (values[2], values[3])} if any(value is not None for value in values) else None
            plan = load_readme_plan(root, workflow, identities=identities)
            if args.preview:
                _write_stdout(preview_publication(plan).encode("utf-8"))
            elif args.check:
                return int(bool(plan.changed_paths))
            else:
                publish_publication(plan)
                print("README publication is current.")
        return 0
    if args.action == "release-draft":
        if args.output is not None and not args.output.strip():
            raise ValueError("--output must be a nonempty path")
        if args.commit is not None:
            from research_repo_tools.performance import _write_stdout

            target = release_assets.preflight_release_target(root, args.repository, args.tag, args.commit, expected_title=args.expected_title)
            payload = release_assets.serialize_release_target(target)
            if args.output is not None:
                from research_repo_tools.files import replace_many

                replace_many({settings.path(args.output): payload})
            else:
                _write_stdout(payload)
        else:
            if args.output is not None or args.expected_title is not None:
                raise ValueError("release target output/title assertions require --commit")
            release_assets.require_draft(release_assets.lookup_release(root, args.repository, args.tag))
    elif args.action == "release-upload":
        if args.target is not None and not args.target.strip():
            raise ValueError("--target must be a nonempty path")
        target = release_assets.parse_release_target(settings.path(args.target).read_bytes()) if args.target is not None else None
        release_assets.publish_release_asset(root, args.repository, args.tag, settings.path(args.asset), target=target, publish=args.publish)
    elif args.action == "baseline":
        from research_repo_tools.release_discovery import normalize_tag

        tag = normalize_tag(args.tag)
        if resolve_revision(root, "HEAD") != resolve_revision(root, tag):
            raise ValueError("baseline packaging requires HEAD at the selected existing tag")
        config = load_measurement(root, args.configuration)
        if not isinstance(config, MeasurementConfig):
            raise ValueError("baseline assets require schema-1 measurement; retain complete runs through measure/promote")
        from research_repo_tools.process import run_git_bytes
        from research_repo_tools.publication import verify_tagged_files

        # HEAD alone does not prove that a tagged checkout's files are clean.
        run_git_bytes(["--no-pager", "diff", "--quiet", "HEAD", "--"], cwd=root)
        inputs = tuple(sorted(set(_inventory(root, config.sources) + _inventory(root, config.harness))))
        verify_tagged_files(root, tag, {name: _path(root, name).read_bytes() for name in inputs}, revision=resolve_revision(root, "HEAD"))
        protected = tuple(dict.fromkeys((args.configuration, *inputs)))
        _validate_distinct_paths(tuple(_path(root, name) for name in (args.output, *protected)))
        sample, source = measure_checkout(root, config, tag, mode="tag")
        release_assets.package_baseline(sample, source, _path(root, args.output))
    elif args.action == "measure":
        if bool(args.current) != bool(args.baseline):
            raise ValueError("current and baseline tags must be provided together")
        from research_repo_tools.performance_acquisition import measure_and_retain
        from research_repo_tools.performance_release import select_measurement_pair

        pair, working_tree = select_measurement_pair(
            root,
            args.configuration,
            repository=args.repository,
            mode=args.mode or ("explicit" if args.current else "infer-release"),
            order=args.order,
            current=args.current or None,
            baseline=args.baseline or None,
        )
        measure_and_retain(
            root,
            args.configuration,
            pair,
            payload=args.payload,
            manifest=args.manifest,
            report=args.report,
            working_tree=working_tree,
            allow_git_mutations=args.allow_git_mutations,
            backend=args.backend,
            checkpoint=args.checkpoint,
            resume=args.resume,
            progress=_progress(args),
        )
    elif args.action == "assets":
        _outputs(args, settings, _asset_pair(args, settings), inputs=(args.legacy_configuration,) if args.legacy_configuration else ())
    elif args.action == "convert":
        from research_repo_tools.legacy_evidence import convert_csv

        retained = convert_csv(
            _path(root, args.source_payload).read_bytes(), _path(root, args.source_manifest).read_bytes(), _path(root, args.configuration).read_bytes()
        )
        _outputs(args, settings, retained, inputs=(args.configuration, args.source_payload, args.source_manifest))
    elif args.action == "export":
        from research_repo_tools.files import replace_many

        paths = tuple(_path(root, name) for name in (args.source_payload, args.source_manifest, args.output))
        _validate_distinct_paths(paths)
        retained = evidence.load_evidence(paths[0], paths[1])
        if retained.payload_schema != criterion.COMPARISON_SCHEMA:
            raise ValueError("CSV export requires shared comparison evidence")
        replace_many({paths[2]: criterion.serialize_comparison_csv(criterion.parse_comparison(retained.payload))})
    elif args.action == "promote":
        from research_repo_tools.performance import _write_stdout
        from research_repo_tools.performance_reports import load_report_plan
        from research_repo_tools.publication import preview_publication, publish_publication

        if args.check and args.payload is None and args.manifest is None:
            from research_repo_tools.report_inventory import check_report

            checked = check_report(root, args.configuration)
            print(f"Report inventory: {checked.status}.")
            return int(checked.status == "stale")
        plan = load_report_plan(root, args.configuration, payload=args.payload, manifest=args.manifest)
        if args.preview:
            _write_stdout(preview_publication(plan).encode("utf-8"))
        elif args.check:
            return int(bool(plan.changed_paths))
        else:
            for path in publish_publication(plan):
                print(f"Updated {path.relative_to(root).as_posix()}")
    return 0


def _progress(args: argparse.Namespace):
    from research_repo_tools.performance_progress import ProgressReporter

    return ProgressReporter(mode=args.progress, cadence=args.progress_cadence)
