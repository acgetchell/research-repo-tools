"""Authenticated inert tag-to-draft composition using the existing draft writer."""

import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any

from research_repo_tools.changelog import notes
from research_repo_tools.config import Config
from research_repo_tools.evidence import _digest, _object, _string, deterministic_json
from research_repo_tools.files import publish_directory, replace_many
from research_repo_tools.release_assets import ReleaseTarget, serialize_release_target
from research_repo_tools.release_drafts import _Assertions, _prepare_release_draft, _regular_receipt, _require_draft
from research_repo_tools.release_evidence import _run_identity, verify_release_evidence
from research_repo_tools.release_publishing import RequiredCheck, _settings, check_release_source
from research_repo_tools.release_receipts import TagPushPolicy, TagSource, _current, _discover, _workflow_source
from research_repo_tools.release_validation import (
    _artifact_identity,
    _executing_run,
    _integer,
    _validate_jobs,
    api,
    download_artifact,
    parse_release_json,
    read_pages,
)

__all__ = ["DraftHandoffArtifact", "plan_release_draft", "prepare_authenticated_draft"]

SCHEMA = "research-repo-tools/draft-handoff/v1"
LIMIT = 4 * 1024 * 1024


def _text_bytes(value: object, context: str) -> bytes:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError(f"{context} requires nonempty UTF-8 text without NUL")
    return value.encode("utf-8")


def _read(path: Path) -> bytes:
    _regular_receipt(path)
    with path.open("rb") as stream:
        payload = stream.read(LIMIT + 1)
    if not 0 < len(payload) <= LIMIT:
        raise ValueError("draft handoff exceeds its nonempty byte bound")
    return payload


def plan_release_draft(config: Config, evidence_policy: Path, source: TagSource, *, event: str, expected: bytes | None = None) -> bytes:
    """Read-only source verification; freeze notes and the exact signed selection.

    ``expected`` is the original authenticated/local handoff when repeating this
    operation. It pins the original native run instead of selecting a new one.
    """
    from research_repo_tools.release_interfaces import _checkout, load_evidence_policy

    if event != "push":
        raise ValueError("draft planning requires an independently asserted tag-push event")
    settings = _settings(config)
    original = _read(evidence_policy)
    policy = load_evidence_policy(evidence_policy)
    if policy.validation.repository != settings.repository or policy.package != settings.package or policy.signing_job is None:
        raise ValueError("draft planning requires matching repository/package and signed evidence policy")
    pinned = None if expected is None else _object(parse_release_json(expected), "original draft handoff")
    selection = None if pinned is None else deterministic_json(pinned.get("selection"))
    _checkout(config.root, policy.package, source.tag[1:], source.commit, path_in_vcs=policy.path_in_vcs, package_kind=policy.package_kind)
    check_release_source(config, source.tag, commit=source.commit)
    generated = notes(config, source.tag)[0]
    verified = verify_release_evidence(policy, source.tag[1:], source.commit, partial(api, repository=settings.repository), expected=selection)
    _Assertions(settings.repository, source.tag, source.commit, source.tag, generated.encode("utf-8"), verified)
    result = deterministic_json(
        {
            "schema": SCHEMA,
            "repository": settings.repository,
            "source": asdict(source),
            "event": event,
            "title": source.tag,
            "notes": generated,
            "evidence_policy": original.decode("utf-8"),
            "selection": parse_release_json(verified),
            "checks": [asdict(check) for check in settings.required_checks],
        }
    )
    if len(result) > LIMIT:
        raise ValueError("draft handoff exceeds its byte bound")
    check_release_source(config, source.tag, commit=source.commit)
    if _read(evidence_policy) != original or notes(config, source.tag)[0] != generated:
        raise ValueError("draft policy or generated notes changed during planning")
    if expected is not None and result != expected:
        raise ValueError("draft handoff changed; retain the original notes and evidence selection")
    return result


