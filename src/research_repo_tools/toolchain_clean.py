"""Preview and remove obsolete installations from the shared tool cache."""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from research_repo_tools import config, toolchain, toolchain_config
from research_repo_tools.process import ExecutableNotFoundError, run_safe_command
from research_repo_tools.tool_pins import SEMVER, STABLE

__all__ = ["CleanPlan", "Removal", "apply_clean", "plan_clean"]


@dataclass(frozen=True)
class Removal:
    """An owned installation and the directory identity observed during preview."""

    kind: str
    path: Path
    identity: tuple[int, int, int]
    command: tuple[str, ...] = ()


@dataclass(frozen=True)
class CleanPlan:
    """A revalidated preview; keep_roots names every consumer to protect."""

    root: Path
    keep_roots: tuple[Path, ...]
    removals: tuple[Removal, ...]
    home: Path
    host: str
    declarations: tuple[tuple[Path, bytes | None], ...]


def _removal(kind: str, path: Path, command: tuple[str, ...] = ()) -> Removal:
    stat = path.lstat()
    return Removal(kind, path, (stat.st_dev, stat.st_ino, stat.st_mtime_ns), command)


def _plain(path: Path) -> None:
    """Never traverse symlinks/junctions while selecting removable directories."""
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError(f"cleanup paths must not contain links or junctions: {part}")


def _directories(path: Path) -> tuple[Path, ...]:
    _plain(path)
    if not path.exists():
        return ()
    if not path.is_dir():
        raise ValueError(f"expected managed installation directory: {path}")
    result = []
    for item in sorted(path.iterdir()):
        _plain(item)
        if item.is_dir():
            result.append(item)
    return tuple(result)


def _version(value: str) -> Version | None:
    # Cargo allows build metadata and prereleases; never guess at unfamiliar names.
    if SEMVER.fullmatch(value) is None:
        return None
    # Compare SemVer's numeric release core only. Prerelease/build variants of
    # the same core stay retained rather than guessing a cross-ecosystem order.
    return Version(value.split("+", 1)[0].split("-", 1)[0])


def _venv_interpreters(directory: Path) -> tuple[Path, ...]:
    """Read environment metadata, without executing arbitrary environment Python."""
    path = directory / "pyvenv.cfg"
    if not path.is_file():
        return ()
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    result = []
    for key in ("home", "executable", "base-executable"):
        if value := values.get(key):
            path = Path(value)
            if not path.is_absolute():
                raise ValueError(f"{directory}: cannot establish the virtual environment's interpreter ownership")
            result.append(path.resolve())
    if not result:
        raise ValueError(f"{directory}: virtual environment has no interpreter ownership metadata")
    return tuple(result)


