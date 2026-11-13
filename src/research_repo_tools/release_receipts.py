"""Retrieve an original tag-push draft receipt through authenticated GitHub reads.

No checkout code, release mutation, signing, acquisition or publication runs here.
Saved selections remain caller-owned assertions and are revalidated on every use.
"""

import base64
import hashlib
import re
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, assert_never

from yaml.nodes import ScalarNode, SequenceNode

from research_repo_tools.evidence import _digest, _object, deterministic_json
from research_repo_tools.release_assets import ReleaseTarget, _resolve_tag_commit, _sha, _stable_tag, parse_release_target
from research_repo_tools.release_drafts import RECEIPT_SCHEMA
from research_repo_tools.release_evidence import _run_identity
from research_repo_tools.release_validation import (
    _artifact_identity,
    _integer,
    _validate_jobs,
    _workflow_identity,
    _workflow_policy,
    download_artifact,
    parse_release_json,
    read_pages,
)
from research_repo_tools.workflow_uses import _document, _mapping

__all__ = ["DiscoverOriginal", "DraftReceipt", "FrozenReceipt", "OriginalRun", "TagPushPolicy", "TagSource", "retrieve_draft_receipt"]

SCHEMA = "research-repo-tools/draft-selection/v1"
FILES = ("draft-receipt.json", "target.json")
LIMIT = 1024 * 1024
RECOVERY = "Retain the original receipt/selection; inspect the original tag-push run and its journal. Never substitute a later attempt or create another draft."


@dataclass(frozen=True, slots=True)
class TagSource:
    """Independent canonical tag-ref and peeled source-commit assertions."""

    ref: str
    commit: str

    def __post_init__(self) -> None:
        if not isinstance(self.ref, str) or not self.ref.startswith("refs/tags/"):
            raise ValueError("draft receipt source requires an explicit refs/tags/vX.Y.Z ref")
        _stable_tag(self.tag)
        _sha(self.commit)

    @property
    def tag(self) -> str:
        return self.ref.removeprefix("refs/tags/")


