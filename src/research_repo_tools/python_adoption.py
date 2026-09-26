"""Preview and apply a pinned shared Python migration with recoverable state."""

import json
import os
import secrets
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

from research_repo_tools import config, files, python_baseline, toolchain, toolchain_config
from research_repo_tools.process import get_safe_executable, run_safe_command
from research_repo_tools.selection import select_files
from research_repo_tools.toml_source import key_line, replace_array_strings, set_value


@dataclass(frozen=True, slots=True)
class PythonAdoptionPlan:
    root: Path
    baseline: python_baseline.PythonBaseline
    originals: tuple[tuple[str, bytes | None], ...]
    inventory: tuple[str, ...]
    replacements: tuple[tuple[str, bytes], ...]
    tools: toolchain_config.Toolchain
    groups: tuple[str, ...]
    notebook_group: str | None

    @property
    def changed_paths(self) -> tuple[str, ...]:
        before = dict(self.originals)
        return tuple(name for name, value in self.replacements if before.get(name) != value)


def _environment(root: Path, source: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        key: value
        for key, value in (os.environ if source is None else source).items()
        if key
        not in {
            "UV_PROJECT",
            "UV_WORKING_DIR",
            "UV_WORKING_DIRECTORY",
            "UV_ENV_FILE",
            "UV_PROJECT_ENVIRONMENT",
            "UV_PYTHON",
            "VIRTUAL_ENV",
            "CONDA_PREFIX",
        }
    }
    env["UV_PROJECT_ENVIRONMENT"] = str(root / ".venv")
    env.setdefault("UV_PYTHON_INSTALL_DIR", str(toolchain_config.home() / "python"))
    return env


def _sync(uv: str, root: Path, selected: str, groups: tuple[str, ...], *, check: bool = False, environment: dict[str, str] | None = None):
    arguments = ["sync", "--locked", "--managed-python", "--python", selected]
    for group in groups:
        arguments += ["--group", group]
    if check:
        arguments.append("--check")
    return run_safe_command(uv, arguments, cwd=root, env=_environment(root, environment), timeout=3600, check=not check)


def _manifest(text: str, authority: python_baseline.PythonBaseline, old_selector: str) -> str:
    document = tomllib.loads(text)
    if problems := python_baseline.target_problems(document, authority.selected):
        raise ValueError("; ".join(problems))
    groups = python_baseline.package_groups(document)
    for group in groups:
        updates = {}
        for raw in document["dependency-groups"][group]:
            if isinstance(raw, str) and canonicalize_name((item := Requirement(raw)).name) == python_baseline.PACKAGE:
                extras = "[" + ",".join(sorted(item.extras)) + "]" if item.extras else ""
                updates[raw] = f"{item.name}{extras}=={authority.package_version}"
        text = replace_array_strings(text, "dependency-groups", group, updates)
    uv = document.get("tool", {}).get("uv", {})
    if uv.get("package") is False:
        text = set_value(text, "project", "requires-python", json.dumps(authority.requirement))
    else:
        requirement = document.get("project", {}).get("requires-python")
        if not isinstance(requirement, str) or (SpecifierSet(requirement) & SpecifierSet(f"=={authority.selected}.*")).is_unsatisfiable():
            raise ValueError("public package runtime requirement excludes shared development Python; change compatibility deliberately before adoption")
        for group in groups:
            existing = uv.get("dependency-groups", {}).get(group, {})
            if not isinstance(existing, dict) or set(existing) - {"requires-python"}:
                raise ValueError(f"unsupported uv dependency-group constraints for {group}")
            text = set_value(text, "tool.uv.dependency-groups", group, "{ requires-python = " + json.dumps(authority.requirement) + " }")
    # Retire redundant target mirrors while preserving intentional lower targets
    # and all rule/fixture exceptions. Ruff and ty infer from project metadata.
    for table, key, old in (("tool.ruff", "target-version", "py" + old_selector.replace(".", "")), ("tool.ty.environment", "python-version", old_selector)):
        value = document
        for part in table.split("."):
            value = value.get(part, {})
        if value.get(key) == old:
            line = key_line(text, table, key)
            lines = text.splitlines(keepends=True)
            del lines[line - 1]
            text = "".join(lines)
    return text


