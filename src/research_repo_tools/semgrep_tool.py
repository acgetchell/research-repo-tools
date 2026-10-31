"""Verify the pinned Semgrep extra in the caller's shared Python environment."""

import json
import subprocess
import sys
import tempfile
import tomllib
from importlib.metadata import metadata
from importlib.resources import files
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from research_repo_tools import python_baseline, release_credentials
from research_repo_tools.process import ExecutableNotFoundError, resolve_executable, run_command_bytes
from research_repo_tools.toml_source import set_value

__all__ = ["VERSION", "setup"]


def _pinned_version() -> str:
    pins = [Requirement(raw) for raw in metadata(python_baseline.PACKAGE).get_all("Requires-Dist", [])]
    selected = [item for item in pins if canonicalize_name(item.name) == "semgrep" and item.marker and item.marker.evaluate({"extra": "semgrep"})]
    if len(selected) != 1 or selected[0].url or len(selected[0].specifier) != 1:
        raise ValueError("installed semgrep extra must declare one exact scanner pin")
    pin = next(iter(selected[0].specifier))
    if pin.operator != "==" or "*" in pin.version:
        raise ValueError("installed semgrep extra must declare an exact scanner pin")
    return pin.version


VERSION = _pinned_version()
_QUERY = """import json, os, sys, sysconfig
from importlib.metadata import version
from pathlib import Path
binary = Path(sysconfig.get_path("scripts")) / ("semgrep.exe" if os.name == "nt" else "semgrep")
print(json.dumps({"path": str(binary), "version": version("semgrep"), "python": f"{sys.version_info.major}.{sys.version_info.minor}"}))
"""


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


def setup(root: Path, *, python: Path | None = None) -> Path:
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
        if not isinstance(data, dict) or data.get("version") != VERSION or data.get("python") != baseline:
            raise ValueError(f"the locked environment must provide Semgrep {VERSION} under shared Python {baseline}")
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
            observed = run_command_bytes(binary, ["--disable-version-check", "--version"], cwd=root, env=env, timeout=30).stdout.decode("utf-8").strip()
        if observed != VERSION:
            raise ValueError(f"expected Semgrep {VERSION}, executable reported {observed[:200]!r}")
        return binary
    except (ExecutableNotFoundError, OSError, ValueError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            f"Semgrep {VERSION} setup failed: {release_credentials.diagnostics(error)}; "
            "request research-repo-tools[semgrep], adopt its shared override, and sync the locked dev environment"
        ) from error
