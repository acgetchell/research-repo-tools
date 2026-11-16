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
from dataclasses import dataclass
from pathlib import PureWindowsPath
from warnings import catch_warnings

from research_repo_tools.notebook_lint import Diagnostic
from research_repo_tools.notebooks import Notebook

MUTATIONS = {"install", "uninstall", "add", "remove", "sync", "update", "upgrade", "create"}
# Parse command boundaries, not occurrences of verbs anywhere in argv. In
# particular, a log filename named "info" or "install" is not a subcommand.
OPTION_VALUES = {
    "pip": {
        "--log",
        "--proxy",
        "--retries",
        "--timeout",
        "--exists-action",
        "--trusted-host",
        "--cert",
        "--client-cert",
        "--cache-dir",
        "--python",
        "--keyring-provider",
        "--use-feature",
        "--use-deprecated",
        "--resume-retries",
    },
    "uv": {
        "--cache-dir",
        "--color",
        "--directory",
        "--project",
        "--config-file",
        "--python",
        "-p",
        "--index",
        "--default-index",
        "--index-url",
        "-i",
        "--extra-index-url",
        "--find-links",
        "-f",
        "--index-strategy",
        "--keyring-provider",
        "--allow-insecure-host",
        "--with",
        "--with-editable",
        "--with-requirements",
        "--extra",
        "--no-extra",
        "--group",
        "--no-group",
        "--only-group",
        "--package",
        "--env-file",
        "--resolution",
        "--prerelease",
        "--fork-strategy",
        "--exclude-newer",
        "--link-mode",
        "--config-setting",
        "-C",
        "--config-settings-package",
        "--upgrade-package",
        "-P",
        "--refresh-package",
        "--python-platform",
    },
    "conda": {"--name", "-n", "--prefix", "-p", "--root-prefix", "-r", "--rc-file", "--cwd"},
}
OPTION_FLAGS = {
    "pip": {
        "--debug",
        "--isolated",
        "--require-virtualenv",
        "--verbose",
        "-v",
        "--quiet",
        "-q",
        "--no-input",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--no-color",
        "--no-python-version-warning",
    },
    "uv": {
        "--verbose",
        "-v",
        "--quiet",
        "-q",
        "--offline",
        "--no-cache",
        "-n",
        "--native-tls",
        "--system-certs",
        "--no-progress",
        "--no-config",
        "--managed-python",
        "--no-managed-python",
        "--no-python-downloads",
        "--locked",
        "--frozen",
        "--no-sync",
        "--no-project",
        "--active",
        "--isolated",
        "--no-env-file",
        "--all-extras",
        "--all-groups",
        "--no-default-groups",
        "--all-packages",
        "--no-dev",
        "--only-dev",
        "--no-build",
        "--no-build-isolation",
        "--no-sources",
        "--no-index",
        "--upgrade",
        "-U",
        "--refresh",
    },
    "conda": {"--verbose", "-v", "--quiet", "-q", "--json", "--offline", "--no-plugins", "--yes", "-y", "--no-capture-output", "--live-stream"},
}
SHELL_CELL = re.compile(r"^%%(?:bash|sh|script\s+(?:bash|sh))\b")
SHELL_REDIRECTION = re.compile(r"&>>?|<<<|<>|<&|>&|>\||>>?|<")
PYTHON_CELL_MAGICS = {"capture", "debug", "prun", "time", "timeit"}
COMMAND_START = re.compile(r"^\s*(?:[A-Za-z_]\w*\s*=\s*)?(?:!|%(?:pip|conda|mamba)\b)|^\s*(?:python[\d.]*|pip[\d.]*|uv|conda|mamba|micromamba)\s")
_WINDOWS = sys.platform == "win32"


def _arguments(command: str, *, windows: bool, posix_escapes: bool = False) -> list[str]:
    lexer = shlex.shlex(command, posix=True)
    if not posix_escapes:
        lexer.escape = ""  # Preserve Windows executable path separators.
    lexer.whitespace_split = True
    if windows:
        lexer.quotes = '"'
        lexer.commenters = ""
    return list(lexer)


