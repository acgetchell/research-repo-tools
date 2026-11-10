"""Select a published Python release that demonstrably contains a development pin."""

import hashlib
import json
import os
import platform
import tempfile
import tomllib
import urllib.error
from dataclasses import replace
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from research_repo_tools import release_validation, toml_source
from research_repo_tools._git_checkout import _in_checkout
from research_repo_tools.dependency_graphs import inspect_project
from research_repo_tools.dependency_policy import POLICY_FILE, parse_policy
from research_repo_tools.dependency_provenance import INDEX, _locked, _published_wheel, _source
from research_repo_tools.dependency_sources import GitCommit, HttpSourceProvider, RegistryRelease, SourceProvider, VerifiedSource, table, verify_source
from research_repo_tools.dependency_workflows import SourcePlan, _plan_verified_source
from research_repo_tools.evidence import _load_json
from research_repo_tools.process import resolve_executable
from research_repo_tools.release_assets import _download

_MAX_METADATA_BYTES = 1024 * 1024
_MAX_CANDIDATES = 20


class NoSupplyingRelease(ValueError):
    """No eligible release could be proved to supply the exact development pin."""


def _lookup(provider: SourceProvider, url: str) -> dict[str, object]:
    payload = provider.read(url)
    if len(payload) > _MAX_METADATA_BYTES:
        raise ValueError("promotion provider metadata exceeds the byte limit")
    return table(_load_json(payload, "promotion metadata"), "promotion metadata")


def _missing(error: ValueError) -> bool:
    return isinstance(error.__cause__, urllib.error.HTTPError) and error.__cause__.code == 404


def _candidates(provider: SourceProvider, pin: GitCommit, minimum: Version, versions: SpecifierSet, prereleases: bool) -> tuple[Version, ...]:
    data = _lookup(provider, f"https://pypi.org/pypi/{pin.package}/json")
    info = table(data.get("info"), "PyPI project")
    if not isinstance(info.get("name"), str) or canonicalize_name(str(info["name"])) != pin.package:
        raise ValueError("promotion provider returned a different PyPI project")
    candidates = []
    python = Version(platform.python_version())
    for raw, uploads in table(data.get("releases"), "PyPI releases").items():
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        if str(version) != raw or version.local is not None or version < minimum or not versions.contains(version, prereleases=prereleases):
            continue
        if not isinstance(uploads, list) or not uploads:
            continue
        records = tuple(table(upload, "PyPI upload") for upload in uploads)
        if all(upload.get("yanked") is False for upload in records) and any(
            python in SpecifierSet(str(upload.get("requires_python") or "")) for upload in records
        ):
            candidates.append(version)
    if len(candidates) > _MAX_CANDIDATES:
        raise ValueError("promotion has too many candidate releases; narrow the accepted version range")
    return tuple(sorted(candidates, reverse=True))


def _release_commit(
    checkout: Path, pin: GitCommit, version: Version, provider: SourceProvider, *, repository_id: int
) -> tuple[str, bytes, dict[str, object]] | None:
    tag = f"v{version}"
    repository = pin.repository
    try:
        release = _lookup(provider, f"https://api.github.com/repos/{repository}/releases/tags/{tag}")
    except ValueError as error:
        if _missing(error):
            return None
        raise
    if release.get("tag_name") != tag or release.get("draft") is not False or release.get("prerelease") is not version.is_prerelease:
        raise ValueError("promotion release identity differs from the selected version")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("promotion release has an invalid asset inventory")
    attachments = [asset for item in assets if (asset := table(item, "release asset")).get("name") == release_validation.BUNDLE]
    if not attachments:
        return None
    if len(attachments) != 1:
        raise ValueError("promotion release has ambiguous provenance assets")
    attachment = attachments[0]
    identifier, size = attachment.get("id"), attachment.get("size")
    if type(identifier) is not int or identifier <= 0 or type(size) is not int or not 0 < size <= _MAX_METADATA_BYTES:
        raise ValueError("promotion release has an invalid provenance asset identity")
    with tempfile.TemporaryDirectory(prefix="python-promotion-proof-") as directory:
        cwd = Path(directory)
        env = _external_environment(checkout)
        _external_gh(checkout, cwd, env)
        payload = _download(cwd, repository, identifier, _MAX_METADATA_BYTES, environment=env)
    if len(payload) != size:
        raise ValueError("promotion provenance bytes differ from the release asset inventory")
    digest = attachment.get("digest")
    if digest != f"sha256:{hashlib.sha256(payload).hexdigest()}":
        raise ValueError("promotion provenance digest differs from the release asset inventory")
    envelope = table(_load_json(payload, "release provenance"), "release provenance")
    predicate_type = f"https://github.com/{repository}/attestations/release-promotion/v1"
    if envelope.get("schema") != predicate_type:
        raise ValueError("promotion provenance uses an unsupported evidence schema")
    document = table(envelope.get("evidence"), "signed release evidence")
    commit, bundle = document.get("commit"), envelope.get("promotion_bundle")
    if (
        not isinstance(commit, str)
        or not isinstance(bundle, str)
        or document.get("tag") != tag
        or type(document.get("promotion_run")) is not int
        or type(document.get("promotion_attempt")) is not int
    ):
        raise ValueError("promotion provenance differs from the candidate release identity")
    # The signed predicate retains the original validation manifest. Bind its
    # repository ID to the current GitHub repository before trusting a name.
    manifest = document.get("manifest")
    if not isinstance(manifest, str):
        raise ValueError("promotion evidence requires the retained validation manifest")
    origin = table(_load_json(manifest.encode(), "retained validation manifest"), "retained validation manifest")
    if origin.get("repository") != repository or origin.get("repository_id") != repository_id:
        raise ValueError("promotion evidence identifies a different GitHub repository")
    GitCommit("python", pin.package, repository, commit, pin.directory)
    return commit, bundle.encode(), document


