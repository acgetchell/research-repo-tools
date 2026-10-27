"""Installed evidence contracts using inert provider data and real archive bytes."""

import contextlib
import copy
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
import zlib
from dataclasses import replace
from pathlib import Path
from typing import override
from unittest.mock import patch

from research_repo_tools import cli
from research_repo_tools.changelog import template
from research_repo_tools.process import run_git_bytes
from research_repo_tools.release_assets import require_release_checkout
from research_repo_tools.release_evidence import EvidencePolicy, create_evidence_manifest, verify_cargo_archive, verify_release_evidence
from research_repo_tools.release_interfaces import load_evidence_policy, write_release_outputs
from research_repo_tools.release_validation import ValidationPolicy, parse_release_json, read_pages, redact_release_diagnostic, verify_attestation

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
        elif endpoint.startswith("actions/runs/123/attempts/2/jobs?"):
            result = {"jobs": self.jobs, "total_count": len(self.jobs)}
        elif endpoint.startswith("actions/runs/123/artifacts?"):
            artifacts = [self.artifact, {**self.artifact, "id": 43}] if self.duplicates else [self.artifact]
            result = {"artifacts": artifacts, "total_count": len(artifacts)}
        elif endpoint == "actions/artifacts/42":
            result = self.artifact
        else:
            raise AssertionError(f"unexpected provider request: {endpoint}")
        return copy.deepcopy(result)

    def execute(self, args, **kwargs):
        self.commands.append(args)
        if args == ["gh", "api", "repos/example/package/actions/artifacts/42/zip"]:
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

        def execute(args, **kwargs):
            commands.append(args)
            if "ls-files" in args or "submodule" in args:
                return subprocess.CompletedProcess(args, 0, b"H tracked\0", b"")
            return subprocess.CompletedProcess(args, 0, "" if "status" in args else COMMIT, "")

        with patch("research_repo_tools.release_assets.run_git_command", execute), patch("research_repo_tools.release_assets.run_git_bytes", execute):
            require_release_checkout(Path("source"), COMMIT)
        self.assertEqual(
            commands,
            [
                ["--no-pager", "rev-parse", "HEAD"],
                ["--no-pager", "--no-optional-locks", "ls-files", "-v", "-z"],
                ["--no-pager", "--no-optional-locks", "submodule", "foreach", "--quiet", "--recursive", "git --no-pager --no-optional-locks ls-files -v -z"],
                [
                    "--no-pager",
                    "--no-optional-locks",
                    "-c",
                    "core.fsmonitor=false",
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                    "--ignore-submodules=none",
                ],
                ["--no-pager", "rev-parse", "HEAD"],
            ],
        )
        for outputs in ((COMMIT, "?? dirty", COMMIT), (COMMIT, "", "b" * 40)):
            responses = [subprocess.CompletedProcess([], 0, value, "") for value in outputs]
            with (
                patch("research_repo_tools.release_assets.run_git_command", side_effect=responses),
                patch("research_repo_tools.release_assets.run_git_bytes", return_value=subprocess.CompletedProcess([], 0, b"", b"")),
            ):
                with self.assertRaisesRegex(ValueError, "observed release checkout"):
                    require_release_checkout(Path("source"), COMMIT)


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
            self.assertEqual(cli.main(["--root", str(self.root), *self.args]), 1)

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