def _heredoc_word(command: str, index: int) -> tuple[str, int, bool]:
    """Read one literal delimiter with shell quote removal, without expansion."""
    word: list[str] = []
    quote = ""
    started = False
    quoted = False
    while index < len(command):
        character = command[index]
        if not started and character in " \t":
            index += 1
            continue
        if not quote and character in " \t\r\n;&|<>()":
            break
        if character == "\\" and quote != "'" and index + 1 < len(command):
            following = command[index + 1]
            if not quote or following in '$`"\\\n':
                if following != "\n":
                    word.append(following)
                    started = True
                    quoted = True
                index += 2
                continue
        started = True
        if character == quote:
            quote = ""
        elif not quote and character in "\"'":
            quote = character
            quoted = True
        else:
            word.append(character)
        index += 1
    if quote or not started:
        raise ValueError("incomplete heredoc delimiter")
    return "".join(word), index, quoted


@dataclass(frozen=True)
class _ShellSource:
    text: str
    lines: tuple[int, ...]

    @classmethod
    def original(cls, text: str) -> _ShellSource:
        lines = []
        line = 1
        for character in text:
            lines.append(line)
            if character == "\n":
                line += 1
        return cls(text, (*lines, line))

    def slice(self, start: int, end: int) -> _ShellSource:
        return _ShellSource(self.text[start:end], self.lines[start : end + 1])

    def unquote(self, escapes: str, *, strip_tabs: bool = False) -> _ShellSource:
        # Preserve each retained character's physical line, including when an
        # outer heredoc removes a newline before the child shell parses it.
        characters = []
        lines = []
        escaped_characters = set(escapes)
        index = 0
        leading = True
        while index < len(self.text):
            character = self.text[index]
            if strip_tabs and leading and character == "\t":
                index += 1
                continue
            if character == "\\" and self.text[index + 1 : index + 2] in escaped_characters:
                index += 1
                character = self.text[index]
                if character == "\n":
                    index += 1
                    continue
            leading = character == "\n"
            characters.append(character)
            lines.append(self.lines[index])
            index += 1
        return _ShellSource("".join(characters), (*lines, self.lines[-1]))


def _heredoc_end(command: str, index: int, delimiter: str, strip_tabs: bool, quoted: bool) -> tuple[int, int]:
    """Return the literal body's end and the position after its delimiter."""
    logical_start = index
    fragments: list[str] = []
    while index < len(command):
        if not fragments:
            logical_start = index
        end = command.find("\n", index)
        end = len(command) if end < 0 else end
        body_line = command[index:end]
        index = end + (end < len(command))
        if not quoted and end < len(command) and (len(body_line) - len(body_line.rstrip("\\"))) % 2:
            fragments.append(body_line[:-1])
            continue
        body_line = "".join([*fragments, body_line])
        if (body_line.lstrip("\t") if strip_tabs else body_line) == delimiter:
            return logical_start, index
        fragments.clear()
    return len(command), index


def _backquote_end(command: str, index: int) -> int | None:
    # Unlike $(...), backquotes close before the extracted shell code is parsed.
    while index < len(command):
        if command[index] == "`":
            return index
        index += 2 if command[index] == "\\" else 1
    return None


