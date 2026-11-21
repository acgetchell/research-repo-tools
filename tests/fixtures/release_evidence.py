"""Inert providers and command drivers for release evidence and draft handoffs."""

import base64
import contextlib
import copy
import hashlib
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, override
from unittest.mock import patch

from research_repo_tools import cli, config
from research_repo_tools.release_evidence import (
    EvidencePolicy,
    create_evidence_manifest,
)
from research_repo_tools.release_validation import (
    ValidationPolicy,
)

COMMIT = "a" * 40


VALIDATION = ValidationPolicy(
    "example/package",
    ".github/workflows/ci.yml",
    "CI",
    (("linux", ("Native checks",)), ("macos", ("Native checks",)), ("windows", ("Native checks",)), ("package", ("Default and exact archive checks",))),
    runner_labels=(("linux", ("ubuntu-latest",)), ("macos", ("macos-latest",)), ("windows", ("windows-latest",))),
)


SOURCE = EvidencePolicy(VALIDATION, "sample", "release-evidence", "signed-source", ("sign", ("Capture", "Sign", "Upload")))


def encode(value):
    """Encode a fixture record as sorted UTF-8 JSON with a trailing newline."""
    return (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")


def interface_policy(root, policy=SOURCE):
    """Write an evidence policy for the release CLI and return its path."""
    text = (
        "\n".join(
            f"{key} = {json.dumps(value)}"
            for key, value in {
                "schema": 1,
                "repository": policy.validation.repository,
                "package": policy.package,
                "workflow": policy.validation.workflow,
                "workflow-name": policy.validation.workflow_name,
                "artifact-prefix": policy.artifact_prefix,
                "guarantee": policy.guarantee,
                "package-kind": policy.package_kind,
                "path-in-vcs": policy.path_in_vcs,
            }.items()
        )
        + "\n"
    )
    for name, steps in policy.validation.required_steps:
        text += f"\n[[jobs]]\nname={json.dumps(name)}\nsteps={json.dumps(steps)}\n"
        labels = dict(policy.validation.runner_labels).get(name)
        if labels:
            text += f"runner-labels={json.dumps(labels)}\n"
    if policy.signing_job:
        name, steps = policy.signing_job
        text += f"\n[signing-job]\nname={json.dumps(name)}\nsteps={json.dumps(steps)}\n"
    if policy.distributions:
        destination = 'destination="undecided"' if policy.distributions.index_url is None else f"index-url={json.dumps(policy.distributions.index_url)}"
        text += (
            f"\n[distributions]\nfiles={json.dumps(policy.distributions.files)}\n"
            f"{destination}\nmax-age-seconds={policy.distributions.max_age_seconds}\n"
            f"max-distribution-bytes={policy.distributions.max_distribution_bytes}\n"
        )
    path = root / "evidence.toml"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def interface_fixture(root, arguments):
    """Native child entrypoint: real CLI/Just/files, inert provider and checkout."""
    provider = Provider(SOURCE)
    provider.retain(create_evidence_manifest(SOURCE, "1.2.3", COMMIT, 123, 2, provider.read))
    with (
        patch("research_repo_tools.release_interfaces.api", side_effect=lambda endpoint, **kwargs: provider.read(endpoint)),
        patch("research_repo_tools.release_interfaces.require_release_checkout"),
        patch("research_repo_tools.release_validation.subprocess.run", provider.execute),
    ):
        return cli.main(["--root", str(root), *arguments])


class Provider:
    """Model GitHub workflow evidence, artifact downloads, and attestation verification for one run."""

    def __init__(self, policy):
        self.policy = policy
        self.run: dict[str, Any] = {
            "id": 123,
            "run_attempt": 2,
            "head_sha": COMMIT,
            "head_branch": "main",
            "workflow_id": 13,
            "path": ".github/workflows/ci.yml",
            "name": "CI",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "repository": {"id": 7, "full_name": "example/package"},
            "head_repository": {"id": 7, "full_name": "example/package"},
        }
        self.workflow: dict[str, object] = {"id": 13, "path": ".github/workflows/ci.yml", "name": "CI", "state": "active"}
        requirements = (*VALIDATION.required_steps, *((policy.signing_job,) if policy.signing_job is not None else ()))
        self.jobs = [
            {
                "id": index,
                "name": name,
                "run_id": 123,
                "run_attempt": 2,
                "head_sha": COMMIT,
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "labels": list(dict(VALIDATION.runner_labels).get(name, ("ubuntu-latest",))),
                "steps": [{"name": step, "status": "completed", "conclusion": "success"} for step in steps],
            }
            for index, (name, steps) in enumerate(requirements, 10)
        ]
        self.artifact = {
            "id": 42,
            "name": f"{policy.artifact_prefix}-123-2",
            "expired": False,
            "workflow_run": {"id": 123, "head_sha": COMMIT, "head_branch": "main", "repository_id": 7, "head_repository_id": 7},
        }
        self.certificate = {"runInvocationURI": "https://github.com/example/package/actions/runs/123/attempts/2", "buildTrigger": "push"}
        self.requests = []
        self.commands = []
        self.files = {}
        self.payload = b""
        self.duplicates = False

    def retain(self, manifest, archive=None):
        """Retain a manifest, its optional signature, and the requested package archive."""
        self.files = {"validation.json": manifest}
        if self.policy.signing_job is not None:
            self.files["validation.sigstore.json"] = b"inert bundle checked by modeled gh"
        if archive is not None:
            self.files["sample-1.2.3.crate"] = archive
        self.repack()

    def repack(self):
        """Rebuild the ZIP with stable timestamps and update the advertised size and digest."""
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, payload in self.files.items():
                # These model one immutable artifact across child processes.
                # ZIP wall-clock timestamps would change its digest between
                # normal/quiet runs, especially with coverage instrumentation.
                archive.writestr(zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0)), payload)
        self.payload = stream.getvalue()
        self.artifact.update(digest="sha256:" + hashlib.sha256(self.payload).hexdigest(), size_in_bytes=len(self.payload))

    def read(self, endpoint):
        """Serve a modeled GitHub API response and record the request."""
        self.requests.append(endpoint)
        if endpoint == "actions/workflows/ci.yml":
            result = self.workflow
        elif endpoint == "actions/runs/123":
            result = self.run
        elif endpoint.startswith("actions/workflows/13/runs?"):
            result = {"workflow_runs": [self.run], "total_count": 1}
        elif endpoint.startswith(f"actions/runs/123/attempts/{self.run['run_attempt']}/jobs?"):
            result = {"jobs": self.jobs, "total_count": len(self.jobs)}
        elif endpoint.startswith("actions/runs/123/artifacts?"):
            artifacts = [self.artifact, {**self.artifact, "id": 43}] if self.duplicates else [self.artifact]
            result = {"artifacts": artifacts, "total_count": len(artifacts)}
        elif endpoint == f"actions/artifacts/{self.artifact['id']}":
            result = self.artifact
        else:
            raise AssertionError(f"unexpected provider request: {endpoint}")
        return copy.deepcopy(result)

    def execute(self, args, **kwargs):
        """Handle expected GitHub artifact or attestation commands."""
        self.commands.append(args)
        if args == ["gh", "api", f"repos/example/package/actions/artifacts/{self.artifact['id']}/zip"]:
            kwargs["stdout"].write(self.payload)
            return subprocess.CompletedProcess(args, 0)
        if args[:3] == ["gh", "attestation", "verify"]:
            result = [{"verificationResult": {"signature": {"certificate": self.certificate}}}]
            return subprocess.CompletedProcess(args, 0, encode(result).decode("utf-8"))
        raise AssertionError(f"unexpected command: {args}")


