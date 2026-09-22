"""Public CLI composition for complete configured performance workflows."""

import argparse
import tempfile
from pathlib import Path

from research_repo_tools import criterion, evidence, release_assets
from research_repo_tools.config import Config
from research_repo_tools.files import _validate_distinct_paths
from research_repo_tools.measurement import load_measurement, measure_checkout, measure_pair, resolve_revision
from research_repo_tools.performance_reports import render_report
from research_repo_tools.publication import _inventory, _path
from research_repo_tools.release_pairs import resolve_pair
from research_repo_tools.releases import published_releases

COMMANDS = {"assets", "baseline", "convert", "export", "measure", "promote", "release-draft", "release-upload"}


def add_command(commands, name: str) -> None:
    help_text = {
        "assets": "compare authenticated published release assets without measuring",
        "baseline": "measure a checked-out tag and package a shared release baseline",
        "convert": "convert verified historical CSV into new shared evidence paths",
        "export": "export verified comparison evidence as CSV",
        "measure": "measure configured benchmarks in explicitly permitted isolated worktrees",
        "promote": "promote/archive new evidence or rerender the retained current report offline",
        "release-draft": "require an existing mutable stable GitHub draft release",
        "release-upload": "attach verified inert bytes, reuse identical retries, and optionally publish",
    }
    command = commands.add_parser(name, help=help_text[name])
    if name in {"baseline", "convert", "measure", "promote"}:
        command.add_argument("configuration")
    if name in {"assets", "measure"}:
        command.add_argument("current", nargs="?", default="")
        command.add_argument("baseline", nargs="?", default="")
        command.add_argument("--order", choices=("published", "version"), default="published")
        command.add_argument("--repository", required=name == "assets")
    if name == "assets":
        command.add_argument("--asset-template", required=True, help="exact filename with one {tag} placeholder")
        command.add_argument("--legacy-configuration", help="explicit historical archive metadata layout")
    if name == "measure":
        command.add_argument("--mode", choices=("current-vs-latest", "explicit", "infer-release"))
        command.add_argument("--allow-git-mutations", action="store_true", help="permit temporary worktree creation/removal for measurement")
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
        command.add_argument("asset")
        command.add_argument("--publish", action="store_true", help="publish only after attachment verification and a fresh mutable-draft check")


def _outputs(args: argparse.Namespace, settings: Config, retained: evidence.Evidence, *, inputs: tuple[str, ...] = ()) -> None:
    root = settings.root
    targets = tuple(_path(root, name) for name in (args.payload, args.manifest, *([args.report] if args.report else [])))
    _validate_distinct_paths((*targets, *(_path(root, name) for name in inputs)))
    reports = {_path(root, args.report): render_report(retained)} if args.report else {}
    evidence.publish_evidence(retained, targets[0], targets[1], reports=reports)


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
    if args.action == "release-draft":
        release_assets.require_draft(release_assets.lookup_release(root, args.repository, args.tag))
    elif args.action == "release-upload":
        release_assets.publish_release_asset(root, args.repository, args.tag, settings.path(args.asset), publish=args.publish)
    elif args.action == "baseline":
        from research_repo_tools.release_discovery import normalize_tag

        tag = normalize_tag(args.tag)
        if resolve_revision(root, "HEAD") != resolve_revision(root, tag):
            raise ValueError("baseline packaging requires HEAD at the selected existing tag")
        config = load_measurement(root, args.configuration)
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
        if not args.allow_git_mutations:
            raise ValueError("measure requires --allow-git-mutations")
        if bool(args.current) != bool(args.baseline):
            raise ValueError("current and baseline tags must be provided together")
        from research_repo_tools.release_metadata import read_package_info

        mode = args.mode or ("explicit" if args.current else "infer-release")
        releases = () if mode == "explicit" else published_releases(root, repository=args.repository)
        package_tag = "v" + read_package_info(root).version
        pair = resolve_pair(mode, package_tag=package_tag, releases=releases, order=args.order, current=args.current or None, baseline=args.baseline or None)
        working_tree = mode == "current-vs-latest" or (mode == "infer-release" and not any(item.tag == pair.current for item in releases))
        configuration = load_measurement(root, args.configuration)
        protected = tuple(dict.fromkeys((args.configuration, *_inventory(root, configuration.sources), *_inventory(root, configuration.harness))))
        targets = (args.payload, args.manifest, *([args.report] if args.report else []))
        _validate_distinct_paths(tuple(_path(root, name) for name in (*targets, *protected)))
        retained = measure_pair(root, configuration, pair, working_tree=working_tree, allow_git_mutations=True)
        _outputs(args, settings, retained, inputs=protected)
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
        from research_repo_tools.performance_reports import load_report_plan
        from research_repo_tools.publication import preview_publication, publish_publication

        plan = load_report_plan(root, args.configuration, payload=args.payload, manifest=args.manifest)
        if args.preview:
            print(preview_publication(plan), end="")
        elif args.check:
            return int(bool(plan.changed_paths))
        else:
            for path in publish_publication(plan):
                print(f"Updated {path.relative_to(root).as_posix()}")
    return 0
