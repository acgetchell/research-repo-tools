"""Canonical Python gates over the complete tracked/nonignored source inventory."""

import math

from research_repo_tools import config, python_baseline
from research_repo_tools.process import resolve_executable, run_command_live
from research_repo_tools.selection import argument_batches, select_files


def run(settings: config.Config, action: str, *, timeout: float = 300) -> int:
    """Run format, lint, and strict types, stopping at the first failing batch.

    Discovery and executable/batch preflight finish before any validator runs.
    Check and typecheck never enable fixes; fix applies Ruff's configured fixes
    and formatting. Notebook validation remains a separate capability.
    """
    if action not in {"check", "fix", "typecheck"}:
        raise ValueError(f"unknown Python action: {action}")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Python validator timeout must be positive and finite")
    python_baseline.check_minimum(settings.root)
    if settings.toolchain.inherit_python:
        python_baseline.check(settings.root)
    names = select_files(settings.root, include=("*.py", "*.pyi"))
    if settings.toolchain.inherit_python_tools:
        from research_repo_tools.python_tools import check

        check(settings.root, executables=bool(names))
    if not names:
        print("No Python files selected.")
        return 0
    ruff = ("--no-cache", "--no-force-exclude", "--no-respect-gitignore")
    commands = []
    if action == "check":
        commands.extend((("ruff", "format", "--check", *ruff), ("ruff", "check", "--no-fix", "--no-fix-only", *ruff)))
    if action == "fix":
        commands.extend((("ruff", "check", "--fix", "--no-fix-only", *ruff), ("ruff", "format", *ruff)))
    if action in {"check", "typecheck"}:
        commands.append(("ty", "check", "--error", "all", "--no-force-exclude", "--no-respect-ignore-files"))
    executables = {name: str(resolve_executable(name, cwd=settings.root)) for name in dict.fromkeys(command[0] for command in commands)}
    batches = tuple(batch for command in commands for batch in argument_batches((executables[command[0]], *command[1:]), names, batch_size=len(names)))
    for batch in batches:
        result = run_command_live(batch[0], batch[1:], cwd=settings.root, timeout=timeout, check=False)
        if result.returncode:
            return result.returncode if result.returncode >= 0 else 128 - result.returncode
    return 0