def _python_removals(runtime: toolchain.Runtime, plans: list[toolchain_config.Toolchain]) -> list[Removal]:
    directory = runtime.base / "python"
    _plain(directory)
    if not directory.exists():
        return []
    uv = runtime.uv_status()
    if not uv.ok:
        raise ValueError("the declared uv must be available to inventory private Python installations")
    env = dict(os.environ)
    env["UV_PYTHON_INSTALL_DIR"] = str(directory)
    args = ["python", "list", "--only-installed", "--managed-python", "--output-format", "json", "--no-config", "--offline"]
    data = json.loads(run_safe_command(uv.path, args, cwd=runtime.plan.root, env=env, timeout=30).stdout)
    if not isinstance(data, list):
        raise ValueError("uv Python inventory must be an array")
    protected = [Path(sys.base_prefix).resolve(), Path(sys.executable).resolve()]
    # uv's minor aliases use symlinks on POSIX and junctions on Windows. Remove
    # only recognized aliases alongside an unused target, inside this store.
    # Avoid `uv python uninstall`: it also maintains user-wide aliases/registry.
    links = {path: path.resolve() for path in directory.iterdir() if path.is_symlink() or path.is_junction()}
    for plan in plans:
        protected.extend(_venv_interpreters(plan.root / ".venv"))
    for value in (os.environ.get("VIRTUAL_ENV"), os.environ.get("UV_PROJECT_ENVIRONMENT")):
        if value:
            path = Path(value)
            protected.extend(_venv_interpreters(path if path.is_absolute() else runtime.plan.root / path))
    # User-level Just and other uv tools can reference a private interpreter.
    tools = run_safe_command(uv.path, ["tool", "dir", "--no-config", "--offline"], cwd=runtime.plan.root, env=env, timeout=30).stdout.strip()
    if not tools or not Path(tools).is_absolute():
        raise ValueError("uv tool dir must return an absolute directory")
    if Path(tools).exists():
        for environment in Path(tools).iterdir():
            if environment.is_dir():
                protected.extend(_venv_interpreters(environment))
    candidates = {}
    for item in data:
        if not isinstance(item, dict) or not isinstance(key := item.get("key"), str) or not isinstance(path := item.get("path"), str):
            raise ValueError("malformed uv Python inventory entry")
        version = item.get("version")
        if not isinstance(version, str):
            raise ValueError("malformed uv Python version")
        if item.get("implementation") != "cpython" or item.get("variant") != "default" or not STABLE.fullmatch(version):
            continue
        # The package requests CPython. Preserve other variants/architectures
        # and anything not physically inside this private installation root.
        if not key.startswith("cpython-" + version + "-") or "/" in key or "\\" in key or ":" in key:
            raise ValueError("malformed uv Python installation key")
        candidate = directory / key
        _plain(candidate)
        if not Path(path).is_absolute():
            raise ValueError("uv Python inventory paths must be absolute")
        resolved = Path(path).resolve()
        if not candidate.is_dir() or not resolved.is_relative_to(candidate.resolve()):
            continue
        host_os = {"apple-darwin": "macos", "unknown-linux-gnu": "linux", "pc-windows-msvc": "windows"}
        system = next(value for suffix, value in host_os.items() if runtime.host.endswith(suffix))
        if item.get("os") != system or item.get("arch") != runtime.host.split("-", 1)[0]:
            continue
        candidates[key] = (candidate, Version(version))
    # A minor selector retains its newest compatible installed patch. Also keep
    # every interpreter actually referenced by retained environments below.
    floor = min(
        max((version for _, version in candidates.values() if version in SpecifierSet(plan.python_request)), default=Version(plan.python)) for plan in plans
    )
    removals = []
    for key, (candidate, version) in sorted(candidates.items()):
        if version >= floor or any(value.is_relative_to(candidate.resolve()) for value in protected):
            continue
        minor = directory / key.replace(f"cpython-{version}-", f"cpython-{version.major}.{version.minor}-", 1)
        aliases = [path for path, target in links.items() if target.is_relative_to(candidate.resolve())]
        if any(path != minor for path in aliases):
            continue  # Preserve targets of unrecognized user-created aliases.
        removals.extend(_removal("python-alias", path) for path in aliases)
        removals.append(_removal("python", candidate))
    return removals


