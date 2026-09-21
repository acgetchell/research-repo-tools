"""Validate synchronized release metadata against the package version."""

import argparse
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, TypeGuard

from packaging.utils import canonicalize_name

from research_repo_tools.archive_changelog import ParsedChangelog, parse_changelog
from research_repo_tools.config import ReleasePolicy, load
from research_repo_tools.release_tags import SEMVER_PATTERN
from research_repo_tools.toml_source import key_line

if TYPE_CHECKING:
    from collections.abc import Sequence

SKIP_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tmp_pycache",
        ".venv",
        "archive",
        "archives",
        "target",
        "tests",
    }
)
SKIP_MARKDOWN_FILES = frozenset({"CHANGELOG.md"})

# Semgrep 1.175.0 falls back to tree-sitter for this alias; that parser
# mishandles rf-string prefixes, so standalone regex f-strings below use escapes.
type ParsedObject = dict[str, object]


class ReleaseCheckError(ValueError):
    """Raised when release metadata cannot be parsed unambiguously."""


def _is_parsed_object(value: object) -> TypeGuard[ParsedObject]:
    """Return true when a parsed TOML value is an object with string keys."""
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _require_parsed_object(value: object, context: str) -> ParsedObject:
    """Return *value* as a TOML object or raise with context."""
    if not _is_parsed_object(value):
        msg = f"{context} is not a TOML object"
        raise ReleaseCheckError(msg)
    return value


def _read_toml(path: Path) -> ParsedObject:
    """Parse *path* as TOML and return its root table."""
    data: object = tomllib.loads(path.read_text(encoding="utf-8"))
    return _require_parsed_object(data, str(path))


def _require_table(data: ParsedObject, key: str, path: Path) -> ParsedObject:
    """Return a required child TOML table."""
    table = data.get(key)
    if not _is_parsed_object(table):
        msg = f"{path} is missing a [{key}] table"
        raise ReleaseCheckError(msg)
    return table


def _require_string(data: ParsedObject, key: str, context: str) -> str:
    """Return a required string field."""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        msg = f"{context} is missing a string {key}"
        raise ReleaseCheckError(msg)
    return value


@dataclass(frozen=True, slots=True)
class PackageInfo:
    """Package identity that defines the expected release version."""

    name: str | None
    version: str


@dataclass(frozen=True, slots=True)
class PythonProjectInfo:
    """Python support-package identity used to locate its uv lock entry."""

    name: str
    version: str


class ReferenceKind(StrEnum):
    """A release surface whose version must match the package manifest."""

    CARGO_ADD = "cargo add command"
    CARGO_LOCK = "Cargo.lock root package"
    CHANGELOG = "latest generated changelog release"
    CHANGELOG_COMPARISON = "current changelog comparison target"
    CITATION = "CITATION.cff version"
    DEPENDENCY_SNIPPET = "documentation dependency snippet"
    PYPROJECT = "pyproject.toml project"
    UV_LOCK = "uv.lock editable package"


@dataclass(frozen=True, slots=True)
class VersionReference:
    """A parsed release-version reference with source location."""

    path: Path
    line: int
    version: str
    kind: ReferenceKind
    text: str


@dataclass(frozen=True, slots=True)
class VersionMismatch:
    """A release-version reference that does not match the package manifest."""

    reference: VersionReference
    package: PackageInfo


class MetadataKind(StrEnum):
    """A release metadata field that must agree across publication surfaces."""

    CHANGELOG_DATE = "latest changelog release date"
    CITATION_DATE = "CITATION.cff date-released"
    CITATION_DOI = "CITATION.cff concept DOI"
    README_DOI = "README DOI badge target"
    REFERENCES_DOI = "REFERENCES.md concept DOI"


@dataclass(frozen=True, slots=True)
class MetadataReference:
    """A parsed non-version release metadata reference with source location."""

    path: Path
    line: int
    value: str
    kind: MetadataKind
    text: str


@dataclass(frozen=True, slots=True)
class MetadataMismatch:
    """A release metadata reference that differs from its canonical value."""

    reference: MetadataReference
    expected: str


