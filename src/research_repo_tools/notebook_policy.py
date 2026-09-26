"""Opt-in source policy for literal dependency installation in notebook cells.

This is static policy, not a sandbox or an interpreter for dynamic commands.
Token positions protect Python string literals without rewriting cell sources.
"""

import ast
import io
import re
import shlex
import tokenize
from pathlib import PureWindowsPath

from research_repo_tools.notebook_lint import Diagnostic
from research_repo_tools.notebooks import Notebook

MUTATIONS = {"install", "uninstall", "add", "remove", "sync", "update", "upgrade", "create"}
READ_COMMANDS = {"list", "show", "freeze", "check", "search", "info", "help", "download", "lock", "export", "run", "exec", "--version"}
SHELL_CELL = re.compile(r"^%%(?:bash|sh|script\s+(?:bash|sh))\b")
COMMAND_START = re.compile(r"^\s*(?:[A-Za-z_]\w*\s*=\s*)?(?:!|%(?:pip|conda|mamba)\b)|^\s*(?:python[\d.]*|pip[\d.]*|uv|conda|mamba|micromamba)\s")


def _mutates(command: str | list[str]) -> bool:
    try:
        if isinstance(command, str):
            lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
            lexer.whitespace_split = True
            tokens = list(lexer)
        else:
            tokens = command
    except ValueError:
        return False  # Native syntax checks own malformed commands.
    # Shell command separators are recognized; quoted/dynamic shell programs
    # and aliases are intentionally outside this narrow policy.
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in {";", "&&", "||", "|", "&"}:
            segments.append([])
        else:
            segments[-1].append(token)
    for segment in segments:
        if not segment:
            continue
        if segment[0] in {"sudo", "env"}:
            segment = segment[1:]
        while segment and re.fullmatch(r"[A-Za-z_]\w*=.*", segment[0]):
            segment = segment[1:]
        if not segment:
            continue
        name = PureWindowsPath(segment[0]).name.removesuffix(".exe")
        remaining = segment[1:]
        if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", name) and remaining[:2] == ["-m", "pip"]:
            name, remaining = "pip", remaining[2:]
        if name == "uv" and remaining[:1] == ["pip"]:
            remaining = remaining[1:]
        if name not in {"uv", "conda", "mamba", "micromamba"} and not re.fullmatch(r"pip(?:\d+(?:\.\d+)*)?", name):
            continue
        for argument in remaining:
            if argument in READ_COMMANDS:
                break
            if argument in MUTATIONS:
                return True
    return False


def _masked(source: str) -> str:
    """Blank string/comment token spans in a scratch view, retaining positions."""
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    result = list(source)
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type not in {tokenize.STRING, tokenize.COMMENT, tokenize.FSTRING_MIDDLE, tokenize.TSTRING_MIDDLE}:
                continue
            start = offsets[token.start[0] - 1] + token.start[1]
            end = offsets[token.end[0] - 1] + token.end[1]
            for index in range(start, end):
                if result[index] not in "\r\n":
                    result[index] = " "
    except tokenize.TokenError, IndentationError:
        pass  # Native notebook lint diagnoses invalid syntax.
    return "".join(result)


def install_diagnostics(notebook: Notebook) -> list[Diagnostic]:
    """Report original cell identities and source lines; never execute a cell."""
    result = []
    for number, cell in enumerate(notebook.node.cells, 1):
        if cell.cell_type != "code":
            continue
        source = cell.source
        lines = source.splitlines()
        shell = bool(lines and SHELL_CELL.match(lines[0]))
        masked = lines if shell else _masked(source).splitlines()
        failures: set[int] = set()
        for line, (original, visible) in enumerate(zip(lines, masked, strict=True), 1):
            if shell and line > 1 or COMMAND_START.match(visible):
                command = re.sub(r"^\s*(?:[A-Za-z_]\w*\s*=\s*)?!+", "", original).lstrip().removeprefix("%")
                if _mutates(command):
                    failures.add(line)
        try:
            tree = ast.parse(source)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                owner = node.func.value
                if not isinstance(owner, ast.Name) or (owner.id, node.func.attr) not in {
                    ("subprocess", "run"),
                    ("subprocess", "Popen"),
                    ("subprocess", "call"),
                    ("subprocess", "check_call"),
                    ("subprocess", "check_output"),
                    ("os", "system"),
                }:
                    continue
                keyword = "args" if owner.id == "subprocess" else "command"
                argument = node.args[0] if node.args else next((item.value for item in node.keywords if item.arg == keyword), None)
                if argument is None:
                    continue
                try:
                    command = ast.literal_eval(argument)
                except ValueError, TypeError:
                    continue
                if isinstance(command, str) or isinstance(command, (list, tuple)) and all(isinstance(item, str) for item in command):
                    if _mutates(command if isinstance(command, str) else list(command)):
                        failures.add(node.lineno)
        result.extend(Diagnostic("dependency-install: move dependency changes to the locked project environment", number, line, 1) for line in sorted(failures))
    return result
