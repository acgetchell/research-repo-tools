"""Typed CodeRabbit agent outcomes; service text is data, never a command."""

import json
import re
from dataclasses import dataclass
from typing import Literal

from research_repo_tools.evidence import _digest, _load_json, deterministic_json, sha256

__all__ = [
    "CompletedReview",
    "ConsentRequired",
    "FailedReview",
    "IncompleteReview",
    "ReviewApproval",
    "ReviewDenied",
    "ReviewQuote",
    "ReviewResult",
    "UncertainReview",
]

_MAX_EVENT_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ReviewQuote:
    """Original consent event, including unknown terms in its identity.

    CodeRabbit 0.7.6 supplies cents, count and confirmationHeadCommitId but no
    ISO currency or initial/incremental kind. Never infer those from file count.
    A human/caller must independently establish missing terms before approval.
    """

    payload: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes) or len(self.payload) > _MAX_EVENT_BYTES:
            raise ValueError("CodeRabbit quote must be bytes within the byte limit")
        data = _load_json(self.payload, "CodeRabbit quote")
        if not isinstance(data, dict) or data.get("type") != "action_required" or data.get("status") != "awaiting_confirmation":
            raise ValueError("expected a CodeRabbit consent-required event")

    @property
    def identity(self) -> str:
        """Return the SHA-256 of the serialized provider quote."""
        return sha256(self.payload)

    def terms(self) -> tuple[int, int, str]:
        """Require the verified provider fields, without estimating missing price."""
        data = json.loads(self.payload)
        count, cents, head = data.get("billableFilesCount"), data.get("maximumCostCents"), data.get("confirmationHeadCommitId")
        if data.get("action") != "rerun_with_use_credits":
            raise ValueError("unsupported CodeRabbit consent action; remain stopped")
        if type(count) is not int or count < 0 or type(cents) is not int or cents < 0:
            raise ValueError("quote lacks an unambiguous billable file count or maximumCostCents; obtain a new quote")
        if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
            raise ValueError("quote lacks a supported provider content identity; obtain a new quote")
        return count, cents, head


@dataclass(frozen=True, slots=True)
class ReviewApproval:
    """One caller authorization, not authentication of a human or a price cap.

    The caller has independently confirmed currency/review kind when the CLI
    omits them, and explicitly accepts a new invocation with an unenforced quote.
    The receipt binds these assertions, but cannot establish their truth itself.
    """

    pending_identity: str
    currency: str
    review_kind: Literal["initial", "incremental"]
    terms_source: str
    accept_unenforced_quote: bool

    def __post_init__(self) -> None:
        _digest(self.pending_identity, "pending review identity")
        if not isinstance(self.currency, str) or not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("approval requires an independently confirmed ISO currency")
        if self.review_kind not in {"initial", "incremental"}:
            raise ValueError("approval requires independently confirmed initial/incremental review kind")
        if not isinstance(self.terms_source, str) or not self.terms_source.strip() or len(self.terms_source) > 1000:
            raise ValueError("identify where the missing quote terms were independently confirmed")
        if self.accept_unenforced_quote is not True:
            raise ValueError("CodeRabbit cannot enforce a per-review price cap; paid dispatch remains stopped")


@dataclass(frozen=True, slots=True)
class CompletedReview:
    """The provider's finding count, reviewed files, and reuse or skip status."""

    findings: int
    reviewed_files: tuple[str, ...]
    reused: bool = False
    skipped: bool = False


@dataclass(frozen=True, slots=True)
class ConsentRequired:
    """A provider quote with the pending-record details available for an approval decision."""

    quote: ReviewQuote
    pending_path: str | None = None
    pending_identity: str | None = None
    repository: str | None = None
    base: str | None = None
    head: str | None = None
    base_commit: str | None = None
    content_identity: str | None = None
    selected_paths: int | None = None
    branch: str | None = None


@dataclass(frozen=True, slots=True)
class FailedReview:
    """Exit status and diagnostic for a review that did not complete successfully."""

    exit_code: int
    reason: str


@dataclass(frozen=True, slots=True)
class IncompleteReview(FailedReview):
    """Findings and file coverage reported by a partially completed review."""

    findings: int
    unreviewed_file_count: int
    unreviewed_files: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True, slots=True)
class ReviewDenied:
    """The identity of a pending review whose quote was declined."""

    pending_identity: str


@dataclass(frozen=True, slots=True)
class UncertainReview:
    """A paid invocation started but completion/billing needs reconciliation."""

    reason: str
    incomplete: IncompleteReview | None = None


type ReviewResult = CompletedReview | ConsentRequired | FailedReview | ReviewDenied | UncertainReview


