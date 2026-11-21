#!/usr/bin/env -S uv run
"""Post-process a git-cliff generated CHANGELOG.md.

Applies lightweight markdown hygiene that is difficult to express in
Tera templates:

  1. Inject summary sections (Breaking Changes, Merged Pull Requests).
  2. Reflow long lines at word boundaries, preserving markdown links
     and code spans as atomic tokens (MD013).
  3. Tag bare fenced code blocks with a language (MD040).
  4. Normalize indented commit-body headings (MD023).
  5. Strip trailing blank lines (MD012).

Usage:
    postprocess-changelog                     # default: CHANGELOG.md
    postprocess-changelog path/to/CHANGELOG.md
"""

import argparse
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from research_repo_tools.files import replace_many
from research_repo_tools.process import ExecutableNotFoundError, run_safe_command

# Markdown line-length limit used by this project.
MAX_LINE_WIDTH = 160


@dataclass(frozen=True, slots=True)
class _CodeFence:
    """Delimiter evidence for an open Markdown fenced code block."""

    delimiter: str
    length: int


_FENCE_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")


# Version section heading: ## [X.Y.Z], ## [vX.Y.Z], or ## [Unreleased]
_VERSION_RE = re.compile(
    r"^## \[(?:v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?|Unreleased)\]"
    r"(?:\([^\s]+\))?(?:\s+-\s+\d{4}-\d{2}-\d{2})?\s*$"
)

# Changelog category headings that delimit generated entries.
_CHANGELOG_SECTION_HEADINGS = {
    "Added",
    "Breaking Changes",
    "Changed",
    "Deprecated",
    "Dependencies",
    "Documentation",
    "Fixed",
    "Maintenance",
    "Merged Pull Requests",
    "Performance",
    "Removed",
    "Security",
    "⚠️ Breaking Changes",
}

# PR link: [#123](https://github.com/.../pull/123)
_PR_LINK_RE = re.compile(r"\[#(\d+)\]\(https://github\.com/[^)]+/pull/\d+\)")

# Commit-hash link to strip from summary lines.
_COMMIT_LINK_RE = re.compile(r"\s*\[`[a-f0-9]{7}`\]\(https://github\.com/[^)]+/commit/[a-f0-9]+\)")
_ENTRY_COMMIT_RE = re.compile(r"\[`[a-f0-9]{7}`\]\((?P<url>https://github\.com/[^()\s]+/commit/[a-f0-9]+)\)[ \t]*$")

# Leading git-cliff breaking marker to strip from normalized comparison keys.
_BREAKING_MARKER_RE = re.compile(r"^\s*(?:[-*]\s+)?\[?\*\*breaking\*\*\]?\s*", re.IGNORECASE)

# Leading ``* `` list marker to normalise to ``- `` (MD004).
_STAR_LIST_RE = re.compile(r"^(\s*)\* ")

# Unicode bullets from historical changelog entries: ``•  item`` → ``- item``.
_BULLET_SYMBOL_RE = re.compile(r"^(\s*)•\s+")

# Extra spaces after list marker: ``-   `` → ``- `` (MD030).
_LIST_MARKER_SPACE_RE = re.compile(r"^(\s*-)\s{2,}")

# Historical configurations HTML-escaped co-author email angle brackets.
# Markdown needs real brackets for email autolinks, including during rumdl checks.
_ESCAPED_EMAIL_RE = re.compile(r"&lt;(?P<email>[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})&gt;", re.IGNORECASE)

# Angle brackets used by Markdown syntax must survive prose escaping.
_AUTOLINK_RE = re.compile(r"<(?:[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*|[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+)>")
_REFERENCE_DEFINITION_RE = re.compile(r" {0,3}\[[^\]\n]+\]:[^\n]*")
_BLOCKQUOTE_PREFIX_RE = re.compile(r"[ \t]*(?:>[ \t]*)+")

# Indented ATX headings from commit bodies: ``  ## Title`` → ``#### Title``.
_INDENTED_ATX_HEADING_RE = re.compile(r"^(?P<indent>\s+)#{1,6}\s+(?P<title>.*?)(?:\s+#+\s*)?$")

# Isolated indented bold headings from historical commit bodies.
_INDENTED_BOLD_HEADING_RE = re.compile(r"^\s+\*\*(?P<title>[^*].*?)\*\*\s*$")

# Historical squash bodies can also contain unindented ATX headings inside a
# generated entry. Demote those without touching release/category headings.
_ENTRY_ATX_HEADING_RE = re.compile(r"^#{2,3}\s+(?P<title>.*?)(?:\s+#+\s*)?$")

