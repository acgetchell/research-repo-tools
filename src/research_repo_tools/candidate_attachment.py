"""Attach an independently accepted benchmark archive to one captured draft."""

import tempfile
from pathlib import Path

from research_repo_tools.archives import ArchiveLimits
from research_repo_tools.candidate_reuse import CandidatePolicy, _acceptance, verify_candidate_archive
from research_repo_tools.evidence import _digest, _load_json, _object, _string, deterministic_json, sha256
from research_repo_tools.release_assets import (
    GitHubRelease,
    ReleaseTarget,
    _asset_name,
    _download,
    _publish_release_asset,
    parse_release_target,
    revalidate_release_target,
    serialize_release_target,
)
from research_repo_tools.release_validation import _integer

__all__ = ["attach_candidate_archive", "verify_candidate_attachment"]

_SCHEMA = "research-repo-tools/candidate-attachment/v1"


def _bound_archive(policy: CandidatePolicy, acceptance: bytes, target: ReleaseTarget) -> str:
    if (target.repository, target.tag, target.commit) != (policy.repository, policy.tag, policy.commit):
        raise ValueError("draft target differs from accepted final release")
    return _digest(_acceptance(acceptance, policy)["archive_sha256"], "accepted archive")


def attach_candidate_archive(
    root: Path,
    archive: Path,
    policy: CandidatePolicy,
    acceptance: bytes,
    target: ReleaseTarget,
    *,
    approve: bool,
    checkout: Path | None = None,
    limits: ArchiveLimits = ArchiveLimits(),
    dependencies: tuple[str, ...] = (),
) -> bytes:
    """Attach exact approved bytes and return an identity receipt for later gates."""
    if approve is not True:
        raise ValueError("candidate attachment requires explicit approval")
    digest = _bound_archive(policy, acceptance, target)
    verify_candidate_archive(archive, policy, acceptance, target=target, limits=limits)
    name, identifier, size, observed_digest = _publish_release_asset(
        root,
        target.repository,
        target.tag,
        archive,
        target=target,
        checkout=checkout,
        publish=False,
        limits=limits,
        dependencies=dependencies,
        expected_sha256=digest,
    )
    if observed_digest != digest:
        raise ValueError("provider asset differs from accepted archive")
    return deterministic_json(
        {
            "schema": _SCHEMA,
            "target": _load_json(serialize_release_target(target), "release target"),
            "acceptance_sha256": sha256(acceptance),
            "asset": {"name": name, "id": identifier, "size": size, "sha256": digest},
        }
    )


def verify_candidate_attachment(
    root: Path,
    policy: CandidatePolicy,
    acceptance: bytes,
    target: ReleaseTarget,
    expected: bytes,
    *,
    checkout: Path | None = None,
    limits: ArchiveLimits = ArchiveLimits(),
    dependencies: tuple[str, ...] = (),
) -> None:
    """Revalidate the same draft, acceptance, asset ID, digest and downloaded bytes."""
    digest = _bound_archive(policy, acceptance, target)
    record = _object(_load_json(expected, "candidate attachment"), "candidate attachment", {"schema", "target", "acceptance_sha256", "asset"})
    asset = _object(record["asset"], "attached asset", {"name", "id", "size", "sha256"})
    name = _asset_name(_string(asset["name"], "asset name"))
    identifier = _integer(asset["id"], "asset ID")
    size = _integer(asset["size"], "asset size")
    if (
        record["schema"] != _SCHEMA
        or deterministic_json(record) != expected
        or parse_release_target(deterministic_json(record["target"])) != target
        or record["acceptance_sha256"] != sha256(acceptance)
        or asset["sha256"] != digest
        or not 0 < size <= limits.archive_bytes
    ):
        raise ValueError("attachment receipt differs from the approved target or archive")

    def confirm(release: GitHubRelease) -> None:
        if (name, identifier, size, digest) not in release.assets or dict(release.asset_states).get(identifier) != "uploaded":
            raise ValueError("attached asset identity changed since verification")

    confirm(revalidate_release_target(root, target, checkout=checkout, dependencies=dependencies))
    data = _download(root, target.repository, identifier, limits.archive_bytes)
    if len(data) != size or sha256(data) != digest:
        raise ValueError("downloaded attachment differs from the approved archive")
    with tempfile.TemporaryDirectory(prefix="research-candidate-attachment-") as temporary:
        archive = Path(temporary) / name
        archive.write_bytes(data)
        verify_candidate_archive(archive, policy, acceptance, target=target, limits=limits)
    confirm(revalidate_release_target(root, target, checkout=checkout, dependencies=dependencies))
