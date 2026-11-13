"""Typed CodeRabbit agent outcomes; service text is data, never a command."""

import json
import re
from dataclasses import dataclass
from typing import Literal

from research_repo_tools.evidence import _digest, _load_json, deterministic_json, sha256

__all__ = ["CompletedReview", "ConsentRequired", "FailedReview", "ReviewApproval", "ReviewDenied", "ReviewQuote", "ReviewResult", "UncertainReview"]


@dataclass(frozen=True, slots=True)
class ReviewQuote:
    """Original consent event, including unknown terms in its identity.

    CodeRabbit 0.7.6 supplies cents, count and confirmationHeadCommitId but no
    ISO currency or initial/incremental kind. Never infer those from file count.
    A human/caller must independently establish missing terms before approval.
    """

    payload: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes) or len(self.payload) > 1024 * 1024:
            raise ValueError("CodeRabbit quote must be bytes within the byte limit")
        data = _load_json(self.payload, "CodeRabbit quote")
        if not isinstance(data, dict) or data.get("type") != "action_required" or data.get("status") != "awaiting_confirmation":
            raise ValueError("expected a CodeRabbit consent-required event")

    @property
    def identity(self) -> str:
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
    findings: int
    reviewed_files: tuple[str, ...]
    reused: bool = False
    skipped: bool = False


@dataclass(frozen=True, slots=True)
class ConsentRequired:
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
    exit_code: int
    reason: str


@dataclass(frozen=True, slots=True)
class ReviewDenied:
    pending_identity: str


@dataclass(frozen=True, slots=True)
class UncertainReview:
    """A paid invocation started but completion/billing needs reconciliation."""

    reason: str


type ReviewResult = CompletedReview | ConsentRequired | FailedReview | ReviewDenied | UncertainReview


class AgentEvents:
    """Bounded incremental NDJSON parsing independent of stdout chunk boundaries."""

    def __init__(self) -> None:
        self.pending = bytearray()
        self.terminal: dict[str, object] | None = None
        self.quote: ReviewQuote | None = None
        self.failure: str | None = None

    def feed(self, chunk: bytes) -> None:
        self.pending.extend(chunk)
        while b"\n" in self.pending:
            line, _, rest = self.pending.partition(b"\n")
            self.pending = bytearray(rest)
            self._line(bytes(line))
        if len(self.pending) > 1024 * 1024:
            raise ValueError("CodeRabbit event exceeds the byte limit")

    def _line(self, line: bytes) -> None:
        if len(line) > 1024 * 1024:
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
        exit_code = exit_code if exit_code >= 0 else 128 - exit_code
        if self.pending:
            self._line(bytes(self.pending))
            self.pending.clear()
        if self.failure is not None:
            return FailedReview(exit_code or 1, self.failure)
        # Consent is a rate-limit stop: older providers exit 0, while providers
        # with failure reporting can exit 1. A crash/signal is not a usable quote.
        if self.quote is not None and exit_code in (0, 1):
            return ConsentRequired(self.quote)
        if exit_code:
            return FailedReview(exit_code if exit_code > 0 else 128 - exit_code, "CodeRabbit failed; review is unavailable")
        data = self.terminal
        if data is None:
            return FailedReview(1, "CodeRabbit exited without a completion event")
        if data.get("status") not in ("review_completed", "review_skipped") or data.get("outcome") not in (None, "completed", "completed_with_warnings"):
            return FailedReview(1, "CodeRabbit did not complete the review")
        remaining = data.get("unreviewedFileCount", 0)
        count = data.get("findings")
        files = data.get("reviewedFiles", [])
        if type(remaining) is not int or remaining != 0:
            return FailedReview(1, "CodeRabbit left files unreviewed")
        if type(count) is not int or count < 0 or not isinstance(files, list) or any(not isinstance(path, str) for path in files):
            return FailedReview(1, "malformed CodeRabbit completion")
        reused = data.get("reused", False)
        if type(reused) is not bool:
            return FailedReview(1, "malformed CodeRabbit reuse state")
        return CompletedReview(count, tuple(files), reused, data["status"] == "review_skipped")
