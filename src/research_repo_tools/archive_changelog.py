#!/usr/bin/env -S uv run
"""Archive completed minor series from CHANGELOG.md into per-minor files.

Parses the full CHANGELOG.md (produced by git-cliff + postprocess-changelog)
into version blocks, groups them by minor series (X.Y), and writes:

  - ``docs/archives/changelog/X.Y.md`` for each completed minor series
  - A trimmed ``CHANGELOG.md`` containing only the preamble, Unreleased,
    the active minor series, and an Archives link section

The active minor is detected from the first tagged release heading after
Unreleased.  All other minors are archived.

Usage:
    archive-changelog                      # default: CHANGELOG.md
    archive-changelog path/to/CHANGELOG.md
    archive-changelog --archive-dir docs/archives/changelog
"""

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, cast

from research_repo_tools.files import _write_text_atomic, _write_texts_transactionally
from research_repo_tools.markdown import relocate_links
from research_repo_tools.postprocess_changelog import _closes_code_fence, _opening_code_fence, normalize_entry_headings_text, postprocess_text
from research_repo_tools.process import format_exception_diagnostics

if TYPE_CHECKING:
    from collections.abc import Sequence

# Matches any bracketed level-2 heading reserved for changelog versions.
_BRACKETED_HEADING_RE = re.compile(r"^## \[")

_SEMVER_NUMERIC_IDENTIFIER = r"(?:0|[1-9]\d*)"
_SEMVER_PRERELEASE_IDENTIFIER = r"(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
_SEMVER_BUILD_IDENTIFIER = r"[0-9A-Za-z-]+"
_SEMVER_PATTERN = (
    rf"{_SEMVER_NUMERIC_IDENTIFIER}\.{_SEMVER_NUMERIC_IDENTIFIER}\.{_SEMVER_NUMERIC_IDENTIFIER}"
    rf"(?:-{_SEMVER_PRERELEASE_IDENTIFIER}(?:\.{_SEMVER_PRERELEASE_IDENTIFIER})*)?"
    rf"(?:\+{_SEMVER_BUILD_IDENTIFIER}(?:\.{_SEMVER_BUILD_IDENTIFIER})*)?"
)

# Matches one exact release heading with an optional ISO date.
_RELEASE_HEADING_RE = re.compile(rf"^## \[v?(?P<version>{_SEMVER_PATTERN})\](?:\([^\s]+\))?(?: - (?P<date>\d{{4}}-\d{{2}}-\d{{2}}))?\s*$")
_UNRELEASED_HEADING_RE = re.compile(r"^## \[Unreleased\](?:\([^\s]+\))?\s*$")

# Matches a reference-style link definition: ``[label]: URL``
_LINK_DEF_RE = re.compile(r"^\[([^\]]+)\]:\s+\S+")

# Archive directory relative to the repository root.
_DEFAULT_ARCHIVE_DIR = "docs/archives/changelog"

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParsedChangelog:
    """A changelog whose version-heading invariants have been established."""

    preamble: str
    unreleased: str | None
    version_blocks: tuple[tuple[str, str], ...]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _minor_key(version: str) -> str:
    """Return the ``X.Y`` minor key for a semver version string.

    Parameters:
        version: A version string like ``0.7.2`` or ``1.2.3-rc.1``.

    Returns:
        The first two numeric components joined by a dot (e.g. ``0.7``).

    Raises:
        ValueError: If *version* does not contain at least two dot-separated components.
    """
    parts = version.split(".")
    if len(parts) < 2:
        msg = f"Expected a version with at least two components (X.Y), got: {version!r}"
        raise ValueError(msg)
    return f"{parts[0]}.{parts[1]}"


