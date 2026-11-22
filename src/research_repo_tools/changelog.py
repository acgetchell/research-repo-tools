"""Configured changelog generation, resources, release notes, and local tags."""

import difflib
import io
import re
import shlex
import subprocess
import tempfile
import tomllib
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, date, datetime
from functools import cache
from importlib.resources import files
from pathlib import Path

from research_repo_tools.process import format_exception_diagnostics, run_command_bytes, run_git_command, run_git_command_with_input, run_safe_command
from research_repo_tools.release_tags import _GITHUB_REPO_COMPONENT_RE, _GITHUB_TAG_ANNOTATION_LIMIT, SEMVER_PATTERN, _heading_to_anchor, validate_semver
lazy from research_repo_tools import archive_changelog as archive
lazy from research_repo_tools import changelog_issues, changelog_notes
lazy from research_repo_tools.config import Config
lazy from research_repo_tools.files import preserve_files, replace_many
lazy from research_repo_tools.postprocess_changelog import check_markdown, fix_markdown, postprocess_text

TEMPLATES = (
    "CHANGELOG.md",
    "RELEASING.md",
    "VALIDATING_WORKFLOWS.md",
    "action-updates.toml",
    "benchmark.toml",
    "cargo-examples.toml",
    "cliff.toml",
    "command-timing.toml",
    "common-benchmark.toml",
    "complete-measurement.toml",
    "complete-publication.toml",
    "complete-report.toml",
    "criterion-inventory.toml",
    "criterion-saved.toml",
    "dependency-sources.toml",
    "examples.toml",
    "justfile",
    "performance-release.toml",
    "performance-report.toml",
    "profiling.toml",
    "publish-crates.yml",
    "publishing.toml",
    "python-bootstrap.py",
    "python-validation.toml",
    "release-assets.toml",
    "release-evidence.toml",
    "research-repo-tools.toml",
    "rumdl.toml",
    "semgrep-constraints.txt",
    "suite-measurement.toml",
    "zizmor.yml",
)
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def template(name: str, *, owner: str | None = None, repository: str | None = None, dependency_bodies: str = "concise") -> str:
    """Read a packaged template; resolve remote parameters without code interpolation."""
    return _template(name, owner, repository, dependency_bodies, body="entry_body")


def _template(name: str, owner: str | None, repository: str | None, dependency_bodies: str, *, body: str) -> str:
    if name not in TEMPLATES:
        raise ValueError(f"unknown template {name!r}; choose {', '.join(TEMPLATES)}")
    if dependency_bodies not in {"concise", "preserve"}:
        raise ValueError("dependency_bodies must be concise or preserve")
    resource = files("research_repo_tools")
    resource = resource.joinpath("tooling_runtime.py") if name == "python-bootstrap.py" else resource.joinpath("templates", name)
    text = resource.read_text(encoding="utf-8")
    for placeholder, value in (("__OWNER__", owner), ("__REPOSITORY__", repository)):
        if placeholder not in text or value is None:
            continue
        component = _GITHUB_REPO_COMPONENT_RE if placeholder == "__REPOSITORY__" else _COMPONENT
        if not component.fullmatch(value) or value in {".", ".."}:
            raise ValueError("GitHub owner and repository must be single URL path components")
        text = text.replace(placeholder, value)
    condition = f'{body} and group != "Dependencies"' if dependency_bodies == "concise" else body
    return text.replace("__SEMVER_PATTERN__", SEMVER_PATTERN).replace("__DEPENDENCY_BODY_CONDITION__", condition)


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


def _retained_files(path: Path, archives: Path) -> dict[Path, bytes]:
    """Snapshot the original bytes for date authority, preview, and publication checks."""
    retained = {}
    for source in (path, *sorted(archives.glob("*.md"))):
        if source.is_symlink():
            raise ValueError(f"Changelog output must not be a symlink: {source}")
        if source.exists():
            retained[source] = source.read_bytes()
    return retained


def _retained_versions(retained: dict[Path, bytes]) -> dict[str, str | None]:
    """Collect every retained release, including headings without date authority."""
    versions: dict[str, str | None] = {}
    for source, data in retained.items():
        text = io.StringIO(data.decode("utf-8"), newline=None).read()
        parsed = archive.parse_changelog(archive._extract_link_defs(text)[0])
        for heading in parsed.release_headings:
            previous = versions.get(heading.version)
            if previous and heading.date and previous != heading.date:
                raise ValueError(f"conflicting declared dates for {heading.version} in {source}")
            versions[heading.version] = heading.date or previous
    return versions