def _command_substitution(source: _ShellSource, index: int) -> tuple[_ShellSource, int] | None:
    """Locate a literal substitution without evaluating its output."""
    command = source.text
    if command[index] == "`":
        end = _backquote_end(command, index + 1)
        return None if end is None else (source.slice(index + 1, end).unquote("\\$`"), end + 1)
    start = index + 2
    index = start
    contexts: list[tuple[str, list[tuple[str, bool, bool]], bool]] = [("", [], False)]
    while index < len(command):
        quote, heredocs, arithmetic = contexts[-1]
        character = command[index]
        if character == "\n" and not quote and heredocs:
            index += 1
            for delimiter, strip_tabs, quoted in heredocs:
                _body_end, index = _heredoc_end(command, index, delimiter, strip_tabs, quoted)
            heredocs.clear()
            continue
        if character == "\\" and quote != "'":
            index += 2
            continue
        if character == ")" and not quote:
            contexts.pop()
            if not contexts:
                return source.slice(start, index), index + 1
        elif quote == "'":
            if character == quote:
                contexts[-1] = ("", heredocs, arithmetic)
        elif command.startswith("$(", index):
            contexts.append(("", [], command.startswith("$((", index)))
            index += 1
        elif character == "`":
            end = _backquote_end(command, index + 1)
            if end is None:
                return None
            index = end + 1
            continue
        elif quote:
            if character == quote:
                contexts[-1] = ("", heredocs, arithmetic)
        elif character in "\"'":
            contexts[-1] = (character, heredocs, arithmetic)
        elif not arithmetic and command.startswith("<<<", index):
            index += 3
            continue
        elif not arithmetic and command.startswith("<<", index):
            strip_tabs = command.startswith("<<-", index)
            try:
                delimiter, index, quoted = _heredoc_word(command, index + 2 + strip_tabs)
            except ValueError:
                return None
            heredocs.append((delimiter, strip_tabs, quoted))
            continue
        elif character == "(":
            contexts.append(("", [], arithmetic or command.startswith("((", index)))
        elif character == "#" and (index == start or command[index - 1].isspace()):
            newline = command.find("\n", index)
            index = len(command) if newline < 0 else newline
            continue
        index += 1
    return None


def _heredoc_substitutions(source: _ShellSource) -> list[_ShellSource]:
    """Ordinary heredoc quotes are data; only escaped expansion markers are inert."""
    body = source.text
    result = []
    index = 0
    while index < len(body):
        if body[index] == "\\" and body[index + 1 : index + 2] in {"\\", "$", "`", "\n"}:
            index += 2
            continue
        if (body.startswith("$(", index) and not body.startswith("$((", index) or body[index] == "`") and (
            substitution := _command_substitution(source, index)
        ):
            content, end = substitution
            result.append(content)
            index = end
            continue
        index += 1
    return result


def _shell_reads_stdin(command: str) -> bool:
    try:
        arguments = _arguments(command, windows=False, posix_escapes=True)
    except ValueError:
        return False
    while arguments and (arguments[0] in {"env", "sudo"} or re.fullmatch(r"[A-Za-z_]\w*=.*", arguments[0])):
        arguments = arguments[1:]
    if not arguments or PureWindowsPath(arguments[0]).name.removesuffix(".exe") not in {"bash", "sh"}:
        return False
    stdin = False
    for index, argument in enumerate(arguments[1:], 1):
        if argument == "--":
            return stdin or index == len(arguments) - 1
        if argument in {"--noprofile", "--norc", "--posix", "--restricted", "--login"}:
            continue
        if argument.startswith("-"):
            if not re.fullmatch(r"-[abefhiklmnprstuvxBCEHPT]+", argument) or "n" in argument:
                return False  # -c, syntax-only, help, and unsupported option modes.
            stdin |= "s" in argument
        else:
            return stdin  # A script filename unless -s selected standard input.
    return True


