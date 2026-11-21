"""Original tag-push receipt policy and GitHub provider fixture."""

import base64
import copy
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from research_repo_tools.release_assets import ReleaseTarget, serialize_release_target
from research_repo_tools.release_receipts import TagPushPolicy, TagSource

COMMIT = "a" * 40


SOURCE = TagSource("refs/tags/v1.2.3", COMMIT)


POLICY = TagPushPolicy(
    "example/package", ".github/workflows/prepare.yml", "Prepare draft", (("verify", ("Verify source",)), ("prepare", ("Prepare", "Retain")))
)


class Provider:
    """Serve a tag workflow, original run history, and a retained receipt archive for one policy."""

    def __init__(self, policy: TagPushPolicy = POLICY):
        self.policy = policy
        self.calls = []
        self.downloads = []
        self.after_download = lambda: None
        self.workflow = {"id": 7, "path": self.policy.workflow, "name": self.policy.workflow_name, "state": "active"}
        self.workflow_text = b'name: Prepare draft\non:\n  push:\n    tags: ["v*"]\njobs: {}\n'
        self.run = {
            "id": 101,
            "run_attempt": 1,
            "workflow_id": 7,
            "path": self.policy.workflow,
            "name": self.policy.workflow_name,
            "head_sha": COMMIT,
            "head_branch": SOURCE.tag,
            "event": "push",
            "repository": {"id": 4, "full_name": self.policy.repository},
            "head_repository": {"id": 4, "full_name": self.policy.repository},
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
            for index, (name, steps) in enumerate(self.policy.required_steps, 10)
        ]
        self.target = ReleaseTarget(self.policy.repository, SOURCE.tag, 91, COMMIT, SOURCE.tag)
        self.receipt = {
            "schema": "research-repo-tools/draft-receipt/v1",
            "repository": self.policy.repository,
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
        """Rebuild the immutable artifact bytes and their advertised digest from the selected files."""
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
        """Serve a copy of one GitHub response and record its endpoint."""
        self.calls.append(endpoint)
        if endpoint == "git/ref/tags/v1.2.3":
            value = {"ref": SOURCE.ref, "object": {"type": "tag" if self.annotated else "commit", "sha": "d" * 40 if self.annotated else self.tag_commit}}
        elif endpoint == f"git/tags/{'d' * 40}":
            value = {"sha": "d" * 40, "object": {"type": "commit", "sha": self.tag_commit}}
        elif endpoint == f"actions/workflows/{Path(self.policy.workflow).name}":
            value = self.workflow
        elif endpoint == f"contents/{self.policy.workflow}?ref={COMMIT}":
            value = {
                "type": "file",
                "path": self.policy.workflow,
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
        """Write the retained receipt ZIP to the requested download stream."""
        assert command == ["gh", "api", f"repos/{self.policy.repository}/actions/artifacts/201/zip"]
        self.downloads.append(command)
        stdout.write(self.zip)
        self.after_download()
        return subprocess.CompletedProcess(command, 0)
