"""Bounded, location-bound pending records and exclusive one-use receipts.

The caller controls this local state. It is not a security boundary against that
caller, filesystem tampering, raw CodeRabbit use, or server-side price changes.
"""

import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

from research_repo_tools._review_identity import ReviewIdentity
from research_repo_tools.evidence import _digest, _load_json, deterministic_json, sha256
from research_repo_tools.path_identity import RegularFile, observe_paths
from research_repo_tools.review_protocol import ConsentRequired, ReviewApproval, ReviewQuote

_SCHEMA = "research-repo-tools/pending-review/v1"
_MAX_RECORD_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PendingReview:
    path: Path
    identity: str
    root: Path
    worktree: Path
    base: str | None
    content: str
    quote: ReviewQuote


def _contained(parent: Path, worktree: Path) -> bool:
    # Both are existing, resolved directories. Identity also catches alternate
    # case/Unicode spellings that POSIX lexical path comparisons cannot detect.
    return any(ancestor.samefile(worktree) for ancestor in (parent, *parent.parents))


def temporary_parent(worktree: Path) -> Path:
    parent = Path(tempfile.gettempdir()).resolve(strict=True)
    if _contained(parent, worktree):
        raise ValueError("review temporary storage must be outside the Git worktree; select an outside system temporary directory")
    return parent


def destination(path: Path | None, worktree: Path) -> Path:
    if path is None:
        parent = temporary_parent(worktree)
        path = Path(tempfile.mkdtemp(prefix="rrt-pending-review-", dir=parent)) / "pending.json"
    if path.is_symlink() or path.is_junction():
        raise ValueError("pending review must not be a link")
    selected = path.parent.resolve(strict=True) / path.name
    if _contained(selected.parent, worktree):
        raise ValueError("save pending review state outside the Git worktree")
    return selected


def _create(path: Path, payload: bytes) -> None:
    # O_EXCL also rejects a symlink at the leaf. The selected parent is trusted.
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def save(path: Path, *, root: Path, base: str | None, observed: ReviewIdentity, quote: ReviewQuote) -> ConsentRequired:
    # ASCII escapes preserve POSIX surrogateescape paths in portable JSON bytes.
    payload = (
        json.dumps(
            {
                "schema": _SCHEMA,
                "nonce": secrets.token_hex(32),
                "path": str(path),
                "root": str(root),
                "worktree": str(observed.worktree),
                "base": base,
                "content": observed.digest,
                "quote": json.loads(quote.payload),
            },
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )
    if len(payload) > _MAX_RECORD_BYTES:
        raise ValueError("serialized pending review exceeds the byte limit")
    _create(path, payload)
    return ConsentRequired(
        quote, str(path), sha256(payload), str(root), base, observed.head, observed.base_commit, observed.digest, observed.selected_paths, observed.branch
    )


def load(path: Path) -> PendingReview:
    if path.is_symlink() or path.is_junction():
        raise ValueError("pending review must not be a link")
    path = path.parent.resolve(strict=True) / path.name
    observed = observe_paths(path.parent, [path.name], max_file_bytes=_MAX_RECORD_BYTES).observations[0].value
    if not isinstance(observed, RegularFile):
        raise ValueError("pending review requires a regular file")
    data = _load_json(observed.content, "pending review")
    if not isinstance(data, dict) or set(data) != {"schema", "nonce", "path", "root", "worktree", "base", "content", "quote"} or data["schema"] != _SCHEMA:
        raise ValueError("unsupported pending review schema")
    for key in ("nonce", "content"):
        _digest(data[key], key)
    for key in ("path", "root", "worktree"):
        if not isinstance(data[key], str) or "\0" in data[key] or not Path(data[key]).is_absolute():
            raise ValueError(f"pending {key} must be an absolute native path")
    if data["path"] != str(path):
        raise ValueError("pending review was moved; request a new quote")
    root, worktree = Path(data["root"]), Path(data["worktree"])
    if root.resolve(strict=True) != root or worktree.resolve(strict=True) != worktree or not root.is_relative_to(worktree):
        raise ValueError("pending repository location changed")
    destination(path, worktree)
    base = data["base"]
    if base is not None and (not isinstance(base, str) or not base or base.startswith("-") or any(c.isspace() or c == "\0" for c in base)):
        raise ValueError("invalid pending review base")
    return PendingReview(path, sha256(observed.content), root, worktree, base, data["content"], ReviewQuote(deterministic_json(data["quote"])))


def consume(pending: PendingReview, approval: ReviewApproval | None) -> None:
    if approval is not None and approval.pending_identity != pending.identity:
        raise ValueError("approval does not match this pending review")
    payload = deterministic_json(
        {
            "schema": "research-repo-tools/review-use/v1",
            "pending": pending.identity,
            "action": "denied" if approval is None else "resume-attempt",
            "currency": None if approval is None else approval.currency,
            "review_kind": None if approval is None else approval.review_kind,
            "terms_source": None if approval is None else approval.terms_source,
            "unenforced_quote": approval is not None,
        }
    )
    try:
        _create(pending.path.with_name(pending.path.name + ".used"), payload)
    except FileExistsError as error:
        raise ValueError("pending review was already used or denied; reconcile any prior attempt before obtaining a new quote") from error
