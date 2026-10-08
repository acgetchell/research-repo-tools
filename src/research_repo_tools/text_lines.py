"""Read-only raw UTF-8 line limits, without renderer-specific exemptions."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from research_repo_tools.selection import select_files

__all__ = ["LineCheck", "LineViolation", "check_lines", "inspect_lines"]


@dataclass(frozen=True, slots=True)
class LineViolation:
    path: str
    line: int
    length: int
    limit: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: line length {self.length} exceeds {self.limit}"


@dataclass(frozen=True, slots=True)
class LineCheck:
    files: int
    violations: tuple[LineViolation, ...]


def inspect_lines(path: Path, *, limit: int, label: str | None = None) -> tuple[LineViolation, ...]:
    """Count Unicode code points on every physical line; exclude CR/LF endings.

    Tabs, trailing spaces, combining marks, fences, tables and URLs all count.
    Universal newline reading recognizes LF, CRLF and CR, without treating
    Unicode separators as newlines. Decode/read failures raise, never pass.
    """
    if type(limit) is not int or limit <= 0:
        raise ValueError("raw line limit must be a positive integer")
    failures = []
    try:
        with path.open(encoding="utf-8", newline=None) as source:
            for number, line in enumerate(source, 1):
                length = len(line.removesuffix("\n"))
                if length > limit:
                    failures.append(LineViolation(label or str(path), number, length, limit))
    except (OSError, UnicodeError) as error:
        raise ValueError(f"{label or path}: failed to read UTF-8 text: {error}") from error
    return tuple(failures)


def check_lines(root: Path, *, limit: int, include: Sequence[str] = (), exclude: Sequence[str] = ()) -> LineCheck:
    """Check a complete shared fail-closed Git selection without writing files."""
    if type(limit) is not int or limit <= 0:
        raise ValueError("raw line limit must be a positive integer")
    names = select_files(root, include=include, exclude=exclude)
    failures = tuple(violation for name in names for violation in inspect_lines(root / name, limit=limit, label=name))
    return LineCheck(len(names), failures)