def plan_python_adoption(settings: config.Config) -> PythonAdoptionPlan:
    """Resolve and validate a private candidate; leave source files/environment intact.

    uv may download interpreters and populate caches. The exact executing package
    owns the target; there is no lookup of a newer shared release.
    """
    if not settings.toolchain.inherit_python:
        raise ValueError("opt in with tool.research-repo-tools.toolchain.inherit-python = true before adoption")
    root = settings.root
    authority = python_baseline.baseline()
    manifest = root / "pyproject.toml"
    original = manifest.read_bytes()
    selector = root / ".python-version"
    old_selector = selector.read_text(encoding="utf-8").strip() if selector.exists() else ""
    text = _manifest(original.decode("utf-8"), authority, old_selector)
    document = tomllib.loads(text)
    declared_groups = document.get("dependency-groups", {})
    notebook_group = settings.notebooks.group if settings.notebooks.group in declared_groups else None
    groups = tuple(
        sorted(
            set(python_baseline.package_groups(document)) | ({"dev"} if "dev" in declared_groups else set()) | ({notebook_group} if notebook_group else set())
        )
    )
    tracked = select_files(root, exclude=(".venv/**", "target/**", "dist/**", "build/**", "node_modules/**"))
    if "pyproject.toml" not in tracked:
        raise ValueError("adoption requires a tracked or nonignored pyproject.toml")
    originals = tuple((name, (root / name).read_bytes() if (root / name).exists() else None) for name in sorted(set(tracked) | {".python-version", "uv.lock"}))
    newline = b"\r\n" if selector.exists() and b"\r\n" in selector.read_bytes() else b"\n"
    replacements = {"pyproject.toml": text.encode("utf-8"), ".python-version": authority.selected.encode("ascii") + newline}
    uv = get_safe_executable("uv")
    with tempfile.TemporaryDirectory(prefix="research-python-adoption-") as directory:
        candidate = Path(directory).resolve() / root.name
        candidate.mkdir()
        for name, payload in originals:
            if payload is not None:
                path = candidate / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                # Build hooks may execute checkout helpers directly. Keep their
                # executable bits while leaving the candidate writable for uv.
                path.chmod(path.stat().st_mode | ((root / name).stat().st_mode & 0o111))
        for name, payload in replacements.items():
            (candidate / name).write_bytes(payload)
        candidate_settings = config.load(root=candidate)
        tools = toolchain_config.load(candidate_settings)
        status = toolchain.Runtime(tools).uv_status()
        if not status.ok:
            raise ValueError(f"adoption requires uv {tools.uv}; found {status.actual}")
        run_safe_command(uv, ["lock", "--managed-python", "--python", authority.selected], cwd=candidate, env=_environment(candidate), timeout=600)
        replacements["uv.lock"] = (candidate / "uv.lock").read_bytes()
        _sync(uv, candidate, authority.selected, groups, environment=toolchain.Runtime(tools).environment())
        if notebook_group:
            python = toolchain_config.executable(candidate / ".venv" / ("Scripts" if os.name == "nt" else "bin"), "python")
            run_safe_command(str(python), ["-c", "import ipykernel"], cwd=candidate, env=_environment(candidate), timeout=30)
    return PythonAdoptionPlan(root, authority, originals, tracked, tuple(replacements.items()), replace(tools, root=root), groups, notebook_group)


def apply_python_adoption(plan: PythonAdoptionPlan) -> None:
    """Publish validated declarations and rebuild at the final environment path.

    The old environment is restored at its original path on caught failures.
    Windows callers must use the standalone uvx bootstrap, outside that venv.
    This does not promise a crash-atomic transaction or concurrent-writer lock.
    """
    root = plan.root
    current = select_files(root, exclude=(".venv/**", "target/**", "dist/**", "build/**", "node_modules/**"))
    if current != plan.inventory:
        raise ValueError("source inventory changed since the adoption plan; prepare a fresh plan")
    for name, original in plan.originals:
        path = root / name
        if path.is_symlink() or (path.read_bytes() if path.exists() else None) != original:
            raise ValueError(f"{name}: source changed since the adoption plan; prepare a fresh plan")
    environment = root / ".venv"
    if environment.is_symlink() or environment.is_junction() or environment.exists() and not (environment / "pyvenv.cfg").is_file():
        raise ValueError("adoption requires .venv to be absent or a regular project virtual environment")
    if Path(sys.prefix).resolve().is_relative_to(environment.resolve()):
        raise ValueError("run adoption through standalone uvx outside the consumer environment, especially on Windows")
    uv = get_safe_executable("uv")
    kernel = environment / "share/jupyter/kernels/research-repo-tools/kernel.json"
    kernel_current = not plan.notebook_group
    if plan.notebook_group and kernel.is_file():
        try:
            argv = json.loads(kernel.read_bytes()).get("argv", [])
            python = toolchain_config.executable(environment / ("Scripts" if os.name == "nt" else "bin"), "python")
            kernel_current = bool(argv) and Path(argv[0]) == python and "ipykernel_launcher" in argv
        except OSError, ValueError, TypeError, AttributeError:
            kernel_current = False
    if not plan.changed_paths and environment.exists() and kernel_current:
        if _sync(uv, root, plan.baseline.selected, plan.groups, check=True).returncode == 0:
            return
    # Versioned tool installations occur before declaration publication. Old
    # versions remain available if candidate verification or migration fails.
    toolchain.Runtime(plan.tools).sync()
    backup = root / f".venv.rrt-backup-{secrets.token_hex(8)}"
    saved = False
    started = False
    with files.preserve_files(tuple(root / name for name, _ in plan.replacements)):
        try:
            if environment.exists():
                environment.rename(backup)
                saved = True
            started = True
            files.replace_many({root / name: payload for name, payload in plan.replacements})
            _sync(uv, root, plan.baseline.selected, plan.groups, environment=toolchain.Runtime(plan.tools).environment())
            if plan.notebook_group:
                python = toolchain_config.executable(environment / ("Scripts" if os.name == "nt" else "bin"), "python")
                run_safe_command(
                    str(python),
                    [
                        "-m",
                        "ipykernel",
                        "install",
                        "--prefix",
                        str(environment),
                        "--name",
                        "research-repo-tools",
                        "--display-name",
                        "Research project (locked Python)",
                    ],
                    cwd=root,
                    env=_environment(root),
                    timeout=60,
                )
        except BaseException as error:
            try:
                # If rename itself failed, the original environment is intact.
                if started:
                    if environment.exists():
                        shutil.rmtree(environment)
                    if saved:
                        backup.rename(environment)
            except OSError as recovery:
                raise BaseExceptionGroup(
                    "Python adoption failed; environment recovery required", [error, files.RecoveryError(environment, backup if saved else None, recovery)]
                ) from None
            raise
    if saved:
        try:
            shutil.rmtree(backup)
        except OSError:
            print(f"Adoption completed; old environment backup retained at {backup}", file=sys.stderr)
