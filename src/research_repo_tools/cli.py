"""Common repository maintenance with standard defaults."""

import argparse
import subprocess
import sys
from dataclasses import replace
from io import TextIOWrapper
from pathlib import Path

from research_repo_tools import __version__, config
from research_repo_tools.process import ExecutableNotFoundError, format_exception_diagnostics


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--config", type=Path, help="TOML config; defaults to [tool.research-repo-tools] in pyproject.toml")
    result.add_argument("--root", type=Path, help="consumer root; defaults to the configuration directory")
    groups = result.add_subparsers(dest="group", required=True)
    changelog = groups.add_parser("changelog", help="generate, normalize, archive, and extract release history").add_subparsers(dest="action", required=True)
    for name, help_text in {
        "archive": "rotate completed minor series from the existing changelog",
        "check": "validate the whole changelog and all archives without changing files",
        "generate": "generate and normalize history, then rotate completed minor series",
        "normalize": "normalize the existing changelog without regenerating history",
        "notes": "extract release notes from the root changelog or an archive",
        "tag": "create a local annotated tag from validated release notes",
    }.items():
        command = changelog.add_parser(name, help=help_text, description=help_text)
        if name in {"notes", "tag"}:
            command.add_argument("tag")
        if name == "generate":
            command.add_argument("--tag")
            command.add_argument("--date")
        if name in {"generate", "tag"}:
            command.add_argument("--dry-run", action="store_true", help="print candidate output without publishing")
        if name == "tag":
            command.add_argument("--force", action="store_true", help="replace an existing local tag")
    coverage = groups.add_parser("coverage", help="summarize Cobertura coverage").add_subparsers(dest="action", required=True).add_parser("report")
    coverage.add_argument("--report", default="coverage/cobertura.xml")
    coverage.add_argument("--prefix", default="")
    coverage.add_argument("--limit", type=int)
    coverage.add_argument("--descending", action="store_true")
    deps = groups.add_parser("deps", help="maintain dependency and tool pins").add_subparsers(dest="action", required=True)
    uv = deps.add_parser("check-uv")
    uv.add_argument("--uv-executable")
    uv.add_argument("--output", help="validate captured output without running an executable")
    deps.add_parser("update-python")
    tools = deps.add_parser("update-tools")
    tools.add_argument("--dry-run", action="store_true")
    deps.add_parser("update-uv", help="upgrade uv through its owner and reconcile its project pin")
    docs = groups.add_parser("docs", help="check Markdown source files").add_subparsers(dest="action", required=True)
    docs.add_parser("check-lines").add_argument("files", nargs="+")
    notebooks = groups.add_parser("notebooks", help="validate, clean, execute, and synchronize selected notebooks").add_subparsers(dest="action", required=True)
    for action in ("check", "clear", "execute", "group", "lint", "sync"):
        command = notebooks.add_parser(action)
        if action not in ("group", "sync"):
            command.add_argument("files", nargs="+", help="explicit notebook paths relative to the consumer root")
        if action == "execute":
            command.add_argument("--cwd")
            command.add_argument("--output-dir")
            command.add_argument("--timeout", type=int, help="positive per-cell timeout in seconds")
        if action == "lint":
            command.add_argument("--timeout", type=int, default=30, help="positive per-checker timeout in seconds (default: 30)")
    release = groups.add_parser("release", help="check and synchronize release metadata").add_subparsers(dest="action", required=True)
    command = release.add_parser("check")
    command.add_argument("--final-release", action="store_true")
    command.add_argument("--previous-release")
    command = release.add_parser("update")
    command.add_argument("version")
    command.add_argument("--date")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--final-release", action="store_true")
    command.add_argument("--previous-release")
    review = groups.add_parser("review", help="run an opt-in CodeRabbit review").add_subparsers(dest="action", required=True)
    review.add_parser("branch", help="review branch and local changes against a verified base").add_argument("--base", default="origin/main")
    review.add_parser("uncommitted", help="review only staged, unstaged, and untracked changes")
    semgrep = groups.add_parser("semgrep", help="validate consumer rules and fixtures").add_subparsers(dest="action", required=True)
    semgrep.add_parser("check-fixtures")
    groups.add_parser("setup", help="install Just and declared tools, configure PATH, and sync the locked environment")
    templates = groups.add_parser("templates", help="print or explicitly create shared package resources")
    from research_repo_tools.changelog import TEMPLATES

    templates.add_argument("name", choices=TEMPLATES)
    templates.add_argument("--owner")
    templates.add_argument("--repository")
    templates.add_argument("--output", type=Path, help="create a new file; existing files are never overwritten")
    toolchain = groups.add_parser("toolchain", help="check, install, and select declared development tools").add_subparsers(dest="action", required=True)
    toolchain.add_parser("check", help="inspect installed tools without installing anything").add_argument("--json", action="store_true")
    toolchain.add_parser("run", help="run a command with verified managed tools; never installs").add_argument("command", nargs=argparse.REMAINDER)
    toolchain.add_parser("sync", help="install and verify declared versions").add_argument("--dry-run", action="store_true")
    toolchain.add_parser("upgrade", help="upgrade declared Cargo tools and publish verified pins").add_argument("--dry-run", action="store_true")
    return result


