"""Configured changelog generation, resources, release notes, and local tags."""

import re
import tempfile
import tomllib
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path

from research_repo_tools import archive_changelog as archive
from research_repo_tools import changelog_notes
from research_repo_tools.config import Config
from research_repo_tools.files import replace_many
from research_repo_tools.postprocess_changelog import format_markdown, postprocess_text
from research_repo_tools.process import run_git_command, run_git_command_with_input, run_safe_command
from research_repo_tools.release_tags import _GITHUB_TAG_ANNOTATION_LIMIT, SEMVER_PATTERN, _heading_to_anchor, validate_semver

TEMPLATES = ("cliff.toml", "justfile", "research-repo-tools.toml", "CHANGELOG.md", "rumdl.toml")
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def template(name: str, *, owner: str | None = None, repository: str | None = None) -> str:
    """Read a packaged template; resolve remote parameters without code interpolation."""
    if name not in TEMPLATES:
        raise ValueError(f"unknown template {name!r}; choose {', '.join(TEMPLATES)}")
    text = files("research_repo_tools").joinpath("templates", name).read_text(encoding="utf-8")
    for placeholder, value in (("__OWNER__", owner), ("__REPOSITORY__", repository)):
        if placeholder not in text or value is None:
            continue
        if not _COMPONENT.fullmatch(value) or value in {".", ".."}:
            raise ValueError("GitHub owner and repository must be single URL path components")
        text = text.replace(placeholder, value)
    return text.replace("__SEMVER_PATTERN__", SEMVER_PATTERN)