def _shell_parts(source: _ShellSource, *, windows: bool, posix_escapes: bool) -> tuple[list[tuple[int, str]], list[_ShellSource]]:
    """Retain physical command lines while parsing quotes and continuations."""
    command = source.text
    parts = []
    nested = []
    part: list[str] = []
    index = 0
    start_line = source.lines[0]
    quote = ""
    ansi_quote = False
    arithmetic_depth = 0
    redirection_pending = False
    redirection_start: int | None = None
    heredocs: list[tuple[str, bool, bool, list[str], bool]] = []
    while index < len(command):
        character = command[index]
        line = source.lines[index]
        if not quote and redirection_start is not None and character in " \t\r\n;&|<>":
            del part[redirection_start:]
            redirection_start = None
        if redirection_pending and not character.isspace():
            redirection_start = len(part)
            redirection_pending = False
        if character == "\n" and not quote and heredocs:
            parts.append((start_line, "".join(part)))
            part = []
            index += 1
            for number, (delimiter, strip_tabs, quoted, receiver, stdin) in enumerate(heredocs):
                body_start = index
                body_end, index = _heredoc_end(command, index, delimiter, strip_tabs, quoted)
                body = source.slice(body_start, body_end)
                if not quoted:
                    nested.extend(_heredoc_substitutions(body))
                if stdin and not any(later[3] is receiver and later[4] for later in heredocs[number + 1 :]) and _shell_reads_stdin("".join(receiver)):
                    nested.append(body.unquote("" if quoted else "\\$`\n", strip_tabs=strip_tabs))
            heredocs.clear()
            continue
        if character == ("^" if windows else "\\") and (not quote if windows else quote != "'" or ansi_quote):
            escaped = command[index : index + 2]
            if not windows and escaped == "\\\n":
                index += 2
                continue
            if not part:
                start_line = line
            if posix_escapes and escaped == "\\" and not quote:
                escaped = ""  # Bash/sh discard an unpaired escape at EOF.
            part.append(escaped)
            index += 2
            continue
        if not part and character.isspace():
            index += 1
            continue
        if not part:
            start_line = line
        if not windows and quote != "'" and (command.startswith("$(", index) and not command.startswith("$((", index) or character == "`"):
            if substitution := _command_substitution(source, index):
                content, end = substitution
                nested.append(content)
                part.append("__dynamic_substitution__")
                index = end
                continue
        if quote:
            if character == quote:
                quote = ""
                ansi_quote = False
        elif character in ('"' if windows else "\"'"):
            ansi_quote = character == "'" and part[-1:] == ["$"]
            quote = character
        elif not windows and command.startswith("((", index):
            arithmetic_depth += 2
            part.append("((")
            index += 2
            continue
        elif not windows and arithmetic_depth and character in "()":
            arithmetic_depth += 1 if character == "(" else -1
        elif not windows and not arithmetic_depth and command.startswith("<<", index) and not command.startswith("<<<", index):
            strip_tabs = command.startswith("<<-", index)
            delimiter, end, quoted = _heredoc_word(command, index + 2 + strip_tabs)
            descriptor = re.search(r"(?:^|\s)(\d+)$", "".join(part))
            if descriptor:
                del part[-len(descriptor[1]) :]
            heredocs.append((delimiter, strip_tabs, quoted, part, descriptor is None or descriptor[1] == "0"))
            # Redirection syntax is not an argument, and its quoting must not
            # reach the host-oriented argument parser or affect body parsing.
            part.append(" ")
            index = end
            continue
        elif not windows and not arithmetic_depth and (redirect := SHELL_REDIRECTION.match(command, index)):
            descriptor = re.search(r"(?:^|\s)(\d+)$", "".join(part)) if character != "&" else None
            if descriptor:
                del part[-len(descriptor[1]) :]
            if character == "<" and (descriptor is None or descriptor[1] == "0"):
                heredocs = [(delimiter, tabs, quoted, receiver, stdin and receiver is not part) for delimiter, tabs, quoted, receiver, stdin in heredocs]
            # Scan the operand normally so its substitutions still execute,
            # then discard it before classifying the receiving command's argv.
            part.append(" ")
            redirection_pending = True
            index += len(redirect[0])
            continue
        elif character == "#" and not windows and (not part or command[index - 1].isspace()):
            parts.append((start_line, "".join(part)))
            part = []
            newline = command.find("\n", index)
            index = len(command) if newline < 0 else newline
            continue
        elif character in ("&|\r\n" if windows else ";&|\r\n"):
            parts.append((start_line, "".join(part)))
            part = []
            index += 1
            continue
        part.append(character)
        index += 1
    if redirection_start is not None:
        del part[redirection_start:]
    parts.append((start_line, "".join(part)))
    return parts, nested


def _shell_segments(command: str, *, windows: bool, posix_escapes: bool = False) -> list[tuple[int, list[str]]]:
    result = []
    pending = [(_ShellSource.original(command), windows, posix_escapes)]
    while pending:
        source, windows, posix_escapes = pending.pop()
        parts, nested = _shell_parts(source, windows=windows, posix_escapes=posix_escapes)
        for line, part in parts:
            try:
                arguments = _arguments(part, windows=windows, posix_escapes=posix_escapes)
            except ValueError:
                continue  # One unsupported segment must not erase other installs.
            result.append((line, arguments))
        pending.extend((body, False, True) for body in nested)
    return result


