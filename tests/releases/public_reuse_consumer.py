"""Inert GitHub evidence and real binary transport for installed reuse consumers."""

import base64
import contextlib
import copy
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from research_repo_tools import cli, config
from research_repo_tools.process import run_git_bytes
from research_repo_tools.release_assets import GitHubRelease
from research_repo_tools.release_publishing import PublishingSettings, RequiredCheck, check_reviewed_release, publish_reviewed_release
from research_repo_tools.release_reuse import ReusePolicy, create_reuse_manifest, parse_reuse_policy, verify_ci_reuse
from research_repo_tools.release_validation import ValidationPolicy

HEAD = "a" * 40
CHECKOUT = "b" * 40
RELEASE = "c" * 40
BASE = "d" * 40
TREE = "e" * 40
WORKFLOW = b"# inert reviewed workflow\r\n"
NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
CONTRACT = {"commands": "1" * 64, "environment": "2" * 64, "inputs": "3" * 64, "tools": "4" * 64}
VALIDATION = ValidationPolicy(
    "example/project",
    ".github/workflows/source.yml",
    "Source checks",
    (("linux", ("Check offline tree",)), ("macos", ("Check offline tree",)), ("windows", ("Check offline tree",))),
    runner_labels=(("linux", ("ubuntu-24.04",)), ("macos", ("macos-15",)), ("windows", ("windows-2025",))),
)
POLICY = ReusePolicy(
    VALIDATION,
    hashlib.sha256(WORKFLOW).hexdigest(),
    tuple((name, tuple(CONTRACT.items())) for name, _ in VALIDATION.required_steps),
    ("sign", ("Seal", "Sign", "Upload")),
)


