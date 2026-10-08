"""Common repository maintenance with standard defaults."""

import argparse
import os
import subprocess
import sys
from dataclasses import replace
from io import TextIOWrapper
from pathlib import Path

from research_repo_tools import __version__, config
from research_repo_tools.performance import _write_stdout
from research_repo_tools.process import ExecutableNotFoundError, format_exception_diagnostics


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--config", type=Path, help="TOML config; defaults to [tool.research-repo-tools] in pyproject.toml")
    result.add_argument("--root", type=Path, help="consumer root; defaults to the configuration directory")
    groups = result.add_subparsers(dest="group", required=True)
    actions = groups.add_parser("actions", help="check external allowlists and update explicitly selected commit pins").add_subparsers(
        dest="action", required=True
    )
    for name in ("allowlist", "update"):
        command = actions.add_parser(name)
        command.add_argument("--policy", type=Path, required=True, help="consumer selected-actions JSON or Actions update TOML")
        command.add_argument("paths", nargs="+", help="explicit workflow files or directories")
        if name == "update":
            mode = command.add_mutually_exclusive_group()
            mode.add_argument("--check", action="store_true", help="preview and return nonzero when selected pins differ")
            mode.add_argument("--dry-run", action="store_true", help="preview resolved pins without writing")
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
    ci = groups.add_parser("ci", help="export validated CI environment values").add_subparsers(dest="action", required=True)
    command = ci.add_parser("export", help="append checked single-line values to GITHUB_ENV")
    command.add_argument("names", nargs="+")
    command.add_argument("--file", type=Path, help="command file; defaults to GITHUB_ENV")
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
    deps.add_parser("update-uv", help="upgrade uv through its owner and reconcile its project pin").add_argument("--dry-run", action="store_true")
    docs = groups.add_parser("docs", help="check Markdown source files").add_subparsers(dest="action", required=True)
    docs.add_parser("check-lines").add_argument("files", nargs="+")
    files = groups.add_parser("files", help="select tracked and nonignored inputs and batch commands").add_subparsers(dest="action", required=True)
    for action in ("check-lines", "list", "run"):
        command = files.add_parser(action)
        command.add_argument("--include", action="append", default=None if action == "check-lines" else [], help="Git pathspec; may be repeated")
        command.add_argument("--exclude", action="append", default=None if action == "check-lines" else [], help="POSIX glob; may be repeated")
        if action == "check-lines":
            command.add_argument("--limit", type=int, help="positive raw Unicode character limit; overrides text.line-limit")
        elif action == "list":
            command.add_argument("--null", action="store_true", help="separate file names with NUL")
        else:
            command.add_argument("--batch-size", type=int, default=100)
            command.add_argument("--timeout", type=float, default=300)
            command.add_argument("command", nargs=argparse.REMAINDER)
    notebooks = groups.add_parser("notebooks", help="inspect, review, validate, clean, execute, launch, reset, and synchronize notebooks").add_subparsers(
        dest="action", required=True
    )
    for action in ("advise", "check", "clear", "execute", "group", "inspect", "launch", "lint", "reset", "sync"):
        command = notebooks.add_parser(action)
        if action not in ("group", "launch", "sync"):
            command.add_argument("files", nargs="*" if action == "reset" else "+", help="literal paths relative to the consumer root")
        if action == "execute":
            command.add_argument("--cwd")
            command.add_argument("--output-dir")
            command.add_argument("--timeout", type=int, help="positive per-cell timeout in seconds")
        if action == "inspect":
            command.add_argument("--json", action="store_true", help="emit the versioned inspection schema")
            command.add_argument("--no-preview", action="store_true", help="omit source text previews")
        if action == "advise":
            command.add_argument("--strict", action="store_true", help="fail on advisory warnings as well as errors")
        if action == "launch":
            command.add_argument("--browser", action=argparse.BooleanOptionalAction, default=None, help="open a browser (default: configured policy, false)")
            command.add_argument("--scratch-dir", help="consumer-relative directory for private Jupyter/IPython/Matplotlib state")
        if action == "lint":
            command.add_argument("--id-pattern", help="full-match regular expression for existing cell IDs; overrides configured policy")
        if action == "reset":
            command.add_argument(
                "--apply", action="store_true", help="explicitly restore source notebooks and delete declared scratch/checkpoints; default: preview"
            )
            command.add_argument("--revision", help="explicit Git revision to restore; default: index")
        if action in ("advise", "lint"):
            command.add_argument("--timeout", type=int, default=30, help="positive per-checker timeout in seconds (default: 30)")
    from research_repo_tools.performance import add_commands

    papers = groups.add_parser("papers", help="explicit reproducible dates, optional PDF checks and normalization").add_subparsers(dest="action", required=True)
    for action in ("check", "normalize", "source-date"):
        command = papers.add_parser(action)
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument("path", nargs="?", help="TeX source for source-date; PDF for check/normalize")
        source.add_argument("--paper", help="named consumer papers.documents declaration")
        if action != "source-date":
            command.add_argument("--min-pages", type=int)
            command.add_argument("--require-text", action="append")
            command.add_argument("--forbid-text", action="append")
            command.add_argument("--reference")
        if action == "normalize":
            command.add_argument("--tex")
            command.add_argument("--identity", help="stable consumer identity, independent of filesystem paths")
            command.add_argument("--output", help="explicit destination; defaults to in-place normalization")
    add_commands(groups)
    python = groups.add_parser("python", help="check, fix, or typecheck the complete Python inventory").add_subparsers(dest="action", required=True)
    for action in ("check", "fix", "typecheck"):
        python.add_parser(action).add_argument("--timeout", type=float, default=300, help="positive per-batch timeout in seconds (default: 300)")
    release = groups.add_parser("release", help="prepare, check, publish, and verify reviewed releases").add_subparsers(dest="action", required=True)
    command = release.add_parser("check")
    command.add_argument("tag", nargs="?", help="canonical stable tag; requires final metadata and release notes")
    command.add_argument("--final-release", action="store_true")
    command.add_argument("--previous-release")
    command = release.add_parser("gate", help="require reviewed exact-commit evidence for a published release event")
    command.add_argument("tag")
    command = release.add_parser("publish", help="approve a validated draft GitHub Release and trigger its publication workflow")
    command.add_argument("tag")
    command.add_argument("--approve", action="store_true", required=True, help="explicit approval of the reviewed draft")
    command = release.add_parser("registry", help="require an absent or present exact registry version; never uploads")
    command.add_argument("tag")
    command.add_argument("--expect", choices=("absent", "present"), required=True)
    command = release.add_parser("update")
    command.add_argument("version")
    command.add_argument("--date")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--final-release", action="store_true")
    command.add_argument("--first-release", action="store_true", help="require empty stable published history; prepare without a predecessor")
    command.add_argument("--offline", action="store_true", help="use reviewed first-release intent or an explicit previous release without GitHub")
    command.add_argument("--previous-release")
    command = release.add_parser("verify", help="verify the published GitHub Release/assets and exact registry version")
    command.add_argument("tag")
    command.add_argument("--attempts", type=int, default=1)
    command.add_argument("--interval", type=int, default=10)
    review = groups.add_parser("review", help="run an opt-in CodeRabbit review").add_subparsers(dest="action", required=True)
    review.add_parser("branch", help="review branch and local changes against a verified base").add_argument("--base", default="origin/main")
    review.add_parser("uncommitted", help="review only staged, unstaged, and untracked changes")
    sarif = groups.add_parser("sarif", help="filter and split consumer-selected SARIF runs").add_subparsers(dest="action", required=True)
    split = sarif.add_parser("split", help="publish a complete filtered SARIF generation")
    split.add_argument("source")
    split.add_argument("--github-output", type=Path, help="append SARIF_DIRECTORY, SARIF_HAS_UPLOADABLE_RUNS and SARIF_RUN_COUNT after publication")
    split.add_argument("--output", required=True, help="owned directory replaced as a complete generation")
    security = groups.add_parser("security", help="run managed native dependency and secret scanners").add_subparsers(dest="action", required=True)
    osv = security.add_parser("osv", help="scan explicit tracked/nonignored uv.lock and Cargo.lock files")
    osv.add_argument("lockfiles", nargs="+")
    osv.add_argument("--output", default="target/security")
    osv.add_argument("--scanner-config")
    secrets = security.add_parser("secrets", help="scan full Git history and current tracked/nonignored files")
    secrets.add_argument("--exclude", action="append", default=[])
    secrets.add_argument("--output", default="target/security")
    secrets.add_argument("--scanner-config")
    semgrep = groups.add_parser("semgrep", help="validate consumer rules and fixtures").add_subparsers(dest="action", required=True)
    semgrep.add_parser("check-fixtures").add_argument("--rust-docs", action="store_true")
    scan = semgrep.add_parser("scan", help="scan explicit inventory with strict native reports")
    scan.add_argument("--batch-size", type=int)
    scan.add_argument("--exclude", action="append", default=[])
    scan.add_argument("--include", action="append", required=True)
    scan.add_argument("--inline-suppressions", action=argparse.BooleanOptionalAction, default=None)
    scan.add_argument("--jobs", type=int)
    scan.add_argument("--output", default="target/security/semgrep")
    scan.add_argument("--report-category")
    scan.add_argument("--report-layout", choices=("aggregate", "numbered"))
    scan.add_argument("--rust-docs", action="store_true")
    scan.add_argument("--target-timeout", type=int)
    groups.add_parser("setup", help="install Just and declared tools, configure PATH, and sync the locked environment")
    from research_repo_tools.changelog import TEMPLATES

    tectonic = groups.add_parser("tectonic", help="read-only native dependency discovery; provisioning stays consumer-owned").add_subparsers(
        dest="action", required=True
    )
    for action in ("discover", "export"):
        command = tectonic.add_parser(action)
        command.add_argument("--pkg-config", default="pkg-config")
        command.add_argument("--prefix", action="append", default=[], help="existing native prefix; may be repeated")
        if action == "discover":
            command.add_argument("--format", choices=("json", "shell"), default="json")
        else:
            command.add_argument("--file", type=Path, help="append assignments to this command file, or GITHUB_ENV")
    templates = groups.add_parser("templates", help="print or explicitly create shared package resources")
    templates.add_argument("name", choices=TEMPLATES)
    templates.add_argument("--owner")
    templates.add_argument("--repository")
    templates.add_argument("--dependency-bodies", choices=("concise", "preserve"), default="concise", help="body policy for the shared cliff.toml")
    templates.add_argument("--output", type=Path, help="create a new file; existing files are never overwritten")
    toolchain = groups.add_parser("toolchain", help="check, install, and select declared development tools").add_subparsers(dest="action", required=True)
    adoption = toolchain.add_parser("adopt", help="inherit the minimum from the exact executing shared package and reconcile consumer settings")
    mode = adoption.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    toolchain.add_parser("check", help="inspect installed tools without installing anything").add_argument("--json", action="store_true")
    clean = toolchain.add_parser("clean", help="preview obsolete package-owned installations; --apply removes them")
    mode = clean.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true", help="preview only (the default)")
    clean.add_argument("--keep-root", type=Path, action="append", default=[], help="also retain this consumer's pins (repeatable)")
    toolchain.add_parser("export", help="verify tools and export their environment to GITHUB_ENV").add_argument("--file", type=Path)
    toolchain.add_parser("python-check", help="check mandatory shared Python support and optional development mirrors without mutation")
    toolchain.add_parser("python-tools-check", help="check opt-in Python tool declarations, lock, and executable versions without changes")
    toolchain.add_parser("run", help="run a command with verified managed tools; never installs").add_argument("command", nargs=argparse.REMAINDER)
    toolchain.add_parser("sync", help="install and verify declared versions").add_argument("--dry-run", action="store_true")
    toolchain.add_parser("sync-binaries", help="install and verify pinned release binaries without package synchronization or builds")
    toolchain.add_parser("upgrade", help="upgrade declared Cargo tools and release binaries, then publish verified pins").add_argument(
        "--dry-run", action="store_true"
    )
    validation = groups.add_parser("validation", help="run configured command and prerequisite checks").add_subparsers(dest="action", required=True)
    validation.add_parser("cargo-examples", help="discover, build and run Cargo examples with consumer policy").add_argument("configuration")
    validation.add_parser("cargo-metadata").add_argument("--package")
    validation.add_parser("require").add_argument("names", nargs="+")
    command = validation.add_parser("run")
    command.add_argument("configuration")
    command.add_argument("names", nargs="*")
    zizmor = groups.add_parser("zizmor", help="audit workflows with the declared scanner and authentication policy").add_subparsers(
        dest="action", required=True
    )
    command = zizmor.add_parser("check", help="run local audits; report unauthenticated offline fallback")
    command.add_argument("--format", choices=("plain", "sarif"), default="plain")
    mode = command.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="disable authentication discovery and all online audits")
    mode.add_argument("--require-online", action="store_true", help="fail if authentication is unavailable; never downgrade")
    command.add_argument("paths", nargs="*", default=[".github"], help="local input paths relative to the consumer root (default: .github)")
    return result


