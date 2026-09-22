"""Supported release discovery, candidate validation, and transactional plans.

Adapters are trusted Python callbacks, never commands loaded from configuration.
They read the selected candidate tree and return edits or validation messages.
All publication goes through :func:`apply_release` and the shared byte transaction.
"""

import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from research_repo_tools import release_metadata as metadata
from research_repo_tools.config import load
from research_repo_tools.files import replace_many
from research_repo_tools.release_discovery import TAG, _published_releases, _tag_version, normalize_tag
from research_repo_tools.release_discovery import PublishedRelease as PublishedRelease
from research_repo_tools.release_metadata import PackageInfo as PackageInfo
from research_repo_tools.release_metadata import VersionReference as VersionReference
from research_repo_tools.release_policy import ReleasePolicy as ReleasePolicy
from research_repo_tools.release_policy import ReleaseRule as ReleaseRule
from research_repo_tools.release_policy import relative_path

__all__ = [
    "PackageInfo",
    "PublishedRelease",
    "ReleaseAdapter",
    "ReleaseCheckResult",
    "ReleaseContext",
    "ReleaseDiscovery",
    "ReleaseEdit",
    "ReleasePlan",
    "ReleasePolicy",
    "ReleaseResult",
    "ReleaseRule",
    "ReleaseValidationError",
    "StaleReleasePlanError",
    "VersionReference",
    "apply_release",
    "check_release",
    "discover_release",
    "plan_release",
    "published_releases",
]

_CORE_FILES = ("Cargo.toml", "Cargo.lock", "pyproject.toml", "uv.lock", "CITATION.cff", "CHANGELOG.md", "README.md", "REFERENCES.md")


class ReleaseValidationError(ValueError):
    """A complete candidate failed shared, declarative, or adapter validation."""

    def __init__(self, problems: Sequence[str]):
        self.problems = tuple(problems)
        super().__init__("prepared release metadata failed validation: " + "; ".join(self.problems))


class StaleReleasePlanError(ValueError):
    """Selected source files changed after a release plan was prepared."""


@dataclass(frozen=True, slots=True)
class ReleaseContext:
    tag: str
    previous_tag: str | None
    release_date: str

    @property
    def version(self) -> str:
        return self.tag.removeprefix("v")


@dataclass(frozen=True, slots=True)
class ReleaseAdapter:
    """Declare extra existing inputs and optional read-only candidate callbacks.

    ``prepare`` returns repository-relative paths mapped to replacement bytes.
    Targets must already belong to the selected tree. ``validate`` returns
    diagnostic strings; any message rejects the candidate. Exceptions propagate.
    Both callbacks must leave the candidate and source trees unchanged.
    """

    input_files: tuple[str, ...] = ()
    prepare: Callable[[Path, ReleaseContext], Mapping[str, bytes]] | None = None
    validate: Callable[[Path, ReleaseContext], Sequence[str]] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_files", tuple(relative_path(path) for path in self.input_files))


@dataclass(frozen=True, slots=True)
class ReleaseDiscovery:
    root: Path
    package: PackageInfo
    files: tuple[Path, ...]
    references: tuple[VersionReference, ...]
    policy: ReleasePolicy


@dataclass(frozen=True, slots=True)
class ReleaseCheckResult:
    discovery: ReleaseDiscovery
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass(frozen=True, slots=True)
class ReleaseEdit:
    path: Path
    before: bytes
    after: bytes


@dataclass(frozen=True, slots=True)
class ReleasePlan:
    """Immutable validated preview; apply this exact plan without recomputing edits."""

    discovery: ReleaseDiscovery
    context: ReleaseContext
    files: tuple[ReleaseEdit, ...]
    adapter_inputs: tuple[str, ...]

    @property
    def edits(self) -> tuple[ReleaseEdit, ...]:
        return tuple(edit for edit in self.files if edit.before != edit.after)


@dataclass(frozen=True, slots=True)
class ReleaseResult:
    context: ReleaseContext
    changed_paths: tuple[Path, ...]


def published_releases(root: Path, *, repository: str | None = None) -> tuple[PublishedRelease, ...]:
    """Query gh for stable published releases, descending by numeric version.

    Drafts and prereleases are excluded. Command, decoding, and parsing failures
    propagate with the process API's diagnostic and exception contracts.
    """
    return _published_releases(root.resolve()) if repository is None else _published_releases(root.resolve(), repository=repository)


