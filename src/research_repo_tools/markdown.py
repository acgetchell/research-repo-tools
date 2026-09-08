"""Portable link relocation when a Markdown document moves between directories."""

import posixpath
import re

from research_repo_tools.postprocess_changelog import (
    _backtick_span_end,
    _balanced_delimiter_end,
    _closes_code_fence,
    _opening_code_fence,
)


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