class DraftProvider(Provider):
    """Authenticated provider model; records every attempted remote mutation."""

    def __init__(self):
        super().__init__(SOURCE)
        self.retain(create_evidence_manifest(SOURCE, "1.2.3", COMMIT, 123, 2, self.read))
        self.release = None
        self.posts = []
        self.calls = []
        self.listing = None
        self.response = None
        self.annotated = False
        self.ref = "refs/tags/v1.2.3"
        self.tag_commit = COMMIT
        self.on_create = lambda: None
        self.on_tag = lambda: None
        self.on_list = lambda: None

    def draft(self, **changes):
        """Build the GitHub draft release record with the requested field overrides."""
        return {
            "url": "https://api.github.com/repos/example/package/releases/91",
            "id": 91,
            "tag_name": "v1.2.3",
            "name": "Release 1.2.3",
            "body": "Generated notes café\r\n\n- Change.\n",
            "draft": True,
            "immutable": False,
            "prerelease": False,
            "assets": [],
            **changes,
        }

    def gh(self, program, args, **kwargs):
        """Model release creation, asset listing, and tag lookup through the GitHub CLI."""
        assert program == "gh"
        self.calls.append(args)
        endpoint = "repos/example/package"
        if "POST" in args:
            assert args[:5] == ["api", "--method", "POST", f"{endpoint}/releases", "--input"]
            request = json.loads(Path(args[5]).read_bytes())
            self.posts.append(request)
            self.release = self.draft(**request)
            self.on_create()
            value = self.release if self.response is None else self.response
        elif args[-1] == f"{endpoint}/releases?per_page=100":
            assert args[:3] == ["api", "--paginate", "--slurp"]
            self.on_list()
            value = [[self.release] if self.release is not None else []] if self.listing is None else self.listing
        elif args[-1] == f"{endpoint}/releases/91":
            if self.release is None:
                raise subprocess.CalledProcessError(1, args, stderr=b"not found")
            value = self.release
        elif args[-1] == f"{endpoint}/releases/91/assets?per_page=100":
            assert self.release is not None
            value = [self.release["assets"]]
        elif args[-1] == f"{endpoint}/git/ref/tags/v1.2.3":
            self.on_tag()
            value = {"ref": self.ref, "object": {"type": "tag" if self.annotated else "commit", "sha": "b" * 40 if self.annotated else self.tag_commit}}
        elif args[-1] == f"{endpoint}/git/tags/" + "b" * 40:
            value = {"sha": "b" * 40, "object": {"type": "commit", "sha": self.tag_commit}}
        else:
            raise AssertionError(f"unexpected draft provider command: {args}")
        if isinstance(value, Exception):
            raise value
        return subprocess.CompletedProcess(args, 0, value if isinstance(value, bytes) else encode(value), b"")


