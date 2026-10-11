"""Authenticated, bounded GitHub assets and retry-safe draft publication."""

import gzip
import hashlib
import io
import re
import subprocess
import tarfile
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import quote

from research_repo_tools.archives import ArchiveLimits, extract_archive
from research_repo_tools.criterion import SAMPLE_SCHEMA, Sample, parse_sample, serialize_sample
from research_repo_tools.evidence import (
    Evidence,
    Provenance,
    _digest,
    _load_json,
    _object,
    deterministic_json,
    parse_evidence,
    serialize_evidence,
    verify_sha256,
)
from research_repo_tools.files import replace_many
from research_repo_tools.process import resolve_executable, run_command_bytes
from research_repo_tools.release_discovery import normalize_tag

__all__ = [
    "GitHubRelease",
    "ReleasePublicationUnknownError",
    "ReleaseTarget",
    "download_release_asset",
    "lookup_release",
    "package_baseline",
    "parse_release_target",
    "preflight_release_target",
    "publish_release_asset",
    "read_baseline",
    "require_draft",
    "revalidate_release_target",
    "serialize_release_target",
]

TARGET_SCHEMA = "research-repo-tools/release-target/v1"


def _repository(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) is None or any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("GitHub repository must be owner/name")
    return value


def _asset_name(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is None:
        raise ValueError("release asset name must be a plain filename of letters, digits, dots, hyphens and underscores")
    return value


def _gh(root: Path, args: list[str]) -> bytes:
    return run_command_bytes("gh", args, cwd=root, timeout=600).stdout


def _stable_tag(value: object) -> str:
    if not isinstance(value, str) or normalize_tag(value) != value:
        raise ValueError("publication requires a canonical stable vX.Y.Z tag")
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("release commit must be a full lowercase GitHub commit SHA")
    return value


def _expected_title(value: object) -> str | None:
    if value is not None and (
        not isinstance(value, str) or not value.strip() or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        raise ValueError("expected release title must be a nonempty single line")
    return value


@dataclass(frozen=True, slots=True)
class ReleaseTarget:
    """Validated identity for a trusted handoff; parsing does not authenticate it."""

    repository: str
    tag: str
    release_id: int
    commit: str
    expected_title: str | None = None

    def __post_init__(self) -> None:
        _repository(self.repository)
        _stable_tag(self.tag)
        _sha(self.commit)
        if type(self.release_id) is not int or self.release_id <= 0:
            raise ValueError("release ID must be a positive integer")
        _expected_title(self.expected_title)


def serialize_release_target(target: ReleaseTarget) -> bytes:
    """Return versioned UTF-8/LF JSON suitable for a consumer's trusted handoff."""
    if not isinstance(target, ReleaseTarget):
        raise TypeError("release target must be a ReleaseTarget")
    return deterministic_json(
        {
            "schema": TARGET_SCHEMA,
            "repository": target.repository,
            "tag": target.tag,
            "release_id": target.release_id,
            "commit": target.commit,
            "expected_title": target.expected_title,
        }
    )


def parse_release_target(payload: bytes) -> ReleaseTarget:
    """Reject ambiguous JSON and unknown fields, without claiming authenticity."""
    data = _object(_load_json(payload, "release target"), "release target", {"schema", "repository", "tag", "release_id", "commit", "expected_title"})
    if data["schema"] != TARGET_SCHEMA:
        raise ValueError("unsupported release target schema")
    return ReleaseTarget(
        cast(str, data["repository"]),
        cast(str, data["tag"]),
        cast(int, data["release_id"]),
        cast(str, data["commit"]),
        cast(str | None, data["expected_title"]),
    )


def _remote_commit(root: Path, repository: str, tag: str) -> str:
    """Resolve only refs/tags, with bounded annotated-tag peeling and no checkout."""
    data = _object(_load_json(_gh(root, ["api", f"repos/{repository}/git/ref/tags/{quote(tag, safe='')}"]), "GitHub tag"), "tag ref")
    if data.get("ref") != f"refs/tags/{tag}":
        raise ValueError("remote release tag is missing or differs")
    obj = data.get("object")
    seen: set[str] = set()
    for _ in range(5):
        if not isinstance(obj, dict):
            break
        sha = _sha(obj.get("sha"))
        if sha in seen:
            break
        seen.add(sha)
        if obj.get("type") == "commit":
            return sha
        if obj.get("type") != "tag":
            break
        data = _object(_load_json(_gh(root, ["api", f"repos/{repository}/git/tags/{sha}"]), "GitHub annotated tag"), "annotated tag")
        if data.get("sha") != sha:
            break
        obj = data.get("object")
    raise ValueError("remote tag does not resolve to one bounded commit identity")


@dataclass(frozen=True, slots=True)
class GitHubRelease:
    repository: str
    tag: str
    identifier: int
    draft: bool
    immutable: bool
    prerelease: bool
    assets: tuple[tuple[str, int, int, str | None], ...]
    title: str | None = None
    asset_states: tuple[tuple[int, str], ...] = ()


def lookup_release(root: Path, repository: str, tag: str) -> GitHubRelease:
    """Use authenticated gh API lookup; malformed/missing state fails closed."""
    repository, tag = _repository(repository), normalize_tag(tag)
    # The REST tag endpoint finds published releases only. gh's release lookup
    # also resolves pending draft tags before we fetch the authoritative ID.
    identity = _object(
        _load_json(_gh(root, ["release", "view", tag, "--repo", repository, "--json", "databaseId,tagName"]), "GitHub release identity"), "release identity"
    )
    identifier = identity.get("databaseId")
    if identity.get("tagName") != tag or type(identifier) is not int or identifier <= 0:
        raise ValueError("GitHub release lookup has invalid tag or identity")
    return _release_by_id(root, repository, tag, identifier)


def _parse_release(data: dict[str, object], repository: str, tag: str, expected_identifier: int) -> GitHubRelease:
    release_id = data.get("id")
    url = data.get("url")
    if (
        data.get("tag_name") != tag
        or type(release_id) is not int
        or release_id != expected_identifier
        or not isinstance(url, str)
        or url.casefold() != f"https://api.github.com/repos/{repository}/releases/{expected_identifier}".casefold()
    ):
        raise ValueError("GitHub release has invalid tag or identity")
    if "name" not in data or (data["name"] is not None and not isinstance(data["name"], str)):
        raise ValueError("GitHub release title must be a string or null")
    for field in ("draft", "immutable", "prerelease"):
        if type(data.get(field)) is not bool:
            raise ValueError(f"GitHub release {field} must be a boolean")
    raw_assets = data.get("assets")
    if not isinstance(raw_assets, list):
        raise ValueError("GitHub release assets must be an array")
    assets = []
    states = []
    for item in raw_assets:
        asset = _object(item, "release asset")
        name, identifier, size = asset.get("name"), asset.get("id"), asset.get("size")
        if not isinstance(name, str) or type(identifier) is not int or identifier <= 0 or type(size) is not int or size < 0:
            raise ValueError("invalid release asset name, identity or size")
        digest = asset.get("digest")
        if digest is not None and (not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None):
            raise ValueError("unsupported release asset digest")
        assets.append((name, identifier, size, None if digest is None else digest.removeprefix("sha256:")))
        state = asset.get("state")
        if not isinstance(state, str) or state not in {"uploaded", "starter"}:
            raise ValueError("invalid release asset upload state")
        states.append((identifier, state))
    if len({name for name, *_ in assets}) != len(assets) or len({identifier for _, identifier, *_ in assets}) != len(assets):
        raise ValueError("duplicate release asset name or identity")
    return GitHubRelease(
        repository,
        tag,
        release_id,
        data["draft"] is True,
        data["immutable"] is True,
        data["prerelease"] is True,
        tuple(assets),
        data["name"],
        tuple(states),
    )


def _release_by_id(root: Path, repository: str, tag: str, identifier: int) -> GitHubRelease:
    endpoint = f"repos/{repository}/releases/{identifier}"
    data = _object(_load_json(_gh(root, ["api", endpoint]), "GitHub release"), "release")
    # Validate the ID response before requesting its complete paginated inventory.
    _parse_release(data, repository, tag, identifier)
    pages = _load_json(_gh(root, ["api", "--paginate", "--slurp", f"{endpoint}/assets?per_page=100"]), "GitHub asset pages")
    if not isinstance(pages, list) or not pages or any(not isinstance(page, list) for page in pages):
        raise ValueError("GitHub assets require paginated arrays")
    data["assets"] = [asset for page in pages for asset in page]
    return _parse_release(data, repository, tag, identifier)


def _require_identity(release: GitHubRelease, target: ReleaseTarget) -> None:
    if (release.repository, release.tag, release.identifier) != (target.repository, target.tag, target.release_id):
        raise ValueError("release identity differs from captured target")
    if target.expected_title is not None and release.title != target.expected_title:
        raise ValueError("release title differs from captured assertion")


def preflight_release_target(root: Path, repository: str, tag: str, commit: str, *, expected_title: str | None = None) -> ReleaseTarget:
    """Read-only preflight for a mutable stable draft on the expected tag commit."""
    # Validate caller assertions before any API access.
    repository, tag, commit = _repository(repository), _stable_tag(tag), _sha(commit)
    expected_title = _expected_title(expected_title)
    pages = _load_json(_gh(root, ["api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100"]), "GitHub release pages")
    if not isinstance(pages, list) or not pages or any(not isinstance(page, list) for page in pages):
        raise ValueError("GitHub releases require paginated arrays")
    releases = [_object(item, "release") for page in pages for item in page]
    matches = [item for item in releases if item.get("tag_name") == tag]
    if len(matches) != 1:
        raise ValueError("preflight requires exactly one release for the stable tag")
    identifier = matches[0].get("id")
    target = ReleaseTarget(repository, tag, cast(int, identifier), commit, expected_title)
    release = _parse_release(matches[0], repository, tag, target.release_id)
    _require_identity(release, target)
    require_draft(release)
    revalidate_release_target(root, target)
    return target


def revalidate_release_target(root: Path, target: ReleaseTarget) -> GitHubRelease:
    """Read captured ID directly; require identity, title, lifecycle and tag SHA."""
    if not isinstance(target, ReleaseTarget):
        raise TypeError("release target must be a ReleaseTarget")
    release = _release_by_id(root, target.repository, target.tag, target.release_id)
    _require_identity(release, target)
    require_draft(release)
    if _remote_commit(root, target.repository, target.tag) != target.commit:
        raise ValueError("remote release tag commit differs from captured target")
    return release


class ReleasePublicationUnknownError(RuntimeError):
    """A publication request was attempted but its outcome cannot be confirmed."""


def _publish_release(root: Path, target: ReleaseTarget) -> GitHubRelease:
    """Call only after final prerequisites; never retry or claim rollback."""
    try:
        payload = _gh(root, ["api", "--method", "PATCH", f"repos/{target.repository}/releases/{target.release_id}", "--field", "draft=false"])
        release = _parse_release(_object(_load_json(payload, "GitHub publication response"), "release"), target.repository, target.tag, target.release_id)
        _require_identity(release, target)
        # Immutable releases may lock as part of successful publication.
        if release.draft or release.prerelease:
            raise ValueError("publication response must identify a published stable release")
        return release
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ReleasePublicationUnknownError(
            f"publication outcome unknown for {target.repository} release {target.release_id}: inspect its state before retrying; no rollback was attempted"
        ) from error


def require_draft(release: GitHubRelease) -> None:
    """Require a mutable stable draft, including immediately before publishing."""
    if release.draft is not True or release.immutable is not False or release.prerelease is not False:
        raise ValueError(f"release {release.tag} must be a mutable stable draft")


def _download(root: Path, repository: str, identifier: int, limit: int, *, timeout: float = 600) -> bytes:
    """Bound gh's byte output and deadline without decoding asset bytes."""
    command = [
        str(resolve_executable("gh", cwd=root)),
        "api",
        f"repos/{repository}/releases/assets/{identifier}",
        "--header",
        "Accept: application/octet-stream",
    ]
    expired = threading.Event()
    # stderr stays outside the captured payload, so partial failure output can
    # never be accepted as an asset. Disk-backed stderr avoids pipe deadlocks.
    with tempfile.TemporaryFile("w+b") as errors:
        with subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors) as child:

            def expire() -> None:
                expired.set()
                child.kill()

            timer = threading.Timer(timeout, expire)
            timer.start()
            try:
                assert child.stdout is not None
                output = child.stdout.read(limit + 1)
                if len(output) > limit:
                    child.kill()
                    raise ValueError("release asset exceeds the archive byte limit")
                status = child.wait()
                if expired.is_set():
                    raise subprocess.TimeoutExpired(command, timeout)
                if status:
                    errors.seek(0)
                    raise subprocess.CalledProcessError(status, command, stderr=errors.read(8192))
                return output
            finally:
                timer.cancel()
                timer.join()
                if child.poll() is None:
                    child.kill()


def download_release_asset(
    root: Path,
    repository: str,
    tag: str,
    name: str,
    destination: Path,
    *,
    limits: ArchiveLimits = ArchiveLimits(),
    expected_sha256: str | None = None,
    allow_draft: bool = False,
) -> None:
    """Download one exact authenticated asset ID with size and optional hash checks.

    Historical assets need no invented trusted digest. In that case authenticity
    relies on GitHub HTTPS, gh authentication and repository write access; a
    provider digest verifies transport consistency, not independent authorship.
    """
    _asset_name(name)
    if expected_sha256 is not None:
        _digest(expected_sha256, "expected digest")
    release = lookup_release(root, repository, tag)
    if release.prerelease or (release.draft and not allow_draft):
        raise ValueError("benchmark retrieval requires a published stable release")
    asset = next((item for item in release.assets if item[0] == name), None)
    if asset is None:
        raise ValueError(f"release {release.tag} has no asset named {name}")
    _, identifier, size, digest = asset
    if size > limits.archive_bytes:
        raise ValueError("release asset exceeds the archive byte limit")
    payload = _download(root, release.repository, identifier, limits.archive_bytes)
    if len(payload) != size:
        raise ValueError("downloaded release asset size differs from its metadata")
    if digest:
        verify_sha256(payload, digest)
    if expected_sha256 is not None:
        verify_sha256(payload, expected_sha256)
    replace_many({destination: payload})


def package_baseline(sample: Sample, provenance: Provenance, destination: Path) -> None:
    """Package a complete shared sample/envelope as deterministic tar.gz bytes."""
    if not sample.estimates:
        raise ValueError("baseline requires at least one estimate")
    payload, manifest = serialize_evidence(Evidence(serialize_sample(sample), SAMPLE_SCHEMA, (("sample", provenance),)))
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0, filename="") as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, content in (("sample.json", payload), ("provenance.json", manifest)):
                member = tarfile.TarInfo(name)
                member.size, member.mode, member.mtime = len(content), 0o644, 0
                archive.addfile(member, io.BytesIO(content))
    replace_many({destination: output.getvalue()})


