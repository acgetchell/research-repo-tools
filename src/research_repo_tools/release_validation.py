"""Promote immutable distributions from one trusted, complete main-push attempt.

This module deliberately uses only the standard library: release runners load
this reviewed source directly, without installing the distribution under review.
GitHub CLI authenticates transport and verifies Sigstore signatures. Parsed JSON
alone never establishes trust. Consumers supply their own validation policy;
registry upload, Rust packaging and scientific gates remain their responsibility.
"""

import argparse
import gzip
import hashlib
import io
import json
import math
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import zipfile
import zlib
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

if __package__:
    from research_repo_tools._archive_paths import ArchivePaths
    from research_repo_tools._console import console_text, write_stdout
    from research_repo_tools._diagnostics import format_exception_diagnostics
    from research_repo_tools._git_checkout import READ_ONLY_OPTIONS, git_context, require_checkout
    from research_repo_tools.files import replace_many
    from research_repo_tools.release_summary import render_distribution_summary
else:
    # The install-free runner loads reviewed sibling source, never site packages.
    import runpy

    ArchivePaths = runpy.run_path(str(Path(__file__).with_name("_archive_paths.py")))["ArchivePaths"]
    _console_helpers = runpy.run_path(str(Path(__file__).with_name("_console.py")))
    console_text, write_stdout = _console_helpers["console_text"], _console_helpers["write_stdout"]
    format_exception_diagnostics = runpy.run_path(str(Path(__file__).with_name("_diagnostics.py")))["format_exception_diagnostics"]
    _checkout_helpers = runpy.run_path(str(Path(__file__).with_name("_git_checkout.py")))
    READ_ONLY_OPTIONS, require_checkout = _checkout_helpers["READ_ONLY_OPTIONS"], _checkout_helpers["require_checkout"]
    git_context = _checkout_helpers["git_context"]
    replace_many = runpy.run_path(str(Path(__file__).with_name("files.py")))["replace_many"]
    render_distribution_summary = runpy.run_path(str(Path(__file__).with_name("release_summary.py")))["render_distribution_summary"]

__all__ = [
    "ValidationPolicy",
    "api",
    "artifact_identity",
    "distribution_names",
    "download_artifact",
    "parse_release_json",
    "read_pages",
    "redact_release_diagnostic",
    "select_main_run",
    "validate_jobs",
    "validate_run",
    "verify_attestation",
    "verify_distributions",
]