class WorkflowProvider(DraftProvider):
    """Extend draft publication with original-run history and handoff artifacts."""

    def __init__(self):
        super().__init__()
        self.tag_workflow: dict[str, Any] = {"id": 7, "path": ".github/workflows/prepare.yml", "name": "Prepare draft", "state": "active"}
        self.workflow_text = b'name: Prepare draft\non:\n  push:\n    tags: ["v*"]\njobs: {}\n'
        self.tag_run = dict(
            self.run,
            id=101,
            run_attempt=1,
            workflow_id=7,
            path=self.tag_workflow["path"],
            name="Prepare draft",
            head_branch="v1.2.3",
            status="in_progress",
            conclusion=None,
            pull_requests=[],
        )
        self.tag_jobs = [
            dict(
                self.jobs[0],
                id=201,
                name="verify",
                run_id=101,
                run_attempt=1,
                head_branch="v1.2.3",
                steps=[{"name": "Verify source", "status": "completed", "conclusion": "success"}],
            )
        ]
        self.history = [self.tag_run]
        self.recovery_run = {**self.tag_run, "id": 999}
        self.handoff_artifact: dict[str, Any] = {
            "id": 301,
            "name": "draft-handoff-101-1",
            "expired": False,
            "workflow_run": {"id": 101, "head_sha": COMMIT, "head_branch": "v1.2.3", "repository_id": 7, "head_repository_id": 7},
        }
        self.after_handoff = lambda: None
        self.check_conclusion = "success"

    def handoff(self, payload):
        """Retain the handoff bytes and publish their size and digest in the artifact record."""
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr(zipfile.ZipInfo("handoff.json", date_time=(2000, 1, 1, 0, 0, 0)), payload)
        self.handoff_zip = stream.getvalue()
        self.handoff_artifact.update(digest="sha256:" + hashlib.sha256(self.handoff_zip).hexdigest(), size_in_bytes=len(self.handoff_zip))

    @override
    def read(self, endpoint):
        """Serve a modeled GitHub API response and record the request."""
        if not hasattr(self, "tag_workflow"):
            return super().read(endpoint)
        self.requests.append(endpoint)
        if endpoint == "":
            return {"full_name": "example/package", "default_branch": "main"}
        if endpoint == "branches/main":
            return {"name": "main", "protected": True}
        if endpoint == f"compare/{COMMIT}...main":
            return {"status": "ahead"}
        if endpoint == f"commits/{COMMIT}/check-runs?filter=latest&per_page=100":
            return [
                {
                    "total_count": 1,
                    "check_runs": [
                        {"id": 501, "name": "native", "app": {"id": 15368}, "head_sha": COMMIT, "status": "completed", "conclusion": self.check_conclusion}
                    ],
                }
            ]
        if endpoint == f"actions/workflows/{Path(self.tag_workflow['path']).name}":
            return copy.deepcopy(self.tag_workflow)
        if endpoint == "actions/runs/101":
            return copy.deepcopy(self.tag_run)
        if endpoint == "actions/runs/999":
            return copy.deepcopy(self.recovery_run)
        if endpoint.startswith("actions/runs/101/attempts/1/jobs?"):
            return {"total_count": 1, "jobs": copy.deepcopy(self.tag_jobs)}
        if endpoint.startswith("actions/workflows/7/runs?"):
            page = int(endpoint.rsplit("=", 1)[1])
            return {"total_count": len(self.history), "workflow_runs": copy.deepcopy(self.history[(page - 1) * 100 : page * 100])}
        if endpoint == "actions/artifacts/301":
            return copy.deepcopy(self.handoff_artifact)
        if endpoint == f"contents/{self.tag_workflow['path']}?ref={COMMIT}":
            return {
                "type": "file",
                "path": self.tag_workflow["path"],
                "encoding": "base64",
                "size": len(self.workflow_text),
                "content": base64.b64encode(self.workflow_text).decode(),
                "sha": hashlib.sha1(b"blob " + str(len(self.workflow_text)).encode() + b"\0" + self.workflow_text).hexdigest(),
            }
        return super().read(endpoint)

    @override
    def execute(self, args, **kwargs):
        """Handle expected GitHub artifact or attestation commands."""
        if args == ["gh", "api", "repos/example/package/actions/artifacts/301/zip"]:
            kwargs["stdout"].write(self.handoff_zip)
            self.after_handoff()
            return subprocess.CompletedProcess(args, 0)
        return super().execute(args, **kwargs)