def read_baseline(
    archive: Path, *, limits: ArchiveLimits = ArchiveLimits(), legacy_configuration: bytes | None = None, expected_tag: str | None = None
) -> tuple[Sample, Provenance]:
    """Extract bounded shared assets and verify original bytes before parsing."""
    with tempfile.TemporaryDirectory(prefix="research-baseline-") as directory:
        root = Path(directory) / "baseline"
        extract_archive(archive, root, limits=limits)
        if legacy_configuration is not None and not (root / "sample.json").exists() and not (root / "provenance.json").exists():
            from research_repo_tools.legacy_evidence import read_legacy_baseline

            if expected_tag is None:
                raise ValueError("legacy baseline reading requires an expected release tag")
            return read_legacy_baseline(archive, legacy_configuration, expected_tag, limits=limits)
        if {path.name for path in root.iterdir()} != {"sample.json", "provenance.json"}:
            raise ValueError("baseline archive requires exactly sample.json and provenance.json")
        retained = parse_evidence((root / "sample.json").read_bytes(), (root / "provenance.json").read_bytes())
        if retained.payload_schema != SAMPLE_SCHEMA or tuple(name for name, _ in retained.sources) != ("sample",):
            raise ValueError("unsupported baseline evidence schema or source inventory")
        sample = parse_sample(retained.payload)
        if not sample.estimates:
            raise ValueError("baseline asset has no estimates")
        source = retained.sources[0][1]
        if expected_tag is not None and dict(source.context).get("release") != normalize_tag(expected_tag):
            raise ValueError("baseline asset does not identify the expected release")
        return sample, source


