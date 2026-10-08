"""Opt-in source policy for literal dependency installation in notebook cells.

This is static policy, not a sandbox or an interpreter for dynamic commands.
Token positions protect Python string literals without rewriting cell sources.
"""

import ast
import io
import re
import shlex
import subprocess
import sys
import tokenize
from pathlib import PureWindowsPath
from warnings import catch_warnings

from research_repo_tools.notebook_lint import Diagnostic
from research_repo_tools.notebooks import Notebook

MUTATIONS = {"install", "uninstall", "add", "remove", "sync", "update", "upgrade", "create"}
READ_COMMANDS = {"list", "show", "freeze", "check", "search", "info", "help", "download", "lock", "export", "run", "exec", "--version"}
SHELL_CELL = re.compile(r"^%%(?:bash|sh|script\s+(?:bash|sh))\b")
PYTHON_CELL_MAGICS = {"capture", "debug", "prun", "time", "timeit"}
COMMAND_START = re.compile(r"^\s*(?:[A-Za-z_]\w*\s*=\s*)?(?:!|%(?:pip|conda|mamba)\b)|^\s*(?:python[\d.]*|pip[\d.]*|uv|conda|mamba|micromamba)\s")
_WINDOWS = sys.platform == "win32"


def _arguments(command: str, *, windows: bool) -> list[str]:
    lexer = shlex.shlex(command, posix=True)
    lexer.escape = ""  # Preserve Windows executable path separators.
    lexer.whitespace_split = True
    if windows:
        lexer.quotes = '"'
        lexer.commenters = ""
    return list(lexer)


def _shell_segments(command: str, *, windows: bool) -> list[list[str]]:
    """Split literal unquoted shell operators before discarding argument quotes."""
    parts = []
    start = index = 0
    quote = ""
    while index < len(command):
        character = command[index]
        if character == ("^" if windows else "\\") and (not quote if windows else quote != "'"):
            index += 2
            continue
        if quote:
            if character == quote:
                quote = ""
        elif character in ('"' if windows else "\"'"):
            quote = character
        elif character == "#" and not windows and (index == start or command[index - 1].isspace()):
            parts.append(command[start:index])
            newline = command.find("\n", index)
            start = index = len(command) if newline < 0 else newline + 1
            continue
        elif character in ("&|\r\n" if windows else ";&|\r\n"):
            parts.append(command[start:index])
            start = index + 1
        index += 1
    parts.append(command[start:])
    return [_arguments(part, windows=windows) for part in parts]


def _mutates(command: str | list[str], *, windows: bool = False) -> bool:
    try:
        segments = _shell_segments(command, windows=windows) if isinstance(command, str) else [command]
    except ValueError:
        return False  # Native syntax checks own malformed commands.
    for segment in segments:
        if not segment:
            continue
        while segment and (segment[0] in {"sudo", "env"} or re.fullmatch(r"[A-Za-z_]\w*=.*", segment[0])):
            segment = segment[1:]
        if not segment:
            continue
        name = PureWindowsPath(segment[0]).name.lower().removesuffix(".exe")
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
    lines = io.StringIO(source).readlines()
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


def _python_tree(lines: list[str], masked: list[str]) -> ast.Module | None:
    """Parse Python around IPython escapes without changing source line numbers."""
    scratch = list(lines)
    while True:
        try:
            with catch_warnings(action="ignore", category=SyntaxWarning):
                return ast.parse("\n".join(scratch))
        except SyntaxError as error:
            if error.lineno is None or not 1 <= error.lineno <= len(masked):
                return None
            index = error.lineno - 1
            visible = masked[index]
            if cell_magic := re.match(r"^\s*%%(\w+)", visible):
                if cell_magic[1] not in PYTHON_CELL_MAGICS:
                    return None  # Shell and other language bodies are not Python.
            escape = re.match(r"^([ \t]*)(?:[A-Za-z_]\w*[ \t]*=[ \t]*)?[!%]", visible)
            if escape is None or scratch[index] == escape[1] + "pass":
                return None
            scratch[index] = escape[1] + "pass"


def install_diagnostics(notebook: Notebook) -> list[Diagnostic]:
    """Report original cell identities and source lines; never execute a cell."""
    result = []
    for number, cell in enumerate(notebook.node.cells, 1):
        if cell.cell_type != "code":
            continue
        # Match Python's physical CR/LF lines; Unicode separators inside a
        # string are content, not extra source lines. Only the scratch view changes.
        source = cell.source.replace("\r\n", "\n").replace("\r", "\n")
        lines = source.split("\n")
        shell = bool(lines and SHELL_CELL.match(lines[0]))
        masked = lines if shell else _masked(source).split("\n")
        failures: set[int] = set()
        for line, (original, visible) in enumerate(zip(lines, masked, strict=True), 1):
            if shell and line > 1 or COMMAND_START.match(visible):
                command = re.sub(r"^\s*(?:[A-Za-z_]\w*\s*=\s*)?!+", "", original).lstrip().removeprefix("%")
                if _mutates(command, windows=_WINDOWS and not shell):
                    failures.add(line)
        tree = _python_tree(lines, masked)
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
                    # Popen's ninth positional parameter is shell; run/call/
                    # check_call/check_output forward their positional arguments.
                    positional_shell = node.args[8] if len(node.args) > 8 and not any(isinstance(item, ast.Starred) for item in node.args[:9]) else None
                    shell_argument = next((item.value for item in node.keywords if item.arg == "shell"), positional_shell)
                    try:
                        uses_shell = owner.id == "os" or shell_argument is not None and bool(ast.literal_eval(shell_argument))
                    except ValueError, TypeError:
                        uses_shell = False  # Dynamic shell selection is outside literal policy.
                    if isinstance(command, (list, tuple)):
                        # POSIX passes only argv[0] as sh's program; Windows
                        # serializes the complete vector before cmd.exe /c.
                        command = (subprocess.list2cmdline(command) if _WINDOWS else command[0] if command else "") if uses_shell else list(command)
                    elif not uses_shell:
                        # POSIX treats a string as one executable path. Windows
                        # passes a command line, but does not apply shell operators.
                        try:
                            command = _arguments(command, windows=True) if _WINDOWS else [command]
                        except ValueError:
                            continue
                    if _mutates(command, windows=uses_shell and _WINDOWS):
                        failures.add(node.lineno)
        result.extend(Diagnostic("dependency-install: move dependency changes to the locked project environment", number, line, 1) for line in sorted(failures))
    return result
