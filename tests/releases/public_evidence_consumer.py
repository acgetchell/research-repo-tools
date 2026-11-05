"""Installed evidence contracts using inert provider data and real archive bytes."""

import contextlib
import copy
import gzip
import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import unittest
import urllib.error
import urllib.request
import urllib.response
import zipfile
import zlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from typing import override
from unittest.mock import patch

from research_repo_tools import cli, release_validation
from research_repo_tools.archives import extract_archive
from research_repo_tools.changelog import template
from research_repo_tools.files import RecoveryError
from research_repo_tools.process import run_git_bytes
from research_repo_tools.registry import lookup_version, verify_published_distributions
from research_repo_tools.release_assets import require_release_checkout
from research_repo_tools.release_evidence import (
    EvidencePolicy,
    PythonDistributionPolicy,
    create_evidence_manifest,
    verify_cargo_archive,
    verify_release_evidence,
)
from research_repo_tools.release_interfaces import load_evidence_policy, write_release_outputs
from research_repo_tools.release_metadata import PackageInfo, read_package_info
from research_repo_tools.release_validation import (
    ValidationPolicy,
    parse_release_json,
    read_pages,
    redact_release_diagnostic,
    verify_attestation,
    verify_distributions,
)

COMMIT = "a" * 40
TOOLS = {"cargo": "cargo 1.99.0 (fixture)", "rustc": "rustc 1.99.0\nhost: fixture"}
VALIDATION = ValidationPolicy(
    "example/package",
    ".github/workflows/ci.yml",
    "CI",
    (("linux", ("Native checks",)), ("macos", ("Native checks",)), ("windows", ("Native checks",)), ("package", ("Default and exact archive checks",))),
    runner_labels=(("linux", ("ubuntu-latest",)), ("macos", ("macos-latest",)), ("windows", ("windows-latest",))),
)
SOURCE = EvidencePolicy(VALIDATION, "sample", "release-evidence", "signed-source", ("sign", ("Capture", "Sign", "Upload")))
CARGO = EvidencePolicy(VALIDATION, "sample", "cargo-package", "cargo-archive")
PYTHON = EvidencePolicy(
    VALIDATION,
    "Example.App",
    "python-evidence",
    "python-distributions",
    package_kind="python",
    distributions=PythonDistributionPolicy(
        ("example_app-{version}-cp315-cp315-win_amd64.whl", "example_app-{version}-py3-none-any.whl", "example_app-{version}.tar.gz"),
        index_url="https://test.pypi.org",
        max_age_seconds=86400,
    ),
)


