"""Common repository maintenance with standard defaults."""

import argparse
import subprocess
import sys
from pathlib import Path

from research_repo_tools import __version__, config
from research_repo_tools.process import ExecutableNotFoundError, format_exception_diagnostics


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--config", type=Path, help="TOML config; defaults to [tool.research-repo-tools] in pyproject.toml")
    result.add_argument("--root", type=Path, help="consumer root; defaults to the configuration directory")
    groups = result.add_subparsers(dest="group", required=True)
    templates = groups.add_parser("templates", help="print or explicitly create shared package resources")
    from research_repo_tools.changelog import TEMPLATES

    templates.add_argument("name", choices=TEMPLATES)
    templates.add_argument("--owner")
    templates.add_argument("--repository")
    templates.add_argument("--output", type=Path, help="create a new file; existing files are never overwritten")
    docs = groups.add_parser("docs").add_subparsers(dest="action", required=True)
    docs.add_parser("check-lines").add_argument("files", nargs="+")
    coverage = groups.add_parser("coverage").add_subparsers(dest="action", required=True).add_parser("report")
    coverage.add_argument("--report", default="coverage/cobertura.xml")
    coverage.add_argument("--prefix", default="")
    coverage.add_argument("--limit", type=int)
    coverage.add_argument("--descending", action="store_true")
    semgrep = groups.add_parser("semgrep").add_subparsers(dest="action", required=True)
    semgrep.add_parser("check-fixtures")
    deps = groups.add_parser("deps").add_subparsers(dest="action", required=True)
    deps.add_parser("update-python")
    tools = deps.add_parser("update-tools")
    tools.add_argument("--dry-run", action="store_true")
    uv = deps.add_parser("check-uv")
    uv.add_argument("--uv-executable")
    uv.add_argument("--output", help="validate captured output without running an executable")
    release = groups.add_parser("release").add_subparsers(dest="action", required=True)
    release.add_parser("check").add_argument("--final-release", action="store_true")
    command = release.add_parser("update")
    command.add_argument("version")
    command.add_argument("--date")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--final-release", action="store_true")
    command.add_argument("--previous-release")
    changelog = groups.add_parser("changelog").add_subparsers(dest="action", required=True)
    for name in ("generate", "archive", "normalize", "notes", "tag"):
        command = changelog.add_parser(name)
        if name in {"notes", "tag"}:
            command.add_argument("tag")
        if name == "generate":
            command.add_argument("--tag")
            command.add_argument("--date")
        if name in {"generate", "tag"}:
            command.add_argument("--dry-run", action="store_true", help="print candidate output without publishing")
        if name == "tag":
            command.add_argument("--force", action="store_true", help="replace an existing local tag")
    return result


def run(args: argparse.Namespace, settings: config.Config) -> int:
    section = settings.section(args.group)
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
        if args.action == "update-python":
            from research_repo_tools.dependencies import main

            return main(["--pyproject", str(settings.path(section.get("pyproject", "pyproject.toml")))])
        from research_repo_tools.tool_pins import check_uv, update

        if args.action == "check-uv":
            version = check_uv(executable=args.uv_executable or section.get("uv", "uv"), output=args.output)
            print(f"uv {version} satisfies the stable X.Y.Z contract")
            return 0

        changes = update(
            settings.path(section.get("justfile", "justfile")),
            section.get("tools", {}),
            uv=section.get("uv", "uv"),
            dry_run=args.dry_run,
        )
        for pin, (old, new) in changes.items():
            print(f"{pin}: {old} -> {new}")
        return 0
    if args.group == "release":
        if args.final_release:
            section = {**section, "final-changelog": True}
        from research_repo_tools import release_metadata, update_release

        if args.action == "check":
            return release_metadata.check(settings.root, policy=section)
        if not args.version:
            raise ValueError("release update requires a target version")
        from research_repo_tools.release_discovery import normalize_tag

        summary = update_release.update_release_version(
            settings.root,
            normalize_tag(args.version),
            previous_tag=args.previous_release,
            release_date=args.date,
            dry_run=args.dry_run,
            policy=section,
        )
        for path in summary.changed_paths:
            print(f"{'Would update' if args.dry_run else 'Updated'}: {path.relative_to(settings.root)}")
        return 0
    if args.group == "changelog":
        from research_repo_tools import archive_changelog, changelog, postprocess_changelog

        path = settings.root / "CHANGELOG.md"
        if args.action == "generate":
            rendered = changelog.generate(settings, tag=args.tag, released=args.date, dry_run=args.dry_run)
            if args.dry_run:
                print(rendered, end="")
            return 0
        if args.action == "archive":
            archive_changelog.archive_changelog(path, settings.root / "docs/archives/changelog")
            return 0
        if args.action == "normalize":
            formatter = settings.path(section["formatter"]) if "formatter" in section else None
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