def _preserve_declared_dates(text: str, dates: dict[str, str]) -> str:
    """Retain authored dates, including dates missing from generated headings."""
    # Match the parser's LF boundaries without translating CRLF or final lines.
    lines = text.split("\n")
    for heading in archive.parse_changelog(text).release_headings:
        if heading.version not in dates:
            continue
        line = lines[heading.line - 1]
        declared = dates[heading.version]
        if heading.date_span is None:
            end = len(line.rstrip())
            lines[heading.line - 1] = line[:end] + f" - {declared}" + line[end:]
        else:
            start, end = heading.date_span
            lines[heading.line - 1] = line[:start] + declared + line[end:]
    return "\n".join(lines)


def resolve_date(config: Config, *, tag: str | None = None, released: str | None = None) -> str | None:
    """Resolve an explicit date or the configured matching prepared citation date.

    Untagged history never consults citation metadata. Explicit dates bypass
    citation lookup; neither path consults the clock or updates metadata.
    """
    if tag is None:
        if released is not None:
            raise ValueError("prospective changelog generation requires both --tag and --date")
        return None
    validate_semver(tag)
    if released is None:
        if config.changelog.date_source != "citation":
            raise ValueError("prospective changelog generation requires both --tag and --date, or changelog.date-source = 'citation'")
        from research_repo_tools.publication import _path
        from research_repo_tools.release_metadata import _citation_scalars

        path = _path(config.root, config.changelog.citation)
        try:
            values = _citation_scalars(path, ("version", "date-released"))
            if values["version"][0] != tag.removeprefix("v"):
                raise ValueError(f"citation version must match {tag}")
            released = values["date-released"][0]
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError(f"prepare matching citation metadata in {path} before generation: {error}") from error
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", released) is None or date.fromisoformat(released).isoformat() != released:
            raise ValueError("noncanonical date")
    except (TypeError, ValueError) as error:
        raise ValueError("release date must be a valid YYYY-MM-DD calendar date; prepare citation metadata or supply --date") from error
    return released


def _run_cliff(root: Path, rendered: str, args: list[str], *, context: str | None = None) -> str:
    """Render offline, optionally using an already captured release history."""
    with tempfile.TemporaryDirectory(prefix="research-repo-tools-cliff-") as directory:
        cliff = Path(directory) / "cliff.toml"
        cliff.write_text(rendered, encoding="utf-8", newline="\n")
        if context is not None:
            source = Path(directory) / "context.json"
            source.write_text(context, encoding="utf-8", newline="\n")
            args = [*args, "--from-context", str(source)]
        return run_safe_command("git-cliff", ["--config", str(cliff), "--offline", "--no-exec", *args], cwd=root).stdout


def generate(config: Config, *, tag: str | None = None, released: str | None = None, dry_run: bool = False) -> str:
    """Generate, normalize, and rotate history, then publish with recoverable rollback.

    git-cliff runs offline. Prospective dates are explicit or citation-owned;
    generation never acquires a date from this machine's wall clock.
    Dry runs validate every candidate and return the trimmed root changelog
    without creating directories or replacing root/archive files.
    """
    path, archives, _formatter = _paths(config)
    outputs = _generate_candidates(config, tag, released, _retained_files(path, archives), rebuild=False)
    if not dry_run:
        replace_many(outputs)
    return outputs[path].decode("utf-8")


def rebuild(config: Config, *, tag: str | None = None, released: str | None = None, apply: bool = False) -> str:
    """Regenerate from full Git history, return the diff, and optionally apply it."""
    shallow = run_git_command(["rev-parse", "--is-shallow-repository"], cwd=config.root).stdout.strip()
    if shallow != "false":
        raise ValueError("changelog rebuild requires full Git history; use a non-shallow checkout (fetch-depth: 0 in GitHub Actions)")
    path, archives, _formatter = _paths(config)

    def snapshot() -> dict[Path, bytes]:
        if any(parent.is_symlink() or parent.is_junction() for parent in (archives, *archives.parents) if parent.is_relative_to(config.root)):
            raise ValueError("rebuild archive directory must not contain links")
        return _retained_files(path, archives)

    before = snapshot()
    outputs = _generate_candidates(config, tag, released, before, rebuild=True)
    removed = sorted(target for target in before.keys() - outputs.keys() if archive.MINOR_ARCHIVE.fullmatch(target.name))
    updates = {target: data for target, data in outputs.items() if before.get(target) != data}
    chunks = []
    for target in sorted((*updates, *removed)):
        name = target.relative_to(config.root).as_posix()
        old = io.StringIO(before.get(target, b"").decode("utf-8")).readlines()
        new = io.StringIO(outputs.get(target, b"").decode("utf-8")).readlines()
        chunks.append(f"diff --git a/{name} b/{name}\n")
        left = f"a/{name}" if target in before else "/dev/null"
        right = f"b/{name}" if target in outputs else "/dev/null"
        if not old and not new:
            chunks.append(f"--- {left}\n+++ {right}\n")
        for line in difflib.unified_diff(old, new, left, right):
            chunks.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    if apply and (updates or removed):
        with ExitStack() as rollback:

            def verify_and_remove() -> None:
                if snapshot() != before:
                    raise ValueError("retained changelogs changed during rebuild")
                if removed:
                    rollback.enter_context(preserve_files(removed))
                    for target in removed:
                        target.unlink()

            if updates:
                replace_many(updates, verify=verify_and_remove)
            else:
                verify_and_remove()
    return "".join(chunks)


