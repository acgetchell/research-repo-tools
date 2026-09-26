"""Read-only Python notebook gates using the consumer's native Ruff and ty."""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from packaging.version import Version

from research_repo_tools.config import Config
from research_repo_tools.notebooks import Notebook, generated_state, selected
from research_repo_tools.process import format_exception_diagnostics, run_safe_command

MINIMUM_VERSIONS = {"ruff": "0.16.8", "ty": "0.0.82"}
RUFF_COMMON = ["--no-cache", "--no-force-exclude", "--no-respect-gitignore", "--output-format", "json"]
TY_LOCATION = re.compile(r"^.+:cell (?P<cell>\d+):(?P<line>\d+):(?P<column>\d+): (?P<message>.+)$")


@dataclass(frozen=True)
class Diagnostic:
    message: str
    cell: int | None = None
    line: int | None = None
    column: int | None = None
    operational: bool = False

    def render(self, notebook: Notebook) -> str:
        location = str(notebook.path)
        if self.cell is not None:
            location += f": cell {self.cell} ({notebook.node.cells[self.cell - 1].id})"
        if self.line is not None:
            location += f":{self.line}:{self.column}"
        return f"{location}: {self.message}"


def require_checkers(packages: tuple[str, ...] = ("ruff", "ty"), *, command: str = "notebooks lint") -> None:
    for package in packages:
        minimum = MINIMUM_VERSIONS[package]
        try:
            installed = version(package)
        except PackageNotFoundError as error:
            raise ValueError(f"{command} requires {package}>={minimum} in the locked project environment; add it to the dev group") from error
        if Version(installed) < Version(minimum):
            raise ValueError(f"{command} requires {package}>={minimum}; found {installed}; update the consumer's declaration and lockfile")


def _cell(value: object, notebook: Notebook) -> int:
    if type(value) is not int or not 1 <= value <= len(notebook.node.cells):
        raise ValueError("checker returned an invalid notebook cell number")
    return value


def _positive(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("checker returned an invalid source position")
    return value


def ruff_diagnostics(output: str, notebook: Notebook, label: str) -> list[Diagnostic]:
    raw = json.loads(output)
    if not isinstance(raw, list):
        raise ValueError("Ruff did not return a JSON diagnostic array")
    result = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("message"), str) or not isinstance(entry.get("code"), str):
            raise ValueError("Ruff returned a malformed diagnostic")
        position = entry.get("location")
        if not isinstance(position, dict):
            raise ValueError("Ruff returned a malformed diagnostic location")
        cell = _cell(entry["cell"], notebook) if entry.get("cell") is not None else None
        result.append(
            Diagnostic(
                f"{label} [{entry['code']}]: {entry['message']}",
                cell,
                _positive(position.get("row")),
                _positive(position.get("column")),
                operational=entry["code"] == "invalid-syntax",
            )
        )
    return result


def ty_diagnostics(output: str, notebook: Notebook) -> list[Diagnostic]:
    result = []
    for line in output.splitlines():
        if not line.strip() or line == "All checks passed!" or re.fullmatch(r"Found \d+ diagnostics?", line):
            continue
        match = TY_LOCATION.fullmatch(line)
        if match is None:
            result.append(Diagnostic(f"ty: {line}"))
        else:
            result.append(
                Diagnostic(f"ty: {match['message']}", _cell(int(match["cell"]), notebook), _positive(int(match["line"])), _positive(int(match["column"])))
            )
    return result


def _run(notebook: Notebook, settings: Config, module: str, args: list[str], label: str, timeout: int) -> list[Diagnostic]:
    env = {**os.environ, "NO_COLOR": "1"}
    try:
        result = run_safe_command(sys.executable, ["-m", module, *args, str(notebook.path)], cwd=settings.root, env=env, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return [Diagnostic(f"{label} timed out after {timeout} seconds", operational=True)]
    except (OSError, subprocess.SubprocessError) as error:
        return [Diagnostic(f"{label} unavailable: {format_exception_diagnostics(error, single_line=True)}", operational=True)]
    if result.returncode not in (0, 1):
        detail = (result.stderr or result.stdout).strip()
        return [Diagnostic(f"{label} failed with exit code {result.returncode}: {detail}", operational=True)]
    try:
        diagnostics = ruff_diagnostics(result.stdout, notebook, label) if module == "ruff" else ty_diagnostics(result.stdout, notebook)
    except ValueError as error:
        return [Diagnostic(f"{label} returned invalid diagnostics: {error}", operational=True)]
    if result.stderr.strip():
        diagnostics.append(Diagnostic(f"{label}: {result.stderr.strip()}", operational=True))
    if result.returncode and not diagnostics:
        diagnostics.append(Diagnostic(f"{label} failed with exit code {result.returncode} without diagnostics", operational=True))
    return diagnostics


def python_notebooks(paths: list[Path]) -> list[Notebook]:
    notebooks = selected(paths)
    for notebook in notebooks:
        for section, key in (("language_info", "name"), ("kernelspec", "language")):
            language = notebook.node.metadata.get(section, {}).get(key)
            if language is not None and language != "python":
                raise ValueError(f"{notebook.path}: notebook Python analysis requires Python notebook metadata; found {language!r}")
    return notebooks


def lint(settings: Config, paths: list[Path], *, timeout: int = 30) -> int:
    if type(timeout) is not int or timeout <= 0:
        raise ValueError("notebook lint timeout must be a positive integer")
    notebooks = python_notebooks(paths)
    require_checkers()
    checks = (
        ("ruff", ["check", "--no-fix", "--no-fix-only", *RUFF_COMMON], "ruff check"),
        ("ruff", ["format", "--check", *RUFF_COMMON], "ruff format"),
        (
            "ty",
            [
                "check",
                "--project",
                str(settings.root),
                "--python",
                sys.prefix,
                "--no-force-exclude",
                "--no-respect-ignore-files",
                "--error-on-warning",
                "--output-format",
                "concise",
            ],
            "ty",
        ),
    )
    failed = False
    for notebook in notebooks:
        diagnostics = []
        if settings.notebooks.prohibit_installs:
            from research_repo_tools.notebook_policy import install_diagnostics

            diagnostics.extend(install_diagnostics(notebook))
        if settings.notebooks.outputs == "clear" and generated_state(notebook.node):
            diagnostics.append(Diagnostic("generated outputs, counts, timing, or widget state must be cleared"))
        for module, args, label in checks:
            diagnostics.extend(_run(notebook, settings, module, args, label, timeout))
        for diagnostic in diagnostics:
            print(diagnostic.render(notebook), file=sys.stderr)
        if diagnostics:
            failed = True
        else:
            print(f"OK linted {notebook.path}")
    return int(failed)
