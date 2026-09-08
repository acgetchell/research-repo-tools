"""Prepare release metadata transactionally from one stable GitHub tag."""

import argparse
import re
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from research_repo_tools.process import ExecutableNotFoundError, get_safe_executable
from research_repo_tools.release_discovery import _publish_texts, _published_releases, _tag_version, normalize_tag
from research_repo_tools.release_metadata import (
    ReferenceKind,
    _cargo_add_regex,
    _changelog_date_reference,
    _citation_date_reference,
    _citation_reference,
    _dependency_regex,
    _iter_markdown_files,
    cargo_lock_references,
    find_release_metadata_mismatches,
    find_version_mismatches,
    package_version_reference,
    python_version_references,
    read_package_info,
)

_VERSION_ASSIGNMENT = re.compile(r"^(?:\s*version\s*=\s*|version:\s*)(?P<quote>[\"\']?)(?P<value>[0-9]+\.[0-9]+\.[0-9]+)(?P=quote)\s*(?:#.*)?$")
_DATE_ASSIGNMENT = re.compile(r"^date-released:\s*(?P<quote>[\"\']?)(?P<value>\d{4}-\d{2}-\d{2})(?P=quote)\s*(?:#.*)?$")


@dataclass(frozen=True, slots=True)
class UpdateSummary:
    """Release identities and files changed by a successful preparation."""

    tag: str
    previous_tag: str
    release_date: str
    changed_paths: tuple[Path, ...]


def parse_release_tag(tag: str) -> str:
    """Require the stable vX.Y.Z spelling, without normalizing user input."""
    if normalize_tag(tag) != tag:
        msg = f"release tag must use stable vX.Y.Z form: {tag!r}"
        raise ValueError(msg)
    return tag


def infer_previous_release(root: Path, target: str) -> str:
    """Discover the preceding stable published release, excluding drafts and prereleases."""
    get_safe_executable("gh")
    releases = _published_releases(root)
    target_version = _tag_version(target)
    if any(_tag_version(release.tag) > target_version for release in releases):
        msg = f"target {target} is older than an already published stable release"
        raise ValueError(msg)
    previous = [release.tag for release in releases if _tag_version(release.tag) < target_version]
    if not previous:
        msg = f"no published stable GitHub release precedes {target}"
        raise ValueError(msg)
    return max(previous, key=_tag_version)


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _replace_scalar(text: str, line: int, pattern: re.Pattern[str], value: str, *, allowed: frozenset[str]) -> str:
    lines = text.splitlines(keepends=True)
    source = lines[line - 1]
    match = pattern.fullmatch(source.rstrip("\r\n"))
    if match is None or match.group("value") not in allowed:
        msg = f"unsupported or unexpected release value at line {line}: {source.strip()}"
        raise ValueError(msg)
    start, end = match.span("value")
    lines[line - 1] = source[:start] + value + source[end:]
    return "".join(lines)


def _replace_version_match(match: re.Match[str], value: str, allowed: frozenset[str], group: str) -> str:
    original = match.group(group)
    if original not in allowed:
        msg = f"unexpected active release version {original!r}; expected one of {sorted(allowed)}"
        raise ValueError(msg)
    start, end = match.span(group)
    return match.group(0)[: start - match.start()] + value + match.group(0)[end - match.start() :]


def _changelog_with_date(path: Path, version: str, release_date: str, *, required: bool = False) -> str:
    original = _read_text(path)
    heading = _changelog_date_reference(path, version)
    if heading is None:
        if required:
            msg = f"CHANGELOG.md has no release heading for {version}"
            raise ValueError(msg)
        return original
    pattern = re.compile(rf"^##\s+\[?v?{re.escape(version)}\]?\s+-\s+(?P<value>\d{{4}}-\d{{2}}-\d{{2}})\s*$")
    return _replace_scalar(original, heading.line, pattern, release_date, allowed=frozenset({heading.value}))


def sync_changelog_date(root: Path, tag: str) -> None:
    """Align a newly generated heading with the prepared UTC citation date, offline."""
    root = root.resolve()
    version = parse_release_tag(tag).removeprefix("v")
    citation = root / "CITATION.cff"
    changelog = root / "CHANGELOG.md"
    for path in (citation, changelog, root / "Cargo.toml", root / "pyproject.toml"):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            msg = f"release metadata must not be a symbolic link outside the repository: {path}"
            raise ValueError(msg)
    if read_package_info(root).version != version or _citation_reference(citation).version != version:
        msg = "package and citation versions must match the target; run just update-version first"
        raise ValueError(msg)
    prepared = _changelog_with_date(changelog, version, _citation_date_reference(citation).value, required=True)
    if prepared != _read_text(changelog):
        _publish_texts(((changelog, prepared),))