# ATX headings after normalization. Used for final heading-specific cleanup.
_ATX_HEADING_RE = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.*?)(?:\s+#+\s*)?$")

# Markdown code spans used in generated text.

# Bare glob-like identifiers can be parsed as emphasis by Markdown linters.
# Match complete slash-delimited paths and Rust paths so suffixes are never
# wrapped in isolation; the inline-span scanner below excludes real emphasis.
_WILDCARD_IDENTIFIER_RE = re.compile(
    r"(?<![`*A-Za-z0-9_:/.-])"
    r"((?:\.?[A-Za-z_][A-Za-z0-9_.-]*/)*(?:[A-Za-z_][A-Za-z0-9_]*::)*"
    r"\.?[A-Za-z_][A-Za-z0-9_-]*\*+(?:[A-Za-z0-9_-]+)?(?:\.[A-Za-z0-9_-]+)*)"
    r"(?![`A-Za-z0-9_*/-])"
)

# Squash-merge commit bodies often contain inner conventional-commit
# headings from the PR branch: ``* fix: thing``. After MD004 normalization
# those become ordinary list items, which makes them look like separate
# generated commits. Treat them as prose headings inside the parent entry.
_SQUASH_HEADING_RE = re.compile(r"^(?P<indent>\s*)-\s+(?P<prefix>[A-Za-z]+(?:\([^)]+\))?!?):\s+(?P<title>.+?)\s*$")

# Recognize conventional body headings without changing their authored wording.
_SQUASH_HEADING_KINDS = {
    "feat",
    "fix",
    "perf",
    "refactor",
    "test",
    "style",
    "build",
    "chore",
    "ci",
    "doc",
    "docs",
    "added",
    "fixed",
    "changed",
    "performance",
    "documentation",
    "maintenance",
    "deprecated",
    "removed",
}

_TOP_LEVEL_LIST_MARKERS = ("- ", "* ", "• ")


def _backtick_span_end(text: str, start: int) -> int | None:
    """Return the end of a code span whose closing run matches its opener."""
    delimiter_length = 1
    while start + delimiter_length < len(text) and text[start + delimiter_length] == "`":
        delimiter_length += 1

    position = start + delimiter_length
    while position < len(text):
        run_start = text.find("`", position)
        if run_start < 0:
            return None
        run_end = run_start + 1
        while run_end < len(text) and text[run_end] == "`":
            run_end += 1
        if run_end - run_start == delimiter_length:
            return run_end
        position = run_end
    return None


def _delimiter_run_end(text: str, start: int, delimiter: str) -> int:
    """Return the exclusive end of one repeated Markdown delimiter run."""
    end = start + 1
    while end < len(text) and text[end] == delimiter:
        end += 1
    return end


def _is_left_flanking_asterisk_run(text: str, start: int, end: int) -> bool:
    """Return whether an asterisk run can open Markdown emphasis."""
    before = text[start - 1] if start > 0 else " "
    after = text[end] if end < len(text) else " "
    before_is_punctuation = not before.isalnum() and not before.isspace()
    after_is_punctuation = not after.isalnum() and not after.isspace()
    return not after.isspace() and (not after_is_punctuation or before.isspace() or before_is_punctuation)


def _is_right_flanking_asterisk_run(text: str, start: int, end: int) -> bool:
    """Return whether an asterisk run can close Markdown emphasis."""
    before = text[start - 1] if start > 0 else " "
    after = text[end] if end < len(text) else " "
    before_is_punctuation = not before.isalnum() and not before.isspace()
    after_is_punctuation = not after.isalnum() and not after.isspace()
    return not before.isspace() and (not before_is_punctuation or after.isspace() or after_is_punctuation)


def _asterisk_emphasis_span_end(text: str, start: int) -> int | None:
    """Return the end of an emphasis span with a matching asterisk run."""
    opener_end = _delimiter_run_end(text, start, "*")
    delimiter_length = opener_end - start
    if not _is_left_flanking_asterisk_run(text, start, opener_end):
        return None

    position = opener_end
    while position < len(text):
        if text[position] == "`":
            code_span_end = _backtick_span_end(text, position)
            if code_span_end is not None:
                position = code_span_end
                continue
        if text[position] != "*":
            position += 1
            continue

        closer_end = _delimiter_run_end(text, position, "*")
        if closer_end - position == delimiter_length and _is_right_flanking_asterisk_run(text, position, closer_end):
            return closer_end
        position = closer_end
    return None


def _balanced_delimiter_end(text: str, start: int, opening: str, closing: str) -> int | None:
    """Return the end of a balanced delimiter pair, honoring escapes."""
    depth = 1
    position = start + 1
    while position < len(text):
        character = text[position]
        if character == "\\" and position + 1 < len(text):
            position += 2
            continue
        if character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1
    return None


def _markdown_link_end(text: str, start: int) -> int | None:
    """Return the end of an inline link, balancing brackets and parentheses."""
    label_end = _balanced_delimiter_end(text, start, "[", "]")
    if label_end is None or label_end >= len(text) or text[label_end] != "(":
        return None
    return _balanced_delimiter_end(text, label_end, "(", ")")


def _markdown_tokens(text: str) -> list[str]:
    """Tokenize reflowable prose without splitting links or code spans."""
    tokens: list[str] = []
    position = 0
    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text):
            break

        token_end: int | None = None
        if text[position] == "[":
            token_end = _markdown_link_end(text, position)
        elif text[position] == "`":
            token_end = _backtick_span_end(text, position)

        if token_end is not None:
            while token_end < len(text) and not text[token_end].isspace():
                token_end += 1
        else:
            token_end = position + 1
            while token_end < len(text) and not text[token_end].isspace():
                token_end += 1
        tokens.append(text[position:token_end])
        position = token_end
    return tokens