def _external_environment(checkout: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key.upper() not in {"GH_HOST", "PATH"}}
    env["PATH"] = os.pathsep.join(
        entry for entry in os.get_exec_path() if (path := Path(entry)).is_absolute() and not path.is_relative_to(checkout) and not _in_checkout(path, checkout)
    )
    return env


def _external_gh(checkout: Path, cwd: Path, env: dict[str, str]) -> Path:
    gh = resolve_executable("gh", cwd=cwd, env=env)
    if gh.is_relative_to(checkout) or _in_checkout(gh, checkout):
        raise ValueError("promotion requires a GitHub CLI executable outside the observed checkout")
    return gh


def _contains(provider: SourceProvider, pin: GitCommit, commit: str) -> bool:
    if commit == pin.commit:
        return True
    result = _lookup(provider, f"https://api.github.com/repos/{pin.repository}/compare/{pin.commit}...{commit}?per_page=1")
    status = result.get("status")
    if status not in {"ahead", "behind", "diverged"} or table(result.get("base_commit"), "base commit").get("sha") != pin.commit:
        raise ValueError("GitHub returned an inconclusive commit comparison")
    ancestor = table(result.get("merge_base_commit"), "merge base").get("sha")
    behind = result.get("behind_by")
    if type(behind) is not int or (status == "ahead") != (behind == 0 and ancestor == pin.commit):
        raise ValueError("GitHub returned inconsistent commit containment evidence")
    return status == "ahead"


def _verify_artifacts(checkout: Path, pin: GitCommit, proof: VerifiedSource, record: tuple[str, bytes, dict[str, object]], provider: SourceProvider) -> None:
    commit, bundle, document = record
    tag = f"v{proof.version}"
    with tempfile.TemporaryDirectory(prefix="python-promotion-artifacts-") as directory:
        cwd = Path(directory)
        env = _external_environment(checkout)
        gh = _external_gh(checkout, cwd, env)
        signed = cwd / "promotion.sigstore.json"
        signed.write_bytes(bundle)
        token = release_validation._GH_EXECUTABLE.set(str(gh))
        try:
            for artifact in proof.artifacts:
                payload = provider.read(artifact.url)
                if len(payload) != artifact.size or hashlib.sha256(payload).hexdigest() != artifact.sha256:
                    raise ValueError("published artifact differs from verified PyPI metadata")
                path = cwd / artifact.filename
                path.write_bytes(payload)
                release_validation.verify_attestation(
                    path,
                    signed,
                    commit,
                    ".github/workflows/prepare-release.yml",
                    f"refs/tags/{tag}",
                    release_validation._integer(document.get("promotion_run"), "promotion run ID"),
                    release_validation._integer(document.get("promotion_attempt"), "promotion attempt"),
                    repository=pin.repository,
                    predicate=document,
                    predicate_type=f"https://github.com/{pin.repository}/attestations/release-promotion/v1",
                )
        finally:
            release_validation._GH_EXECUTABLE.reset(token)