def _prepare_updates(root: Path, tag: str, previous: str, release_date: str) -> dict[Path, str]:
    version = tag.removeprefix("v")
    allowed = frozenset({version, previous.removeprefix("v")})
    package = read_package_info(root)
    references = [
        package_version_reference(root, package),
        *((reference.path, reference.line) for reference in cargo_lock_references(root, package)),
        *((reference.path, reference.line) for reference in python_version_references(root)),
    ]
    citation = root / "CITATION.cff"
    if citation.is_file():
        references.append((citation, _citation_reference(citation).line))
    updates: dict[Path, str] = {}
    for path, line in references:
        updates[path] = _replace_scalar(updates.get(path, _read_text(path)), line, _VERSION_ASSIGNMENT, version, allowed=allowed)
    if citation.is_file():
        if re.search(r"^date-released:", updates[citation], re.MULTILINE):
            citation_date = _citation_date_reference(citation)
            updates[citation] = _replace_scalar(updates[citation], citation_date.line, _DATE_ASSIGNMENT, release_date, allowed=frozenset({citation_date.value}))
        else:
            newline = "\r\n" if "\r\n" in updates[citation] else "\n"
            updates[citation] = updates[citation].rstrip("\r\n") + newline + f"date-released: {release_date}" + newline
    changelog = root / "CHANGELOG.md"
    updates[changelog] = _changelog_with_date(changelog, version, release_date)
    for path in _iter_markdown_files(root):
        text = _read_text(path)
        if package.name and (root / "Cargo.toml").is_file():
            text = _dependency_regex(package.name).sub(
                lambda match: _replace_version_match(match, version, allowed, "plain" if match.group("plain") is not None else "table"), text
            )
            text = _cargo_add_regex(package.name).sub(lambda match: _replace_version_match(match, version, allowed, "version"), text)
        updates[path] = text
    return updates


def _validate_prepared(updates: dict[Path, str], root: Path, previous: str, *, policy: dict | None = None) -> None:
    """Validate the complete proposed file set without replacing any repository file."""
    with tempfile.TemporaryDirectory(prefix="research-release-validation-") as directory:
        staged = Path(directory)
        cargo = root / "Cargo.toml"
        if cargo.is_file():
            manifest = tomllib.loads(_read_text(cargo))
            for pattern in manifest.get("workspace", {}).get("members", []):
                for member in root.glob(pattern):
                    source = member / "Cargo.toml"
                    if not source.resolve().is_relative_to(root) or source.is_symlink():
                        raise ValueError(f"workspace member must be a repository-contained regular file: {source}")
                    destination = staged / source.relative_to(root)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(source.read_bytes())
        for path, text in updates.items():
            destination = staged / path.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(text.encode("utf-8"))
        mismatches = [
            mismatch
            for mismatch in find_version_mismatches(staged)
            if not (mismatch.reference.kind == ReferenceKind.CHANGELOG and mismatch.reference.version == previous.removeprefix("v"))
        ]
        metadata = find_release_metadata_mismatches(staged, policy=policy)
        if mismatches or metadata:
            msg = "prepared release metadata failed validation: " + "; ".join(str(item) for item in [*mismatches, *metadata])
            raise ValueError(msg)


def update_release_version(
    root: Path,
    tag: str,
    *,
    previous_tag: str | None = None,
    release_date: str | None = None,
    dry_run: bool = False,
    policy: dict | None = None,
) -> UpdateSummary:
    """Validate then atomically replace owned metadata; restore prior contents on failure."""
    root = root.resolve()
    tag = parse_release_tag(tag)
    owned = {root / name for name in ("Cargo.toml", "Cargo.lock", "pyproject.toml", "uv.lock", "CITATION.cff", "CHANGELOG.md")}
    owned.update(_iter_markdown_files(root))
    for path in owned:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            msg = f"release metadata must be a repository-contained regular file, not a symbolic link: {path}"
            raise ValueError(msg)
    previous = parse_release_tag(previous_tag) if previous_tag is not None else infer_previous_release(root, tag)
    if _tag_version(previous) >= _tag_version(tag):
        msg = f"previous release {previous} must precede {tag}"
        raise ValueError(msg)
    today = release_date if release_date is not None else datetime.now(UTC).date().isoformat()
    if date.fromisoformat(today).isoformat() != today:
        msg = "release date must use YYYY-MM-DD form"
        raise ValueError(msg)
    updates = _prepare_updates(root, tag, previous, today)
    if policy is None:
        from research_repo_tools.config import load

        policy = load(root=root).section("release")
    _validate_prepared(updates, root, previous, policy=policy)
    changed = tuple((path, updates[path]) for path in sorted(updates, key=lambda path: path.relative_to(root).as_posix()) if _read_text(path) != updates[path])
    if not dry_run:
        _publish_texts(changed)
    return UpdateSummary(tag, previous, today, tuple(path for path, _ in changed))


def main(argv: list[str] | None = None) -> int:
    """Prepare a release without dependency upgrades, changelog generation, or measurements."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="Target stable release tag in vX.Y.Z form")
    parser.add_argument("--sync-changelog-date", action="store_true", help="Only align a generated changelog heading with CITATION.cff, without GitHub access")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        if args.sync_changelog_date:
            sync_changelog_date(args.repo_root, args.tag)
            return 0
        summary = update_release_version(args.repo_root, args.tag)
    except subprocess.CalledProcessError as error:
        print(f"Release preparation failed: {error.stderr or error.stdout or error}", file=sys.stderr)
        return 1
    except (ExecutableNotFoundError, OSError, RuntimeError, subprocess.TimeoutExpired, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"Release preparation failed: {error}", file=sys.stderr)
        return 1
    print(f"Prepared {summary.tag} against {summary.previous_tag}; UTC release date {summary.release_date}.")
    for path in summary.changed_paths:
        print(f"Updated {path.relative_to(args.repo_root.resolve())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