def _safe_file(root: Path, path: Path) -> Path:
    # Inspect every component, including internal symlink parents. Resolving a
    # path alone would hide aliases and allow two names for one publication.
    if not path.is_absolute():
        path = root / path
    if not path.is_relative_to(root):
        raise ValueError(f"release metadata must be repository-contained: {path}")
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"release metadata must not be a symbolic link: {current}")
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError(f"required release file is missing or not a regular file: {path}")
    return path


def _inventory(root: Path, policy: ReleasePolicy, inputs: tuple[str, ...]) -> tuple[Path, ...]:
    paths = {root / name for name in _CORE_FILES if (root / name).exists() or (root / name).is_symlink()}
    paths.update(metadata._iter_markdown_files(root, policy))
    paths.update(root / name for name in (*policy.required_files, *inputs))
    paths.update(root / rule.path for rule in policy.rules)
    # Check core and explicitly selected paths before parsing or remote lookup.
    for path in paths:
        _safe_file(root, path)
    if (root / "Cargo.toml").is_file():
        paths.update(Path(os.path.abspath(path)) for path in metadata.workspace_member_manifests(root))
    for path in paths:
        _safe_file(root, path)
    return tuple(sorted((path.relative_to(root) for path in paths), key=lambda path: path.as_posix()))


def discover_release(root: Path, *, policy: ReleasePolicy | None = None, adapter: ReleaseAdapter | None = None) -> ReleaseDiscovery:
    """Discover the shared and explicitly selected release files without a network call."""
    root = root.resolve()
    policy = load(root=root).release if policy is None else policy
    paths = _inventory(root, policy, adapter.input_files if adapter else ())
    package = metadata.read_package_info(root)
    references = tuple(metadata._version_references(root, package, policy))
    captures: dict[str, list[tuple[int, int]]] = {}
    for rule in policy.rules:
        matches = rule.matches((root / rule.path).read_bytes().decode("utf-8"))
        if rule.source is not None:
            captures.setdefault(rule.path, []).extend(match.span("value") for match in matches)
    for path, spans in captures.items():
        spans.sort()
        if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
            raise ValueError(f"{path}: overlapping release rule captures")
    return ReleaseDiscovery(root, package, paths, references, policy)


def _expected(rule: ReleaseRule, context: ReleaseContext) -> str:
    if rule.value is not None:
        return rule.value
    if rule.source == "previous-tag":
        if context.previous_tag is None:
            raise ValueError(f"{rule.path}: previous-tag rule requires previous release discovery")
        return context.previous_tag
    return {"version": context.version, "tag": context.tag, "release-date": context.release_date}[str(rule.source)]


def _rule_problems(root: Path, policy: ReleasePolicy, context: ReleaseContext, *, fixed_only: bool = False) -> list[str]:
    problems = []
    for rule in policy.rules:
        if fixed_only and rule.value is None:
            continue
        text = (root / rule.path).read_bytes().decode("utf-8")
        expected = _expected(rule, context)
        for match in rule.matches(text):
            if match["value"] != expected:
                line = text.count("\n", 0, match.start("value")) + 1
                problems.append(f"{rule.path}:{line}: found {match['value']!r}, expected {expected!r}")
    return problems


def _previous(root: Path, target: str, previous: str | None, *, required: bool) -> str | None:
    if previous is None and required:
        from research_repo_tools.update_release import infer_previous_release

        previous = infer_previous_release(root, target)
    if previous is not None:
        previous = normalize_tag(previous)
        if _tag_version(previous) >= _tag_version(target):
            raise ValueError(f"previous release {previous} must precede {target}")
    return previous


def _problems(root: Path, policy: ReleasePolicy, context: ReleaseContext, *, preparing: bool) -> tuple[str, ...]:
    mismatches = metadata.find_version_mismatches(root, policy=policy)
    problems = [
        f"{item.reference.path.relative_to(root)}:{item.reference.line}: {item.reference.kind} found {item.reference.version}, expected {item.package.version}"
        for item in mismatches
        if not (
            preparing
            and not policy.final_changelog
            and item.reference.kind == metadata.ReferenceKind.CHANGELOG
            and context.previous_tag is not None
            and item.reference.version == context.previous_tag.removeprefix("v")
        )
    ]
    problems.extend(
        f"{item.reference.path.relative_to(root)}:{item.reference.line}: {item.reference.kind} found {item.reference.value}, expected {item.expected}"
        for item in metadata.find_release_metadata_mismatches(root, policy=policy)
    )
    problems.extend(_rule_problems(root, policy, context))
    return tuple(problems)


