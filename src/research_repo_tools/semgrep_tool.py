"""Package-owned Semgrep pin and isolated uv tool provisioning."""

import json
import subprocess
import tempfile
from pathlib import Path

from research_repo_tools import release_credentials
from research_repo_tools.process import ExecutableNotFoundError, resolve_executable, run_command_bytes
from research_repo_tools.toolchain_config import home

__all__ = ["VERSION", "setup"]

VERSION = "1.182.0"
PYTHON = "3.14"
SETUP_TIMEOUT = 300
_QUERY = """import json, os, sys, sysconfig
from importlib.metadata import version
from pathlib import Path
binary = Path(sysconfig.get_path("scripts")) / ("semgrep.exe" if os.name == "nt" else "semgrep")
print(json.dumps({"path": str(binary), "version": version("semgrep"), "python": f"{sys.version_info.major}.{sys.version_info.minor}"}))
"""
_REDIRECTS = frozenset(
    {
        "VIRTUAL_ENV",
        "__PYVENV_LAUNCHER__",
        "UV_PROJECT",
        "UV_PROJECT_ENVIRONMENT",
        "UV_WORKING_DIR",
        "UV_WORKING_DIRECTORY",
        "UV_CONFIG_FILE",
        "UV_ENV_FILE",
        "UV_PYTHON",
        "UV_CONSTRAINT",
        "UV_BUILD_CONSTRAINT",
        "UV_OVERRIDE",
    }
)


def _environment() -> dict[str, str]:
    """Keep uv cache/network policy while isolating the scanner's Python runtime."""
    env = {key: value for key, value in release_credentials.environment().items() if key.upper() not in _REDIRECTS and not key.upper().startswith("PYTHON")}
    # Keep the scanner's older runtime separate from project Python retention.
    env.setdefault("UV_PYTHON_INSTALL_DIR", str(home() / "semgrep" / "python"))
    env.update(PYTHONNOUSERSITE="1", PYTHONSAFEPATH="1", PYTHONUTF8="1", SEMGREP_SEND_METRICS="off", OTEL_SDK_DISABLED="true")
    return env


def setup(root: Path) -> Path:
    """Provision and verify the package's scanner without editing the consumer.

    uv reuses its cached isolated environment across repositories. The exact
    Semgrep release and compatible Python minor belong to this package; ambient
    Semgrep installs, active project environments and local uv configuration do
    not select the scanner. A cold cache may require network access.
    """
    root = root.absolute()
    env = _environment()
    try:
        uv = resolve_executable("uv", cwd=root, env=env)
        result = run_command_bytes(
            uv,
            [
                "tool",
                "run",
                "--isolated",
                "--no-config",
                "--no-env-file",
                "--managed-python",
                "--python",
                PYTHON,
                "--from",
                f"semgrep=={VERSION}",
                "python",
                "-I",
                "-X",
                "utf8",
                "-c",
                _QUERY,
            ],
            cwd=root,
            env=env,
            timeout=SETUP_TIMEOUT,
        )
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or data.get("version") != VERSION or data.get("python") != PYTHON:
            raise ValueError(f"uv must provide Semgrep {VERSION} under Python {PYTHON}")
        path = data.get("path")
        if not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_file():
            raise ValueError("uv did not return an existing absolute Semgrep executable")
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
        raise RuntimeError(f"Semgrep {VERSION} setup failed: {release_credentials.diagnostics(error)}") from error
