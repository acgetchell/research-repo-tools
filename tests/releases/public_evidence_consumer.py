"""Installed evidence contracts using inert provider data and real archive bytes."""

import copy
import gzip
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
import zlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from research_repo_tools.release_assets import require_release_checkout
from research_repo_tools.release_evidence import EvidencePolicy, create_evidence_manifest, verify_cargo_archive, verify_release_evidence
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
                archive.writestr(name, payload)
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
            return subprocess.CompletedProcess(args, 0, "" if "status" in args else COMMIT, "")

        with patch("research_repo_tools.release_assets.run_git_command", execute):
            require_release_checkout(Path("source"), COMMIT)
        self.assertEqual(
            commands,
            [["--no-pager", "rev-parse", "HEAD"], ["--no-pager", "status", "--porcelain", "--untracked-files=all"], ["--no-pager", "rev-parse", "HEAD"]],
        )
        for outputs in ((COMMIT, "?? dirty", COMMIT), (COMMIT, "", "b" * 40)):
            responses = [subprocess.CompletedProcess([], 0, value, "") for value in outputs]
            with patch("research_repo_tools.release_assets.run_git_command", side_effect=responses):
                with self.assertRaisesRegex(ValueError, "observed release checkout"):
                    require_release_checkout(Path("source"), COMMIT)


if __name__ == "__main__":
    unittest.main()
