"""Verify the selected Semgrep in the caller's shared Python environment."""

import json
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from importlib.metadata import metadata
from importlib.resources import files
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from research_repo_tools import python_baseline, python_locks, release_credentials
from research_repo_tools._semgrep_launcher import command as command
from research_repo_tools._semgrep_launcher import command_for_python
from research_repo_tools.process import ExecutableNotFoundError, resolve_executable, run_command_bytes
from research_repo_tools.toml_source import set_value

__all__ = ["VerifiedScanner", "command", "setup", "verify"]


def _supported_versions() -> SpecifierSet:
    requirements = [Requirement(raw) for raw in metadata(python_baseline.PACKAGE).get_all("Requires-Dist", [])]
    selected = [item for item in requirements if canonicalize_name(item.name) == "semgrep" and item.marker and item.marker.evaluate({"extra": "semgrep"})]
    if len(selected) != 1 or selected[0].url or not selected[0].specifier:
        raise ValueError("installed semgrep extra must declare a scanner version requirement")
    return selected[0].specifier


_QUERY = """import json, os, sysconfig
from importlib.metadata import version
from pathlib import Path
from packaging.markers import default_environment
binary = Path(sysconfig.get_path("scripts")) / ("semgrep.exe" if os.name == "nt" else "semgrep")
print(json.dumps({"path": str(binary), "version": version("semgrep"), "markers": default_environment()}))
"""


@dataclass(frozen=True, slots=True)
class VerifiedScanner:
    path: Path
    version: str


def _environment() -> dict[str, str]:
    """Keep the project environment, withholding credentials and Python injection."""
    env = {
        key: value for key, value in release_credentials.environment().items() if not key.upper().startswith("PYTHON") and key.upper() != "__PYVENV_LAUNCHER__"
    }
    env.update(PYTHONNOUSERSITE="1", PYTHONSAFEPATH="1", PYTHONUTF8="1", SEMGREP_SEND_METRICS="off", OTEL_SDK_DISABLED="true")
    return env


def constraints() -> tuple[str, ...]:
    """Published temporary Windows dependency policy, shared with uv adoption."""
    text = files("research_repo_tools").joinpath("templates/semgrep-constraints.txt").read_text(encoding="utf-8")
    return tuple(line for line in text.splitlines() if line and not line.startswith("#"))


def adopt_manifest(text: str) -> str:
    """Add the shared override for consumers requesting the Semgrep extra.

    Existing conflicting compatibility overrides require a deliberate consumer decision.
    Other overrides retain their original values and order.
    """
    document = tomllib.loads(text)
    groups = python_baseline.package_groups(document)
    if not any("semgrep" in item.extras for entries in groups.values() for item in entries):
        return text
    uv = document.get("tool", {}).get("uv", {})
    existing = uv.get("override-dependencies", [])
    if not isinstance(existing, list) or any(not isinstance(item, str) for item in existing):
        raise ValueError("tool.uv.override-dependencies must be requirement strings")
    wanted = [Requirement(raw) for raw in constraints()]
    for raw in existing:
        item = Requirement(raw)
        for required in wanted:
            if canonicalize_name(item.name) == canonicalize_name(required.name) and item != required:
                raise ValueError(f"{raw} conflicts with the shared Semgrep override {required}; reconcile before adoption")
    missing = [str(item) for item in wanted if item not in [Requirement(raw) for raw in existing]]
    return set_value(text, "tool.uv", "override-dependencies", json.dumps([*existing, *missing])) if missing else text


