"""Prepare an unpublished draft from a frozen signed-evidence selection.

Only inert records are consumed. No checkout, notes generator, build, signing,
asset upload or registry operation runs here. Keep exclusive control of receipt
storage and serialize every writer for a repository/tag across invocations.
"""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import assert_never

from research_repo_tools.evidence import _load_json, _object, deterministic_json
from research_repo_tools.files import replace_many
from research_repo_tools.release_assets import (
    ReleaseTarget,
    _expected_title,
    _gh,
    _parse_release,
    _release_by_id,
    _release_matches,
    _remote_commit,
    _repository,
    _require_identity,
    _require_response_identity,
    _sha,
    _stable_tag,
    require_draft,
)
from research_repo_tools.release_evidence import EvidencePolicy, verify_release_evidence
from research_repo_tools.release_validation import api

__all__ = ["DraftPreparationUnknownError", "prepare_release_draft"]

RECEIPT_SCHEMA = "research-repo-tools/draft-receipt/v1"


class DraftPreparationUnknownError(RuntimeError):
    """Creation may have happened; retain the receipt and inspect before recovery."""


@dataclass(frozen=True, slots=True)
class _Assertions:
    repository: str
    tag: str
    commit: str
    title: str
    notes: bytes
    selection: bytes

    def __post_init__(self) -> None:
        _repository(self.repository)
        _stable_tag(self.tag)
        _sha(self.commit)
        if _expected_title(self.title) is None:
            raise ValueError("draft preparation requires an expected title")
        for value, context in ((self.notes, "notes"), (self.selection, "evidence selection")):
            if not isinstance(value, bytes) or not 0 < len(value) <= 1024 * 1024:
                raise ValueError(f"draft {context} must be nonempty bytes within 1 MiB")
        if "\0" in self.notes.decode("utf-8"):
            raise ValueError("draft notes must be UTF-8 without NUL")

    def record(self, identifier: int | None) -> bytes:
        return deterministic_json(
            {
                "schema": RECEIPT_SCHEMA,
                "repository": self.repository,
                "tag": self.tag,
                "commit": self.commit,
                "title": self.title,
                "notes_sha256": hashlib.sha256(self.notes).hexdigest(),
                "selection_sha256": hashlib.sha256(self.selection).hexdigest(),
                "state": "pending" if identifier is None else "captured",
                "release_id": identifier,
            }
        )

    def target(self, identifier: object) -> ReleaseTarget:
        if type(identifier) is not int or identifier <= 0:
            raise ValueError("release ID must be a positive integer")
        return ReleaseTarget(self.repository, self.tag, identifier, self.commit, self.title)


def _regular_receipt(path: Path) -> None:
    if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_file()):
        raise ValueError("draft receipt must be a regular file, never a link or junction")


@dataclass(frozen=True, slots=True)
class _Fresh:
    """No prior intent exists; only this case can authorize a POST."""


@dataclass(frozen=True, slots=True)
class _Pending:
    payload: bytes


@dataclass(frozen=True, slots=True)
class _Captured:
    payload: bytes
    target: ReleaseTarget


type _PreparationState = _Fresh | _Pending | _Captured


def _parse_receipt(payload: bytes, assertions: _Assertions) -> _Pending | _Captured:
    # Equality with canonical reconstructed bytes checks every binding and field;
    # parsing alone never authenticates a receipt's origin.
    data = _object(_load_json(payload, "draft receipt"), "draft receipt")
    identifier = data.get("release_id")
    target = None if identifier is None else assertions.target(identifier)
    if deterministic_json(data) != assertions.record(None if target is None else target.release_id):
        raise ValueError("draft receipt differs from independent assertions, notes or evidence")
    return _Pending(payload) if target is None else _Captured(payload, target)


def _resume_target(state: _PreparationState, assertions: _Assertions, recover_release_id: int | None) -> ReleaseTarget | None:
    match state:
        case _Fresh():
            if recover_release_id is not None:
                raise ValueError("recovery requires the original pending receipt")
            return None
        case _Pending():
            if recover_release_id is None:
                raise DraftPreparationUnknownError(
                    "pending draft creation: retain receipt, inspect GitHub and explicitly recover the reviewed release ID; never create again"
                )
            return assertions.target(recover_release_id)
        case _Captured(target=target):
            if recover_release_id is not None and target.release_id != recover_release_id:
                raise ValueError("recovery cannot replace the captured release ID")
            return target
        case _:
            assert_never(state)


def _capture(path: Path, assertions: _Assertions, target: ReleaseTarget, previous: bytes | None) -> None:
    _regular_receipt(path)
    if previous is None:
        _reserve(path, assertions.record(target.release_id))
    else:
        replace_many({path: assertions.record(target.release_id)}, expected={path: previous})