@dataclass(frozen=True, slots=True)
class DraftHandoffArtifact:
    """Independent original producer and immutable artifact assertions."""

    run_id: int
    attempt: int
    artifact_id: int
    sha256: str

    def __post_init__(self) -> None:
        _integer(self.run_id, "handoff run ID")
        _integer(self.artifact_id, "handoff artifact ID")
        if type(self.attempt) is not int or self.attempt != 1:
            raise ValueError("draft handoff requires its original attempt 1; recover original inputs instead of rerunning verification")
        _digest(self.sha256, "handoff artifact SHA-256")


def _producer(policy: TagPushPolicy, source: TagSource, selected: DraftHandoffArtifact, read: Callable[[str], Any]) -> dict[str, Any]:
    workflow = _object(read(f"actions/workflows/{PurePosixPath(policy.workflow).name}"), "handoff workflow")
    run = _current(policy, source, selected.run_id, workflow, read, completed=False)
    provenance = _workflow_source(policy, source, read)
    jobs = _validate_jobs(
        policy.required_steps,
        policy.runner_labels,
        run,
        read_pages(read, f"actions/runs/{selected.run_id}/attempts/{selected.attempt}/jobs", "jobs"),
        recovery="Recover the original authenticated handoff and journal.",
    )
    artifact = _artifact_identity(
        _object(read(f"actions/artifacts/{selected.artifact_id}"), "handoff artifact"),
        run,
        f"{policy.artifact_prefix}-{selected.run_id}-{selected.attempt}",
        max_bytes=LIMIT,
        recovery="Recover the original authenticated handoff and journal.",
    )
    if artifact["id"] != selected.artifact_id or artifact["digest"] != "sha256:" + selected.sha256:
        raise ValueError("handoff artifact differs from independent ID/digest assertions")
    return {
        "policy": asdict(policy),
        "source": asdict(source),
        "producer": asdict(selected),
        "run": _run_identity(run),
        "workflow_source": provenance,
        "jobs": jobs,
        "artifact": artifact,
    }