class AgentEvents:
    """Bounded incremental NDJSON parsing independent of stdout chunk boundaries."""

    def __init__(self) -> None:
        self.pending = bytearray()
        self.terminal: dict[str, object] | None = None
        self.quote: ReviewQuote | None = None
        self.failure: str | None = None

    def feed(self, chunk: bytes) -> None:
        """Buffer a stream chunk and parse complete bounded CodeRabbit events."""
        self.pending.extend(chunk)
        while b"\n" in self.pending:
            line, _, rest = self.pending.partition(b"\n")
            self.pending = bytearray(rest)
            self._line(bytes(line))
        if len(self.pending) > _MAX_EVENT_BYTES:
            raise ValueError("CodeRabbit event exceeds the byte limit")

    def _line(self, line: bytes) -> None:
        if len(line) > _MAX_EVENT_BYTES:
            raise ValueError("CodeRabbit event exceeds the byte limit")
        if not line.strip():
            return
        data = _load_json(line, "CodeRabbit event")
        if not isinstance(data, dict) or not isinstance(data.get("type"), str):
            raise ValueError("CodeRabbit agent event must be a typed JSON object")
        kind = data["type"]
        if kind == "error":
            self.failure = str(data.get("message", "CodeRabbit reported an error"))[:1000]
        elif kind == "action_required":
            if self.quote is not None or self.terminal is not None:
                raise ValueError("conflicting CodeRabbit terminal events")
            self.quote = ReviewQuote(deterministic_json(data))
        elif kind == "complete":
            if self.quote is not None or self.terminal is not None:
                raise ValueError("conflicting CodeRabbit terminal events")
            self.terminal = data

    def finish(self, exit_code: int) -> CompletedReview | ConsentRequired | FailedReview:
        """Classify the process exit and observed events as completed, pending consent, or failed."""
        exit_code = exit_code if exit_code >= 0 else 128 - exit_code
        if self.pending:
            self._line(bytes(self.pending))
            self.pending.clear()
        if self.failure is not None:
            return FailedReview(exit_code or 1, self.failure)
        # Consent is a rate-limit stop: older providers exit 0, while providers
        # with failure reporting can exit 1. A crash/signal is not a usable quote.
        if exit_code not in (0, 1):
            return FailedReview(exit_code, "CodeRabbit failed; review is unavailable")
        if self.quote is not None:
            return ConsentRequired(self.quote)
        data = self.terminal
        if data is None:
            return FailedReview(1, "CodeRabbit failed; review is unavailable" if exit_code else "CodeRabbit exited without a completion event")
        if data.get("status") not in ("review_completed", "review_skipped") or data.get("outcome") not in (
            None,
            "completed",
            "completed_with_warnings",
            "failed",
        ):
            return FailedReview(1, "CodeRabbit did not complete the review")
        count = data.get("findings")
        files = data.get("reviewedFiles", [])
        if type(count) is not int or count < 0 or not isinstance(files, list) or any(not isinstance(path, str) for path in files):
            return FailedReview(1, "malformed CodeRabbit completion")
        reused = data.get("reused", False)
        if type(reused) is not bool:
            return FailedReview(1, "malformed CodeRabbit reuse state")
        if incomplete := _incomplete_review(data, count):
            return incomplete
        if exit_code or data.get("outcome") == "failed":
            return FailedReview(1, "CodeRabbit did not complete the review")
        return CompletedReview(count, tuple(files), reused, data["status"] == "review_skipped")


def _incomplete_review(data: dict[str, object], findings: int) -> FailedReview | None:
    """Classify skipped coverage, or return None when every file was reviewed."""
    details = data.get("unreviewedFiles", [])
    if not isinstance(details, list):
        return FailedReview(1, "malformed CodeRabbit unreviewed files")
    count = data.get("unreviewedFileCount", len(details))
    if type(count) is not int or count < len(details):
        return FailedReview(1, "malformed CodeRabbit unreviewed file count")
    if not count:
        return None
    skipped = []
    for item in details:
        if not isinstance(item, dict):
            return FailedReview(1, "malformed CodeRabbit unreviewed file")
        path = item.get("fileName", item.get("path", item.get("filePath", item.get("file"))))
        reason = item.get("reason")
        if not isinstance(path, str) or not path or (reason is not None and not isinstance(reason, str)):
            return FailedReview(1, "malformed CodeRabbit unreviewed file")
        skipped.append((path, reason))
    lines = [
        f"CodeRabbit review incomplete. The provider reported {findings} finding{'s' if findings != 1 else ''} and skipped {count} file{'s' if count != 1 else ''}."
    ]
    for path, reason in skipped:
        lines.append(f"  {ascii(path)}" + (f" ({ascii(reason)})" if reason is not None else ""))
    if any(reason == "diff_too_large" for _, reason in skipped):
        lines.append("Reduce the oversized file diff before rerunning the review. An identical retry leaves that file uncovered.")
    return IncompleteReview(1, "\n".join(lines), findings, count, tuple(skipped))