def _verified_asset(release: GitHubRelease, name: str, original: bytes) -> tuple[str, int, int, str | None]:
    asset = next((entry for entry in release.assets if entry[0] == name), None)
    if asset is None:
        raise ValueError("uploaded release asset is missing")
    _, identifier, size, digest = asset
    if dict(release.asset_states).get(identifier) != "uploaded":
        raise ValueError("release asset upload is incomplete; inspect it before retrying")
    if digest is None:
        raise ValueError("release asset requires provider SHA-256 digest metadata")
    if size != len(original) or digest != hashlib.sha256(original).hexdigest():
        raise ValueError("existing release asset differs; refusing to overwrite or publish")
    return asset


def publish_release_asset(
    root: Path,
    repository: str,
    tag: str,
    asset: Path,
    *,
    target: ReleaseTarget | None = None,
    publish: bool = False,
    limits: ArchiveLimits = ArchiveLimits(),
) -> None:
    """Attach inert bytes to the captured ID; verify metadata/bytes, publish last.

    Existing callers capture a fresh target within this invocation. Trusted jobs
    spanning a long build must supply the preflight target. No consumer code is
    executed. Prerequisite failures never publish; publication-response failures
    raise ReleasePublicationUnknownError and require inspection before retrying.
    """
    repository, tag = _repository(repository), normalize_tag(tag)
    if type(publish) is not bool:
        raise ValueError("publish must be an explicit boolean")
    if target is not None:
        if not isinstance(target, ReleaseTarget):
            raise TypeError("release target must be a ReleaseTarget")
        if (repository, tag) != (target.repository, target.tag):
            raise ValueError("repository/tag differ from captured release target")
    name = _asset_name(asset.name)
    if asset.is_symlink() or not asset.is_file():
        raise ValueError("release asset must be a regular file")
    if asset.stat().st_size > limits.archive_bytes:
        raise ValueError("release asset exceeds the archive byte limit")
    original = asset.read_bytes()
    if not original or len(original) > limits.archive_bytes:
        raise ValueError("release asset must be nonempty and within the archive byte limit")
    if target is None:
        target = preflight_release_target(root, repository, tag, _remote_commit(root, repository, tag))
    with tempfile.TemporaryDirectory(prefix="research-release-upload-") as directory:
        staged = Path(directory) / name
        staged.write_bytes(original)
        release = revalidate_release_target(root, target)
        existing = any(entry[0] == name for entry in release.assets)
        if existing:
            _verified_asset(release, name, original)
        else:
            # Address the captured ID; a racing duplicate fails without overwrite.
            _gh(
                root,
                [
                    "api",
                    "--method",
                    "POST",
                    f"https://uploads.github.com/repos/{target.repository}/releases/{target.release_id}/assets?name={quote(name, safe='')}",
                    "--header",
                    "Content-Type: application/octet-stream",
                    "--input",
                    str(staged),
                ],
            )
        current = revalidate_release_target(root, target)
        attached = _verified_asset(current, name, original)
        downloaded = _download(root, target.repository, attached[1], limits.archive_bytes)
        if downloaded != original:
            raise ValueError("downloaded release asset differs; refusing to publish")
        # Recheck after the potentially slow byte verification, immediately before
        # publication. Require the same completed asset ID as the verified bytes.
        final = revalidate_release_target(root, target)
        if _verified_asset(final, name, original) != attached:
            raise ValueError("release asset identity changed after byte verification")
        if publish:
            published = _publish_release(root, target)
            try:
                if _verified_asset(published, name, original) != attached:
                    raise ValueError("publication response changed the verified asset identity")
            except ValueError as error:
                raise ReleasePublicationUnknownError(
                    f"publication outcome unknown for {target.repository} release {target.release_id}: response did not confirm the verified asset; inspect before retrying"
                ) from error
