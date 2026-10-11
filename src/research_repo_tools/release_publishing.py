"""Shared release gates and human-reviewed GitHub publication, without Cargo policy."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast
from urllib.parse import quote

from research_repo_tools.evidence import _load_json
from research_repo_tools.process import run_git_command, run_safe_command
from research_repo_tools.registry import identity, wait_for_version
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
    revalidate_release_target,
)

if TYPE_CHECKING:
    from research_repo_tools.config import Config

__all__ = [
    "PublishingSettings",
    "RequiredCheck",
    "ReviewedRelease",
    "check_metadata",
    "check_reviewed_release",
    "parse_publishing",
    "publish_reviewed_release",
    "require_checks",
    "validate_event",
    "verify_publication",
]


@dataclass(frozen=True, slots=True)
class RequiredCheck:
    name: str
    app_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or any(ord(char) < 32 for char in self.name):
            raise ValueError("required check name must be a nonempty single line")
        if type(self.app_id) is not int or self.app_id <= 0:
            raise ValueError("required check app-id must be a positive integer")


@dataclass(frozen=True, slots=True)
class PublishingSettings:
    registry: Literal["pypi", "crates-io"]
    package: str
    repository: str
    required_checks: tuple[RequiredCheck, ...]
    required_assets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        identity(self.registry, self.package, "0.0.0")
        _repository(self.repository)
        object.__setattr__(self, "required_checks", tuple(self.required_checks))
        object.__setattr__(self, "required_assets", tuple(self.required_assets))
        if not self.required_checks or any(not isinstance(check, RequiredCheck) for check in self.required_checks):
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


def parse_publishing(data: dict[str, object]) -> PublishingSettings:
    """Parse one explicit registry/repository policy; never select shell commands."""
    if data.keys() - {"registry", "package", "repository", "required-checks", "required-assets"}:
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
    return PublishingSettings(cast(Literal["pypi", "crates-io"], values[0]), cast(str, values[1]), cast(str, values[2]), tuple(parsed), tuple(assets))


def _settings(config: Config) -> PublishingSettings:
    if config.publishing is None:
        raise ValueError("configure [publishing] or [tool.research-repo-tools.publishing] first")
    return config.publishing


def _tag(tag: str) -> str:
    return _stable_tag(tag)[1:]


@dataclass(frozen=True, slots=True)
class ReviewedRelease:
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
    if not checks or any(not isinstance(check, RequiredCheck) for check in checks):
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
    for check in checks:
        matches = [run for run in runs if run.get("name") == check.name and isinstance(run.get("app"), dict) and run["app"].get("id") == check.app_id]
        if len(matches) != 1 or matches[0].get("status") != "completed" or matches[0].get("conclusion") != "success":
            raise ValueError(f"required check {check.name!r} lacks one unambiguous successful result on {commit}")


def _assets(release: GitHubRelease, settings: PublishingSettings, version: str) -> None:
    inventory = {name: size for name, _, size, _ in release.assets}
    for pattern in settings.required_assets:
        name = pattern.replace("{version}", version)
        if inventory.get(name, 0) <= 0:
            raise ValueError(f"required release asset {name!r} is missing or empty")


def check_reviewed_release(config: Config, tag: str, *, event: ReviewedRelease | None = None, draft: bool = False) -> GitHubRelease:
    """Read-only final gate: clean checkout, tag, ancestry, checks, lifecycle/assets.

    Fetch evidence through authenticated gh. No fetch, checkout, ref mutation,
    packaging, or upload occurs here. The caller owns native package verification.
    """
    settings = _settings(config)
    check_metadata(config, tag)
    head = _sha(run_git_command(["--no-pager", "rev-parse", "HEAD"], cwd=config.root).stdout.strip())
    if run_git_command(["--no-pager", "status", "--porcelain", "--untracked-files=all"], cwd=config.root).stdout.strip():
        raise ValueError("publication requires a clean reviewed checkout")
    if event is not None and (event.tag != tag or event.commit != head):
        raise ValueError("release event and checkout tag/commit differ")
    if _remote_commit(config.root, settings.repository, tag) != head:
        raise ValueError("remote release tag and checkout commit differ")
    repo = _api(config.root, settings.repository, "")
    if (
        not isinstance(repo, dict)
        or repo.get("full_name") != settings.repository
        or not isinstance(repo.get("default_branch"), str)
        or not repo["default_branch"]
    ):
        raise ValueError("GitHub repository identity or default branch is unavailable")
    branch = quote(repo["default_branch"], safe="")
    protection = _api(config.root, settings.repository, f"branches/{branch}")
    if not isinstance(protection, dict) or protection.get("name") != repo["default_branch"] or protection.get("protected") is not True:
        raise ValueError("publication requires a protected default branch")
    ancestry = _api(config.root, settings.repository, f"compare/{head}...{branch}")
    if not isinstance(ancestry, dict) or not isinstance(ancestry.get("status"), str) or ancestry["status"] not in {"ahead", "identical"}:
        raise ValueError("release commit is not an ancestor of the protected default branch")
    evidence = _api(config.root, settings.repository, f"commits/{head}/check-runs?filter=latest&per_page=100", paginate=True)
    require_checks(evidence, head, settings.required_checks)
    release = lookup_release(config.root, settings.repository, tag)
    if release.prerelease or release.draft != draft or (event is not None and release.identifier != event.release_id):
        raise ValueError("GitHub release lifecycle or identity differs from the reviewed release")
    _require_identity(release, ReleaseTarget(settings.repository, tag, event.release_id if event is not None else release.identifier, head))
    _assets(release, settings, _tag(tag))
    return release


def publish_reviewed_release(config: Config, tag: str) -> None:
    """Publish a validated draft as the invoking maintainer, triggering its workflow.

    Invocation is approval of the draft. This never uploads registry packages,
    creates tags, replaces assets, or configures a trusted publisher.
    """
    head = _sha(run_git_command(["--no-pager", "rev-parse", "HEAD"], cwd=config.root).stdout.strip())
    release = check_reviewed_release(config, tag, draft=True)
    require_draft(release)
    # Recheck immediately before the one lifecycle mutation, including tag/checks.
    current = check_reviewed_release(config, tag, draft=True)
    require_draft(current)
    if current.identifier != release.identifier:
        raise ValueError("draft release identity changed before publication")
    target = ReleaseTarget(release.repository, tag, release.identifier, head)
    final = revalidate_release_target(config.root, target)
    _assets(final, _settings(config), _tag(tag))
    published = _publish_release(config.root, target)
    try:
        _assets(published, _settings(config), _tag(tag))
    except ValueError as error:
        raise ReleasePublicationUnknownError(
            f"publication outcome unknown for {target.repository} release {target.release_id}: response did not confirm required assets; inspect before retrying"
        ) from error


def verify_publication(config: Config, tag: str, *, attempts: int = 1, interval: int = 10) -> None:
    """Require the published stable GitHub release/assets and exact registry version."""
    settings = _settings(config)
    version = _tag(tag)
    release = lookup_release(config.root, settings.repository, tag)
    if release.draft or release.prerelease:
        raise ValueError("publication verification requires a published stable GitHub release")
    _assets(release, settings, version)
    if not wait_for_version(settings.registry, settings.package, version, attempts=attempts, interval=interval).present:
        raise ValueError("exact registry version is not yet visible; inspect the upload before retrying")
