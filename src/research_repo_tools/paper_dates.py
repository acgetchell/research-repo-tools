"""Explicit locale-independent TeX dates and reproducible UTC epochs."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

__all__ = ["PaperDate", "parse_source_date", "read_source_date"]

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
_DATE = re.compile(r"([A-Z][a-z]+) ([1-9]|[12][0-9]|3[01]), ([0-9]{4})")
_COMMAND = re.compile(r"(?<!\\)\\date(?![A-Za-z])\s*\{([^{}]*)\}")


@dataclass(frozen=True, slots=True)
class PaperDate:
    raw: str
    instant: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.instant, datetime) or self.instant.tzinfo is None or self.instant.utcoffset() != UTC.utcoffset(None):
            raise ValueError("paper date must be timezone-aware UTC")
        if any((self.instant.hour, self.instant.minute, self.instant.second, self.instant.microsecond)):
            raise ValueError("paper date must be UTC midnight")
        expected = f"{_MONTHS[self.instant.month - 1]} {self.instant.day}, {self.instant.year:04d}"
        if self.raw != expected:
            raise ValueError("paper date raw text and UTC instant disagree")

    @classmethod
    def from_raw(cls, raw: str) -> Self:
        match = _DATE.fullmatch(raw)
        if match is None or match[1] not in _MONTHS:
            raise ValueError(f"paper date must use 'Month day, year' with an English month: {raw!r}")
        try:
            return cls(raw, datetime(int(match[3]), _MONTHS.index(match[1]) + 1, int(match[2]), tzinfo=UTC))
        except ValueError as error:
            raise ValueError(f"invalid paper calendar date: {raw!r}") from error

    @property
    def source_date_epoch(self) -> int:
        # Arithmetic avoids platform time_t limits for dates before 1970.
        return (self.instant - datetime(1970, 1, 1, tzinfo=UTC)).days * 86400


def _uncomment(line: str) -> str:
    backslashes = 0
    for index, character in enumerate(line):
        if character == "%" and backslashes % 2 == 0:
            return line[:index]
        backslashes = backslashes + 1 if character == "\\" else 0
    return line


def parse_source_date(source: str) -> PaperDate:
    """Require one explicit uncommented date; this is not a general TeX parser."""
    source = "\n".join(_uncomment(line) for line in source.splitlines())
    commands = list(re.finditer(r"(?<!\\)\\date(?![A-Za-z])", source))
    matches = list(_COMMAND.finditer(source))
    if len(commands) != 1 or len(matches) != 1:
        raise ValueError(r"paper source must declare exactly one explicit \date{Month day, year}")
    return PaperDate.from_raw(matches[0][1])


def read_source_date(path: Path) -> PaperDate:
    try:
        return parse_source_date(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"{path}: failed to read paper source date: {error}") from error