def _snapshot(root: Path) -> dict[Path, bytes | None]:
    paths = sorted(root.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("release adapter must not create symbolic links")
    return {path.relative_to(root): path.read_bytes() if path.is_file() else None for path in paths}


def _validate_adapter(root: Path, context: ReleaseContext, adapter: ReleaseAdapter | None) -> tuple[str, ...]:
    if adapter is None or adapter.validate is None:
        return ()
    before = _snapshot(root)
    result = adapter.validate(root, context)
    if isinstance(result, str) or not isinstance(result, Sequence) or any(not isinstance(item, str) for item in result):
        raise ValueError("release adapter validate must return a sequence of diagnostic strings")
    if _snapshot(root) != before:
        raise ValueError("release adapter validate must not mutate the candidate tree")
    return tuple(result)


def _stage(root: Path, destination: Path, files: Mapping[Path, bytes]) -> Path:
    staged = destination / root.name
    staged.mkdir()
    for path, payload in files.items():
        target = staged / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return staged


def check_release(
    root: Path,
    *,
    policy: ReleasePolicy | None = None,
    previous_tag: str | None = None,
    adapter: ReleaseAdapter | None = None,
) -> ReleaseCheckResult:
    """Return all synchronization failures; malformed or missing inputs raise ValueError.

    Adapter validation reads the same isolated selected tree used by preparation.
    Checking never invokes the adapter's prepare callback or publishes files.
    """
    discovery = discover_release(root, policy=policy, adapter=adapter)
    root, policy = discovery.root, discovery.policy
    target = "v" + discovery.package.version.removeprefix("v")
    previous = _previous(root, target, previous_tag, required=any(rule.source == "previous-tag" for rule in policy.rules))
    files = {path: (root / path).read_bytes() for path in discovery.files}
    with tempfile.TemporaryDirectory(prefix="research-release-check-") as directory:
        staged = _stage(root, Path(directory).resolve(), files)
        citation = staged / "CITATION.cff"
        released = metadata._citation_date_reference(citation).value if citation.is_file() else None
        heading = metadata._changelog_date_reference(staged / "CHANGELOG.md", discovery.package.version)
        context = ReleaseContext(target, previous, released or (heading.value if heading else datetime.now(UTC).date().isoformat()))
        problems = _problems(staged, policy, context, preparing=False) + _validate_adapter(staged, context, adapter)
    return ReleaseCheckResult(discovery, problems)


def _edit_rules(root: Path, policy: ReleasePolicy, context: ReleaseContext) -> None:
    edits: dict[str, list[tuple[int, int, str]]] = {}
    for rule in policy.rules:
        if rule.source is None:
            continue
        text = (root / rule.path).read_bytes().decode("utf-8")
        for match in rule.matches(text):
            value = match["value"]
            if rule.source == "version" or (rule.source == "tag" and TAG.fullmatch(value)):
                allowed = {context.version, str(context.previous_tag).removeprefix("v")} if rule.source == "version" else {context.tag, context.previous_tag}
                if value not in allowed:
                    raise ValueError(f"{rule.path}: unexpected active release version {value!r}; expected previous or target release")
            if rule.source == "previous-tag":
                if normalize_tag(value) != value or _tag_version(value) >= _tag_version(context.tag):
                    raise ValueError(f"{rule.path}: previous-tag reference must precede the target: {value!r}")
            if rule.source == "release-date" and date.fromisoformat(value).isoformat() != value:
                raise ValueError(f"{rule.path}: release-date reference must use YYYY-MM-DD form")
            start, end = match.span("value")
            edits.setdefault(rule.path, []).append((start, end, _expected(rule, context)))
    for path, replacements in edits.items():
        replacements.sort()
        if any(left[1] > right[0] for left, right in zip(replacements, replacements[1:])):
            raise ValueError(f"{path}: overlapping release rule captures")
        target = root / path
        text = target.read_bytes().decode("utf-8")
        for start, end, value in reversed(replacements):
            text = text[:start] + value + text[end:]
        target.write_bytes(text.encode("utf-8"))


def plan_release(
    root: Path,
    tag: str,
    *,
    previous_tag: str | None = None,
    release_date: str | None = None,
    policy: ReleasePolicy | None = None,
    adapter: ReleaseAdapter | None = None,
) -> ReleasePlan:
    """Prepare and validate one complete candidate without modifying source files.

    Explicit previous_tag avoids GitHub discovery. Fixed metadata is checked
    before any edits. Callbacks see shared and declarative edits already applied.
    """
    from research_repo_tools.update_release import _prepare_updates

    discovery = discover_release(root, policy=policy, adapter=adapter)
    root, policy = discovery.root, discovery.policy
    normalized = normalize_tag(tag)
    if policy.tag_policy == "canonical-stable" and tag != normalized:
        raise ValueError("release tag must use canonical stable vX.Y.Z form")
    tag = normalized
    released = release_date if release_date is not None else datetime.now(UTC).date().isoformat()
    if date.fromisoformat(released).isoformat() != released:
        raise ValueError("release date must use YYYY-MM-DD form")
    # Fixed assertions fail even when a consumer adapter could otherwise repair them.
    fixed_context = ReleaseContext(tag, previous_tag, released)
    if problems := _rule_problems(root, policy, fixed_context, fixed_only=True):
        raise ReleaseValidationError(problems)
    previous = _previous(root, tag, previous_tag, required=True)
    assert previous is not None
    context = ReleaseContext(tag, previous, released)
    originals = {path: (root / path).read_bytes() for path in discovery.files}
    with tempfile.TemporaryDirectory(prefix="research-release-validation-") as directory:
        staged = _stage(root, Path(directory).resolve(), originals)
        for path, text in _prepare_updates(staged, tag, previous, released, policy=policy).items():
            path.write_bytes(text.encode("utf-8"))
        _edit_rules(staged, policy, context)
        if adapter is not None and adapter.prepare is not None:
            before = _snapshot(staged)
            edits = adapter.prepare(staged, context)
            if _snapshot(staged) != before:
                raise ValueError("release adapter prepare must return edits without mutating the candidate tree")
            if not isinstance(edits, Mapping):
                raise ValueError("release adapter prepare must return a mapping of relative paths to bytes")
            for name, payload in edits.items():
                path = Path(relative_path(name))
                if path not in originals or policy.excludes(path.as_posix()):
                    raise ValueError(f"release adapter edit must select an existing non-excluded input: {name}")
                if not isinstance(payload, bytes):
                    raise ValueError(f"release adapter edit must contain bytes: {name}")
                (staged / path).write_bytes(payload)
        # Re-discover after edits so required files, selectors, and shared metadata
        # cannot be made invalid by an otherwise successful adapter callback.
        discover_release(staged, policy=policy, adapter=adapter)
        problems = _problems(staged, policy, context, preparing=True) + _validate_adapter(staged, context, adapter)
        if problems:
            raise ReleaseValidationError(problems)
        files = tuple(ReleaseEdit(path, original, (staged / path).read_bytes()) for path, original in originals.items())
    return ReleasePlan(discovery, context, files, adapter.input_files if adapter else ())


def apply_release(plan: ReleasePlan) -> ReleaseResult:
    """Publish an unchanged validated plan; reject stale inputs before replacement.

    There is no concurrent-writer lock or crash-atomic multi-file commit. Ordinary
    publication failures use replace_many's rollback and RecoveryError contracts.
    """
    root = plan.discovery.root
    try:
        inventory = _inventory(root, plan.discovery.policy, plan.adapter_inputs)
        changed = [edit.path for edit in plan.files if _safe_file(root, root / edit.path).read_bytes() != edit.before]
    except (OSError, ValueError) as error:
        raise StaleReleasePlanError(f"release inputs are no longer available; prepare a fresh plan: {error}") from error
    if inventory != plan.discovery.files or changed:
        detail = ", ".join(str(path) for path in changed) or "file selection"
        raise StaleReleasePlanError(f"{detail}: source changed; prepare a fresh plan")
    replace_many({root / edit.path: edit.after for edit in plan.edits})
    return ReleaseResult(plan.context, tuple(edit.path for edit in plan.edits))
