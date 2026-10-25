"""Opt-in signed source-check reuse for equivalent PR merge and squash trees.

Trust starts with a reviewed workflow digest and its offline execution contract.
Signatures authenticate observations; they cannot make an arbitrary workflow
context-independent. This contract never establishes release archive identity.
"""

import base64
import binascii
import hashlib
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from research_repo_tools.evidence import deterministic_json
from research_repo_tools.process import run_git_command
from research_repo_tools.release_assets import require_release_checkout
from research_repo_tools.release_validation import (
    ValidationPolicy,
    artifact_identity,
    download_artifact,
    parse_release_json,
    read_pages,
    validate_jobs,
    verify_attestation,
)

__all__ = ["ReusePolicy", "create_reuse_manifest", "parse_reuse_policy", "verify_ci_reuse"]

SCHEMA = "research-repo-tools/ci-reuse/v1"
SELECTION = "research-repo-tools/ci-reuse-selection/v1"
MANIFEST = "reuse.json"
BUNDLE = "reuse.sigstore.json"
CONTRACT_FIELDS = {"commands", "environment", "inputs", "tools"}
MAX_DOCUMENT = 1024 * 1024


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("expected a CI reuse object")
    return value


def _positive(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("expected a positive CI reuse identity")
    return value


def _digest(value: Any, length: int = 64) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value) is None:
        raise ValueError(f"expected a lowercase {length}-digit CI reuse digest")
    return value


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing CI reuse timestamp")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("CI reuse timestamps require a timezone")
    return result.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ReusePolicy:
    """Reviewed, content-pinned offline source-check policy; never inferred.

    Each job requires four SHA-256 identities for commands, environment, inputs,
    and tools. The reviewed workflow must enforce these identities and attest
    that Git metadata, event context and network inputs were unavailable to the
    checks. runner_labels must cover every job, including all three native OSes.
    """

    validation: ValidationPolicy
    workflow_sha256: str
    contracts: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    signing_job: tuple[str, tuple[str, ...]]
    artifact_prefix: str = "ci-reuse"
    max_age_seconds: int = 86400
    app_id: int = 15368

    def __post_init__(self) -> None:
        if not isinstance(self.validation, ValidationPolicy):
            raise ValueError("reuse requires a ValidationPolicy")
        _digest(self.workflow_sha256)
        _positive(self.app_id)
        if type(self.max_age_seconds) is not int or not 1 <= self.max_age_seconds <= 604800:
            raise ValueError("reuse freshness must be between one second and seven days")
        if not isinstance(self.artifact_prefix, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", self.artifact_prefix) is None:
            raise ValueError("reuse artifact prefix must be a portable name")
        contracts = tuple((name, tuple(sorted(values))) for name, values in self.contracts)
        object.__setattr__(self, "contracts", tuple(sorted(contracts)))
        names = set(dict(self.validation.required_steps))
        if len(contracts) != len(names) or set(dict(contracts)) != names:
            raise ValueError("reuse contracts must cover each required job exactly once")
        for _, values in contracts:
            if len(values) != len(CONTRACT_FIELDS) or set(dict(values)) != CONTRACT_FIELDS:
                raise ValueError("each reuse contract requires commands, environment, inputs and tools digests")
            for _, value in values:
                _digest(value)
        labels = dict(self.validation.runner_labels)
        if set(labels) != names or any("self-hosted" in value for value in labels.values()):
            raise ValueError("every reused job requires hosted runner labels")
        if any(not any(any(label.startswith(prefix) for label in value) for value in labels.values()) for prefix in ("ubuntu-", "macos-", "windows-")):
            raise ValueError("reuse requires Linux, macOS and Windows native jobs")
        name, steps = self.signing_job
        object.__setattr__(self, "signing_job", (name, tuple(steps)))
        replace(self.validation, required_steps=(self.signing_job,), runner_labels=())
        if name in names:
            raise ValueError("reuse signing must be separate from native validation")


def parse_reuse_policy(data: dict[str, Any], repository: str) -> ReusePolicy:
    """Parse the optional publishing.reuse table; policy alone enables nothing."""
    fields = {"workflow", "workflow-name", "workflow-sha256", "branch", "jobs", "signing-job", "artifact-prefix", "max-age-seconds", "app-id"}
    if not isinstance(data, dict) or data.keys() - fields or not {"workflow", "workflow-name", "workflow-sha256", "jobs", "signing-job"} <= data.keys():
        raise ValueError("unknown or missing CI reuse policy fields")
    if any(not isinstance(data[key], str) or not data[key] for key in ("workflow", "workflow-name", "workflow-sha256")):
        raise ValueError("reuse workflow identity requires nonempty strings")
    jobs = data["jobs"]
    if not isinstance(jobs, list) or any(not isinstance(job, dict) or set(job) != {"name", "steps", "labels", *CONTRACT_FIELDS} for job in jobs):
        raise ValueError("reuse jobs require name, steps, labels and four execution digests")
    signer = _object(data["signing-job"])
    if set(signer) != {"name", "steps"} or not isinstance(signer["steps"], list):
        raise ValueError("reuse signing-job requires name and steps")
    if any(not isinstance(job["steps"], list) or not isinstance(job["labels"], list) for job in jobs):
        raise ValueError("reuse steps and labels must be arrays")
    return ReusePolicy(
        ValidationPolicy(
            repository,
            data["workflow"],
            data["workflow-name"],
            tuple((job["name"], tuple(job["steps"])) for job in jobs),
            branch=data.get("branch", "main"),
            runner_labels=tuple((job["name"], tuple(job["labels"])) for job in jobs),
        ),
        data["workflow-sha256"],
        tuple((job["name"], tuple((key, job[key]) for key in sorted(CONTRACT_FIELDS))) for job in jobs),
        (signer["name"], tuple(signer["steps"])),
        data.get("artifact-prefix", "ci-reuse"),
        data.get("max-age-seconds", 86400),
        data.get("app-id", 15368),
    )


def _commit(read: Callable[[str], Any], sha: str) -> dict[str, Any]:
    result = _object(read(f"git/commits/{_digest(sha, 40)}"))
    if result.get("sha") != sha:
        raise ValueError("provider returned a different commit")
    _digest(_object(result.get("tree")).get("sha"), 40)
    parents = result.get("parents")
    if not isinstance(parents, list):
        raise ValueError("missing commit parents")
    for parent in parents:
        _digest(_object(parent).get("sha"), 40)
    return result


def _workflow(policy: ReusePolicy, sha: str, read: Callable[[str], Any]) -> None:
    data = _object(read(f"contents/{policy.validation.workflow}?ref={_digest(sha, 40)}"))
    content = data.get("content")
    if data.get("type") != "file" or data.get("encoding") != "base64" or not isinstance(content, str) or len(content) > MAX_DOCUMENT:
        raise ValueError("workflow definition is missing or exceeds the bound")
    try:
        payload = base64.b64decode(content.replace("\n", ""), validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("invalid workflow definition encoding") from error
    if hashlib.sha256(payload).hexdigest() != policy.workflow_sha256:
        raise ValueError("workflow definition differs from the reviewed reuse policy")


def _run(policy: ReusePolicy, run_id: int, attempt: int, read: Callable[[str], Any], *, completed: bool) -> dict[str, Any]:
    run = _object(read(f"actions/runs/{_positive(run_id)}"))
    workflow = _object(read(f"actions/workflows/{PurePosixPath(policy.validation.workflow).name}"))
    repository = _object(run.get("repository"))
    head = _object(run.get("head_repository"))
    if (
        repository.get("full_name") != policy.validation.repository
        or head.get("full_name") != repository["full_name"]
        or _positive(repository.get("id")) != _positive(head.get("id"))
    ):
        raise ValueError("reuse rejects foreign repositories and forks")
    if (
        (run.get("id"), run.get("run_attempt")) != (_positive(run_id), _positive(attempt))
        or type(run.get("id")) is not int
        or type(run.get("run_attempt")) is not int
        or run.get("event") != "pull_request"
        or workflow.get("state") != "active"
        or (workflow.get("path"), run.get("path")) != (policy.validation.workflow, policy.validation.workflow)
        or (workflow.get("name"), run.get("name")) != (policy.validation.workflow_name, policy.validation.workflow_name)
        or _positive(workflow.get("id")) != _positive(run.get("workflow_id"))
    ):
        raise ValueError("reuse run, attempt, event or workflow identity differs")
    _digest(run.get("head_sha"), 40)
    _positive(run.get("check_suite_id"))
    _timestamp(run.get("created_at"))
    if completed and (run.get("status"), run.get("conclusion")) != ("completed", "success"):
        raise ValueError("reuse requires a complete successful run; rerun ALL jobs or use exact-commit CI")
    return run


def _jobs(policy: ReusePolicy, run: dict[str, Any], read: Callable[[str], Any], *, signing: bool) -> tuple[dict[str, int], dict[str, int]]:
    validation = replace(policy.validation, required_steps=(*policy.validation.required_steps, policy.signing_job)) if signing else policy.validation
    jobs = read_pages(read, f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", "jobs")
    selected = validate_jobs(validation, run, jobs)
    # The latest check collection also rejects a newer failed/pending run and
    # multiple workflows publishing the same required name/provider.
    checks = read_pages(read, f"commits/{run['head_sha']}/check-runs?filter=latest", "check_runs")
    identifiers = [_positive(check.get("id")) for check in checks]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate reuse check identity")
    check_ids = {}
    for name, job_id in selected.items():
        matches = [check for check in checks if check.get("name") == name]
        if len(matches) != 1:
            raise ValueError(f"missing or ambiguous reuse check {name}")
        check = matches[0]
        job = next(job for job in jobs if job["id"] == job_id)
        if (
            _positive(_object(check.get("app")).get("id")) != policy.app_id
            or check.get("head_sha") != run["head_sha"]
            or _positive(_object(check.get("check_suite")).get("id")) != run["check_suite_id"]
            or (check.get("status"), check.get("conclusion")) != ("completed", "success")
            or job.get("check_run_url") != f"https://api.github.com/repos/{policy.validation.repository}/check-runs/{check['id']}"
        ):
            raise ValueError(f"reuse check {name} has a different provider, run, job or conclusion")
        check_ids[name] = _positive(check["id"])
    return selected, check_ids


def _identity(run: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: run[key] for key in ("id", "run_attempt", "head_sha", "head_branch", "workflow_id", "check_suite_id", "event", "created_at")},
        "repository_id": run["repository"]["id"],
    }


def _observations(policy: ReusePolicy, checkout: str, tree: str, jobs: dict[str, int]) -> dict[str, Any]:
    return {
        name: {
            "job_id": jobs[name],
            "checkout": checkout,
            "tree": tree,
            "mode": "offline-tree",
            "git_metadata": False,
            "event_context": False,
            "network": False,
            **dict(contract),
        }
        for name, contract in policy.contracts
    }


def _document(policy: ReusePolicy, run: dict[str, Any], checkout: dict[str, Any], jobs: dict[str, int], pull_request: int) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "policy": asdict(policy),
        "run": _identity(run),
        "pull_request": _positive(pull_request),
        "checkout": checkout["sha"],
        "tree": checkout["tree"]["sha"],
        "observations": _observations(policy, checkout["sha"], checkout["tree"]["sha"], jobs),
    }


def create_reuse_manifest(
    policy: ReusePolicy,
    checkout: Path,
    pull_request: int,
    run_id: int,
    attempt: int,
    observations: Mapping[str, Any],
    read: Callable[[str], Any],
) -> bytes:
    """Seal independently observed native receipts in the trusted signing job.

    Observe each check's actual inputs/isolation in the reviewed workflow; never
    populate receipts by copying policy values. Sign these returned bytes with
    GitHub provenance and retain both files in an attempt-specific artifact.
    """
    sha = run_git_command(["--no-pager", "rev-parse", "HEAD"], cwd=checkout).stdout.strip()
    require_release_checkout(checkout, sha)
    run = _run(policy, run_id, attempt, read, completed=False)
    source = _commit(read, sha)
    tree = run_git_command(["--no-pager", "rev-parse", "HEAD^{tree}"], cwd=checkout).stdout.strip()
    if _digest(tree, 40) != source["tree"]["sha"]:
        raise ValueError("local checkout tree differs from provider source identity")
    _workflow(policy, sha, read)
    if len(source["parents"]) != 2 or source["parents"][1]["sha"] != run["head_sha"]:
        raise ValueError("reuse requires the PR's synthetic merge checkout")
    jobs, _ = _jobs(policy, run, read, signing=False)
    document = _document(policy, run, source, jobs, pull_request)
    if deterministic_json(dict(observations)) != deterministic_json(document["observations"]):
        raise ValueError("observed checkout, context, tools or inputs differ from the reviewed execution contract")
    require_release_checkout(checkout, sha)
    if _identity(_run(policy, run_id, attempt, read, completed=False)) != document["run"]:
        raise ValueError("reuse run changed while sealing observations")
    return deterministic_json(document)


def _relationship(
    policy: ReusePolicy, release_commit: str, pull_request: int, run: dict[str, Any], checkout: str, read: Callable[[str], Any]
) -> dict[str, Any]:
    repository = policy.validation.repository
    branch_name = policy.validation.branch
    repo = _object(read(""))
    branch = _object(read(f"branches/{quote(branch_name, safe='')}"))
    if (
        repo.get("full_name") != repository
        or _positive(repo.get("id")) != run["repository"]["id"]
        or repo.get("default_branch") != branch_name
        or branch.get("name") != branch_name
        or branch.get("protected") is not True
    ):
        raise ValueError("reuse target must belong to the protected default branch")
    comparison = _object(read(f"compare/{release_commit}...{quote(branch_name, safe='')}"))
    if comparison.get("status") not in ("ahead", "identical"):
        raise ValueError("reuse target is outside the protected branch")
    pr = _object(read(f"pulls/{_positive(pull_request)}"))
    head, base = _object(pr.get("head")), _object(pr.get("base"))
    if (
        type(pr.get("number")) is not int
        or pr.get("number") != pull_request
        or pr.get("merged") is not True
        or pr.get("state") != "closed"
        or pr.get("merge_commit_sha") != release_commit
        or head.get("sha") != run["head_sha"]
        or head.get("ref") != run["head_branch"]
        or base.get("ref") != branch_name
        or any(
            _object(side.get("repo")).get("full_name") != repository or _positive(_object(side.get("repo")).get("id")) != repo["id"] for side in (head, base)
        )
    ):
        raise ValueError("reuse requires this repository's merged PR and exact squash release commit")
    source, target = _commit(read, checkout), _commit(read, release_commit)
    if (
        len(source["parents"]) != 2
        or len(target["parents"]) != 1
        or source["parents"][1]["sha"] != run["head_sha"]
        or source["parents"][0]["sha"] != target["parents"][0]["sha"]
        or source["tree"]["sha"] != target["tree"]["sha"]
    ):
        raise ValueError("tested merge and release must have equal trees and the same base; use exact-commit CI")
    _workflow(policy, checkout, read)
    _workflow(policy, release_commit, read)
    return source


def verify_ci_reuse(
    policy: ReusePolicy,
    release_commit: str,
    read: Callable[[str], Any],
    *,
    pull_request: int | None = None,
    run_id: int | None = None,
    attempt: int | None = None,
    expected: bytes | None = None,
    now: datetime | None = None,
) -> bytes:
    """Verify explicit evidence, or revalidate saved approval bytes without reselection.

    Only source checks covered by this policy may be replaced. All remaining
    release checks, packaging, tags, assets and publication safeguards still run.
    Rejection never silently selects older evidence or dispatches a workflow.
    """
    _digest(release_commit, 40)
    pinned = _object(parse_release_json(expected)) if expected is not None else None
    if pinned is not None:
        if any(value is not None for value in (pull_request, run_id, attempt)):
            raise ValueError("approval revalidation cannot select a different reuse candidate")
        if pinned.get("schema") != SELECTION or pinned.get("release_commit") != release_commit:
            raise ValueError("approved reuse target differs")
        identity = _object(pinned.get("run"))
        pull_request, run_id, attempt = _positive(pinned.get("pull_request")), _positive(identity.get("id")), _positive(identity.get("run_attempt"))
    pull_request, run_id, attempt = _positive(pull_request), _positive(run_id), _positive(attempt)
    instant = now if now is not None else datetime.now(UTC)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("reuse clock requires a timezone")

    def snapshot() -> dict[str, Any]:
        current = _run(policy, run_id, attempt, read, completed=True)
        age = ((now if now is not None else datetime.now(UTC)) - _timestamp(current.get("created_at"))).total_seconds()
        if not 0 <= age <= policy.max_age_seconds:
            raise ValueError("reuse evidence is stale or future-dated; use exact-commit CI")
        jobs, checks = _jobs(policy, current, read, signing=True)
        name = f"{policy.artifact_prefix}-{run_id}-{attempt}"
        matches = [entry for entry in read_pages(read, f"actions/runs/{run_id}/artifacts", "artifacts") if entry.get("name") == name]
        if len(matches) != 1:
            raise ValueError("missing or ambiguous reuse artifact")
        artifact = artifact_identity(matches[0], current, name)
        if artifact_identity(_object(read(f"actions/artifacts/{artifact['id']}")), current, name) != artifact:
            raise ValueError("reuse artifact changed during selection")
        return {"run": _identity(current), "jobs": jobs, "checks": checks, "artifact": artifact}

    run = _run(policy, run_id, attempt, read, completed=True)
    selected = snapshot()
    if _identity(run) != selected["run"]:
        raise ValueError("CI reuse run changed during selection")
    with tempfile.TemporaryDirectory(prefix="research-ci-reuse-") as temporary:
        directory = Path(temporary) / "evidence"
        download_artifact(policy.validation.repository, selected["artifact"], directory, (MANIFEST, BUNDLE))
        with (directory / MANIFEST).open("rb") as stream:
            payload = stream.read(MAX_DOCUMENT + 1)
        if len(payload) > MAX_DOCUMENT:
            raise ValueError("reuse manifest exceeds the byte bound")
        document = _object(parse_release_json(payload))
        checkout = _digest(document.get("checkout"), 40)
        verify_attestation(
            directory / MANIFEST,
            directory / BUNDLE,
            checkout,
            policy.validation.workflow,
            f"refs/pull/{pull_request}/merge",
            run_id,
            attempt,
            repository=policy.validation.repository,
            event="pull_request",
        )
        source = _relationship(policy, release_commit, pull_request, run, checkout, read)
        wanted = _document(policy, run, source, selected["jobs"], pull_request)
        if deterministic_json(document) != deterministic_json(wanted):
            raise ValueError("signed reuse observations differ from the source, jobs, context or execution policy")
        if snapshot() != selected or _relationship(policy, release_commit, pull_request, run, checkout, read) != source:
            raise ValueError("CI reuse evidence changed during verification")
    record = deterministic_json(
        {
            "schema": SELECTION,
            "policy": asdict(policy),
            "release_commit": release_commit,
            "pull_request": pull_request,
            "checkout": checkout,
            "tree": source["tree"]["sha"],
            "manifest_sha256": hashlib.sha256(payload).hexdigest(),
            **selected,
        }
    )
    if pinned is not None and deterministic_json(pinned) != record:
        raise ValueError("approved reuse evidence changed; inspect before making a new selection")
    return record
