"""Generated Markdown links and portable relocation between directories."""

import posixpath
import re

from research_repo_tools.postprocess_changelog import (
    MAX_LINE_WIDTH,
    _backtick_span_end,
    _balanced_delimiter_end,
    _closes_code_fence,
    _opening_code_fence,
    _reflow_line,
)


class _GeneratedLinks:
    """Keep long generated links separate from their indivisible destinations.

    Labels are already Markdown-escaped and destinations already URL-encoded.
    Reserve reference uses in surrounding/prose text, excluding the old generated
    section so repeated publication chooses the same names. Short links retain
    their established spelling. Long destinations are never shortened or split.
    """

    def __init__(self, reserved: str) -> None:
        self._reserved = {match.casefold() for match in re.findall(r"\[\s*(rrt-performance-link-[0-9]+)\s*\]", reserved, re.IGNORECASE)}
        self._references: dict[str, str] = {}

    def render(self, label: str, destination: str, *, prefix: str = "", image: bool = False) -> str:
        start = prefix + ("!" if image else "") + f"[{label}]"
        inline = f"{start}({destination})"
        if len(inline) <= MAX_LINE_WIDTH:
            return inline
        if destination not in self._references:
            index = len(self._references) + 1
            while (name := f"rrt-performance-link-{index}") in self._reserved:
                index += 1
            self._reserved.add(name)
            self._references[destination] = name
        return f"{start}[{self._references[destination]}]"

    def definitions(self) -> str:
        # The newline before the destination is valid Markdown and survives
        # rumdl formatting. Angle brackets also protect parentheses in URLs.
        return "".join(f"\n[{name}]:\n  <{destination}>\n" for destination, name in self._references.items())


def _html_cell(value: str, tag: str) -> str:
    """Wrap escaped generated cell text without introducing visible characters.

    Comments carry physical newlines, including inside an indivisible value.
    Entities and the renderer's explicit line-break elements remain atomic.
    This accepts only the escaped cells produced by our report renderers.
    """
    lines = []
    line = f"<{tag}>"
    for token in re.findall(r"&(?:#[0-9]+|#x[0-9a-fA-F]+|[A-Za-z]+);|<br>|.", value):
        if len(line) + len(token) > MAX_LINE_WIDTH - 6:
            lines.append(line + "<!--")
            line = "-->"
        line += token
    lines.append(line + f"</{tag}>")
    return "\n".join(lines)


def _generated_report_layout(text: str) -> str:
    """Format our escaped report grammar; this is not a general Markdown parser.

    Wide tables use HTML so physical line wrapping preserves each displayed
    cell. Recorded URLs in cells are plain data, not Markdown autolinks.
    """
    source = text.splitlines()
    result: list[str] = []
    index = 0
    while index < len(source):
        line = source[index]
        if line.startswith("| "):
            table = []
            while index < len(source) and source[index].startswith("| "):
                table.append(source[index])
                index += 1
            if any(len(row) > MAX_LINE_WIDTH or re.search(r"https?://", row) for row in table):
                result.extend(["<table>", "<thead>", "<tr>"])
                result.extend(_html_cell(cell, "th") for cell in table[0][2:-2].split(" | "))
                result.extend(["</tr>", "</thead>", "<tbody>"])
                for row in table[2:]:
                    result.append("<tr>")
                    result.extend(_html_cell(cell, "td") for cell in row[2:-2].split(" | "))
                    result.append("</tr>")
                result.extend(["</tbody>", "</table>"])
            else:
                result.extend(table)
            continue
        if line.startswith("- ") and result and result[-1] and not result[-1].startswith(("- ", "  ")):
            result.append("")
        result.append(_reflow_line(line))
        index += 1
    return "\n".join(result) + "\n"


def relocate_links(text: str, prefix: str) -> str:
    """Rebase relative inline/image/reference destinations, keeping code opaque.

    Absolute URLs, site-root paths and fragment-only links retain their meaning.
    Escapes, query strings, fragments and optional link titles are preserved.
    """

    def destination(value: str) -> str:
        if not value or value.startswith(("/", "#", "?")) or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", value):
            return value
        path, *suffix = re.split(r"(?=[?#])", value, maxsplit=1)
        return posixpath.normpath(posixpath.join(prefix, path)) + "".join(suffix)

    def target(value: str) -> str:
        if value.startswith("<"):
            end = value.find(">")
            return "<" + destination(value[1:end]) + value[end:] if end >= 0 else value
        # The outer link delimiter has already been balanced. Spaces after
        # the destination introduce an optional title, not part of its path.
        match = re.match(r"(?:\\.|[^\s])+", value)
        return destination(match[0]) + value[match.end() :] if match else value

    output = []
    fence = None
    for line in text.splitlines(keepends=True):
        if fence is not None:
            output.append(line)
            if _closes_code_fence(line.rstrip("\r\n"), fence):
                fence = None
            continue
        fence = _opening_code_fence(line.rstrip("\r\n"))
        if fence is not None or line.startswith(("    ", "\t")):
            output.append(line)
            continue
        definition = re.match(r"( {0,3}\[[^\]\n]+\]:[ \t]*)(.*)", line)
        if definition:
            output.append(definition[1] + target(definition[2]) + line[definition.end() :])
            continue
        pieces = []
        position = 0
        while position < len(line):
            if line[position] == "\\" and position + 1 < len(line):
                pieces.append(line[position : position + 2])
                position += 2
                continue
            if line[position] == "`" and (end := _backtick_span_end(line, position)) is not None:
                pieces.append(line[position:end])
                position = end
                continue
            if line[position] == "[":
                label = _balanced_delimiter_end(line, position, "[", "]")
                if label is not None and label < len(line) and line[label] == "(":
                    end = _balanced_delimiter_end(line, label, "(", ")")
                    if end is not None:
                        pieces.append(line[position : label + 1] + target(line[label + 1 : end - 1]) + ")")
                        position = end
                        continue
            pieces.append(line[position])
            position += 1
        output.append("".join(pieces))
    return "".join(output)
