"""Update the main Python project and explicitly declared independent uv projects."""

import shutil
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

from research_repo_tools import config, dependencies, files, python_locks, python_resolution, python_tools, toolchain, toolchain_config
from research_repo_tools.process import run_command_live
from research_repo_tools.release_policy import relative_path


def _projects(settings: config.Config) -> tuple[Path, ...]:
    """Check the complete project inventory before invoking any updater."""
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
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
            dependencies._python_constraints(dependencies._required_table(data, "project"))
            python_resolution.policy(manifest.read_text(encoding="utf-8"), project)
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
    return tuple(projects)


def update(settings: config.Config) -> int:
    """Validate all new locks, then sync; restore original declarations on failure."""
    projects = _projects(settings)
    if settings.toolchain.inherit_python_tools:
        python_tools.check(settings.root, executables=False)
    runtime = toolchain.Runtime(toolchain_config.load(settings))
    if not toolchain.report(runtime.inspect(), stream=sys.stderr):
        raise ValueError("toolchain is incomplete; run toolchain sync before updating Python projects")
    env = runtime.project_environment()
    # An activated root environment or caller-selected interpreter must not
    # redirect a subproject's lock or replace the root's Python environment.
    for name in ("UV_CONFIG_FILE", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON", "VIRTUAL_ENV"):
        env.pop(name, None)
    uv = settings.executable(settings.deps.uv)
    targets = tuple(project / name for project in projects for name in ("pyproject.toml", "uv.lock"))
    had_environment = {project: (project / ".venv").exists() for project in projects}
    attempted_sync: list[Path] = []
    originals: Mapping[Path, bytes | None] = dict[Path, bytes | None]()

    def sync(project: Path) -> None:
        project_env = env | {"UV_PROJECT_ENVIRONMENT": str(project / ".venv")}
        target = ["--directory", str(project), "--project", str(project)]
        groups = ["--group", "dev"] if project == projects[0] else []
        run_command_live(uv, ["sync", "--locked", "--no-active", "--managed-python", *groups, *target], cwd=project, env=project_env, timeout=600)

    try:
        with files.preserve_files(targets) as originals:
            changes = dependencies.update_dev_pins(settings.path(settings.deps.pyproject), uv=uv, toolchain=settings.toolchain, env=env)
            for project in projects:
                print(f"Updating Python project: {project}", flush=True)
                project_env = env | {"UV_PROJECT_ENVIRONMENT": str(project / ".venv")}
                target = ["--directory", str(project), "--project", str(project)]
                run_command_live(uv, ["lock", "--upgrade", *target], cwd=project, env=project_env, timeout=300)
                python_locks.require_no_downgrades(originals[project / "uv.lock"], project / "uv.lock")
            for project in projects:
                attempted_sync.append(project)
                sync(project)
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
    return 0