def _plain_summary(text: str) -> str:
    """Return a normalized comparison key for changelog entry text."""
    text = _BREAKING_MARKER_RE.sub("", text)
    text = _COMMIT_LINK_RE.sub("", text)
    text = _PR_LINK_RE.sub("", text)
    text = re.sub(r"^\s*(?:[-*]|•)\s+", "", text)
    text = re.sub(r"^[A-Za-z]+(?:\([^)]+\))?!?:\s+", "", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _is_top_level_list_item(line: str) -> bool:
    """Return true for supported column-zero Markdown list markers."""
    return line.startswith(_TOP_LEVEL_LIST_MARKERS)


def _squash_heading_parts(line: str) -> tuple[str, str, str] | None:
    """Return ``(indent, label, title)`` for a squash-body pseudo-heading."""
    if _COMMIT_LINK_RE.search(line):
        return None

    match = _SQUASH_HEADING_RE.match(line)
    if match is None:
        return None

    raw_prefix = cast(str, match.group("prefix"))
    kind = re.sub(r"\([^)]+\)", "", raw_prefix).rstrip("!").casefold()
    if kind not in _SQUASH_HEADING_KINDS:
        return None

    title = cast("str", match.group("title")).strip()
    if not title:
        return None

    return cast("str", match.group("indent")), raw_prefix, title


def _normalize_squash_heading(line: str, *, nested: bool = False) -> str:
    """
    Convert squash-merge pseudo-commit bullets into level-4 headings.

    This keeps release-note subsections from PR squash bodies readable while
    avoiding fake top-level changelog entries.
    """
    parts = _squash_heading_parts(line)
    if parts is None:
        return line

    _indent, label, title = parts
    return f"#### {label}: {title}"


def _is_isolated_body_heading(lines: list[str], idx: int) -> bool:
    """Return true when a body line is separated like a squash heading."""
    prev_is_blank = idx > 0 and not lines[idx - 1].strip()
    next_is_blank = idx + 1 < len(lines) and not lines[idx + 1].strip()
    return prev_is_blank and next_is_blank


def _is_squash_heading_candidate(lines: list[str], idx: int) -> bool:
    """Return true when an original body line will become bold prose."""
    return _squash_heading_parts(_squash_heading_line(lines[idx])) is not None and _is_isolated_body_heading(lines, idx)


def _squash_heading_line(line: str) -> str:
    """Normalize list syntax enough to recognize squash-body headings."""
    line = _BULLET_SYMBOL_RE.sub(r"\1- ", line)
    line = _STAR_LIST_RE.sub(r"\1- ", line)
    return _LIST_MARKER_SPACE_RE.sub(r"\1 ", line)


def _max_pr_number(entry: str) -> int:
    """
    Get the largest pull request number referenced in the given changelog entry.

    Returns:
        highest_pr (int): The largest PR number found, or 0 if no PR links are present.
    """
    numbers = [int(m) for m in _PR_LINK_RE.findall(entry)]
    return max(numbers) if numbers else 0


def _compact_entry(line: str, *, strip_breaking: bool = False) -> str:
    """
    Produce a compact summary of a changelog list item.

    Removes a trailing commit-hash link from the given line. If `strip_breaking` is True,
    also removes a single leading breaking marker.

    Parameters:
        line (str): The changelog list item to compact.
        strip_breaking (bool): If True, strip a single leading breaking marker.

    Returns:
        str: The compacted changelog entry with the commit-hash link (and optional breaking prefix) removed.
    """
    result = _COMMIT_LINK_RE.sub("", line).rstrip()
    if strip_breaking:
        bullet = result[:2] if result.startswith(("- ", "* ")) else ""
        body = result[2:] if bullet else result
        result = bullet + _BREAKING_MARKER_RE.sub("", body, count=1)
    return result


def _append_unique(entries: list[str], entry: str) -> None:
    """Append *entry* to *entries* only once, preserving first-seen order."""
    if entry not in entries:
        entries.append(entry)


def _entry_commit_link(lines: list[str], index: int) -> tuple[int, re.Match[str]] | None:
    """Locate the terminal commit link of a possibly wrapped entry title."""
    while True:
        if match := _ENTRY_COMMIT_RE.search(lines[index]):
            return index, match
        index += 1
        if index == len(lines) or not lines[index].startswith("  "):
            return None
        continuation = lines[index].lstrip()
        if not continuation or continuation.startswith((*_TOP_LEVEL_LIST_MARKERS, "#", ">", "```", "~~~")):
            return None


def _entry_commit_links(lines: list[str], *, nested: bool = False) -> dict[int, tuple[int, re.Match[str]]]:
    """Map list items to actual terminal links, excluding literal Markdown examples."""
    candidates: dict[int, tuple[int, re.Match[str]]] = {}
    for index, line in enumerate(lines):
        indent = _list_item_indent(line)
        if indent is None or bool(indent) != nested:
            continue
        if link := _entry_commit_link(lines, index):
            candidates[index] = link
    if not candidates:
        return {}

    # Locate actual links, excluding identical text in code spans or examples.
    from markdown_it import MarkdownIt

    probe = lines.copy()
    fragment = "#rrt-citation-"
    while any(fragment in line for line in lines):
        fragment += "x"
    for number, match in candidates.values():
        end = match.end("url")
        probe[number] = lines[number][:end] + f"{fragment}{number}" + lines[number][end:]
    links = {token.attrGet("href") for block in MarkdownIt("commonmark").parse("\n".join(probe)) for token in block.children or () if token.type == "link_open"}
    return {index: (number, match) for index, (number, match) in candidates.items() if f"{match['url']}{fragment}{number}" in links}


def _extract_section_summaries(
    section: list[str],
) -> tuple[list[str], list[str]]:
    """
    Extract summary lines for merged pull requests and breaking changes from a version section.

    Processes only top-level list items in the provided `section` (lines starting with "- " or
    "* "), detects PR-linked entries and entries containing breaking markers. Each matching line
    is compacted (trailing commit-hash links removed; the breaking marker is stripped when
    requested) before inclusion.

    Parameters:
        section (list[str]): Lines belonging to a single version section from a changelog.

    Returns:
        tuple[list[str], list[str]]: `pr_entries` — compacted lines that contain PR links;
            `breaking_entries` — compacted lines marked as breaking changes.
    """
    pr_entries: list[str] = []
    breaking_entries: list[str] = []
    in_summary = False
    in_subsection = False
    entries = _entry_commit_links(section)

    for index, sline in enumerate(section):
        if sline.startswith("### "):
            in_summary = sline in {"### Merged Pull Requests", "### ⚠️ Breaking Changes"}
            in_subsection = False
        elif sline.startswith("#### ") or (sline.startswith("- **") and sline.endswith("**") and index not in entries):
            in_subsection = True
            continue
        # Only top-level list items (no leading whitespace).
        if in_summary or not sline.startswith(("- ", "* ")):
            continue
        if in_subsection:
            # After an entry heading its body bullets also start at column zero.
            # A real generated entry carries its commit link, possibly wrapped.
            if index not in entries:
                continue
            in_subsection = False

        is_breaking = bool(_BREAKING_MARKER_RE.search(sline))
        has_pr = bool(_PR_LINK_RE.search(sline))

        if is_breaking:
            _append_unique(breaking_entries, _compact_entry(sline, strip_breaking=True))
        if has_pr:
            _append_unique(pr_entries, _compact_entry(sline, strip_breaking=True))

    return pr_entries, breaking_entries


def _summary_insertion_index(section: list[str]) -> int:
    """Place new summaries after the version heading or an existing breaking summary."""
    if "### ⚠️ Breaking Changes" in section:
        index = section.index("### ⚠️ Breaking Changes") + 1
        while index < len(section) and not section[index].startswith("### "):
            index += 1
    else:
        index = 1
        while index < len(section) and not section[index].strip():
            index += 1
    return index


def _inject_summary_sections(text: str) -> str:
    """
    Insert "Merged Pull Requests" and "Breaking Changes" summary sections into a changelog text.

    Scans each version section for PR-linked list items and entries marked as breaking,
    builds compact summary lists (sorted by PR number), and injects a summary block
    immediately after the version heading when relevant.

    Returns:
        processed_text (str): The input text with summary sections inserted; unchanged if
        no version sections or no summary entries are found.
    """
    lines = text.split("\n")

    # Locate version-section boundaries.
    boundaries: list[int] = []
    for i, line in enumerate(lines):
        if _VERSION_RE.match(line):
            boundaries.append(i)

    if not boundaries:
        return text

    # Walk sections in reverse so insertions don't shift later indices.
    for sec_idx in reversed(range(len(boundaries))):
        start = boundaries[sec_idx]
        end = boundaries[sec_idx + 1] if sec_idx + 1 < len(boundaries) else len(lines)
        section = lines[start:end]

        # Preserve full template-rendered descriptions while filling either missing summary.
        has_pr_summary = "### Merged Pull Requests" in section
        has_breaking_summary = "### ⚠️ Breaking Changes" in section
        pr_entries, breaking_entries = _extract_section_summaries(section)
        if has_pr_summary:
            pr_entries = []
        if has_breaking_summary:
            breaking_entries = []

        if not pr_entries and not breaking_entries:
            continue

        # Sort PRs by highest PR number, descending (newest first).
        pr_entries.sort(key=_max_pr_number, reverse=True)

        insert_at = start + _summary_insertion_index(section)

        block: list[str] = []
        if breaking_entries:
            block.append("### ⚠️ Breaking Changes")
            block.append("")
            block.extend(breaking_entries)
            block.append("")
        if pr_entries:
            block.append("### Merged Pull Requests")
            block.append("")
            block.extend(pr_entries)
            block.append("")

        lines[insert_at:insert_at] = block

    return "\n".join(lines)


def _reflow_line(line: str, max_width: int = MAX_LINE_WIDTH) -> str:
    """
    Reflow a single markdown line to fit within max_width while preserving atomic markdown tokens.

    Preserves a leading list marker ("- " or "* ") on the first line and indents continuation
    lines to maintain list nesting. Tokens such as links and code spans are kept intact and not
    split across lines.

    Parameters:
        line (str): The original line to reflow.
        max_width (int): Maximum allowed line width; lines longer than this will be wrapped.

    Returns:
        str: The reflowed line, potentially containing newline characters so that no output line exceeds max_width.
    """
    if len(line) <= max_width:
        return line

    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]

    # Determine first-line prefix vs continuation indent.
    if stripped.startswith(("- ", "* ")):
        first_prefix = indent + stripped[:2]
        content = stripped[2:]
        cont_indent = indent + "  "
    else:
        first_prefix = indent
        content = stripped
        cont_indent = indent

    tokens = _markdown_tokens(content)
    if not tokens:
        return line

    lines: list[str] = []
    current = first_prefix + tokens[0]

    for token in tokens[1:]:
        candidate = current + " " + token
        if len(candidate) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = cont_indent + token

    lines.append(current)
    return "\n".join(lines)


