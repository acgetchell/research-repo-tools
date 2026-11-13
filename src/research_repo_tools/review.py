"""Opt-in CodeRabbit review with a verified default base and consumer instructions."""

import json
import math
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from packaging.version import Version

from research_repo_tools import _review_pending
from research_repo_tools._review_identity import ReviewIdentity, capture
from research_repo_tools._review_stream import stream_review
from research_repo_tools.evidence import deterministic_json, sha256
from research_repo_tools.path_identity import fingerprint_paths, observe_paths
from research_repo_tools.process import ExecutableNotFoundError, get_safe_executable, run_git_command, run_safe_command
from research_repo_tools.review_protocol import CompletedReview, ConsentRequired, FailedReview, ReviewApproval, ReviewDenied, ReviewResult, UncertainReview

__all__ = ["DEFAULT_BASE", "deny", "resume", "run", "start"]

DEFAULT_BASE = "origin/main"
_FETCH_GUIDANCE = "Run 'git fetch origin', then retry 'just review'."


def _instructions(root: Path) -> list[str]:
    agents = root / "AGENTS.md"
    if agents.is_symlink() or not agents.is_file():
        raise ValueError(f"review requires {agents}")
    candidates = [root / name for name in (".coderabbit.yaml", ".coderabbit.yml")]
    present = [path for path in candidates if path.exists() or path.is_symlink()]
    if len(present) != 1 or present[0].is_symlink() or not present[0].is_file():
        raise ValueError("review requires exactly one regular .coderabbit.yaml or .coderabbit.yml file at the consumer root")
    return [str(agents), str(present[0])]


def _commit(output: str) -> str:
    value = output.strip()
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value):
        raise ValueError("Git did not return exactly one full commit ID")
    return value


