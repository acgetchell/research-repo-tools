"""Authenticate a hosted acquisition against independent producer and artifact assertions."""

import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from research_repo_tools.archives import ArchiveLimits
from research_repo_tools.candidate_publication import _parse_publication
from research_repo_tools.candidate_reuse import _eligible, acquisition_assertion_digest, parse_candidate_policy
from research_repo_tools.evidence import _digest, _object, deterministic_json, parse_evidence, sha256
from research_repo_tools.release_assets import _asset_name, _repository, _sha
from research_repo_tools.release_validation import _integer, artifact_identity, download_artifact

__all__ = ["AcquisitionProducer", "retrieve_candidate_handoff", "serialize_acquisition_producer"]

_FILES = ("acquisition.sha256", "evidence.json", "policy.json", "producer.json", "publication.json", "publication.sha256", "run.json")
_SCHEMA = "research-repo-tools/acquisition-producer/v1"


@dataclass(frozen=True, slots=True)
class AcquisitionProducer:
    """Independent Actions producer assertion for a single trusted attempt."""

    repository: str
    workflow: str
    event: str
    branch: str
    commit: str
    run_id: int
    attempt: int

    def __post_init__(self) -> None:
        _repository(self.repository)
        _sha(self.commit)
        _integer(self.run_id, "acquisition run ID")
        _integer(self.attempt, "acquisition run attempt")
        if not re.fullmatch(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml", self.workflow):
            raise ValueError("acquisition producer requires a workflow file in .github/workflows")
        if self.event not in {"push", "workflow_dispatch"}:
            raise ValueError("acquisition producer event must be push or workflow_dispatch")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", self.branch) or ".." in self.branch or "//" in self.branch:
            raise ValueError("acquisition producer requires an explicit branch name")


def serialize_acquisition_producer(producer: AcquisitionProducer) -> bytes:
    """Record the producing attempt inside its original immutable artifact."""
    return deterministic_json({"schema": _SCHEMA, **asdict(producer)})


def _actions_producer(environment: Mapping[str, str]) -> AcquisitionProducer:
    repository, branch = environment.get("GITHUB_REPOSITORY", ""), environment.get("GITHUB_REF_NAME", "")
    workflow_ref = environment.get("GITHUB_WORKFLOW_REF", "")
    prefix, suffix = f"{repository}/", f"@refs/heads/{branch}"
    if (
        environment.get("GITHUB_ACTIONS") != "true"
        or environment.get("GITHUB_REF_TYPE") != "branch"
        or not (workflow_ref.startswith(prefix) and workflow_ref.endswith(suffix))
    ):
        raise ValueError("hosted capture requires the original GitHub Actions branch workflow environment")
    return AcquisitionProducer(
        repository,
        workflow_ref.removeprefix(prefix).removesuffix(suffix),
        environment.get("GITHUB_EVENT_NAME", ""),
        branch,
        environment.get("GITHUB_SHA", ""),
        int(environment.get("GITHUB_RUN_ID", "")),
        int(environment.get("GITHUB_RUN_ATTEMPT", "")),
    )


def _observe(
    producer: AcquisitionProducer,
    artifact: int,
    name: str,
    digest: str,
    read: Callable[[str], object],
    limit: int,
) -> tuple[dict[str, Any], int, int]:
    workflow = _object(read(f"actions/workflows/{PurePosixPath(producer.workflow).name}"), "acquisition workflow")
    workflow_id = _integer(workflow.get("id"), "acquisition workflow ID")
    if workflow.get("path") != producer.workflow:
        raise ValueError("acquisition workflow differs from the trusted producer")
    run = _object(read(f"actions/runs/{producer.run_id}/attempts/{producer.attempt}"), "acquisition run attempt")
    expected = {
        "id": producer.run_id,
        "run_attempt": producer.attempt,
        "workflow_id": workflow_id,
        "path": producer.workflow,
        "event": producer.event,
        "head_branch": producer.branch,
        "head_sha": producer.commit,
        "status": "completed",
        "conclusion": "success",
    }
    for key in ("id", "run_attempt", "workflow_id"):
        _integer(run.get(key), f"acquisition run {key}")
    if any(run.get(key) != value for key, value in expected.items()):
        raise ValueError("acquisition run differs from the trusted successful producer attempt")
    repositories = [_object(run.get(field), field) for field in ("repository", "head_repository")]
    identifiers = {_integer(repository.get("id"), "acquisition repository ID") for repository in repositories}
    if len(identifiers) != 1 or any(repository.get("full_name") != producer.repository for repository in repositories):
        raise ValueError("acquisition run did not originate in the trusted repository")
    observed = artifact_identity(_object(read(f"actions/artifacts/{artifact}"), "acquisition artifact"), run, name, max_bytes=limit)
    if observed["id"] != artifact or observed["digest"] != f"sha256:{digest}":
        raise ValueError("acquisition artifact differs from the independently captured ID or digest")
    return observed, workflow_id, next(iter(identifiers))


def retrieve_candidate_handoff(
    producer: AcquisitionProducer,
    read: Callable[[str], object],
    *,
    artifact_id: int,
    artifact_prefix: str,
    artifact_sha256: str,
    limits: ArchiveLimits = ArchiveLimits(),
) -> dict[str, bytes]:
    """Return original hosted bytes after authenticating the original provider attempt.

    Use an authenticated repository-scoped reader and obtain the expected
    producer, artifact ID and digest independently of the artifact itself.
    """
    _integer(artifact_id, "acquisition artifact ID")
    _digest(artifact_sha256, "trusted acquisition artifact digest")
    artifact_name = f"{_asset_name(artifact_prefix)}-{producer.run_id}-{producer.attempt}"
    observed = _observe(producer, artifact_id, artifact_name, artifact_sha256, read, limits.archive_bytes)
    with tempfile.TemporaryDirectory(prefix="research-hosted-acquisition-") as temporary:
        destination = Path(temporary) / "handoff"
        download_artifact(producer.repository, observed[0], destination, _FILES, max_bytes=limits.archive_bytes, max_expanded_bytes=limits.content_bytes)
        files = {name: (destination / name).read_bytes() for name in _FILES}
    if files["producer.json"] != serialize_acquisition_producer(producer):
        raise ValueError("artifact producer manifest differs from the trusted original attempt")
    policy = parse_candidate_policy(files["policy.json"])
    if (policy.repository, policy.acquisition_owner, policy.commit, policy.candidate.revision) != (
        producer.repository,
        "github-actions",
        None,
        producer.commit,
    ):
        raise ValueError("candidate policy does not belong to the original hosted acquisition")
    _eligible(parse_evidence(files["run.json"], files["evidence.json"]), policy)
    _parse_publication(files["publication.json"], policy)
    for name, digest in (("acquisition", acquisition_assertion_digest(policy)), ("publication", sha256(files["publication.json"]))):
        if files[f"{name}.sha256"] != (digest + "\n").encode("ascii"):
            raise ValueError(f"hosted {name} assertions differ from retained bytes")
    if _observe(producer, artifact_id, artifact_name, artifact_sha256, read, limits.archive_bytes) != observed:
        raise ValueError("acquisition provider state changed during retrieval")
    return files