def _deindent_orphan(line: str, lines: list[str], idx: int) -> str:
    """
    Normalize indentation for sub-bullet list items produced by git-cliff.

    Cliff's ``indent(prefix="  ")`` filter can compound with pre-existing
    indentation in commit bodies, producing non-standard nesting depths.
    This function scans backward through the original *lines* to find the
    nearest list ancestor and normalizes the indent to ``parent + 2``
    spaces (MD007).
    """
    stripped = line.lstrip()
    if not (line.startswith("  ") and stripped.startswith("- ")):
        return line

    our_indent = len(line) - len(stripped)
    nearest_parent_indent: int | None = None

    for j in range(idx - 1, -1, -1):
        prev = lines[j]
        if not prev.strip():
            continue  # skip blanks
        if prev.startswith(" "):
            prev_stripped = prev.lstrip()
            if prev_stripped.startswith(("- ", "* ")):
                if _is_squash_heading_candidate(lines, j):
                    continue
                parent_indent = len(prev) - len(prev_stripped)
                if our_indent > parent_indent and nearest_parent_indent is None:
                    nearest_parent_indent = parent_indent
            continue  # skip cliff-indented content
        # Column-0 non-blank line — determines final result.
        is_list_parent = prev.startswith(("- ", "* "))
        if is_list_parent:
            base = nearest_parent_indent + 2 if nearest_parent_indent is not None else 2
            return " " * base + stripped
        if prev.startswith("#### "):
            # Entry-local headings retain the body indentation on later runs.
            return " " * (nearest_parent_indent + 2 if nearest_parent_indent is not None else 2) + stripped
        # Column-0 non-list — orphan.
        return line[2:] if nearest_parent_indent is not None else stripped
    # Reached top of document — orphan.
    return line[2:] if nearest_parent_indent is not None else stripped


def _normalize_list_continuation_indent(line: str, lines: list[str], idx: int) -> str:
    """
    Normalize generated list continuation indentation.

    git-cliff commit bodies can contain pre-indented prose under a list item.
    Markdown treats that prose as list-continuation content, where rumdl's MD077
    expects exactly two spaces past the parent bullet indentation.
    """
    stripped = line.lstrip()
    if not line.startswith(" ") or not stripped or stripped.startswith(("- ", "* ")):
        return line

    our_indent = len(line) - len(stripped)

    for j in range(idx - 1, -1, -1):
        prev = lines[j]
        if not prev.strip():
            continue

        prev_stripped = prev.lstrip()
        if prev_stripped.startswith(("- ", "* ")):
            parent_indent = len(prev) - len(prev_stripped)
            expected_indent = parent_indent + 2
            if our_indent > expected_indent:
                return " " * expected_indent + stripped
            return line

        if not prev.startswith(" "):
            return line

    return line


def _list_item_indent(line: str) -> int | None:
    """Return the indentation of a Markdown list item, if *line* is one."""
    stripped = line.lstrip()
    if not stripped.startswith(("- ", "* ", "• ")):
        return None
    return len(line) - len(stripped)


def _has_previous_peer_list_item(lines: list[str], idx: int, peer_indent: int) -> bool:
    """Return true if a prior list item exists at *peer_indent* before *idx*."""
    for j in range(idx - 1, -1, -1):
        prev = lines[j]
        if not prev.strip():
            continue

        prev_indent = _list_item_indent(prev)
        if prev_indent == peer_indent:
            return True
        if prev_indent is not None:
            if prev_indent < peer_indent:
                return False
            continue
        if prev.startswith(" "):
            continue
        return False

    return False


def _needs_blank_before(line: str, result: list[str]) -> bool:
    """
    Determine whether a blank line is required before a list item to satisfy Markdown rule MD032.

    Parameters:
        line (str): The current line.
        result (list[str]): The lines already emitted immediately before the current line.

    Returns:
        bool: `True` if a blank line should be inserted before the list item, `False` otherwise.
    """
    stripped = line.lstrip()
    if not stripped.startswith("- ") or not result or not result[-1].strip():
        return False
    prev = result[-1].lstrip()
    if prev.startswith(("-", "#")):
        return False

    current_indent = len(line) - len(stripped)
    return not _has_previous_peer_list_item(result, len(result), current_indent)


def _normalize_indented_heading(line: str) -> str:
    """
    Convert indented commit-body headings into level-4 headings.

    git-cliff indents commit bodies under each changelog entry. If a historical
    commit body contains an ATX heading such as ``## Correctness Fixes``, the
    rendered changelog contains ``  ## Correctness Fixes``. Rumdl treats
    emphasis-only headings as MD036 violations, so internal commit-body headings
    become real level-4 headings below the release category heading.
    """
    match = _INDENTED_ATX_HEADING_RE.match(line)
    if match is None:
        return line

    title = match.group("title").strip()
    if not title:
        return line

    return f"#### {title}"


def _normalize_indented_bold_heading(
    line: str,
    current_entry_summary: str | None,
    is_isolated_body_heading: bool,
) -> str:
    """Convert isolated indented bold commit-body headings into level-4 headings."""
    if current_entry_summary is None or not is_isolated_body_heading:
        return line

    match = _INDENTED_BOLD_HEADING_RE.match(line)
    if match is None:
        return line

    title = match.group("title").strip()
    if not title:
        return line

    return f"#### {title}"


def _is_changelog_boundary_heading(line: str) -> bool:
    """Return true for root, version, or category headings that end an entry."""
    if line in {"# Changelog", "## Archives"} or _VERSION_RE.match(line):
        return True

    match = _ATX_HEADING_RE.match(line)
    if match is None:
        return False

    title = match.group("title").strip()
    return cast("str", match.group("level")) == "###" and title in _CHANGELOG_SECTION_HEADINGS


def _normalize_entry_heading(line: str, current_entry_summary: str | None = None) -> str:
    """Demote column-zero headings that belong to the active changelog entry."""
    if _is_changelog_boundary_heading(line):
        return line

    match = _ENTRY_ATX_HEADING_RE.match(line)
    if match is None:
        return line

    title = match.group("title").strip()
    if not title:
        return line

    return f"#### {title}"


def _code_span_wildcard_identifiers(line: str) -> str:
    """Wrap bare wildcard identifiers outside code and emphasis spans."""

    def code_span(match: re.Match[str]) -> str:
        return f"`{match.group(1)}`"

    result: list[str] = []
    plain_start = 0
    position = 0
    while position < len(line):
        span_end: int | None = None
        if line[position] == "`":
            span_end = _backtick_span_end(line, position)
        elif line[position] == "*":
            span_end = _asterisk_emphasis_span_end(line, position)

        if span_end is None:
            position += 1
            continue

        result.append(_WILDCARD_IDENTIFIER_RE.sub(code_span, line[plain_start:position]))
        result.append(line[position:span_end])
        position = span_end
        plain_start = position

    result.append(_WILDCARD_IDENTIFIER_RE.sub(code_span, line[plain_start:]))
    return "".join(result)


def normalize_entry_headings_text(text: str) -> str:
    """Demote entry-local headings without applying broader changelog cleanup."""
    lines = text.split("\n")
    current_entry_summary: str | None = None
    for index, line in _changelog_prose(lines):
        current_entry_summary = _update_entry_summary(line, current_entry_summary)
        line = _normalize_entry_heading(line, current_entry_summary)
        line = _code_span_wildcard_identifiers(line)
        lines[index] = line

    return "\n".join(lines)


