"""Initialize a checkout using an existing uv installation."""

import os
import tomllib
from importlib.metadata import version
from pathlib import Path

from research_repo_tools.dependencies import _group_requirements
from research_repo_tools.process import run_safe_command
from research_repo_tools.toolchain import INSTALL_TIMEOUT, Runtime, _probe, report
from research_repo_tools.toolchain_config import executable


def setup(runtime: Runtime) -> None:
    """Install user Just and declared tools before building project dependencies.

    uv owns the persistent Just environment and shell PATH configuration. Python,
    Rust, and Cargo installations follow the same contract as toolchain sync.
    Repository manifests, lockfiles, and existing just recipes are not rewritten.
    """
    root = runtime.plan.root
    if not (root / "uv.lock").is_file():
        raise ValueError("setup requires a committed uv.lock; run uv lock before setup")
    document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    groups = document.get("dependency-groups", {})
    if not isinstance(groups, dict):
        raise ValueError("dependency-groups must be a table")
    for name in groups:
        _group_requirements(groups, name)
    if "tooling" in groups and {"include-group": "tooling"} not in groups.get("dev", []):
        raise ValueError("setup requires dev to include the tooling group so the full sync retains research-repo-tools")
    include_dev = "dev" in groups
    runtime.sync()
    uv = runtime.uv_status()
    required = version("rust-just")
    # Keep this independent of a disposable project venv and of project indexes.
    env = dict(os.environ)
    run_safe_command(
        uv.path,
        ["tool", "install", "--no-config", "--managed-python", "--python", runtime.plan.python_request, f"rust-just=={required}"],
        cwd=root,
        env=env,
        timeout=INSTALL_TIMEOUT,
        capture_output=False,
    )
    directory = run_safe_command(uv.path, ["tool", "dir", "--bin", "--no-config"], cwd=root, env=env, timeout=30).stdout.strip()
    if not directory or not Path(directory).is_absolute():
        raise ValueError("uv tool dir did not return an absolute executable directory")
    just = _probe(executable(Path(directory), "just"), "just", required, env=env, cwd=root)
    if not report([just]):
        raise RuntimeError("Just installation failed verification; rerun setup after fixing the reported cause")
    # Do not prepend the tool directory: uv needs the inherited PATH to determine
    # whether the user's shell configuration needs updating.
    run_safe_command(uv.path, ["tool", "update-shell", "--no-config"], cwd=root, env=env, timeout=30, capture_output=False)
    statuses = runtime.inspect()
    # Inspection under uv run can see project-local Just. Verify the user command
    # instead, including when setup is called from an environment without Just.
    statuses = [just if status.name == "just" else status for status in statuses]
    if not report(statuses):
        raise RuntimeError("setup prerequisites are incomplete; fix the reported failures and rerun setup")
    sync = ["sync", "--locked", "--managed-python"]
    if include_dev:
        sync += ["--group", "dev"]
    run_safe_command(uv.path, sync, cwd=root, env=runtime.environment(), timeout=INSTALL_TIMEOUT, capture_output=False)
    print("Setup complete. Open a new terminal if PATH changed, then run just help.")
