#!/usr/bin/env python3
"""
Utility for summarizing cargo-llvm-cov Cobertura coverage results.

The script expects a Cobertura XML report produced by `just coverage-ci`
(Default location: `coverage/cobertura.xml`). It prints all files that have
coverable lines, sorted by ascending coverage percentage. Entries can be filtered
by a path prefix so you can focus on application code (e.g. `src/`).

Example usage (run from repo root):

    uv run python scripts/coverage_report.py
    uv run python scripts/coverage_report.py --prefix src/cdt --limit 5
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

if TYPE_CHECKING:
    from collections.abc import Iterable

DEFAULT_REPORT = Path("coverage/cobertura.xml")


@dataclass(frozen=True)
class CoverageEntry:
    coverage: float
    coverable: int
    covered: int
    path: Path

    def format(self, relative_to: Path | None = None) -> str:
        display_path = self.relative_path(relative_to)
        return f"{self.coverage:6.2f}%  {display_path}"

    def relative_path(self, relative_to: Path | None) -> Path:
        if relative_to is None:
            return self.path
        try:
            return self.path.relative_to(relative_to)
        except ValueError:
            return self.path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    Parse command-line options for summarizing Cobertura XML coverage data.

    Returns:
        argparse.Namespace: Parsed arguments containing the report path,
            optional path-prefix filter, result limit, and sort order flag.
    """
    parser = argparse.ArgumentParser(description="Summarize Cobertura XML coverage report.")
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help="Path to Cobertura XML report (default: %(default)s).",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help=("Only include files whose (relative) path starts with this prefix. Use empty string to include all."),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit output to the N lowest-covered entries.",
    )
    parser.add_argument(
        "--descending",
        action="store_true",
        help="Sort in descending order (default: ascending).",
    )
    return parser.parse_args(argv)


def load_report(report_path: Path) -> ET.Element:
    """
    Load and parse the Cobertura coverage report from disk.

    Args:
        report_path (Path): Path to the Cobertura XML coverage report.

    Returns:
        ET.Element: Parsed XML root describing coverage information.

    Raises:
        SystemExit: If the report file does not exist.
    """
    if not report_path.is_file():
        raise ValueError(f"Coverage report not found: {report_path}")

    try:
        root = ET.parse(report_path).getroot()  # noqa: S314 - local cargo-llvm-cov report.
    except ET.ParseError as exc:
        raise ValueError(f"Coverage report must be valid XML: {report_path}: {exc}") from exc
    if root.tag != "coverage":
        raise ValueError(f"Coverage report must have a Cobertura coverage root: {report_path}")
    return root


def coverage_entries(root: ET.Element) -> Iterable[CoverageEntry]:
    """
    Iterate over coverage entries extracted from Cobertura XML data.

    Args:
        root (ET.Element): Parsed Cobertura XML root.

    Yields:
        CoverageEntry: Coverage details for each file with coverable lines.
    """
    files: dict[str, dict[int, int]] = {}
    for class_element in root.findall(".//class"):
        raw_path = class_element.get("filename")
        if not raw_path:
            continue

        lines = class_element.findall("./lines/line")
        coverable = len(lines)
        if not coverable:
            continue

        observed = files.setdefault(raw_path.replace("\\", "/"), {})
        for line in lines:
            hits = _line_hits(line, raw_path)
            raw_number = line.get("number", "")
            if not raw_number.isascii() or not raw_number.isdecimal() or int(raw_number) < 1:
                raise ValueError(f"Coverage report has invalid line number for {raw_path}: {raw_number!r}")
            number = int(raw_number)
            observed[number] = max(observed.get(number, 0), hits)
    for raw_path, observed in sorted(files.items()):
        coverable = len(observed)
        covered = sum(hits > 0 for hits in observed.values())
        path = Path(raw_path)
        coverage = (covered / coverable) * 100
        yield CoverageEntry(coverage=coverage, coverable=coverable, covered=covered, path=path)


def _line_hits(line: ET.Element, raw_path: str) -> int:
    """Return a Cobertura line hit count or fail with file and line context."""
    raw_hits = line.get("hits", "0")
    try:
        value = int(raw_hits)
    except ValueError as exc:
        line_number = line.get("number", "unknown")
        raise ValueError(f"Coverage report has non-integer hits value for {raw_path}:{line_number}: {raw_hits!r}") from exc
    if value < 0:
        raise ValueError(f"Coverage report has negative hits for {raw_path}:{line.get('number', 'unknown')}: {raw_hits!r}")
    return value


def filter_entries(
    entries: Iterable[CoverageEntry],
    prefix: str,
    relative_to: Path,
) -> list[CoverageEntry]:
    """
    Reduce coverage entries to those matching a path prefix relative to the repo root.

    Args:
        entries (Iterable[CoverageEntry]): Coverage entries to filter.
        prefix (str): Path prefix to match against each entry.
        relative_to (Path): Base directory used to compute relative paths.

    Returns:
        List[CoverageEntry]: Entries whose relative paths start with the specified prefix.
    """
    if not prefix:
        return list(entries)
    normalized_prefix = prefix if prefix.endswith("/") else f"{prefix}/"
    filtered: list[CoverageEntry] = []
    for entry in entries:
        relative = entry.relative_path(relative_to)
        relative_str = relative.as_posix()
        if relative_str.startswith(normalized_prefix):
            filtered.append(entry)
    return filtered


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    """
    Execute the coverage reporting workflow based on CLI arguments.

    This function parses CLI arguments, loads coverage data, filters results,
    and prints formatted output.
    """
    args = parse_args(argv)
    if args.limit is not None and args.limit < 1:
        raise ValueError("coverage limit must be a positive integer")
    data = load_report(args.report)

    repo_root = (root or Path.cwd()).resolve()
    entries = list(coverage_entries(data))
    filtered = filter_entries(entries, args.prefix, repo_root)

    if not filtered:
        prefix_message = f" with prefix '{args.prefix}'" if args.prefix else ""
        print(f"No coverable files found{prefix_message}.")
        return 0

    sorted_entries = sorted(filtered, key=lambda item: (-item.coverage if args.descending else item.coverage, item.path.as_posix()))
    if args.limit is not None:
        sorted_entries = sorted_entries[: args.limit]

    for entry in sorted_entries:
        print(entry.format(relative_to=repo_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