def _normalize_horizontal_rule(line: str, result: list[str]) -> str:
    """Normalize indented horizontal rules and ensure they have surrounding blanks."""
    if line.strip() != "---":
        return line

    if result and result[-1].strip():
        result.append("")
    return "---"


def _fence_parts(line: str) -> tuple[str, str, str] | None:
    """Return indentation, delimiter run, and info string for a fence line."""
    match = _FENCE_RE.fullmatch(line)
    if match is None:
        return None
    return cast("str", match.group("indent")), cast("str", match.group("fence")), cast("str", match.group("info"))


def _opening_code_fence(line: str) -> _CodeFence | None:
    """Parse an opening backtick or tilde fence."""
    parts = _fence_parts(line)
    if parts is None:
        return None
    _, delimiter_run, info = parts
    delimiter = delimiter_run[0]
    if delimiter == "`" and "`" in info:
        return None
    return _CodeFence(delimiter=delimiter, length=len(delimiter_run))


def _closes_code_fence(line: str, active_fence: _CodeFence) -> bool:
    """Return whether *line* validly closes *active_fence*."""
    parts = _fence_parts(line)
    if parts is None:
        return False
    _, delimiter_run, info = parts
    return delimiter_run[0] == active_fence.delimiter and len(delimiter_run) >= active_fence.length and not info.strip()


def _process_code_fence(
    line: str,
    result: list[str],
    active_fence: _CodeFence | None,
    next_line: str | None,
) -> tuple[bool, _CodeFence | None]:
    """Handle fenced-code transitions and append the line when consumed."""
    if active_fence is None:
        active_fence = _opening_code_fence(line)
        if active_fence is None:
            return False, None
        # MD031: blank line before fenced code block.
        if result and result[-1].strip():
            result.append("")
        # MD040: add language tag if missing.
        parts = _fence_parts(line)
        if parts is not None:
            indent, delimiter_run, info = parts
            if not info.strip():
                line = f"{indent}{delimiter_run}text"
        result.append(line)
        return True, active_fence

    if not _closes_code_fence(line, active_fence):
        return False, active_fence

    result.append(line)
    if next_line is not None and next_line.strip():
        result.append("")
    return True, None


def _update_entry_summary(line: str, current_entry_summary: str | None) -> str | None:
    """Track the active changelog entry summary for squash-body cleanup."""
    if _squash_heading_parts(line) is not None:
        return current_entry_summary
    if _is_top_level_list_item(line):
        return _plain_summary(line)
    if _is_changelog_boundary_heading(line):
        return None
    return current_entry_summary


def _normalize_body_line(
    line: str,
    result: list[str],
    current_entry_summary: str | None,
    is_isolated_body_heading: bool,
) -> str:
    """Apply markdown hygiene transforms to a non-code line."""
    line = _normalize_indented_heading(line)
    line = _normalize_indented_bold_heading(line, current_entry_summary, is_isolated_body_heading)
    line = _normalize_entry_heading(line, current_entry_summary)
    line = _normalize_horizontal_rule(line, result)

    if is_isolated_body_heading:
        line = _normalize_squash_heading(line, nested=current_entry_summary is not None)

    line = _code_span_wildcard_identifiers(line)

    if _needs_blank_before(line, result):
        result.append("")

    return _reflow_line(line) if len(line) > MAX_LINE_WIDTH else line


def _dependabot_metadata_end(lines: list[str], separator_index: int) -> int | None:
    """Return the closing marker index for an unfenced Dependabot footer."""
    metadata_start = separator_index + 1
    while metadata_start < len(lines) and not lines[metadata_start].strip():
        metadata_start += 1
    if metadata_start >= len(lines) or lines[metadata_start].strip() != "updated-dependencies:":
        return None

    metadata_end = metadata_start + 1
    while metadata_end < len(lines) and lines[metadata_end].strip() != "..." and _opening_code_fence(lines[metadata_end]) is None:
        metadata_end += 1
    if metadata_end >= len(lines) or lines[metadata_end].strip() != "...":
        return None
    return metadata_end


def _strip_dependabot_metadata(text: str) -> str:
    """Remove Dependabot's YAML metadata footer from rendered commit bodies."""
    lines = text.split("\n")
    result: list[str] = []
    active_fence: _CodeFence | None = None
    idx = 0

    while idx < len(lines):
        if active_fence is not None:
            result.append(lines[idx])
            if _closes_code_fence(lines[idx], active_fence):
                active_fence = None
            idx += 1
            continue

        active_fence = _opening_code_fence(lines[idx])
        if active_fence is not None:
            result.append(lines[idx])
            idx += 1
            continue

        metadata_end = _dependabot_metadata_end(lines, idx) if lines[idx].strip() == "---" else None
        if metadata_end is None:
            result.append(lines[idx])
            idx += 1
            continue

        while result and not result[-1].strip():
            result.pop()
        idx = metadata_end + 1
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
        if result and idx < len(lines):
            result.append("")

    return "\n".join(result)


def _indent_columns(line: str) -> int:
    """Count leading Markdown indentation in columns, with four-column tab stops."""
    return len(line[: len(line) - len(line.lstrip(" \t"))].expandtabs(4))


@dataclass(frozen=True)
class _ProtectedCode:
    source: str
    indent: int
    literal: str | None = None

    def restore(self, indent: int) -> str:
        if indent == self.indent:
            return self.source
        if self.literal is not None:
            # Rebuild only the container and the four-column code prefix.
            # Tabs inside the parsed literal remain verbatim.
            prefix = " " * (indent + 4)
            return "\n".join(prefix + row if row else "" for row in self.literal.removesuffix("\n").split("\n"))
        return "\n".join(_move_code_line(row, indent - self.indent) for row in self.source.split("\n"))


def _indented_fence_examples(text: str) -> dict[int, tuple[int, _ProtectedCode]]:
    """Locate literal fence examples and the indentation of their container."""
    if not re.search(r"(?m)^(?: {4}|[ \t]*\t)[ \t]*(?:`{3}|~{3})", text):
        return {}
    from markdown_it import MarkdownIt

    lines = text.split("\n")
    examples = {}
    for token in MarkdownIt("commonmark").parse(text):
        if token.type == "code_block" and token.map is not None and re.search(r"(?m)^[ \t]*(?:`{3}|~{3})", token.content):
            start, end = token.map
            # An indented code block consumes four columns after its container.
            literal = token.content.partition("\n")[0]
            suffix = literal.lstrip(" ")
            # A tab spanning the boundary appears as spaces in the parsed text.
            # Tabs in the shared suffix keep their original meaning.
            padding = len(literal) - len(suffix)
            indent = max(0, _indent_columns(lines[start].removesuffix(suffix)) - padding - 4)
            examples[start] = (end, _ProtectedCode("\n".join(lines[start:end]), indent, token.content))
    return examples