def _read_python_project_info(pyproject_toml: Path) -> PythonProjectInfo:
    """Read the Python support package name and version."""
    project = _require_table(_read_toml(pyproject_toml), "project", pyproject_toml)
    return PythonProjectInfo(
        name=_require_string(project, "name", f"{pyproject_toml} [project]"),
        version=_require_string(project, "version", f"{pyproject_toml} [project]"),
    )


def read_package_info(root: Path) -> PackageInfo:
    """Infer the release identity from existing Cargo or Python project metadata."""
    cargo = root / "Cargo.toml"
    if cargo.is_file():
        data = _read_toml(cargo)
        if "package" in data:
            package = _require_table(data, "package", cargo)
            version_source = package
            if package.get("version") == {"workspace": True}:
                version_source = _require_table(_require_table(data, "workspace", cargo), "package", cargo)
            return PackageInfo(_require_string(package, "name", str(cargo)), _require_string(version_source, "version", str(cargo)))
        workspace = _require_table(data, "workspace", cargo)
        package = _require_table(workspace, "package", cargo)
        return PackageInfo(None, _require_string(package, "version", str(cargo)))
    project = _read_python_project_info(root / "pyproject.toml")
    return PackageInfo(project.name, project.version)


def package_version_reference(root: Path, package: PackageInfo) -> tuple[Path, int]:
    cargo = root / "Cargo.toml"
    if cargo.is_file():
        package_table = _read_toml(cargo).get("package")
        table = "package" if isinstance(package_table, dict) and package_table.get("version") != {"workspace": True} else "workspace.package"
        return cargo, _toml_table_key_line(cargo, table, "version")
    project = root / "pyproject.toml"
    return project, _toml_table_key_line(project, "project", "version")


def workspace_member_manifests(root: Path) -> list[Path]:
    """Expand declared workspace members and exclusions once for release tooling."""
    workspace = _read_toml(root / "Cargo.toml").get("workspace", {})
    workspace = _require_parsed_object(workspace, "workspace")

    def path_patterns(field: str) -> list[str]:
        patterns = workspace.get(field, [])
        if not isinstance(patterns, list) or any(not isinstance(pattern, str) or not pattern for pattern in patterns):
            raise ReleaseCheckError(f"workspace.{field} must be an array of nonempty path patterns")
        return patterns

    excluded_paths = {path.resolve() for pattern in path_patterns("exclude") for path in root.glob(pattern)}
    manifests: set[Path] = set()
    for pattern in path_patterns("members"):
        matched = sorted(root.glob(pattern))
        if not matched:
            raise ReleaseCheckError(f"workspace member not found: {pattern}")
        for directory in matched:
            if directory.resolve() in excluded_paths:
                continue
            manifest = directory / "Cargo.toml"
            if not manifest.resolve().is_relative_to(root.resolve()) or manifest.is_symlink():
                raise ReleaseCheckError(f"workspace member must be a repository-contained regular file: {manifest}")
            manifests.add(manifest)
    return sorted(manifests)


def cargo_lock_references(root: Path, package: PackageInfo) -> list[VersionReference]:
    """Locate release-owned Cargo lock entries without touching dependencies."""
    cargo = root / "Cargo.toml"
    if not cargo.is_file() or not (root / "Cargo.lock").is_file():
        return []
    data = _read_toml(cargo)
    root_package = data.get("package")
    references = [_cargo_lock_reference(root / "Cargo.lock", package)] if root_package is not None else []
    if isinstance(root_package, dict) and root_package.get("version") != {"workspace": True}:
        return references
    workspace = _require_table(data, "workspace", cargo)
    if not workspace.get("members"):
        raise ReleaseCheckError("workspace.members must explicitly name member paths")
    seen = {package.name} if root_package is not None else set()
    for manifest in workspace_member_manifests(root):
        data = _require_table(_read_toml(manifest), "package", manifest)
        if data.get("version") != {"workspace": True}:
            continue
        name = _require_string(data, "name", str(manifest))
        if name not in seen:
            references.append(_cargo_lock_reference(root / "Cargo.lock", PackageInfo(name, package.version)))
            seen.add(name)
    return references


