"""Require explicit newline handling at Python text-file write boundaries.

Checks src, scripts, and tests, including parseable Python string literals used
for child scripts. This is a syntax check, not type inference or dataflow analysis:
method aliases, computed source, and computed newline values need code review.
"""

import argparse
import ast
import sys
import textwrap
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def argument(call: ast.Call, name: str, position: int, default: ast.expr | None = None) -> ast.expr | None:
    return next((keyword.value for keyword in call.keywords if keyword.arg == name), call.args[position] if len(call.args) > position else default)


def qualified_name(node: ast.expr, imports: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return imports.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return f"{qualified_name(node.value, imports)}.{node.attr}"
    return ""


def newline_position(call: ast.Call, imports: dict[str, str]) -> int | None:
    """Return the newline argument index only for a recognized text writer."""
    name = qualified_name(call.func, imports)
    if isinstance(call.func, ast.Attribute) and call.func.attr == "write_text":
        return 4 if name in {"pathlib.Path.write_text", "pathlib.PosixPath.write_text", "pathlib.WindowsPath.write_text"} else 3
    if name == "io.TextIOWrapper":
        return 3
    if name in {"open", "builtins.open", "io.open", "os.fdopen", "pathlib.Path.open", "pathlib.PosixPath.open", "pathlib.WindowsPath.open"}:
        mode_position, newline, default_mode = 1, 5, "r"
    elif name in {"tempfile.NamedTemporaryFile", "tempfile.TemporaryFile", "tempfile.SpooledTemporaryFile"}:
        mode_position = 1 if name == "tempfile.SpooledTemporaryFile" else 0
        newline, default_mode = mode_position + 3, "w+b"
    elif isinstance(call.func, ast.Attribute) and call.func.attr == "open":
        # Exclude module APIs such as tarfile.open and os.open. For unknown
        # receivers, recognize Path.open's literal mode rather than treating
        # archive member names or network requests as filesystem modes.
        receiver = qualified_name(call.func.value, imports).split(".")[0]
        if receiver in {module.split(".")[0] for module in imports.values()}:
            return None
        mode_position, newline, default_mode = 0, 4, "r"
        mode = argument(call, "mode", mode_position)
        if not isinstance(mode, ast.Constant) or not isinstance(mode.value, str) or not set(mode.value) <= set("rwaxbt+"):
            return None
    else:
        return None
    mode = argument(call, "mode", mode_position, ast.Constant(default_mode))
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return newline if "b" not in mode.value and any(character in mode.value for character in "wax+") else None
    # Known file APIs with a dynamic mode must make their newline policy clear.
    return newline


def violations(source: str, filename: str, *, embedded: bool = False) -> list[str]:
    try:
        with warnings.catch_warnings():
            # Regexes and other data strings can look like Python expressions.
            # Only real source diagnostics should escape this embedded scan.
            if embedded:
                warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, filename=filename)
    except (SyntaxError, ValueError) as error:
        if embedded:
            return []  # Ordinary data literals need not be Python source.
        return [f"{filename}:{getattr(error, 'lineno', 1)}: invalid Python: {error}"]
    imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update({alias.asname or alias.name.split(".")[0]: alias.name if alias.asname else alias.name.split(".")[0] for alias in node.names})
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.update({alias.asname or alias.name: f"{node.module}.{alias.name}" for alias in node.names})
    result: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            position = newline_position(node, imports)
            if position is not None:
                newline = argument(node, "newline", position)
                if newline is None or isinstance(newline, ast.Constant) and newline.value is None:
                    result.append(
                        f"{filename}:{node.lineno}:{node.col_offset + 1}: implicit-newline: "
                        'specify newline="\\n" for portable text, an intentional newline policy, or write bytes'
                    )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Adjacent literals are already joined by the parser. Parse only;
            # never evaluate fixture data or execute embedded child programs.
            result.extend(violations(textwrap.dedent(node.value), f"{filename}:{node.lineno}:literal", embedded=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="*", default=[ROOT / name for name in ("src", "scripts", "tests")])
    args = parser.parse_args()
    paths: set[Path] = set()
    errors: list[str] = []
    for path in args.paths:
        if not path.exists():
            errors.append(f"{path}: input does not exist")
        elif path.is_dir():
            paths.update(path.rglob("*.py"))
        else:
            paths.add(path)
    for path in sorted(paths):
        try:
            errors.extend(violations(path.read_text(encoding="utf-8"), str(path)))
        except (OSError, UnicodeError) as error:
            errors.append(f"{path}: cannot read Python source: {error}")
    for error in errors:
        print(error, file=sys.stderr)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