def _changelog_prose(lines: list[str], *, require_closed: bool = False) -> Iterator[tuple[int, str]]:
    """Yield prose locations outside fences and literal fence examples."""
    fence = None
    examples = {i for start, (end, _) in _indented_fence_examples("\n".join(lines)).items() for i in range(start, end)}
    for index, line in enumerate(lines):
        if index in examples:
            continue
        if fence is not None:
            if _closes_code_fence(line, fence):
                fence = None
            continue
        fence = _opening_code_fence(line)
        if fence is None:
            yield index, line
    if require_closed and fence is not None:
        raise ValueError("unclosed changelog fence makes release boundaries ambiguous")


def _protect_fenced_blocks(text: str) -> tuple[str, dict[str, _ProtectedCode]]:
    """Keep code opaque to document-wide prose transforms, including examples of releases.

    Tokens are collision-free within this input. Restoring them after all prose
    transforms also protects code from summary injection and deduplication.
    """
    prefix = "RRTFENCEDBLOCK"
    while prefix in text:
        prefix += "X"
    lines = text.split("\n")
    result: list[str] = []
    blocks: dict[str, _ProtectedCode] = {}
    examples = _indented_fence_examples(text)
    index = 0
    while index < len(lines):
        if index in examples:
            end, example = examples[index]
            marker = f"{prefix}{len(blocks)}END"
            blocks[marker] = example
            result.append(" " * example.indent + marker)
            index = end
            continue
        opener = _opening_code_fence(lines[index])
        if opener is None:
            result.append(lines[index])
            index += 1
            continue
        start = index
        index += 1
        while index < len(lines):
            closing = _closes_code_fence(lines[index], opener)
            index += 1
            if closing:
                break
        block = lines[start:index]
        parts = _fence_parts(block[0])
        assert parts is not None
        indent, delimiter, info = parts
        if not info.strip():
            block[0] = f"{indent}{delimiter}text"
        marker = f"{prefix}{len(blocks)}END"
        blocks[marker] = _ProtectedCode("\n".join(block), _indent_columns(indent))
        if result and result[-1].strip():
            result.append("")
        result.append(indent + marker)
        if index < len(lines) and lines[index].strip():
            result.append("")
    return "\n".join(result), blocks


def _move_code_line(line: str, shift: int) -> str:
    """Move only structural indentation, preserving tabs within the literal."""
    if not line or shift == 0:
        return line
    if shift > 0:
        return " " * shift + line
    columns = 0
    for index, char in enumerate(line):
        if char not in " \t":
            return line[index:]
        columns += 4 - columns % 4 if char == "\t" else 1
        if columns >= -shift:
            return " " * (columns + shift) + line[index + 1 :]
    return ""


def _table_cells(line: str) -> list[str]:
    """Split pipe-table cells, respecting escaped pipes and optional borders."""
    cells: list[str] = []
    start = position = 0
    while position < len(line):
        if line[position] == "\\":
            position += 2
            continue
        if line[position] == "|":
            cells.append(line[start:position].strip())
            start = position + 1
        position += 1
    cells.append(line[start:].strip())
    if len(cells) == 1:
        return []
    if cells[0] == "":
        cells.pop(0)
    if cells and cells[-1] == "":
        cells.pop()
    return cells


def _table_line_indices(lines: list[str]) -> set[int]:
    """Recognize pipe tables before applying prose-only transformations."""
    preserved: set[int] = set()
    index = 0
    while index + 1 < len(lines):
        header = _table_cells(lines[index])
        separator = _table_cells(lines[index + 1])
        if not header or len(header) != len(separator) or not all(re.fullmatch(r":?-+:?", cell) for cell in separator):
            index += 1
            continue
        preserved.update((index, index + 1))
        index += 2
        # Rows may omit cells and even all pipes. A blank or a new Markdown
        # block ends the table; prose reflow must never split an existing row.
        while index < len(lines) and lines[index].strip():
            row = lines[index].strip()
            if (
                _ATX_HEADING_RE.match(row)
                or row.startswith(">")
                or re.match(r"(?:[-+*]|[0-9]{1,9}[.)])(?:\s|$)", row)
                or re.fullmatch(r"(?:-\s*){3,}|(?:_\s*){3,}|(?:\*\s*){3,}", row)
            ):
                break
            preserved.add(index)
            index += 1
    return preserved


def _normalize_inline_prose(text: str) -> str:
    """Normalize prose angles, email autolinks and word spacing in list items.

    Fenced blocks have already been protected. Scanning the complete text also
    keeps multiline code spans opaque. Tables retain their email entities.
    """
    result: list[str] = []
    position = 0
    line_index = 0
    lines = text.split("\n")
    table_lines = _table_line_indices(lines)
    list_lines = {index for index, line in enumerate(lines) if _list_item_indent(line) is not None} - table_lines
    word_gap = re.compile(r"(?<=\w) {2,}(?=\w)")
    while position < len(text):
        end: int | None = None
        if position == 0 or text[position - 1] == "\n":
            if match := _REFERENCE_DEFINITION_RE.match(text, position) or _BLOCKQUOTE_PREFIX_RE.match(text, position):
                end = match.end()
        if end is None:
            if text[position] == "\\":
                end = min(position + 2, len(text))
            elif text[position] == "`":
                end = _backtick_span_end(text, position)
                # Code spans cannot cross paragraph boundaries. Keep an
                # unmatched delimiter run together instead of reopening it
                # at its second backtick and consuming a later code span.
                if end is None or re.search(r"\n[ \t]*\n", text[position:end]):
                    end = _delimiter_run_end(text, position, "`")
            elif text.startswith("](", position):
                end = _balanced_delimiter_end(text, position + 1, "(", ")")
            elif text[position] == "<" and (match := _AUTOLINK_RE.match(text, position)):
                end = match.end()
        if end is not None:
            result.append(text[position:end])
            line_index += text.count("\n", position, end)
            position = end
        elif text[position] == "&" and line_index not in table_lines and (email := _ESCAPED_EMAIL_RE.match(text, position)):
            result.append(f"<{email.group('email')}>")
            position = email.end()
        elif text[position] == " " and line_index in list_lines and (gap := word_gap.match(text, position)):
            result.append(" ")
            position = gap.end()
        else:
            result.append({"<": "&lt;", ">": "&gt;"}.get(text[position], text[position]))
            line_index += text[position] == "\n"
            position += 1
    return "".join(result)


def _normalize_entry_lists(lines: list[str]) -> None:
    """A level-four heading ends the commit's list item and starts its own list."""
    in_subsection = False
    base = 0
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if not indent:
            in_subsection = line.startswith("#### ")
            base = 0
        elif in_subsection:
            if _list_item_indent(line) is not None:
                base = min(base or indent, indent)
            elif indent <= base:
                base = 0
            if base:
                lines[index] = line[base:]


@dataclass
class _BulletList:
    indent: int
    items: list[int]
    loose: bool = False


def _nest_subsection_body(body: list[str]) -> list[str]:
    """Place details and deeper subsections inside the converted heading's list item."""
    prefix = "  " if any(line.strip() and not line.startswith("  ") for line in body) else ""
    levels: list[int] = []
    nested = []
    for line in body:
        if heading := _ATX_HEADING_RE.match(line):
            level = len(heading["level"])
            while levels and levels[-1] >= level:
                levels.pop()
            levels.append(level)
            nested.append("  " * len(levels) + f"- **{heading['title'].strip()}**")
        else:
            nested.append(prefix + "  " * len(levels) + line if line.strip() else "")
    return nested


