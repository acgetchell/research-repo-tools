"""Source and installed review contracts using synthetic providers, never billing."""

import contextlib
import errno
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import override
from unittest.mock import patch

from packaging.version import Version

from research_repo_tools import _review_identity, _review_pending, cli, process, review
from research_repo_tools._review_stream import stream_review
from research_repo_tools.evidence import deterministic_json
from research_repo_tools.path_identity import MissingPath, RegularFile
from research_repo_tools.review_protocol import (
    AgentEvents,
    CompletedReview,
    ConsentRequired,
    FailedReview,
    ReviewApproval,
    ReviewDenied,
    ReviewQuote,
    UncertainReview,
)

SHA = "a" * 40
QUOTE = {
    "type": "action_required",
    "status": "awaiting_confirmation",
    "action": "rerun_with_use_credits",
    "command": "DO NOT EXECUTE; $(touch compromised)",
    "billableFilesCount": 1,
    "maximumCostCents": 25,
    "confirmationHeadCommitId": "b" * 40,
    "promotionTerms": "synthetic fixture only",
}
COMPLETE = {"type": "complete", "status": "review_completed", "findings": 0, "reviewedFiles": ["café.py"], "outcome": "completed", "unreviewedFileCount": 0}


def event(value: Mapping[str, object]) -> bytes:
    return json.dumps(dict(value), ensure_ascii=False).encode("utf-8") + b"\r\n"


def fixture_git_environment() -> dict[str, str]:
    environment = {name: value for name, value in os.environ.items() if not name.upper().startswith("GIT_")}
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="Fixture",
        GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_NAME="Fixture",
        GIT_COMMITTER_EMAIL="fixture@example.invalid",
    )
    return environment