def _generate_candidates(config: Config, tag: str | None, released: str | None, retained: dict[Path, bytes], *, rebuild: bool) -> dict[Path, bytes]:
    path, archives, formatter = _paths(config)
    section = config.changelog
    if path.is_symlink():
        raise ValueError(f"Changelog output must not be a symlink: {path}")
    versions = _retained_versions(retained)
    dates = {version: value for version, value in versions.items() if value is not None}
    released = resolve_date(config, tag=tag, released=released)
    if tag:
        validate_semver(tag)
        assert released is not None
        if tag.removeprefix("v") in dates and dates[tag.removeprefix("v")] != released:
            raise ValueError(f"prospective date conflicts with declared date for {tag}")
    if section.cliff_config is not None:
        rendered = config.path(section.cliff_config).read_text(encoding="utf-8")
    else:
        if section.owner is None or section.repository is None:
            raise ValueError("changelog generation requires owner and repository, or cliff-config")
        rendered = template("cliff.toml", owner=section.owner, repository=section.repository, dependency_bodies=section.dependency_bodies)
    tomllib.loads(rendered)
    args = ["--tag", tag] if tag is not None else []
    # Both historic representations must describe the same commits and tags.
    shared = section.cliff_config is None
    context = _run_cliff(config.root, rendered, [*args, "--context"]) if shared and not rebuild and any(archives.glob("*.md")) else None
    generated = _run_cliff(config.root, rendered, args if context is None else [], context=context)
    if not generated.strip():
        raise ValueError("git-cliff returned empty changelog output")

    repository = f"{section.owner}/{section.repository}"
    closing_notes = changelog_issues.read_closing_notes(generated, config.root, repository) if shared else {}
    cleaned = changelog_issues.strip_linked_directives(generated, closing_notes, repository) if closing_notes else generated

    def prepare(raw: str, notes: dict[str, str]) -> str:
        if notes:
            raw = changelog_issues.add_closing_notes(raw, notes, repository)
        if tag:
            assert released is not None
            raw = archive.replace_release_date(raw, tag.removeprefix("v"), released, required=True)
        return _preserve_declared_dates(raw, dates)

    @cache
    def previous_history() -> str:
        # The old template emitted only git-cliff's parsed commit.body. Fall
        # back to this exact representation only for a retained release mismatch.
        assert context is not None
        previous = _template("cliff.toml", section.owner, section.repository, section.dependency_bodies, body="commit.body")
        # Retained archives could also contain standalone directives in the
        # parsed body. Compare their original text before adopting today's links.
        return _run_cliff(config.root, previous, [], context=context)

    def retained_histories(existing: str) -> Iterator[str]:
        # Reproduce only the notes already present. The archive comparison
        # still checks every word, link destination, and note placement.
        carried = changelog_issues.retained_notes(existing, closing_notes, repository)
        if closing_notes:
            yield prepare(cleaned, carried)
        yield prepare(previous_history(), carried)

    result = prepare(cleaned, closing_notes)
    parsed = archive.parse_changelog(archive._extract_link_defs(result)[0])
    if rebuild and (missing := versions.keys() - {heading.version for heading in parsed.release_headings}):
        raise ValueError(f"rebuild drops retained releases: {', '.join(sorted(missing))}")
    if parsed.unreleased is None and not parsed.version_blocks:
        raise ValueError("git-cliff must generate at least one release or Unreleased section")

    def format_candidate(candidate: str, target: Path) -> str:
        expected = archive.parse_changelog(archive._extract_link_defs(candidate)[0])
        if formatter is not None:
            candidate = fix_markdown(candidate, target, formatter)
        parsed = archive.parse_changelog(archive._extract_link_defs(candidate)[0])
        archive.require_preserved_releases(expected, parsed)
        if target == path and parsed.unreleased is None and not parsed.version_blocks:
            raise ValueError("formatted changelog must contain at least one release or Unreleased section")
        return candidate

    candidates = dict(
        archive.plan_archives(
            path,
            result,
            archives,
            formatter=format_candidate if formatter is not None else None,
            rebuild=rebuild,
            retained_histories=retained_histories if context is not None else None,
        )
    )
    if path not in candidates:
        candidates[path] = postprocess_text(result)
    candidates = {target: format_candidate(candidate, target) for target, candidate in candidates.items()}
    outputs = {target: candidate.encode("utf-8") for target, candidate in candidates.items()}
    if formatter is not None:
        check_markdown(candidates[path], path, formatter, outputs)
    if rebuild:
        seen = set()
        # Include untouched, non-series Markdown files in the final inventory.
        unchanged = {target: data for target, data in retained.items() if target not in outputs and not archive.MINOR_ARCHIVE.fullmatch(target.name)}
        for target, data in (outputs | unchanged).items():
            for version in _retained_versions({target: data}):
                if version in seen:
                    raise ValueError(f"duplicate rebuilt release {version}")
                seen.add(version)
        if missing := versions.keys() - seen:
            raise ValueError(f"rebuild drops retained releases: {', '.join(sorted(missing))}")
    return outputs


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