@contextlib.contextmanager
def workflow_io(provider: WorkflowProvider):
    """Route GitHub and Git calls from the draft workflow to this scenario's provider."""
    with (
        patch("research_repo_tools.release_validation.subprocess.run", provider.execute),
        patch("research_repo_tools.release_drafts.api", side_effect=lambda endpoint, **kw: provider.read(endpoint)),
        patch("research_repo_tools.draft_workflow.api", side_effect=lambda endpoint, **kw: provider.read(endpoint)),
        patch("research_repo_tools.release_validation.api", side_effect=lambda endpoint, **kw: provider.read(endpoint)),
        patch("research_repo_tools.release_drafts._api", side_effect=lambda root, repo, endpoint, **kw: provider.read(endpoint)),
        patch("research_repo_tools.release_assets.run_command_bytes", provider.gh),
        patch("research_repo_tools.release_publishing._api", side_effect=lambda root, repo, endpoint, **kw: provider.read(endpoint)),
        patch("research_repo_tools.release_interfaces.require_release_checkout"),
        patch("research_repo_tools.release_publishing.require_release_checkout"),
        patch("research_repo_tools.release_publishing.run_git_command", return_value=subprocess.CompletedProcess([], 0, COMMIT, "")),
    ):
        yield


def workflow_fixture(arguments):
    """Execute the installed module, substituting only bootstrap/provider/Git I/O."""
    expected = [
        "run",
        "--no-config",
        "--no-project",
        "--isolated",
        "--no-cache",
        "--python",
        "3.15",
        "--with",
        "research-repo-tools==0.3.0",
        "python",
        "-I",
        "-m",
        "research_repo_tools",
    ]
    assert arguments[: len(expected)] == expected, arguments
    root = Path(os.environ["FIXTURE_ROOT"])
    provider = WorkflowProvider()
    guide = (root / "guide.yml").read_bytes()
    provider.workflow_text = guide
    provider.tag_workflow.update(path=".github/workflows/prepare-draft.yml", name="Prepare consumer draft")
    provider.tag_run.update(path=provider.tag_workflow["path"], name=provider.tag_workflow["name"])
    provider.tag_jobs[0]["steps"] = [{"name": "Assert source and verify original signed evidence", "status": "completed", "conclusion": "success"}]
    provider.tag_jobs[0]["labels"] = ["ubuntu-24.04"]
    record = root / "provider.json"
    if record.exists():
        state = json.loads(record.read_bytes())
        provider.release, provider.posts = state["release"], state["posts"]
    handoff = root / "runner temp" / "handoff" / "handoff.json"
    if handoff.exists():
        provider.handoff(handoff.read_bytes())
    if os.environ.get("FIXTURE_DOWNLOAD_DRIFT"):
        provider.handoff_artifact["digest"] = "sha256:" + "f" * 64
    provider.recovery_run.update(path=provider.tag_workflow["path"], name=provider.tag_workflow["name"])
    with (
        workflow_io(provider),
        patch("research_repo_tools.release_interfaces.api", side_effect=lambda endpoint, **kw: provider.read(endpoint)),
        patch("research_repo_tools.toolchain.Runtime.sync", side_effect=AssertionError("draft workflow must not install contributor tools")),
        patch("research_repo_tools.toolchain.Runtime.inspect", side_effect=AssertionError("draft workflow must not probe contributor tools")),
    ):
        sys.argv = ["research_repo_tools", *arguments[len(expected) :]]
        try:
            runpy.run_module("research_repo_tools", run_name="__main__")
        finally:
            record.write_bytes(encode({"release": provider.release, "posts": provider.posts}))