REPOSITORY = "acgetchell/research-repo-tools"
BUNDLE = "release-attestation.json"
MANIFEST = "validation.json"
VALIDATION_BUNDLE = "validation.sigstore.json"
SCHEMA = "research-repo-tools/validated-distributions/v1"
PROMOTION = "https://github.com/acgetchell/research-repo-tools/attestations/release-promotion/v1"
MAX_BYTES = 32 * 1024 * 1024
_DEFAULT_WAIT_ATTEMPTS = 241
_DEFAULT_WAIT_INTERVAL = 30
_MAX_WAIT_ATTEMPTS = 721
_GH_EXECUTABLE = ContextVar("release_gh_executable", default="gh")
RECOVERY = (
    "Rerun ALL jobs of the original main-push Package checks run for this exact commit within GitHub's 30-day rerun window, then retry preparation. "
    "After that window, land a new reviewed commit on main and follow the explicitly authorized unpublished-candidate correction procedure; preserve published releases. "
    "Do not dispatch, rebuild locally, or substitute PR artifacts or another commit silently."
)


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    return value


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_release_json(payload: str | bytes) -> Any:
    """Parse bounded UTF-8 evidence, rejecting duplicate/nonfinite/deep JSON."""

    def finite(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite JSON number")
        return number

    try:
        if len(payload) > MAX_BYTES:
            raise ValueError("release JSON exceeds the byte bound")
        source = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        if len(source.encode("utf-8")) > MAX_BYTES:
            raise ValueError("release JSON exceeds the byte bound")
        value = json.loads(source, object_pairs_hook=_pairs, parse_constant=finite, parse_float=finite)
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 128:
                raise ValueError("release JSON exceeds 128 levels of nesting")
            if isinstance(item, str):
                item.encode("utf-8")
            elif isinstance(item, dict):
                pending.extend((key, depth + 1) for key in item)
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        return value
    except (UnicodeError, RecursionError) as error:
        raise ValueError("release JSON must be bounded, shallow UTF-8") from error


_json = parse_release_json


def _bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _integer(value: Any, context: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{context} must be a positive integer")
    return value


def _commit(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("expected a full lowercase commit SHA")
    return value


def _repository(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) is None or any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("repository must be owner/name")
    return value


def _python_package(package: str) -> str:
    if not isinstance(package, str) or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", package) is None:
        raise ValueError("expected a Python distribution package name")
    return re.sub(r"[-_.]+", "_", package).lower()


def distribution_names(tag: str, *, package: str = "research-repo-tools", filenames: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """Validate a stable version's explicit wheel/sdist inventory, without a tag lookup.

    Omitted filenames select one universal wheel and sdist. Native wheels and
    multiple wheel tags require an explicit inventory; no globbing is performed.
    """
    if re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", tag) is None:
        raise ValueError("release assets require a canonical stable vX.Y.Z tag")
    prefix = f"{_python_package(package)}-{tag[1:]}"
    names = tuple(filenames) if filenames is not None else (f"{prefix}-py3-none-any.whl", f"{prefix}.tar.gz")
    wheel = re.escape(prefix) + r"-(?:[0-9][A-Za-z0-9_]*-)?[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+\.whl"
    if (
        not 2 <= len(names) <= 64
        or len({name.casefold() for name in names}) != len(names)
        or names.count(f"{prefix}.tar.gz") != 1
        or any(name != f"{prefix}.tar.gz" and re.fullmatch(wheel, name) is None for name in names)
    ):
        raise ValueError("distribution inventory requires one matching sdist and distinct matching wheel filenames")
    return tuple(sorted(names))


def _policy_name(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{context} requires nonempty single-line names")
    value.encode("utf-8")
    return value


def _requirements(value: object, context: str, *, unique_values: bool = True) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{context} requires a sequence of job/requirement pairs")
    result = []
    for pair in value:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise ValueError(f"{context} requires job/requirement pairs")
        name = _policy_name(pair[0], context)
        values = pair[1]
        if not isinstance(values, (tuple, list)) or not values:
            raise ValueError(f"{context} requires a nonempty sequence for each job")
        requirements = tuple(_policy_name(item, context) for item in values)
        if unique_values and len(set(requirements)) != len(requirements):
            raise ValueError(f"{context} requires distinct names for each job")
        result.append((name, requirements))
    if len(dict(result)) != len(result):
        raise ValueError(f"{context} requires unique job names")
    return tuple(result)


def _workflow_policy(
    repository: str,
    workflow: str,
    workflow_name: str,
    required_steps: object,
    runner_labels: object,
) -> tuple[tuple[tuple[str, tuple[str, ...]], ...], tuple[tuple[str, tuple[str, ...]], ...]]:
    _repository(repository)
    steps = _requirements(required_steps, "step requirements")
    labels = _requirements(runner_labels, "runner labels", unique_values=False)
    if not isinstance(workflow, str) or re.fullmatch(r"\.github/workflows/[A-Za-z0-9_-]+\.yml", workflow) is None:
        raise ValueError("workflow must be an explicit .github/workflows/*.yml path")
    _policy_name(workflow_name, "workflow name")
    names = {name for name, _ in steps}
    if not names:
        raise ValueError("validation policy requires unique named jobs and nonempty step requirements")
    if any(name not in names for name, _ in labels):
        raise ValueError("runner labels require unique required-job names and nonempty labels")
    return steps, labels


@dataclass(frozen=True)
class ValidationPolicy:
    """Trusted repository/workflow and named gates, independent of registry policy."""

    repository: str
    workflow: str
    workflow_name: str
    required_steps: tuple[tuple[str, tuple[str, ...]], ...]
    branch: str = "main"
    runner_labels: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        steps, labels = _workflow_policy(self.repository, self.workflow, self.workflow_name, self.required_steps, self.runner_labels)
        object.__setattr__(self, "required_steps", steps)
        object.__setattr__(self, "runner_labels", labels)
        if not isinstance(self.branch, str) or re.fullmatch(r"[A-Za-z0-9_/-]+", self.branch) is None or any(not part for part in self.branch.split("/")):
            raise ValueError("branch must be an explicit portable branch name")


POLICY = ValidationPolicy(
    REPOSITORY,
    ".github/workflows/ci.yml",
    "Package checks",
    (
        ("build", ("Build release distributions once", "Upload immutable distributions")),
        *(
            (
                f"{stage} ({platform})",
                steps,
            )
            for platform in ("ubuntu-24.04", "macos-15", "windows-2025")
            for stage, steps in (
                ("source", ("Retrieve distributions by immutable ID", "Verify native dependency provenance", "Validate source policy", "Run source tests")),
                ("distributions", ("Retrieve distributions by immutable ID", "Validate installed distributions")),
                ("setup", ("Retrieve distributions by immutable ID", "Exercise native setup and Cargo updates from the built wheel")),
                ("check", ("Require all native validation stages",)),
            )
        ),
        (
            "changelog-integration",
            ("Retrieve distributions by immutable ID", "Validate, install the pinned generator, and exercise source and installed contracts"),
        ),
    ),
)


def gh(*args: str) -> str:
    return subprocess.run([_GH_EXECUTABLE.get(), *args], check=True, capture_output=True, text=True, encoding="utf-8", timeout=120).stdout


def api(endpoint: str, *, repository: str = REPOSITORY) -> Any:
    path = f"repos/{_repository(repository)}"
    if endpoint:
        path = f"{path}/{endpoint}"
    return _json(gh("api", path))


def read_pages(read: Callable[[str], Any], endpoint: str, field: str) -> list[dict[str, Any]]:
    """Read at most 100 pages through repository-scoped authenticated transport.

    Reject changing/incomplete total counts when supplied by the provider.
    Consumers still validate collection-specific identities and duplicates.
    """
    result = []
    totals = set()
    separator = "&" if "?" in endpoint else "?"
    for page in range(1, 101):
        data = _object(read(f"{endpoint}{separator}per_page=100&page={page}"), endpoint)
        entries = data.get(field)
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise ValueError(f"invalid {field} listing")
        if "total_count" in data:
            count = data["total_count"]
            if type(count) is not int or count < 0:
                raise ValueError(f"invalid {field} total count")
            totals.add(count)
        result.extend(entries)
        if len(entries) < 100:
            if totals and totals != {len(result)}:
                raise ValueError(f"incomplete or changing {field} pagination")
            return result
    raise ValueError(f"{field} listing exceeds the bounded page limit; inspect repository evidence")


_pages = read_pages


def _workflow_id(policy: ValidationPolicy, workflow: dict[str, Any]) -> int:
    return _workflow_identity(policy.workflow, policy.workflow_name, workflow)


def _workflow_identity(path: str, name: str, workflow: dict[str, Any]) -> int:
    identifier = _integer(workflow.get("id"), "workflow ID")
    if workflow.get("path") != path or workflow.get("name") != name or workflow.get("state") != "active":
        raise ValueError("expected active workflow identity matching the validation policy")
    return identifier


def validate_run(policy: ValidationPolicy, run: dict[str, Any], commit: str, workflow: dict[str, Any], *, completed: bool = True) -> None:
    """Reject PRs, forks, dispatches and workflow/ref/commit/attempt mismatches."""
    _commit(commit)
    repository, head = _object(run.get("repository"), "run.repository"), _object(run.get("head_repository"), "run.head_repository")
    repo_id = _integer(repository.get("id"), "repository ID")
    if (
        repository.get("full_name") != policy.repository
        or head.get("full_name") != policy.repository
        or _integer(head.get("id"), "head repository ID") != repo_id
    ):
        raise ValueError("validation run repository or head repository differs")
    _integer(run.get("id"), "run ID")
    _integer(run.get("run_attempt"), "run attempt")
    workflow_id = _workflow_id(policy, workflow)
    if (
        _integer(run.get("workflow_id"), "run workflow ID") != workflow_id
        or run.get("path") != policy.workflow
        or run.get("name") != policy.workflow_name
        or run.get("event") != "push"
        or run.get("head_branch") != policy.branch
        or run.get("head_sha") != commit
        or run.get("pull_requests")
    ):
        raise ValueError("expected the exact commit's trusted main-push Package checks run")
    if completed and (run.get("status") != "completed" or run.get("conclusion") != "success"):
        raise ValueError(f"validation run {run['id']} attempt {run['run_attempt']} is not successful; {RECOVERY}")


def select_main_run(
    policy: ValidationPolicy,
    commit: str,
    read: Callable[[str], Any],
    *,
    attempts: int = _DEFAULT_WAIT_ATTEMPTS,
    interval: int = _DEFAULT_WAIT_INTERVAL,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Poll one unambiguous exact run, with at most two hours of default retry delay.

    read is repository-scoped authenticated JSON transport. Only pending matching
    main-push runs are waited on. The caller's read/sleep callbacks own their
    deadlines; time spent in transport is additional to retry delay.
    Missing/failed evidence never causes a fallback.
    """
    _commit(commit)
    if type(attempts) is not int or not 1 <= attempts <= _MAX_WAIT_ATTEMPTS or type(interval) is not int or not 0 <= interval <= 30:
        raise ValueError(f"wait requires 1..{_MAX_WAIT_ATTEMPTS} attempts and an interval of 0..30 seconds")
    workflow = _object(read(f"actions/workflows/{Path(policy.workflow).name}"), "workflow")
    workflow_id = _workflow_id(policy, workflow)
    pending: list[dict[str, Any]] = []
    for index in range(attempts):
        runs = read_pages(read, f"actions/workflows/{workflow_id}/runs?event=push&branch={quote(policy.branch, safe='')}&head_sha={commit}", "workflow_runs")
        for run in runs:
            validate_run(policy, run, commit, workflow, completed=False)
            if run.get("status") not in ("queued", "in_progress", "waiting", "requested", "pending", "completed"):
                raise ValueError(f"invalid validation run status for run {run['id']} attempt {run['run_attempt']}")
        pending = [run for run in runs if run.get("status") != "completed"]
        successful = [run for run in runs if run.get("status") == "completed" and run.get("conclusion") == "success"]
        if len(successful) > 1:
            raise ValueError(f"ambiguous successful main-push validation runs for {commit}; inspect duplicate runs before retrying")
        if not pending:
            if len(successful) != 1:
                raise ValueError(f"missing or failed main-push validation for {commit}; {RECOVERY}")
            return successful[0]
        if index + 1 < attempts:
            print(f"Waiting for exact main validation of {commit} ({index + 1}/{attempts}).", file=sys.stderr)
            sleep(interval)
    pending_urls = ", ".join(f"https://github.com/{policy.repository}/actions/runs/{run['id']} (attempt {run['run_attempt']})" for run in pending)
    raise ValueError(
        f"exact main validation is still pending after the bounded wait: {pending_urls}; "
        "wait for the original run to succeed, then retry preparation. Preserve its run/attempt and artifacts; pending validation does not require a rerun."
    )


def validate_jobs(policy: ValidationPolicy, run: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, int]:
    """Require every producer/validator in this attempt, including installed checks.

    A failed-jobs-only or individual-job rerun omits the original producer and is
    rejected. A full rerun produces a new artifact and evidence for the new attempt.
    """
    return _validate_jobs(policy.required_steps, policy.runner_labels, run, jobs, recovery=RECOVERY)


def _validate_jobs(
    required_steps: tuple[tuple[str, tuple[str, ...]], ...],
    runner_labels: tuple[tuple[str, tuple[str, ...]], ...],
    run: dict[str, Any],
    jobs: list[dict[str, Any]],
    *,
    recovery: str,
) -> dict[str, int]:
    result = {}
    for name, steps in required_steps:
        matches = [job for job in jobs if job.get("name") == name]
        if len(matches) != 1:
            raise ValueError(f"missing or ambiguous {name} in attempt {run['run_attempt']}; {recovery}")
        job = matches[0]
        if any(job.get(key) != run[key] for key in ("head_sha", "head_branch")) or (
            _integer(job.get("run_id"), "job run ID"),
            _integer(job.get("run_attempt"), "job attempt"),
        ) != (run["id"], run["run_attempt"]):
            raise ValueError(f"{name} belongs to another run, attempt, ref or commit")
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            raise ValueError(f"{name} did not succeed; {recovery}")
        labels = dict(runner_labels).get(name)
        if labels is not None and (
            not isinstance(job.get("labels"), list)
            or any(not isinstance(label, str) or not label for label in job["labels"])
            or not set(labels).issubset(job["labels"])
            or "self-hosted" in job["labels"]
        ):
            raise ValueError(f"{name} did not run on the required hosted platform")
        if not isinstance(job.get("steps"), list) or any(not isinstance(step, dict) for step in job["steps"]):
            raise ValueError(f"{name} has malformed step evidence")
        for required in steps:
            found = [step for step in job.get("steps", []) if step.get("name") == required]
            if len(found) != 1 or found[0].get("status") != "completed" or found[0].get("conclusion") != "success":
                raise ValueError(f"{name} did not validate required step {required}; {recovery}")
        result[name] = _integer(job.get("id"), "job ID")
    if len(set(result.values())) != len(result):
        raise ValueError("validation job IDs are not unique")
    return result


def artifact_identity(data: dict[str, Any], run: dict[str, Any], name: str, *, max_bytes: int = MAX_BYTES) -> dict[str, Any]:
    """Bind an immutable artifact ID/digest to its exact repository/run/ref/SHA.

    GitHub does not report an artifact's attempt. Callers must use an
    attempt-specific name and verify the attempt inside its retained manifest.
    max_bytes bounds the original ZIP; pass the same bound to download_artifact.
    """
    return _artifact_identity(data, run, name, max_bytes=max_bytes, recovery=RECOVERY)


def _artifact_identity(data: dict[str, Any], run: dict[str, Any], name: str, *, max_bytes: int, recovery: str) -> dict[str, Any]:
    _integer(max_bytes, "artifact byte limit")
    identity = _object(data.get("workflow_run"), "artifact.workflow_run")
    if data.get("name") != name or data.get("expired") is not False:
        raise ValueError(f"missing, expired or mismatched {name}; {recovery}")
    expected = {
        "id": run["id"],
        "head_sha": run["head_sha"],
        "head_branch": run["head_branch"],
        "repository_id": run["repository"]["id"],
        "head_repository_id": run["head_repository"]["id"],
    }
    for key in ("id", "repository_id", "head_repository_id"):
        _integer(identity.get(key), f"artifact {key}")
    if any(identity.get(key) != value for key, value in expected.items()):
        raise ValueError("artifact is associated with another repository, ref, run or commit")
    identifier = _integer(data.get("id"), "artifact ID")
    digest = data.get("digest")
    if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise ValueError("artifact lacks an authoritative SHA-256 digest")
    if type(data.get("size_in_bytes")) is not int or not 0 < data["size_in_bytes"] <= max_bytes:
        raise ValueError("artifact size exceeds the release bound")
    return {"id": identifier, "name": name, "digest": digest, "size_in_bytes": data["size_in_bytes"]}


_artifact = artifact_identity


def _find_artifact(run: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [data for data in _pages(api, f"actions/runs/{run['id']}/artifacts", "artifacts") if data.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"missing or ambiguous {name}; {RECOVERY}")
    return _artifact(matches[0], run, name)


def _available_artifact(identifier: int) -> dict[str, Any]:
    try:
        return _object(api(f"actions/artifacts/{_integer(identifier, 'artifact ID')}"), "artifact")
    except subprocess.CalledProcessError as error:
        detail = _diagnostic(error.stderr or error.stdout or str(error))
        raise ValueError(
            f"immutable artifact {identifier} is unavailable (missing, expired or access denied): {detail}; inspect GitHub access/retention. {RECOVERY}"
        ) from error


def _publish_distributions(destination: Path, contents: dict[str, bytes], *, evidence: tuple[Path, bytes] | None = None) -> None:
    """Publish a new distribution directory and its optional proof with rollback."""
    if destination.exists() or destination.is_symlink() or destination.is_junction():
        raise FileExistsError(f"distribution destination already exists: {destination}")
    updates = {destination / name: payload for name, payload in contents.items()}
    if evidence is not None:
        path, payload = evidence
        if path in updates:
            raise ValueError("evidence must be separate from the distributions")
        updates[path] = payload
    replace_many(updates)


def download_artifact(
    repository: str,
    artifact: dict[str, Any],
    destination: Path,
    names: tuple[str, ...],
    *,
    max_bytes: int = MAX_BYTES,
    max_expanded_bytes: int | None = None,
) -> None:
    """Retrieve one immutable ID, verify original ZIP digest, and extract flat files.

    Refuse links, directory entries, duplicates, aliases, extras and resource
    excesses before creating the destination. Never call extractall on input.
    max_bytes bounds the ZIP; max_expanded_bytes defaults to that same limit.
    Retry transient HTTP transport failures at most twice for this immutable ID.
    """
    _repository(repository)
    _integer(max_bytes, "artifact byte limit")
    expanded_limit = max_bytes if max_expanded_bytes is None else _integer(max_expanded_bytes, "expanded artifact byte limit")
    if (
        not names
        or len({name.casefold() for name in names}) != len(names)
        or any(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) is None
            or name.endswith(".")
            or re.fullmatch(r"(?:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", name, flags=re.IGNORECASE)
            for name in names
        )
    ):
        raise ValueError("expected artifact names must be distinct portable plain filenames")
    identifier = _integer(artifact.get("id"), "artifact ID")
    expected_size = _integer(artifact.get("size_in_bytes"), "artifact size")
    if expected_size > max_bytes:
        raise ValueError("artifact size exceeds the release bound")
    with tempfile.TemporaryFile() as stream:
        for attempt in range(3):
            # A failed transfer may have written a partial or longer response.
            stream.seek(0)
            stream.truncate()
            try:
                subprocess.run(
                    [_GH_EXECUTABLE.get(), "api", f"repos/{repository}/actions/artifacts/{identifier}/zip"],
                    stdout=stream,
                    stderr=subprocess.PIPE,
                    check=True,
                    timeout=120,
                )
                break
            except subprocess.CalledProcessError as error:
                diagnostic = error.stderr or b""
                if isinstance(diagnostic, str):
                    diagnostic = diagnostic.encode("utf-8")
                if attempt == 2 or re.search(rb"\bHTTP (?:500|502|503|504)\b", diagnostic) is None:
                    raise
                time.sleep(2**attempt)
        stream.seek(0)
        payload = stream.read(expected_size + 1)
    if len(payload) != expected_size or f"sha256:{_sha(payload)}" != artifact.get("digest"):
        raise ValueError("downloaded artifact ZIP size or SHA-256 differs from immutable API identity")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if len(members) != len(names) or {member.orig_filename for member in members} != set(names):
            raise ValueError(f"artifact must contain exactly {names}")
        if any(member.is_dir() or member.flag_bits & 1 or stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG) for member in members):
            raise ValueError("artifact entries must be regular, unencrypted files")
        if any(member.file_size <= 0 for member in members) or sum(member.file_size for member in members) > expanded_limit:
            raise ValueError("artifact expanded size exceeds the release bound")
        contents = {member.filename: archive.read(member) for member in members}
    _publish_distributions(destination, contents)


def _distribution_member(name: str, paths: ArchivePaths, *, directory: bool) -> None:
    # Distribution inventories use canonical paths for their metadata locations.
    canonical = name.removesuffix("/") if directory else name
    if str(PurePosixPath(canonical)) != canonical:
        raise ValueError("distribution contains noncanonical member paths")
    paths.add(name, directory=directory)


def _distribution_metadata(payload: bytes, name: str, prefix: str) -> bytes:
    paths = ArchivePaths()
    content = None
    if name.endswith(".whl"):
        metadata_name = f"{prefix}.dist-info/METADATA"
        with zipfile.ZipFile(io.BytesIO(payload)) as wheel:
            members = wheel.infolist()
            if len(members) > 10000 or sum(entry.file_size for entry in members) > MAX_BYTES * 8:
                raise ValueError("wheel scan exceeds the release bound")
            for entry in members:
                _distribution_member(entry.orig_filename, paths, directory=entry.is_dir())
                if (
                    entry.orig_filename != entry.filename
                    or entry.flag_bits & 1
                    or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR)
                ):
                    raise ValueError("wheel members must be regular unencrypted files or directories")
                if entry.filename.endswith(".dist-info/METADATA"):
                    if entry.filename != metadata_name or not 0 < entry.file_size <= 1024 * 1024:
                        raise ValueError("wheel metadata inventory differs")
                    content = wheel.read(entry)
    else:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
            expanded = compressed.read(MAX_BYTES * 8 + 1)
        if len(expanded) > MAX_BYTES * 8:
            raise ValueError("sdist scan exceeds the release bound")
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as sdist:
            for index, entry in enumerate(sdist):
                if index >= 10000:
                    raise ValueError("sdist scan exceeds the release bound")
                _distribution_member(entry.name, paths, directory=entry.isdir())
                if (
                    entry.sparse is not None
                    or not (entry.isfile() or entry.isdir())
                    or PurePosixPath(entry.name).parts[0] != prefix
                    or not 0 <= entry.size <= len(expanded) - entry.offset_data
                ):
                    raise ValueError("sdist must contain regular package files or directories")
                if entry.name == f"{prefix}/PKG-INFO":
                    if not entry.isfile() or not 0 < entry.size <= 1024 * 1024:
                        raise ValueError("sdist metadata inventory differs")
                    source = sdist.extractfile(entry)
                    if source is not None:
                        with source:
                            content = source.read()
    if content is None:
        raise ValueError("distribution package metadata is missing")
    return content


def verify_distributions(
    dist: Path, tag: str, *, package: str = "research-repo-tools", filenames: tuple[str, ...] | None = None, max_total_bytes: int | None = None
) -> dict[str, str]:
    """Check exact inventory, safe bounded archives and core package metadata.

    Return hashes of the very bytes inspected. This is an integrity check, not
    CI provenance. The default preserves this tooling package's original API.
    max_total_bytes optionally bounds the sum of the inspected distribution bytes.
    """
    if max_total_bytes is not None:
        _integer(max_total_bytes, "total distribution byte limit")
    names = distribution_names(tag, package=package, filenames=filenames)
    if {path.name for path in dist.iterdir()} != set(names):
        raise ValueError(f"expected exactly these distributions: {', '.join(names)}")
    result = {}
    total = 0
    prefix = f"{_python_package(package)}-{tag[1:]}"
    for name in names:
        path = dist / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("distributions must be bounded nonempty regular files")
        with path.open("rb") as source:
            payload = source.read(MAX_BYTES + 1)
        if not 0 < len(payload) <= MAX_BYTES:
            raise ValueError("distributions must be bounded nonempty regular files")
        total += len(payload)
        if max_total_bytes is not None and total > max_total_bytes:
            raise ValueError("distribution inventory exceeds the total byte bound")
        try:
            metadata = BytesParser().parsebytes(_distribution_metadata(payload, name, prefix))
        except (OSError, EOFError, zlib.error, tarfile.TarError, zipfile.BadZipFile) as error:
            raise ValueError("distribution is not a readable wheel or sdist") from error
        package_names = metadata.get_all("Name", [])
        if len(package_names) != 1 or _python_package(package_names[0]) != _python_package(package) or metadata.get_all("Version") != [tag[1:]]:
            raise ValueError("distribution package name or version differs from the release tag")
        result[name] = _sha(payload)
    return result


def _jobs(run: dict[str, Any]) -> list[dict[str, Any]]:
    return _pages(api, f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", "jobs")


def _identity(run: dict[str, Any]) -> dict[str, Any]:
    return {key: run[key] for key in ("id", "run_attempt", "head_sha", "head_branch", "workflow_id", "path", "event")}


def _current(run: dict[str, Any], *, completed: bool = True) -> dict[str, Any]:
    identifier = _integer(run.get("id"), "recorded run ID")
    _integer(run.get("run_attempt"), "recorded run attempt")
    _commit(run.get("head_sha", ""))
    current = _object(api(f"actions/runs/{identifier}"), "run")
    validate_run(POLICY, current, run["head_sha"], api("actions/workflows/ci.yml"), completed=completed)
    if _identity(current) != _identity(run):
        raise ValueError("validation run or attempt changed during promotion; retry after inspecting current evidence")
    return current


def _remote_tag(tag: str, commit: str) -> None:
    data = _object(api(f"git/ref/tags/{tag}"), "remote tag")
    if data.get("ref") != f"refs/tags/{tag}":
        raise ValueError("remote tag reference differs")
    obj = _object(data.get("object"), "remote tag object")
    for _ in range(5):
        sha = _commit(obj.get("sha", ""))
        if obj.get("type") == "commit":
            if sha != commit:
                raise ValueError("remote release tag moved or resolves to another commit")
            return
        if obj.get("type") != "tag":
            break
        data = _object(api(f"git/tags/{sha}"), "annotated tag")
        if data.get("sha") != sha:
            break
        obj = _object(data.get("object"), "annotated tag object")
    raise ValueError("remote tag does not resolve to a bounded commit identity")


def verify_attestation(
    subject: Path,
    bundle: Path,
    commit: str,
    workflow: str,
    ref: str,
    run_id: int,
    attempt: int,
    *,
    repository: str,
    predicate: dict[str, Any] | None = None,
    predicate_type: str = "https://slsa.dev/provenance/v1",
    event: str = "push",
) -> None:
    """Use gh's cryptographic verification, then bind the signed invocation/event.

    The caller supplies trusted policy, not values learned from an unverified
    bundle. No source or build scripts execute in this operation.
    """
    _repository(repository)
    _commit(commit)
    valid_ref = (
        ref.startswith(("refs/heads/", "refs/tags/")) if event == "push" else event == "pull_request" and re.fullmatch(r"refs/pull/[1-9][0-9]*/merge", ref)
    )
    if re.fullmatch(r"\.github/workflows/[A-Za-z0-9_-]+\.yml", workflow) is None or not valid_ref:
        raise ValueError("attestation requires an explicit workflow and source ref")
    _integer(run_id, "signed run ID")
    _integer(attempt, "signed run attempt")
    result = _json(
        gh(
            "attestation",
            "verify",
            str(subject),
            "--bundle",
            str(bundle),
            "--repo",
            repository,
            "--signer-workflow",
            f"{repository}/{workflow}",
            "--signer-digest",
            commit,
            "--source-digest",
            commit,
            "--source-ref",
            ref,
            "--deny-self-hosted-runners",
            "--predicate-type",
            predicate_type,
            "--format",
            "json",
        )
    )
    if not isinstance(result, list) or len(result) != 1:
        raise ValueError("expected exactly one verified attestation")
    verified = _object(_object(result[0], "verified attestation").get("verificationResult"), "verification result")
    certificate = _object(_object(verified.get("signature"), "verified signature").get("certificate"), "verified certificate")
    invocation = f"https://github.com/{repository}/actions/runs/{run_id}/attempts/{attempt}"
    if certificate.get("runInvocationURI") != invocation or certificate.get("buildTrigger") != event:
        raise ValueError("attestation certificate run, attempt or expected event differs")
    if predicate is not None and _object(verified.get("statement"), "verified statement").get("predicate") != predicate:
        raise ValueError("signed promotion predicate differs from the retained evidence")


def _signature(
    subject: Path, bundle: Path, commit: str, workflow: str, ref: str, run_id: int, attempt: int, *, predicate: dict[str, Any] | None = None
) -> None:
    verify_attestation(
        subject,
        bundle,
        commit,
        workflow,
        ref,
        run_id,
        attempt,
        repository=REPOSITORY,
        predicate=predicate,
        predicate_type=PROMOTION if predicate is not None else "https://slsa.dev/provenance/v1",
    )


def seal(run_id: int, attempt: int, artifact_id: int, output: Path) -> None:
    """After native CI, capture job/artifact identities for signing on main only."""
    run = api(f"actions/runs/{_integer(run_id, 'run ID')}")
    validate_run(POLICY, run, os.environ.get("GITHUB_SHA", ""), api("actions/workflows/ci.yml"), completed=False)
    if run["run_attempt"] != _integer(attempt, "attempt"):
        raise ValueError("seal attempt differs from the executing main run")
    jobs = validate_jobs(POLICY, run, _jobs(run))
    artifact = _artifact(_available_artifact(artifact_id), run, f"distributions-{attempt}")
    with tempfile.TemporaryDirectory() as temporary:
        dist = Path(temporary) / "dist"
        # Source version is read from this exact checkout, not a mutable ref/API.
        version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        tag = f"v{version}"
        download_artifact(REPOSITORY, artifact, dist, distribution_names(tag))
        files = verify_distributions(dist, tag)
    _current(run, completed=False)
    manifest = {
        "schema": SCHEMA,
        "repository": REPOSITORY,
        "repository_id": run["repository"]["id"],
        "run": _identity(run),
        "jobs": jobs,
        "artifact": artifact,
        "version": version,
        "files": files,
    }
    replace_many({output: _bytes(manifest)})


def _executing_run(repository: str) -> dict[str, Any]:
    """Bind the runner environment to its current provider run and attempt."""
    if _repository(os.environ.get("GITHUB_REPOSITORY", "")) != repository:
        raise ValueError("executing repository differs from the expected repository")
    identifier = _integer(int(os.environ.get("GITHUB_RUN_ID", "0")), "run ID")
    attempt = _integer(int(os.environ.get("GITHUB_RUN_ATTEMPT", "0")), "attempt")
    run = _object(api(f"actions/runs/{identifier}", repository=repository), "executing run")
    if _object(run.get("repository"), "executing repository").get("full_name") != repository:
        raise ValueError("artifact validation run differs from the executing repository")
    if _integer(run.get("id"), "executing run ID") != identifier:
        raise ValueError("executing run ID differs from the runner environment")
    if _integer(run.get("run_attempt"), "executing attempt") != attempt:
        raise ValueError("validation attempt changed before artifact retrieval")
    return run


def consume(artifact_id: int, dist: Path) -> None:
    """Give each normal PR/main validator the exact producer's verified bytes."""
    repository = _repository(os.environ.get("GITHUB_REPOSITORY", ""))
    run = _executing_run(repository)
    attempt = run["run_attempt"]
    artifact = _artifact(api(f"actions/artifacts/{_integer(artifact_id, 'artifact ID')}", repository=repository), run, f"distributions-{attempt}")
    version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    names = distribution_names(f"v{version}")
    with tempfile.TemporaryDirectory() as temporary:
        candidate = Path(temporary) / "dist"
        download_artifact(repository, artifact, candidate, names)
        verify_distributions(candidate, f"v{version}")
        _publish_distributions(dist, {name: (candidate / name).read_bytes() for name in names})


def _validate_evidence(document: dict[str, Any], tag: str, commit: str, dist: Path) -> None:
    """Recheck signed original validation and live attempt association, never rebuild."""
    if document.get("tag") != tag or document.get("commit") != commit:
        raise ValueError("promotion evidence tag or commit differs")
    manifest_text, bundle_text = document["manifest"], document["validation_bundle"]
    if not isinstance(manifest_text, str) or not isinstance(bundle_text, str):
        raise ValueError("validation manifest and bundle must retain original UTF-8 bytes")
    manifest = _object(_json(manifest_text), "validation manifest")
    if manifest.get("schema") != SCHEMA or manifest.get("repository") != REPOSITORY or manifest.get("version") != tag[1:]:
        raise ValueError("validation manifest schema, repository or version differs")
    run = _current(manifest["run"])
    if run["head_sha"] != commit or manifest.get("repository_id") != run["repository"]["id"]:
        raise ValueError("validation manifest source association differs")
    if validate_jobs(POLICY, run, _jobs(run)) != manifest.get("jobs"):
        raise ValueError("validation results changed or belong to another attempt")
    if manifest.get("files") != verify_distributions(dist, tag):
        raise ValueError("distribution bytes differ from signed main validation")
    artifact = manifest["artifact"]
    if _artifact(_available_artifact(artifact["id"]), run, f"distributions-{run['run_attempt']}") != artifact:
        raise ValueError("origin artifact identity changed")
    evidence = document["evidence_artifact"]
    if _artifact(_available_artifact(evidence["id"]), run, f"validated-evidence-{run['run_attempt']}") != evidence:
        raise ValueError("validation evidence artifact identity changed")
    sealing = [job for job in _jobs(run) if job.get("name") == "release-evidence"]
    if (
        len(sealing) != 1
        or sealing[0].get("status") != "completed"
        or sealing[0].get("conclusion") != "success"
        or sealing[0].get("run_attempt") != run["run_attempt"]
    ):
        raise ValueError("main validation evidence job did not succeed in the selected attempt")
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary)
        (path / MANIFEST).write_bytes(manifest_text.encode("utf-8"))
        (path / VALIDATION_BUNDLE).write_bytes(bundle_text.encode("utf-8"))
        _signature(path / MANIFEST, path / VALIDATION_BUNDLE, commit, POLICY.workflow, "refs/heads/main", run["id"], run["run_attempt"])
    _remote_tag(tag, commit)
    _current(run)


def _retrieve_evidence(tag: str, commit: str, dist: Path, *, attempts: int, interval: int) -> dict[str, Any]:
    """Share exact signed main validation between local inspection and promotion."""
    distribution_names(tag)
    _commit(commit)
    _remote_tag(tag, commit)
    run = select_main_run(POLICY, commit, api, attempts=attempts, interval=interval)
    validate_jobs(POLICY, run, _jobs(run))
    artifact = _find_artifact(run, f"distributions-{run['run_attempt']}")
    proof = _find_artifact(run, f"validated-evidence-{run['run_attempt']}")
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "evidence"
        download_artifact(REPOSITORY, proof, path, (MANIFEST, VALIDATION_BUNDLE))
        document = {
            "tag": tag,
            "commit": commit,
            "manifest": (path / MANIFEST).read_bytes().decode("utf-8"),
            "validation_bundle": (path / VALIDATION_BUNDLE).read_bytes().decode("utf-8"),
            "evidence_artifact": proof,
        }
    manifest = _json(document["manifest"])
    if manifest.get("artifact") != artifact or manifest.get("run") != _identity(run):
        raise ValueError("signed validation does not describe the selected run and artifact")
    download_artifact(REPOSITORY, artifact, dist, distribution_names(tag))
    _validate_evidence(document, tag, commit, dist)
    return document


def _checkout_identity(root: Path) -> tuple[str, str]:
    executable, environment = git_context(root)

    def git_bytes(args: list[str]) -> bytes:
        return subprocess.run([str(executable), *args], cwd=root, env=environment, check=True, capture_output=True, timeout=30).stdout

    def git(*args: str) -> str:
        return git_bytes([*READ_ONLY_OPTIONS, *args]).decode("utf-8").strip()

    if Path(git("rev-parse", "--show-toplevel")).resolve() != root.resolve():
        raise ValueError("release evidence requires the repository root")
    commit = _commit(git("rev-parse", "HEAD"))
    try:
        require_checkout(commit, git_bytes, executable=executable)
    except ValueError as error:
        raise ValueError(f"release evidence requires a clean checkout: {error}") from error
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project["name"] != "research-repo-tools":
        raise ValueError("release evidence requires the research-repo-tools package")
    tag = f"v{project['version']}"
    distribution_names(tag)
    return tag, commit


def inspect_evidence(root: Path) -> dict[str, Any]:
    """Inspect this clean checkout's signed distributions without persistent writes."""
    tag, commit = _checkout_identity(root)
    with tempfile.TemporaryDirectory(prefix="research-repo-tools-evidence-") as temporary:
        document = _retrieve_evidence(tag, commit, Path(temporary) / "dist", attempts=1, interval=0)
        if _checkout_identity(root) != (tag, commit):
            raise ValueError("checkout changed during evidence inspection; retry from the reviewed release commit")
    manifest = _json(document["manifest"])
    return {
        "tag": tag,
        "commit": commit,
        "run": manifest["run"],
        "artifact": manifest["artifact"],
        "evidence_artifact": document["evidence_artifact"],
        "files": manifest["files"],
    }


def promote(tag: str, commit: str, dist: Path, evidence: Path, *, attempts: int = _DEFAULT_WAIT_ATTEMPTS, interval: int = _DEFAULT_WAIT_INTERVAL) -> None:
    promotion_run = _integer(int(os.environ.get("GITHUB_RUN_ID", "0")), "promotion run")
    promotion_attempt = _integer(int(os.environ.get("GITHUB_RUN_ATTEMPT", "0")), "promotion attempt")
    with tempfile.TemporaryDirectory() as temporary:
        candidate = Path(temporary) / "dist"
        document = _retrieve_evidence(tag, commit, candidate, attempts=attempts, interval=interval)
        document.update(promotion_run=promotion_run, promotion_attempt=promotion_attempt)
        contents = {name: (candidate / name).read_bytes() for name in distribution_names(tag)}
        _publish_distributions(dist, contents, evidence=(evidence, _bytes(document)))
    manifest = _json(document["manifest"])
    artifact, run = manifest["artifact"], manifest["run"]
    print(
        f"Reused {artifact['id']} ({artifact['digest']}) from {REPOSITORY}/actions/runs/{run['id']}/attempts/{run['run_attempt']}; no build or test matrix ran during promotion."
    )


def require_release(release: dict[str, Any], tag: str, *, draft: bool) -> None:
    _object(release, "release")
    if release.get("tag_name") != tag or release.get("draft") is not draft or release.get("prerelease") is not False:
        raise ValueError(f"expected {'draft' if draft else 'published'} stable release for {tag}")


def _release_identity(release: dict[str, Any]) -> dict[str, Any]:
    """Exclude mutable download counters; retain every publication input."""
    return {
        **{key: release.get(key) for key in ("id", "tag_name", "target_commitish", "draft", "prerelease", "immutable", "body")},
        "assets": [{key: asset.get(key) for key in ("id", "name", "size", "state", "digest", "updated_at")} for asset in release["assets"]],
    }


def stage(tag: str, dist: Path, bundle: Path, evidence: Path) -> None:
    document = _object(_json(evidence.read_bytes()), "promotion evidence")
    commit = _commit(document["commit"])
    _validate_evidence(document, tag, commit, dist)
    for name in distribution_names(tag):
        _signature(
            dist / name,
            bundle,
            commit,
            ".github/workflows/prepare-release.yml",
            f"refs/tags/{tag}",
            document["promotion_run"],
            document["promotion_attempt"],
            predicate=document,
        )
    pages = _json(gh("api", "--paginate", "--slurp", f"repos/{REPOSITORY}/releases?per_page=100"))
    matches = [release for page in pages for release in page if release["tag_name"] == tag]
    if len(matches) > 1:
        raise ValueError("multiple releases name the target tag")
    if matches:
        require_release(matches[0], tag, draft=True)
        if matches[0].get("assets"):
            raise ValueError("draft already has assets; inspect and remove the incomplete draft before retrying; assets are never overwritten")
    _remote_tag(tag, commit)
    attachment = dist.parent / BUNDLE
    if attachment.exists() or attachment.is_symlink() or attachment.is_junction():
        raise FileExistsError(f"release attachment already exists: {attachment}")
    replace_many({attachment: _bytes({"schema": PROMOTION, "evidence": document, "promotion_bundle": bundle.read_bytes().decode("utf-8")})})
    try:
        if not matches:
            gh("release", "create", tag, "--draft", "--verify-tag", "--title", tag, "--notes-from-tag")
        gh("release", "upload", tag, "--repo", REPOSITORY, *(str(dist / name) for name in distribution_names(tag)), str(attachment))
    except BaseException as error:
        try:
            attachment.unlink()
        except OSError as cleanup:
            raise BaseExceptionGroup("release staging and attachment cleanup failed", [error, cleanup]) from None
        raise
    print(f"Draft {tag} contains the same validated wheel/sdist and signed main-validation plus promotion provenance. Review before publishing.")


def verify(event: dict[str, Any], commit: str, output: Path) -> None:
    _object(event, "event")
    repository = _object(event.get("repository"), "event.repository")
    if event.get("action") != "published" or repository.get("full_name") != REPOSITORY:
        raise ValueError("expected a published release event from the owning repository")
    _commit(commit)
    released = _object(event.get("release"), "event.release")
    tag = released["tag_name"]
    names = distribution_names(tag)
    require_release(released, tag, draft=False)
    release_id = _integer(released["id"], "release ID")
    _remote_tag(tag, commit)
    current = api(f"releases/{release_id}")
    require_release(current, tag, draft=False)
    assets = current["assets"]
    if current.get("id") != release_id or len(assets) != 3 or {asset["name"] for asset in assets} != {*names, BUNDLE}:
        raise ValueError("release must contain exactly the wheel, sdist, and signed provenance")
    identifiers = [_integer(asset["id"], "release asset ID") for asset in assets]
    if len(set(identifiers)) != 3:
        raise ValueError("release asset IDs must be unique")
    proof = output.parent / BUNDLE
    if proof.exists() or proof.is_symlink() or proof.is_junction():
        raise FileExistsError(f"release proof already exists: {proof}")
    with tempfile.TemporaryDirectory() as temporary:
        candidate = Path(temporary) / "dist"
        candidate.mkdir()
        bundle = Path(temporary) / BUNDLE
        for asset in assets:
            target = bundle if asset["name"] == BUNDLE else candidate / asset["name"]
            with target.open("xb") as stream:
                subprocess.run(
                    [_GH_EXECUTABLE.get(), "api", f"repos/{REPOSITORY}/releases/assets/{asset['id']}", "-H", "Accept: application/octet-stream"],
                    stdout=stream,
                    stderr=subprocess.PIPE,
                    check=True,
                    timeout=120,
                )
            if target.stat().st_size > MAX_BYTES or f"sha256:{_sha(target.read_bytes())}" != asset.get("digest"):
                raise ValueError("downloaded release asset differs from its authoritative digest")
        envelope = _object(_json(bundle.read_bytes()), "promotion envelope")
        if envelope.get("schema") != PROMOTION:
            raise ValueError("expected signed validation and release-promotion provenance")
        document = _object(envelope.get("evidence"), "promotion evidence")
        signed = Path(temporary) / "promotion.sigstore.json"
        signed.write_bytes(envelope["promotion_bundle"].encode("utf-8"))
        for name in names:
            _signature(
                candidate / name,
                signed,
                commit,
                ".github/workflows/prepare-release.yml",
                f"refs/tags/{tag}",
                document["promotion_run"],
                document["promotion_attempt"],
                predicate=document,
            )
        _validate_evidence(document, tag, commit, candidate)
        if _release_identity(api(f"releases/{release_id}")) != _release_identity(current):
            raise ValueError("release metadata or assets changed during verification; inspect before retrying")
        _publish_distributions(output, {name: (candidate / name).read_bytes() for name in names}, evidence=(proof, bundle.read_bytes()))
    print(f"Verified main-validation and promotion provenance for {tag} at {commit}; ready for the approval-gated upload.")


def redact_release_diagnostic(value: str | bytes) -> str:
    """Remove URL credentials, queries/fragments and recognizable GitHub tokens.

    Apply to rendered subprocess diagnostics before logging them. This cannot
    identify arbitrary secrets in free text; never include tokens in commands.
    """
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")

    def url(match: re.Match[str]) -> str:
        try:
            parsed = urlsplit(match[0])
            if not parsed.hostname:
                return "<redacted-url>"
            suffix = "?<redacted>" if parsed.query or parsed.fragment or parsed.username or parsed.password else ""
            return f"{parsed.scheme}://{parsed.hostname}{parsed.path}{suffix}"
        except ValueError:
            return "<redacted-url>"

    value = re.sub(r"https?://[^\s\"<>]+", url, value)
    return re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b", "<redacted-token>", value).strip()


_diagnostic = redact_release_diagnostic


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("consume")
    command.add_argument("--artifact", type=int, required=True)
    command.add_argument("--dist", type=Path, default=Path("dist"))
    command = commands.add_parser("evidence", help="inspect the clean checkout's retained signed distributions without rebuilding or uploading")
    command.add_argument("--format", choices=("json", "summary"), default="json", help="presentation only; JSON remains the automation default")
    command = commands.add_parser("promote")
    command.add_argument("tag")
    command.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""))
    command.add_argument("--dist", type=Path, default=Path("dist"))
    command.add_argument("--evidence", type=Path, default=Path("promotion.json"))
    command.add_argument("--attempts", type=int, default=_DEFAULT_WAIT_ATTEMPTS, help="maximum validation polls (default: 241)")
    command.add_argument("--interval", type=int, default=_DEFAULT_WAIT_INTERVAL, help="seconds between validation polls (default: 30)")
    command = commands.add_parser("seal")
    command.add_argument("--run", type=int, default=os.environ.get("GITHUB_RUN_ID", "0"))
    command.add_argument("--attempt", type=int, default=os.environ.get("GITHUB_RUN_ATTEMPT", "0"))
    command.add_argument("--artifact", type=int, required=True)
    command.add_argument("--output", type=Path, default=Path(MANIFEST))
    command = commands.add_parser("stage")
    command.add_argument("tag")
    command.add_argument("--dist", type=Path, default=Path("dist"))
    command.add_argument("--bundle", type=Path, required=True)
    command.add_argument("--evidence", type=Path, default=Path("promotion.json"))
    command = commands.add_parser("verify")
    command.add_argument("--event", type=Path, default=Path(os.environ.get("GITHUB_EVENT_PATH", "event.json")))
    command.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""))
    command.add_argument("--output", type=Path, default=Path("verified-dist"))
    args = parser.parse_args()
    try:
        if args.command == "consume":
            consume(args.artifact, args.dist)
        elif args.command == "evidence":
            result = inspect_evidence(Path.cwd())
            if args.format == "summary":
                print(console_text(render_distribution_summary(result), sys.stdout), end="")
            else:
                write_stdout(_bytes(result))
        elif args.command == "seal":
            seal(args.run, args.attempt, args.artifact, args.output)
        elif args.command == "promote":
            promote(args.tag, args.commit, args.dist, args.evidence, attempts=args.attempts, interval=args.interval)
        elif args.command == "stage":
            stage(args.tag, args.dist, args.bundle, args.evidence)
        else:
            verify(_json(args.event.read_bytes()), args.commit, args.output)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile, tarfile.TarError, ExceptionGroup) as error:
        print(console_text(f"Release assets failed: {_diagnostic(format_exception_diagnostics(error))}", sys.stderr), file=sys.stderr)
        return 1
    return 0