def plan_promotion(checkout: Path, name: str, *, versions: str = "", prereleases: bool = False, provider: SourceProvider | None = None) -> SourcePlan:
    """Resolve a private candidate, or retain the pin when no release supplies it."""
    checkout = checkout.resolve(strict=True)
    policy_file = checkout / POLICY_FILE
    if policy_file.is_symlink() or not policy_file.is_file():
        raise ValueError("promotion requires a regular dependency-sources.toml")
    original = policy_file.read_bytes()
    policy = parse_policy(tomllib.loads(original.decode()))
    matches = [selection for selection in policy.selections if selection.name == name]
    if len(matches) != 1 or not isinstance(pin := matches[0].source, GitCommit) or pin.ecosystem != "python":
        raise ValueError("promotion requires a named exact Python Git selection in dependency-sources.toml")
    selection = matches[0]
    graph = inspect_project(checkout, selection.project, release=False)
    locked = [dependency for dependency in graph.dependencies if dependency.source.package == pin.package]
    if len(locked) != 1 or locked[0].source != pin:
        raise ValueError("promotion source must match the exact locked development pin")
    document = tomllib.loads((graph.root / "pyproject.toml").read_text(encoding="utf-8"))
    override = table(_source(document, pin.package), "development source")
    expected = {"git": pin.url, "rev": pin.commit}
    if pin.directory != ".":
        expected["subdirectory"] = pin.directory
    if override != expected and override != (expected | {"git": pin.url.removesuffix(".git")}):
        raise ValueError("promotion requires the exact development source in the project manifest, lock and policy")
    provider = provider or HttpSourceProvider()
    repository = _lookup(provider, f"https://api.github.com/repos/{pin.repository}")
    repository_id = repository.get("id")
    if repository.get("full_name") != pin.repository or type(repository_id) is not int or repository_id <= 0:
        raise ValueError("promotion requires an exact GitHub repository identity")
    reasons = []
    for candidate in _candidates(provider, pin, Version(locked[0].version), SpecifierSet(versions), prereleases):
        record = _release_commit(checkout, pin, candidate, provider, repository_id=repository_id)
        if record is None:
            reasons.append(f"{candidate} has no supported release provenance")
            continue
        if not _contains(provider, pin, record[0]):
            reasons.append(f"{candidate} does not contain the pin")
            continue
        released = RegistryRelease("python", pin.package, str(candidate))
        verified = verify_source(released, provider=provider, release=True)
        if verify_source(replace(pin, commit=record[0]), provider=provider).version != released.version:
            raise ValueError("attested source metadata differs from the published version")
        _verify_artifacts(checkout, pin, verified, record, provider)
        scope = f"sources.{json.dumps(name)}"
        text = original.decode()
        for key in ("github", "commit", "directory"):
            raw = tomllib.loads(text)["sources"][name]
            if key in raw:
                text = toml_source.remove_value(text, scope, key)
        text = toml_source.set_value(text, scope, "version", json.dumps(released.version))
        graph.snapshot.verify()
        return _plan_verified_source(checkout, replace(selection, source=released), verified, transition_policy=(original, text.encode()))
    graph.snapshot.verify()
    raise NoSupplyingRelease(", ".join(reasons) if reasons else "no non-yanked release satisfies the version, prerelease and Python requirements")


def check_promoted_lock(plan: SourcePlan, root: Path) -> None:
    """Verify the final updater resolution still uses the attested PyPI bytes."""
    from research_repo_tools.dependency_provenance import _artifacts

    name, version = plan.selection.source.package, plan.verified.version
    locked = _locked(root, name, version)
    if locked.get("source") != {"registry": INDEX}:
        raise ValueError("promoted package did not resolve to PyPI")
    expected = {(item.url, item.sha256, item.size) for item in plan.verified.artifacts}
    if any((item["url"], item["hash"][7:], item["size"]) not in expected for item in _artifacts(locked)):
        raise ValueError("updated lock contains artifacts outside the verified promotion")


def check_promoted_installation(plan: SourcePlan, root: Path) -> None:
    """Verify the normal environment without starting its interpreter."""
    from research_repo_tools._dependency_probe import inspect_installation

    name, version = plan.selection.source.package, plan.verified.version
    environment = root / ".venv"
    sites = [environment / "Lib/site-packages"] if os.name == "nt" else list((environment / "lib").glob("python*/site-packages"))
    if len(sites) != 1 or not sites[0].is_dir():
        raise ValueError("promoted package requires an unambiguous project environment")
    installed = inspect_installation(name, paths=tuple(sites))
    if installed.get("version") != version or installed.get("direct_url") is not None:
        raise ValueError("promoted installation differs from the verified registry identity")
    _published_wheel(name, version, _locked(root, name, version), (installed,))