def encode(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")


def interface_policy(root, policy=SOURCE):
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
        text += (
            f"\n[distributions]\nfiles={json.dumps(policy.distributions.files)}\n"
            f"index-url={json.dumps(policy.distributions.index_url)}\nmax-age-seconds={policy.distributions.max_age_seconds}\n"
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


def crate(*, extra=None, vcs=None, manifest=None, kind=tarfile.REGTYPE):
    contents = {
        "sample-1.2.3/.cargo_vcs_info.json": encode(vcs if vcs is not None else {"git": {"sha1": COMMIT}, "path_in_vcs": ""}),
        "sample-1.2.3/Cargo.toml": manifest if manifest is not None else b'[package]\nname="sample"\nversion="1.2.3"\n',
        "sample-1.2.3/src/lib.rs": b"// synthetic\r\npub fn value() {}\n",
    }
    if extra is not None:
        contents[extra] = b"extra"
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:") as archive:
        for name, payload in contents.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            if name == extra:
                member.type = kind
            archive.addfile(member, io.BytesIO(payload))
    # Verify fixture names survived the writer, especially on Windows.
    with tarfile.open(fileobj=io.BytesIO(stream.getvalue()), mode="r:") as archive:
        assert archive.getnames() == list(contents)
    return gzip.compress(stream.getvalue(), mtime=0)


class Provider:
    def __init__(self, policy):
        self.policy = policy
        self.run = {
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
        self.workflow = {"id": 13, "path": ".github/workflows/ci.yml", "name": "CI", "state": "active"}
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
        self.files = {"validation.json": manifest}
        if self.policy.signing_job is not None:
            self.files["validation.sigstore.json"] = b"inert bundle checked by modeled gh"
        if archive is not None:
            self.files["sample-1.2.3.crate"] = archive
        self.repack()

    def repack(self):
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
        self.commands.append(args)
        if args == ["gh", "api", f"repos/example/package/actions/artifacts/{self.artifact['id']}/zip"]:
            kwargs["stdout"].write(self.payload)
            return subprocess.CompletedProcess(args, 0)
        if args[:3] == ["gh", "attestation", "verify"]:
            result = [{"verificationResult": {"signature": {"certificate": self.certificate}}}]
            return subprocess.CompletedProcess(args, 0, encode(result).decode("utf-8"))
        raise AssertionError(f"unexpected command: {args}")


class TestEvidenceConsumer(unittest.TestCase):
    def source(self):
        provider = Provider(SOURCE)
        manifest = create_evidence_manifest(SOURCE, "1.2.3", COMMIT, 123, 2, provider.read)
        document = json.loads(manifest)
        self.assertEqual(document["jobs"], {"linux": 10, "macos": 11, "windows": 12, "package": 13})
        self.assertEqual(
            document["run"],
            {
                "id": 123,
                "run_attempt": 2,
                "head_sha": COMMIT,
                "head_branch": "main",
                "workflow_id": 13,
                "path": ".github/workflows/ci.yml",
                "name": "CI",
                "event": "push",
                "repository_id": 7,
                "head_repository_id": 7,
            },
        )
        self.assertEqual(document["policy"]["guarantee"], "signed-source")
        self.assertNotIn("archive", document)
        provider.retain(manifest)
        return provider

    def verify(self, provider, **kwargs):
        with patch("research_repo_tools.release_validation.subprocess.run", provider.execute):
            return verify_release_evidence(provider.policy, "1.2.3", COMMIT, provider.read, **kwargs)

    def test_signed_source_and_exact_approval_handoff(self):
        provider = self.source()
        selected = self.verify(provider)
        self.assertEqual(json.loads(selected)["artifact"]["id"], 42)
        self.assertEqual(json.loads(selected)["jobs"]["sign"], 14)
        provider.requests.clear()
        self.assertEqual(self.verify(provider, expected=selected), selected)
        self.assertFalse(any("/runs?" in endpoint for endpoint in provider.requests))
        attestation = next(args for args in provider.commands if args[1:3] == ["attestation", "verify"])
        for option, value in (
            ("--repo", "example/package"),
            ("--signer-workflow", "example/package/.github/workflows/ci.yml"),
            ("--signer-digest", COMMIT),
            ("--source-digest", COMMIT),
            ("--source-ref", "refs/heads/main"),
            ("--predicate-type", "https://slsa.dev/provenance/v1"),
        ):
            self.assertEqual(attestation[attestation.index(option) + 1], value)
        self.assertIn("--deny-self-hosted-runners", attestation)

    def test_every_native_job_and_required_step_is_mandatory(self):
        for damage in ("missing", "duplicate", "failed", "pending", "step", "platform", "self-hosted", "attempt", "commit", "id"):
            with self.subTest(damage=damage):
                provider = self.source()
                if damage == "missing":
                    provider.jobs.pop(2)
                elif damage == "duplicate":
                    provider.jobs.append(copy.deepcopy(provider.jobs[2]))
                elif damage == "step":
                    provider.jobs[2]["steps"][0]["conclusion"] = "skipped"
                elif damage == "platform":
                    provider.jobs[2]["labels"] = ["ubuntu-latest"]
                elif damage == "self-hosted":
                    provider.jobs[2]["labels"].append("self-hosted")
                else:
                    field, value = {
                        "failed": ("conclusion", "failure"),
                        "pending": ("status", "in_progress"),
                        "attempt": ("run_attempt", 1),
                        "commit": ("head_sha", "b" * 40),
                        "id": ("id", True),
                    }[damage]
                    provider.jobs[2][field] = value
                with self.assertRaises(ValueError):
                    self.verify(provider)
                self.assertEqual(provider.commands, [])

    def test_wrong_context_and_artifact_rejected_before_download(self):
        for damage in (
            "repository",
            "fork",
            "workflow",
            "event",
            "branch",
            "commit",
            "expired",
            "artifact-repository",
            "artifact-commit",
            "artifact-attempt",
            "digest",
            "ambiguous",
        ):
            with self.subTest(damage=damage):
                provider = self.source()
                if damage == "repository":
                    provider.run["repository"]["full_name"] = "other/repo"
                elif damage == "fork":
                    provider.run["head_repository"]["id"] = 8
                elif damage in ("workflow", "event", "branch", "commit"):
                    field, value = {
                        "workflow": ("workflow_id", 14),
                        "event": ("event", "pull_request"),
                        "branch": ("head_branch", "dev"),
                        "commit": ("head_sha", "b" * 40),
                    }[damage]
                    provider.run[field] = value
                elif damage == "expired":
                    provider.artifact["expired"] = True
                elif damage.startswith("artifact-"):
                    if damage == "artifact-attempt":
                        provider.artifact["name"] = "release-evidence-123-1"
                    else:
                        field, value = ("repository_id", 8) if damage == "artifact-repository" else ("head_sha", "b" * 40)
                        provider.artifact["workflow_run"][field] = value
                elif damage == "digest":
                    provider.artifact["digest"] = None
                else:
                    provider.duplicates = True
                with self.assertRaises(ValueError):
                    self.verify(provider)
                self.assertEqual(provider.commands, [])

    def test_approval_rechecks_run_jobs_policy_and_artifact(self):
        for damage in ("attempt", "job", "artifact", "policy", "bool-id", "extra", "manifest"):
            with self.subTest(damage=damage):
                provider = self.source()
                selected = self.verify(provider)
                if damage == "attempt":
                    provider.run["run_attempt"] = 3
                elif damage == "job":
                    provider.jobs[2]["id"] = 22
                elif damage == "artifact":
                    provider.artifact["id"] = 43
                elif damage == "policy":
                    provider.policy = replace(SOURCE, validation=replace(VALIDATION, runner_labels=()))
                elif damage == "manifest":
                    provider.files["validation.json"] += b" "
                    provider.repack()
                else:
                    pin = json.loads(selected)
                    if damage == "bool-id":
                        pin["run"]["repository_id"] = 7.0
                    else:
                        pin["unrecognized"] = "ignored?"
                    selected = encode(pin)
                with self.assertRaises(ValueError):
                    self.verify(provider, expected=selected)

    def test_signed_manifest_and_signing_invocation_reject_tampering(self):
        for damage in ("manifest", "float-id", "signature-attempt", "signature-event", "unsigned", "zip-bytes"):
            with self.subTest(damage=damage):
                provider = self.source()
                if damage in ("manifest", "float-id"):
                    document = json.loads(provider.files["validation.json"])
                    document["jobs"]["windows"] = 99 if damage == "manifest" else 12.0
                    provider.files["validation.json"] = encode(document)
                    provider.repack()
                elif damage == "signature-attempt":
                    provider.certificate["runInvocationURI"] = provider.certificate["runInvocationURI"].replace("attempts/2", "attempts/1")
                elif damage == "signature-event":
                    provider.certificate["buildTrigger"] = "pull_request"
                elif damage == "unsigned":
                    del provider.files["validation.sigstore.json"]
                    provider.repack()
                else:
                    provider.payload += b"tampered"
                with self.assertRaises(ValueError):
                    self.verify(provider)

    def test_signature_verification_failure_is_never_accepted(self):
        with patch("research_repo_tools.release_validation.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["gh"])):
            with self.assertRaises(subprocess.CalledProcessError):
                verify_attestation(Path("subject"), Path("bundle"), COMMIT, VALIDATION.workflow, "refs/heads/main", 123, 2, repository=VALIDATION.repository)

    def test_provider_changes_during_download_reject(self):
        provider = self.source()

        def execute(args, **kwargs):
            result = provider.execute(args, **kwargs)
            if args[1] == "api":
                provider.jobs[2]["id"] = 99
            return result

        with patch("research_repo_tools.release_validation.subprocess.run", execute):
            with self.assertRaisesRegex(ValueError, "changed during"):
                verify_release_evidence(SOURCE, "1.2.3", COMMIT, provider.read)

    def test_cargo_archive_and_reproduction_are_separate_from_source_validation(self):
        provider = Provider(CARGO)
        with tempfile.TemporaryDirectory(prefix="archive café ") as temporary:
            path = Path(temporary) / "sample.crate"
            payload = crate()
            path.write_bytes(payload)
            manifest = create_evidence_manifest(CARGO, "1.2.3", COMMIT, 123, 2, provider.read, archive=path, tools=TOOLS)
            self.assertEqual(json.loads(manifest)["archive"]["sha256"], hashlib.sha256(payload).hexdigest())
            provider.retain(manifest, payload)
            selected = self.verify(provider)
            self.assertEqual(self.verify(provider, expected=selected, reproduced_archive=path, reproduced_tools=TOOLS), selected)
            self.assertFalse(any(args[1] == "attestation" for args in provider.commands))
            with self.assertRaisesRegex(ValueError, "tools differ"):
                self.verify(provider, expected=selected, reproduced_archive=path, reproduced_tools={**TOOLS, "cargo": "another cargo"})
            path.write_bytes(crate(extra="sample-1.2.3/src/extra.rs"))
            with self.assertRaisesRegex(ValueError, "bytes or tools"):
                self.verify(provider, expected=selected, reproduced_archive=path, reproduced_tools=TOOLS)
            with self.assertRaisesRegex(ValueError, "both"):
                self.verify(provider, reproduced_archive=path)
            with self.assertRaises(ValueError):
                self.verify(self.source(), reproduced_archive=path, reproduced_tools=TOOLS)

    def test_cargo_rejects_wrong_source_package_and_unsafe_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.crate"
            for payload in (
                crate(vcs={"git": {"sha1": "b" * 40}, "path_in_vcs": ""}),
                crate(vcs={"git": {"sha1": COMMIT, "dirty": True}, "path_in_vcs": ""}),
                crate(vcs={"git": {"sha1": COMMIT}, "path_in_vcs": "nested"}),
                crate(manifest=b'[package]\nname="different"\nversion="1.2.3"\n'),
                crate(extra="sample-1.2.3/../escape"),
                crate(extra="sample-1.2.3/link", kind=tarfile.SYMTYPE),
                crate(extra="sample-1.2.3/back\\slash"),
                crate(extra="/absolute"),
            ):
                with self.subTest(digest=hashlib.sha256(payload).hexdigest()):
                    path.write_bytes(payload)
                    with self.assertRaises(ValueError):
                        verify_cargo_archive(path, "sample", "1.2.3", COMMIT)
            path.write_bytes(crate())
            with patch("research_repo_tools.release_evidence.MAX_CRATE_MEMBERS", 2):
                with self.assertRaisesRegex(ValueError, "inventory exceeds"):
                    verify_cargo_archive(path, "sample", "1.2.3", COMMIT)
            with patch("research_repo_tools.release_evidence.MAX_METADATA_BYTES", 1):
                with self.assertRaisesRegex(ValueError, "metadata exceeds"):
                    verify_cargo_archive(path, "sample", "1.2.3", COMMIT)
            with patch("research_repo_tools.release_evidence.MAX_CRATE_BYTES", 4096):
                with self.assertRaisesRegex(ValueError, "expanded size"):
                    verify_cargo_archive(path, "sample", "1.2.3", COMMIT)

    def test_cargo_archive_read_errors_preserve_the_cause(self):
        cases = (
            ("not-gzip", b"not a gzip archive", gzip.BadGzipFile),
            ("truncated-gzip", crate()[:-1], EOFError),
            ("invalid-deflate", bytes.fromhex("1f8b08000000000002ff07"), zlib.error),
            ("not-tar", gzip.compress(b"not a tar archive", mtime=0), tarfile.ReadError),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.crate"
            for name, payload, error_type in cases:
                with self.subTest(name=name):
                    # Verify the serialized corruption reaches the expected reader.
                    with self.assertRaises(error_type):
                        expanded = gzip.decompress(payload)
                        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:"):
                            pass
                    path.write_bytes(payload)
                    with self.assertRaisesRegex(ValueError, "not a readable gzip/tar package") as caught:
                        verify_cargo_archive(path, "sample", "1.2.3", COMMIT)
                    self.assertIsInstance(caught.exception.__cause__, error_type)
                    self.assertEqual(path.read_bytes(), payload)
            path.write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "nonempty byte bound") as caught:
                verify_cargo_archive(path, "sample", "1.2.3", COMMIT)
            self.assertIsNone(caught.exception.__cause__)

    def test_json_and_pagination_fail_closed(self):
        for payload in (b'{"id":1,"id":2}', b'{"id":NaN}', b'{"id":1e999}', b"\xff", b"[" * 2000 + b"]" * 2000):
            with self.subTest(payload=payload[:30]), self.assertRaises(ValueError):
                parse_release_json(payload)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            read_pages(lambda endpoint: {"jobs": [{"id": 1}], "total_count": 2}, "jobs", "jobs")
        with self.assertRaisesRegex(ValueError, "bounded page limit"):
            read_pages(lambda endpoint: {"jobs": [{}] * 100}, "jobs", "jobs")

    def test_transport_diagnostics_redact_signed_urls_and_credentials(self):
        message = redact_release_diagnostic(b"failed https://user:secret@example.org/artifact?sig=sensitive#fragment ghp_inert github_pat_inert")
        self.assertIn("https://example.org/artifact?<redacted>", message)
        for secret in ("user", "secret", "sensitive", "fragment", "ghp_inert", "github_pat_inert"):
            self.assertNotIn(secret, message)

    def test_clean_checkout_assertion_is_observed_and_read_only(self):
        commands = []
        executables = []

        def execute(command, args, **kwargs):
            self.assertTrue(Path(command).is_absolute())
            self.assertFalse(any(key.upper().startswith("GIT_") for key in kwargs["env"]))
            self.assertEqual(kwargs["env"]["CHECKOUT_FIXTURE"], "preserved")
            executables.append(Path(command))
            commands.append(args)
            if "ls-files" in args or "submodule" in args:
                return subprocess.CompletedProcess(args, 0, b"H tracked\0", b"")
            return subprocess.CompletedProcess(args, 0, b"" if "status" in args else COMMIT.encode(), b"")

        with (
            patch.dict(os.environ, {"GIT_DIR": "elsewhere", "git_work_tree": "elsewhere", "Git_Index_File": "elsewhere", "CHECKOUT_FIXTURE": "preserved"}),
            patch("research_repo_tools.release_assets.run_command_bytes", execute),
        ):
            require_release_checkout(Path("source"), COMMIT)
        prefix = ["--no-pager", "--no-optional-locks", "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "diff.autoRefreshIndex=false"]
        self.assertEqual(len(set(executables)), 1)
        self.assertEqual(
            commands,
            [
                [*prefix, "rev-parse", "HEAD"],
                [*prefix, "ls-files", "-v", "-z"],
                [*prefix, "submodule", "foreach", "--quiet", "--recursive", shlex.join([executables[0].as_posix(), *prefix, "ls-files", "-v", "-z"])],
                [
                    *prefix,
                    "status",
                    "--porcelain",
                    "-z",
                    "--untracked-files=all",
                    "--ignore-submodules=none",
                ],
                [*prefix, "rev-parse", "HEAD"],
            ],
        )
        for outputs in ((COMMIT, "?? dirty", COMMIT), (COMMIT, "", "b" * 40)):
            responses = [subprocess.CompletedProcess([], 0, value.encode(), b"") for value in (outputs[0], "", "", *outputs[1:])]
            with patch("research_repo_tools.release_assets.run_command_bytes", side_effect=responses):
                with self.assertRaisesRegex(ValueError, "observed release checkout"):
                    require_release_checkout(Path("source"), COMMIT)

    def test_install_free_checkout_observation_rejects_hidden_index_entries(self):
        import runpy

        observe = runpy.run_path(release_validation.__file__)["_checkout_identity"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project = b'[project]\nname="research-repo-tools"\nversion="0.2.3"\n'
            (root / "pyproject.toml").write_bytes(project)
            for hidden in (None, b"h tracked\0", b"S tracked\0"):

                def git(args, **kwargs):
                    self.assertTrue(Path(args[0]).is_absolute())
                    self.assertIn("--no-optional-locks", args)
                    self.assertIn("core.fsmonitor=false", args)
                    if "--show-toplevel" in args:
                        output = str(root).encode("utf-8")
                    elif "rev-parse" in args:
                        output = COMMIT.encode()
                    elif "ls-files" in args:
                        output = b"H tracked\0" if hidden is None else hidden
                    else:
                        output = b""
                    return subprocess.CompletedProcess(args, 0, output, b"")

                with self.subTest(hidden=hidden), patch("subprocess.run", git):
                    if hidden is None:
                        self.assertEqual(observe(root), ("v0.2.3", COMMIT))
                    else:
                        with self.assertRaisesRegex(ValueError, "requires a clean checkout.*index entries"):
                            observe(root)
                self.assertEqual((root / "pyproject.toml").read_bytes(), project)
                self.assertEqual([path.name for path in root.iterdir()], ["pyproject.toml"])

    def test_checkout_rejects_untrusted_only_path_before_launching_git(self):
        import runpy

        standalone = runpy.run_path(release_validation.__file__)["_checkout_identity"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for path in ("", "bin", str(root / "bin")):
                with (
                    self.subTest(path=path),
                    patch.dict(os.environ, {"PATH": path}),
                    patch("subprocess.run", side_effect=AssertionError("untrusted executable launched")),
                ):
                    with self.assertRaisesRegex(ValueError, "absolute PATH entry outside the checkout"):
                        require_release_checkout(root, COMMIT)
                    with self.assertRaisesRegex(ValueError, "absolute PATH entry outside the checkout"):
                        standalone(root)


class TestEvidenceInterfaces(unittest.TestCase):
    @override
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="evidence café ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        (self.checkout / "Cargo.toml").write_bytes(b'[package]\nname="sample"\nversion="1.2.3"\n')
        self.policy = interface_policy(self.root)
        self.output = self.root / "selection.json"
        self.command_file = self.root / "actions-output"
        self.command_file.write_bytes(b"prior=value\n")
        self.args = [
            "release",
            "evidence",
            "verify",
            "evidence.toml",
            "--checkout",
            str(self.checkout),
            "--commit",
            COMMIT,
            "--version",
            "1.2.3",
            "--output",
            str(self.output),
            "--github-output",
            str(self.command_file),
        ]

    def test_configured_guarantees_are_distinct_and_strict(self):
        for policy in (SOURCE, CARGO):
            with self.subTest(guarantee=policy.guarantee):
                self.assertEqual(load_evidence_policy(interface_policy(self.root, policy)), policy)
        original = interface_policy(self.root).read_bytes()
        for before, after in (
            (b"schema = 1", b"schema = true"),
            (b"schema = 1", b"schema = 2"),
            (b'guarantee = "signed-source"', b'guarantee = "unsigned"'),
            (b'workflow-name = "CI"', b'workflow-name = "CI"\nunknown = "field"'),
        ):
            self.policy.write_bytes(original.replace(before, after))
            with self.assertRaises(ValueError):
                load_evidence_policy(self.policy)

    def test_cli_file_handoff_and_post_approval_attempt_revalidation(self):
        provider = Provider(SOURCE)
        provider.retain(create_evidence_manifest(SOURCE, "1.2.3", COMMIT, 123, 2, provider.read))
        with (
            patch("research_repo_tools.release_interfaces.api", side_effect=lambda endpoint, **kwargs: provider.read(endpoint)),
            patch("research_repo_tools.release_interfaces.require_release_checkout") as observe,
            patch("research_repo_tools.release_validation.subprocess.run", provider.execute),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cli.main(["--root", str(self.root), *self.args]), 0)
            self.assertGreaterEqual(observe.call_count, 3)
            original = self.output.read_bytes()
            command = self.command_file.read_bytes()
            identity_line = next(line for line in command.splitlines() if line.startswith(b"identity="))
            self.assertEqual(json.loads(identity_line.split(b"=", 1)[1]), json.loads(original))
            provider.run["run_attempt"] = 3
            args = [*self.args, "--expected", str(self.output)]
            args[args.index("--output") + 1] = str(self.root / "revalidated.json")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["--root", str(self.root), *args]), 1)
            self.assertFalse((self.root / "revalidated.json").exists())
            self.assertEqual(self.output.read_bytes(), original)
            self.assertEqual(self.command_file.read_bytes(), command)

    def test_relative_evidence_paths_follow_root_from_another_directory(self):
        caller = self.root / "caller"
        caller.mkdir()
        args = [*self.args]
        for flag in ("--checkout", "--output", "--github-output"):
            index = args.index(flag) + 1
            args[index] = str(Path(args[index]).relative_to(self.root))
        with contextlib.chdir(caller), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(interface_fixture(self.root, args), 0)
            original = self.output.read_bytes()
            args += ["--expected", "selection.json"]
            args[args.index("--output") + 1] = "revalidated.json"
            self.assertEqual(interface_fixture(self.root, args), 0)
        self.assertEqual((self.root / "revalidated.json").read_bytes(), original)
        self.assertIn(b"identity=", self.command_file.read_bytes())
        self.assertEqual(list(caller.iterdir()), [])

    def test_create_requires_executing_push_and_observed_package(self):
        provider = Provider(SOURCE)
        args = [*self.args]
        args[2] = "create"
        args += ["--run-id", "123", "--attempt", "2"]
        environment = {
            "GITHUB_REPOSITORY": "example/package",
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": COMMIT,
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "2",
        }
        with (
            patch("research_repo_tools.release_interfaces.api", side_effect=lambda endpoint, **kwargs: provider.read(endpoint)),
            patch("research_repo_tools.release_interfaces.require_release_checkout"),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with patch.dict(os.environ, {**environment, "GITHUB_EVENT_NAME": "pull_request"}):
                self.assertEqual(cli.main(["--root", str(self.root), *args]), 1)
            self.assertFalse(self.output.exists())
            with patch.dict(os.environ, environment):
                self.assertEqual(cli.main(["--root", str(self.root), *args]), 0)
            self.assertEqual(self.output.read_bytes(), create_evidence_manifest(SOURCE, "1.2.3", COMMIT, 123, 2, provider.read))
            (self.checkout / "Cargo.toml").write_bytes(b'[package]\nname="wrong"\nversion="1.2.3"\n')
            with contextlib.redirect_stderr(io.StringIO()) as diagnostics:
                self.assertEqual(cli.main(["--root", str(self.root), *self.args]), 1)
            self.assertIn("observed checkout package/version differs from release assertions", diagnostics.getvalue())

    def test_cli_preserves_nested_recovery_diagnostics_and_redacts_secrets(self):
        failure = ExceptionGroup(
            "File update failed and rollback was incomplete",
            [
                OSError("publication denied ghp_test_secret"),
                RecoveryError(self.output, None, PermissionError("rollback denied")),
            ],
        )
        with (
            patch("research_repo_tools.release_interfaces.run_evidence", side_effect=failure),
            contextlib.redirect_stderr(io.StringIO()) as diagnostics,
        ):
            self.assertEqual(cli.main(["--root", str(self.root), *self.args]), 1)
        message = diagnostics.getvalue()
        self.assertIn("publication denied <redacted-token>", message)
        self.assertIn(f"failed to restore {self.output}: rollback denied", message)
        self.assertNotIn("ghp_test_secret", message)

    def test_missing_unwritable_and_failed_output_transactions_preserve_records(self):
        from research_repo_tools.files import _replace_path

        self.output.write_bytes(b"original")
        prior = self.command_file.read_bytes()
        with self.assertRaises(FileNotFoundError):
            write_release_outputs(self.output, b"original", github_output=self.root / "missing", values={"identity": "value"})
        self.assertFalse((self.root / "missing").exists())

        def fail_command(source, destination):
            if destination == self.command_file and source.suffix == ".tmp":
                raise OSError("output denied")
            _replace_path(source, destination)

        with patch("research_repo_tools.files._replace_path", side_effect=fail_command), self.assertRaises(OSError):
            write_release_outputs(self.output, b"original", github_output=self.command_file, values={"identity": "value"})
        self.assertEqual(self.output.read_bytes(), b"original")
        self.assertEqual(self.command_file.read_bytes(), prior)
        opening = Path.open

        def unwritable(path, mode="r", *args, **kwargs):
            if path == self.command_file and mode == "rb+":
                raise PermissionError("unwritable command file")
            return opening(path, mode, *args, **kwargs)

        with patch.object(Path, "open", unwritable), self.assertRaises(PermissionError):
            write_release_outputs(self.output, b"original", github_output=self.command_file, values={"identity": "value"})
        with self.assertRaisesRegex(ValueError, "different evidence"):
            write_release_outputs(self.output, b"changed")
        self.assertEqual(self.command_file.read_bytes(), prior)
        self.assertEqual(self.output.read_bytes(), b"original")

    def test_normal_and_quiet_just_produce_the_same_file_based_identity(self):
        import shlex

        just = shutil.which("just")
        self.assertIsNotNone(just, "Just is a required fixture prerequisite")
        assert just is not None
        driver = self.root / "driver.py"
        driver.write_text(
            "import runpy, sys\nfrom pathlib import Path\n"
            f"suite = runpy.run_path({str(Path(__file__).resolve())!r})\n"
            "raise SystemExit(suite['interface_fixture'](Path.cwd(), sys.argv[1:]))\n",
            encoding="utf-8",
            newline="\n",
        )
        justfile = template("justfile").replace(
            'uv run --locked --group dev research-repo-tools release evidence "$@"',
            f'{shlex.quote(Path(sys.executable).as_posix())} driver.py release evidence "$@"',
        )
        (self.root / "justfile").write_text(justfile, encoding="utf-8", newline="\n")
        records = []
        for quiet in (False, True):
            self.output.unlink(missing_ok=True)
            self.command_file.write_bytes(b"prior=value\n")
            result = subprocess.run(
                [just, *(["--quiet"] if quiet else []), "release-evidence", *self.args[2:]], cwd=self.root, capture_output=True, encoding="utf-8", timeout=60
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('"artifact"', result.stdout)
            records.append((self.output.read_bytes(), self.command_file.read_bytes()))
        self.assertEqual(records[0], records[1])


def python_distributions(*, package="example-app", version="1.2.3", extra=None, members=()):
    """Tiny inert archives; content bytes and original member names are deliberate."""
    metadata = f"Metadata-Version: 2.4\nName: {package}\nVersion: {version}\n".encode()
    assert PYTHON.distributions is not None
    result = {}
    for name in PYTHON.distributions.filenames(PYTHON.package, "1.2.3"):
        stream = io.BytesIO()
        if name.endswith(".whl"):
            entries = [("example_app-1.2.3.dist-info/METADATA", metadata), ("example_app/__init__.py", b"# caf\xc3\xa9\r\n"), *members]
            if extra is not None:
                entries.append((extra, b"adversarial"))
            with zipfile.ZipFile(stream, "w") as archive:
                for member, payload in entries:
                    entry = zipfile.ZipInfo()
                    # Assign after construction: Windows ZipInfo normalizes
                    # backslashes, which would erase the malformed-path fixture.
                    entry.filename = member + "/" if payload is None else member
                    archive.writestr(entry, b"" if payload is None else payload)
            with zipfile.ZipFile(io.BytesIO(stream.getvalue())) as archive:
                assert [entry.orig_filename for entry in archive.infolist()] == [member + "/" if payload is None else member for member, payload in entries]
        else:
            entries = [("example_app-1.2.3/PKG-INFO", metadata), *((f"example_app-1.2.3/{member}", payload) for member, payload in members)]
            with tarfile.open(fileobj=stream, mode="w:") as archive:
                for member, payload in entries:
                    entry = tarfile.TarInfo(member)
                    entry.type = tarfile.DIRTYPE if payload is None else tarfile.REGTYPE
                    entry.size = 0 if payload is None else len(payload)
                    archive.addfile(entry, None if payload is None else io.BytesIO(payload))
            with tarfile.open(fileobj=io.BytesIO(stream.getvalue()), mode="r:") as archive:
                assert [entry.name for entry in archive] == [member for member, _ in entries]
            stream = io.BytesIO(gzip.compress(stream.getvalue(), mtime=0))
        result[name] = stream.getvalue()
    return result


class TestPythonDistributionEvidence(unittest.TestCase):
    @override
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="python evidence café ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.files = python_distributions()
        self.write_distributions(self.files)
        self.hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in self.files.items()}

    def write_distributions(self, files):
        for name, payload in files.items():
            (self.dist / name).write_bytes(payload)

    def provider(self, policy=PYTHON):
        provider = Provider(policy)
        now = datetime.now(UTC)
        provider.run["created_at"] = (now - timedelta(hours=2)).isoformat()
        provider.artifact.update(created_at=(now - timedelta(hours=1)).isoformat(), expires_at=(now + timedelta(days=7)).isoformat())
        manifest = create_evidence_manifest(policy, "1.2.3", COMMIT, 123, 2, provider.read, distributions=self.dist)
        provider.retain(manifest)
        provider.files.update(self.files)
        provider.repack()
        return provider

    def verify(self, provider, **kwargs):
        with patch("research_repo_tools.release_validation.subprocess.run", provider.execute):
            return verify_release_evidence(provider.policy, "1.2.3", COMMIT, provider.read, **kwargs)

    def test_explicit_python_identity_preserves_cargo_and_virtual_workspace_defaults(self):
        (self.root / "pyproject.toml").write_bytes(b'[project]\nname="Example.App"\nversion="1.2.3"\n')
        for cargo, expected in (
            (b'[workspace]\nmembers=[]\n[workspace.package]\nversion="9.8.7"\n', PackageInfo(None, "9.8.7")),
            (b'[package]\nname="rust-package"\nversion="9.8.7"\n', PackageInfo("rust-package", "9.8.7")),
        ):
            with self.subTest(cargo=cargo):
                (self.root / "Cargo.toml").write_bytes(cargo)
                self.assertEqual(read_package_info(self.root), expected)
                self.assertEqual(read_package_info(self.root, kind="cargo"), expected)
                self.assertEqual(read_package_info(self.root, kind="python"), PackageInfo("Example.App", "1.2.3"))
        (self.root / "pyproject.toml").write_bytes(b'[project]\nname="Example.App"\ndynamic=["version"]\n')
        with self.assertRaisesRegex(ValueError, "version"):
            read_package_info(self.root, kind="python")
        with self.assertRaisesRegex(ValueError, "kind"):
            read_package_info(self.root, kind="unknown")  # ty: ignore[invalid-argument-type]  Exercise untyped callers.

    def test_policy_rejects_implicit_identity_unsafe_inventory_and_destination(self):
        assert PYTHON.distributions is not None
        for changes in (
            {"package_kind": "auto"},
            {"distributions": None},
            {"guarantee": "cargo-archive"},
            {"distributions": replace(PYTHON.distributions, files=("../escape-{version}.whl", "example_app-{version}.tar.gz"))},
            {"distributions": replace(PYTHON.distributions, files=("example_app-{version}.tar.gz",))},
            {"distributions": replace(PYTHON.distributions, files=(*PYTHON.distributions.files, PYTHON.distributions.files[0]))},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(PYTHON, **changes)
        for changes in ({"index_url": "http://pypi.org"}, {"index_url": "https://user:secret@pypi.org"}, {"max_age_seconds": True}, {"max_age_seconds": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(PYTHON.distributions, **changes)

    def test_complete_rerun_requires_new_evidence_and_new_approval(self):
        provider = self.provider()
        original = self.verify(provider)
        provider.run["run_attempt"] = 3
        provider.artifact["id"] = 43
        provider.artifact["name"] = "python-evidence-123-3"
        for job in provider.jobs:
            job["run_attempt"] = 3
            job["id"] += 100
        # Keeping the previous manifest cannot qualify a new complete attempt.
        with self.assertRaises(ValueError):
            self.verify(provider)
        manifest = create_evidence_manifest(PYTHON, "1.2.3", COMMIT, 123, 3, provider.read, distributions=self.dist)
        provider.retain(manifest)
        provider.files.update(self.files)
        provider.repack()
        with self.assertRaisesRegex(ValueError, "attempt changed"):
            self.verify(provider, expected=original)
        replacement = self.verify(provider)
        self.assertEqual(json.loads(replacement)["run"]["run_attempt"], 3)
        self.assertEqual(json.loads(replacement)["files"], self.hashes)
        self.assertEqual(self.verify(provider, expected=replacement), replacement)

    def test_python_signed_source_remains_a_separate_guarantee(self):
        policy = replace(SOURCE, package="Example.App", package_kind="python")
        provider = Provider(policy)
        provider.retain(create_evidence_manifest(policy, "1.2.3", COMMIT, 123, 2, provider.read))
        selected = self.verify(provider)
        self.assertNotIn("files", json.loads(selected))
        with self.assertRaisesRegex(ValueError, "distribution inputs"):
            self.verify(provider, distributions=self.dist)

    def test_incomplete_native_gates_cannot_create_python_evidence(self):
        provider = Provider(PYTHON)
        provider.jobs[2]["conclusion"] = "failure"
        with self.assertRaisesRegex(ValueError, "windows did not succeed"):
            create_evidence_manifest(PYTHON, "1.2.3", COMMIT, 123, 2, provider.read, distributions=self.dist)

    def test_before_tagging_retained_bytes_and_approval_are_exact_with_optional_signatures(self):
        for signing in (None, SOURCE.signing_job):
            with self.subTest(signing=signing):
                provider = self.provider(replace(PYTHON, signing_job=signing))
                selected = self.verify(provider, distributions=self.dist)
                document = json.loads(selected)
                self.assertEqual(document["files"], self.hashes)
                self.assertEqual(document["run"]["head_sha"], COMMIT)
                self.assertEqual(document["policy"]["package_kind"], "python")
                self.assertEqual(document["policy"]["distributions"]["index_url"], "https://test.pypi.org")
                self.assertIn("expires_at", document["freshness"])
                self.assertEqual(self.verify(provider, expected=selected, distributions=self.dist), selected)
                self.assertEqual(any(args[1] == "attestation" for args in provider.commands), signing is not None)
                self.assertFalse(any("tags" in request or "releases" in request for request in provider.requests))
        self.write_distributions(python_distributions(extra="example_app/changed.py"))
        with self.assertRaisesRegex(ValueError, "publication distribution bytes"):
            self.verify(provider, expected=selected, distributions=self.dist)

    def test_bad_evidence_never_falls_back_or_downloads(self):
        for damage in (
            "pending",
            "failed",
            "cancelled",
            "stale",
            "expired",
            "ambiguous",
            "wrong-source",
            "artifact-source",
            "artifact-digest",
            "fork",
            "dispatch",
            "pr",
            "wrong-ref",
            "partial-rerun",
            "step",
            "job-attempt",
            "untrusted-runner",
            "missing-timestamp",
            "future",
        ):
            with self.subTest(damage=damage):
                provider = self.provider()
                if damage == "pending":
                    provider.run["status"] = "in_progress"
                elif damage in ("failed", "cancelled"):
                    provider.run["conclusion"] = "failure" if damage == "failed" else "cancelled"
                elif damage == "stale":
                    provider.run["created_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
                elif damage == "expired":
                    provider.artifact["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
                elif damage == "ambiguous":
                    provider.duplicates = True
                elif damage == "wrong-source":
                    provider.run["head_sha"] = "b" * 40
                elif damage == "artifact-source":
                    provider.artifact["workflow_run"]["head_sha"] = "b" * 40
                elif damage == "artifact-digest":
                    provider.artifact["digest"] = None
                elif damage == "fork":
                    provider.run["head_repository"]["id"] = 8
                elif damage in ("dispatch", "pr"):
                    provider.run["event"] = "workflow_dispatch" if damage == "dispatch" else "pull_request"
                elif damage == "wrong-ref":
                    provider.run["head_branch"] = "other"
                elif damage == "partial-rerun":
                    provider.jobs.pop()
                elif damage == "step":
                    provider.jobs[2]["steps"][0]["conclusion"] = "skipped"
                elif damage == "job-attempt":
                    provider.jobs[2]["run_attempt"] = 1
                elif damage == "untrusted-runner":
                    provider.jobs[2]["labels"].append("self-hosted")
                elif damage == "missing-timestamp":
                    del provider.artifact["created_at"]
                else:
                    provider.artifact["created_at"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
                with self.assertRaises(ValueError):
                    self.verify(provider)
                self.assertEqual(provider.commands, [])

    def test_approval_rechecks_policy_attempt_jobs_artifact_retention_and_manifest(self):
        for damage in ("attempt", "jobs", "artifact", "policy", "retention", "manifest", "wheel", "zip", "missing", "extra"):
            with self.subTest(damage=damage):
                provider = self.provider()
                selected = self.verify(provider)
                if damage == "attempt":
                    provider.run["run_attempt"] = 3
                elif damage == "jobs":
                    provider.jobs[0]["id"] = 99
                elif damage == "artifact":
                    provider.artifact["id"] = 43
                elif damage == "policy":
                    assert PYTHON.distributions is not None
                    provider.policy = replace(PYTHON, distributions=replace(PYTHON.distributions, index_url="https://pypi.org"))
                elif damage == "retention":
                    provider.artifact["expires_at"] = (datetime.now(UTC) + timedelta(days=6)).isoformat()
                elif damage == "zip":
                    provider.payload += b"changed"
                else:
                    if damage == "manifest":
                        document = json.loads(provider.files["validation.json"])
                        document["files"][next(iter(self.files))] = "0" * 64
                        provider.files["validation.json"] = encode(document)
                    elif damage == "wheel":
                        provider.files.update(python_distributions(extra="example_app/new.py"))
                    elif damage == "missing":
                        del provider.files[next(iter(self.files))]
                    else:
                        provider.files["extra.txt"] = b"extra"
                    provider.repack()
                with self.assertRaises(ValueError):
                    self.verify(provider, expected=selected)
                # Fresh selection still cannot accept inconsistent retained bytes.
                if damage in ("manifest", "wheel", "zip", "missing", "extra"):
                    with self.assertRaises(ValueError):
                        self.verify(provider)

    def test_consumer_distribution_inventory_metadata_and_unsafe_archives(self):
        names = tuple(self.files)
        self.assertEqual(verify_distributions(self.dist, "v1.2.3", package="example.app", filenames=names), self.hashes)
        with self.assertRaisesRegex(ValueError, "exactly"):
            verify_distributions(self.dist, "v1.2.3")
        for files in (
            python_distributions(package="wrong"),
            python_distributions(version="1.2.4"),
            python_distributions(extra="../escape"),
            python_distributions(extra="example_app/back\\slash"),
            python_distributions(extra="example_app/CON"),
            python_distributions(extra="EXAMPLE_APP/__INIT__.py"),
        ):
            self.write_distributions(files)
            with self.assertRaises(ValueError):
                verify_distributions(self.dist, "v1.2.3", package=PYTHON.package, filenames=names)
        self.write_distributions(self.files)
        (self.dist / names[0]).write_bytes(b"not a wheel")
        with self.assertRaisesRegex(ValueError, "not a readable"):
            verify_distributions(self.dist, "v1.2.3", package=PYTHON.package, filenames=names)

    def test_distribution_paths_match_portable_extraction_in_each_format(self):
        cases: list[tuple[tuple[str, bytes | None], ...]] = [
            ((f"example_app/{name}", b"reserved"),) for name in ("COM¹", "COM².txt", "LPT³", "delete\x7f", "back\\slash")
        ]
        for first, second in (
            (("example_app/café.py", b"one"), ("example_app/cafe\u0301.py", b"two")),
            (("example_app/A/x.py", b"one"), ("example_app/a/y.py", b"two")),
            (("example_app/café/x.py", b"one"), ("example_app/cafe\u0301/y.py", b"two")),
            (("example_app/data", b"file"), ("example_app/data/x.py", b"child")),
            (("example_app/data", b"file"), ("example_app/data", None)),
            (("example_app/A", None), ("example_app/a/x.py", b"child")),
        ):
            cases.extend(((first, second), (second, first)))
        for members in cases:
            for name, payload in python_distributions(members=members).items():
                with self.subTest(members=members, distribution=name):
                    # Corrupt one artifact at a time so every wheel and the sdist
                    # reach validation, rather than failing on the first wheel.
                    self.write_distributions({**self.files, name: payload})
                    with self.assertRaisesRegex(ValueError, "archive (?:path|file)"):
                        verify_distributions(self.dist, "v1.2.3", package=PYTHON.package, filenames=tuple(self.files))
                    destination = self.root / "unpacked"
                    with self.assertRaisesRegex(ValueError, "archive (?:path|file)"):
                        extract_archive(self.dist / name, destination)
                    self.assertFalse(destination.exists())
                    self.assertEqual((self.dist / name).read_bytes(), payload)

    def test_valid_distribution_directories_work_before_and_after_their_children(self):
        members = (("example_app/café", None), ("example_app/café/empty.txt", b""), ("example_app/café/code.py", "# café\r\n".encode("utf-8")))
        for entries in (members, tuple(reversed(members))):
            with self.subTest(entries=entries):
                files = python_distributions(members=entries)
                self.write_distributions(files)
                expected = {name: hashlib.sha256(payload).hexdigest() for name, payload in files.items()}
                self.assertEqual(verify_distributions(self.dist, "v1.2.3", package=PYTHON.package, filenames=tuple(files)), expected)
                for name in files:
                    with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                        destination = Path(temporary) / "unpacked"
                        extract_archive(self.dist / name, destination)
                        prefix = destination if name.endswith(".whl") else destination / "example_app-1.2.3"
                        self.assertEqual((prefix / "example_app/café/code.py").read_bytes(), members[2][1])
                        self.assertEqual((prefix / "example_app/café/empty.txt").read_bytes(), b"")

    def test_install_free_verifier_uses_shared_portability_checks(self):
        invalid = self.root / "invalid"
        invalid.mkdir()
        for name, payload in python_distributions(members=(("example_app/COM¹", b"reserved"),)).items():
            (invalid / name).write_bytes(payload)
        code = (
            "import json, runpy, sys\nfrom pathlib import Path\n"
            "verify = runpy.run_path(sys.argv[1])['verify_distributions']\n"
            "def check(directory):\n"
            " return verify(Path(directory), 'v1.2.3', package='example-app', filenames=tuple(json.loads(sys.argv[4])))\n"
            "hashes = check(sys.argv[2])\n"
            "try:\n check(sys.argv[3])\n"
            "except ValueError as error:\n print(json.dumps({'hashes': hashes, 'error': str(error)}))\n"
            "else:\n raise AssertionError('accepted a Windows device path')\n"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-X",
                "utf8",
                "-c",
                code,
                release_validation.__file__,
                str(self.dist),
                str(invalid),
                json.dumps(tuple(self.files)),
            ],
            cwd=self.root,
            capture_output=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["hashes"], self.hashes)
        self.assertIn("nonportable archive path", document["error"])

    def registry(self):
        return {
            "info": {"name": "example-app", "version": "1.2.3"},
            "urls": [
                {
                    "filename": name,
                    "digests": {"sha256": self.hashes[name]},
                    "size": len(payload),
                    "yanked": False,
                    "packagetype": "bdist_wheel" if name.endswith(".whl") else "sdist",
                    "url": f"https://files.example.org/{name}",
                }
                for name, payload in self.files.items()
            ],
        }

    def registry_transport(self, document, downloads=None):
        class Response(io.BytesIO):
            def __init__(self, payload, url):
                super().__init__(payload)
                self.status = 200
                self.url = url

        def open_url(request, **kwargs):
            url = request.full_url if hasattr(request, "full_url") else request
            if url == "https://test.pypi.org/pypi/example-app/1.2.3/json":
                payload = encode(document)
            else:
                self.assertTrue(url.startswith("https://files.example.org/"), url)
                payload = (self.files if downloads is None else downloads)[url.rsplit("/", 1)[1]]
            return Response(payload, url)

        return patch("research_repo_tools.registry._OPENER.open", open_url)

    def test_published_bytes_are_distinct_from_version_presence(self):
        with self.registry_transport(self.registry()):
            self.assertTrue(lookup_version("pypi", PYTHON.package, "1.2.3", index_url="https://test.pypi.org").present)
            self.assertEqual(verify_published_distributions(PYTHON.package, "1.2.3", self.hashes, index_url="https://test.pypi.org"), self.hashes)
        for damage in ("missing", "duplicate", "extra", "yanked", "metadata", "digest", "bytes", "size", "http"):
            with self.subTest(damage=damage):
                document = self.registry()
                downloads = dict(self.files)
                if damage == "missing":
                    document["urls"].pop()
                elif damage == "duplicate":
                    document["urls"][1] = document["urls"][0]
                elif damage == "extra":
                    document["urls"].append(document["urls"][0])
                elif damage == "yanked":
                    document["urls"][0]["yanked"] = True
                elif damage == "metadata":
                    document["info"]["version"] = "1.2.4"
                elif damage == "digest":
                    document["urls"][0]["digests"]["sha256"] = "0" * 64
                elif damage == "size":
                    document["urls"][0]["size"] += 1
                elif damage == "http":
                    document["urls"][0]["url"] = "http://files.example.org/unsafe"
                else:
                    name = next(iter(downloads))
                    downloads[name] = b"x" * len(downloads[name])
                with self.registry_transport(document, downloads), self.assertRaises(ValueError):
                    verify_published_distributions(PYTHON.package, "1.2.3", self.hashes, index_url="https://test.pypi.org")

    def test_failed_registry_download_closes_its_error_response(self):
        body = io.BytesIO(b"registry temporarily unavailable")
        error = urllib.error.HTTPError("https://files.example.org/package.whl", 503, "unavailable", Message(), body)
        with (
            patch("research_repo_tools.registry._json", return_value=self.registry()),
            patch("research_repo_tools.registry._OPENER.open", side_effect=error),
            self.assertRaisesRegex(ValueError, "download failed") as caught,
        ):
            verify_published_distributions(PYTHON.package, "1.2.3", self.hashes, index_url="https://test.pypi.org")
        self.assertTrue(body.closed)
        self.assertIs(caught.exception.__cause__, error)

    def test_registry_redirects_never_request_the_target_or_read_its_body(self):
        class RedirectResponse(urllib.response.addinfourl):
            msg = "Redirect"

        for operation in ("pypi", "crates-io", "download"):
            for code in (301, 302, 303, 307, 308):
                for destination in ("http://127.0.0.1/private", "https://other.invalid/private", "/redirected"):
                    with self.subTest(operation=operation, code=code, destination=destination):
                        requests = []
                        body = io.BytesIO(b"redirect body must not be consumed")
                        headers = Message()
                        headers["Location"] = destination

                        def transport(handler, request):
                            requests.append(request.full_url)
                            self.assertEqual(len(requests), 1, "redirect destination was requested")
                            return RedirectResponse(body, headers, request.full_url, code)

                        with (
                            patch.object(urllib.request.HTTPSHandler, "https_open", transport),
                            patch.object(urllib.request.HTTPHandler, "http_open", transport),
                            patch.object(body, "read", wraps=body.read) as read,
                        ):
                            with self.assertRaisesRegex(ValueError, "download failed|registry lookup failed with HTTP"):
                                if operation == "download":
                                    with patch("research_repo_tools.registry._json", return_value=self.registry()):
                                        verify_published_distributions(PYTHON.package, "1.2.3", self.hashes)
                                else:
                                    lookup_version(operation, "example-app", "1.2.3")
                            self.assertEqual(len(requests), 1)
                            read.assert_not_called()
                            self.assertTrue(body.closed)

    def test_installed_cli_create_verify_approval_and_published_in_mixed_workspace(self):
        checkout = self.root / "source café"
        checkout.mkdir()
        (checkout / "pyproject.toml").write_bytes(b'[project]\nname="example-app"\nversion="1.2.3"\n')
        (checkout / "Cargo.toml").write_bytes(b'[workspace]\nmembers=[]\n[workspace.package]\nversion="9.8.7"\n')
        interface_policy(self.root, PYTHON)
        self.assertEqual(load_evidence_policy(self.root / "evidence.toml"), PYTHON)
        provider = self.provider()
        common = ["evidence.toml", "--checkout", str(checkout), "--version", "1.2.3", "--commit", COMMIT, "--distributions", str(self.dist)]
        environment = {
            "GITHUB_REPOSITORY": "example/package",
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": COMMIT,
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "2",
        }
        with (
            patch("research_repo_tools.release_interfaces.api", side_effect=lambda endpoint, **kwargs: provider.read(endpoint)),
            patch("research_repo_tools.release_interfaces.require_release_checkout") as observed,
            patch("research_repo_tools.release_validation.subprocess.run", provider.execute),
            patch.dict(os.environ, environment),
            contextlib.redirect_stdout(io.StringIO()),
        ):

            def invoke(operation, output, *extra):
                return cli.main(["--root", str(self.root), "release", "evidence", operation, *common, "--output", output, *extra])

            self.assertEqual(invoke("create", "manifest.json", "--run-id", "123", "--attempt", "2"), 0)
            self.assertEqual((self.root / "manifest.json").read_bytes(), provider.files["validation.json"])
            self.assertEqual(invoke("verify", "selection.json"), 0)
            self.assertEqual(invoke("verify", "approved.json", "--expected", "selection.json"), 0)
            self.assertEqual((self.root / "approved.json").read_bytes(), (self.root / "selection.json").read_bytes())
            with self.registry_transport(self.registry()):
                self.assertEqual(invoke("published", "published.json", "--expected", "approved.json"), 0)
            observed.assert_called_with(checkout.resolve(), COMMIT)
            provider.run["run_attempt"] = 3
            with contextlib.redirect_stderr(io.StringIO()) as diagnostics:
                self.assertEqual(invoke("verify", "rejected.json", "--expected", "selection.json"), 1)
            self.assertIn("attempt changed", diagnostics.getvalue())
            self.assertFalse((self.root / "rejected.json").exists())


class TestReleaseCheckoutConsumer(unittest.TestCase):
    @override
    def setUp(self):
        if os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1":
            self.skipTest("Git mutations disabled by local policy")
        temporary = tempfile.TemporaryDirectory(prefix="release checkout café ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.enterContext(patch.dict(os.environ, env, clear=True))
        self.git("init", "--template=")
        self.path = self.root / "tracked café.txt"
        self.path.write_bytes(b"original\n")
        self.git("add", "--", self.path.name)
        self.git("commit", "-m", "fixture")
        self.commit = self.git("rev-parse", "HEAD").stdout.decode("ascii").strip()

    def git(self, *args, cwd=None):
        return run_git_bytes(
            [
                "--no-pager",
                "--no-optional-locks",
                "-c",
                "core.autocrlf=false",
                "-c",
                f"core.hooksPath={self.root / 'disabled-hooks'}",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=Release fixture",
                "-c",
                "user.email=release@example.invalid",
                *args,
            ],
            cwd=self.root if cwd is None else cwd,
            timeout=30,
        )

    def observe(self, commit=None):
        before = (self.root / ".git/index").read_bytes()
        try:
            require_release_checkout(self.root, self.commit if commit is None else commit)
        finally:
            self.assertEqual((self.root / ".git/index").read_bytes(), before)

    def test_real_checkout_accepts_clean_and_rejects_dirty_and_changed_head(self):
        self.observe()
        self.path.write_bytes(b"changed content\n")
        with self.assertRaisesRegex(ValueError, "observed release checkout"):
            self.observe()
        self.path.write_bytes(b"original\n")
        untracked = self.root / "untracked.txt"
        untracked.write_bytes(b"new\n")
        with self.assertRaisesRegex(ValueError, "observed release checkout"):
            self.observe()
        untracked.unlink()
        self.git("commit", "--allow-empty", "-m", "next commit")
        with self.assertRaisesRegex(ValueError, "observed release checkout"):
            self.observe()
        self.observe(self.git("rev-parse", "HEAD").stdout.decode("ascii").strip())

    def test_checkout_and_submodule_cannot_supply_git_through_path(self):
        import runpy

        child = self.submodule()
        (self.root / "pyproject.toml").write_bytes(b'[project]\nname="research-repo-tools"\nversion="0.2.5"\n')
        name = "git.cmd" if os.name == "nt" else "git"
        for commands in (self.root, self.root / "bin", child, child / "bin"):
            commands.mkdir(exist_ok=True)
            executable = commands / name
            executable.write_bytes(
                b"@echo off\r\necho injected>git-hijacked\r\nexit /b 99\r\n" if os.name == "nt" else b"#!/bin/sh\n: > git-hijacked\nexit 99\n"
            )
            executable.chmod(0o700)
        self.git("add", "bin", name, cwd=child)
        self.git("commit", "-m", "untrusted submodule executable", cwd=child)
        child_commit = self.git("rev-parse", "HEAD", cwd=child).stdout.decode("ascii").strip()
        self.git("update-index", "--cacheinfo", f"160000,{child_commit},child module")
        self.git("add", "bin", name, "pyproject.toml")
        self.git("commit", "-m", "untrusted checkout executable")
        self.commit = self.git("rev-parse", "HEAD").stdout.decode("ascii").strip()
        standalone = runpy.run_path(release_validation.__file__)["_checkout_identity"]
        original_path = os.environ.get("PATH", os.defpath)
        prefixes = ["bin", "", str(self.root / "bin"), str(child / "bin")]
        for basename in (self.root.name.swapcase(), unicodedata.normalize("NFD", self.root.name)):
            alias = self.root.with_name(basename) / "bin"
            if str(alias) != str(self.root / "bin") and alias.is_dir():
                self.assertTrue(alias.samefile(self.root / "bin"))
                prefixes.append(str(alias))
        for prefix in prefixes:
            for directory in (self.root, child):
                (directory / "git-hijacked").unlink(missing_ok=True)
            self.assertEqual(self.git("status", "--porcelain", "--ignore-submodules=none").stdout, b"")
            with self.subTest(prefix=prefix), patch.dict(os.environ, {"PATH": prefix + os.pathsep + original_path}):
                self.observe()
                self.assertEqual(standalone(self.root), ("v0.2.5", self.commit))
                self.assertFalse((self.root / "git-hijacked").exists())
                self.assertFalse((child / "git-hijacked").exists())

    def test_git_environment_cannot_redirect_checkout_observation(self):
        import runpy

        project = b'[project]\nname="research-repo-tools"\nversion="0.2.5"\n'
        (self.root / "pyproject.toml").write_bytes(project)
        self.git("add", "pyproject.toml")
        self.git("commit", "-m", "Python package fixture")
        self.commit = self.git("rev-parse", "HEAD").stdout.decode("ascii").strip()
        standalone = runpy.run_path(release_validation.__file__)["_checkout_identity"]
        with tempfile.TemporaryDirectory(prefix="alternate checkout café ") as temporary:
            alternate = Path(temporary).resolve()
            self.git("init", "--template=", cwd=alternate)
            (alternate / "pyproject.toml").write_bytes(project)
            self.git("add", "pyproject.toml", cwd=alternate)
            self.git("commit", "-m", "alternate repository", cwd=alternate)
            alternate_commit = self.git("rev-parse", "HEAD", cwd=alternate).stdout.decode("ascii").strip()
            self.assertNotEqual(alternate_commit, self.commit)
            self.assertEqual(self.git("status", "--porcelain", cwd=alternate).stdout, b"")
            overrides = {"GIT_DIR": str(alternate / ".git"), "GIT_WORK_TREE": str(alternate), "GIT_INDEX_FILE": str(alternate / ".git/index")}
            with patch.dict(os.environ, overrides):
                # Prove the inherited environment would select the other repository.
                self.assertEqual(self.git("rev-parse", "HEAD").stdout.decode("ascii").strip(), alternate_commit)
                self.observe()
                self.assertEqual(standalone(self.root), ("v0.2.5", self.commit))
                self.path.write_bytes(b"dirty source\n")
                for commit in (self.commit, alternate_commit):
                    with self.subTest(commit=commit), self.assertRaisesRegex(ValueError, "observed release checkout"):
                        self.observe(commit)
                with self.assertRaisesRegex(ValueError, "requires a clean checkout"):
                    standalone(self.root)
                self.assertEqual(self.path.read_bytes(), b"dirty source\n")

    def hidden_change(self, flag, tag):
        self.git("update-index", flag, "--", self.path.name)
        self.assertTrue(self.git("ls-files", "-v", "-z").stdout.startswith(tag + b" "))
        self.path.write_bytes(b"hidden change\n")
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all").stdout, b"")
        with self.assertRaisesRegex(ValueError, "observed release checkout.*index"):
            self.observe()
        self.assertEqual(self.path.read_bytes(), b"hidden change\n")

    def test_assume_unchanged_cannot_hide_modified_source(self):
        self.hidden_change("--assume-unchanged", b"h")

    def test_skip_worktree_cannot_hide_modified_source(self):
        self.hidden_change("--skip-worktree", b"S")

    def submodule(self):
        child = self.root / "child module"
        child.mkdir()
        self.git("init", "--template=", cwd=child)
        (child / "source.txt").write_bytes(b"submodule source\n")
        self.git("add", "source.txt", cwd=child)
        self.git("commit", "-m", "child fixture", cwd=child)
        (self.root / ".gitmodules").write_bytes(b'[submodule "child"]\npath = child module\nurl = ../child\nignore = all\n')
        self.git("add", ".gitmodules", "child module")
        self.git("commit", "-m", "submodule fixture")
        self.commit = self.git("rev-parse", "HEAD").stdout.decode("ascii").strip()
        self.observe()
        return child

    def test_submodule_ignore_configuration_cannot_hide_modified_source(self):
        child = self.submodule()
        (child / "source.txt").write_bytes(b"modified submodule\n")
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all").stdout, b"")
        with self.assertRaisesRegex(ValueError, "observed release checkout"):
            self.observe()
        self.assertEqual((child / "source.txt").read_bytes(), b"modified submodule\n")

    def test_inactive_submodule_index_flags_cannot_hide_modified_source(self):
        child = self.submodule()
        self.git("config", "submodule.child.active", "false")
        self.git("update-index", "--assume-unchanged", "source.txt", cwd=child)
        (child / "source.txt").write_bytes(b"hidden submodule\n")
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none").stdout, b"")
        before = (child / ".git/index").read_bytes()
        with self.assertRaisesRegex(ValueError, "observed release checkout.*index"):
            self.observe()
        self.assertEqual((child / ".git/index").read_bytes(), before)
        self.assertEqual((child / "source.txt").read_bytes(), b"hidden submodule\n")


if __name__ == "__main__":
    unittest.main()