def _version_sort_key(label: str) -> tuple[bool, tuple[int, ...], tuple[tuple[int, int, str], ...]]:
    """Return a sort key for a version label that orders by semantic version.

    Non-numeric labels (e.g. ``unreleased``) sort after all numeric versions.
    Numeric parts are compared as integers so that ``0.10`` sorts after ``0.9``.

    Parameters:
        label: A version label like ``0.7.2``, ``0.10``, or ``unreleased``.

    Returns:
        A tuple suitable for use as a sort key.
    """
    label_without_build = label.split("+", 1)[0]
    core, separator, prerelease = label_without_build.partition("-")
    parts = core.split(".")
    try:
        nums = tuple(int(p) for p in parts)
    except ValueError:
        # Non-numeric labels ("unreleased") sort last (True > False).
        return (True, (), ())

    if not separator:
        prerelease_key: tuple[tuple[int, int, str], ...] = ((2, 0, ""),)
    else:
        prerelease_key = tuple((0, int(part), "") if part.isdecimal() else (1, 0, part) for part in prerelease.split("."))

    return (False, nums, prerelease_key)


def _is_strictly_older(version: str, preceding_version: str) -> bool:
    """Return whether *version* has lower SemVer precedence than its predecessor."""
    version_key = _version_sort_key(version)
    preceding_key = _version_sort_key(preceding_version)
    return version_key < preceding_key


def _extract_link_defs(text: str) -> tuple[str, dict[str, str]]:
    """Separate trailing reference-style link definitions from changelog text.

    git-cliff appends reference-style link definitions at the bottom of
    CHANGELOG.md for every version heading.  When the changelog is split
    into per-version blocks these definitions must be distributed to the
    correct output files so that headings like ``## [0.7.2]`` resolve and
    no unused definitions trigger markdownlint MD053.

    Parameters:
        text: The full changelog text.

    Returns:
        A 2-tuple of (*cleaned_text*, *link_defs*) where *link_defs* maps
        lowercase labels to their full definition lines.
    """
    lines = text.rstrip("\n").split("\n")
    link_defs: dict[str, str] = {}

    # Walk backwards from the end, collecting link-def and blank lines.
    i = len(lines) - 1
    while i >= 0:
        line = lines[i]
        m = _LINK_DEF_RE.match(line)
        if m:
            label = m.group(1).casefold()
            if label in link_defs and link_defs[label] != line:
                raise ValueError(f"Conflicting reference definitions for {label!r}")
            link_defs[label] = line
            i -= 1
        elif line.strip() == "":
            i -= 1
        else:
            break

    cleaned = "\n".join(lines[: i + 1])
    return cleaned.rstrip("\n") + "\n", link_defs


def _parse_release_heading(heading_line: str, line_number: int) -> str:
    """Return the trusted SemVer label from one release heading."""
    release_match = _RELEASE_HEADING_RE.fullmatch(heading_line)
    if release_match is None:
        msg = f"Unrecognized changelog heading at line {line_number}: {heading_line!r}"
        raise ValueError(msg)

    release_date = release_match.group("date")
    if release_date is not None:
        try:
            date.fromisoformat(release_date)
        except ValueError as err:
            msg = f"Invalid release date at line {line_number}: {release_date!r}"
            raise ValueError(msg) from err

    return cast("str", release_match.group("version"))


def parse_changelog(text: str) -> ParsedChangelog:
    """Parse a full changelog into trusted preamble and version blocks.

    Parameters:
        text: The full contents of CHANGELOG.md.

    Returns:
        An immutable parsed changelog. ``unreleased`` is ``None`` when no
        ``## [Unreleased]`` block exists. Each item in ``version_blocks`` is a
        ``(semver_label, full_heading_block)`` pair in strict newest-first
        order.

    Raises:
        ValueError: If a reserved bracketed heading is unknown, a release
            label or date is invalid, headings are duplicated, ``Unreleased``
            is not first, or releases are not newest-first.
    """
    lines = text.split("\n")

    # Locate all ``## [`` headings.
    headings: list[int] = []
    active_fence = None
    for i, line in enumerate(lines):
        if active_fence is not None:
            if _closes_code_fence(line, active_fence):
                active_fence = None
            continue
        active_fence = _opening_code_fence(line)
        if active_fence is not None:
            continue
        if line == "## Archives":
            lines = lines[:i]
            break
        if _BRACKETED_HEADING_RE.match(line):
            headings.append(i)

    if not headings:
        return ParsedChangelog(text, None, ())

    preamble = "\n".join(lines[: headings[0]])

    unreleased: str | None = None
    version_blocks: list[tuple[str, str]] = []
    seen_versions: dict[str, int] = {}
    previous_version: str | None = None

    for heading_index, start in enumerate(headings):
        end = headings[heading_index + 1] if heading_index + 1 < len(headings) else len(lines)
        block = "\n".join(lines[start:end])

        heading_line = lines[start]
        line_number = start + 1
        if _UNRELEASED_HEADING_RE.fullmatch(heading_line):
            if unreleased is not None:
                msg = f"Duplicate Unreleased heading at line {line_number}"
                raise ValueError(msg)
            if heading_index != 0:
                msg = f"Unreleased heading must be the first version heading (line {line_number})"
                raise ValueError(msg)
            unreleased = block
            continue

        version = _parse_release_heading(heading_line, line_number)

        if version in seen_versions:
            msg = f"Duplicate release heading {version!r} at line {line_number}; first seen at line {seen_versions[version]}"
            raise ValueError(msg)

        if previous_version is not None and not _is_strictly_older(version, previous_version):
            msg = f"Release heading out of order at line {line_number}: {version!r} must be older than preceding {previous_version!r}"
            raise ValueError(msg)

        seen_versions[version] = line_number
        previous_version = version
        version_blocks.append((version, block))

    return ParsedChangelog(preamble, unreleased, tuple(version_blocks))