def prepare_authenticated_draft(
    root: Path,
    policy: TagPushPolicy,
    source: TagSource,
    selected: DraftHandoffArtifact,
    *,
    output: Path,
    executing_run_id: int,
    executing_attempt: int,
    approve: bool = False,
    recover_release_id: int | None = None,
) -> ReleaseTarget:
    """Authenticate one handoff, retain it durably, then compose the draft writer.

    New state is allowed only in the original tag run's first attempt with unique
    retained workflow history. Every retry restores the complete original output
    directory, including its journal. A missing journal never means new intent.
    Keep this storage durable and serialize all writers for the repository/tag.
    """
    from research_repo_tools.release_interfaces import load_evidence_policy

    if approve is not True:
        raise ValueError("authenticated draft preparation requires explicit approval")
    root = root.resolve(strict=True)
    if any((parent / ".git").exists() for parent in (root, *root.parents)):
        raise ValueError("authenticated draft writer must run outside every source checkout")
    _integer(executing_run_id, "executing run ID")
    _integer(executing_attempt, "executing attempt")
    output = output.parent.resolve(strict=True) / output.name
    if output.is_symlink() or output.is_junction() or (output.exists() and not output.is_dir()):
        raise ValueError("draft state must be an owned directory without links")
    executing = _executing_run(policy.repository)
    if (executing["id"], executing["run_attempt"]) != (executing_run_id, executing_attempt):
        raise ValueError("executing run identity differs from the independently supplied run and attempt")
    read = partial(api, repository=policy.repository)
    observed = _producer(policy, source, selected, read)
    frozen = deterministic_json(observed)
    if len(frozen) > LIMIT:
        raise ValueError("draft producer record exceeds its byte bound")
    fresh = not output.exists()
    if fresh:
        if (executing_run_id, executing_attempt) != (selected.run_id, 1) or recover_release_id is not None:
            raise ValueError("new runs or attempts require the original handoff and journal; never create fresh retry state")
        workflow = _object(read(f"actions/workflows/{PurePosixPath(policy.workflow).name}"), "handoff workflow")
        if _discover(policy, source, workflow, read) != selected.run_id:
            raise ValueError("draft tag has another preparation intent; recover its original journal")
    else:
        if _read(output / "producer.json") != frozen:
            raise ValueError("retained handoff producer differs; never replace the original selection")
        if not (output / "draft-receipt.json").is_file():
            raise ValueError("original draft journal is missing; reconcile provider state before any further creation")
    with tempfile.TemporaryDirectory(prefix="research-draft-handoff-") as temporary:
        downloaded = Path(temporary) / "downloaded"
        download_artifact(policy.repository, observed["artifact"], downloaded, ("handoff.json",), max_bytes=LIMIT, max_expanded_bytes=LIMIT)
        payload = _read(downloaded / "handoff.json")
        record = _object(
            parse_release_json(payload),
            "draft handoff",
            {"schema", "repository", "source", "event", "title", "notes", "evidence_policy", "selection", "checks"},
        )
        if (
            record["schema"] != SCHEMA
            or record["repository"] != policy.repository
            or record["source"] != asdict(source)
            or record["event"] != "push"
            or record["title"] != source.tag
            or payload != deterministic_json(record)
        ):
            raise ValueError("authenticated handoff differs from independent source/event assertions")
        configuration = Path(temporary) / "evidence.toml"
        configuration.write_bytes(_text_bytes(record["evidence_policy"], "handoff evidence policy"))
        evidence_policy = load_evidence_policy(configuration)
        assertions = _Assertions(
            policy.repository, source.tag, source.commit, source.tag, _text_bytes(record["notes"], "draft notes"), deterministic_json(record["selection"])
        )
        checks_raw = record["checks"]
        if not isinstance(checks_raw, list) or not checks_raw:
            raise ValueError("handoff requires declared source checks")
        checks = []
        for item in checks_raw:
            check = _object(item, "source check", {"name", "app_id"})
            checks.append(RequiredCheck(_string(check["name"], "required check"), _integer(check["app_id"], "check app ID")))

        def unchanged() -> None:
            if deterministic_json(_producer(policy, source, selected, read)) != frozen:
                raise ValueError("handoff producer, gates or artifact changed during preparation")
            if fresh:
                current_workflow = _object(read(f"actions/workflows/{PurePosixPath(policy.workflow).name}"), "handoff workflow")
                if _discover(policy, source, current_workflow, read) != selected.run_id:
                    raise ValueError("draft preparation history changed; reconcile the original intent")
            if output.exists() and (_read(output / "producer.json") != frozen or _read(output / "handoff.json") != payload):
                raise ValueError("retained draft handoff changed during preparation")

        unchanged()

        def reserve_receipt(journal: bytes) -> None:
            if output.exists():
                raise ValueError("draft state appeared before journal reservation; preserve it for reconciliation")
            with publish_directory(output) as candidate:
                (candidate / "producer.json").write_bytes(frozen)
                (candidate / "handoff.json").write_bytes(payload)
                (candidate / "draft-receipt.json").write_bytes(journal)
                # The shared writer just ran its final provider/tag checks.
                # Reservation performs only durable local writes before POST.
                if output.exists():
                    raise ValueError("draft state appeared during journal reservation; preserve it for reconciliation")

        if not fresh and _read(output / "handoff.json") != payload:
            raise ValueError("downloaded handoff differs from original retained bytes")
        target = _prepare_release_draft(
            root,
            policy.repository,
            source.tag,
            source.commit,
            policy=evidence_policy,
            expected=assertions.selection,
            title=source.tag,
            notes=assertions.notes,
            receipt=output / "draft-receipt.json",
            approve=True,
            recover_release_id=recover_release_id,
            source_checks=tuple(checks),
            verify_inputs=unchanged,
            reserve_receipt=reserve_receipt if fresh else None,
        )
        target_bytes = serialize_release_target(target)
        if (output / "target.json").exists() and _read(output / "target.json") != target_bytes:
            raise ValueError("original draft target differs; preserve the journal")

        def verify_target() -> None:
            unchanged()
            _require_draft(root, assertions, target)

        replace_many({output / "target.json": target_bytes}, verify=verify_target)
        return target