def _verify_base(root: Path, base: str) -> None:
    if not base or base.startswith("-") or any(character.isspace() or character == "\0" for character in base):
        raise ValueError("review base must be a nonempty Git reference without whitespace or a leading '-'")
    ref = "refs/remotes/origin/main" if base == DEFAULT_BASE else base
    try:
        local = _commit(run_git_command(["--no-pager", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"], cwd=root).stdout)
    except (subprocess.SubprocessError, ValueError) as error:
        guidance = _FETCH_GUIDANCE if base == DEFAULT_BASE else "Select an existing local commit or reference."
        raise ValueError(f"Cannot resolve review base {base!r}. {guidance}") from error
    if base != DEFAULT_BASE:
        return
    try:
        result = run_git_command(["--no-pager", "ls-remote", "--exit-code", "origin", "refs/heads/main"], cwd=root)
        fields = result.stdout.split()
        if len(fields) != 2 or fields[1] != "refs/heads/main":
            raise ValueError("expected exactly one refs/heads/main remote reference")
        remote = _commit(fields[0])
    except (subprocess.SubprocessError, ValueError) as error:
        raise ValueError("Cannot verify origin/main against the remote; review was not started. Check origin and network access, then retry.") from error
    if local != remote:
        raise ValueError(f"origin/main is stale. {_FETCH_GUIDANCE}")


@dataclass(frozen=True, slots=True)
class _Provider:
    executable: Path
    version: Version
    identity: str


def _provider() -> _Provider:
    try:
        executable = Path(get_safe_executable("coderabbit"))
    except ExecutableNotFoundError as error:
        raise ExecutableNotFoundError(
            "CodeRabbit CLI is required on PATH. Install and authenticate it explicitly; see https://docs.coderabbit.ai/cli."
        ) from error
    output = run_safe_command(str(executable), ["--version"]).stdout.strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", output):
        raise ValueError("unsupported CodeRabbit version response")
    version = Version(output)
    if version < Version("0.7.6"):
        raise ValueError("CodeRabbit 0.7.6 or newer is required for structured consent")
    target = executable.resolve(strict=True)
    observed = fingerprint_paths(observe_paths(target.parent, [target.name], max_file_bytes=256 * 1024 * 1024, max_total_bytes=256 * 1024 * 1024))
    return _Provider(executable, version, sha256(deterministic_json([os.fsencode(executable).hex(), os.fsencode(target).hex(), str(version), observed])))


def _stdout(chunk: bytes) -> None:
    sys.stdout.buffer.write(chunk)
    sys.stdout.buffer.flush()


def _environment() -> dict[str, str]:
    # Apply the same Git semantics to the observer and provider subprocesses.
    # Optional index refreshes and replacement objects must not create a second
    # interpretation of the approved inputs.
    return {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1"}


def _timeout(value: float | None) -> None:
    if value is not None and (isinstance(value, bool) or not math.isfinite(value) or value <= 0):
        raise ValueError("review timeout must be positive and finite, or None")


def _arguments(root: Path, base: str | None) -> list[str]:
    return ["review", "--agent", "--include-untracked", *([f"--base={base}"] if base is not None else ["--uncommitted"]), "--config", *_instructions(root)]


def _observe(root: Path, base: str | None, provider: _Provider, env: dict[str, str]) -> ReviewIdentity:
    return capture(root, base, _instructions(root), provider=provider.identity, env=env)


def _invoke(provider: _Provider, root: Path, args: list[str], output: Callable[[bytes], None], timeout: float | None, env: dict[str, str]) -> ReviewResult:
    terminated = True
    output_failed = False

    def forward(chunk: bytes) -> None:
        nonlocal terminated, output_failed
        try:
            output(chunk)
        except BaseException:
            output_failed = True
            raise
        if chunk:
            terminated = chunk.endswith(b"\n")

    try:
        result = stream_review(provider.executable, args, cwd=root, env=env, output=forward, timeout=timeout)
    except KeyboardInterrupt:
        return FailedReview(130, "CodeRabbit review interrupted")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return FailedReview(1, f"CodeRabbit review unavailable: {error}")
    finally:
        # Separate invocations and the wrapper result even when the provider's
        # last record lacks LF. Never retry a failed caller-owned output sink.
        if not terminated and not output_failed:
            output(b"\n")
    if isinstance(result, CompletedReview) and provider.version < Version("0.7.7"):
        return FailedReview(1, "CodeRabbit before 0.7.7 cannot reliably establish review completeness; upgrade explicitly")
    return result


def start(
    root: Path, *, base: str | None, pending_path: Path | None = None, output: Callable[[bytes], None] = _stdout, timeout: float | None = None
) -> ReviewResult:
    """Start one included review, streaming NDJSON bytes and stopping on consent.

    Save consent outside the worktree (a new temporary directory by default).
    ValueError/OSError indicate local precondition failures. Provider failures
    return FailedReview. No installation, authentication or credits. RRT only
    reads Git; CodeRabbit can persist its own Git preferences and review cache.
    Keep the provider's usage-based continuation mode On demand.
    """
    _timeout(timeout)
    root = root.resolve(strict=True)
    args = _arguments(root, base)
    provider = _provider()
    if base is not None:
        _verify_base(root, base)
    env = _environment()
    before = _observe(root, base, provider, env)
    _review_pending.temporary_parent(before.worktree)
    destination = None if pending_path is None else _review_pending.destination(pending_path, before.worktree)
    if destination is not None and (destination.exists() or destination.with_name(destination.name + ".used").exists()):
        raise ValueError("choose a new pending review path")
    result = _invoke(provider, root, args, output, timeout, env)
    if isinstance(result, CompletedReview | ConsentRequired):
        if _observe(root, base, _provider(), _environment()) != before:
            return FailedReview(1, "review inputs changed while CodeRabbit ran; request a new review")
        if isinstance(result, ConsentRequired):
            destination = _review_pending.destination(destination, before.worktree)
            return _review_pending.save(destination, root=root, base=base, observed=before, quote=result.quote)
    return result


def resume(pending_path: Path, approval: ReviewApproval, *, output: Callable[[bytes], None] = _stdout, timeout: float | None = None) -> ReviewResult:
    """Consume approval once, refresh the quote free-first, then attempt paid work.

    This is a new --use-credits invocation with no server-enforced cap or snapshot.
    Unknown outcomes remain consumed and require external billing reconciliation.
    The trusted caller obtains human consent and verifies missing currency/kind.
    """
    _timeout(timeout)
    if not isinstance(approval, ReviewApproval):
        raise TypeError("resume requires a scoped ReviewApproval")
    pending = _review_pending.load(pending_path)
    if pending.identity != approval.pending_identity:
        raise ValueError("approval does not match this pending review")
    pending.quote.terms()
    provider = _provider()
    if provider.version < Version("0.7.7"):
        raise ValueError("paid resume requires CodeRabbit 0.7.7 or newer for reliable completeness reporting")
    args = _arguments(pending.root, pending.base)
    if pending.base is not None:
        _verify_base(pending.root, pending.base)
    env = _environment()
    before = _observe(pending.root, pending.base, provider, env)
    if before.digest != pending.content or before.worktree != pending.worktree:
        raise ValueError("review inputs changed; request a new quote and approval")
    _review_pending.temporary_parent(before.worktree)
    # Consume before the first attempt, including interrupted quote refreshes.
    # A crash can lose availability, but cannot silently reuse this approval.
    _review_pending.consume(pending, approval)
    refreshed = _invoke(provider, pending.root, args, output, timeout, env)
    if not isinstance(refreshed, CompletedReview | ConsentRequired):
        return refreshed
    if pending.base is not None:
        _verify_base(pending.root, pending.base)
    if _observe(pending.root, pending.base, _provider(), _environment()) != before:
        return FailedReview(1, "review inputs changed during quote refresh; obtain a new quote and approval")
    if isinstance(refreshed, CompletedReview):
        return refreshed
    if refreshed.quote.identity != pending.quote.identity:
        destination = _review_pending.destination(None, pending.worktree)
        return _review_pending.save(destination, root=pending.root, base=pending.base, observed=before, quote=refreshed.quote)
    if _review_pending.load(pending.path).identity != pending.identity:
        return FailedReview(1, "pending review changed during quote refresh")
    # Never use a service-supplied command. No --fresh/--gate/scope changes.
    try:
        paid = _invoke(provider, pending.root, [*args, "--use-credits"], output, timeout, env)
        if not isinstance(paid, CompletedReview):
            return UncertainReview("paid invocation did not establish completion; reconcile CodeRabbit usage before requesting another review")
        if _observe(pending.root, pending.base, _provider(), _environment()) != before:
            return UncertainReview("inputs changed during paid review; reconcile usage and review validity before requesting another review")
        return paid
    except Exception, KeyboardInterrupt:
        # Even a caller's output callback can fail after paid work starts.
        return UncertainReview("paid invocation outcome is uncertain; reconcile CodeRabbit usage before requesting another review")


def deny(pending_path: Path) -> ReviewDenied:
    """Consume the pending selection without invoking CodeRabbit."""
    pending = _review_pending.load(pending_path)
    _review_pending.consume(pending, None)
    return ReviewDenied(pending.identity)


def report(result: ReviewResult) -> int:
    """Emit the wrapper's versioned terminal result, after provider NDJSON."""
    data: dict[str, object] = {"type": "rrt_review_result", "schema": "research-repo-tools/review-result/v1"}
    match result:
        case CompletedReview():
            data.update(status="completed", findings=result.findings, reviewed_files=result.reviewed_files, reused=result.reused, skipped=result.skipped)
            status = 0
        case ConsentRequired():
            data.update(
                status="consent_required",
                pending_path=result.pending_path,
                pending_identity=result.pending_identity,
                quote=json.loads(result.quote.payload),
                repository=result.repository,
                branch=result.branch,
                scope="uncommitted" if result.base is None else "branch",
                base=result.base,
                head=result.head,
                base_commit=result.base_commit,
                content_identity=result.content_identity,
                selected_paths=result.selected_paths,
                currency=None,
                review_kind=None,
                server_enforced_cap=False,
            )
            status = 3
        case ReviewDenied():
            data.update(status="denied", pending_identity=result.pending_identity)
            status = 4
        case FailedReview():
            data.update(status="failed", reason=result.reason)
            print(result.reason, file=sys.stderr)
            status = result.exit_code if result.exit_code > 0 else 128 - result.exit_code
        case UncertainReview():
            data.update(status="uncertain", reason=result.reason)
            print(result.reason, file=sys.stderr)
            status = 5
    print(json.dumps(data, sort_keys=True, ensure_ascii=True, allow_nan=False))
    return status


def run(root: Path, *, base: str | None, pending_path: Path | None = None, timeout: float | None = None) -> int:
    """CLI adapter for start; provider nonzero statuses remain nonzero."""
    try:
        return report(start(root, base=base, pending_path=pending_path, timeout=timeout))
    except KeyboardInterrupt:
        return report(FailedReview(130, "CodeRabbit review interrupted"))