class AuthenticatedDraft:
    """Prepare an observed checkout, isolated writer, and authenticated handoff for one scenario."""

    def __init__(self, stack: contextlib.ExitStack):
        from research_repo_tools.draft_workflow import DraftHandoffArtifact, plan_release_draft
        from research_repo_tools.release_receipts import TagPushPolicy, TagSource

        directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="authenticated draft café "))
        self.root = Path(directory).resolve()
        self.checkout, self.writer = self.root / "observed", self.root / "writer"
        self.checkout.mkdir()
        self.writer.mkdir()
        self.state = self.root / "durable-state"
        self.provider = WorkflowProvider()
        stack.enter_context(patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/package", "GITHUB_RUN_ID": "101", "GITHUB_RUN_ATTEMPT": "1"}))
        self.source = TagSource("refs/tags/v1.2.3", COMMIT)
        self.policy = TagPushPolicy(
            "example/package", ".github/workflows/prepare.yml", "Prepare draft", (("verify", ("Verify source",)),), artifact_prefix="draft-handoff"
        )
        stack.enter_context(workflow_io(self.provider))
        (self.checkout / "pyproject.toml").write_bytes(b'[project]\nname="sample"\nversion="1.2.3"\n')
        (self.checkout / "CHANGELOG.md").write_bytes(b"# Changelog\n\n## [1.2.3] - 2026-09-01\n\n- Verified notes.\n")
        self.settings = config.parse(
            {
                "publishing": {
                    "registry": "pypi",
                    "package": "sample",
                    "repository": "example/package",
                    "required-checks": [{"name": "native", "app-id": 15368}],
                }
            },
            root=self.checkout,
        )
        self.configuration = interface_policy(self.checkout)
        self.payload = plan_release_draft(self.settings, self.configuration, self.source, event="push")
        self.provider.handoff(self.payload)
        self.selected = DraftHandoffArtifact(101, 1, 301, self.provider.handoff_artifact["digest"][7:])

    def prepare(self, **changes):
        """Run authenticated draft preparation with this scenario’s selection and writer directory."""
        from research_repo_tools.draft_workflow import prepare_authenticated_draft

        options: dict[str, Any] = dict(output=self.state, executing_run_id=101, executing_attempt=1, approve=True)
        options.update(changes)
        return prepare_authenticated_draft(self.writer, self.policy, self.source, self.selected, **options)


if __name__ == "__main__":
    workflow_fixture(sys.argv[1:])