def run(args: argparse.Namespace, settings: config.Config) -> int:
    if args.group == "notebooks":
        if args.action == "group":
            print(settings.notebooks.group)
            return 0
        from research_repo_tools import notebooks

        if args.action == "sync":
            notebooks.sync(settings)
            return 0
        paths = [settings.path(path) for path in args.files]
        if args.action == "check":
            notebooks.check(paths, outputs=settings.notebooks.outputs)
        elif args.action == "clear":
            notebooks.clear(paths)
        elif args.action == "lint":
            from research_repo_tools.notebook_lint import lint

            return lint(settings, paths, timeout=args.timeout)
        else:
            return notebooks.execute(settings, paths, cwd=args.cwd, output_dir=args.output_dir, timeout=args.timeout)
        return 0
    if args.group == "setup":
        from research_repo_tools import toolchain, toolchain_config, toolchain_setup

        toolchain_setup.setup(toolchain.Runtime(toolchain_config.load(settings)))
        return 0
    if args.group == "toolchain":
        from research_repo_tools import toolchain, toolchain_config

        if args.action == "upgrade":
            from research_repo_tools.toolchain_upgrade import upgrade

            upgrade(settings, source=args.config, dry_run=args.dry_run)
            return 0
        plan = toolchain_config.load(settings)
        runtime = toolchain.Runtime(plan)
        if args.action == "run":
            return toolchain.run_command(runtime, args.command)
        if args.action == "sync" and not args.dry_run:
            runtime.sync()
        ok = toolchain.report(runtime.inspect(), json_output=getattr(args, "json", False))
        if args.action == "sync" and args.dry_run and not ok:
            print("Dry run: FAIL entries need installation or prerequisite repair; no changes made.")
            return 0
        return 0 if ok else 1
    if args.group == "docs":
        from research_repo_tools.markdown_lines import main

        return main([str(settings.path(path)) for path in args.files])
    if args.group == "coverage":
        from research_repo_tools.coverage import main

        arguments = ["--report", str(settings.path(args.report)), "--prefix", args.prefix]
        if args.limit is not None:
            arguments += ["--limit", str(args.limit)]
        if args.descending:
            arguments.append("--descending")
        return main(arguments, root=settings.root)
    if args.group == "templates":
        from research_repo_tools.changelog import template, write_template

        rendered = template(args.name, owner=args.owner, repository=args.repository)
        if args.output:
            write_template(settings.path(str(args.output)), rendered)
        else:
            print(rendered, end="")
        return 0
    if args.group == "semgrep":
        from research_repo_tools.semgrep import check

        return check(settings)
    if args.group == "deps":
        deps = settings.deps
        if args.action == "update-uv":
            from research_repo_tools.uv_update import update

            update(settings.root, uv=settings.executable(deps.uv))
            return 0
        if args.action == "update-python":
            from research_repo_tools.dependencies import main

            return main(
                [
                    "--pyproject",
                    str(settings.path(deps.pyproject)),
                    "--uv-executable",
                    settings.executable(deps.uv),
                ]
            )
        from research_repo_tools.tool_pins import check_uv, update

        if args.action == "check-uv":
            version = check_uv(executable=settings.executable(args.uv_executable or deps.uv), output=args.output)
            print(f"uv {version} satisfies the stable X.Y.Z contract")
            return 0

        changes = update(
            settings.path(deps.justfile),
            deps.tools,
            uv=settings.executable(deps.uv),
            dry_run=args.dry_run,
        )
        for pin, (old, new) in changes.items():
            print(f"{pin}: {old} -> {new}")
        return 0
    if args.group == "review":
        from research_repo_tools import review

        return review.run(settings.root, base=args.base if args.action == "branch" else None)
    if args.group == "release":
        policy = replace(settings.release, final_changelog=True) if args.final_release else settings.release
        from research_repo_tools import release_metadata, update_release

        if args.action == "check":
            return release_metadata.check(settings.root, policy=policy, previous_tag=args.previous_release)
        if not args.version:
            raise ValueError("release update requires a target version")
        from research_repo_tools.release_discovery import normalize_tag

        summary = update_release.update_release_version(
            settings.root,
            normalize_tag(args.version),
            previous_tag=args.previous_release,
            release_date=args.date,
            dry_run=args.dry_run,
            policy=policy,
        )
        for path in summary.changed_paths:
            print(f"{'Would update' if args.dry_run else 'Updated'}: {path.relative_to(settings.root)}")
        return 0
    if args.group == "changelog":
        from research_repo_tools import archive_changelog, changelog, postprocess_changelog

        path = settings.root / "CHANGELOG.md"
        if args.action == "check":
            changelog.check(settings)
            return 0
        if args.action == "generate":
            rendered = changelog.generate(settings, tag=args.tag, released=args.date, dry_run=args.dry_run)
            if args.dry_run:
                print(rendered, end="")
            return 0
        if args.action == "archive":
            archive_changelog.archive_changelog(path, settings.root / "docs/archives/changelog")
            return 0
        if args.action == "normalize":
            formatter = settings.path(settings.changelog.formatter) if settings.changelog.formatter is not None else None
            postprocess_changelog.postprocess(path, formatter=formatter)
            return 0
        if args.action == "notes":
            notes, _source, _heading = changelog.notes(settings, args.tag)
            print(notes, end="")
            return 0
        body = changelog.tag(settings, args.tag, force=args.force, dry_run=args.dry_run)
        if args.dry_run:
            print(body, end="")
        else:
            print(f"Created local annotated tag {args.tag}")
        return 0
    raise ValueError(f"unknown command group: {args.group}")


def main(argv: list[str] | None = None) -> int:
    # Reporting a completed mutation must not fail on an unencodable path.
    # Preserve the selected encoding while escaping unsupported characters.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")
    args = parser().parse_args(argv)
    try:
        return run(args, config.load(args.config, args.root))
    except ExceptionGroup as error:
        expected, unexpected = error.split((OSError, ValueError, RuntimeError, subprocess.SubprocessError))
        if expected is not None:
            print(f"research-repo-tools: {format_exception_diagnostics(expected)}", file=sys.stderr)
        if unexpected is not None:
            raise unexpected from None
        return 1
    except (ExecutableNotFoundError, OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"research-repo-tools: {format_exception_diagnostics(error, single_line=args.group == 'deps')}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