@dataclass(frozen=True, slots=True)
class TagPushPolicy:
    """Trusted workflow, gates and original-attempt receipt naming convention.

    Only attempt 1 and the exact two-file draft receipt protocol are supported.
    The workflow at the source commit must enable push exclusively for tags.
    """

    repository: str
    workflow: str
    workflow_name: str
    required_steps: tuple[tuple[str, tuple[str, ...]], ...]
    artifact_prefix: str = "draft-receipt"
    runner_labels: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        steps, labels = _workflow_policy(self.repository, self.workflow, self.workflow_name, self.required_steps, self.runner_labels)
        object.__setattr__(self, "required_steps", steps)
        object.__setattr__(self, "runner_labels", labels)
        if not isinstance(self.artifact_prefix, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", self.artifact_prefix) is None:
            raise ValueError("receipt artifact prefix must be a portable name")


@dataclass(frozen=True, slots=True)
class DiscoverOriginal:
    """Require one unambiguous retained push run for the tag, across its history."""


@dataclass(frozen=True, slots=True)
class OriginalRun:
    """Manually reviewed original run ID; all source/workflow/gate checks still apply."""

    run_id: int

    def __post_init__(self) -> None:
        _integer(self.run_id, "original run ID")


def _pinned(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= LIMIT:
        raise ValueError("frozen draft selection must be nonempty bytes within 1 MiB")
    record = _object(
        parse_release_json(payload),
        "draft selection",
        {"schema", "policy", "source", "workflow_source", "run", "jobs", "artifact", "target_sha256", "receipt_sha256"},
    )
    if record["schema"] != SCHEMA:
        raise ValueError("unsupported draft selection schema")
    run = _object(record["run"], "selected run")
    _integer(run.get("id"), "original run ID")
    if type(run.get("run_attempt")) is not int or run["run_attempt"] != 1:
        raise ValueError("draft selection requires original attempt 1")
    _integer(_object(record["artifact"], "selected artifact").get("id"), "original artifact ID")
    _digest(record["target_sha256"], "target SHA-256")
    _digest(record["receipt_sha256"], "receipt SHA-256")
    return record


@dataclass(frozen=True, slots=True)
class FrozenReceipt:
    """Saved selection bytes to revalidate, never authority merely from parsing."""

    payload: bytes

    def __post_init__(self) -> None:
        _pinned(self.payload)


@dataclass(frozen=True, slots=True)
class DraftReceipt:
    """Retrieved files and captured target; constructing this record grants no trust."""

    target: ReleaseTarget
    selection: bytes
    target_bytes: bytes
    receipt_bytes: bytes


def _workflow_source(policy: TagPushPolicy, source: TagSource, read: Callable[[str], Any]) -> dict[str, str]:
    # GitHub's head_branch is a short name shared by branch and tag namespaces.
    # A successful push from a tag-only workflow proves which namespace ran.
    # Read that workflow at the independently asserted commit, never at HEAD/tag.
    data = _object(read(f"contents/{policy.workflow}?ref={source.commit}"), "workflow source")
    if data.get("type") != "file" or data.get("path") != policy.workflow or data.get("encoding") != "base64":
        raise ValueError("workflow source identity or encoding differs")
    size = _integer(data.get("size"), "workflow source size")
    content = data.get("content")
    if size > LIMIT or not isinstance(content, str) or len(content) > 2 * LIMIT:
        raise ValueError("workflow source exceeds the receipt verification bound")
    payload = base64.b64decode(content.replace("\n", ""), validate=True)
    blob = hashlib.sha1(b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload).hexdigest()
    if len(payload) != size or data.get("sha") != blob:
        raise ValueError("workflow source bytes differ from provider blob identity")
    path = Path(policy.workflow)
    document = _document(path, payload.decode("utf-8"))
    event = document.get("on")
    if event is None:
        raise ValueError("receipt workflow must declare a tag-only push trigger")
    push = _mapping(event, path).get("push")
    if push is None:
        raise ValueError("receipt workflow must declare a tag-only push trigger")
    filters = _mapping(push, path)
    tags = set(filters) & {"tags", "tags-ignore"}
    if len(tags) != 1 or set(filters) & {"branches", "branches-ignore"}:
        raise ValueError("receipt workflow push must be tag-only; branch/tag short-name collisions are not trusted")
    patterns = filters[tags.pop()]
    if (
        not isinstance(patterns, SequenceNode)
        or not patterns.value
        or any(not isinstance(item, ScalarNode) or item.tag != "tag:yaml.org,2002:str" or not item.value for item in patterns.value)
    ):
        raise ValueError("receipt workflow requires explicit tag filter patterns")
    return {"blob": blob, "sha256": hashlib.sha256(payload).hexdigest()}


def _current(
    policy: TagPushPolicy, source: TagSource, identifier: int, workflow: dict[str, Any], read: Callable[[str], Any], *, completed: bool = True
) -> dict[str, Any]:
    run = _object(read(f"actions/runs/{identifier}"), "tag-push run")
    repository = _object(run.get("repository"), "run repository")
    head = _object(run.get("head_repository"), "run head repository")
    repo_id = _integer(repository.get("id"), "repository ID")
    if (
        repository.get("full_name") != policy.repository
        or head.get("full_name") != policy.repository
        or _integer(head.get("id"), "head repository ID") != repo_id
    ):
        raise ValueError("tag-push run repository or head repository differs")
    if (
        _integer(run.get("id"), "run ID") != identifier
        or _integer(run.get("workflow_id"), "run workflow ID") != _workflow_identity(policy.workflow, policy.workflow_name, workflow)
        or run.get("path") not in {policy.workflow, f"{policy.workflow}@{source.ref}", f"{policy.workflow}@{source.tag}"}
        or run.get("name") != policy.workflow_name
        or run.get("event") != "push"
        or run.get("head_branch") != source.tag
        or run.get("head_sha") != source.commit
        or run.get("pull_requests") != []
        or type(run.get("run_attempt")) is not int
        or run["run_attempt"] != 1
    ):
        raise ValueError(f"expected the exact source's original tag-push run, attempt 1; {RECOVERY}")
    if completed and (run.get("status") != "completed" or run.get("conclusion") != "success"):
        raise ValueError(f"original tag-push run has not completed successfully; wait for that run before retrying. {RECOVERY}")
    if run.get("status") not in ("queued", "in_progress", "completed"):
        raise ValueError("tag-push producer lifecycle is unavailable")
    return run


def _discover(policy: TagPushPolicy, source: TagSource, workflow: dict[str, Any], read: Callable[[str], Any]) -> int:
    identifier = _workflow_identity(policy.workflow, policy.workflow_name, workflow)
    # Unfiltered history avoids GitHub's 1,000-result cap on filtered listings.
    runs = read_pages(read, f"actions/workflows/{identifier}/runs", "workflow_runs")
    seen = set()
    candidates = []
    for run in runs:
        current = _integer(run.get("id"), "historical run ID")
        if current in seen or _integer(run.get("workflow_id"), "historical workflow ID") != identifier:
            raise ValueError("duplicate or mismatched workflow history identity")
        seen.add(current)
        if not isinstance(run.get("event"), str) or not isinstance(run.get("head_branch"), str):
            raise ValueError("workflow history lacks event/ref identity")
        if run["event"] == "push" and run["head_branch"] == source.tag:
            candidates.append(current)
    if len(candidates) != 1:
        raise ValueError(f"missing or ambiguous original tag-push run; {RECOVERY}")
    return candidates[0]


def _snapshot(policy: TagPushPolicy, source: TagSource, identifier: int, read: Callable[[str], Any], pinned: dict[str, Any] | None) -> dict[str, Any]:
    if _resolve_tag_commit(source.tag, read) != source.commit:
        raise ValueError("remote tag differs from independent source commit")
    workflow = _object(read(f"actions/workflows/{PurePosixPath(policy.workflow).name}"), "workflow")
    run = _current(policy, source, identifier, workflow, read)
    provenance = _workflow_source(policy, source, read)
    jobs = _validate_jobs(
        policy.required_steps, policy.runner_labels, run, read_pages(read, f"actions/runs/{identifier}/attempts/1/jobs", "jobs"), recovery=RECOVERY
    )
    name = f"{policy.artifact_prefix}-{identifier}-1"
    if pinned is None:
        matches = [artifact for artifact in read_pages(read, f"actions/runs/{identifier}/artifacts", "artifacts") if artifact.get("name") == name]
        if len(matches) != 1:
            raise ValueError(f"missing or ambiguous original receipt artifact; {RECOVERY}")
        artifact = _artifact_identity(matches[0], run, name, max_bytes=2 * LIMIT, recovery=RECOVERY)
    else:
        artifact = _object(pinned["artifact"], "frozen artifact")
    current = _artifact_identity(_object(read(f"actions/artifacts/{artifact['id']}"), "receipt artifact"), run, name, max_bytes=2 * LIMIT, recovery=RECOVERY)
    if deterministic_json(artifact) != deterministic_json(current):
        raise ValueError(f"original receipt artifact identity changed; {RECOVERY}")
    return {
        "schema": SCHEMA,
        "policy": asdict(policy),
        "source": asdict(source),
        "workflow_source": provenance,
        "run": _run_identity(run),
        "jobs": jobs,
        "artifact": current,
    }


def _target(target_bytes: bytes, receipt_bytes: bytes, policy: TagPushPolicy, source: TagSource) -> ReleaseTarget:
    target = parse_release_target(target_bytes)
    receipt = _object(
        parse_release_json(receipt_bytes),
        "draft receipt",
        {"schema", "repository", "tag", "commit", "title", "notes_sha256", "selection_sha256", "state", "release_id"},
    )
    _digest(receipt["notes_sha256"], "draft notes SHA-256")
    _digest(receipt["selection_sha256"], "draft evidence SHA-256")
    if (
        receipt["schema"] != RECEIPT_SCHEMA
        or receipt["state"] != "captured"
        or _integer(receipt["release_id"], "captured release ID") != target.release_id
        or (receipt["repository"], receipt["tag"], receipt["commit"]) != (policy.repository, source.tag, source.commit)
        or (target.repository, target.tag, target.commit) != (policy.repository, source.tag, source.commit)
        or target.expected_title is None
        or receipt["title"] != target.expected_title
    ):
        raise ValueError("captured draft receipt and target differ from independent source assertions")
    return target


def retrieve_draft_receipt(
    policy: TagPushPolicy,
    source: TagSource,
    read: Callable[[str], Any],
    *,
    selection: DiscoverOriginal | OriginalRun | FrozenReceipt = DiscoverOriginal(),
) -> DraftReceipt:
    """Authenticate original receipt bytes and return their captured release target.

    ``read`` is repository-scoped authenticated JSON transport (for example api).
    ZIP transport reuses download_artifact's authenticated gh reads and bounds.
    Persist the returned selection outside the checkout. Retry with FrozenReceipt
    to read only the frozen run/artifact IDs; never rediscover after first capture.
    OriginalRun is deliberate manual recovery from an independently reviewed ID.
    Policy, source, required gates and original-attempt checks remain mandatory.
    """
    if not isinstance(policy, TagPushPolicy) or not isinstance(source, TagSource):
        raise TypeError("receipt retrieval requires TagPushPolicy and TagSource")
    if not isinstance(selection, (DiscoverOriginal, OriginalRun, FrozenReceipt)):
        raise TypeError("receipt selection requires DiscoverOriginal, OriginalRun or FrozenReceipt")
    pinned = None
    match selection:
        case DiscoverOriginal():
            workflow = _object(read(f"actions/workflows/{PurePosixPath(policy.workflow).name}"), "workflow")
            identifier = _discover(policy, source, workflow, read)
        case OriginalRun(run_id=identifier):
            pass
        case FrozenReceipt(payload=payload):
            pinned = _pinned(payload)
            if deterministic_json(pinned["policy"]) != deterministic_json(asdict(policy)) or deterministic_json(pinned["source"]) != deterministic_json(
                asdict(source)
            ):
                raise ValueError("frozen receipt policy or source assertions changed")
            identifier = pinned["run"]["id"]
        case _:
            assert_never(selection)
    selected = _snapshot(policy, source, identifier, read, pinned)
    hashes = {"target_sha256", "receipt_sha256"}
    if pinned is not None and deterministic_json({key: value for key, value in pinned.items() if key not in hashes}) != deterministic_json(selected):
        raise ValueError(f"frozen receipt run, jobs or workflow source changed; {RECOVERY}")
    with tempfile.TemporaryDirectory(prefix="research-draft-receipt-") as temporary:
        destination = Path(temporary) / "receipt"
        download_artifact(policy.repository, selected["artifact"], destination, FILES, max_bytes=2 * LIMIT)
        target_bytes = (destination / "target.json").read_bytes()
        receipt_bytes = (destination / "draft-receipt.json").read_bytes()
    if len(target_bytes) > LIMIT or len(receipt_bytes) > LIMIT:
        raise ValueError("draft receipt metadata exceeds 1 MiB per file")
    target = _target(target_bytes, receipt_bytes, policy, source)
    result = {**selected, "target_sha256": hashlib.sha256(target_bytes).hexdigest(), "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest()}
    payload = deterministic_json(result)
    if pinned is not None and payload != deterministic_json(pinned):
        raise ValueError(f"original receipt bytes changed; {RECOVERY}")
    # Same-ID checks after download prevent mid-read attempt/artifact/tag changes.
    if deterministic_json(_snapshot(policy, source, identifier, read, result)) != deterministic_json(selected):
        raise ValueError(f"original receipt provider state changed during retrieval; {RECOVERY}")
    if isinstance(selection, DiscoverOriginal):
        workflow = _object(read(f"actions/workflows/{PurePosixPath(policy.workflow).name}"), "workflow")
        if _discover(policy, source, workflow, read) != identifier:
            raise ValueError(f"original tag-push history changed during retrieval; {RECOVERY}")
    if pinned is None:
        matches = [item for item in read_pages(read, f"actions/runs/{identifier}/artifacts", "artifacts") if item.get("name") == selected["artifact"]["name"]]
        if len(matches) != 1 or _integer(matches[0].get("id"), "receipt artifact ID") != selected["artifact"]["id"]:
            raise ValueError(f"original receipt inventory changed during retrieval; {RECOVERY}")
    return DraftReceipt(target, payload, target_bytes, receipt_bytes)
