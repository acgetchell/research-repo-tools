"""Small generated launchers: obtain uv, then run the locked shared package."""

import os
import tempfile
import tomllib
from importlib.resources import files
from pathlib import Path

from packaging.requirements import Requirement

from research_repo_tools.files import replace
from research_repo_tools.process import run_safe_command
from research_repo_tools.tool_pins import STABLE
from research_repo_tools.toolchain_config import Toolchain

NAMES = ("bootstrap.sh", "bootstrap.ps1")


def render(name: str, uv_version: str) -> str:
    if name not in NAMES or STABLE.fullmatch(uv_version) is None:
        raise ValueError("bootstrap requires a supported launcher name and a stable uv version")
    return files("research_repo_tools").joinpath("templates", name).read_text(encoding="utf-8").replace("__UV_VERSION__", uv_version)


def generate(plan: Toolchain, *, check: bool = False, force: bool = False) -> None:
    document = tomllib.loads((plan.root / "pyproject.toml").read_text(encoding="utf-8"))
    groups = document.get("dependency-groups", {})
    if not isinstance(groups, dict) or not isinstance(groups.get("tooling"), list):
        raise ValueError("bootstrap requires a tooling dependency group")
    group = groups["tooling"]
    pins = [Requirement(item) for item in group if isinstance(item, str) and Requirement(item).name == "research-repo-tools"]
    if (
        len(pins) != 1
        or pins[0].url
        or pins[0].marker
        or not STABLE.fullmatch(str(pins[0].specifier).removeprefix("=="))
        or not str(pins[0].specifier).startswith("==")
    ):
        raise ValueError("bootstrap requires one exact research-repo-tools==X.Y.Z dependency in the tooling group")
    if {"include-group": "tooling"} not in groups.get("dev", []):
        raise ValueError("bootstrap requires dev to include the tooling group")
    if not (plan.root / "uv.lock").is_file():
        raise ValueError("bootstrap requires the consumer's committed uv.lock")
    outputs = [(plan.root / name, render(name, plan.uv).encode("utf-8")) for name in NAMES]
    # Validate the complete output set before replacing any generated file.
    for path, payload in outputs:
        if path.is_symlink():
            raise ValueError(f"bootstrap output must not be a symlink: {path}")
        matches = path.is_file() and path.read_bytes() == payload
        if check and not matches:
            raise ValueError(f"{path.name} is missing or stale; regenerate with toolchain bootstrap --force")
        if not check and path.exists() and not matches and not force:
            raise ValueError(f"{path.name} exists; use --force to regenerate it")
    if not check:
        for path, payload in outputs:
            if not path.is_file() or path.read_bytes() != payload:
                replace(path, payload)


def install_uv(uv_version: str, base: Path) -> None:
    """Reuse the same launcher for uv installation from Python and before Python."""
    name = "bootstrap.ps1" if os.name == "nt" else "bootstrap.sh"
    with tempfile.TemporaryDirectory(prefix="research-repo-tools-uv-") as temporary:
        script = Path(temporary) / name
        script.write_text(render(name, uv_version), encoding="utf-8", newline="\n")
        if os.name == "nt":
            command, args = "powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-UvOnly"]
        else:
            command, args = "sh", [str(script), "--uv-only"]
        env = {**os.environ, "RESEARCH_REPO_TOOLS_HOME": str(base)}
        run_safe_command(command, args, env=env, timeout=600, capture_output=False)