def group_by_minor(
    version_blocks: Sequence[tuple[str, str]],
) -> dict[str, list[tuple[str, str]]]:
    """Group version blocks by their ``X.Y`` minor key.

    Preserves insertion order (newest first within each minor).

    Parameters:
        version_blocks: List of ``(version, block_text)`` pairs.

    Returns:
        An ordered dict mapping minor keys to their version blocks.
    """
    groups: dict[str, list[tuple[str, str]]] = {}
    for ver, block in version_blocks:
        key = _minor_key(ver)
        groups.setdefault(key, []).append((ver, block))
    return groups


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _format_link_defs(link_defs: dict[str, str], labels: set[str]) -> str:
    """Return the subset of *link_defs* whose labels are in *labels*.

    The definitions are returned in reverse-sorted order (matching the
    convention that git-cliff uses: ``[unreleased]`` first, then newest
    version to oldest).
    """
    relevant = [link_defs[label] for label in sorted(link_defs, key=_version_sort_key, reverse=True) if label in labels]
    return "\n".join(relevant) if relevant else ""


def _render_archive(
    minor: str,
    blocks: Sequence[tuple[str, str]],
    link_defs: dict[str, str] | None = None,
) -> str:
    """Render a single minor-series archive without publishing it."""
    parts = [f"# Changelog - {minor}.x\n"]
    for _ver, block in blocks:
        parts.append(block)

    text = "\n".join(parts)

    # Append only the reference-style link definitions for this archive.
    if link_defs:
        versions = _referenced_labels(text)
        defs_text = _format_link_defs(link_defs, versions)
        if defs_text:
            text = text.rstrip("\n") + "\n\n" + defs_text

    # Normalize archive output too; archived blocks can preserve historical
    # commit-body indentation that no longer appears in the trimmed root file.
    return postprocess_text(text)


def _referenced_labels(text: str) -> set[str]:
    """Keep reference definitions used by headings or release-note bodies."""
    return {match.casefold() for match in re.findall(r"\[([^\]\n]+)\]", text)}


def write_archive(
    archive_dir: Path,
    minor: str,
    blocks: list[tuple[str, str]],
    link_defs: dict[str, str] | None = None,
) -> Path:
    """Write an archive file for a single minor series.

    Parameters:
        archive_dir: Directory for archive files.
        minor: The ``X.Y`` minor key.
        blocks: Version blocks belonging to this minor, newest first, using
            the ``(semver_label, full_heading_block)`` shape returned by
            ``parse_changelog``. The archive writer preserves each provided
            block verbatim after the generated archive title.
        link_defs: Optional mapping of lowercase labels to reference-style
            link definition lines.  Only definitions matching versions in
            *blocks* are included.

    Returns:
        The path of the written archive file.
    """
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"{minor}.md"

    _write_text_atomic(path, _render_archive(minor, blocks, link_defs))
    return path


