"""Declarative release selectors, shared by configuration and Python consumers."""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal


def relative_path(value: str) -> str:
    """Require a portable, normalized repository-relative file name."""
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or PureWindowsPath(value).drive
        or PurePosixPath(value).is_absolute()
        or any(part in {"", ".", "..", ".git"} for part in value.split("/"))
    ):
        raise ValueError(f"release path must be a normalized repository-relative path: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class ReleaseRule:
    """Select exactly ``count`` nonempty named ``value`` captures in one file.

    Patterns use Python MULTILINE regex syntax. ``exclude`` filters complete
    matches, allowing historical artifact links beside active links to survive.
    Fixed values are assertions only; source values contribute release edits.
    """

    path: str
    pattern: str
    value: str | None = None
    source: Literal["version", "tag", "previous-tag", "release-date"] | None = None
    count: int = 1
    exclude: str | None = None

    def __post_init__(self) -> None:
        relative_path(self.path)
        if (self.value is None) == (self.source is None):
            raise ValueError("release rule requires exactly one of value or source")
        if self.value is not None and (not isinstance(self.value, str) or not self.value):
            raise ValueError("release rule value must be a nonempty string")
        if self.source is not None and self.source not in {"version", "tag", "previous-tag", "release-date"}:
            raise ValueError("release rule source must be version, tag, previous-tag, or release-date")
        if type(self.count) is not int or self.count < 1:
            raise ValueError("release rule count must be a positive integer")
        if not isinstance(self.pattern, str) or not self.pattern:
            raise ValueError("release rule pattern must be a nonempty string")
        try:
            pattern = re.compile(self.pattern, re.MULTILINE)
            if "value" not in pattern.groupindex:
                raise ValueError("release rule pattern must contain a named (?P<value>...) group")
            if self.exclude is not None:
                if not isinstance(self.exclude, str) or not self.exclude:
                    raise ValueError("release rule exclude must be a nonempty pattern")
                re.compile(self.exclude)
        except re.error as error:
            raise ValueError(f"invalid release rule pattern: {error}") from error

    def matches(self, text: str) -> tuple[re.Match[str], ...]:
        matches = tuple(
            match for match in re.finditer(self.pattern, text, re.MULTILINE) if self.exclude is None or re.search(self.exclude, match.group(0)) is None
        )
        if len(matches) != self.count:
            raise ValueError(f"{self.path}: release rule requires exactly {self.count} matches; found {len(matches)}: {self.pattern}")
        if any(not match.group("value") for match in matches):
            raise ValueError(f"{self.path}: release rule captured an empty or missing value")
        return matches


@dataclass(frozen=True, slots=True)
class ReleasePolicy:
    date_policy: Literal["today", "declared"] = "today"
    final_changelog: bool = False
    required_files: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    rules: tuple[ReleaseRule, ...] = ()
    tag_policy: Literal["normalized-stable", "canonical-stable"] = "normalized-stable"

    def __post_init__(self) -> None:
        if not isinstance(self.tag_policy, str) or self.tag_policy not in {"normalized-stable", "canonical-stable"}:
            raise ValueError("tag policy must be normalized-stable or canonical-stable")
        if self.date_policy not in {"today", "declared"} or type(self.final_changelog) is not bool:
            raise ValueError("invalid release date or final-changelog policy")
        for name in ("required_files", "exclude", "rules"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        for path in (*self.required_files, *self.exclude):
            relative_path(path)
        for rule in self.rules:
            if not isinstance(rule, ReleaseRule):
                raise ValueError("release rules must be ReleaseRule instances")
            if self.excludes(rule.path):
                raise ValueError(f"release rule selects excluded historical file: {rule.path}")

    def excludes(self, path: str) -> bool:
        """Match root-relative POSIX glob patterns against historical files."""
        return any(PurePosixPath(path).full_match(pattern) for pattern in self.exclude)