def _number_navigable_headings(lines: list[str], repeated: set[int]) -> None:
    """Keep real heading targets in documents with local fragment navigation.

    Use the original document order for suffixes, including headings outside
    the repeated category and collisions with pre-existing numbered titles.
    """
    from markdown_it import MarkdownIt

    tokens = MarkdownIt("commonmark").parse("\n".join(lines))
    used: set[str] = set()
    siblings: set[str] = set()
    for position, heading in enumerate(tokens):
        if heading.type != "heading_open" or heading.map is None:
            continue
        inline = tokens[position + 1]
        label = "".join(token.content for token in inline.children or () if token.type in {"text", "code_inline"})
        base = re.sub(r"[^\w -]", "", label.lower()).replace(" ", "-")
        anchor = base
        suffix = 0
        while anchor in used:
            suffix += 1
            anchor = f"{base}-{suffix}"
        used.add(anchor)
        level = int(heading.tag[1:])
        if level < 4:
            siblings.clear()
        elif level == 4:
            index = heading.map[0]
            if (source := _ATX_HEADING_RE.match(lines[index])) is None:
                continue
            title = source["title"].strip()
            if suffix and (index in repeated or title in siblings):
                title = f"{title} ({suffix})"
                lines[index] = f"#### {title}"
            siblings.add(title)


def _has_local_fragment_link(lines: list[str]) -> bool:
    """Keep heading targets reachable by fragment-only and path-qualified links."""
    from markdown_it import MarkdownIt

    # A full history is normalized before it is split into its output files.
    # A relative destination can therefore address the root or a minor archive.
    for block in MarkdownIt("commonmark").parse("\n".join(lines)):
        for token in block.children or ():
            if token.type == "link_open":
                href = str(token.attrGet("href") or "")
                if href.partition("#")[2] and not re.match(r"(?:[a-z][a-z0-9+.-]*:|//)", href, re.IGNORECASE):
                    return True
    return False


def _list_repeated_headings(lines: list[str]) -> None:
    """Keep duplicate sibling titles and their bodies together as list items."""
    occurrences: dict[tuple[int, str], list[int]] = {}
    parent = 0
    for index, line in enumerate(lines):
        heading = _ATX_HEADING_RE.match(line)
        if heading is None:
            continue
        level = len(heading["level"])
        if level < 4:
            parent = index
        elif level == 4:
            occurrences.setdefault((parent, heading["title"].strip()), []).append(index)

    repeated = sorted((index, title) for (_, title), indices in occurrences.items() if len(indices) > 1 for index in indices)
    if not repeated:
        return
    if _has_local_fragment_link(lines):
        _number_navigable_headings(lines, {index for index, _ in repeated})
        return
    # Footer definitions belong to the whole document. A definition followed
    # by more subsection prose stays in that subsection alongside its uses.
    body_limit = len(lines)
    while body_limit and (not lines[body_limit - 1].strip() or _REFERENCE_DEFINITION_RE.fullmatch(lines[body_limit - 1])):
        body_limit -= 1
    gaps: set[int] = set()
    entries = _entry_commit_links(lines)
    for index, title in repeated:
        end = index + 1
        while end < body_limit:
            heading = _ATX_HEADING_RE.match(lines[end])
            if heading and len(heading["level"]) <= 4:
                break
            if end in entries:
                break
            end += 1
        body = lines[index + 1 : end]
        lines[index + 1 : end] = _nest_subsection_body(body)
        lines[index] = f"- **{title}**"
        if index and lines[index - 1].strip():
            gaps.add(index)
        if body and body[0].strip():
            gaps.add(index + 1)
    for index in sorted(gaps, reverse=True):
        lines.insert(index, "")


def _block_spacing(lines: list[str]) -> str:
    """Separate headings and give every peer in a multi-paragraph list a blank line."""
    separate: set[int] = set()
    lists: list[_BulletList] = []
    active: list[_BulletList] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if _ATX_HEADING_RE.match(line):
            separate.update((index, index + 1))
        indent = len(line) - len(line.lstrip())
        bullet = _list_item_indent(line) is not None
        while active and (indent < active[-1].indent or (indent == active[-1].indent and not bullet)):
            active.pop()
        if active and indent > active[-1].indent and not lines[index - 1].strip():
            active[-1].loose = True
        if bullet:
            if not active or indent > active[-1].indent:
                active.append(_BulletList(indent, []))
                lists.append(active[-1])
            active[-1].items.append(index)

    omit: set[int] = set()
    for group in lists:
        if group.loose:
            separate.update(group.items[1:])
        else:
            for item in group.items[1:]:
                previous = item - 1
                while previous >= 0 and not lines[previous].strip():
                    omit.add(previous)
                    previous -= 1
    result = []
    for index, line in enumerate(lines):
        if index in omit:
            continue
        if index in separate and result and result[-1].strip() and line.strip():
            result.append("")
        result.append(line)
    return "\n".join(result)


def _separate_entry_details(lines: list[str]) -> list[str]:
    """End a commit's detail subsection before the next commit starts."""
    result: list[str] = []
    in_details = False
    entries = _entry_commit_links(lines)
    for index, line in enumerate(lines):
        heading = _ATX_HEADING_RE.match(line)
        if (heading and len(heading["level"]) <= 4) or line == "---":
            in_details = line.startswith("#### ")
        elif index in entries:
            if in_details:
                if result and result[-1].strip():
                    result.append("")
                result.extend(("---", ""))
            in_details = False
        elif line.startswith("- **") and line.endswith("**"):
            in_details = True
        result.append(line)
    return result