def _command_arguments(arguments: list[str], kind: str, *, uv_run: bool = False) -> list[str]:
    """Drop known leading options with their values; leave one literal command."""
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            return arguments[index + 1 :]
        if not argument.startswith("-"):
            return arguments[index:]
        option, equals, _value = argument.partition("=")
        if option in OPTION_VALUES[kind]:
            index += 1 if equals else 2
        elif argument in OPTION_FLAGS[kind] or re.fullmatch(r"-[qv]+", argument) or uv_run and argument in {"-m", "--module"}:
            index += 1
        elif any(argument.startswith(short) and len(argument) > 2 for short in OPTION_VALUES[kind] if len(short) == 2):
            index += 1  # Attached short-option value, such as uv -p3.15.
        else:
            return []  # Help/version, unknown options, and dynamic syntax are not commands.
    return []


def _python_module(arguments: list[str], *, launcher: bool) -> list[str]:
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if module := re.fullmatch(r"-[bBdEiIOPqRsSuUx]*m(.*)", argument):
            remaining = arguments[index + 1 :]
            return [module[1], *remaining] if module[1] else remaining
        if argument in {"-W", "-X"}:
            index += 2
        elif argument.startswith(("-W", "-X")) or re.fullmatch(r"-[bBdEiIOPqRsSuUx]+", argument):
            index += 1
        elif launcher and re.fullmatch(r"-(?:[23](?:\.\d+)*(?:-\d+)?|V:.+)", argument):
            index += 1
        else:
            return []  # Script, -c, stdin, help, or an unsupported option.
    return []


def _installer_mutates(segment: list[str]) -> bool:
    while segment:
        name = PureWindowsPath(segment[0]).name.lower().removesuffix(".exe")
        remaining = segment[1:]
        if name in {"sudo", "env"} or re.fullmatch(r"[A-Za-z_]\w*=.*", segment[0]):
            segment = remaining
        elif name in {"py", "pyw"} or re.fullmatch(r"(?:pythonw?|pypy)(?:\d+(?:\.\d+)*)?t?", name):
            segment = _python_module(remaining, launcher=name in {"py", "pyw"})
        else:
            kind = "pip" if re.fullmatch(r"pip(?:\d+(?:\.\d+)*)?", name) else "conda" if name in {"conda", "mamba", "micromamba"} else name
            if kind not in OPTION_VALUES:
                return False
            remaining = _command_arguments(remaining, kind)
            uv_run = kind == "uv" and remaining[:1] == ["run"]
            if remaining and (kind == "uv" and remaining[0] in {"pip", "tool", "python"} or kind == "conda" and remaining[0] == "env"):
                remaining = _command_arguments(remaining[1:], kind)
            if not remaining:
                return False
            if remaining[0] == "run" and kind in {"uv", "conda"}:
                segment = _command_arguments(remaining[1:], kind, uv_run=uv_run)
                continue
            return remaining[0] in MUTATIONS
    return False


def _mutates(command: str | list[str], *, windows: bool = False) -> bool:
    try:
        segments = [arguments for _, arguments in _shell_segments(command, windows=windows)] if isinstance(command, str) else [command]
    except ValueError:
        return False  # Native syntax checks own malformed commands.
    return any(_installer_mutates(segment) for segment in segments)


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
        if shell:
            try:
                # Explicit Bash/sh cells use POSIX quoting on every host;
                # native command heuristics still preserve Windows paths.
                failures.update(
                    line + 1 for line, arguments in _shell_segments("\n".join(lines[1:]), windows=False, posix_escapes=True) if _installer_mutates(arguments)
                )
            except ValueError:
                pass  # Native syntax checks own malformed commands.
        for line, (original, visible) in enumerate(zip(lines, masked, strict=True), 1):
            if not shell and COMMAND_START.match(visible):
                command = re.sub(r"^\s*(?:[A-Za-z_]\w*\s*=\s*)?!+", "", original).lstrip().removeprefix("%")
                if _mutates(command, windows=_WINDOWS):
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
