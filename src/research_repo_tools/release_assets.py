"""Authenticated, bounded GitHub assets and retry-safe draft publication."""

import gzip
import io
import re
import subprocess
import tarfile
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from research_repo_tools.archives import ArchiveLimits, extract_archive
from research_repo_tools.criterion import SAMPLE_SCHEMA, Sample, parse_sample, serialize_sample
from research_repo_tools.evidence import Evidence, Provenance, _load_json, _object, parse_evidence, serialize_evidence, verify_sha256
from research_repo_tools.files import replace_many
from research_repo_tools.process import resolve_executable, run_command_bytes
from research_repo_tools.release_discovery import normalize_tag

__all__ = ["GitHubRelease", "download_release_asset", "lookup_release", "package_baseline", "publish_release_asset", "read_baseline", "require_draft"]


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


@dataclass(frozen=True, slots=True)
class GitHubRelease:
    repository: str
    tag: str
    identifier: int
    draft: bool
    immutable: bool
    prerelease: bool
    assets: tuple[tuple[str, int, int, str | None], ...]


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
    data = _object(_load_json(_gh(root, ["api", f"repos/{repository}/releases/{identifier}"]), "GitHub release"), "release")
    release_id = data.get("id")
    if data.get("tag_name") != tag or type(release_id) is not int or release_id != identifier:
        raise ValueError("GitHub release has invalid tag or identity")
    for field in ("draft", "immutable", "prerelease"):
        if type(data.get(field)) is not bool:
            raise ValueError(f"GitHub release {field} must be a boolean")
    raw_assets = data.get("assets")
    if not isinstance(raw_assets, list):
        raise ValueError("GitHub release assets must be an array")
    assets = []
    for item in raw_assets:
        asset = _object(item, "release asset")
        name, identifier, size = asset.get("name"), asset.get("id"), asset.get("size")
        if not isinstance(name, str) or type(identifier) is not int or identifier <= 0 or type(size) is not int or size < 0:
            raise ValueError("invalid release asset name, identity or size")
        digest = asset.get("digest")
        if digest is not None and (not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None):
            raise ValueError("unsupported release asset digest")
        assets.append((name, identifier, size, None if digest is None else digest.removeprefix("sha256:")))
    if len({name for name, *_ in assets}) != len(assets) or len({identifier for _, identifier, *_ in assets}) != len(assets):
        raise ValueError("duplicate release asset name or identity")
    return GitHubRelease(repository, tag, release_id, data["draft"] is True, data["immutable"] is True, data["prerelease"] is True, tuple(assets))


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
    if expected_sha256:
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


def publish_release_asset(root: Path, repository: str, tag: str, asset: Path, *, publish: bool = False, limits: ArchiveLimits = ArchiveLimits()) -> None:
    """Attach without overwrite; reuse identical retries and publish last.

    Run in a trusted job with no benchmark checkout. The supplied asset is inert
    bytes. No consumer code/configuration is loaded, no unrelated assets change,
    and failed uploads/checks leave the draft unpublished by this operation.
    """
    name = _asset_name(asset.name)
    if asset.is_symlink() or not asset.is_file():
        raise ValueError("release asset must be a regular file")
    if asset.stat().st_size > limits.archive_bytes:
        raise ValueError("release asset exceeds the archive byte limit")
    original = asset.read_bytes()
    if len(original) > limits.archive_bytes:
        raise ValueError("release asset exceeds the archive byte limit")
    release = lookup_release(root, repository, tag)
    require_draft(release)
    with tempfile.TemporaryDirectory(prefix="research-release-upload-") as directory:
        staged = Path(directory) / name
        staged.write_bytes(original)
        if not any(entry[0] == name for entry in release.assets):
            # No --clobber: a racing upload fails without erasing retained data.
            _gh(root, ["release", "upload", release.tag, str(staged), "--repo", release.repository])
        downloaded = Path(directory) / "verified-asset"
        download_release_asset(root, release.repository, release.tag, name, downloaded, limits=limits, allow_draft=True)
        if downloaded.read_bytes() != original:
            raise ValueError("existing release asset differs; refusing to overwrite or publish")
        current = lookup_release(root, release.repository, release.tag)
        require_draft(current)
        if current.identifier != release.identifier:
            raise ValueError("release identity changed during upload")
        if publish:
            _gh(root, ["api", "--method", "PATCH", f"repos/{release.repository}/releases/{release.identifier}", "--field", "draft=false"])
