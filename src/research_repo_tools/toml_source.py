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


def replace_string(text: str, table: str, key: str, value: str) -> str:
    """Replace a standalone string assignment, retaining surrounding TOML bytes.

    Dotted and quoted keys work through key_line. Inline-table declarations are
    rejected rather than reserializing unrelated configuration and comments.
    """
    import json

    line = key_line(text, table, key)
    lines = text.splitlines(keepends=True)
    offset = sum(map(len, lines[: line - 1]))
    declaration = _KEY.match(text, offset + len(lines[line - 1]) - len(lines[line - 1].lstrip()))
    if declaration is None:
        raise ValueError(f"[{table}].{key} must use a standalone string assignment")
    start = declaration.end()
    while start < len(text) and text[start].isspace():
        start += 1
    match = _STRING.match(text, start)
    if match is None:
        raise ValueError(f"[{table}].{key} must be a string")
    # Retain ordinary quote style; multiline values become canonical scalars.
    quoted = f"'{value}'" if text[start] == "'" and "'" not in value else json.dumps(value)
    result = text[:start] + quoted + text[match.end() :]
    tomllib.loads(result)
    return result


def set_value(text: str, table: str, key: str, value: str) -> str:
    """Set a TOML literal, retaining unrelated bytes and rejecting inline owners."""
    import json

    tomllib.loads("value = " + value)
    encoded_key = key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    try:
        line = key_line(text, table, key)
    except ValueError:
        for number, statement in _statements(text):
            if statement.startswith("[") and not statement.startswith("[[") and _key_path(tomllib.loads(statement)) == tuple(table.split(".")):
                if not lines[number - 1].endswith("\n"):
                    lines[number - 1] += newline
                lines.insert(number, f"{encoded_key} = {value}{newline}")
                result = "".join(lines)
                break
        else:
            result = text.rstrip("\r\n") + newline + newline + f"[{table}]{newline}{encoded_key} = {value}{newline}"
    else:
        offset = sum(map(len, lines[: line - 1]))
        match = _KEY.match(text, offset + len(lines[line - 1]) - len(lines[line - 1].lstrip()))
        if match is None:
            raise ValueError(f"[{table}].{key} must use a standalone assignment")
        start = end = match.end()
        while text[start].isspace():
            start += 1
        end = start
        depth = 0
        while end < len(text):
            char = text[end]
            if char in "\"'":
                literal = _STRING.match(text, end)
                if literal is None:
                    raise ValueError("unsupported TOML literal")
                end = literal.end()
                continue
            if char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
            elif char == "#":
                if depth == 0:
                    break
                end = text.find("\n", end)
                if end < 0:
                    raise ValueError("unterminated TOML value")
                continue
            elif char in "\r\n" and depth == 0:
                break
            end += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        result = text[:start] + value + text[end:]
    tomllib.loads(result)
    return result


def replace_array_strings(text: str, table: str, key: str, replacements: dict[str, str]) -> str:
    """Replace exact string array entries without changing comments or includes."""
    import json

    line = key_line(text, table, key)
    lines = text.splitlines(keepends=True)
    offset = sum(map(len, lines[: line - 1]))
    match = _KEY.match(text, offset + len(lines[line - 1]) - len(lines[line - 1].lstrip()))
    if match is None:
        raise ValueError(f"[{table}].{key} requires a standalone array")
    position = match.end()
    while text[position].isspace():
        position += 1
    if text[position] != "[":
        raise ValueError(f"[{table}].{key} requires a standalone array")
    depth = 0
    edits = []
    while position < len(text):
        char = text[position]
        if char == "#":
            position = text.find("\n", position)
            if position < 0:
                raise ValueError("unterminated array")
        elif char in "\"'":
            literal = _STRING.match(text, position)
            if literal is None:
                raise ValueError("unsupported TOML string")
            value = tomllib.loads("value = " + literal.group())["value"]
            if depth == 1 and value in replacements:
                replacement = replacements[value]
                quoted = f"'{replacement}'" if char == "'" and "'" not in replacement else json.dumps(replacement)
                edits.append((position, literal.end(), quoted))
            position = literal.end()
            continue
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                break
        position += 1
    for start, end, value in reversed(edits):
        text = text[:start] + value + text[end:]
    tomllib.loads(text)
    return text
