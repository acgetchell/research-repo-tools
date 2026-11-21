"""Update the main Python project and explicitly declared independent uv projects."""

import shutil
import sys
import tomllib
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from research_repo_tools import config, dependencies, files, python_baseline, python_locks, python_resolution, python_tools, toolchain, toolchain_config
from research_repo_tools.dependency_policy import project_manifests
from research_repo_tools.process import run_command_live
from research_repo_tools.release_policy import relative_path


def _projects(settings: config.Config, *, adopt_supplier: bool) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Identify independent lock roots and manifests sharing the supplier workspace lock."""
    root = settings.root.resolve()
    main = settings.path(settings.deps.pyproject)
    if not main.is_relative_to(root):
        raise ValueError(f"Python update manifest must be inside the repository: {main}")
    relative_path(main.relative_to(root).as_posix())
    projects = [main.parent]
    for name in settings.deps.python_projects:
        try:
            relative_path(name)
        except ValueError as error:
            raise ValueError(f"deps.python-projects: {error}") from error
        projects.append(root / name)
    workspace: tuple[Path, ...] = ()
    seen: set[str] = set()
    for project in projects:
        if not project.is_relative_to(root):
            raise ValueError(f"Python update project must be inside the repository: {project}")
        identity = project.as_posix().casefold()
        if identity in seen:
            raise ValueError(f"duplicate Python update project: {project}")
        seen.add(identity)
        path = root
        for part in project.relative_to(root).parts:
            path /= part
            if path.is_symlink() or path.is_junction():
                raise ValueError(f"Python update project must not traverse links: {path}")
        for name in ("pyproject.toml", "uv.lock", ".python-version", ".venv"):
            path = project / name
            if path.is_symlink() or path.is_junction():
                raise ValueError(f"Python update target must not be a link: {path}")
        try:
            manifest = dependencies._conventional_manifest(project / "pyproject.toml")
            text = manifest.read_text(encoding="utf-8")
            data = tomllib.loads(text)
            dependencies._python_constraints(dependencies._required_table(data, "project"))
            tool = config._table(data.get("tool", {}), "tool")
            uv = config._table(tool.get("uv", {}), "tool.uv")
            if adopt_supplier and project == main.parent and "workspace" in uv:
                workspace = project_manifests(project, "python")
            else:
                python_resolution.policy(text, project)
            lock = project / "uv.lock"
            python_locks.versions(lock.read_bytes() if lock.exists() else None)
            if (project / ".venv").exists() and not lock.exists():
                raise ValueError("an existing .venv requires uv.lock for recoverable updates")
        except (TypeError, ValueError) as error:
            if project == main.parent:
                raise
            raise ValueError(f"{project.relative_to(root).as_posix()}: {error}") from error
    # Preserve the existing main-manifest and development-group contracts.
    dependencies._conventional_manifest(main)
    dependencies.parse_project(main.read_text(encoding="utf-8"), toolchain=settings.toolchain)
    return tuple(projects), workspace


def update(settings: config.Config, *, adopt_supplier: str | None = None, promote: str | None = None, versions: str = "", prereleases: bool = False) -> int:
    """Validate all new locks, then sync; restore original declarations on failure."""
    from research_repo_tools.python_promotion import NoSupplyingRelease, check_promoted_installation, check_promoted_lock, plan_promotion, plan_supplier

    if adopt_supplier is not None and promote is not None:
        raise ValueError("choose automatic supplier adoption or explicit Git promotion")
    # Promotion runs from an external controller that can have a different
    # version than either project pin. Keep the project's declared toolchain
    # and defer package identity to the verified promotion and final sync.
    runtime_settings = (
        settings if promote is None else replace(settings, toolchain=replace(settings.toolchain, inherit_python=False, inherit_python_tools=False))
    )
    projects, workspace = _projects(runtime_settings, adopt_supplier=adopt_supplier is not None)
    promotion = None
    promotion_root = None
    selection = adopt_supplier if adopt_supplier is not None else promote
    if selection is not None:
        try:
            promotion = (
                plan_supplier(settings.root, selection, project=projects[0])
                if adopt_supplier is not None
                else plan_promotion(settings.root, selection, versions=versions, prereleases=prereleases)
            )
        except NoSupplyingRelease as reason:
            print(f"Retained {selection}: {reason}.")
            if promote is not None:
                return 0
    if promotion is not None:
        promotion_root = settings.root / promotion.selection.project.directory
        if promotion_root not in projects:
            raise ValueError("promotion must select the main or a declared Python update project")
        environment = promotion_root / ".venv"
        if Path(sys.prefix).resolve().is_relative_to(environment.resolve()):
            raise ValueError("run supplier adoption from an external installed runtime, such as uv run --isolated, so .venv can be restored")
        if environment.exists() and not (environment / "pyvenv.cfg").is_file():
            raise ValueError("promotion requires .venv to be absent or a regular project virtual environment")
    if runtime_settings.toolchain.inherit_python_tools:
        python_tools.check(settings.root, executables=False)
    runtime = toolchain.Runtime(toolchain_config.load(runtime_settings))
    if not toolchain.report(runtime.inspect(), stream=sys.stderr):
        raise ValueError("toolchain is incomplete; run toolchain sync before updating Python projects")
    env = runtime.project_environment()
    # An activated root environment or caller-selected interpreter must not
    # redirect a subproject's lock or replace the root's Python environment.
    for name in ("UV_CONFIG_FILE", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON", "VIRTUAL_ENV"):
        env.pop(name, None)
    uv = settings.executable(settings.deps.uv)
    active_projects = (promotion_root,) if promote is not None and promotion_root is not None else projects
    targets = tuple(project / name for project in active_projects for name in ("pyproject.toml", "uv.lock"))
    planned = dict(promotion.writes) if promotion is not None else {}
    targets = tuple(dict.fromkeys((*targets, *workspace, *planned)))
    had_environment = {project: (project / ".venv").exists() for project in active_projects}
    attempted_sync: list[Path] = []
    originals: Mapping[Path, bytes | None] = dict[Path, bytes | None]()

    def sync(project: Path) -> None:
        project_env = env | {"UV_PROJECT_ENVIRONMENT": str(project / ".venv")}
        target = ["--directory", str(project), "--project", str(project)]
        groups = ["--group", "dev"] if project == projects[0] else []
        run_command_live(uv, ["sync", "--locked", "--no-active", "--managed-python", *groups, *target], cwd=project, env=project_env, timeout=600)

    try:
        with files.preserve_files(targets) as originals, ExitStack() as environments:
            if promotion is not None:
                files.replace_many(dict(promotion.writes), verify=promotion.snapshot.verify)
            changes = {}
            # The isolated script resolver cannot model a shared workspace lock.
            # Native uv refreshes it below, retaining exact non-supplier pins.
            if promote is None and not workspace:
                options = {}
                pin_toolchain = settings.toolchain
                if adopt_supplier is not None:
                    preserve = {python_baseline.PACKAGE}
                    if settings.toolchain.inherit_python_tools:
                        preserve.update(python_tools.TOOLS)
                    options = {"preserve": frozenset(preserve)}
                    if promotion is not None:
                        # The candidate lock enforces the new supplier extras;
                        # the running updater still reports the old profile.
                        pin_toolchain = replace(pin_toolchain, inherit_python=False, inherit_python_tools=False)
                changes = dependencies.update_dev_pins(settings.path(settings.deps.pyproject), uv=uv, toolchain=pin_toolchain, env=env, **options)
            for project in active_projects:
                if promote is None:
                    print(f"Updating Python project: {project}", flush=True)
                    project_env = env | {"UV_PROJECT_ENVIRONMENT": str(project / ".venv")}
                    target = ["--directory", str(project), "--project", str(project)]
                    run_command_live(uv, ["lock", "--upgrade", *target], cwd=project, env=project_env, timeout=300)
                python_locks.require_no_downgrades(originals[project / "uv.lock"], project / "uv.lock")
            if promotion is not None and promotion_root is not None:
                check_promoted_lock(promotion, promotion_root)
            for project in active_projects:
                if project == promotion_root:
                    from research_repo_tools.python_adoption import _rebuild_environment

                    environments.enter_context(_rebuild_environment(project / ".venv"))
                else:
                    attempted_sync.append(project)
                sync(project)
            if promotion is not None and promotion_root is not None:
                check_promoted_installation(promotion, promotion_root)
                if adopt_supplier is not None:
                    from research_repo_tools.python_adoption import _check_tools

                    _check_tools(promotion_root, settings.toolchain.inherit_python_tools)
            for manifest in workspace:
                if manifest.read_bytes() != planned.get(manifest, originals[manifest]):
                    raise ValueError(f"workspace manifest changed during supplier update: {manifest}")
    except BaseException as error:
        # Restore environments only after preserve_files has restored the exact
        # original manifests/locks. A failed file rollback retains its backups.
        recovery_errors: list[BaseException] = []
        for project in reversed(attempted_sync):
            try:
                if any((path.read_bytes() if path.exists() else None) != originals[path] for path in targets):
                    raise RuntimeError("original declarations could not be restored; recover retained backups before synchronizing environments")
                if had_environment[project]:
                    sync(project)
                elif (project / ".venv").exists():
                    shutil.rmtree(project / ".venv")
            except BaseException as recovery:
                recovery_errors.append(RuntimeError(f"failed to restore Python environment {project / '.venv'}: {recovery}"))
        if recovery_errors:
            raise BaseExceptionGroup("Python update failed; environment recovery also failed", [error, *recovery_errors]) from None
        raise
    for name, (old, new) in changes.items():
        print(f"Updated {name}: {old} -> {new}")
    if promotion is not None:
        print(f"{'Adopted' if adopt_supplier is not None else 'Promoted'} {selection} to verified PyPI release {promotion.verified.version}")
    return 0