def _existing_archive_updates(archive_dir: Path, excluded: set[Path] | None = None) -> list[tuple[Path, str]]:
    """Return required postprocessing updates for existing archives."""
    if not archive_dir.is_dir():
        return []

    excluded = excluded or set()
    updates: list[tuple[Path, str]] = []
    for path in sorted(archive_dir.glob("*.md")):
        if path in excluded:
            continue
        text = path.read_text(encoding="utf-8")
        processed = normalize_entry_headings_text(text)
        if processed != text:
            updates.append((path, processed))
    return updates


def _postprocess_existing_archives(archive_dir: Path) -> None:
    """Apply changelog postprocessing to existing archives transactionally."""
    _write_texts_transactionally(_existing_archive_updates(archive_dir))


def _merge_archive(path: Path, minor: str, blocks: list[tuple[str, str]], definitions: dict[str, str]) -> str:
    """Retain earlier patches and reject conflicting already-retained content."""
    if path.is_symlink():
        raise ValueError(f"Changelog output must not be a symlink: {path}")
    if not path.exists():
        return _render_archive(minor, blocks, definitions)
    if not path.is_file():
        raise IsADirectoryError(f"output path exists but is not a file: {path}")
    original, retained_definitions = _extract_link_defs(path.read_text(encoding="utf-8"))
    retained = parse_changelog(original)
    if retained.unreleased or any(_minor_key(version) != minor for version, _ in retained.version_blocks):
        raise ValueError(f"archive contains releases outside its minor series: {path}")
    merged = dict(retained.version_blocks)
    for version, block in blocks:
        if version in merged and postprocess_text(merged[version]) != postprocess_text(block):
            raise ValueError(f"conflicting retained release {version} in {path}")
        merged[version] = block
    used = _referenced_labels("\n".join(block for _, block in blocks))
    for label in used & definitions.keys():
        if label in retained_definitions and retained_definitions[label] != definitions[label]:
            raise ValueError(f"conflicting retained reference {label!r} in {path}")
        retained_definitions[label] = definitions[label]
    ordered = sorted(merged.items(), key=lambda item: _version_sort_key(item[0]), reverse=True)
    rendered = _render_archive(minor, ordered, retained_definitions)
    # Preserve an existing archive introduction in addition to release bodies.
    generated_preamble = parse_changelog(rendered).preamble
    return retained.preamble + rendered[len(generated_preamble) :]


def build_root(
    preamble: str,
    unreleased: str | None,
    active_blocks: list[tuple[str, str]],
    archived_minors: list[str],
    archive_dir_rel: str,
) -> str:
    """Assemble the trimmed root CHANGELOG.md content.

    Parameters:
        preamble: Text before the first ``## `` heading.
        unreleased: The full Unreleased block, or ``None`` if absent.
        active_blocks: Version blocks for the active minor series.
        archived_minors: Sorted list of archived ``X.Y`` minor keys.
        archive_dir_rel: Relative path to the archive directory from the changelog file.

    Returns:
        The full text for the trimmed CHANGELOG.md.
    """
    parts: list[str] = [preamble]

    if unreleased:
        parts.append(unreleased)

    for _ver, block in active_blocks:
        parts.append(block)

    if archived_minors:
        # Build the Archives section.
        archive_lines = ["## Archives\n"]
        archive_lines.append("Older releases are archived by minor series:\n")
        archive_lines.extend(f"- [{minor}.x]({archive_dir_rel}/{minor}.md)" for minor in archived_minors)
        archive_lines.append("")
        parts.append("\n".join(archive_lines))

    return postprocess_text("\n".join(parts))