def encode(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")


class Provider:
    def __init__(self):
        self.repo = {"id": 7, "full_name": "example/project", "default_branch": "main"}
        self.run = {
            "id": 123,
            "run_attempt": 2,
            "head_sha": HEAD,
            "head_branch": "feature",
            "workflow_id": 13,
            "check_suite_id": 14,
            "path": VALIDATION.workflow,
            "name": VALIDATION.workflow_name,
            "event": "pull_request",
            "created_at": "2026-10-25T11:00:00Z",
            "status": "completed",
            "conclusion": "success",
            "repository": dict(self.repo),
            "head_repository": dict(self.repo),
        }
        self.workflow = {"id": 13, "path": VALIDATION.workflow, "name": VALIDATION.workflow_name, "state": "active"}
        self.definition = {"type": "file", "encoding": "base64", "content": base64.b64encode(WORKFLOW).decode("ascii")}
        self.pr = {
            "number": 9,
            "merged": True,
            "state": "closed",
            "merge_commit_sha": RELEASE,
            "head": {"sha": HEAD, "ref": "feature", "repo": dict(self.repo)},
            "base": {"ref": "main", "repo": dict(self.repo)},
        }
        self.branch = {"name": "main", "protected": True}
        self.comparison = {"status": "identical"}
        self.source = {"sha": CHECKOUT, "tree": {"sha": TREE}, "parents": [{"sha": BASE}, {"sha": HEAD}]}
        self.target = {"sha": RELEASE, "tree": {"sha": TREE}, "parents": [{"sha": BASE}]}
        self.jobs: list[dict[str, Any]] = [
            {
                "id": index,
                "name": name,
                "run_id": 123,
                "run_attempt": 2,
                "head_sha": HEAD,
                "head_branch": "feature",
                "status": "completed",
                "conclusion": "success",
                "labels": list(dict(VALIDATION.runner_labels).get(name, ("ubuntu-24.04",))),
                "steps": [{"name": step, "status": "completed", "conclusion": "success"} for step in steps],
                "check_run_url": f"https://api.github.com/repos/example/project/check-runs/{index + 100}",
            }
            for index, (name, steps) in enumerate((*VALIDATION.required_steps, POLICY.signing_job), 20)
        ]
        self.checks = [
            {
                "id": job["id"] + 100,
                "name": job["name"],
                "app": {"id": 15368},
                "check_suite": {"id": 14},
                "head_sha": HEAD,
                "status": "completed",
                "conclusion": "success",
            }
            for job in self.jobs
        ]
        self.artifact = {
            "id": 42,
            "name": "ci-reuse-123-2",
            "expired": False,
            "workflow_run": {"id": 123, "head_sha": HEAD, "head_branch": "feature", "repository_id": 7, "head_repository_id": 7},
        }
        self.certificate = {"runInvocationURI": "https://github.com/example/project/actions/runs/123/attempts/2", "buildTrigger": "pull_request"}
        self.receipts = {
            job["name"]: {
                "job_id": job["id"],
                "checkout": CHECKOUT,
                "tree": TREE,
                "mode": "offline-tree",
                "git_metadata": False,
                "event_context": False,
                "network": False,
                **CONTRACT,
            }
            for job in self.jobs[:3]
        }
        self.requests = []
        self.commands = []
        self.files = {}
        self.payload = b""

    def read(self, endpoint):
        self.requests.append(endpoint)
        if endpoint == "":
            value = self.repo
        elif endpoint == "branches/main":
            value = self.branch
        elif endpoint == f"compare/{RELEASE}...main":
            value = self.comparison
        elif endpoint == "pulls/9":
            value = self.pr
        elif endpoint == f"git/commits/{CHECKOUT}":
            value = self.source
        elif endpoint == f"git/commits/{RELEASE}":
            value = self.target
        elif endpoint in (f"contents/{VALIDATION.workflow}?ref={CHECKOUT}", f"contents/{VALIDATION.workflow}?ref={RELEASE}"):
            value = self.definition
        elif endpoint == "actions/runs/123":
            value = self.run
        elif endpoint == "actions/workflows/source.yml":
            value = self.workflow
        elif endpoint.startswith("actions/runs/123/attempts/2/jobs?"):
            value = {"total_count": len(self.jobs), "jobs": self.jobs}
        elif endpoint.startswith(f"commits/{HEAD}/check-runs?filter=latest&"):
            value = {"total_count": len(self.checks), "check_runs": self.checks}
        elif endpoint.startswith("actions/runs/123/artifacts?"):
            value = {"total_count": 1, "artifacts": [self.artifact]}
        elif endpoint == "actions/artifacts/42":
            value = self.artifact
        else:
            raise AssertionError(f"unexpected provider request: {endpoint}")
        return copy.deepcopy(value)

    def seal(self):
        # Native receipts are independent literal observations above. The signing
        # checkout boundary is modeled here; real Git coverage is separate.
        with (
            patch(
                "research_repo_tools.release_reuse.run_git_command",
                side_effect=lambda args, **kwargs: subprocess.CompletedProcess([], 0, (TREE if args[-1] == "HEAD^{tree}" else CHECKOUT) + "\n"),
            ),
            patch("research_repo_tools.release_reuse.require_release_checkout"),
        ):
            payload = create_reuse_manifest(POLICY, Path("unused"), 9, 123, 2, self.receipts, self.read)
        self.files = {"reuse.json": payload, "reuse.sigstore.json": b"inert signature bundle"}
        self.repack()

    def repack(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, payload in self.files.items():
                archive.writestr(name, payload)
        self.payload = stream.getvalue()
        self.artifact.update(digest="sha256:" + hashlib.sha256(self.payload).hexdigest(), size_in_bytes=len(self.payload))

    def execute(self, args, **kwargs):
        self.commands.append(args)
        if args == ["gh", "api", "repos/example/project/actions/artifacts/42/zip"]:
            kwargs["stdout"].write(self.payload)
            return subprocess.CompletedProcess(args, 0)
        if args[:3] == ["gh", "attestation", "verify"]:
            return subprocess.CompletedProcess(args, 0, encode([{"verificationResult": {"signature": {"certificate": self.certificate}}}]).decode("utf-8"))
        raise AssertionError(f"unexpected command: {args}")


class TestReuseConsumer(unittest.TestCase):
    def settings(self, root):
        (root / "pyproject.toml").write_bytes(b'[project]\nname="sample"\nversion="1.2.3"\n')
        (root / "CHANGELOG.md").write_bytes(b"# Changelog\n\n## [1.2.3] - 2026-10-25\n\n- Authored notes.\n")
        publishing = PublishingSettings(
            "pypi",
            "sample",
            "example/project",
            tuple(RequiredCheck(name, 15368) for name in ("linux", "macos", "windows", "release-package")),
            ("sample-1.2.3.tgz",),
            POLICY,
        )
        return replace(config.parse({}, root=root), publishing=publishing)

    def provider(self):
        result = Provider()
        result.seal()
        return result

    def verify(self, provider, **kwargs):
        with patch("research_repo_tools.release_validation.subprocess.run", provider.execute):
            if "expected" in kwargs:
                return verify_ci_reuse(POLICY, RELEASE, provider.read, now=NOW, **kwargs)
            return verify_ci_reuse(POLICY, RELEASE, provider.read, now=NOW, pull_request=9, run_id=123, attempt=2, **kwargs)

    def test_equal_tree_different_commit_and_approval_success(self):
        provider = self.provider()
        saved = self.verify(provider)
        record = json.loads(saved)
        self.assertEqual(record["release_commit"], RELEASE)
        self.assertEqual(record["checkout"], CHECKOUT)
        self.assertNotEqual(record["release_commit"], record["checkout"])
        self.assertEqual(record["tree"], TREE)
        self.assertEqual(record["jobs"], {"linux": 20, "macos": 21, "windows": 22, "sign": 23})
        self.assertEqual(record["artifact"]["id"], 42)
        self.assertEqual(self.verify(provider, expected=saved), saved)
        command = next(command for command in provider.commands if command[1:3] == ["attestation", "verify"])
        for option, value in (
            ("--signer-digest", CHECKOUT),
            ("--source-digest", CHECKOUT),
            ("--source-ref", "refs/pull/9/merge"),
            ("--repo", "example/project"),
        ):
            self.assertEqual(command[command.index(option) + 1], value)
        self.assertIn("--deny-self-hosted-runners", command)

    def test_rejects_observed_context_checkout_or_external_input_differences(self):
        for key, value in (
            ("checkout", HEAD),
            ("tree", "f" * 40),
            ("mode", "working-tree"),
            ("git_metadata", True),
            ("event_context", True),
            ("network", True),
            ("commands", "0" * 64),
            ("environment", "0" * 64),
            ("inputs", "0" * 64),
            ("tools", "0" * 64),
            ("job_id", 999),
        ):
            with self.subTest(key=key):
                provider = Provider()
                provider.receipts["linux"][key] = value
                with self.assertRaisesRegex(ValueError, "observed checkout"):
                    provider.seal()
                provider = self.provider()
                document = json.loads(provider.files["reuse.json"])
                document["observations"]["linux"][key] = value
                provider.files["reuse.json"] = encode(document)
                provider.repack()
                with self.assertRaisesRegex(ValueError, "signed reuse observations"):
                    self.verify(provider)

    def test_wrong_tree_base_relationship_or_trust_rejects(self):
        changes = [
            ("target", "tree", {"sha": "f" * 40}),
            ("target", "parents", [{"sha": "f" * 40}]),
            ("source", "parents", [{"sha": BASE}, {"sha": "f" * 40}]),
            ("pr", "merged", False),
            ("pr", "merge_commit_sha", HEAD),
            ("pr", "head", {"sha": HEAD, "ref": "feature", "repo": {"id": 99, "full_name": "fork/project"}}),
            ("branch", "protected", False),
            ("comparison", "status", "diverged"),
            ("repo", "default_branch", "other"),
            ("definition", "content", base64.b64encode(b"unreviewed workflow").decode("ascii")),
            ("run", "event", "push"),
            ("run", "head_repository", {"id": 99, "full_name": "fork/project"}),
            ("run", "run_attempt", 3),
            ("run", "conclusion", "failure"),
            ("run", "status", "in_progress"),
            ("workflow", "id", 99),
            ("workflow", "state", "disabled_manually"),
            ("certificate", "buildTrigger", "push"),
            ("certificate", "runInvocationURI", "https://github.com/example/project/actions/runs/123/attempts/1"),
            ("artifact", "expired", True),
            ("artifact", "workflow_run", {"id": 99}),
        ]
        for item, field, value in changes:
            with self.subTest(item=item, field=field):
                provider = self.provider()
                getattr(provider, item)[field] = value
                with self.assertRaises(ValueError):
                    self.verify(provider)

    def test_complete_native_jobs_and_current_provider_checks_are_required(self):
        for damage in (
            "missing",
            "duplicate",
            "cancelled",
            "step",
            "platform",
            "self-hosted",
            "attempt",
            "checkout",
            "job",
            "provider",
            "suite",
            "newer-failure",
            "ambiguous",
        ):
            with self.subTest(damage=damage):
                provider = self.provider()
                job, check = provider.jobs[2], provider.checks[2]
                if damage == "missing":
                    provider.jobs.pop(2)
                elif damage == "duplicate":
                    provider.jobs.append(copy.deepcopy(job))
                elif damage == "cancelled":
                    job["conclusion"] = "cancelled"
                elif damage == "step":
                    job["steps"][0]["conclusion"] = "skipped"
                elif damage == "platform":
                    job["labels"] = ["ubuntu-24.04"]
                elif damage == "self-hosted":
                    job["labels"].append("self-hosted")
                elif damage == "attempt":
                    job["run_attempt"] = 1
                elif damage == "checkout":
                    job["head_sha"] = RELEASE
                elif damage == "job":
                    job["check_run_url"] += "0"
                elif damage == "provider":
                    check["app"]["id"] = 99
                elif damage == "suite":
                    check["check_suite"]["id"] = 99
                elif damage == "newer-failure":
                    check["conclusion"] = "failure"
                else:
                    provider.checks.append({**check, "id": 999})
                with self.assertRaises(ValueError):
                    self.verify(provider)

    def test_stale_future_and_naive_timestamps_reject(self):
        for created in ((NOW - timedelta(days=2)).isoformat(), (NOW + timedelta(seconds=1)).isoformat(), "2026-10-25T11:00:00", "not a date"):
            with self.subTest(created=created):
                provider = self.provider()
                provider.run["created_at"] = created
                with self.assertRaises(ValueError):
                    self.verify(provider)

    def test_approval_pins_policy_artifact_manifest_and_run(self):
        for damage in ("artifact", "manifest", "attempt", "policy", "target", "jobs", "checks"):
            with self.subTest(damage=damage):
                provider = self.provider()
                saved = self.verify(provider)
                if damage == "artifact":
                    provider.files["reuse.sigstore.json"] += b"changed"
                    provider.repack()
                elif damage == "manifest":
                    provider.files["reuse.json"] += b" "
                    provider.repack()
                elif damage == "attempt":
                    provider.run["run_attempt"] = 3
                elif damage == "jobs":
                    provider.jobs[0]["id"] = 777
                elif damage == "checks":
                    provider.checks[0]["id"] = 777
                    provider.jobs[0]["check_run_url"] = "https://api.github.com/repos/example/project/check-runs/777"
                else:
                    record = json.loads(saved)
                    if damage == "policy":
                        record["policy"]["app_id"] = 99
                    else:
                        record["release_commit"] = HEAD
                    saved = encode(record)
                with self.assertRaises(ValueError):
                    self.verify(provider, expected=saved)

    def test_signature_failure_and_damaged_bytes_never_become_approval(self):
        provider = self.provider()
        provider.payload += b"changed"
        with self.assertRaisesRegex(ValueError, "SHA-256|size"):
            self.verify(provider)
        provider = self.provider()
        execute = provider.execute

        def reject_signature(args, **kwargs):
            if args[:3] == ["gh", "attestation", "verify"]:
                raise subprocess.CalledProcessError(1, args, stderr="signature rejected")
            return execute(args, **kwargs)

        provider.execute = reject_signature
        with self.assertRaises(subprocess.CalledProcessError):
            self.verify(provider)

    def test_race_during_transport_rejects(self):
        provider = self.provider()
        execute = provider.execute

        def change_after_download(args, **kwargs):
            result = execute(args, **kwargs)
            if args[:2] == ["gh", "api"]:
                provider.checks[0]["conclusion"] = "failure"
            return result

        provider.execute = change_after_download
        with self.assertRaises(ValueError):
            self.verify(provider)

    def test_policy_requires_complete_pinned_execution(self):
        for values in (
            {"workflow_sha256": "main"},
            {"max_age_seconds": True},
            {"max_age_seconds": 604801},
            {"app_id": False},
            {"contracts": POLICY.contracts[:-1]},
            {"validation": replace(VALIDATION, runner_labels=())},
            {"signing_job": VALIDATION.required_steps[0]},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(POLICY, **values)
        table = {
            "workflow": VALIDATION.workflow,
            "workflow-name": VALIDATION.workflow_name,
            "workflow-sha256": POLICY.workflow_sha256,
            "jobs": [
                {"name": name, "steps": list(steps), "labels": list(dict(VALIDATION.runner_labels)[name]), **CONTRACT}
                for name, steps in VALIDATION.required_steps
            ],
            "signing-job": {"name": "sign", "steps": ["Seal", "Sign", "Upload"]},
        }
        self.assertEqual(parse_reuse_policy(table, "example/project"), POLICY)
        publishing = {
            "registry": "pypi",
            "package": "sample",
            "repository": "example/project",
            "reuse": table,
            "required-checks": [{"name": name, "app-id": 15368} for name in ("linux", "macos", "windows", "release-package")],
        }
        with tempfile.TemporaryDirectory() as temporary:
            parsed = config.parse({"publishing": publishing}, root=Path(temporary)).publishing
            assert parsed is not None
            self.assertEqual(parsed.reuse, POLICY)
        with self.assertRaisesRegex(ValueError, "strict subset"):
            replace(
                PublishingSettings("pypi", "sample", "example/project", tuple(RequiredCheck(name, 15368) for name in ("linux", "macos", "windows"))),
                reuse=POLICY,
            )
        with self.assertRaisesRegex(ValueError, "unknown"):
            parse_reuse_policy({**table, "allow-forks": True}, "example/project")

    def test_release_gate_defaults_to_exact_commit_and_preserves_package_assets(self):
        provider = self.provider()
        saved = self.verify(provider)
        exact = [{"id": 800, "name": "release-package", "head_sha": RELEASE, "status": "completed", "conclusion": "success", "app": {"id": 15368}}]
        release = GitHubRelease("example/project", "v1.2.3", 1, False, False, False, (("sample-1.2.3.tgz", 2, 20, None),))

        def read(root, repository, endpoint, **kwargs):
            if endpoint == f"commits/{RELEASE}/check-runs?filter=latest&per_page=100":
                return [{"total_count": len(exact), "check_runs": exact}]
            return provider.read(endpoint)

        with tempfile.TemporaryDirectory(prefix="reuse gate café ") as temporary:
            settings = self.settings(Path(temporary))
            with (
                patch(
                    "research_repo_tools.release_publishing.run_git_command",
                    side_effect=lambda args, **kwargs: subprocess.CompletedProcess([], 0, "" if "status" in args else RELEASE),
                ),
                patch("research_repo_tools.release_publishing._remote_commit", return_value=RELEASE),
                patch("research_repo_tools.release_publishing._api", read),
                patch("research_repo_tools.release_publishing.lookup_release", return_value=release) as lookup,
                patch("research_repo_tools.release_validation.subprocess.run", provider.execute),
                patch("research_repo_tools.release_publishing.verify_ci_reuse", side_effect=lambda *args, **kwargs: verify_ci_reuse(*args, now=NOW, **kwargs)),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaisesRegex(ValueError, "linux.*missing"):
                    check_reviewed_release(settings, "v1.2.3")
                self.assertEqual(check_reviewed_release(settings, "v1.2.3", reuse_evidence=saved), release)
                exact.append({**exact[0], "id": 801, "name": "linux", "conclusion": "failure"})
                with self.assertRaisesRegex(ValueError, "linux.*unsuccessful"):
                    check_reviewed_release(settings, "v1.2.3", reuse_evidence=saved)
                exact.pop()
                exact[0]["conclusion"] = "cancelled"
                with self.assertRaisesRegex(ValueError, "release-package.*unsuccessful"):
                    check_reviewed_release(settings, "v1.2.3", reuse_evidence=saved)
                exact[0]["conclusion"] = "success"
                lookup.return_value = replace(release, assets=())
                with self.assertRaisesRegex(ValueError, "asset"):
                    check_reviewed_release(settings, "v1.2.3", reuse_evidence=saved)
                # The unchanged exact-commit path remains a successful fallback.
                lookup.return_value = release
                exact.extend({**exact[0], "id": 801 + i, "name": name} for i, name in enumerate(("linux", "macos", "windows")))
                self.assertEqual(check_reviewed_release(settings, "v1.2.3"), release)

    def test_publication_rechecks_the_same_selection_before_any_mutation(self):
        provider = self.provider()
        saved = self.verify(provider)
        draft = GitHubRelease("example/project", "v1.2.3", 1, True, False, False, ())
        with tempfile.TemporaryDirectory() as temporary:
            settings = self.settings(Path(temporary))
            with (
                patch("research_repo_tools.release_publishing.run_git_command", return_value=subprocess.CompletedProcess([], 0, RELEASE)),
                patch("research_repo_tools.release_publishing.check_reviewed_release", side_effect=[draft, ValueError("approved reuse changed")]) as gate,
                patch("research_repo_tools.release_publishing._publish_release") as publish,
            ):
                with self.assertRaisesRegex(ValueError, "approved reuse changed"):
                    publish_reviewed_release(settings, "v1.2.3", reuse_evidence=saved)
                self.assertEqual([call.kwargs["reuse_evidence"] for call in gate.call_args_list], [saved, saved])
                publish.assert_not_called()

    def test_cli_saves_explicit_selection_without_overwrite_and_passes_it_to_publication(self):
        provider = self.provider()
        saved = self.verify(provider)
        with tempfile.TemporaryDirectory(prefix="reuse CLI café ") as temporary:
            checkout = Path(temporary) / "checkout"
            checkout.mkdir()
            settings = self.settings(checkout)
            output = Path(temporary) / "approval.json"
            args = ["release", "reuse", RELEASE, "--attempt", "2", "--output", str(output), "--pull-request", "9", "--run-id", "123"]
            with (
                patch("research_repo_tools.cli.config.load", return_value=settings),
                patch("research_repo_tools.release_reuse.verify_ci_reuse", return_value=saved) as verify,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(cli.main(args), 0)
                self.assertEqual(output.read_bytes(), saved)
                self.assertEqual(verify.call_args.kwargs, {"pull_request": 9, "run_id": 123, "attempt": 2})
                self.assertNotEqual(cli.main(args), 0)
                self.assertEqual(output.read_bytes(), saved)
                with patch("research_repo_tools.release_publishing.publish_reviewed_release") as publish:
                    self.assertEqual(cli.main(["release", "publish", "v1.2.3", "--approve", "--reuse-evidence", str(output)]), 0)
                    self.assertEqual(publish.call_args.kwargs, {"reuse_evidence": saved})
                output.unlink()
                verify.side_effect = ValueError("evidence rejected")
                self.assertEqual(cli.main(args), 1)
                self.assertFalse(output.exists())

    def test_cli_rejects_approval_inside_checkout_before_verification(self):
        with tempfile.TemporaryDirectory(prefix="reuse destination café ") as temporary:
            root = Path(temporary).resolve() / "checkout"
            root.mkdir()
            settings = self.settings(root)
            alias = root.parent / "checkout-alias"
            if os.name == "nt":
                subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(root)], check=True, capture_output=True, timeout=30)
                self.assertTrue(alias.is_junction())
            else:
                alias.symlink_to(root, target_is_directory=True)
                self.assertTrue(alias.is_symlink())
            self.assertTrue(alias.samefile(root))
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            outputs = [
                Path("approval.json"),
                root / "approval.json",
                root / "missing" / "approval.json",
                root / ".." / "checkout" / "approval.json",
                alias / "approval.json",
                root,
            ]
            case_alias = root.with_name("CHECKOUT")
            if case_alias.exists():
                self.assertTrue(case_alias.samefile(root))
                outputs.append(case_alias / "approval.json")
            for output in outputs:
                with (
                    self.subTest(output=output),
                    contextlib.chdir(root),
                    patch("research_repo_tools.cli.config.load", return_value=settings),
                    patch("research_repo_tools.release_reuse.verify_ci_reuse", return_value=b"approval") as verify,
                    contextlib.redirect_stdout(io.StringIO()) as stdout,
                    contextlib.redirect_stderr(io.StringIO()) as stderr,
                ):
                    self.assertEqual(
                        cli.main(["release", "reuse", RELEASE, "--attempt", "2", "--output", str(output), "--pull-request", "9", "--run-id", "123"]), 1
                    )
                    self.assertIn("outside the reviewed checkout", stderr.getvalue())
                    self.assertEqual(stdout.getvalue(), "")
                    verify.assert_not_called()
                    self.assertEqual({path.name: path.read_bytes() for path in root.iterdir()}, before)
            self.assertTrue(alias.samefile(root))

    @unittest.skipIf(os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1", "disposable Git mutations explicitly disabled")
    def test_manifest_observes_real_clean_merge_checkout_without_changing_index(self):
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        with (
            tempfile.TemporaryDirectory(prefix="reuse git café ") as temporary,
            patch.dict(os.environ, environment, clear=True),
        ):
            root = Path(temporary)

            def git(*args):
                return run_git_bytes(
                    [
                        "--no-pager",
                        "-c",
                        "core.autocrlf=false",
                        "-c",
                        "commit.gpgsign=false",
                        "-c",
                        f"core.hooksPath={root / 'disabled-hooks'}",
                        "-c",
                        "user.name=Reuse fixture",
                        "-c",
                        "user.email=reuse@example.invalid",
                        *args,
                    ],
                    cwd=root,
                    timeout=30,
                ).stdout

            git("init", "--template=")
            path = root / "source.txt"
            path.write_bytes(b"base\r\n")
            git("add", "source.txt")
            git("commit", "-m", "base")
            base = git("rev-parse", "HEAD").decode("ascii").strip()
            path.write_bytes(b"candidate\r\n")
            git("add", "source.txt")
            git("commit", "-m", "head")
            head, tree = (git("rev-parse", value).decode("ascii").strip() for value in ("HEAD", "HEAD^{tree}"))
            merge = git("commit-tree", tree, "-p", base, "-p", head, "-m", "synthetic merge").decode("ascii").strip()
            git("checkout", "--detach", merge)
            provider = Provider()
            provider.run["head_sha"] = head
            for item in (*provider.jobs, *provider.checks):
                item["head_sha"] = head
            source = {"sha": merge, "tree": {"sha": tree}, "parents": [{"sha": base}, {"sha": head}]}

            def read(endpoint):
                if endpoint == f"git/commits/{merge}":
                    return source
                return provider.read(endpoint.replace(head, HEAD).replace(merge, CHECKOUT))

            receipts = {name: {**value, "checkout": merge, "tree": tree} for name, value in provider.receipts.items()}
            before = (root / ".git/index").read_bytes()
            manifest = create_reuse_manifest(POLICY, root, 9, 123, 2, receipts, read)
            self.assertEqual(json.loads(manifest)["checkout"], merge)
            self.assertEqual(json.loads(manifest)["tree"], tree)
            self.assertEqual((root / ".git/index").read_bytes(), before)
            path.write_bytes(b"dirty\n")
            with self.assertRaisesRegex(ValueError, "observed release checkout"):
                create_reuse_manifest(POLICY, root, 9, 123, 2, receipts, read)


if __name__ == "__main__":
    unittest.main()
