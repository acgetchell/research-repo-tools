"""Locate keys in validated TOML without treating value text as declarations."""

import re
import tomllib
from collections.abc import Iterator

KEY_PATTERN = r"""(?:[A-Za-z0-9_-]+|"(?:[^"\\]|\\.)*"|'[^']*')(?:\s*\.\s*(?:[A-Za-z0-9_-]+|"(?:[^"\\]|\\.)*"|'[^']*'))*"""
_KEY = re.compile(rf"(?P<key>{KEY_PATTERN})\s*=")
_STRING = re.compile(
    r'''"""(?:[^"\\]|\\[\s\S]|"(?!""))*"""(?:"{1,2})?'''
    r"|'''(?:[^']|'(?!''))*'''(?:'{1,2})?"
    r'''|"(?:[^"\\]|\\[^\r\n])*"'''
    r"|'[^'\r\n]*'"
)


def _statements(text: str) -> Iterator[tuple[int, str]]:
    """Split outside strings, comments, arrays, and inline tables."""
    start = position = depth = 0
    line = start_line = 1
    while position < len(text):
        char = text[position]
        if char in "\"'":
            match = _STRING.match(text, position)
            if match is None:
                raise ValueError(f"unsupported TOML string at line {line}")
            line += match.group().count("\n")
            position = match.end()
            continue
        if char == "#":
            position = text.find("\n", position)
            if position < 0:
                position = len(text)
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "\n":
            if depth == 0:
                statement = text[start:position].strip()
                if statement and not statement.startswith("#"):
                    yield start_line, statement
                start = position + 1
                start_line = line + 1
            line += 1
        position += 1
    statement = text[start:].strip()
    if statement and not statement.startswith("#"):
        yield start_line, statement


def _key_path(data: object) -> tuple[str, ...]:
    """Decode quoted/dotted names through tomllib, including array headers."""
    result: list[str] = []
    while isinstance(data, dict) and len(data) == 1:
        key, data = next(iter(data.items()))
        result.append(key)
        if isinstance(data, list):
            data = data[0]
    return tuple(result)


def key_line(text: str, table: str, key: str, *, index: int | None = None) -> int:
    """Locate a table key or array-table entry, respecting TOML lexical context.

    Full TOML validity is checked before scanning. Only declaration positions
    are tracked; values remain owned by tomllib and retain their original bytes.
    """
    tomllib.loads(text)
    target = tuple(table.split("."))
    current: tuple[str, ...] = ()
    array_indices: dict[tuple[str, ...], int] = {}
    current_index = None
    for line, statement in _statements(text):
        if statement.startswith("["):
            current = _key_path(tomllib.loads(statement))
            if statement.startswith("[["):
                current_index = array_indices.get(current, -1) + 1
                array_indices[current] = current_index
            else:
                current_index = None
            continue
        match = _KEY.match(statement)
        if match is None:
            continue
        path = _key_path(tomllib.loads(match.group("key") + " = 0"))
        if current + path == target + (key,) and current_index == index:
            return line
    label = f"[[{table}]] entry {index + 1}" if index is not None else f"[{table}]"
    raise ValueError(f"{label} is missing {key}")
