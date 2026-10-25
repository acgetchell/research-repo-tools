"""Exact-commit release evidence and immutable approval handoffs.

Signed source validation and retained Cargo archives are different guarantees.
This module never obtains publishing credentials, executes consumer code, builds
packages, signs documents, or uploads to a registry. Consumers own those gates.
"""

import gzip
import hashlib
import io
import re
import tarfile
import tempfile
import tomllib
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from research_repo_tools.evidence import deterministic_json
from research_repo_tools.release_validation import (
    ValidationPolicy,
    artifact_identity,
    download_artifact,
    parse_release_json,
    read_pages,
    select_main_run,
    validate_jobs,
    validate_run,
    verify_attestation,
)

__all__ = ["EvidencePolicy", "create_evidence_manifest", "verify_cargo_archive", "verify_release_evidence"]

MANIFEST = "validation.json"
BUNDLE = "validation.sigstore.json"
SCHEMA = "research-repo-tools/release-evidence/v1"
SELECTION_SCHEMA = "research-repo-tools/release-selection/v1"
MAX_CRATE_BYTES = 64 * 1024 * 1024
MAX_CRATE_MEMBERS = 4096
MAX_METADATA_BYTES = 1024 * 1024


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("expected a release evidence object")
    return value


def _positive(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("expected a positive evidence identity")
    return value


def _commit(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("expected a full lowercase commit SHA")
    return value


def _version(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value) is None:
        raise ValueError("release evidence requires a stable X.Y.Z version")
    return value


def _package(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", value) is None:
        raise ValueError("expected a portable Cargo package name")
    return value


def _relative(value: str) -> str:
    if (
        not isinstance(value, str)
        or "\\" in value
        or ":" in value
        or (value and (PurePosixPath(value).is_absolute() or str(PurePosixPath(value)) != value or ".." in PurePosixPath(value).parts))
    ):
        raise ValueError("path_in_vcs must be an empty or canonical relative POSIX path")
    return value


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    """Consumer-owned gates and artifact/signature policy, captured in each record.

    Artifact names are PREFIX-RUN_ID-ATTEMPT. Signed source evidence requires a
    separate signing job; its successful steps are checked only when consuming
    the completed run. Cargo gates must describe the actual retained archive.
    """

    validation: ValidationPolicy
    package: str
    artifact_prefix: str
    guarantee: Literal["signed-source", "cargo-archive"]
    signing_job: tuple[str, tuple[str, ...]] | None = None
    path_in_vcs: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.validation, ValidationPolicy):
            raise TypeError("validation must be a ValidationPolicy")
        _package(self.package)
        _relative(self.path_in_vcs)
        if not isinstance(self.artifact_prefix, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", self.artifact_prefix) is None:
            raise ValueError("artifact prefix must contain portable letters, digits, hyphens or underscores")
        if self.guarantee not in ("signed-source", "cargo-archive"):
            raise ValueError("unknown release evidence guarantee")
        if self.guarantee == "signed-source" and self.signing_job is None:
            raise ValueError("signed source validation requires a signing job")
        if self.signing_job is not None:
            name, steps = self.signing_job
            object.__setattr__(self, "signing_job", (name, tuple(steps)))
            replace(self.validation, required_steps=(self.signing_job,), runner_labels=())
            if name in dict(self.validation.required_steps):
                raise ValueError("signing job must be separate from the jobs it attests")


def _policy(policy: EvidencePolicy) -> dict[str, Any]:
    validation = policy.validation
    return {
        "repository": validation.repository,
        "workflow": validation.workflow,
        "workflow_name": validation.workflow_name,
        "ref": f"refs/heads/{validation.branch}",
        "event": "push",
        "required_steps": {name: list(steps) for name, steps in validation.required_steps},
        "runner_labels": {name: list(labels) for name, labels in validation.runner_labels},
        "artifact_prefix": policy.artifact_prefix,
        "guarantee": policy.guarantee,
        "signing_job": None if policy.signing_job is None else {policy.signing_job[0]: list(policy.signing_job[1])},
        "path_in_vcs": policy.path_in_vcs,
    }


def _run_identity(run: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: run[key] for key in ("id", "run_attempt", "head_sha", "head_branch", "workflow_id", "path", "name", "event")},
        "repository_id": run["repository"]["id"],
        "head_repository_id": run["head_repository"]["id"],
    }


def _current(policy: EvidencePolicy, commit: str, identifier: int, attempt: int, read: Callable[[str], Any], *, completed: bool = True) -> dict[str, Any]:
    identifier, attempt = _positive(identifier), _positive(attempt)
    run = _object(read(f"actions/runs/{identifier}"))
    workflow = _object(read(f"actions/workflows/{PurePosixPath(policy.validation.workflow).name}"))
    validate_run(policy.validation, run, commit, workflow, completed=completed)
    if (run["id"], run["run_attempt"]) != (identifier, attempt):
        raise ValueError("selected run or attempt changed; inspect the original evidence before retrying")
    return run


def _jobs(policy: EvidencePolicy, run: dict[str, Any], read: Callable[[str], Any], *, signing: bool) -> dict[str, int]:
    jobs = read_pages(read, f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", "jobs")
    validation = policy.validation
    if signing and policy.signing_job is not None:
        validation = replace(validation, required_steps=(*validation.required_steps, policy.signing_job))
    return validate_jobs(validation, run, jobs)


def _tools(value: Mapping[str, str]) -> dict[str, str]:
    if set(value) != {"cargo", "rustc"} or any(not isinstance(item, str) or not item.strip() for item in value.values()):  # ty: ignore[redundant-condition-strict]  Validate untyped manifest tools.
        raise ValueError("Cargo evidence requires nonempty cargo and rustc tool identities")
    return dict(value)


def _bounded_file(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("evidence must be a regular file, not a link")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if not 0 < len(data) <= limit:
        raise ValueError("evidence file exceeds the nonempty byte bound")
    return data


def _crate(payload: bytes, package: str, version: str, commit: str, path_in_vcs: str) -> str:
    prefix = f"{_package(package)}-{_version(version)}"
    _commit(commit)
    _relative(path_in_vcs)
    with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
        expanded = compressed.read(MAX_CRATE_BYTES + 1)
    if len(expanded) > MAX_CRATE_BYTES:
        raise ValueError("Cargo archive expanded size exceeds the release bound")
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
        names: set[str] = set()
        metadata: dict[str, bytes] = {}
        for member in archive:
            name = PurePosixPath(member.name)
            if len(names) >= MAX_CRATE_MEMBERS:
                raise ValueError("Cargo archive inventory exceeds the release bound")
            if (
                member.name in names
                or not member.isfile()
                or member.sparse is not None
                or not member.name.startswith(f"{prefix}/")
                or ".." in name.parts
                or str(name) != member.name
                or "\\" in member.name
                or not 0 <= member.size <= len(expanded) - member.offset_data
            ):
                raise ValueError("Cargo archive must contain unique regular package files with safe paths")
            names.add(member.name)
            if member.name in (f"{prefix}/.cargo_vcs_info.json", f"{prefix}/Cargo.toml"):
                if member.size > MAX_METADATA_BYTES:
                    raise ValueError("Cargo archive metadata exceeds the release bound")
                content = archive.extractfile(member)
                if content is not None:
                    metadata[name.name] = content.read()
        if set(metadata) != {".cargo_vcs_info.json", "Cargo.toml"}:
            raise ValueError("Cargo archive lacks source or package metadata")
    identity = _object(parse_release_json(metadata[".cargo_vcs_info.json"]))
    packaged = _object(tomllib.loads(metadata["Cargo.toml"].decode("utf-8")).get("package"))
    git = _object(identity.get("git"))
    if (
        git.get("sha1") != commit
        or git.get("dirty", False) is not False
        or identity.get("path_in_vcs") != path_in_vcs
        or (packaged.get("name"), packaged.get("version")) != (package, version)
    ):
        raise ValueError("Cargo archive source/package/version differs from the clean release identity")
    return hashlib.sha256(payload).hexdigest()


def verify_cargo_archive(archive: Path, package: str, version: str, commit: str, *, path_in_vcs: str = "") -> str:
    """Inspect bounded gzip/tar and source/package metadata without extraction.

    Return the original archive's SHA-256. This is an integrity/identity check;
    it does not prove native tests, feature checks, signatures or registry upload.
    """
    payload = _bounded_file(archive, MAX_CRATE_BYTES)
    try:
        return _crate(payload, package, version, commit, path_in_vcs)
    except (OSError, EOFError, zlib.error, tarfile.TarError) as error:
        raise ValueError("Cargo archive is not a readable gzip/tar package") from error


def _manifest(policy: EvidencePolicy, version: str, run: dict[str, Any]) -> dict[str, Any]:
    return {"schema": SCHEMA, "policy": _policy(policy), "package": {"name": policy.package, "version": _version(version)}, "run": _run_identity(run)}


def create_evidence_manifest(
    policy: EvidencePolicy,
    version: str,
    commit: str,
    run_id: int,
    attempt: int,
    read: Callable[[str], Any],
    *,
    archive: Path | None = None,
    tools: Mapping[str, str] | None = None,
) -> bytes:
    """Create bytes to retain/sign in the executing trusted push attempt.

    The consumer independently observes checkout/package identity and passes its
    executing run/attempt. Signed-source creation requires completed validation
    jobs. Cargo creation checks an archive already verified by consumer-owned
    Cargo gates; completed gates are required when consuming the finished run.
    """
    _version(version)
    run = _current(policy, commit, run_id, attempt, read, completed=False)
    document = _manifest(policy, version, run)
    if policy.guarantee == "signed-source":
        if archive is not None or tools is not None:
            raise ValueError("signed source evidence does not establish Cargo archive identity")
        document["jobs"] = _jobs(policy, run, read, signing=False)
    else:
        if archive is None or tools is None:
            raise ValueError("Cargo evidence requires the retained archive and producer tools")
        document["archive"] = {
            "file": f"{policy.package}-{version}.crate",
            "sha256": verify_cargo_archive(archive, policy.package, version, commit, path_in_vcs=policy.path_in_vcs),
            "tools": _tools(tools),
        }
    if _run_identity(_current(policy, commit, run_id, attempt, read, completed=False)) != document["run"]:
        raise ValueError("run identity changed while creating evidence")
    return deterministic_json(document)


def _snapshot(policy: EvidencePolicy, version: str, run: dict[str, Any], read: Callable[[str], Any], pinned: dict[str, Any] | None) -> dict[str, Any]:
    name = f"{policy.artifact_prefix}-{run['id']}-{run['run_attempt']}"
    artifacts = read_pages(read, f"actions/runs/{run['id']}/artifacts", "artifacts")
    matches = [entry for entry in artifacts if entry.get("name") == name]
    if len(matches) != 1:
        raise ValueError("missing or ambiguous evidence artifact; inspect retention and the complete original attempt")
    artifact = artifact_identity(matches[0], run, name)
    if pinned is not None and artifact != _object(pinned.get("artifact")):
        raise ValueError("approved artifact identity changed")
    if artifact_identity(_object(read(f"actions/artifacts/{artifact['id']}")), run, name) != artifact:
        raise ValueError("artifact identity changed during verification")
    return {**_manifest(policy, version, run), "schema": SELECTION_SCHEMA, "jobs": _jobs(policy, run, read, signing=True), "artifact": artifact}


def verify_release_evidence(
    policy: EvidencePolicy,
    version: str,
    commit: str,
    read: Callable[[str], Any],
    *,
    expected: bytes | None = None,
    reproduced_archive: Path | None = None,
    reproduced_tools: Mapping[str, str] | None = None,
) -> bytes:
    """Verify one exact complete push; return a deterministic JSON approval record.

    Pass the saved bytes as expected after approval: no new run/attempt/artifact
    may replace that selection. Recheck the current workflow, gates, artifact and
    signature on every invocation. Cargo callers may additionally compare a
    credential-free reproduction and its tools before acquiring credentials.
    Source validation never implies byte-identical Cargo publication.
    """
    _commit(commit)
    _version(version)
    if (reproduced_archive is None) != (reproduced_tools is None) or (reproduced_archive is not None and policy.guarantee != "cargo-archive"):
        raise ValueError("Cargo reproduction requires both an archive and tool identities")
    pinned = _object(parse_release_json(expected)) if expected is not None else None
    if pinned is None:
        run = select_main_run(policy.validation, commit, read, attempts=1, interval=0)
    else:
        identity = _object(pinned.get("run"))
        run = _current(policy, commit, _positive(identity.get("id")), _positive(identity.get("run_attempt")), read)
    selected = _snapshot(policy, version, run, read, pinned)
    if pinned is not None and deterministic_json({key: value for key, value in pinned.items() if key != "manifest_sha256"}) != deterministic_json(selected):
        raise ValueError("approved evidence policy, source, jobs or run identity changed")
    names = (MANIFEST,)
    if policy.signing_job is not None:
        names += (BUNDLE,)
    if policy.guarantee == "cargo-archive":
        names += (f"{policy.package}-{version}.crate",)
    with tempfile.TemporaryDirectory(prefix="research-release-evidence-") as temporary:
        directory = Path(temporary) / "retained"
        download_artifact(policy.validation.repository, selected["artifact"], directory, names)
        manifest_bytes = _bounded_file(directory / MANIFEST, MAX_METADATA_BYTES)
        document = _object(parse_release_json(manifest_bytes))
        wanted = _manifest(policy, version, run)
        if policy.guarantee == "signed-source":
            wanted["jobs"] = {name: selected["jobs"][name] for name, _ in policy.validation.required_steps}
        else:
            archive = _object(document.get("archive"))
            recorded_tools = _tools(_object(archive.get("tools")))
            digest = verify_cargo_archive(directory / names[-1], policy.package, version, commit, path_in_vcs=policy.path_in_vcs)
            wanted["archive"] = {"file": names[-1], "sha256": digest, "tools": recorded_tools}
            if reproduced_archive is not None and reproduced_tools is not None:
                reproduced = verify_cargo_archive(reproduced_archive, policy.package, version, commit, path_in_vcs=policy.path_in_vcs)
                if reproduced != digest or _tools(reproduced_tools) != recorded_tools:
                    raise ValueError("Cargo reproduction bytes or tools differ from retained validation")
        if deterministic_json(document) != deterministic_json(wanted):
            raise ValueError("retained evidence differs from current exact source, policy, archive or job identities")
        if policy.signing_job is not None:
            verify_attestation(
                directory / MANIFEST,
                directory / BUNDLE,
                commit,
                policy.validation.workflow,
                f"refs/heads/{policy.validation.branch}",
                run["id"],
                run["run_attempt"],
                repository=policy.validation.repository,
            )
        current = _current(policy, commit, run["id"], run["run_attempt"], read)
        if _snapshot(policy, version, current, read, selected) != selected:
            raise ValueError("evidence changed during verification")
        selected["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    if pinned is not None and deterministic_json(pinned) != deterministic_json(selected):
        raise ValueError("approved evidence manifest changed")
    return deterministic_json(selected)