def write_template(path: Path, text: str) -> None:
    """Explicitly create a template without overwriting any existing file or symlink."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def _paths(config: Config) -> tuple[Path, Path, Path | None]:
    section = config.changelog
    path = config.root / "CHANGELOG.md"
    archives = config.root / "docs/archives/changelog"
    formatter = config.path(section.formatter) if section.formatter is not None else None
    return path, archives, formatter


def _declared_dates(path: Path, archives: Path) -> dict[str, str]:
    """Read dated release authorities before generating replacement history."""
    dates: dict[str, str] = {}
    for source in (path, *sorted(archives.glob("*.md"))):
        if not source.exists():
            continue
        parsed = archive.parse_changelog(archive._extract_link_defs(source.read_text(encoding="utf-8"))[0])
        for heading in parsed.release_headings:
            if heading.date is None:
                continue
            if heading.version in dates and dates[heading.version] != heading.date:
                raise ValueError(f"conflicting declared dates for {heading.version} in {source}")
            dates[heading.version] = heading.date
    return dates


def _preserve_declared_dates(text: str, dates: dict[str, str]) -> str:
    """Retain authored dates, including dates missing from generated headings."""
    lines = text.splitlines(keepends=True)
    for heading in archive.parse_changelog(text).release_headings:
        if heading.version not in dates:
            continue
        line = lines[heading.line - 1]
        declared = dates[heading.version]
        if heading.date_span is None:
            lines[heading.line - 1] = line.rstrip() + f" - {declared}\n"
        else:
            start, end = heading.date_span
            lines[heading.line - 1] = line[:start] + declared + line[end:]
    return "".join(lines)


def generate(config: Config, *, tag: str | None = None, released: str | None = None, dry_run: bool = False) -> str:
    """Generate, normalize, and rotate history, then publish with recoverable rollback.

    git-cliff runs offline. A prospective release requires an explicit date; it
    does not acquire an accidental release date from this machine's wall clock.
    Dry runs validate every candidate and return the trimmed root changelog
    without creating directories or replacing root/archive files.
    """
    path, archives, formatter = _paths(config)
    section = config.changelog
    if path.is_symlink():
        raise ValueError(f"Changelog output must not be a symlink: {path}")
    dates = _declared_dates(path, archives)
    if bool(tag) != bool(released):
        raise ValueError("prospective changelog generation requires both --tag and --date")
    if tag:
        validate_semver(tag)
        assert released is not None
        if date.fromisoformat(released).isoformat() != released:
            raise ValueError("release date must be YYYY-MM-DD")
        if tag.removeprefix("v") in dates and dates[tag.removeprefix("v")] != released:
            raise ValueError(f"prospective date conflicts with declared date for {tag}")
    if section.cliff_config is not None:
        rendered = config.path(section.cliff_config).read_text(encoding="utf-8")
    else:
        if section.owner is None or section.repository is None:
            raise ValueError("changelog generation requires owner and repository, or cliff-config")
        rendered = template("cliff.toml", owner=section.owner, repository=section.repository)
    tomllib.loads(rendered)
    with tempfile.TemporaryDirectory(prefix="research-repo-tools-cliff-") as directory:
        cliff = Path(directory) / "cliff.toml"
        cliff.write_text(rendered, encoding="utf-8", newline="\n")
        args = ["--config", str(cliff), "--offline", "--no-exec"]
        if tag:
            args += ["--tag", tag]
        generated = run_safe_command("git-cliff", args, cwd=config.root).stdout
    if not generated.strip():
        raise ValueError("git-cliff returned empty changelog output")
    if tag:
        assert released is not None
        generated = archive.replace_release_date(generated, tag.removeprefix("v"), released, required=True)
    generated = _preserve_declared_dates(generated, dates)
    result = postprocess_text(generated)
    parsed = archive.parse_changelog(archive._extract_link_defs(result)[0])
    if parsed.unreleased is None and not parsed.version_blocks:
        raise ValueError("git-cliff must generate at least one release or Unreleased section")

    def format_candidate(candidate: str, target: Path) -> str:
        expected = archive.parse_changelog(archive._extract_link_defs(candidate)[0])
        if formatter is not None:
            candidate = format_markdown(candidate, target, formatter)
        parsed = archive.parse_changelog(archive._extract_link_defs(candidate)[0])
        archive.require_preserved_releases(expected, parsed)
        if target == path and parsed.unreleased is None and not parsed.version_blocks:
            raise ValueError("formatted changelog must contain at least one release or Unreleased section")
        return candidate

    candidates = dict(archive.plan_archives(path, result, archives, formatter=format_candidate if formatter is not None else None))
    candidates.setdefault(path, result)
    candidates = {target: format_candidate(candidate, target) for target, candidate in candidates.items()}
    if not dry_run:
        replace_many({target: candidate.encode("utf-8") for target, candidate in candidates.items()})
    return candidates[path]


def notes(config: Config, tag: str) -> tuple[str, Path, str]:
    """Extract a unique release body and its reference links from root or archive."""
    validate_semver(tag)
    version = tag.removeprefix("v")
    path, archive_dir, _formatter = _paths(config)
    candidates = (path, archive_dir / f"{archive._minor_key(version)}.md")
    matches: list[tuple[str, Path, str]] = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        extracted = changelog_notes.extract(candidate.read_text(encoding="utf-8"), version)
        if extracted is not None:
            body, heading = extracted
            matches.append((body, candidate, heading))
    if len(matches) > 1:
        raise ValueError(f"Duplicate release {tag} across root and archive")
    if matches:
        return matches[0]
    raise ValueError(f"release notes for {tag} not found in configured changelog or archive")


def check(config: Config) -> None:
    """Validate the whole root changelog and all archives without changing files."""
    path, archives, _formatter = _paths(config)
    seen: dict[str, Path] = {}
    for source in (path, *sorted(archives.glob("*.md"))):
        text = source.read_text(encoding="utf-8")
        changelog_notes.reference_definitions(text)
        parsed = archive.parse_changelog(archive._extract_link_defs(text)[0])
        if not parsed.version_blocks and parsed.unreleased is None:
            raise ValueError(f"no release or Unreleased section in {source}")
        if source != path and (parsed.unreleased is not None or any(archive._minor_key(version) != source.stem for version, _ in parsed.version_blocks)):
            raise ValueError(f"archive contains releases outside its minor series: {source}")
        for version, _block in parsed.version_blocks:
            if version in seen:
                raise ValueError(f"Duplicate release {version} in {seen[version]} and {source}")
            seen[version] = source


def tag(config: Config, version: str, *, force: bool = False, dry_run: bool = False) -> str:
    """Create an annotated local tag after validating metadata, date, and notes.

    Never pushes or publishes a release. --force uses Git's ref replacement;
    the previous tag is not deleted before the replacement object exists.
    """
    validate_semver(version)
    check(config)
    policy = config.release
    from research_repo_tools.release_metadata import read_package_info

    package = read_package_info(config.root)
    if version.removeprefix("v") != package.version:
        raise ValueError(f"tag {version} does not match package version {package.version!r}")
    body, source, heading = notes(config, version)
    match = archive._RELEASE_HEADING_RE.fullmatch(heading)
    assert match is not None
    released = match.group("date")
    if released is None:
        raise ValueError("tagging requires a dated changelog release heading")
    if policy.date_policy == "today" and released != datetime.now(UTC).date().isoformat():
        raise ValueError("release heading date must equal the current UTC date before tagging")
    citation = config.root / "CITATION.cff"
    if citation.exists():
        from research_repo_tools.release_metadata import _citation_date_reference

        if _citation_date_reference(citation).value != released:
            raise ValueError("CITATION.cff date and changelog release date differ")
    if len(body.encode("utf-8")) > _GITHUB_TAG_ANNOTATION_LIMIT:
        from research_repo_tools.release_tags import _github_repo_url

        section = config.changelog
        owner, repository = section.owner, section.repository
        if not owner or not repository:
            raise ValueError("oversized tag notes require explicit changelog.owner and changelog.repository")
        template("cliff.toml", owner=owner, repository=repository)
        url = _github_repo_url(f"{owner}/{repository}")
        relative = source.resolve().relative_to(config.root).as_posix()
        body = f"Version {package.version}\n\nSee full changelog:\n<{url}/blob/{version}/{relative}#{_heading_to_anchor(heading)}>\n"
    exists = run_git_command(["show-ref", "--verify", "--quiet", f"refs/tags/{version}"], cwd=config.root, check=False)
    if exists.returncode not in (0, 1):
        raise ValueError("Git could not check the existing tag reference")
    if exists.returncode == 0 and not force:
        raise ValueError(f"tag {version} already exists; use --force to replace it")
    if not dry_run:
        args = ["tag", "-a", version, "-F", "-", "--cleanup=verbatim"]
        if force:
            args.insert(1, "-f")
        run_git_command_with_input(args, input_data=body, cwd=config.root)
    return body