def postprocess_text(text: str) -> str:
    """Apply one common Markdown policy, preserving fenced code and reference links."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text, fenced_blocks = _protect_fenced_blocks(text)
    text = _strip_dependabot_metadata(text)
    text = _normalize_inline_prose(text)
    lines = text.split("\n")
    for number, match in _entry_commit_links(lines, nested=True).values():
        # These body bullets may move to column zero. Angle-delimited Markdown
        # destinations retain the same visible links without becoming entries.
        start, end = match.span("url")
        row = lines[number]
        lines[number] = row[:start] + "<" + row[start:end] + ">" + row[end:]

    # Inject PR / breaking-change summary sections before reflow.
    text = _inject_summary_sections("\n".join(lines))

    lines = text.split("\n")
    table_lines = _table_line_indices(lines)
    result: list[str] = []
    active_fence: _CodeFence | None = None
    current_entry_summary: str | None = None

    for idx, line in enumerate(lines):
        if idx in table_lines:
            result.append(line)
            continue
        # --- fenced code-block tracking ---
        next_line = lines[idx + 1] if idx + 1 < len(lines) else None
        handled, active_fence = _process_code_fence(line, result, active_fence, next_line)
        if handled:
            continue

        # Never reflow inside code blocks.
        if active_fence is not None:
            result.append(line)
            continue

        # --- MD004: normalise historical and ``* `` list markers to ``- `` ---
        line = _BULLET_SYMBOL_RE.sub(r"\1- ", line)
        line = _STAR_LIST_RE.sub(r"\1- ", line)

        # --- MD030: normalise spaces after list marker ---
        line = _LIST_MARKER_SPACE_RE.sub(r"\1 ", line)

        current_entry_summary = _update_entry_summary(line, current_entry_summary)
        is_isolated_body_heading = _is_isolated_body_heading(lines, idx)

        line = _deindent_orphan(line, lines, idx)
        line = _normalize_list_continuation_indent(line, lines, idx)
        normalized = _normalize_body_line(line, result, current_entry_summary, is_isolated_body_heading)
        result.append(normalized)
        if normalized == "---" and next_line is not None and next_line.strip():
            result.append("")

    # 1. Reassemble and strip trailing blank lines.
    lines = "\n".join(result).split("\n")
    _normalize_entry_lists(lines)
    _list_repeated_headings(lines)
    text = _block_spacing(_separate_entry_details(lines))
    text = text.rstrip() + "\n"
    if any(text.count(marker) != 1 for marker in fenced_blocks):
        raise ValueError("Changelog normalization would lose a fenced code block")
    lines = text.rstrip("\n").split("\n")
    for index, line in enumerate(lines):
        marker = line.lstrip()
        if marker in fenced_blocks:
            lines[index] = fenced_blocks.pop(marker).restore(_indent_columns(line))
    if fenced_blocks:
        raise ValueError("Changelog normalization would lose a fenced code block")
    return "\n".join(lines) + "\n"


class MarkdownFormatError(RuntimeError):
    """An explicitly requested Markdown formatter failed to produce valid output."""


def _check_markdown_candidates(text: str, path: Path, config: Path, candidates: Mapping[Path, bytes]) -> None:
    """Check the proposed workspace without materializing publication outputs."""
    # Match rumdl's Markdown link-target extensions. Supplying their actual
    # contents preserves fragment checks even when an existing file is replaced.
    markdown_extensions = {".md", ".markdown", ".mdx", ".mkd", ".mkdn", ".mdown", ".mdwn", ".qmd", ".rmd"}
    documents = {path: text}
    from research_repo_tools.markdown import existing_archive_link_targets

    targets: set[str] = set()
    for candidate, data in candidates.items():
        if candidate == path:
            continue
        if candidate.suffix.lower() in markdown_extensions:
            documents[candidate] = data.decode("utf-8")
        else:
            targets.add(str(candidate))
    for document, content in documents.items():
        if not document.parent.is_dir():
            targets.update(existing_archive_link_targets(content, document))
    records = [part for name, content in documents.items() for part in (str(name), content)]
    if any("\0" in part for part in (*records, *targets)):
        raise MarkdownFormatError("Markdown candidates and paths cannot contain NUL characters")
    # The inventory lives outside the publication root and is closed before
    # rumdl opens it, including on Windows. Binary writes retain exact framing.
    with tempfile.TemporaryDirectory(prefix="rrt-markdown-") as temporary:
        inventory = Path(temporary) / "targets"
        inventory.write_bytes("".join(target + "\0" for target in sorted(targets)).encode("utf-8"))
        run_safe_command(
            "rumdl",
            ["check", "--stdin-batch", "--stdin-batch-targets", str(inventory), "--no-cache", "--config", str(config)],
            input="\0".join(records) + "\0",
            timeout=30,
        )


def fix_markdown(text: str, path: Path, config: Path, *, batch: bool = True) -> str:
    """Format a candidate, deferring link checks when validating a batch."""
    if not config.is_file():
        raise MarkdownFormatError(f"Markdown formatter configuration not found: {config}")
    try:
        # Single-document fixing cannot see planned link targets. Defer link
        # checks to the full-policy batch check below; never disable them there.
        deferred_checks = ["--extend-disable", "MD051,MD057"] if batch else []
        result = run_safe_command(
            "rumdl",
            ["check", "--fix", "--stdin", "--stdin-filename", str(path), "--no-cache", "--config", str(config), *deferred_checks],
            input=text,
            timeout=30,
        )
    except subprocess.CalledProcessError as error:
        diagnostics = (error.stderr or error.stdout or "unknown formatter error").strip()
        raise MarkdownFormatError(f"rumdl could not format {path.name}: {diagnostics}") from error
    if text.strip() and not result.stdout.strip():
        raise MarkdownFormatError(f"rumdl returned empty output for nonempty {path.name}")
    return result.stdout.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"


def check_markdown(text: str, path: Path, config: Path, candidates: Mapping[Path, bytes] | None = None) -> None:
    """Check the exact formatted content against the full policy before publication."""
    # Some fixers return success after making only fixable corrections. Check the
    # completed candidate without --fix before replacing the existing artifact.
    try:
        if candidates:
            _check_markdown_candidates(text, path, config, candidates)
        else:
            run_safe_command(
                "rumdl",
                ["check", "--stdin", "--stdin-filename", str(path), "--no-cache", "--config", str(config)],
                input=text,
                timeout=30,
            )
    except subprocess.CalledProcessError as error:
        raise MarkdownFormatError(f"rumdl rejected formatted {path.name}: {error.stderr or error.stdout}") from error


def format_markdown(text: str, path: Path, config: Path, *, candidates: Mapping[Path, bytes] | None = None) -> str:
    """Format and check a document with its consumer's explicit policy."""
    output = fix_markdown(text, path, config, batch=bool(candidates))
    check_markdown(output, path, config, candidates)
    return output


def postprocess(path: Path, *, formatter: Path | None = None) -> None:
    """Read *path*, apply hygiene fixes, and write it back."""
    from research_repo_tools.archive_changelog import parse_changelog, require_preserved_releases

    if path.is_symlink():
        raise ValueError(f"Changelog output must not be a symlink: {path}")
    text = path.read_text(encoding="utf-8")
    original = parse_changelog(text)
    text = postprocess_text(text)
    if formatter is not None:
        text = format_markdown(text, path, formatter)
    require_preserved_releases(original, parse_changelog(text))

    replace_many({path: text.encode("utf-8")})


def main(argv: list[str] | None = None) -> int:
    """Run ``postprocess-changelog`` with concise file diagnostics."""
    parser = argparse.ArgumentParser(
        prog="postprocess-changelog",
        description="Apply markdown hygiene to a git-cliff generated CHANGELOG.md.",
        suggest_on_error=True,
        color=False,
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="CHANGELOG.md",
        help="Path to CHANGELOG.md (default: CHANGELOG.md)",
    )
    parser.add_argument("--formatter", type=Path, help="explicit rumdl configuration")
    args = parser.parse_args(argv)

    changelog = Path(args.path)
    if not changelog.is_file():
        print(f"postprocess-changelog: error: not a regular file: {changelog}", file=sys.stderr)
        return 1

    try:
        postprocess(changelog, formatter=args.formatter)
    except (UnicodeDecodeError, OSError, ValueError, RuntimeError, ExecutableNotFoundError, subprocess.SubprocessError) as error:
        detail = " ".join(str(error).split()) or type(error).__name__
        print(f"postprocess-changelog: error: {changelog}: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
