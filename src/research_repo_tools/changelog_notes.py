"""Release-scoped extraction without weakening whole-document validation."""

import re

from research_repo_tools import archive_changelog as archive
from research_repo_tools.postprocess_changelog import _backtick_span_end, _balanced_delimiter_end, _closes_code_fence, _opening_code_fence

_BOUNDARY = re.compile(r"^ {0,3}#{1,2}(?:[ \t]+|$)")
_REFERENCE = re.compile(r"^ {0,3}\[([^\]\n]+)\]:[ \t]+\S+.*$")


def _scan(text: str) -> tuple[list[str], list[int], dict[str, list[str]], set[int]]:
    """Locate section boundaries and reference definitions outside closed fences."""
    lines = text.splitlines()
    boundaries: list[int] = []
    definitions: dict[str, list[str]] = {}
    definition_lines: set[int] = set()
    fence = None
    for index, line in enumerate(lines):
        if fence is not None:
            if _closes_code_fence(line, fence):
                fence = None
            continue
        fence = _opening_code_fence(line)
        if fence is not None:
            continue
        if _BOUNDARY.match(line):
            boundaries.append(index)
        elif re.match(r"^ {0,3}##\[", line):
            raise ValueError(f"ambiguous changelog section boundary at line {index + 1}")
        if match := _REFERENCE.fullmatch(line):
            label = " ".join(match.group(1).split()).casefold()
            definitions.setdefault(label, []).append(line.strip())
            definition_lines.add(index)
    if fence is not None:
        raise ValueError("unclosed changelog fence makes release boundaries ambiguous")
    return lines, boundaries, definitions, definition_lines


def reference_definitions(text: str, required: set[str] | None = None) -> dict[str, str]:
    """Reject conflicting definitions, optionally only for requested labels."""
    definitions = _scan(text)[2]
    selected: dict[str, str] = {}
    for label, values in definitions.items():
        if required is not None and label not in required:
            continue
        # Label case and whitespace do not distinguish Markdown references.
        destinations = {value.split("]:", 1)[1].strip() for value in values}
        if len(destinations) != 1:
            raise ValueError(f"Conflicting reference definitions for {label!r}")
        selected[label] = values[0]
    return selected


def _required_references(body: str) -> set[str]:
    """Collect reference labels while keeping literal code and inline links opaque."""
    prose: list[str] = []
    fence = None
    for line in body.splitlines():
        if fence is not None:
            if _closes_code_fence(line, fence):
                fence = None
            continue
        fence = _opening_code_fence(line)
        if fence is None:
            prose.append(line)
    text = "\n".join(prose)
    labels: set[str] = set()
    index = 0
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "`" and (end := _backtick_span_end(text, index)) is not None:
            index = end
            continue
        if text[index] == "[" and (end := _balanced_delimiter_end(text, index, "[", "]")) is not None:
            label = text[index + 1 : end - 1]
            if end < len(text) and text[end] == "(" and (link_end := _balanced_delimiter_end(text, end, "(", ")")) is not None:
                index = link_end
                continue
            if end < len(text) and text[end] == "[" and (reference_end := _balanced_delimiter_end(text, end, "[", "]")) is not None:
                label = text[end + 1 : reference_end - 1] or label
                end = reference_end
            labels.add(" ".join(label.split()).casefold())
            index = end
            continue
        index += 1
    return labels


def extract(text: str, version: str) -> tuple[str, str] | None:
    """Return the unique requested body and heading, ignoring unrelated defects.

    Every level-one/two heading is a boundary, including malformed historical
    release headings. A malformed requested heading still fails. Unterminated
    fences and missing heading separators cannot establish reliable boundaries.
    """
    lines, boundaries, _definitions, definition_lines = _scan(text)
    target = re.compile(rf"^ {{0,3}}##[ \t]+\[?v?{re.escape(version)}(?=$|[\]\s(])")
    matches: list[tuple[str, str]] = []
    for position, start in enumerate(boundaries):
        heading = lines[start]
        if not target.match(heading):
            continue
        archive._parse_release_heading(heading, start + 1)
        end = boundaries[position + 1] if position + 1 < len(boundaries) else len(lines)
        body = "\n".join(lines[index] for index in range(start + 1, end) if index not in definition_lines).strip()
        matches.append((body, heading))
    if len(matches) > 1:
        raise ValueError(f"Duplicate release heading {version!r}")
    if not matches:
        return None
    body, heading = matches[0]
    if not body:
        raise ValueError(f"empty release notes for v{version}")
    required = _required_references(body)
    definitions = reference_definitions(text, required)
    links = archive._format_link_defs(definitions, required)
    return body + ("\n\n" + links if links else "") + "\n", heading