def plan_clean(settings: config.Config, *, keep_roots: tuple[Path, ...] = ()) -> CleanPlan:
    """Read declared retention roots and managed directories; never delete/install.

    Retain undeclared tools, newer versions, other hosts, and every selected
    consumer's pins. Obsolete Cargo/binary versions must precede all retained
    pins. Relative retention roots resolve against settings.root. Rust cleanup
    uses the package's private rustup home only.
    """
    resolved = ((settings.root / root).resolve(strict=True) for root in keep_roots)
    roots = tuple(dict.fromkeys(root for root in resolved if root != settings.root.resolve()))
    plans = [toolchain_config.load(settings), *(toolchain_config.load(config.load(root=root)) for root in roots)]
    runtime = toolchain.Runtime(plans[0])
    _plain(runtime.base)
    removals = []
    cargo = {}
    binaries = {}
    rust = set()
    for plan in plans:
        if plan.rust:
            rust.add(plan.rust.channel)
        for tool in plan.cargo:
            cargo.setdefault(tool.package, []).append(tool.version)
        for tool in plan.binaries:
            binaries.setdefault(tool.name, []).append(tool.version)
    minimum_rust = min(map(Version, rust)) if rust else None
    for compiler in _directories(runtime.base / "cargo" / runtime.host):
        if not STABLE.fullmatch(compiler.name):
            continue
        compiler_version = Version(compiler.name)
        for package in _directories(compiler):
            if package.name not in cargo:
                continue
            oldest = min(version for pin in cargo[package.name] if (version := _version(pin)) is not None)
            for installed in _directories(package):
                version = _version(installed.name)
                if version is not None and (version < oldest or minimum_rust is not None and compiler_version < minimum_rust and version <= oldest):
                    removals.append(_removal("cargo", installed))
    for package in _directories(runtime.base / "binaries" / runtime.host):
        if package.name not in binaries:
            continue
        oldest = min(Version(version) for version in binaries[package.name])
        for installed in _directories(package):
            version = _version(installed.name)
            if version is not None and version < oldest:
                removals.append(_removal("binary", installed))
    if minimum_rust is not None:
        settings_file = runtime.manager / "toolchains" / "settings.toml"
        _plain(settings_file)
        default = tomllib.loads(settings_file.read_text(encoding="utf-8")).get("default_toolchain") if settings_file.is_file() else None
        for installed in _directories(runtime.manager / "toolchains" / "toolchains"):
            suffix = "-" + runtime.host
            name = installed.name.removesuffix(suffix)
            if default not in (installed.name, name) and installed.name.endswith(suffix) and STABLE.fullmatch(name) and Version(name) < minimum_rust:
                _plain(runtime.rustup)
                if not runtime.rustup.is_file():
                    raise ValueError("managed rustup is missing; repair setup before cleaning Rust toolchains")
                removals.append(_removal("rust", installed, (str(runtime.rustup), "toolchain", "uninstall", installed.name)))
    removals.extend(_python_removals(runtime, plans))
    declarations = tuple(
        (path, path.read_bytes() if path.exists() else None)
        for plan in plans
        for name in ("pyproject.toml", ".python-version", "rust-toolchain.toml")
        for path in (plan.root / name,)
    )
    return CleanPlan(settings.root, roots, tuple(removals), runtime.base, runtime.host, declarations)


def apply_clean(plan: CleanPlan, settings: config.Config) -> None:
    """Recompute before deletion; stop on the first error without claiming rollback.

    Close processes using obsolete tools first, particularly on Windows. No
    concurrency guarantee: do not install or change declarations during cleanup.
    """
    current = plan_clean(settings, keep_roots=plan.keep_roots)
    if current != plan:
        raise ValueError("cleanup inventory or declarations changed; prepare a fresh preview")
    runtime = toolchain.Runtime(toolchain_config.load(settings))
    env = dict(os.environ)
    env.update(CARGO_HOME=str(runtime.manager / "cargo"), RUSTUP_HOME=str(runtime.manager / "toolchains"), RUSTUP_AUTO_INSTALL="0", RUSTUP_NO_UPDATE_CHECK="1")
    env.pop("RUSTUP_TOOLCHAIN", None)
    for removal in plan.removals:
        _plain(removal.path.parent if removal.kind == "python-alias" else removal.path)
        try:
            if removal.kind == "python-alias":
                if removal.path.is_symlink():
                    removal.path.unlink()
                elif removal.path.is_junction():
                    removal.path.rmdir()
                else:
                    raise ValueError(f"Python alias changed; prepare a fresh preview: {removal.path}")
            elif removal.command:
                run_safe_command(removal.command[0], list(removal.command[1:]), cwd=plan.root, env=env, timeout=600, capture_output=False)
            else:
                shutil.rmtree(removal.path)
        except (ExecutableNotFoundError, OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(f"could not remove {removal.path}; earlier removals remain applied; close processes using it and retry: {error}") from error
