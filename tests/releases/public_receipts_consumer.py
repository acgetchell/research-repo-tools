"""Original tag-push receipt contracts, also executed from wheel/sdist installs."""

import base64
import contextlib
import copy
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any, override
from unittest.mock import patch

from research_repo_tools import cli
from research_repo_tools.release_assets import ReleaseTarget, serialize_release_target
from research_repo_tools.release_receipts import DiscoverOriginal, FrozenReceipt, OriginalRun, TagPushPolicy, TagSource, retrieve_draft_receipt

COMMIT = "a" * 40
SOURCE = TagSource("refs/tags/v1.2.3", COMMIT)
POLICY = TagPushPolicy(
    "example/package", ".github/workflows/prepare.yml", "Prepare draft", (("verify", ("Verify source",)), ("prepare", ("Prepare", "Retain")))
)


class Provider:
    def __init__(self):
        self.calls = []
        self.downloads = []
        self.after_download = lambda: None
        self.workflow = {"id": 7, "path": POLICY.workflow, "name": POLICY.workflow_name, "state": "active"}
        self.workflow_text = b'name: Prepare draft\non:\n  push:\n    tags: ["v*"]\njobs: {}\n'
        self.run = {
            "id": 101,
            "run_attempt": 1,
            "workflow_id": 7,
            "path": POLICY.workflow,
            "name": POLICY.workflow_name,
            "head_sha": COMMIT,
            "head_branch": SOURCE.tag,
            "event": "push",
            "repository": {"id": 4, "full_name": POLICY.repository},
            "head_repository": {"id": 4, "full_name": POLICY.repository},
            "pull_requests": [],
            "status": "completed",
            "conclusion": "success",
        }
        self.history = [self.run]
        # Raw provider transport deliberately admits malformed rejection fixtures.
        self.jobs: list[dict[str, Any]] = [
            {
                "id": index,
                "name": name,
                "run_id": 101,
                "run_attempt": 1,
                "head_sha": COMMIT,
                "head_branch": SOURCE.tag,
                "status": "completed",
                "conclusion": "success",
                "labels": ["ubuntu-24.04"],
                "steps": [{"name": step, "status": "completed", "conclusion": "success"} for step in steps],
            }
            for index, (name, steps) in enumerate(POLICY.required_steps, 10)
        ]
        self.target = ReleaseTarget(POLICY.repository, SOURCE.tag, 91, COMMIT, SOURCE.tag)
        self.receipt = {
            "schema": "research-repo-tools/draft-receipt/v1",
            "repository": POLICY.repository,
            "tag": SOURCE.tag,
            "commit": COMMIT,
            "title": SOURCE.tag,
            "state": "captured",
            "release_id": 91,
            "notes_sha256": "b" * 64,
            "selection_sha256": "c" * 64,
        }
        self.artifact: dict[str, Any] = {
            "id": 201,
            "name": "draft-receipt-101-1",
            "expired": False,
            "workflow_run": {"id": 101, "head_sha": COMMIT, "head_branch": SOURCE.tag, "repository_id": 4, "head_repository_id": 4},
        }
        self.artifacts = [self.artifact]
        self.annotated = False
        self.tag_commit = COMMIT
        self.retain()

    def retain(self, files=None):
        files = files if files is not None else {"target.json": serialize_release_target(self.target), "draft-receipt.json": json.dumps(self.receipt).encode()}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, payload in files.items():
                archive.writestr(name, payload)
        self.zip = stream.getvalue()
        # Stored paths, not writer arguments, establish unsafe-archive fixtures.
        with zipfile.ZipFile(io.BytesIO(self.zip)) as archive:
            assert [member.orig_filename for member in archive.infolist()] == list(files)
        self.artifact.update(digest="sha256:" + hashlib.sha256(self.zip).hexdigest(), size_in_bytes=len(self.zip))

    def read(self, endpoint):
        self.calls.append(endpoint)
        if endpoint == "git/ref/tags/v1.2.3":
            value = {"ref": SOURCE.ref, "object": {"type": "tag" if self.annotated else "commit", "sha": "d" * 40 if self.annotated else self.tag_commit}}
        elif endpoint == f"git/tags/{'d' * 40}":
            value = {"sha": "d" * 40, "object": {"type": "commit", "sha": self.tag_commit}}
        elif endpoint == f"actions/workflows/{Path(POLICY.workflow).name}":
            value = self.workflow
        elif endpoint == f"contents/{POLICY.workflow}?ref={COMMIT}":
            value = {
                "type": "file",
                "path": POLICY.workflow,
                "encoding": "base64",
                "size": len(self.workflow_text),
                "content": base64.b64encode(self.workflow_text).decode(),
                "sha": hashlib.sha1(b"blob " + str(len(self.workflow_text)).encode() + b"\0" + self.workflow_text).hexdigest(),
            }
        elif endpoint == "actions/runs/101":
            value = self.run
        elif endpoint.startswith("actions/workflows/7/runs?"):
            assert endpoint.startswith("actions/workflows/7/runs?per_page=100&page=")
            page = int(endpoint.rsplit("=", 1)[1])
            value = {"total_count": len(self.history), "workflow_runs": self.history[(page - 1) * 100 : page * 100]}
        elif endpoint == "actions/runs/101/attempts/1/jobs?per_page=100&page=1":
            value = {"total_count": len(self.jobs), "jobs": self.jobs}
        elif endpoint == "actions/runs/101/artifacts?per_page=100&page=1":
            value = {"total_count": len(self.artifacts), "artifacts": self.artifacts}
        elif endpoint == "actions/artifacts/201":
            value = self.artifact
        else:
            raise AssertionError(f"unexpected provider read: {endpoint}")
        return copy.deepcopy(value)

    def execute(self, command, *, stdout, **kwargs):
        assert command == ["gh", "api", "repos/example/package/actions/artifacts/201/zip"]
        self.downloads.append(command)
        stdout.write(self.zip)
        self.after_download()
        return subprocess.CompletedProcess(command, 0)


