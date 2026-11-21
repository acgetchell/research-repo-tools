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

from packaging.ranges import VersionRange
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from research_repo_tools import python_resolution, release_validation, toml_source
from research_repo_tools._git_checkout import _in_checkout
from research_repo_tools.dependency_graphs import DependencyGraph, inspect_project
from research_repo_tools.dependency_policy import POLICY_FILE, SourceSelection, observe_policy
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


def _candidates(
    provider: SourceProvider,
    package: str,
    minimum: Version,
    versions: SpecifierSet,
    prereleases: bool,
    *,
    newer: bool = False,
    python_support: tuple[VersionRange, ...] = (),
) -> tuple[Version, ...]:
    data = _lookup(provider, f"https://pypi.org/pypi/{package}/json")
    info = table(data.get("info"), "PyPI project")
    if not isinstance(info.get("name"), str) or canonicalize_name(str(info["name"])) != package:
        raise ValueError("promotion provider returned a different PyPI project")
    candidates = []
    python = Version(platform.python_version())
    for raw, uploads in table(data.get("releases"), "PyPI releases").items():
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        if (
            str(version) != raw
            or version.local is not None
            or (version <= minimum if newer else version < minimum)
            or not versions.contains(version, prereleases=prereleases)
        ):
            continue
        if not isinstance(uploads, list) or not uploads:
            continue
        records = tuple(table(upload, "PyPI upload") for upload in uploads)
        if any(upload.get("yanked") is not False for upload in records):
            continue
        requirements = tuple(SpecifierSet(str(upload.get("requires_python") or "")) for upload in records)
        if not any(python in requirement for requirement in requirements):
            continue
        if python_support:
            coverage = VersionRange.empty()
            for requirement in requirements:
                coverage = coverage.union(requirement.to_range())
            if not all(required.is_subset(coverage) for required in python_support):
                continue
        candidates.append(version)
    if not newer and len(candidates) > _MAX_CANDIDATES:
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


def _selection(checkout: Path, name: str) -> tuple[SourceSelection, bytes, DependencyGraph]:
    policy, observed = observe_policy(checkout)
    matches = [] if policy is None else [selection for selection in policy.selections if selection.name == name]
    if len(matches) != 1 or matches[0].source.ecosystem != "python":
        raise ValueError("supplier updates require a named exact Python selection in dependency-sources.toml")
    selection = matches[0]
    graph = inspect_project(checkout, selection.project, release=False)
    original = observed.files[0][1]
    if original is None or original != dict(graph.snapshot.files).get(checkout / POLICY_FILE):
        raise ValueError("dependency inputs changed during source lookup; prepare a new plan")
    return selection, original, graph


def _plan_release(
    checkout: Path, selection: SourceSelection, original: bytes, graph: DependencyGraph, released: RegistryRelease, verified: VerifiedSource
) -> SourcePlan:
    scope = f"sources.{json.dumps(selection.name)}"
    text = original.decode()
    for key in ("github", "commit", "directory"):
        if key in tomllib.loads(text)["sources"][selection.name]:
            text = toml_source.remove_value(text, scope, key)
    text = toml_source.set_value(text, scope, "version", json.dumps(released.version))
    return _plan_verified_source(checkout, replace(selection, source=released), verified, observed=graph.snapshot, transition_policy=text.encode())


def _declared_python_support(document: dict[str, object], text: str, package: str) -> tuple[VersionRange, ...]:
    from research_repo_tools.dependencies import _python_constraints

    project = table(document.get("project", {}), "project")
    build = table(document.get("build-system", {}), "build-system")
    base = _python_constraints(project)
    optional = table(project.get("optional-dependencies", {}), "project.optional-dependencies")

    def declared(entries: object, support: SpecifierSet) -> tuple[VersionRange, ...]:
        if not isinstance(entries, list):
            return ()
        scope = support.to_range()
        return tuple(
            scope & python_resolution.marker_python_support(requirement.marker, minor_prereleases=bool(support.prereleases))
            for raw in entries
            if isinstance(raw, str) and canonicalize_name((requirement := Requirement(raw)).name) == package
        )

    project_ranges = tuple(
        required for entries in (project.get("dependencies", []), *optional.values(), build.get("requires", [])) for required in declared(entries, base)
    )
    project_support = base.to_range()
    if project_support in project_ranges:
        return (project_support,)
    groups = table(document.get("dependency-groups", {}), "dependency-groups")
    constraints = python_resolution.group_constraints(text)
    return (
        *project_ranges,
        *(required for group, entries in groups.items() for required in declared(entries, base & SpecifierSet(constraints.get(group, "")))),
    )


def _captured_manifest(graph: DependencyGraph) -> str:
    path = graph.root / "pyproject.toml"
    payload = dict(graph.snapshot.files).get(path)
    if payload is None:
        raise ValueError(f"supplier selection requires a captured Python manifest: {path}")
    return payload.decode("utf-8")