def run(args: argparse.Namespace, settings: config.Config) -> int:
    if args.group == "actions":
        policy = settings.path(str(args.policy))
        if args.action == "allowlist":
            from research_repo_tools.workflow_allowlist import check

            return check(settings.root, policy, args.paths)
        from research_repo_tools.action_updates import update

        return update(settings, policy, args.paths, dry_run=args.dry_run, check=args.check)
    if args.group == "python":
        from research_repo_tools.python_checks import run

        return run(settings, args.action, timeout=args.timeout)
    if args.group == "zizmor":
        from research_repo_tools.zizmor import check

        return check(settings, args.paths, output_format=args.format, offline=args.offline, require_online=args.require_online)
    if args.group == "validation":
        from research_repo_tools.validation import check_cargo_metadata, require_executables, run_checks

        if args.action == "cargo-examples":
            from research_repo_tools.cargo_examples import run_examples

            run_examples(settings.root, args.configuration)
        elif args.action == "cargo-metadata":
            check_cargo_metadata(settings.root, package=args.package)
        elif args.action == "require":
            require_executables(settings.root, tuple(args.names))
        else:
            run_checks(settings.root, args.configuration, tuple(args.names))
        return 0
    if args.group == "ci":
        from research_repo_tools.ci import export_environment

        destination = settings.path(str(args.file)) if args.file is not None else os.environ.get("GITHUB_ENV")
        if not destination:
            raise ValueError("ci export requires --file or GITHUB_ENV")
        export_environment(Path(destination), args.names)
        return 0
    if args.group == "files":
        from research_repo_tools.selection import run_selected, select_files

        if args.action == "check-lines":
            from research_repo_tools.text_lines import check_lines

            limit = args.limit if args.limit is not None else settings.text.line_limit
            if limit is None:
                raise ValueError("files check-lines requires --limit or text.line-limit")
            result = check_lines(
                settings.root,
                limit=limit,
                include=settings.text.include if args.include is None else args.include,
                exclude=settings.text.exclude if args.exclude is None else args.exclude,
            )
            for violation in result.violations:
                print(violation, file=sys.stderr)
            if not result.files:
                print("No matching files.")
            return int(bool(result.violations))
        if args.action == "list":
            separator = "\0" if args.null else "\n"
            names = select_files(settings.root, include=args.include, exclude=args.exclude)
            _write_stdout("".join(name + separator for name in names).encode("utf-8"))
        else:
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            count = run_selected(settings.root, command, include=args.include, exclude=args.exclude, batch_size=args.batch_size, timeout=args.timeout)
            if not count:
                print("No matching files.")
        return 0
    if args.group == "papers":
        from research_repo_tools.paper_dates import read_source_date
        from research_repo_tools.paper_pdf import PdfPolicy, check_pdf, normalize_pdf

        document = settings.papers.get(args.paper) if args.paper else None
        if args.paper and document is None:
            raise ValueError(f"unknown papers.documents declaration: {args.paper}")
        if args.action == "source-date":
            path = document.tex if document is not None else args.path
            print(read_source_date(settings.path(path)).source_date_epoch)
            return 0
        path = settings.path(document.pdf if document is not None else args.path)
        declared = document.policy if document is not None else PdfPolicy()
        policy = PdfPolicy(
            declared.min_pages if args.min_pages is None else args.min_pages,
            declared.required_text if args.require_text is None else tuple(args.require_text),
            declared.forbidden_text if args.forbid_text is None else tuple(args.forbid_text),
        )
        reference = args.reference if args.reference is not None else document.reference if document is not None else None
        reference = settings.path(reference) if reference is not None else None
        if args.action == "check":
            inspection = check_pdf(path, policy=policy, reference=reference)
        else:
            tex = args.tex if args.tex is not None else document.tex if document is not None else None
            identity = args.identity if args.identity is not None else document.identity if document is not None else None
            if tex is None or identity is None:
                raise ValueError("papers normalize requires --tex and --identity, or a named --paper declaration")
            inspection = normalize_pdf(
                path, tex=settings.path(tex), identity=identity, output=settings.path(args.output) if args.output else None, policy=policy, reference=reference
            )
        print(f"OK {path}: {inspection.page_count} page(s)")
        return 0
    if args.group == "tectonic":
        import json
        import shlex

        from research_repo_tools.tectonic import discover_environment

        environment = discover_environment(pkg_config=settings.executable(args.pkg_config), prefixes=tuple(settings.path(path) for path in args.prefix))
        if args.action == "export":
            from research_repo_tools.ci import export_environment

            destination = settings.path(str(args.file)) if args.file is not None else os.environ.get("GITHUB_ENV")
            if not destination:
                raise ValueError("tectonic export requires --file or GITHUB_ENV")
            export_environment(Path(destination), sorted(environment), environment=environment)
        elif args.format == "json":
            print(json.dumps(environment, ensure_ascii=True, sort_keys=True))
        else:
            for name, value in sorted(environment.items()):
                print(f"export {name}={shlex.quote(value)}")
        return 0
    if args.group == "performance":
        from research_repo_tools.performance import run

        return run(args, settings)
    if args.group == "notebooks":
        if args.action == "group":
            print(settings.notebooks.group)
            return 0
        from research_repo_tools import notebooks

        if args.action == "sync":
            notebooks.sync(settings)
            return 0
        if args.action == "launch":
            from research_repo_tools.notebook_workflows import launch

            return launch(settings, browser=args.browser, scratch_dir=args.scratch_dir)
        if args.action == "reset":
            from research_repo_tools.notebook_workflows import reset

            reset(settings, [Path(path) for path in args.files], revision=args.revision, apply=args.apply)
            return 0
        paths = [settings.path(path) for path in args.files]
        if args.action == "advise":
            from research_repo_tools.notebook_advice import advise

            return advise(settings, paths, strict=args.strict, timeout=args.timeout)
        elif args.action == "check":
            notebooks.check(paths, outputs=settings.notebooks.outputs)
        elif args.action == "clear":
            notebooks.clear(paths)
        elif args.action == "inspect":
            from research_repo_tools.notebook_inspect import inspect

            inspect(paths, preview=not args.no_preview, as_json=args.json)
        elif args.action == "lint":
            from research_repo_tools.notebook_lint import lint

            return lint(settings, paths, timeout=args.timeout, id_pattern=args.id_pattern)
        else:
            return notebooks.execute(settings, paths, cwd=args.cwd, output_dir=args.output_dir, timeout=args.timeout)
        return 0
    if args.group == "setup":
        from research_repo_tools import toolchain, toolchain_config, toolchain_setup

        toolchain_setup.setup(toolchain.Runtime(toolchain_config.load(settings)))
        return 0
    if args.group == "toolchain":
        from research_repo_tools import toolchain, toolchain_config

        if args.action == "python-check":
            from research_repo_tools.python_baseline import check_minimum

            check_minimum(settings.root)
            if settings.toolchain.inherit_python:
                from research_repo_tools.python_baseline import check

                check(settings.root)
            return 0

        if args.action == "python-tools-check":
            if settings.toolchain.inherit_python_tools:
                from research_repo_tools.python_tools import check

                check(settings.root)
            return 0

        if args.action == "adopt":
            from research_repo_tools.python_adoption import apply_python_adoption, plan_python_adoption

            plan = plan_python_adoption(settings)
            print(f"Shared package {plan.baseline.package_version}: Python {plan.tools.python}; requirement {plan.baseline.requirement}")
            for path in plan.changed_paths:
                print(f"{'Would update' if args.dry_run else 'Update'}: {path}")
                if args.dry_run:
                    from difflib import unified_diff

                    before = (dict(plan.originals)[path] or b"").decode("utf-8").splitlines(keepends=True)
                    after = dict(plan.replacements)[path].decode("utf-8").splitlines(keepends=True)
                    print("".join(unified_diff(before, after, fromfile=path, tofile=path)), end="")
            if args.apply:
                apply_python_adoption(plan)
            return 0

        if args.action == "clean":
            from research_repo_tools.toolchain_clean import apply_clean, plan_clean

            plan = plan_clean(settings, keep_roots=tuple(args.keep_root))
            print(f"Package-owned store: {plan.home}")
            for root in (plan.root, *plan.keep_roots):
                print(f"Retain declarations: {root}")
            for removal in plan.removals:
                print(f"Would remove {removal.kind}: {removal.path}")
            if args.apply:
                apply_clean(plan, config.load(args.config, args.root))
                print(f"Removed {len(plan.removals)} obsolete installations.")
            else:
                print("Preview only; pass --apply to remove the listed installations. Use --keep-root for other consumers sharing this store.")
            return 0

        if args.action == "upgrade":
            from research_repo_tools.toolchain_upgrade import upgrade

            upgrade(settings, source=args.config, dry_run=args.dry_run)
            return 0
        if settings.toolchain.inherit_python_tools and args.action in {"check", "export", "run"}:
            from research_repo_tools.python_tools import check

            check(settings.root)
        plan = toolchain_config.load(settings)
        runtime = toolchain.Runtime(plan)
        if args.action == "sync-binaries":
            return 0 if toolchain.report(runtime.sync_binaries()) else 1
        if args.action == "export":
            from research_repo_tools.ci import export_environment

            destination = settings.path(str(args.file)) if args.file is not None else os.environ.get("GITHUB_ENV")
            if not destination:
                raise ValueError("toolchain export requires --file or GITHUB_ENV")
            if not toolchain.report(runtime.inspect(), stream=sys.stderr):
                raise ValueError("toolchain is incomplete; run toolchain sync before exporting")
            environment = runtime.environment()
            environment["RESEARCH_REPO_TOOLS_HOME"] = str(runtime.base)
            names = ["RESEARCH_REPO_TOOLS_HOME", "UV_PYTHON_INSTALL_DIR", "PATH"]
            if plan.rust:
                names.extend(["CARGO_HOME", "RUSTUP_HOME", "RUSTUP_TOOLCHAIN", "RUSTUP_AUTO_INSTALL", "RUSTUP_NO_UPDATE_CHECK"])
            export_environment(Path(destination), names, environment=environment)
            return 0
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

        rendered = template(args.name, owner=args.owner, repository=args.repository, dependency_bodies=args.dependency_bodies)
        if args.output:
            write_template(settings.path(str(args.output)), rendered)
        else:
            _write_stdout(rendered.encode("utf-8"))
        return 0
    if args.group == "sarif":
        from research_repo_tools.sarif import split
        from research_repo_tools.scanner_output import _print

        if settings.sarif is None:
            raise ValueError("sarif split requires explicit sarif.drivers configuration")
        outputs = split(settings.path(args.source), settings.path(args.output), settings.sarif)
        if args.github_output is not None:
            from research_repo_tools.ci import export_environment

            export_environment(
                settings.path(str(args.github_output)),
                ("SARIF_DIRECTORY", "SARIF_HAS_UPLOADABLE_RUNS", "SARIF_RUN_COUNT"),
                environment={
                    "SARIF_DIRECTORY": str(settings.path(args.output)),
                    "SARIF_HAS_UPLOADABLE_RUNS": "true" if outputs else "false",
                    "SARIF_RUN_COUNT": str(len(outputs)),
                },
            )
        for output in outputs:
            _print(f"Published {settings.path(args.output) / output.filename} with category {output.category}")
        _print(f"Published {len(outputs)} uploadable SARIF run(s)")
        return 0
    if args.group == "security":
        from research_repo_tools.security import scan_osv, scan_secrets

        if args.action == "osv":
            return scan_osv(settings, tuple(args.lockfiles), output=args.output, configuration=args.scanner_config)
        return scan_secrets(settings, output=args.output, configuration=args.scanner_config, exclude=tuple(args.exclude))
    if args.group == "semgrep":
        from research_repo_tools.semgrep_scan import check_documentation_fixtures, scan

        if args.action == "scan":
            return scan(
                settings,
                include=tuple(args.include),
                exclude=tuple(args.exclude),
                output=args.output,
                rust_docs=args.rust_docs,
                batch_size=args.batch_size,
                inline_suppressions=args.inline_suppressions,
                jobs=args.jobs,
                report_category=args.report_category,
                report_layout=args.report_layout,
                target_timeout=args.target_timeout,
            )
        if args.rust_docs:
            return check_documentation_fixtures(settings)
        from research_repo_tools.semgrep import check

        return check(settings)
    if args.group == "deps":
        deps = settings.deps
        if args.action == "update-uv":
            from research_repo_tools.uv_update import update

            update(settings.root, uv=settings.executable(deps.uv), dry_run=args.dry_run)
            return 0
        if args.action == "update-python":
            from research_repo_tools.dependencies import main

            if settings.toolchain.inherit_python_tools:
                from research_repo_tools.python_tools import check

                check(settings.root, executables=False)
            return main(
                [
                    "--pyproject",
                    str(settings.path(deps.pyproject)),
                    "--uv-executable",
                    settings.executable(deps.uv),
                ],
                toolchain=settings.toolchain,
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
            owners=deps.tool_owners,
            dry_run=args.dry_run,
        )
        for pin, (old, new) in changes.items():
            print(f"{pin}: {old} -> {new}")
        return 0
    if args.group == "review":
        from research_repo_tools import review

        return review.run(settings.root, base=args.base if args.action == "branch" else None)
    if args.group == "release":
        from research_repo_tools import release_publishing

        if args.action == "gate":
            from research_repo_tools.evidence import _load_json

            if os.environ.get("GITHUB_EVENT_NAME") != "release" or os.environ.get("GITHUB_REPOSITORY") != release_publishing._settings(settings).repository:
                raise ValueError("release gate requires the configured repository's GitHub release event")
            event_file = os.environ.get("GITHUB_EVENT_PATH")
            if not event_file:
                raise ValueError("release gate requires GITHUB_EVENT_PATH")
            with Path(event_file).open("rb") as stream:
                payload = stream.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                raise ValueError("release event exceeds the byte limit")
            event = release_publishing.validate_event(
                _load_json(payload, "GitHub release event"), os.environ["GITHUB_REPOSITORY"], os.environ.get("GITHUB_SHA", ""), os.environ.get("GITHUB_REF", "")
            )
            release_publishing.check_reviewed_release(settings, args.tag, event=event)
            return 0
        if args.action == "publish":
            release_publishing.publish_reviewed_release(settings, args.tag)
            return 0
        if args.action == "registry":
            from research_repo_tools.registry import lookup_version

            publishing = release_publishing._settings(settings)
            result = lookup_version(publishing.registry, publishing.package, release_publishing._tag(args.tag))
            if result.present != (args.expect == "present"):
                raise ValueError(f"registry version is {'already present; inspect before retrying uploads' if result.present else 'not yet visible'}")
            return 0
        if args.action == "verify":
            release_publishing.verify_publication(settings, args.tag, attempts=args.attempts, interval=args.interval)
            print(f"Verified published GitHub Release and registry version {args.tag}.")
            return 0
        if args.action == "check" and args.tag:
            release_publishing.check_metadata(settings, args.tag, previous_tag=args.previous_release)
            print(f"Ready to validate packages for {args.tag}.")
            return 0
        policy = replace(settings.release, final_changelog=True) if args.final_release else settings.release
        from research_repo_tools import release_metadata, update_release

        if args.action == "check":
            return release_metadata.check(settings.root, policy=policy, previous_tag=args.previous_release)
        if not args.version:
            raise ValueError("release update requires a target version")
        from research_repo_tools.release_discovery import normalize_tag

        if policy.tag_policy == "canonical-stable" and args.version != normalize_tag(args.version):
            raise ValueError("release tag must use canonical stable vX.Y.Z form")
        summary = update_release.update_release_version(
            settings.root,
            normalize_tag(args.version),
            previous_tag=args.previous_release,
            release_date=args.date,
            dry_run=args.dry_run,
            policy=policy,
            first_release=args.first_release,
            offline=args.offline,
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
                _write_stdout(rendered.encode("utf-8"))
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
            _write_stdout(notes.encode("utf-8"))
            return 0
        body = changelog.tag(settings, args.tag, force=args.force, dry_run=args.dry_run)
        if args.dry_run:
            _write_stdout(body.encode("utf-8"))
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
    expected_errors = (ExecutableNotFoundError, OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError)
    try:
        return run(args, config.load(args.config, args.root))
    except ExceptionGroup as error:
        expected, unexpected = error.split(expected_errors)
        if expected is not None:
            print(f"research-repo-tools: {format_exception_diagnostics(expected)}", file=sys.stderr)
        if unexpected is not None:
            raise unexpected from None
        return 1
    except expected_errors as error:
        print(f"research-repo-tools: {format_exception_diagnostics(error, single_line=args.group == 'deps')}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
