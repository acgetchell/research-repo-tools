"""Recover explicit issue closures from the original commits in a generated history."""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import cast

from research_repo_tools._git_checkout import READ_ONLY_OPTIONS
from research_repo_tools.postprocess_changelog import _closes_code_fence, _literal_directive_lines, _opening_code_fence, _reflow_line
from research_repo_tools.process import run_git_bytes

_KEYWORD = r"(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)"
_SLUG = r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*"
_TARGET = rf"(?:(?:{_SLUG})?#[1-9][0-9]*|https://github\.com/{_SLUG}/issues/[1-9][0-9]*)"
_CLOSING_LINE = re.compile(rf"{_KEYWORD}:?[ \t]+{_TARGET}(?:,[ \t]+(?:{_KEYWORD}:?[ \t]+)?{_TARGET})*\.?[ \t]*", re.IGNORECASE | re.ASCII)
_REFERENCE = re.compile(rf"(?:https://github\.com/(?P<url>{_SLUG})/issues/|(?P<slug>{_SLUG})?#)(?P<number>[1-9][0-9]*)", re.IGNORECASE | re.ASCII)
_CLOSING_NOTE = re.compile(rf"^Closes \[(?:{_SLUG})?#[1-9][0-9]*\]\(")
_COMMIT = re.compile(rf"\[`(?P<short>[a-f0-9]{{7}})`\]\(https://github\.com/(?P<slug>{_SLUG})/commit/(?P<oid>[a-f0-9]{{40}}(?:[a-f0-9]{{24}})?)\)$")


def _prose(lines: list[str]) -> Iterator[tuple[int, str]]:
    fence = None
    for index, line in enumerate(lines):
        if fence is not None:
            if _closes_code_fence(line, fence):
                fence = None
            continue
        fence = _opening_code_fence(line)
        if fence is None:
            yield index, line


def _entry_oid(line: str, repository: str) -> str | None:
    match = _COMMIT.search(line)
    if match is None or match["slug"].casefold() != repository.casefold():
        return None
    oid = cast("str", match["oid"])
    return oid if oid.startswith(match["short"]) else None


def _commit_lines(lines: list[str], repository: str) -> Iterator[tuple[int, str]]:
    """Locate a packaged entry's terminal hash link, including wrapped titles."""
    in_title = False
    for index, line in _prose(lines):
        if line.startswith("- "):
            in_title = True
        elif not line.startswith("  ") or not index or not lines[index - 1].strip():
            in_title = False
        if in_title and (oid := _entry_oid(line, repository)):
            yield index, oid
            in_title = False


def _closing_links(message: str, repository: str) -> list[str]:
    """Read standalone directives, preserving authored order across repositories."""
    links: dict[tuple[str, str], str] = {}
    lines = message.replace("\r\n", "\n").split("\n")
    directives = {index: line for index, line in _prose(lines) if _CLOSING_LINE.fullmatch(line)}
    example_lines = _literal_directive_lines(lines, set(directives))
    for index, line in directives.items():
        if index in example_lines:
            continue
        for match in _REFERENCE.finditer(line):
            slug = match["url"] or match["slug"] or repository
            number = match["number"]
            local = slug.casefold() == repository.casefold()
            label = f"#{number}" if local else f"{slug}#{number}"
            links.setdefault((slug.casefold(), number), f"[{label}](https://github.com/{repository if local else slug}/issues/{number})")
    return list(links.values())


def read_closing_notes(text: str, root: Path, repository: str) -> dict[str, str]:
    """Read exactly the rendered commit objects in one offline, read-only Git call."""
    # At this point git-cliff emits the title and hash on a single line.
    # Every body line is indented, including any example commit links.
    commits = dict.fromkeys(
        oid for _, line in _prose(text.replace("\r\n", "\n").split("\n")) if line.startswith("- ") and (oid := _entry_oid(line, repository))
    )
    if not commits:
        return {}
    result = run_git_bytes(
        [
            *READ_ONLY_OPTIONS,
            "log",
            "--no-walk=unsorted",
            "--no-decorate",
            "--no-notes",
            "--no-color",
            "--no-show-signature",
            "--encoding=UTF-8",
            "--format=%H%x00%B",
            "-z",
            "--stdin",
        ],
        cwd=root,
        input="".join(f"{oid}\n" for oid in commits).encode("ascii"),
    )
    fields = result.stdout.removesuffix(b"\0").split(b"\0")
    notes = {}
    for raw_oid, body in zip(fields[::2], fields[1::2], strict=True):
        if links := _closing_links(body.decode("utf-8", errors="replace"), repository):
            notes[raw_oid.decode("ascii")] = _reflow_line("  Closes " + ", ".join(links) + ".")
    return notes


def add_closing_notes(text: str, notes: dict[str, str], repository: str) -> str:
    """Enrich unannotated entries; archived and freshly rendered notes use the same rule."""
    lines = text.split("\n")
    for index, oid in reversed(list(_commit_lines(lines, repository))):
        if oid not in notes:
            continue
        note = notes[oid]
        start = index + 1
        while start < len(lines) and not lines[start].strip():
            start += 1
        prefix = " ".join(line.strip() for line in lines[start : start + 2])
        if _CLOSING_NOTE.match(prefix):
            # A formatter may wrap the same links at a different column.
            parts = []
            for line in lines[start:]:
                parts.append(line.strip())
                if not line.strip() or line.endswith("."):
                    break
            if " ".join(parts) != " ".join(note.split()):
                raise ValueError(f"conflicting closed issue links for commit {oid}")
        else:
            lines[index + 1 : index + 1] = note.split("\n")
    return "\n".join(lines)
