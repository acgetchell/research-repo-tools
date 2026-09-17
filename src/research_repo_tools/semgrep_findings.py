"""Validate repository-owned Semgrep fixture annotations."""

import collections
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypeIs

RULE_ANNOTATION = re.compile(r"\bruleid:\s*([A-Za-z0-9_.-]+(?:\s*,\s*[A-Za-z0-9_.-]+)*)")


type ParsedObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class Finding:
    """One immutable finding with a nonempty rule/path and ordered positive span."""

    check_id: str
    path: Path
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class SemgrepResults:
    """Validated subset of Semgrep JSON needed by fixture checks."""

    results: tuple[Finding, ...]


def _is_parsed_object(value: object) -> TypeIs[ParsedObject]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _path_argument(argv: list[str]) -> Path | None:
    if len(argv) <= 1:
        print("Missing required path argument: sys.argv[1]", file=sys.stderr)
        return None

    path = Path(argv[1])
    if not path.exists():
        print(f"path argument does not exist: {path}", file=sys.stderr)
        return None
    if not path.is_file():
        print(f"path argument is not a file: {path}", file=sys.stderr)
        return None
    if not os.access(path, os.R_OK):
        print(f"path argument is not readable: {path}", file=sys.stderr)
        return None
    return path


def _semgrep_results() -> SemgrepResults | None:
    semgrep_json = os.environ.get("SEMGREP_JSON")
    if semgrep_json is None:
        print("Missing required SEMGREP_JSON environment variable", file=sys.stderr)
        return None
    try:
        return parse_results(semgrep_json)
    except ValueError as error:
        print(f"Invalid SEMGREP_JSON: {error}", file=sys.stderr)
        return None


def _finding_line(value: object, field: str, errors: list[str]) -> int | None:
    line = value.get("line") if _is_parsed_object(value) else None
    if type(line) is not int or line < 1:
        errors.append(f"missing positive integer field '{field}.line'")
        return None
    return line


def _parse_finding(value: object, index: int) -> Finding:
    if not _is_parsed_object(value):
        raise ValueError(f"result {index} is not an object")
    errors: list[str] = []
    check_id = value.get("check_id")
    if not isinstance(check_id, str) or not check_id.strip():
        errors.append("missing non-empty string field 'check_id'")
        check_id = None
    path = value.get("path")
    if not isinstance(path, str) or not path or "\x00" in path:
        errors.append("missing non-empty finding path")
        path = None
    start = _finding_line(value.get("start"), "start", errors)
    end = _finding_line(value.get("end"), "end", errors)
    if start is not None and end is not None and end < start:
        errors.append(f"has end.line {end} before start.line {start}")
    if check_id is None or path is None or start is None or end is None or end < start:
        raise ValueError("; ".join(f"result {index} {error}" for error in errors))
    return Finding(check_id, Path(path), start, end)


def parse_results(text: str) -> SemgrepResults:
    """Reject malformed or incomplete scan output before trusting its findings."""
    data: object = json.loads(text)
    if not _is_parsed_object(data):
        raise ValueError("expected a JSON object")
    errors = data.get("errors", [])
    if not isinstance(errors, list) or errors:
        raise ValueError("malformed or incomplete Semgrep output: errors must be an empty list when present")
    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError("expected 'results' to be a list")

    parsed_results: list[Finding] = []
    malformed_results: list[str] = []
    for index, result in enumerate(results):
        try:
            parsed_results.append(_parse_finding(result, index))
        except ValueError as error:
            malformed_results.append(str(error))
    if malformed_results:
        raise ValueError("malformed Semgrep findings: " + "; ".join(malformed_results))
    return SemgrepResults(results=tuple(parsed_results))


type ExpectedFinding = tuple[str, int]


def _expected_findings(path: Path) -> collections.Counter[ExpectedFinding]:
    expected: collections.Counter[ExpectedFinding] = collections.Counter()
    lines = path.read_text(encoding="utf-8").splitlines()
    for line_number, line in enumerate(lines, start=1):
        for match in RULE_ANNOTATION.finditer(line):
            finding_line = line_number + 1
            while finding_line <= len(lines):
                candidate = lines[finding_line - 1].strip()
                if candidate and not candidate.startswith("```"):
                    break
                finding_line += 1
            expected.update((rule_id.strip(), finding_line) for rule_id in match.group(1).split(",") if rule_id.strip())
    return expected


def _finding_mismatches(
    expected: collections.Counter[ExpectedFinding],
    actual: tuple[Finding, ...],
) -> tuple[str, ...]:
    unmatched_actual = list(actual)
    mismatches: list[str] = []

    for (rule_id, line), expected_count in sorted(expected.items()):
        for _ in range(expected_count):
            match_index = min(
                (index for index, finding in enumerate(unmatched_actual) if finding.check_id == rule_id and finding.start_line <= line <= finding.end_line),
                key=lambda index: unmatched_actual[index].end_line,
                default=None,
            )
            if match_index is None:
                mismatches.append(f"{rule_id} at line {line}: expected finding not reported")
            else:
                unmatched_actual.pop(match_index)

    for finding in sorted(unmatched_actual, key=lambda item: (item.check_id, item.start_line, item.end_line)):
        span = str(finding.start_line) if finding.start_line == finding.end_line else f"{finding.start_line}-{finding.end_line}"
        mismatches.append(f"{finding.check_id} at lines {span}: unexpected finding")

    return tuple(mismatches)


def main() -> int:
    """Compare expected fixture annotations with the supplied Semgrep results."""
    path = _path_argument(sys.argv)
    if path is None:
        return 1

    expected = _expected_findings(path)

    semgrep = _semgrep_results()
    if semgrep is None:
        return 1

    target = path.resolve()
    for finding in semgrep.results:
        if finding.path.resolve() != target:
            print(f"Semgrep finding path does not identify fixture {path}: {finding.path}", file=sys.stderr)
            return 1
    mismatches = _finding_mismatches(expected, semgrep.results)
    if not mismatches:
        return 0

    print(f"Semgrep fixture mismatch in {path}", file=sys.stderr)
    for mismatch in mismatches:
        print(f"  {mismatch}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
