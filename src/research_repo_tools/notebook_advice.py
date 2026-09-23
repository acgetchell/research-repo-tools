"""Opt-in review policy; native Ruff owns generic Python lint rules."""

import ast
import re
import sys
from pathlib import Path

from research_repo_tools.config import Config
from research_repo_tools.notebook_lint import RUFF_COMMON, Diagnostic, _run, python_notebooks, require_checkers

GENERATED_ID = re.compile(r"(?:[0-9a-f]{8,64}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", re.IGNORECASE)
POSITIONAL_ID = re.compile(r"(?:\d+|(?:cell|code|markdown|raw)[-_]?\d+)", re.IGNORECASE)
SUBPROCESS_CALLS = {"call", "check_call", "check_output", "run"}


def _timeouts(source: str, number: int) -> tuple[list[Diagnostic], list[Diagnostic]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # No text-based removal of magics: that can corrupt multiline strings.
        # Native Ruff/ty remain responsible for notebook syntax validation.
        return [], [Diagnostic("plain-AST timeout advice skipped: cell is not plain Python; use notebooks lint for native syntax checks", number)]
    warnings = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr in SUBPROCESS_CALLS
        ):
            continue
        timeout = next((keyword.value for keyword in node.keywords if keyword.arg == "timeout"), None)
        if timeout is None or isinstance(timeout, ast.Constant) and timeout.value is None:
            warnings.append(
                Diagnostic("subprocess-timeout: supply an explicit non-None timeout to this subprocess call", number, node.lineno, node.col_offset + 1)
            )
    return warnings, []


def advise(settings: Config, paths: list[Path], *, strict: bool = False, timeout: int = 30) -> int:
    if type(timeout) is not int or timeout <= 0:
        raise ValueError("notebook advice timeout must be a positive integer")
    notebooks = python_notebooks(paths)
    policy = settings.notebooks.advice
    if policy.ruff_rules:
        require_checkers(("ruff",), command="notebooks advise")
    failed = False
    for notebook in notebooks:
        diagnostics = []
        information = []
        for number, cell in enumerate(notebook.node.cells, 1):
            if policy.descriptive_ids and (GENERATED_ID.fullmatch(cell.id) or POSITIONAL_ID.fullmatch(cell.id)):
                diagnostics.append(Diagnostic("descriptive-id: ID looks generated or positional; consider a stable description of the cell's purpose", number))
            if policy.subprocess_timeout and cell.cell_type == "code":
                warnings, skipped = _timeouts(cell.source, number)
                diagnostics.extend(warnings)
                information.extend(skipped)
        if policy.ruff_rules:
            diagnostics.extend(
                _run(
                    notebook,
                    settings,
                    "ruff",
                    ["check", "--no-fix", "--no-fix-only", *RUFF_COMMON, "--select", ",".join(policy.ruff_rules)],
                    "ruff advice",
                    timeout,
                )
            )
        for diagnostic in information:
            print(f"INFO {diagnostic.render(notebook)}", file=sys.stderr)
        for diagnostic in diagnostics:
            print(f"{'ERROR' if diagnostic.operational else 'WARNING'} {diagnostic.render(notebook)}", file=sys.stderr)
        failed |= any(diagnostic.operational for diagnostic in diagnostics) or bool(diagnostics) and (strict or policy.strict)
        print(f"Reviewed {notebook.path}: {sum(not item.operational for item in diagnostics)} advisory warning(s), {len(information)} skipped cell(s)")
    return int(failed)