class GitFixture:
    """Documented read-only byte responses; every other command is rejected."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.head, self.base, self.index_hash = SHA.encode(), b"c" * 40, b"d" * 40
        self.branch = b"refs/heads/topic\n"
        self.config = b"local\0file:.git/config\0remote.origin.url\nhttps://example.invalid/repository\0"
        self.tracked = [b"AGENTS.md", b".coderabbit.yaml", b"cafe.py"]
        self.tree_paths = [*self.tracked, b"deleted.py"]
        self.stage, self.mode = b"0", b"100644"
        self.calls: list[list[str]] = []

    def run(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(args)
        prefix = list(_review_identity.READ_ONLY_OPTIONS)
        if args[: len(prefix)] != prefix:
            raise AssertionError(f"Git optional-write safeguards missing: {args}")
        command = args[len(prefix) :]
        match command:
            case ["rev-parse", "--show-toplevel"]:
                value = os.fsencode(self.root) + b"\n"
            case ["rev-parse", "--verify", "HEAD^{commit}"]:
                value = self.head + b"\n"
            case ["rev-parse", "--verify", "--end-of-options", _]:
                value = self.base + b"\n"
            case ["rev-parse", "--symbolic-full-name", "HEAD"]:
                value = self.branch
            case ["merge-base", "--all", "HEAD", _]:
                value = self.base + b"\n"
            case ["rev-parse", "--absolute-git-dir"]:
                value = os.fsencode(self.root / ".git") + b"\n"
            case ["ls-files", "--stage", "-z"]:
                value = b"".join(self.mode + b" " + self.index_hash + b" " + self.stage + b"\t" + name + b"\0" for name in self.tracked)
            case ["ls-tree", "-r", "-z", "HEAD"]:
                value = b"".join(b"100644 blob " + b"e" * 40 + b"\t" + name + b"\0" for name in self.tree_paths)
            case ["ls-files", "--others", "--exclude-standard", "-z"]:
                names = sorted(os.fsencode(path.relative_to(self.root).as_posix()) for path in self.root.rglob("*") if path.is_file())
                value = b"".join(name + b"\0" for name in names if name not in self.tracked)
            case ["config", "--null", "--list", "--show-origin", "--show-scope"]:
                value = self.config
            case _:
                raise AssertionError(f"unexpected or mutating Git command: {command}")
        return subprocess.CompletedProcess(args, 0, value, b"")


class TestReviewConsumer(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.area = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="review café "))).resolve()
        self.root = self.area / "consumer"
        self.root.mkdir()
        for name, content in (("AGENTS.md", b"review instructions\n"), (".coderabbit.yaml", b"reviews: {}\n"), ("cafe.py", b"print('original')\r\n")):
            (self.root / name).write_bytes(content)
        self.pending = self.area / "pending.json"
        self.git = GitFixture(self.root)
        self.stack.enter_context(patch.object(_review_identity, "run_git_bytes", self.git.run))
        self.provider = review._Provider(Path(sys.executable), Version("0.9.5"), "f" * 64)
        self.stack.enter_context(patch.object(review, "_provider", lambda: self.provider))
        self.base_checks: list[str] = []
        self.stack.enter_context(patch.object(review, "_verify_base", lambda root, base: self.base_checks.append(base)))
        self.calls: list[list[str]] = []
        self.results: list[Mapping[str, object] | int] = [QUOTE]
        self.output: list[bytes] = []
        self.final_newline = True
        self.script = self.area / "synthetic provider.py"
        self.stack.enter_context(patch.object(review, "stream_review", self.invoke))

    def invoke(
        self, command: Path, args: list[str], *, cwd: Path, env: Mapping[str, str], output: Callable[[bytes], None], timeout: float | None
    ) -> CompletedReview | ConsentRequired | FailedReview:
        self.calls.append(args)
        selected = self.results.pop(0)
        if isinstance(selected, int):
            source = f"raise SystemExit({selected})\n"
        else:
            payload = event({"type": "status", "message": "café progress"}) + event(selected)
            if not self.final_newline:
                payload = payload.removesuffix(b"\r\n")
            source = f"import os, sys\npayload = {payload!r}\nfor byte in payload:\n    os.write(sys.stdout.fileno(), bytes([byte]))\n"
        self.script.write_text(source, encoding="utf-8", newline="\n")
        return stream_review(Path(sys.executable), [str(self.script), *args], cwd=cwd, env=env, output=output, timeout=timeout)

    def start(self, *, base: str | None = None) -> ConsentRequired:
        result = review.start(self.root, base=base, pending_path=self.pending, output=self.output.append)
        self.assertIsInstance(result, ConsentRequired)
        assert isinstance(result, ConsentRequired)
        return result

    def approval(self, result: ConsentRequired) -> ReviewApproval:
        assert result.pending_identity is not None
        return ReviewApproval(result.pending_identity, "USD", "incremental", "synthetic provider contract, not a real quote", True)

    def test_free_first_scoped_paid_resume_and_one_use(self) -> None:
        result = self.start(base="main")
        self.assertEqual((result.repository, result.head, result.base_commit), (str(self.root), SHA, self.git.base.decode()))
        self.assertEqual(result.quote.terms(), (1, 25, "b" * 40))
        self.assertNotIn("--use-credits", self.calls[0])
        self.results = [QUOTE, COMPLETE]
        approval = self.approval(result)
        self.assertEqual(review.resume(self.pending, approval, output=self.output.append), CompletedReview(0, ("café.py",)))
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[1], self.calls[0])
        self.assertEqual(self.calls[2], [*self.calls[0], "--use-credits"])
        self.assertFalse(any(arg in self.calls[2] for arg in ("--fresh", "--gate", QUOTE["command"])))
        self.assertIn(event(COMPLETE), b"".join(self.output))
        with self.assertRaisesRegex(ValueError, "already used"):
            review.resume(self.pending, approval, output=self.output.append)
        self.assertEqual(len(self.calls), 3)
        self.assertFalse((self.root / "compromised").exists())

    def test_dirty_and_git_context_changes_reject_approval(self) -> None:
        for change in ("bytes", "untracked", "instructions", "configuration", "index", "head", "base", "branch", "git-config", "provider"):
            with self.subTest(change=change), contextlib.ExitStack() as scope:
                self.pending = self.area / f"{change}.json"
                self.results = [QUOTE]
                result = self.start(base="main")
                if change in {"bytes", "instructions", "configuration"}:
                    path = self.root / {"bytes": "cafe.py", "instructions": "AGENTS.md", "configuration": ".coderabbit.yaml"}[change]
                    previous = path.read_bytes()
                    scope.callback(path.write_bytes, previous)
                    path.write_bytes(previous + b"changed\n")
                elif change == "untracked":
                    path = self.root / "new file café.txt"
                    path.write_bytes(b"new\0bytes\r\n")
                    scope.callback(path.unlink)
                elif change == "provider":
                    scope.enter_context(patch.object(self, "provider", replace(self.provider, identity="0" * 64)))
                else:
                    name = {"index": "index_hash", "git-config": "config"}.get(change, change)
                    changed = self.git.config + b"local\0file:.git/config\0core.autocrlf\ntrue\0" if change == "git-config" else b"1" * 40
                    scope.enter_context(patch.object(self.git, name, changed))
                count = len(self.calls)
                with self.assertRaisesRegex(ValueError, "inputs changed"):
                    review.resume(self.pending, self.approval(result), output=self.output.append)
                self.assertEqual(len(self.calls), count)

    def test_changed_price_returns_new_pending_without_payment(self) -> None:
        result = self.start()
        self.results = [{**QUOTE, "maximumCostCents": 50}]
        changed = review.resume(self.pending, self.approval(result), output=self.output.append)
        self.assertIsInstance(changed, ConsentRequired)
        assert isinstance(changed, ConsentRequired) and changed.pending_path is not None
        path = Path(changed.pending_path)
        self.addCleanup(path.parent.rmdir)
        self.addCleanup(path.unlink)
        self.assertNotEqual(result.pending_identity, changed.pending_identity)
        self.assertEqual(changed.quote.terms()[1], 50)
        self.assertFalse(any("--use-credits" in args for args in self.calls))

    def test_refresh_can_complete_using_included_capacity(self) -> None:
        result = self.start()
        self.results = [COMPLETE]
        self.assertIsInstance(review.resume(self.pending, self.approval(result), output=self.output.append), CompletedReview)
        self.assertFalse(any("--use-credits" in args for args in self.calls))

    def test_changes_during_refresh_stop_before_paid_effect(self) -> None:
        result = self.start()
        self.results = [QUOTE]

        def edited(chunk: bytes) -> None:
            (self.root / "cafe.py").write_bytes(b"edited during refresh\n")

        self.assertIsInstance(review.resume(self.pending, self.approval(result), output=edited), FailedReview)
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("--use-credits", self.calls[-1])

    def test_uncertain_paid_outcome_never_retries(self) -> None:
        result = self.start()
        self.results = [QUOTE, 9]
        self.assertIsInstance(review.resume(self.pending, self.approval(result), output=self.output.append), UncertainReview)
        self.assertEqual(len(self.calls), 3)
        with self.assertRaisesRegex(ValueError, "already used"):
            review.resume(self.pending, self.approval(result), output=self.output.append)
        self.assertEqual(len(self.calls), 3)

    def test_uncertain_paid_timeout_interruption_and_callback_failure(self) -> None:
        for failure in (
            FailedReview(124, "timed out"),
            FailedReview(130, "interrupted"),
            FailedReview(1, "incomplete"),
            RuntimeError("output callback failed"),
        ):
            with self.subTest(failure=failure):
                self.pending = self.area / f"uncertain-{len(self.calls)}.json"
                self.results = [QUOTE]
                selected = self.start()
                refreshed = ConsentRequired(ReviewQuote(deterministic_json(QUOTE)))
                with patch.object(review, "stream_review", side_effect=[refreshed, failure]) as invoke:
                    self.assertIsInstance(review.resume(self.pending, self.approval(selected), output=self.output.append), UncertainReview)
                self.assertEqual(invoke.call_count, 2)
                self.assertTrue(self.pending.with_name(self.pending.name + ".used").is_file())

    def test_environment_changes_and_missing_acknowledgment_cannot_spend(self) -> None:
        result = self.start()
        with patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1"}), self.assertRaisesRegex(ValueError, "inputs changed"):
            review.resume(self.pending, self.approval(result), output=self.output.append)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["--root", str(self.root), "review", "resume", str(self.pending), "--approve", result.pending_identity or ""])
        self.assertEqual(len(self.calls), 1)

    def test_large_included_scope_has_no_consumer_file_limit(self) -> None:
        self.results = [{**COMPLETE, "reviewedFiles": [f"file-{index}.py" for index in range(150)]}]
        result = review.start(self.root, base=None, output=self.output.append)
        self.assertIsInstance(result, CompletedReview)
        self.assertNotIn("--use-credits", self.calls[0])

    def test_denial_without_provider_and_copied_record_rejected(self) -> None:
        result = self.start()
        copied = self.area / "copied.json"
        copied.write_bytes(self.pending.read_bytes())
        with self.assertRaisesRegex(ValueError, "moved"):
            review.resume(copied, self.approval(result), output=self.output.append)
        with patch.object(review, "_provider", side_effect=AssertionError("denial must not launch provider")):
            assert result.pending_identity is not None
            self.assertEqual(review.deny(self.pending), ReviewDenied(result.pending_identity))
        with self.assertRaisesRegex(ValueError, "already used"):
            review.resume(self.pending, self.approval(result), output=self.output.append)
        self.assertEqual(len(self.calls), 1)

    def test_unknown_price_remains_quote_but_cannot_resume(self) -> None:
        for key in ("billableFilesCount", "maximumCostCents", "confirmationHeadCommitId"):
            with self.subTest(key=key):
                self.pending = self.area / f"missing-{key}.json"
                self.results = [{name: value for name, value in QUOTE.items() if name != key}]
                result = self.start()
                count = len(self.calls)
                with self.assertRaisesRegex(ValueError, "quote lacks"):
                    review.resume(self.pending, self.approval(result), output=self.output.append)
                self.assertEqual(len(self.calls), count)

    def test_cap_required_wrong_approval_and_record_tampering(self) -> None:
        result = self.start()
        approval = self.approval(result)
        for changes in ({"accept_unenforced_quote": False}, {"currency": "$"}, {"review_kind": "unknown"}, {"terms_source": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(approval, **changes)
        with self.assertRaisesRegex(ValueError, "does not match"):
            review.resume(self.pending, replace(approval, pending_identity="1" * 64), output=self.output.append)
        data = json.loads(self.pending.read_bytes())
        data["base"] = "main"
        self.pending.write_bytes(deterministic_json(data))
        with self.assertRaisesRegex(ValueError, "does not match"):
            review.resume(self.pending, approval, output=self.output.append)
        self.assertEqual(len(self.calls), 1)

    def test_atomic_one_use_reservation(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        result = self.start()
        pending = _review_pending.load(self.pending)

        def attempt(_: int) -> bool:
            try:
                _review_pending.consume(pending, self.approval(result))
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(attempt, range(2))), [False, True])

    def test_selection_rejects_submodules_unmerged_and_inside_state(self) -> None:
        for attribute, value in (("stage", b"2"), ("mode", b"160000")):
            with self.subTest(attribute=attribute), patch.object(self.git, attribute, value), self.assertRaisesRegex(ValueError, "merged index"):
                self.start()
        with self.assertRaisesRegex(ValueError, "outside the Git worktree"):
            review.start(self.root, base=None, pending_path=self.root / "pending.json", output=self.output.append)
        self.assertEqual(self.calls, [])

    def test_old_provider_completion_is_not_clean(self) -> None:
        self.provider = replace(self.provider, version=Version("0.7.6"))
        self.results = [COMPLETE]
        self.assertIsInstance(review.start(self.root, base=None, output=self.output.append), FailedReview)

    def test_cli_quote_and_denial_terminal_contract(self) -> None:
        buffer = io.BytesIO()
        with contextlib.redirect_stdout(io.TextIOWrapper(buffer, encoding="utf-8", newline="\n")) as output:
            self.assertEqual(cli.main(["--root", str(self.root), "review", "uncommitted", "--pending", str(self.pending)]), 3)
            output.flush()
            body = buffer.getvalue()
        terminal = json.loads(body.splitlines()[-1])
        self.assertEqual(terminal["status"], "consent_required")
        self.assertIsNone(terminal["currency"])
        self.assertIsNone(terminal["review_kind"])
        self.assertFalse(terminal["server_enforced_cap"])
        with contextlib.redirect_stdout(io.StringIO()) as denied:
            self.assertEqual(cli.main(["--root", str(self.root), "review", "deny", str(self.pending)]), 4)
        self.assertEqual(json.loads(denied.getvalue())["status"], "denied")

    def test_cli_explicit_scoped_resume(self) -> None:
        result = self.start()
        self.results = [QUOTE, COMPLETE]
        buffer = io.BytesIO()
        with contextlib.redirect_stdout(io.TextIOWrapper(buffer, encoding="utf-8", newline="\n")) as output:
            status = cli.main(
                [
                    "--root",
                    str(self.root),
                    "review",
                    "resume",
                    str(self.pending),
                    "--approve",
                    result.pending_identity or "",
                    "--currency",
                    "USD",
                    "--review-kind",
                    "incremental",
                    "--terms-source",
                    "synthetic confirmed terms",
                    "--accept-unenforced-quote",
                ]
            )
            output.flush()
            body = buffer.getvalue()
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(body.splitlines()[-1])["status"], "completed")
        self.assertEqual(self.calls[-1], [*self.calls[0], "--use-credits"])

    def test_unterminated_provider_records_remain_separate_during_resume(self) -> None:
        selected = self.start()
        self.final_newline = False
        self.results = [QUOTE, COMPLETE]
        buffer = io.BytesIO()
        with contextlib.redirect_stdout(io.TextIOWrapper(buffer, encoding="utf-8", newline="\n")) as output:
            result = review.resume(self.pending, self.approval(selected))
            self.assertEqual(review.report(result), 0)
            output.flush()
            records = [json.loads(line) for line in buffer.getvalue().splitlines()]
        self.assertEqual([record["type"] for record in records], ["status", "action_required", "status", "complete", "rrt_review_result"])
        self.assertEqual(records[-1]["status"], "completed")

    @unittest.skipIf(os.name == "nt", "Windows names exclude line breaks")
    def test_checkout_with_trailing_newline_preserves_native_path(self) -> None:
        moved = self.root.with_name("consumer\n")
        self.root.rename(moved)
        self.root = moved
        self.git.root = moved
        result = self.start()
        self.assertEqual(result.repository, str(moved))

    def test_explicit_base_tolerates_provider_saved_preference(self) -> None:
        def remember_base(chunk: bytes) -> None:
            self.git.config = b"local\0file:.git/config\0coderabbit.basebranch\nmain\0"

        self.git.config = b"local\0file:.git/config\0coderabbit.basebranch\nold-base\0"
        result = review.start(self.root, base="main", pending_path=self.pending, output=remember_base)
        self.assertIsInstance(result, ConsentRequired)
        assert isinstance(result, ConsentRequired)
        self.results = [QUOTE, COMPLETE]
        self.assertIsInstance(review.resume(self.pending, self.approval(result), output=self.output.append), CompletedReview)
        self.assertEqual(len(self.calls), 3)

    def test_implicit_base_preference_remains_bound(self) -> None:
        result = self.start()
        self.git.config += b"local\0file:.git/config\0coderabbit.basebranch\nchanged\0"
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            review.resume(self.pending, self.approval(result), output=self.output.append)
        self.assertEqual(len(self.calls), 1)

    def test_default_pending_rejects_checkout_temp_without_creating_state(self) -> None:
        with patch.object(tempfile, "gettempdir", return_value=str(self.root)), patch.object(tempfile, "mkdtemp", wraps=tempfile.mkdtemp) as create:
            with self.assertRaisesRegex(ValueError, "outside the Git worktree"):
                _review_pending.destination(None, self.root)
        create.assert_not_called()

    def test_checkout_temp_stops_before_provider_even_with_explicit_pending(self) -> None:
        # The provider stdout spool must also remain outside its input inventory.
        with patch.object(tempfile, "gettempdir", return_value=str(self.root)), self.assertRaisesRegex(ValueError, "outside the Git worktree"):
            self.start()
        self.assertEqual(self.calls, [])

    def test_pending_serialization_preserves_native_path_surrogates(self) -> None:
        # Model POSIX surrogateescape without requiring a filesystem that accepts
        # non-UTF-8 names (APFS and Windows do not). Only publication is replaced.
        native_name = "native-\udcff"
        path, root = self.area / native_name / "pending.json", self.root / native_name
        identity = _review_identity.ReviewIdentity(root, "f" * 64, SHA, None, 1)
        with patch.object(_review_pending, "_create") as create:
            saved = _review_pending.save(path, root=root, base=None, observed=identity, quote=ReviewQuote(deterministic_json(QUOTE)))
        payload = create.call_args.args[1]
        self.assertEqual(json.loads(payload)["path"], str(path))
        self.assertEqual(json.loads(payload)["root"], str(root))
        self.assertEqual(saved.repository, str(root))

    @unittest.skipIf(os.name == "nt", "Windows path policy requires UTF-8")
    def test_non_utf8_checkout_quote_and_resume_when_supported(self) -> None:
        moved = self.root.with_name(os.fsdecode(b"consumer-\xff"))
        try:
            self.root.rename(moved)
        except OSError as error:
            if sys.platform == "darwin" and error.errno in {errno.EILSEQ, errno.ENOTSUP}:
                self.skipTest("native filesystem rejects non-UTF-8 names")
            raise
        self.root = self.git.root = moved
        result = self.start()
        self.assertEqual(result.repository, str(moved))
        self.results = [QUOTE, COMPLETE]
        self.assertIsInstance(review.resume(self.pending, self.approval(result), output=self.output.append), CompletedReview)

    def test_staged_file_directory_replacements_preserve_deleted_identity(self) -> None:
        for old, new in ((b"file-to-directory", b"file-to-directory/child"), (b"directory-to-file/child", b"directory-to-file")):
            with self.subTest(old=old), contextlib.ExitStack() as scope:
                scope.enter_context(patch.object(self.git, "tree_paths", [*self.git.tree_paths, old]))
                scope.enter_context(patch.object(self.git, "tracked", [*self.git.tracked, new]))
                leaf = self.root / os.fsdecode(new)
                leaf.parent.mkdir(parents=True, exist_ok=True)
                leaf.write_bytes(b"replacement\r\n")
                self.pending = self.area / (os.fsdecode(old).replace("/", "-") + ".json")
                self.results = [QUOTE]
                with patch.object(_review_identity, "fingerprint_paths", wraps=_review_identity.fingerprint_paths) as fingerprint:
                    result = self.start()
                inventory = fingerprint.call_args.args[0]
                values = {item.name: item.value for item in inventory.observations}
                self.assertEqual(values[old], MissingPath())
                self.assertEqual(values[new], RegularFile(b"replacement\r\n", False))
                self.results = [QUOTE, COMPLETE]
                self.assertIsInstance(review.resume(self.pending, self.approval(result), output=self.output.append), CompletedReview)

    def test_native_fixture_discards_inherited_git_overrides(self) -> None:
        overrides = {
            name: "outside-fixture"
            for name in (
                "GIT_DIR",
                "GIT_WORK_TREE",
                "GIT_COMMON_DIR",
                "GIT_INDEX_FILE",
                "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                "GIT_CONFIG",
                "GIT_CONFIG_SYSTEM",
                "GIT_CONFIG_PARAMETERS",
                "GIT_CONFIG_COUNT",
                "GIT_CONFIG_KEY_0",
                "GIT_CONFIG_VALUE_0",
                "GIT_TEMPLATE_DIR",
            )
        }
        with patch.dict(os.environ, overrides):
            environment = fixture_git_environment()
        self.assertFalse(set(overrides) & environment.keys())
        self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(environment.get("PATH"), os.environ.get("PATH"))


class TestAgentEvents(unittest.TestCase):
    def test_oversized_blank_line_is_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "byte limit"):
            AgentEvents().feed(b" " * (1024 * 1024 + 1) + b"\n")

    def test_quote_does_not_mask_abnormal_process_exit(self) -> None:
        for code, expected in ((9, 9), (-15, 143)):
            with self.subTest(code=code):
                parser = AgentEvents()
                parser.feed(event(QUOTE))
                result = parser.finish(code)
                self.assertIsInstance(result, FailedReview)
                assert isinstance(result, FailedReview)
                self.assertEqual(result.exit_code, expected)
        for code in (0, 1):
            parser = AgentEvents()
            parser.feed(event(QUOTE))
            self.assertIsInstance(parser.finish(code), ConsentRequired)

    def test_split_unicode_crlf_and_final_fragment(self) -> None:
        parser = AgentEvents()
        payload = event({"type": "finding", "comment": "café", "codegenInstructions": "untrusted commands"}) + event(COMPLETE).rstrip(b"\r\n")
        for byte in payload:
            parser.feed(bytes([byte]))
        self.assertEqual(parser.finish(0), CompletedReview(0, ("café.py",)))

    def test_incomplete_error_missing_and_malformed_are_not_clean(self) -> None:
        for value in (
            {**COMPLETE, "outcome": "failed"},
            {**COMPLETE, "outcome": []},
            {**COMPLETE, "status": {}},
            {**COMPLETE, "unreviewedFileCount": 1},
            {**COMPLETE, "findings": True},
            {"type": "error"},
            {"type": "status"},
        ):
            with self.subTest(value=value):
                parser = AgentEvents()
                parser.feed(event(value))
                self.assertIsInstance(parser.finish(0), FailedReview)
        for payload in (b"not JSON\n", b'{"type":"complete","type":"error"}\n', b"x" * (1024 * 1024 + 1)):
            with self.subTest(prefix=payload[:30]), self.assertRaises(ValueError):
                AgentEvents().feed(payload)

    def test_reused_skipped_warning_and_nonzero_outcomes(self) -> None:
        for changes in (
            {"reused": True},
            {"status": "review_skipped", "reviewedFiles": []},
            {"outcome": "completed_with_warnings"},
            {"persistence": {"status": "failed"}},
        ):
            with self.subTest(changes=changes):
                parser = AgentEvents()
                parser.feed(event({**COMPLETE, **changes}))
                self.assertIsInstance(parser.finish(0), CompletedReview)
        for status, expected in ((9, 9), (-15, 143)):
            parser = AgentEvents()
            parser.feed(event(COMPLETE))
            failed = parser.finish(status)
            assert isinstance(failed, FailedReview)
            self.assertEqual(failed.exit_code, expected)

    def test_conflicting_terminal_events_and_invalid_quote(self) -> None:
        parser = AgentEvents()
        parser.feed(event(QUOTE))
        with self.assertRaisesRegex(ValueError, "conflicting"):
            parser.feed(event(COMPLETE))
        for key, value in (("billableFilesCount", True), ("maximumCostCents", -1), ("confirmationHeadCommitId", "unknown"), ("action", "execute_shell")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                ReviewQuote(deterministic_json({**QUOTE, key: value})).terms()

    def test_timeout_and_interrupt_cleanup_real_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "child.py"
            script.write_text('import time\nprint(\'{"type":"heartbeat"}\', flush=True)\ntime.sleep(60)\n', newline="\n")
            output: list[bytes] = []
            result = stream_review(Path(sys.executable), [str(script)], cwd=root, env=dict(os.environ), output=output.append, timeout=1)
            self.assertEqual(result, FailedReview(124, "CodeRabbit timed out; review is unavailable"))
            self.assertIn(b'"heartbeat"', b"".join(output))

            def interrupt(chunk: bytes) -> None:
                raise KeyboardInterrupt

            result = stream_review(Path(sys.executable), [str(script)], cwd=root, env=dict(os.environ), output=interrupt, timeout=10)
            self.assertEqual(result, FailedReview(130, "CodeRabbit review interrupted"))


@unittest.skipIf(os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1", "disposable Git fixture mutations disabled")
class TestNativeGitReviewIdentity(unittest.TestCase):
    def test_staged_file_directory_replacements(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native replacements café ") as directory:
            root = Path(directory).resolve()
            environment = fixture_git_environment()
            for name in ("AGENTS.md", ".coderabbit.yaml", "file-to-directory", "directory-to-file/child"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"original\n")
            for args in (
                ["init", "--initial-branch=main"],
                ["-c", "core.autocrlf=false", "add", "."],
                ["-c", "commit.gpgsign=false", "commit", "-m", "fixture"],
            ):
                process.run_git_bytes(args, cwd=root, env=environment)
            (root / "file-to-directory").unlink()
            (root / "file-to-directory").mkdir()
            first = root / "file-to-directory/child"
            first.write_bytes(b"first replacement\r\n")
            (root / "directory-to-file/child").unlink()
            (root / "directory-to-file").rmdir()
            second = root / "directory-to-file"
            second.write_bytes(b"second replacement\r\n")
            process.run_git_bytes(["-c", "core.autocrlf=false", "add", "-A"], cwd=root, env=environment)
            instructions = [str(root / "AGENTS.md"), str(root / ".coderabbit.yaml")]
            index = root / ".git/index"
            staged_index = index.read_bytes()
            observed = _review_identity.capture(root, "main", instructions, provider="fixture", env=environment)
            self.assertEqual(observed.selected_paths, 6)  # Four current paths plus two HEAD-only deletions.
            first.write_bytes(b"changed first replacement\n")
            changed = _review_identity.capture(root, "main", instructions, provider="fixture", env=environment)
            self.assertNotEqual(observed.digest, changed.digest)
            second.write_bytes(b"changed second replacement\n")
            self.assertNotEqual(changed.digest, _review_identity.capture(root, "main", instructions, provider="fixture", env=environment).digest)
            self.assertEqual(index.read_bytes(), staged_index)

    def test_real_git_selection_index_and_worktree_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native review café ") as directory:
            root = Path(directory).resolve()
            environment = fixture_git_environment()
            for name, payload in (("AGENTS.md", b"instructions\n"), (".coderabbit.yaml", b"reviews: {}\n"), ("café file.txt", b"original\r\n")):
                (root / name).write_bytes(payload)
            for args in (
                ["init", "--initial-branch=main"],
                ["-c", "core.autocrlf=false", "add", "."],
                ["-c", "commit.gpgsign=false", "commit", "-m", "fixture"],
            ):
                process.run_git_bytes(args, cwd=root, env=environment)
            instructions = [str(root / "AGENTS.md"), str(root / ".coderabbit.yaml")]
            index = root / ".git" / "index"
            original_index = index.read_bytes()
            first = _review_identity.capture(root, "main", instructions, provider="fixture", env=environment)
            self.assertEqual(index.read_bytes(), original_index)
            (root / "café file.txt").write_bytes(b"changed unstaged\r\n")
            dirty = _review_identity.capture(root, "main", instructions, provider="fixture", env=environment)
            self.assertNotEqual(first.digest, dirty.digest)
            self.assertEqual(index.read_bytes(), original_index)
            process.run_git_bytes(["-c", "core.autocrlf=false", "add", "."], cwd=root, env=environment)
            staged_index = index.read_bytes()
            staged = _review_identity.capture(root, "main", instructions, provider="fixture", env=environment)
            self.assertNotEqual(staged.digest, dirty.digest)
            self.assertEqual(index.read_bytes(), staged_index)
            (root / "untracked café.txt").write_bytes(b"new\0bytes\n")
            untracked = _review_identity.capture(root, "main", instructions, provider="fixture", env=environment)
            self.assertNotEqual(untracked.digest, staged.digest)
            self.assertEqual(index.read_bytes(), staged_index)


if __name__ == "__main__":
    unittest.main()
