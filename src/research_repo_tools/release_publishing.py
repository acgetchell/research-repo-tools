"""Shared release gates and human-reviewed GitHub publication, without Cargo policy."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Literal, cast
from urllib.parse import quote, urlsplit

from research_repo_tools.evidence import _load_json
from research_repo_tools.process import run_git_command, run_safe_command
from research_repo_tools.registry import RegistryVersion, identity, wait_for_version
from research_repo_tools.release_assets import (
    GitHubRelease,
    ReleasePublicationUnknownError,
    ReleaseTarget,
    _asset_name,
    _publish_release,
    _remote_commit,
    _repository,
    _require_identity,
    _sha,
    _stable_tag,
    lookup_release,
    require_draft,
    require_release_checkout,
    revalidate_release_target,
)
from research_repo_tools.release_reuse import ReusePolicy, parse_reuse_policy, verify_ci_reuse
lazy from research_repo_tools.config import Config

__all__ = [
    "PublishingSettings",
    "RequiredCheck",
    "ReviewedRelease",
    "check_metadata",
    "check_release_source",
    "check_reviewed_release",
    "parse_publishing",
    "publish_reviewed_release",
    "require_checks",
    "validate_event",
    "verify_publication",
    "verify_publication_visibility",
]


@dataclass(frozen=True, slots=True)
class RequiredCheck:
    """A required GitHub check identified by both its name and reporting app ID."""

    name: str
    app_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or any(ord(char) < 32 for char in self.name):
            raise ValueError("required check name must be a nonempty single line")
        if type(self.app_id) is not int or self.app_id <= 0:
            raise ValueError("required check app-id must be a positive integer")


@dataclass(frozen=True, slots=True)
class PublishingSettings:
    """Consumer requirements for the release repository, registry destination, checks, and assets."""

    registry: Literal["pypi", "crates-io"]
    package: str
    repository: str
    required_checks: tuple[RequiredCheck, ...]
    required_assets: tuple[str, ...] = ()
    reuse: ReusePolicy | None = None
    expected_title: Literal["tag"] | None = None
    generated_notes: bool = False
    require_immutable: bool = False

    def __post_init__(self) -> None:
        identity(self.registry, self.package, "0.0.0")
        _repository(self.repository)
        if self.expected_title not in (None, "tag"):
            raise ValueError("publishing.expected-title must be 'tag'")
        if type(self.generated_notes) is not bool or type(self.require_immutable) is not bool:
            raise ValueError("publishing generated-notes and require-immutable must be booleans")
        object.__setattr__(self, "required_checks", tuple(self.required_checks))
        object.__setattr__(self, "required_assets", tuple(self.required_assets))
        if not self.required_checks or any(not isinstance(check, RequiredCheck) for check in self.required_checks):  # ty: ignore[redundant-condition-strict]  Runtime boundary also protects untyped callers.
            raise ValueError("publishing requires explicit required-checks")
        if len(set(self.required_checks)) != len(self.required_checks):
            raise ValueError("duplicate required checks")
        for asset in self.required_assets:
            # Version placeholders expand only after stable tag parsing.
            if not isinstance(asset, str):
                raise ValueError("required asset must be a filename string")
            _asset_name(asset.replace("{version}", "0.0.0"))
        if len(set(self.required_assets)) != len(self.required_assets):
            raise ValueError("duplicate required assets")
        if self.reuse is not None:
            reused = {RequiredCheck(name, self.reuse.app_id) for name, _ in self.reuse.validation.required_steps}
            if self.reuse.validation.repository != self.repository or not reused < set(self.required_checks):
                raise ValueError("reuse must cover a strict subset of required checks in this repository; retain exact-commit package checks")


def parse_publishing(data: dict[str, object]) -> PublishingSettings:
    """Parse one explicit registry/repository policy; never select shell commands."""
    if data.keys() - {
        "registry",
        "package",
        "repository",
        "required-checks",
        "required-assets",
        "reuse",
        "expected-title",
        "generated-notes",
        "require-immutable",
    }:
        raise ValueError("unknown publishing field")
    values = [data.get(name) for name in ("registry", "package", "repository")]
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("publishing requires registry, package, and repository strings")
    checks = data.get("required-checks")
    if not isinstance(checks, list):
        raise ValueError("publishing.required-checks must be an array of name/app-id tables")
    parsed = []
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"name", "app-id"}:
            raise ValueError("required check requires exactly name and app-id")
        parsed.append(RequiredCheck(check["name"], check["app-id"]))
    assets = data.get("required-assets", [])
    if not isinstance(assets, list) or any(not isinstance(asset, str) for asset in assets):
        raise ValueError("publishing.required-assets must be an array of filenames")
    repository = cast(str, values[2])
    reuse = parse_reuse_policy(cast(dict[str, object], data["reuse"]), repository) if "reuse" in data else None
    return PublishingSettings(
        cast(Literal["pypi", "crates-io"], values[0]),
        cast(str, values[1]),
        repository,
        tuple(parsed),
        tuple(assets),
        reuse,
        cast(Literal["tag"] | None, data.get("expected-title")),
        cast(bool, data.get("generated-notes", False)),
        cast(bool, data.get("require-immutable", False)),
    )


def _settings(config: Config) -> PublishingSettings:
    if config.publishing is None:
        raise ValueError("configure [publishing] or [tool.research-repo-tools.publishing] first")
    return config.publishing


def _tag(tag: str) -> str:
    return _stable_tag(tag)[1:]


@dataclass(frozen=True, slots=True)
class ReviewedRelease:
    """The release ID, tag, and commit selected for publication review."""

    tag: str
    commit: str
    release_id: int

    def __post_init__(self) -> None:
        _tag(self.tag)
        _sha(self.commit)
        if type(self.release_id) is not int or self.release_id <= 0:
            raise ValueError("release ID must be a positive integer")


def validate_event(event: object, repository: str, commit: str, ref: str) -> ReviewedRelease:
    """Require a stable published release event bound to its repository/tag/SHA."""
    _repository(repository)
    _sha(commit)
    if not isinstance(event, dict) or event.get("action") != "published":
        raise ValueError("publication requires a release: published event")
    repo, release = event.get("repository"), event.get("release")
    if not isinstance(repo, dict) or repo.get("full_name") != repository:
        raise ValueError("release event names the wrong repository")
    if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("release event must be published and stable")
    tag = release.get("tag_name")
    _tag(tag)
    identifier = release.get("id")
    if type(identifier) is not int or identifier <= 0 or ref != f"refs/tags/{tag}":
        raise ValueError("release event has an invalid release identity or tag ref")
    return ReviewedRelease(tag, commit, identifier)


def check_metadata(config: Config, tag: str, *, previous_tag: str | None = None) -> None:
    """Reuse final metadata, date, and nonempty notes validation for both registries."""
    from research_repo_tools import changelog, release_metadata

    version = _tag(tag)
    package = release_metadata.read_package_info(config.root)
    if package.version != version:
        raise ValueError(f"tag {tag} does not match package version {package.version!r}")
    if config.publishing is not None and package.name != config.publishing.package:
        raise ValueError("manifest package differs from publishing.package")
    if release_metadata.check(config.root, policy=replace(config.release, final_changelog=True), previous_tag=previous_tag):
        raise ValueError("release metadata validation failed")
    changelog.check(config)
    changelog.notes(config, tag)


def _api(root: Path, repository: str, endpoint: str, *, paginate: bool = False) -> object:
    args = ["api", *(["--paginate", "--slurp"] if paginate else []), f"repos/{repository}" + (f"/{endpoint}" if endpoint else "")]
    payload = run_safe_command("gh", args, cwd=root, timeout=120).stdout
    return _load_json(payload.encode("utf-8"), "GitHub release evidence")


def require_checks(pages: object, commit: str, checks: tuple[RequiredCheck, ...]) -> None:
    """Require one completed success per name/provider on the exact release SHA.

    Input is gh's paginated/slurped REST check-runs response with filter=latest.
    Pending, missing, duplicated, wrong-SHA, or unsuccessful evidence rejects.
    """
    _sha(commit)
    if not checks or any(not isinstance(check, RequiredCheck) for check in checks):  # ty: ignore[redundant-condition-strict]  Runtime boundary also protects untyped callers.
        raise ValueError("required checks must be explicit RequiredCheck values")
    if not isinstance(pages, list) or not pages:
        raise ValueError("missing paginated check evidence")
    runs = []
    totals = set()
    identifiers = set()
    for page in pages:
        if not isinstance(page, dict) or type(page.get("total_count")) is not int or page["total_count"] < 0 or not isinstance(page.get("check_runs"), list):
            raise ValueError("malformed check-run page")
        totals.add(page["total_count"])
        for run in page["check_runs"]:
            if not isinstance(run, dict) or type(run.get("id")) is not int or run["id"] <= 0 or run["id"] in identifiers:
                raise ValueError("invalid or duplicate check-run identity")
            identifiers.add(run["id"])
            if run.get("head_sha") != commit:
                raise ValueError("check evidence names a different release commit")
            app = run.get("app")
            if not isinstance(app, dict) or type(app.get("id")) is not int or app["id"] <= 0:
                raise ValueError("check evidence has an invalid provider identity")
            runs.append(run)
    if totals != {len(runs)}:
        raise ValueError("incomplete or changing check-run pagination")
    unmet = []
    pending = False
    for check in checks:
        matches = [run for run in runs if run.get("name") == check.name and isinstance(run.get("app"), dict) and run["app"].get("id") == check.app_id]
        label = f"required check {check.name!r} (app ID {check.app_id})"
        if not matches:
            same_name = [run for run in runs if run.get("name") == check.name]
            providers = sorted({run["app"]["id"] for run in same_name})
            detail = f"; same name observed from app IDs {providers}; " + "; ".join(_check_observation(run) for run in same_name) if same_name else ""
            unmet.append(f"{label}: missing{detail}")
        elif len(matches) > 1:
            unmet.append(f"{label}: ambiguous ({len(matches)} results); " + "; ".join(_check_observation(run) for run in matches))
        elif matches[0].get("status") != "completed" or matches[0].get("conclusion") != "success":
            running = matches[0].get("status") in ("queued", "in_progress", "waiting", "pending", "requested")
            pending |= running
            unmet.append(f"{label}: {'pending/running' if running else 'unsuccessful'}; {_check_observation(matches[0])}")
    if unmet:
        advice = "\nCI is still running. Wait for the existing run to succeed, then manually retry publication." if pending else ""
        raise ValueError(f"Unmet release checks on exact commit {commit}:\n" + "\n".join(unmet) + advice)


def _check_observation(run: dict[str, object]) -> str:
    detail = f"check ID {run['id']}, status={run.get('status')!r}, conclusion={run.get('conclusion')!r}"
    # Provider URLs are diagnostics, never authority; omit credentials and signed queries.
    for field in ("html_url", "details_url"):
        value = run.get(field)
        if isinstance(value, str):
            try:
                url = urlsplit(value)
                if url.scheme == "https" and url.hostname == "github.com" and not url.username and not url.password:
                    return detail + f", https://github.com{url.path}"
            except ValueError:
                pass
    return detail


def _assets(release: GitHubRelease, settings: PublishingSettings, version: str) -> None:
    inventory = {name: size for name, _, size, _ in release.assets}
    for pattern in settings.required_assets:
        name = pattern.replace("{version}", version)
        if inventory.get(name, 0) <= 0:
            raise ValueError(f"required release asset {name!r} is missing or empty")


def _retained_crate_sha256(release: GitHubRelease, registry: str, package: str, version: str) -> str:
    if registry != "crates-io":
        raise ValueError("retained crate verification requires crates.io publication")
    if not release.immutable:
        raise ValueError("retained crate verification requires an immutable GitHub Release")
    filename = f"{package}-{version}.crate"
    states = dict(release.asset_states)
    for name, identifier, size, digest in release.assets:
        if name == filename and size > 0 and states.get(identifier) == "uploaded" and digest is not None:
            return digest
    raise ValueError(f"immutable GitHub Release requires an uploaded {filename} with a SHA-256 for the retained crate")


def _release_assertions(config: Config, release: GitHubRelease, tag: str, *, draft: bool) -> None:
    from research_repo_tools.changelog import notes

    settings = _settings(config)
    if release.draft is not draft or release.prerelease is not False or type(release.immutable) is not bool:
        raise ValueError("GitHub release lifecycle fields differ or are malformed")
    if draft:
        require_draft(release)
    elif settings.require_immutable and release.immutable is not True:
        raise ValueError("published release must be immutable")
    if settings.expected_title == "tag" and release.title != tag:
        raise ValueError("release title differs from the declared tag title")
    if settings.generated_notes:
        # GitHub may use CRLF and add one terminal LF. Preserve all other
        # whitespace, including extra blank lines and lone carriage returns.
        expected = notes(config, tag)[0].replace("\r\n", "\n").removesuffix("\n")
        if not isinstance(release.body, str) or release.body.replace("\r\n", "\n").removesuffix("\n") != expected:
            raise ValueError("release body differs from generated changelog notes")
    _assets(release, settings, _tag(tag))


def _protected_source(root: Path, repository: str, tag: str, head: str) -> None:
    if _remote_commit(root, repository, tag) != head:
        raise ValueError("remote release tag and checkout commit differ")
    repo = _api(root, repository, "")
    if not isinstance(repo, dict) or repo.get("full_name") != repository or not isinstance(repo.get("default_branch"), str) or not repo["default_branch"]:
        raise ValueError("GitHub repository identity or default branch is unavailable")
    branch = quote(repo["default_branch"], safe="")
    protection = _api(root, repository, f"branches/{branch}")
    if not isinstance(protection, dict) or protection.get("name") != repo["default_branch"] or protection.get("protected") is not True:
        raise ValueError("publication requires a protected default branch")
    ancestry = _api(root, repository, f"compare/{head}...{branch}")
    if not isinstance(ancestry, dict) or not isinstance(ancestry.get("status"), str) or ancestry["status"] not in {"ahead", "identical"}:
        raise ValueError("release commit is not an ancestor of the protected default branch")


def check_release_source(config: Config, tag: str, *, commit: str | None = None, reuse_evidence: bytes | None = None) -> str:
    """Verify clean metadata/source, protected-branch ancestry and exact checks."""
    from research_repo_tools.dependency_provenance import check_release_dependencies

    check_release_dependencies(config.root, packages=config.release.published_dependencies)
    return _check_release_source(config, tag, commit=commit, reuse_evidence=reuse_evidence)


def _check_release_source(config: Config, tag: str, *, commit: str | None, reuse_evidence: bytes | None) -> str:
    """Recheck source and provider assertions within a dependency-verified operation."""
    settings = _settings(config)
    check_metadata(config, tag)
    head = _sha(run_git_command(["--no-pager", "rev-parse", "HEAD"], cwd=config.root).stdout.strip())
    require_release_checkout(config.root, head)
    if commit is not None and commit != head:
        raise ValueError("release event and checkout tag/commit differ")
    _protected_source(config.root, settings.repository, tag, head)
    evidence = _api(config.root, settings.repository, f"commits/{head}/check-runs?filter=latest&per_page=100", paginate=True)
    required = settings.required_checks
    if reuse_evidence is not None:
        if settings.reuse is None:
            raise ValueError("CI reuse requires an explicit publishing.reuse policy")
        verify_ci_reuse(settings.reuse, head, lambda endpoint: _api(config.root, settings.repository, endpoint), expected=reuse_evidence)
        reused = {RequiredCheck(name, settings.reuse.app_id) for name, _ in settings.reuse.validation.required_steps}
        # Never hide an attempted exact-commit check's failure/cancellation.
        observed = (
            {run.get("name") for page in evidence if isinstance(page, dict) for run in page.get("check_runs", []) if isinstance(run, dict)}
            if isinstance(evidence, list)
            else set()
        )
        required = tuple(check for check in required if check not in reused or check.name in observed)
    require_checks(evidence, head, required)
    return head


def check_reviewed_release(
    config: Config,
    tag: str,
    *,
    event: ReviewedRelease | None = None,
    draft: bool = False,
    reuse_evidence: bytes | None = None,
    target: ReleaseTarget | None = None,
    require_crate_asset: bool = False,
) -> GitHubRelease:
    """Read-only final source gate plus captured release lifecycle and assertions."""
    if event is not None and event.tag != tag:
        raise ValueError("release event and checkout tag/commit differ")
    head = check_release_source(config, tag, commit=None if event is None else event.commit, reuse_evidence=reuse_evidence)
    return _check_reviewed_target(config, tag, head, event=event, draft=draft, target=target, require_crate_asset=require_crate_asset)


def _check_reviewed_target(
    config: Config, tag: str, head: str, *, draft: bool, target: ReleaseTarget | None, event: ReviewedRelease | None = None, require_crate_asset: bool = False
) -> GitHubRelease:
    settings = _settings(config)
    if target is not None and (target.repository, target.tag, target.commit) != (settings.repository, tag, head):
        raise ValueError("captured release target differs from configured repository/tag/source")
    release = lookup_release(config.root, settings.repository, tag)
    if release.prerelease or release.draft != draft or (event is not None and release.identifier != event.release_id):
        raise ValueError("GitHub release lifecycle or identity differs from the reviewed release")
    if target is None:
        target = ReleaseTarget(settings.repository, tag, event.release_id if event is not None else release.identifier, head)
    _require_identity(release, target)
    _release_assertions(config, release, tag, draft=draft)
    if require_crate_asset:
        _retained_crate_sha256(release, settings.registry, settings.package, _tag(tag))
    return release


def publish_reviewed_release(
    config: Config,
    tag: str,
    *,
    reuse_evidence: bytes | None = None,
    target: ReleaseTarget | None = None,
    expected_assets: frozenset[tuple[str, int, int, str]] | None = None,
) -> None:
    """Publish a validated draft as the invoking maintainer, triggering its workflow.

    Invocation is approval of the draft. This never uploads registry packages,
    creates tags, replaces assets, or configures a trusted publisher.
    The maintainer must serialize every writer for this repository and tag
    until the publication response is confirmed. GitHub's draft transition has
    no asset inventory precondition. Inspect an uncertain result before retry.
    """
    if expected_assets is not None and target is None:
        raise ValueError("reviewed asset publication requires its original draft target")

    def check_inventory(observed: GitHubRelease) -> None:
        if expected_assets is not None and (
            set(observed.assets) != expected_assets or any(dict(observed.asset_states).get(identifier) != "uploaded" for _, identifier, _, _ in expected_assets)
        ):
            raise ValueError("release assets differ from the reviewed inventory")

    print("Running initial checks on release metadata, CI results, and the GitHub draft.", flush=True)
    head = _sha(run_git_command(["--no-pager", "rev-parse", "HEAD"], cwd=config.root).stdout.strip())
    release = check_reviewed_release(config, tag, draft=True, reuse_evidence=reuse_evidence, target=target)
    require_draft(release)
    check_inventory(release)
    # Recheck tag and CI here. The final target observation runs the fresh
    # dependency check immediately before the one lifecycle mutation.
    print("Running final checks before publishing the GitHub release.", flush=True)
    current_head = _check_release_source(config, tag, commit=head, reuse_evidence=reuse_evidence)
    current = _check_reviewed_target(config, tag, current_head, draft=True, target=target)
    require_draft(current)
    check_inventory(current)
    if current.identifier != release.identifier:
        raise ValueError("draft release identity changed before publication")
    if target is None:
        target = ReleaseTarget(release.repository, tag, release.identifier, head)
    final = revalidate_release_target(config.root, target, dependencies=config.release.published_dependencies)
    _release_assertions(config, final, tag, draft=True)
    check_inventory(final)
    published = _publish_release(config.root, target)
    try:
        _release_assertions(config, published, tag, draft=False)
        check_inventory(published)
    except ValueError as error:
        raise ReleasePublicationUnknownError(
            f"publication outcome unknown for {target.repository} release {target.release_id}: response did not confirm declared release assertions; inspect before retrying"
        ) from error


def verify_publication(
    config: Config, tag: str, *, attempts: int = 1, interval: int = 10, target: ReleaseTarget | None = None, require_crate_asset: bool = False
) -> None:
    """Check publication, optionally binding the registry checksum to an immutable crate asset."""
    from research_repo_tools.dependency_provenance import check_release_dependencies

    check_release_dependencies(config.root, packages=config.release.published_dependencies)
    _verify_configured_publication(config, tag, attempts=attempts, interval=interval, target=target, require_crate_asset=require_crate_asset)


def _verify_configured_publication(config: Config, tag: str, *, attempts: int, interval: int, target: ReleaseTarget | None, require_crate_asset: bool) -> None:
    """Observe GitHub and registry publication using the configured assertions."""
    settings = _settings(config)
    _verify_publication(
        config.root,
        settings.repository,
        tag,
        partial(_release_assertions, config, tag=tag, draft=False),
        registry=settings.registry,
        package=settings.package,
        attempts=attempts,
        interval=interval,
        target=target,
        require_crate_asset=require_crate_asset,
    )


def verify_publication_visibility(
    root: Path,
    repository: str,
    tag: str,
    *,
    registry: Literal["pypi", "crates-io"],
    package: str,
    required_assets: tuple[str, ...] = (),
    attempts: int = 1,
    interval: int = 10,
    target: ReleaseTarget | None = None,
) -> RegistryVersion:
    """Read remote publication state using explicit identities and no checkout."""
    required_assets = tuple(_asset_name(name) for name in required_assets)
    if len(set(required_assets)) != len(required_assets):
        raise ValueError("duplicate required release asset names")

    def check(release: GitHubRelease) -> None:
        if release.draft is not False or release.prerelease is not False or release.immutable is not True:
            raise ValueError("visibility requires a published, stable, immutable GitHub release")
        states = dict(release.asset_states)
        completed = {name for name, identifier, size, _ in release.assets if size > 0 and states.get(identifier) == "uploaded"}
        for name in required_assets:
            if name not in completed:
                raise ValueError(f"required release asset {name!r} is missing, empty or incomplete")

    return _verify_publication(root, repository, tag, check, registry=registry, package=package, attempts=attempts, interval=interval, target=target)


def _verify_publication(
    root: Path,
    repository: str,
    tag: str,
    check: Callable[[GitHubRelease], None],
    *,
    registry: Literal["pypi", "crates-io"],
    package: str,
    attempts: int,
    interval: int,
    target: ReleaseTarget | None,
    require_crate_asset: bool = False,
) -> RegistryVersion:
    """Poll for the exact registry version and recheck the release, comparing crates.io's SHA-256 with the retained archive."""
    _repository(repository)
    version = _tag(tag)
    identity(registry, package, version)
    if target is not None and (target.repository != repository or target.tag != tag):
        raise ValueError("publication target differs from configured repository/tag")

    def observe() -> tuple[GitHubRelease, str | None]:
        release = lookup_release(root, repository, tag)
        if target is not None:
            _require_identity(release, target)
            if _remote_commit(root, target.repository, tag) != target.commit:
                raise ValueError("published tag differs from captured source")
        check(release)
        checksum = _retained_crate_sha256(release, registry, package, version) if require_crate_asset else None
        return release, checksum

    release, retained_checksum = observe()
    result = wait_for_version(registry, package, version, attempts=attempts, interval=interval)
    if not result.present:
        raise ValueError("exact registry version is not yet visible; inspect the upload before retrying")
    current, current_checksum = observe()
    if current.identifier != release.identifier:
        raise ValueError("published release identity changed during registry verification")
    if current_checksum != retained_checksum:
        raise ValueError("retained crate SHA-256 changed during registry verification")
    if retained_checksum is not None and result.checksum != retained_checksum:
        raise ValueError(f"crates.io SHA-256 for {package} {version} is {result.checksum}, expected {retained_checksum} from the retained .crate")
    return result