def _workspace_python_support(graph: DependencyGraph, document: dict[str, object], text: str, package: str) -> tuple[VersionRange, ...]:
    required = list(_declared_python_support(document, text, package))
    captured = dict(graph.snapshot.files)
    for path in graph.manifests:
        if path == graph.root / "pyproject.toml":
            continue
        payload = captured.get(path)
        if payload is None:
            raise ValueError(f"supplier selection requires a captured Python manifest: {path}")
        member = payload.decode("utf-8")
        required.extend(_declared_python_support(tomllib.loads(member), member, package))
    return tuple(required)


def plan_supplier(checkout: Path, name: str, *, project: Path, provider: SourceProvider | None = None) -> SourcePlan:
    """Resolve the newest compatible RRT release, requiring signed proof for a Git pin."""
    from research_repo_tools.dependency_provenance import _requirements
    from research_repo_tools.python_baseline import PACKAGE

    checkout = checkout.resolve(strict=True)
    selection, original, graph = _selection(checkout, name)
    pin = selection.source
    if pin.package != PACKAGE or graph.root != project:
        raise ValueError("automatic supplier adoption must select research-repo-tools in the main Python project")
    manifest = _captured_manifest(graph)
    document = tomllib.loads(manifest)
    requirement = _requirements(document, PACKAGE)[0]
    declared = Version(next(iter(requirement.specifier)).version)
    if [(item.source, Version(item.version)) for item in graph.dependencies if item.source.package == PACKAGE] != [(pin, declared)]:
        raise ValueError("supplier pin must agree across the manifest, lock and source policy")
    if isinstance(pin, GitCommit):
        if pin.directory != ".":
            raise ValueError("the shared tooling source must identify the repository root")
        return _plan_promotion(checkout, selection, original, graph, versions="", prereleases=False, provider=provider)

    if _source(document, PACKAGE) is not None:
        raise ValueError("a supplier registry pin cannot have a source override")
    python_support = _workspace_python_support(graph, document, manifest, PACKAGE)
    provider = provider or HttpSourceProvider()
    candidates = _candidates(provider, PACKAGE, Version(pin.version), SpecifierSet(), False, newer=True, python_support=python_support)
    if not candidates:
        graph.snapshot.verify()
        raise NoSupplyingRelease(f"no newer compatible non-yanked PyPI release after {pin.version}")
    released = RegistryRelease("python", PACKAGE, str(candidates[0]))
    verified = verify_source(released, provider=provider, release=True)
    return _plan_release(checkout, selection, original, graph, released, verified)


def plan_promotion(checkout: Path, name: str, *, versions: str = "", prereleases: bool = False, provider: SourceProvider | None = None) -> SourcePlan:
    """Resolve a private candidate, or retain the pin when no release supplies it."""
    checkout = checkout.resolve(strict=True)
    selection, original, graph = _selection(checkout, name)
    return _plan_promotion(checkout, selection, original, graph, versions=versions, prereleases=prereleases, provider=provider)


def _plan_promotion(
    checkout: Path,
    selection: SourceSelection,
    original: bytes,
    graph: DependencyGraph,
    *,
    versions: str,
    prereleases: bool,
    provider: SourceProvider | None,
) -> SourcePlan:
    pin = selection.source
    if not isinstance(pin, GitCommit):
        raise ValueError("promotion requires a named exact Python Git selection in dependency-sources.toml")
    locked = [dependency for dependency in graph.dependencies if dependency.source.package == pin.package]
    if len(locked) != 1 or locked[0].source != pin:
        raise ValueError("promotion source must match the exact locked development pin")
    manifest = _captured_manifest(graph)
    document = tomllib.loads(manifest)
    override = table(_source(document, pin.package), "development source")
    expected = {"git": pin.url, "rev": pin.commit}
    if pin.directory != ".":
        expected["subdirectory"] = pin.directory
    if override != expected and override != (expected | {"git": pin.url.removesuffix(".git")}):
        raise ValueError("promotion requires the exact development source in the project manifest, lock and policy")
    python_support = _workspace_python_support(graph, document, manifest, pin.package)
    provider = provider or HttpSourceProvider()
    repository = _lookup(provider, f"https://api.github.com/repos/{pin.repository}")
    repository_id = repository.get("id")
    if repository.get("full_name") != pin.repository or type(repository_id) is not int or repository_id <= 0:
        raise ValueError("promotion requires an exact GitHub repository identity")
    reasons = []
    for candidate in _candidates(provider, pin.package, Version(locked[0].version), SpecifierSet(versions), prereleases, python_support=python_support):
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
        return _plan_release(checkout, selection, original, graph, released, verified)
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