def _toml_table_key_line(path: Path, table_name: str, key: str) -> int:
    """Return the line number for *key* in a TOML table."""
    try:
        return key_line(path.read_text(encoding="utf-8"), table_name, key)
    except ValueError as error:
        raise ReleaseCheckError(f"{path} {error}") from error


def _version_reference(path: Path, line: int, version: str, kind: ReferenceKind) -> VersionReference:
    """Build a version reference and include the source line text."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not 1 <= line <= len(lines):
        msg = f"{path} has no line {line} for {kind}"
        raise ReleaseCheckError(msg)
    return VersionReference(path=path, line=line, version=version, kind=kind, text=lines[line - 1].strip())


def _package_entries(path: Path) -> list[ParsedObject]:
    """Return TOML ``[[package]]`` entries from a lockfile."""
    packages = _read_toml(path).get("package")
    if not isinstance(packages, list):
        msg = f"{path} is missing [[package]] entries"
        raise ReleaseCheckError(msg)
    return [_require_parsed_object(package, f"{path} [[package]] entry {index}") for index, package in enumerate(packages, start=1)]


def _array_table_key_line(path: Path, table_name: str, table_index: int, key: str) -> int:
    """Return the line for *key* inside the requested array-table entry."""
    try:
        return key_line(path.read_text(encoding="utf-8"), table_name, key, index=table_index)
    except ValueError as error:
        raise ReleaseCheckError(f"{path} {error}") from error


def _single_package_reference(
    path: Path,
    entries: list[ParsedObject],
    candidate_indices: list[int],
    package_name: str,
    kind: ReferenceKind,
) -> VersionReference:
    """Return the only matching package reference or raise on ambiguity."""
    if len(candidate_indices) != 1:
        msg = f"{path} must contain exactly one {kind} named {package_name!r}; found {len(candidate_indices)}"
        raise ReleaseCheckError(msg)
    index = candidate_indices[0]
    version = _require_string(entries[index], "version", f"{path} [[package]] entry {index + 1}")
    line = _array_table_key_line(path, "package", index, "version")
    return _version_reference(path, line, version, kind)


def _cargo_lock_reference(path: Path, package: PackageInfo) -> VersionReference:
    """Return the root package reference from Cargo.lock."""
    assert package.name is not None
    entries = _package_entries(path)
    candidates = [index for index, entry in enumerate(entries) if entry.get("name") == package.name and "source" not in entry]
    return _single_package_reference(path, entries, candidates, package.name, ReferenceKind.CARGO_LOCK)


def _pyproject_reference(path: Path, project: PythonProjectInfo) -> VersionReference:
    """Return the Python project version reference."""
    line = _toml_table_key_line(path, "project", "version")
    return _version_reference(path, line, project.version, ReferenceKind.PYPROJECT)


def _uv_lock_reference(path: Path, project: PythonProjectInfo) -> VersionReference:
    """Return the editable Python project reference from uv.lock."""
    entries = _package_entries(path)
    candidates: list[int] = []
    for index, entry in enumerate(entries):
        source = entry.get("source")
        name = entry.get("name")
        if (
            isinstance(name, str)
            and canonicalize_name(name) == canonicalize_name(project.name)
            and _is_parsed_object(source)
            and (source.get("editable") == "." or source.get("virtual") == ".")
        ):
            candidates.append(index)
    return _single_package_reference(path, entries, candidates, project.name, ReferenceKind.UV_LOCK)


_CITATION_VERSION_RE = re.compile(r"^version:\s*(?P<quote>['\"]?)(?P<version>[0-9A-Za-z][0-9A-Za-z.+-]*)(?P=quote)\s*(?:#.*)?$")


def _citation_reference(path: Path) -> VersionReference:
    """Return the single top-level CITATION.cff version reference."""
    references: list[VersionReference] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.startswith("version:"):
            continue
        match = _CITATION_VERSION_RE.fullmatch(line)
        if match is None:
            msg = f"{path}:{line_number}: top-level version must be a non-empty scalar"
            raise ReleaseCheckError(msg)
        references.append(_version_reference(path, line_number, match.group("version"), ReferenceKind.CITATION))
    if len(references) != 1:
        msg = f"{path} must contain exactly one top-level version; found {len(references)}"
        raise ReleaseCheckError(msg)
    return references[0]


def _parsed_changelog(path: Path) -> ParsedChangelog:
    """Use the same validated, fence-aware heading grammar as archiving."""
    try:
        return parse_changelog(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ReleaseCheckError(f"{path}: {error}") from error


def _changelog_reference(path: Path) -> VersionReference:
    """Return the first generated release heading from CHANGELOG.md."""
    parsed = _parsed_changelog(path)
    if parsed.release_headings:
        heading = parsed.release_headings[0]
        return _version_reference(path, heading.line, heading.version, ReferenceKind.CHANGELOG)
    msg = f"{path} has no generated release heading"
    raise ReleaseCheckError(msg)


def _metadata_reference(path: Path, line: int, value: str, kind: MetadataKind, text: str) -> MetadataReference:
    """Build a metadata reference while preserving its diagnostic context."""
    return MetadataReference(path=path, line=line, value=value, kind=kind, text=text.strip())


def _single_reference(references: list[MetadataReference], path: Path, description: str) -> MetadataReference:
    """Require exactly one parsed metadata reference."""
    if len(references) != 1:
        location = f":{references[0].line}" if references else ""
        lines = f" at lines {', '.join(str(reference.line) for reference in references)}" if references else ""
        msg = f"{path}{location} must contain exactly one {description}; found {len(references)}{lines}"
        raise ReleaseCheckError(msg)
    return references[0]


def _citation_metadata_reference(path: Path, field: str, value_pattern: str, kind: MetadataKind) -> MetadataReference:
    """Return one non-empty top-level scalar from CITATION.cff."""
    pattern = re.compile(f"^{re.escape(field)}:\\s*(?P<quote>['\\\"]?)(?P<value>{value_pattern})(?P=quote)\\s*(?:#.*)?$")
    references: list[MetadataReference] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.startswith(f"{field}:"):
            continue
        match = pattern.fullmatch(line)
        if match is None:
            msg = f"{path}:{line_number}: top-level {field} must be a valid non-empty scalar"
            raise ReleaseCheckError(msg)
        references.append(_metadata_reference(path, line_number, match.group("value"), kind, line))
    return _single_reference(references, path, f"top-level {field}")


def _citation_doi_reference(path: Path) -> MetadataReference:
    """Return the stable concept DOI from CITATION.cff."""
    return _citation_metadata_reference(path, "doi", r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", MetadataKind.CITATION_DOI)


def _citation_date_reference(path: Path) -> MetadataReference:
    """Return and validate the release date from CITATION.cff."""
    reference = _citation_metadata_reference(path, "date-released", r"\d{4}-\d{2}-\d{2}", MetadataKind.CITATION_DATE)
    try:
        date.fromisoformat(reference.value)
    except ValueError as error:
        msg = f"{path}:{reference.line}: date-released is not a valid calendar date: {reference.value}"
        raise ReleaseCheckError(msg) from error
    return reference


def _changelog_date_reference(path: Path, version: str) -> MetadataReference | None:
    """Return the validated date on the current package-version heading."""
    for heading in _parsed_changelog(path).release_headings:
        if heading.version != version:
            continue
        if heading.date is None:
            raise ReleaseCheckError(f"{path}:{heading.line}: current-version heading must contain exactly one ISO release date")
        line = path.read_text(encoding="utf-8").splitlines()[heading.line - 1]
        return _metadata_reference(path, heading.line, heading.date, MetadataKind.CHANGELOG_DATE, line)
    return None


_README_DOI_RE = re.compile(r"\[!\[DOI\]\([^)]*\)\]\(https://doi\.org/(?P<doi>10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\)")


def _readme_doi_reference(path: Path) -> MetadataReference | None:
    """Return the DOI targeted by the README badge."""
    references: list[MetadataReference] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        matches = list(_README_DOI_RE.finditer(line))
        if line.count("[![DOI]") != len(matches):
            raise ReleaseCheckError(f"{path}:{line_number}: malformed DOI badge target")
        for match in matches:
            references.append(_metadata_reference(path, line_number, match.group("doi"), MetadataKind.README_DOI, line))
    return _single_reference(references, path, "DOI badge target") if references else None


_REFERENCES_DOI_RE = re.compile(r"^- DOI: <https://doi\.org/(?P<doi>10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)>\s*$")


def _references_doi_reference(path: Path) -> MetadataReference | None:
    """Return the concept DOI entry from REFERENCES.md."""
    dois: list[MetadataReference] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = _REFERENCES_DOI_RE.fullmatch(line)
        if match is None:
            if line.startswith("- DOI:"):
                raise ReleaseCheckError(f"{path}:{line_number}: malformed concept DOI entry; expected - DOI: <https://doi.org/...>")
            continue
        dois.append(_metadata_reference(path, line_number, match.group("doi"), MetadataKind.REFERENCES_DOI, line))
    return _single_reference(dois, path, "concept DOI entry") if dois else None


def _changelog_comparison_references(path: Path, version: str) -> list[VersionReference]:
    """Return comparison targets whose link label is the current version."""
    comparison_re = re.compile(rf"^\[{re.escape(version)}\]:\s+\S+/compare/v{SEMVER_PATTERN}\.\.\.v(?P<version>{SEMVER_PATTERN})(?:\s|$)")
    references: list[VersionReference] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = comparison_re.match(line)
        if match is not None:
            references.append(_version_reference(path, line_number, match.group("version"), ReferenceKind.CHANGELOG_COMPARISON))
    return references


def _iter_markdown_files(root: Path, policy: ReleasePolicy | None = None) -> list[Path]:
    """Return active Markdown files that can carry current release references."""
    markdown_files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        relative_dir = Path(dirpath).relative_to(root)
        dirnames[:] = [dirname for dirname in dirnames if not (set((relative_dir / dirname).parts) & SKIP_DIRS)]
        markdown_files.extend(Path(dirpath) / filename for filename in filenames if filename.endswith(".md") and filename not in SKIP_MARKDOWN_FILES)
    return sorted(
        (path for path in markdown_files if policy is None or not policy.excludes(path.relative_to(root).as_posix())),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _dependency_regex(package_name: str) -> re.Pattern[str]:
    """Build a regex for Cargo dependency snippets naming *package_name*."""
    escaped_name = re.escape(package_name)
    return re.compile(f'(?<![\\w.-]){escaped_name}\\s*=\\s*(?:"(?P<plain>[^"]+)"|\\{{[^}}]*version\\s*=\\s*"(?P<table>[^"]+)"[^}}]*\\}})')


def _dependency_references(path: Path, package_name: str) -> list[VersionReference]:
    """Return dependency snippet references in a Markdown file."""
    dependency_re = _dependency_regex(package_name)
    references: list[VersionReference] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        for match in dependency_re.finditer(line):
            references.append(
                VersionReference(
                    path=path,
                    line=line_number,
                    version=match.group("plain") or match.group("table"),
                    kind=ReferenceKind.DEPENDENCY_SNIPPET,
                    text=line.strip(),
                )
            )
    return references


def _cargo_add_regex(package_name: str) -> re.Pattern[str]:
    """Build a regex for versioned cargo-add commands naming *package_name*."""
    escaped_name = re.escape(package_name)
    return re.compile(f"(?<![\\w.-])cargo\\s+add\\b[^`\\n]*?(?<![\\w.-]){escaped_name}@(?P<version>[^\\s`]+)")


def _cargo_add_references(path: Path, package_name: str) -> list[VersionReference]:
    """Return versioned cargo-add command references in a Markdown file."""
    cargo_add_re = _cargo_add_regex(package_name)
    references: list[VersionReference] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        references.extend(
            VersionReference(path, line_number, match.group("version"), ReferenceKind.CARGO_ADD, line.strip()) for match in cargo_add_re.finditer(line)
        )
    return references


def python_version_references(root: Path) -> list[VersionReference]:
    """Locate declared Python versions and their optional local uv lock entry."""
    path = root / "pyproject.toml"
    if not path.is_file() or "project" not in _read_toml(path):
        return []
    project = _read_python_project_info(path)
    references = [_pyproject_reference(path, project)]
    lock = root / "uv.lock"
    if lock.is_file():
        references.append(_uv_lock_reference(lock, project))
    return references


def _version_references(root: Path, package: PackageInfo, policy: ReleasePolicy | None = None) -> list[VersionReference]:
    """Collect all current-release references that should match the package manifest."""
    changelog_path = root / "CHANGELOG.md"
    references = [*cargo_lock_references(root, package), *python_version_references(root), _changelog_reference(changelog_path)]
    if (root / "CITATION.cff").is_file():
        references.append(_citation_reference(root / "CITATION.cff"))
    references.extend(_changelog_comparison_references(changelog_path, package.version))
    if package.name and (root / "Cargo.toml").is_file():
        for path in _iter_markdown_files(root, policy):
            references.extend(_dependency_references(path, package.name))
            references.extend(_cargo_add_references(path, package.name))
    return references


def find_version_mismatches(root: Path, *, policy: ReleasePolicy | None = None) -> list[VersionMismatch]:
    """Return release-version references that differ from the package manifest."""
    package = read_package_info(root)
    return [
        VersionMismatch(reference=reference, package=package)
        for reference in _version_references(root, package, policy)
        if reference.version != package.version
    ]


def find_release_metadata_mismatches(root: Path, *, policy: ReleasePolicy | None = None) -> list[MetadataMismatch]:
    """Return DOI and release-date references that disagree across release surfaces."""
    package = read_package_info(root)
    policy = load(root=root).release if policy is None else policy
    citation = root / "CITATION.cff"
    citation_text = citation.read_text(encoding="utf-8") if citation.is_file() else ""
    citation_date = _citation_date_reference(citation) if citation.is_file() else None
    changelog_date = _changelog_date_reference(root / "CHANGELOG.md", package.version)
    expected: list[tuple[MetadataReference, str]] = []
    if re.search(r"^doi:", citation_text, re.MULTILINE):
        citation_doi = _citation_doi_reference(citation)
        doi = citation_doi.value
        for path, reader in (
            (root / "README.md", _readme_doi_reference),
            (root / "REFERENCES.md", _references_doi_reference),
        ):
            if path.is_file():
                reference = reader(path)
                if reference is not None:
                    expected.append((reference, doi))
    if policy.final_changelog and changelog_date is None:
        raise ReleaseCheckError("final release requires the current changelog heading")
    if policy.final_changelog and citation.is_file() and citation_date is None:
        raise ReleaseCheckError("final release requires CITATION.cff date-released")
    if changelog_date is not None and citation_date is not None:
        expected.insert(0, (citation_date, changelog_date.value))
    return [MetadataMismatch(reference=reference, expected=value) for reference, value in expected if reference.value != value]


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="release-check",
        description="Check release metadata and active version references against the package manifest.",
        suggest_on_error=True,
        color=False,
    )
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd(), help="Repository root to check (default: current directory)")
    return parser.parse_args(argv)


def check(root: Path, *, policy: ReleasePolicy | None = None, previous_tag: str | None = None) -> int:
    """Print the structured release check result for command-line consumers."""
    from research_repo_tools.releases import check_release

    try:
        result = check_release(root, policy=policy, previous_tag=previous_tag)
    except (OSError, ValueError) as error:
        print(f"Could not check release-version synchronization: {error}", file=sys.stderr)
        return 1
    if result.problems:
        print("Release-version references are out of sync or release policy failed:", file=sys.stderr)
        for problem in result.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"Release metadata is synchronized at {result.discovery.package.version}.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Validate release metadata and report the synchronized version."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    return check(args.root)


if __name__ == "__main__":
    sys.exit(main())