def _check_lock(root: Path, interpreter: Path, selected: Version, markers: dict[str, str], env: dict[str, str], uv: str | Path) -> None:
    manifest, lock = root / "pyproject.toml", root / "uv.lock"
    if not manifest.exists() and not lock.exists():
        return
    if not manifest.is_file() or not lock.is_file():
        raise ValueError("managed Semgrep requires both pyproject.toml and uv.lock")
    manifest_bytes, lock_bytes = manifest.read_bytes(), lock.read_bytes()
    selector = root / ".python-version"
    if selector.is_file():
        request = selector.read_text(encoding="utf-8").strip()
        expected = SpecifierSet(f"=={request}.*" if request.count(".") == 1 else f"=={request}")
        if not expected.contains(markers["python_full_version"], prereleases=True):
            raise ValueError(f"scanner interpreter does not match .python-version ({request})")
    lock_env = {key: value for key, value in env.items() if key.upper() not in _UV_REDIRECTS}
    run_command_bytes(
        uv,
        ["lock", "--check", "--offline", "--no-python-downloads", "--project", str(root), "--python", str(interpreter)],
        cwd=root,
        env=lock_env,
        timeout=30,
    )
    if manifest.read_bytes() != manifest_bytes or lock.read_bytes() != lock_bytes:
        raise ValueError("pyproject.toml or uv.lock changed while verifying Semgrep")
    locked = python_locks.active_versions(lock_bytes, "semgrep", markers)
    if locked != {selected}:
        expected = ", ".join(map(str, sorted(locked))) or "no scanner"
        raise ValueError(f"uv.lock selects {expected} for this interpreter; installed Semgrep is {selected}")


_UV_REDIRECTS = frozenset(
    {
        "UV_CONFIG_FILE",
        "UV_CONSTRAINT",
        "UV_ENV_FILE",
        "UV_LOCKED",
        "UV_FROZEN",
        "UV_OVERRIDE",
        "UV_PROJECT",
        "UV_PROJECT_ENVIRONMENT",
        "UV_PYTHON",
        "UV_WORKING_DIR",
        "UV_WORKING_DIRECTORY",
        "VIRTUAL_ENV",
    }
)


def setup(root: Path, *, python: Path | None = None) -> Path:
    """Return the absolute executable after verifying the scanner."""
    return verify(root, python=python).path


def verify(root: Path, *, python: Path | None = None, uv: str | Path = "uv") -> VerifiedScanner:
    """Verify Semgrep in this interpreter (or setup's freshly synced project).

    Never resolve/install dependencies or select a scanner-specific interpreter.
    Consumers request research-repo-tools[semgrep] in their locked dev graph.
    Absolute script discovery ignores unrelated ambient Semgrep executables.
    """
    root = root.absolute()
    env = _environment()
    try:
        interpreter = resolve_executable(python or Path(sys.executable), cwd=root, env=env)
        result = run_command_bytes(interpreter, ["-I", "-X", "utf8", "-c", _QUERY], cwd=root, env=env, timeout=30)
        data = json.loads(result.stdout)
        baseline = python_baseline.DEVELOPMENT_PYTHON
        markers = data.get("markers") if isinstance(data, dict) else None
        if not isinstance(markers, dict) or markers.get("python_version") != baseline or not isinstance(markers.get("python_full_version"), str):
            raise ValueError(f"Semgrep must use shared Python {baseline}")
        installed = data.get("version")
        if not isinstance(installed, str):
            raise ValueError("the shared environment did not return a Semgrep distribution version")
        selected = Version(installed)
        supported = _supported_versions()
        if selected.is_prerelease or not supported.contains(selected):
            raise ValueError(f"installed Semgrep {installed} is outside the supported stable range {supported}")
        _check_lock(root, interpreter, selected, markers, env, uv)
        path = data.get("path")
        if not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_file():
            raise ValueError("the shared environment did not return an existing absolute Semgrep executable")
        binary = resolve_executable(Path(path), cwd=root, env=env).resolve()
        with tempfile.TemporaryDirectory(prefix="research-semgrep-tool-") as directory:
            temporary = Path(directory)
            env.update(
                SEMGREP_SETTINGS_FILE=str(temporary / "settings.yml"),
                SEMGREP_VERSION_CACHE_PATH=str(temporary / "version-cache"),
                SEMGREP_LOG_FILE=str(temporary / "semgrep.log"),
            )
            executable, *arguments = command_for_python(binary, interpreter)
            observed = (
                run_command_bytes(
                    executable,
                    [*arguments, "--disable-version-check", "--version"],
                    cwd=root,
                    env=env,
                    timeout=30,
                )
                .stdout.decode("utf-8")
                .strip()
            )
        if observed != installed:
            raise ValueError(f"expected Semgrep {installed}, executable reported {observed[:200]!r}")
        return VerifiedScanner(binary, installed)
    except (ExecutableNotFoundError, OSError, ValueError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            f"Semgrep scanner setup failed: {release_credentials.diagnostics(error)}; "
            "request research-repo-tools[semgrep], adopt its shared override, and sync the locked dev environment"
        ) from error