def _reserve(path: Path, payload: bytes) -> None:
    # Exclusive creation leaves even a partial journal in place on failure. No
    # remote mutation may precede flush/fsync of the pending intent.
    _regular_receipt(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _require_draft(root: Path, assertions: _Assertions, target: ReleaseTarget) -> None:
    matches = _release_matches(root, assertions.repository, assertions.tag)
    if len(matches) != 1 or matches[0].get("id") != target.release_id:
        raise ValueError("draft disappeared, was replaced or has duplicate matches; retain the original receipt")
    release = _release_by_id(root, target.repository, target.tag, target.release_id)
    _require_identity(release, target)
    require_draft(release)
    if release.body is None or release.body.encode("utf-8") != assertions.notes:
        raise ValueError("draft body differs from generated note bytes")
    _require_tag(root, assertions)


def _require_tag(root: Path, assertions: _Assertions) -> None:
    if _remote_commit(root, assertions.repository, assertions.tag) != assertions.commit:
        raise ValueError("remote release tag differs from the independent commit assertion")


def prepare_release_draft(
    root: Path,
    repository: str,
    tag: str,
    commit: str,
    *,
    policy: EvidencePolicy,
    expected: bytes,
    title: str,
    notes: bytes,
    receipt: Path,
    approve: bool = False,
    recover_release_id: int | None = None,
) -> ReleaseTarget:
    """Create once or retain a matching stable draft; return its captured target.

    ``expected`` is the original verify_release_evidence selection, authenticated
    by the caller's immutable handoff. A signing policy is required. Revalidate
    that same selection live before and after mutation; never select a fallback.
    ``approve=True`` explicitly authorizes creation. Existing matching drafts
    and all assets remain untouched. Notes are compared as exact UTF-8 bytes.

    Keep the receipt across failures and retries. Pending intent blocks all new
    creates; only an independently reviewed ``recover_release_id`` can resolve
    it, without mutation. Captured IDs cannot be replaced. Validation failures
    raise ValueError; an unconfirmed creation raises DraftPreparationUnknownError.
    Receipt writes use fsync but do not provide distributed/crash-atomic storage.
    """
    assertions = _Assertions(repository, tag, commit, title, notes, expected)
    if approve is not True:
        raise ValueError("draft preparation requires explicit approve=True")
    if not isinstance(policy, EvidencePolicy) or policy.validation.repository != repository or policy.signing_job is None:
        raise ValueError("draft preparation requires a matching signed release evidence policy")
    if recover_release_id is not None:
        assertions.target(recover_release_id)  # Reject booleans and invalid IDs before effects.
    # Ordinary native parent aliases (including macOS /var -> /private/var)
    # select the same storage. Preserve the leaf so links still reject.
    receipt = receipt.parent.resolve(strict=True) / receipt.name
    _regular_receipt(receipt)
    state = _parse_receipt(receipt.read_bytes(), assertions) if receipt.exists() else _Fresh()
    target = _resume_target(state, assertions, recover_release_id)

    def evidence() -> None:
        verified = verify_release_evidence(policy, tag.removeprefix("v"), commit, partial(api, repository=repository), expected=expected)
        if verified != expected:
            raise ValueError("retained evidence selection changed")

    evidence()
    _require_tag(root, assertions)
    matches = _release_matches(root, repository, tag)
    if len(matches) > 1:
        raise ValueError("duplicate releases for the stable tag")
    if target is not None:
        _require_draft(root, assertions, target)
    elif matches:
        target = assertions.target(matches[0]["id"])
        _require_draft(root, assertions, target)
    else:
        # Stage the inert request before the final evidence/tag/absence checks.
        with tempfile.TemporaryDirectory(prefix="research-release-draft-") as temporary:
            request = Path(temporary) / "request.json"
            request.write_bytes(
                deterministic_json(
                    {"tag_name": tag, "target_commitish": commit, "name": title, "body": notes.decode("utf-8"), "draft": True, "prerelease": False}
                )
            )
            evidence()
            if _release_matches(root, repository, tag):
                raise ValueError("release appeared during preparation; inspect before retrying")
            _require_tag(root, assertions)
            pending = assertions.record(None)
            _reserve(receipt, pending)
            try:
                payload = _gh(root, ["api", "--method", "POST", f"repos/{repository}/releases", "--input", str(request)])
                data = _object(_load_json(payload, "draft creation response"), "release")
                candidate = assertions.target(data.get("id"))
                _require_response_identity(data, repository, tag, candidate.release_id)
                # Capture before any further network request: a subsequent failure
                # must never permit creation of a replacement release.
                _capture(receipt, assertions, candidate, pending)
                target = candidate
                _parse_release(data, repository, tag, candidate.release_id)
                _require_draft(root, assertions, target)
                evidence()
                _require_draft(root, assertions, target)
            except Exception as error:
                raise DraftPreparationUnknownError(
                    f"draft creation outcome unconfirmed; retain receipt {receipt} and inspect GitHub before recovery; no automatic retry or rollback"
                ) from error
            return target
    evidence()
    _require_draft(root, assertions, target)
    match state:
        case _Fresh():
            _capture(receipt, assertions, target, None)
        case _Pending(payload=previous):
            _capture(receipt, assertions, target, previous)
        case _Captured():
            pass
        case _:
            assert_never(state)
    return target