def tag(config: Config, version: str, *, commit: str | None = None, force: bool = False, dry_run: bool = False) -> str:
    """Create an annotated local tag after validating metadata, date, and notes.

    Never pushes or publishes a release. --force uses Git's ref replacement;
    the previous tag is not deleted before the replacement object exists.
    """
    validate_semver(version)
    from research_repo_tools._git_checkout import READ_ONLY_OPTIONS, git_context
    from research_repo_tools.dependency_provenance import check_release_dependencies
    from research_repo_tools.release_assets import require_release_checkout

    if commit is not None:
        require_release_checkout(config.root, commit)
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
    citations = dict.fromkeys((config.root / name).resolve() for name in (config.changelog.citation, "CITATION.cff"))
    for citation in citations:
        if citation.exists():
            from research_repo_tools.release_metadata import _citation_date_reference

            if _citation_date_reference(citation).value != released:
                raise ValueError(f"{citation} date and changelog release date differ")
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
    ref_args = ["show-ref", "--verify", "--quiet", f"refs/tags/{version}"]
    if commit is None:
        exists = run_git_command(ref_args, cwd=config.root, check=False).returncode
    else:
        git, environment = git_context(config.root)
        exists = run_command_bytes(git, [*READ_ONLY_OPTIONS, *ref_args], cwd=config.root, env=environment, check=False).returncode
    if exists not in (0, 1):
        raise ValueError("Git could not check the existing tag reference")
    if exists == 0 and not force:
        raise ValueError(f"tag {version} already exists; use --force to replace it")
    check_release_dependencies(config.root, packages=config.release.published_dependencies)
    if not dry_run:
        if commit is not None:
            require_release_checkout(config.root, commit)
        args = ["tag", "-a", version, "-F", "-", "--cleanup=verbatim"]
        if force:
            args.insert(1, "-f")
        if commit is None:
            run_git_command_with_input(args, input_data=body, cwd=config.root)
        else:
            git, environment = git_context(config.root)
            run_command_bytes(git, [*READ_ONLY_OPTIONS, *args, commit], input=body.encode("utf-8"), cwd=config.root, env=environment)
    return body


def tag_with_preflight(
    config: Config,
    version: str,
    *,
    commit: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    explicit_root: bool = False,
    config_file: Path | None = None,
) -> tuple[str, str]:
    """Check final metadata and create or preview a tag, returning its reviewed commit and annotation.

    Recovery commands retain the caller's project, configuration, and preview or force options.
    A metadata correction requires a newly reviewed commit. A tag operation failure can retry the original one.
    """
    from research_repo_tools.release_metadata import check as check_metadata

    if commit is None:
        commit = run_git_command(["rev-parse", "HEAD"], cwd=config.root).stdout.strip()

    def retry_command(revision: str) -> str:
        """Render a POSIX-shell retry against the same configuration and revision."""
        tag_args = [version, "--commit", revision]
        if explicit_root or config_file is not None:
            command = ["research-repo-tools", "--root", str(config.root)]
            if config_file is not None:
                command.extend(("--config", str(config_file.absolute())))
            command.extend(("changelog", "tag", *tag_args))
            if dry_run:
                command.append("--dry-run")
        else:
            command = ["just", "tag-preview" if dry_run else "tag", *tag_args]
        if force:
            command.append("--force")
        return shlex.join(command)

    if check_metadata(config.root, policy=replace(config.release, final_changelog=True), quiet=dry_run):
        raise ValueError(
            f"tagging preflight failed for {version} at {commit}. Correct the reported metadata. "
            "If tracked files change, commit and review the correction. Replace REVIEWED_COMMIT with the reviewed SHA. "
            f"Retry in a POSIX shell with\n{retry_command('REVIEWED_COMMIT')}"
        )
    try:
        body = tag(config, version, commit=commit, force=force, dry_run=dry_run)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            f"{version} at {commit}: {format_exception_diagnostics(error)}\nTag preflight must pass before creation. Resume in a POSIX shell with\n{retry_command(commit)}"
        ) from error
    return commit, body