class TestDraftReceipts(unittest.TestCase):
    @override
    def setUp(self):
        self.provider = Provider()

    def retrieve(self, *, selection=DiscoverOriginal(), policy=POLICY):
        with patch("research_repo_tools.release_validation.subprocess.run", side_effect=self.provider.execute):
            return retrieve_draft_receipt(policy, SOURCE, self.provider.read, selection=selection)

    def test_original_dotted_tag_annotated_source_and_frozen_retry(self):
        self.provider.annotated = True
        result = self.retrieve()
        self.assertEqual(result.target, self.provider.target)
        record = json.loads(result.selection)
        self.assertEqual((record["run"]["id"], record["run"]["run_attempt"], record["artifact"]["id"]), (101, 1, 201))
        self.assertEqual(record["source"], {"ref": SOURCE.ref, "commit": COMMIT})
        self.assertEqual(record["target_sha256"], hashlib.sha256(result.target_bytes).hexdigest())
        self.assertEqual(record["receipt_sha256"], hashlib.sha256(result.receipt_bytes).hexdigest())
        self.assertIn(f"git/tags/{'d' * 40}", self.provider.calls)
        self.provider.calls.clear()
        # A new run does not replace the frozen run, even if discovery would fail.
        self.provider.history = [dict(self.provider.run, id=102)]
        self.assertEqual(self.retrieve(selection=FrozenReceipt(result.selection)), result)
        self.assertFalse(any("/runs?" in call or "/artifacts?" in call for call in self.provider.calls))

    def test_branch_tag_collisions_require_source_workflow_with_tag_only_push(self):
        for trigger in ("on: push", "on: [push]", "on: {push: {}}", 'on: {push: {branches: ["v1.2.3"]}}', 'on: {push: {tags: ["v*"], branches: ["*"]}}'):
            with self.subTest(trigger=trigger):
                self.provider = Provider()
                self.provider.workflow_text = (trigger + "\njobs: {}\n").encode()
                with self.assertRaises(ValueError):
                    self.retrieve()
                self.assertEqual(self.provider.downloads, [])

    def test_source_workflow_rejects_duplicate_keys_merges_and_recursive_aliases(self):
        for text in (
            'on: {push: {tags: ["v*"]}}\non: push\n',
            'on: {push: {tags: ["v*"], tags: ["*"]}}\n',
            'on: {push: {<<: {tags: ["v*"]}}}\n',
            "on: &loop {push: *loop}\n",
        ):
            with self.subTest(text=text):
                self.provider.workflow_text = text.encode()
                with self.assertRaises(ValueError):
                    self.retrieve()
        self.assertEqual(self.provider.downloads, [])

    def test_run_identity_rejections_precede_download(self):
        for field, value in (
            ("id", 102),
            ("id", True),
            ("run_attempt", 2),
            ("run_attempt", True),
            ("workflow_id", 8),
            ("path", ".github/workflows/other.yml"),
            ("path", POLICY.workflow + "@refs/heads/v1.2.3"),
            ("name", "other"),
            ("head_sha", "b" * 40),
            ("head_branch", "main"),
            ("event", "pull_request"),
            ("event", "workflow_dispatch"),
            ("pull_requests", [{}]),
            ("status", "in_progress"),
            ("conclusion", "failure"),
            ("conclusion", "skipped"),
            ("repository", {"id": 4, "full_name": "other/package"}),
            ("head_repository", {"id": 5, "full_name": POLICY.repository}),
            ("head_repository", {"id": 4, "full_name": "fork/package"}),
        ):
            with self.subTest(field=field, value=value):
                self.provider = Provider()
                self.provider.run[field] = value
                with self.assertRaises(ValueError):
                    self.retrieve(selection=OriginalRun(101))
                self.assertEqual(self.provider.downloads, [])

    def test_missing_ambiguous_and_paginated_history_never_selects_latest(self):
        for history in ([], [101, 101], [101, 102]):
            with self.subTest(history=history):
                self.provider = Provider()
                self.provider.history = [dict(self.provider.run, id=identifier) for identifier in history]
                with self.assertRaises(ValueError):
                    self.retrieve()
        self.provider = Provider()
        self.provider.history = [dict(self.provider.run, id=index, head_branch="unrelated") for index in range(1000, 1100)] + [self.provider.run]
        self.assertEqual(self.retrieve().target.release_id, 91)
        self.assertIn("actions/workflows/7/runs?per_page=100&page=2", self.provider.calls)
        self.provider.history.append(dict(self.provider.run, id=102, head_sha="b" * 40, conclusion="failure"))
        with self.assertRaises(ValueError):
            self.retrieve()
        # Explicit manual recovery still verifies the complete selected identity.
        self.assertEqual(self.retrieve(selection=OriginalRun(101)).target.release_id, 91)

    def test_job_and_required_step_failures(self):
        for mutation in (
            "missing-job",
            "failed-job",
            "skipped-job",
            "wrong-attempt",
            "missing-step",
            "failed-step",
            "skipped-step",
            "duplicate-job",
            "duplicate-step",
        ):
            with self.subTest(mutation=mutation):
                self.provider = Provider()
                job = self.provider.jobs[-1]
                if mutation == "missing-job":
                    self.provider.jobs.pop()
                elif mutation == "duplicate-job":
                    self.provider.jobs.append(copy.deepcopy(job))
                elif mutation == "wrong-attempt":
                    job["run_attempt"] = 2
                elif mutation in {"failed-job", "skipped-job"}:
                    job["conclusion"] = "failure" if mutation == "failed-job" else "skipped"
                elif mutation == "missing-step":
                    job["steps"].pop()
                elif mutation == "duplicate-step":
                    job["steps"].append(copy.deepcopy(job["steps"][-1]))
                else:
                    job["steps"][-1]["conclusion"] = "failure" if mutation == "failed-step" else "skipped"
                with self.assertRaises(ValueError):
                    self.retrieve()
                self.assertEqual(self.provider.downloads, [])

    def test_artifact_identity_missing_expired_replaced_and_unbound_digest(self):
        for field, value in (("expired", True), ("name", "draft-receipt-101-2"), ("digest", None), ("size_in_bytes", 0), ("workflow_run", {"id": 102})):
            with self.subTest(field=field):
                self.provider = Provider()
                self.provider.artifact[field] = value
                with self.assertRaises(ValueError):
                    self.retrieve()
                self.assertEqual(self.provider.downloads, [])
        self.provider = Provider()
        frozen = FrozenReceipt(self.retrieve().selection)
        self.provider.artifact["id"] = 202
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.retrieve(selection=frozen)

    def test_zip_digest_and_exact_inert_inventory_are_verified(self):
        self.provider.zip += b"changed"
        with self.assertRaisesRegex(ValueError, "ZIP size or SHA-256"):
            self.retrieve()
        for name in ("extra.py", "../target.json", "target.json/", "TARGET.json"):
            with self.subTest(name=name):
                self.provider = Provider()
                self.provider.retain(
                    {
                        "target.json": serialize_release_target(self.provider.target),
                        "draft-receipt.json": json.dumps(self.provider.receipt).encode(),
                        name: b"inert",
                    }
                )
                with self.assertRaises(ValueError):
                    self.retrieve()

    def test_authenticated_bytes_still_require_captured_target_consistency(self):
        for field, value in (
            ("state", "pending"),
            ("release_id", True),
            ("release_id", 92),
            ("commit", "b" * 40),
            ("tag", "v1.2.4"),
            ("title", "changed"),
            ("notes_sha256", "bad"),
        ):
            with self.subTest(field=field):
                self.provider = Provider()
                self.provider.receipt[field] = value
                self.provider.retain()
                with self.assertRaises(ValueError):
                    self.retrieve()

    def test_retry_rejects_changed_attempt_jobs_workflow_and_source(self):
        for mutation in ("attempt", "job", "workflow", "tag", "bytes"):
            with self.subTest(mutation=mutation):
                self.provider = Provider()
                frozen = FrozenReceipt(self.retrieve().selection)
                if mutation == "attempt":
                    self.provider.run["run_attempt"] = 2
                elif mutation == "job":
                    self.provider.jobs[0]["id"] = 99
                elif mutation == "workflow":
                    self.provider.workflow_text += b"# changed provider source\n"
                elif mutation == "tag":
                    self.provider.tag_commit = "b" * 40
                else:
                    self.provider.receipt["notes_sha256"] = "e" * 64
                    self.provider.retain()
                with self.assertRaises(ValueError):
                    self.retrieve(selection=frozen)
                self.assertEqual(len(self.provider.downloads), 1)

    def test_provider_changes_during_download_do_not_publish_a_selection(self):
        for mutation in ("attempt", "tag", "artifact", "history", "inventory"):
            with self.subTest(mutation=mutation):
                self.provider = Provider()

                def change():
                    if mutation == "attempt":
                        self.provider.run["run_attempt"] = 2
                    elif mutation == "tag":
                        self.provider.tag_commit = "b" * 40
                    elif mutation == "artifact":
                        self.provider.artifact["expired"] = True
                    elif mutation == "history":
                        self.provider.history.append(dict(self.provider.run, id=102))
                    else:
                        self.provider.artifacts.append(dict(self.provider.artifact, id=202))

                self.provider.after_download = change
                with self.assertRaises(ValueError):
                    self.retrieve()

    def test_invalid_inputs_and_provider_errors_do_not_fall_back(self):
        for ref, commit in (("refs/heads/v1.2.3", COMMIT), ("refs/tags/v1.2.3-rc.1", COMMIT), (SOURCE.ref, "abc")):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                TagSource(ref, commit)
        for identifier in (True, 0, -1):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                OriginalRun(identifier)
        with self.assertRaises(ValueError):
            FrozenReceipt(b"{}")
        with self.assertRaises(ValueError):
            replace(POLICY, required_steps=())
        with patch.object(self.provider, "read", side_effect=OSError("provider unavailable")), self.assertRaisesRegex(OSError, "provider unavailable"):
            self.retrieve()
        self.assertEqual(self.provider.downloads, [])

    def test_cli_automatically_freezes_and_restores_original_receipt(self):
        with tempfile.TemporaryDirectory(prefix="receipt café ") as temporary:
            directory = Path(temporary).resolve()
            root = directory / "consumer"
            root.mkdir()
            policy = root / "receipt.toml"
            policy.write_text(
                f'schema=1\nrepository="{POLICY.repository}"\nworkflow="{POLICY.workflow}"\nworkflow-name="{POLICY.workflow_name}"\n'
                '[[jobs]]\nname="verify"\nsteps=["Verify source"]\n[[jobs]]\nname="prepare"\nsteps=["Prepare", "Retain"]\n',
                encoding="utf-8",
                newline="\n",
            )
            output = directory / "saved receipt"
            relative_output = str(Path("..") / output.name)
            arguments = ["--root", str(root), "release", "assets", "retrieve", "receipt.toml", "--tag", SOURCE.tag, "--commit", COMMIT]
            with (
                patch("research_repo_tools.release_interfaces.api", side_effect=lambda endpoint, **kwargs: self.provider.read(endpoint)),
                patch("research_repo_tools.release_validation.subprocess.run", side_effect=self.provider.execute),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(cli.main([*arguments, "--output", relative_output]), 0)
                before = {path.name: path.read_bytes() for path in output.iterdir()}
                self.assertEqual(set(before), {"selection.json", "target.json", "draft-receipt.json"})
                self.provider.history = []
                self.assertEqual(cli.main([*arguments, "--output", relative_output]), 0)
                self.assertEqual(cli.main([*arguments, "--output", str(output)]), 0)
                restored = directory / "restored"
                self.assertEqual(cli.main([*arguments, "--output", str(restored), "--expected", str(output / "selection.json")]), 0)
                self.assertEqual({path.name: path.read_bytes() for path in restored.iterdir()}, before)
                self.assertEqual(cli.main([*arguments, "--output", str(output), "--run-id", "102"]), 1)
                self.assertEqual(cli.main([*arguments, "--output", str(root / "unsafe")]), 1)
                self.assertEqual(cli.main([*arguments, "--output", str(Path("..") / root.name / "unsafe")]), 1)
                self.provider.run["run_attempt"] = 2
                self.assertEqual(cli.main([*arguments, "--output", str(output)]), 1)
                self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()}, before)
                self.assertFalse((root / "unsafe").exists())


if __name__ == "__main__":
    unittest.main()
