"""Update the main Python project and explicitly declared independent uv projects."""

import sys
import tomllib
from pathlib import Path

from research_repo_tools import config, dependencies, python_tools, toolchain, toolchain_config
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
        manifest = dependencies._conventional_manifest(project / "pyproject.toml")
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        dependencies._python_constraints(dependencies._required_table(data, "project"))
    # Preserve the existing main-manifest and development-group contracts.
    dependencies._conventional_manifest(main)
    dependencies.parse_project(main.read_text(encoding="utf-8"), toolchain=settings.toolchain)
    return tuple(projects)


def update(settings: config.Config) -> int:
    """Advance main dev pins, then upgrade and sync each independent lock."""
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
    result = dependencies.main(["--pyproject", str(settings.path(settings.deps.pyproject)), "--uv-executable", uv], toolchain=settings.toolchain, env=env)
    if result:
        return result
    for index, project in enumerate(projects):
        print(f"Updating Python project: {project}", flush=True)
        project_env = env | {"UV_PROJECT_ENVIRONMENT": str(project / ".venv")}
        target = ["--directory", str(project), "--project", str(project)]
        run_command_live(uv, ["lock", "--upgrade", *target], cwd=project, env=project_env, timeout=300)
        groups = ["--group", "dev"] if index == 0 else []
        run_command_live(uv, ["sync", "--locked", "--managed-python", *groups, *target], cwd=project, env=project_env, timeout=600)
    return 0