def _archive_dir_link_prefix(archive_dir: Path, changelog_parent: Path, *, warn_outside: bool = True) -> str:
    """Return the Markdown link prefix from a changelog to its archive directory."""
    try:
        return archive_dir.relative_to(changelog_parent).as_posix()
    except ValueError:
        try:
            archive_dir_rel = Path(os.path.relpath(archive_dir, changelog_parent)).as_posix()
        except ValueError as err:
            raise ValueError("Cannot link changelog archives across different filesystem roots") from err
        if warn_outside and (archive_dir_rel == ".." or archive_dir_rel.startswith("../") or Path(archive_dir_rel).is_absolute()):
            LOGGER.warning(
                "Archive directory %s is outside changelog directory %s; generated Markdown links use %s",
                archive_dir,
                changelog_parent,
                archive_dir_rel,
            )
        return archive_dir_rel


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def archive_changelog(
    changelog_path: Path,
    archive_dir: Path | None = None,
) -> None:
    """Split a changelog into root + per-minor archive files.

    Parameters:
        changelog_path: Path to the full CHANGELOG.md.
        archive_dir: Directory for archive files.  Defaults to
            ``docs/archives/changelog`` relative to *changelog_path*'s parent.
    """
    if archive_dir is None:
        archive_dir = changelog_path.parent / _DEFAULT_ARCHIVE_DIR

    text = changelog_path.read_text(encoding="utf-8")

    # Separate trailing reference-style link definitions before parsing
    # so they can be distributed to the correct output files.
    text, link_defs = _extract_link_defs(text)

    parsed = parse_changelog(text)

    if not parsed.version_blocks:
        _postprocess_existing_archives(archive_dir)
        return  # nothing to archive

    groups = group_by_minor(parsed.version_blocks)
    minor_keys = list(groups.keys())

    # Active minor = first minor that appears (newest release).
    active_minor = minor_keys[0]

    # Render every minor except the active one before publishing any output.
    archived_minors = [path.stem for path in archive_dir.glob("*.md") if re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", path.stem)]
    planned_writes: list[tuple[Path, str]] = []
    relocation = _archive_dir_link_prefix(changelog_path.parent, archive_dir, warn_outside=False)
    for minor in minor_keys[1:]:
        archive_path = archive_dir / f"{minor}.md"
        blocks = [(version, relocate_links(block, relocation)) for version, block in groups[minor]]
        definitions = {label: relocate_links(line, relocation) for label, line in link_defs.items()}
        planned_writes.append((archive_path, _merge_archive(archive_path, minor, blocks, definitions)))
        archived_minors.append(minor)

    if not archived_minors:
        _postprocess_existing_archives(archive_dir)
        return  # only one minor series — nothing to archive yet

    archive_dir_rel = _archive_dir_link_prefix(archive_dir, changelog_path.parent)

    root_text = build_root(
        parsed.preamble,
        parsed.unreleased,
        groups[active_minor],
        sorted(set(archived_minors), key=_version_sort_key, reverse=True),
        archive_dir_rel,
    )

    # Append reference-style link definitions for active versions.
    if link_defs:
        labels = _referenced_labels(root_text)
        if parsed.unreleased is not None:
            labels.add("unreleased")
        defs_text = _format_link_defs(link_defs, labels)
        if defs_text:
            root_text = root_text.rstrip("\n") + "\n\n" + defs_text + "\n"

    planned_writes.append((changelog_path, root_text))
    planned_targets = {path for path, _text in planned_writes}
    planned_writes.extend(_existing_archive_updates(archive_dir, planned_targets))
    _write_texts_transactionally(planned_writes)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``archive-changelog``."""
    parser = argparse.ArgumentParser(
        prog="archive-changelog",
        description="Archive completed minor series from CHANGELOG.md.",
        suggest_on_error=True,
        color=False,
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="CHANGELOG.md",
        help="Path to CHANGELOG.md (default: CHANGELOG.md)",
    )
    parser.add_argument(
        "--archive-dir",
        default=None,
        help=f"Archive output directory (default: {_DEFAULT_ARCHIVE_DIR})",
    )
    args = parser.parse_args(argv)

    changelog = Path(args.path)
    if not changelog.is_file():
        print(f"Error: {changelog} not found", file=sys.stderr)
        return 1

    archive_dir = Path(args.archive_dir) if args.archive_dir else None
    try:
        archive_changelog(changelog, archive_dir)
    except BaseExceptionGroup as err:
        rollback_errors, unhandled = err.split((OSError, ValueError))
        if rollback_errors is not None:
            print(f"Error: {format_exception_diagnostics(err)}", file=sys.stderr)
        if unhandled is not None:
            raise unhandled from None
        return 1
    except (OSError, ValueError) as err:
        print(f"Error: {changelog}: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
