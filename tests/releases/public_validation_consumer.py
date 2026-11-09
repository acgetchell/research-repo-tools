"""Installed generic release-validation API, independent of registry/upload policy."""

import copy
import hashlib
import io
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from research_repo_tools import files
from research_repo_tools.release_validation import ValidationPolicy, api, download_artifact, select_main_run, validate_jobs, validate_run

COMMIT = "a" * 40
POLICY = ValidationPolicy("example/package", ".github/workflows/package.yml", "Package", (("build", ("Build",)), ("native", ("Install wheel and sdist",))))
WORKFLOW = {"id": 13, "name": "Package", "path": POLICY.workflow, "state": "active"}


def run():
    return {
        "id": 123,
        "run_attempt": 2,
        "name": "Package",
        "path": POLICY.workflow,
        "workflow_id": 13,
        "head_sha": COMMIT,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "repository": {"id": 7, "full_name": POLICY.repository},
        "head_repository": {"id": 7, "full_name": POLICY.repository},
    }


class TestValidationConsumer(unittest.TestCase):
    def test_repository_api_root_and_nested_endpoints(self):
        for repository in ("acgetchell/research-repo-tools", "example/package"):
            for endpoint, expected_path in (
                ("", f"repos/{repository}"),
                ("branches/main", f"repos/{repository}/branches/main"),
                ("actions/runs?per_page=100&page=2", f"repos/{repository}/actions/runs?per_page=100&page=2"),
            ):
                with self.subTest(repository=repository, endpoint=endpoint):
                    with patch("research_repo_tools.release_validation.subprocess.run") as execute:
                        execute.return_value = subprocess.CompletedProcess([], 0, stdout='{"id": 7}')
                        result = api(endpoint) if repository == "acgetchell/research-repo-tools" else api(endpoint, repository=repository)
                        self.assertEqual(result, {"id": 7})
                        execute.assert_called_once_with(["gh", "api", expected_path], check=True, capture_output=True, text=True, encoding="utf-8", timeout=120)

    def test_selection_never_uses_dispatch_pr_or_another_commit(self):
        for field, value in (
            ("event", "workflow_dispatch"),
            ("event", "pull_request"),
            ("head_sha", "b" * 40),
            ("head_branch", "other"),
            ("path", ".github/workflows/unrelated.yml"),
        ):
            with self.subTest(field=field, value=value):
                candidate = {**run(), field: value}

                def read(endpoint):
                    return WORKFLOW if endpoint == "actions/workflows/package.yml" else {"workflow_runs": [candidate]}

                with self.assertRaises(ValueError):
                    select_main_run(POLICY, COMMIT, read, attempts=1, interval=0)

    def test_generic_policy_accepts_one_complete_main_attempt(self):
        selected = run()
        validate_run(POLICY, selected, COMMIT, WORKFLOW)
        jobs = [
            {
                "id": index,
                "run_id": 123,
                "run_attempt": 2,
                "head_sha": COMMIT,
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "name": name,
                "steps": [{"name": step, "status": "completed", "conclusion": "success"} for step in steps],
            }
            for index, (name, steps) in enumerate(POLICY.required_steps, 10)
        ]
        self.assertEqual(validate_jobs(POLICY, selected, jobs), {"build": 10, "native": 11})
        partial = copy.deepcopy(jobs)
        partial[0]["run_attempt"] = 1
        with self.assertRaisesRegex(ValueError, "another run, attempt"):
            validate_jobs(POLICY, selected, partial)
        with self.assertRaisesRegex(ValueError, "Rerun ALL"):
            validate_jobs(POLICY, selected, jobs[1:])

    def test_exact_binary_zip_transport_and_immutable_identity(self):
        contents = {"wheel.whl": b"binary\0\xff\r\nmixed\n", "source.tar.gz": b"source\r\n"}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, payload in contents.items():
                archive.writestr(name, payload)
        payload = stream.getvalue()
        artifact = {"id": 42, "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}", "size_in_bytes": len(payload)}

        def execute(args, **kwargs):
            self.assertEqual(args, ["gh", "api", "repos/example/package/actions/artifacts/42/zip"])
            self.assertEqual(kwargs["timeout"], 120)
            kwargs["stdout"].write(payload)
            return subprocess.CompletedProcess(args, 0)

        with (
            tempfile.TemporaryDirectory(prefix="validation café-") as temporary,
            patch("research_repo_tools.release_validation.subprocess.run", side_effect=execute),
        ):
            destination = Path(temporary) / "dist"
            download_artifact(POLICY.repository, artifact, destination, tuple(contents))
            self.assertEqual({path.name: path.read_bytes() for path in destination.iterdir()}, contents)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                download_artifact(POLICY.repository, {**artifact, "digest": f"sha256:{'0' * 64}"}, Path(temporary) / "bad", tuple(contents))
            self.assertFalse((Path(temporary) / "bad").exists())
            before = {path.name: path.read_bytes() for path in destination.iterdir()}
            with self.assertRaises(FileExistsError):
                download_artifact(POLICY.repository, artifact, destination, tuple(contents))
            self.assertEqual({path.name: path.read_bytes() for path in destination.iterdir()}, before)

            failed = (Path(temporary) / "failed").resolve()
            replace = files.os.replace

            def fail_second(source, target):
                if Path(target) == failed / "source.tar.gz":
                    raise OSError("injected artifact publication failure")
                return replace(source, target)

            with patch.object(files.os, "replace", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "injected artifact publication"):
                    download_artifact(POLICY.repository, artifact, failed, tuple(contents))
            self.assertFalse(failed.exists())

    def test_nonportable_names_fail_before_transport(self):
        for names in (("../escape",), ("CON.txt",), ("name.",), ("Case", "case"), ("a/b",), ("x:y",)):
            with self.subTest(names=names), patch("research_repo_tools.release_validation.subprocess.run", side_effect=AssertionError("unexpected transport")):
                with self.assertRaisesRegex(ValueError, "portable plain filenames"):
                    download_artifact(POLICY.repository, {"id": 42}, Path("unused"), names)

    def test_immutable_download_retries_transient_http_errors_without_partial_bytes(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("wheel.whl", b"exact\r\n\xff\x00bytes")
        payload = stream.getvalue()
        artifact = {"id": 42, "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}", "size_in_bytes": len(payload)}
        attempts = 0

        def execute(args, **kwargs):
            nonlocal attempts
            attempts += 1
            self.assertEqual(args, ["gh", "api", "repos/example/package/actions/artifacts/42/zip"])
            self.assertEqual(kwargs["stdout"].tell(), 0)
            self.assertEqual(kwargs["stdout"].read(), b"")
            if attempts < 3:
                kwargs["stdout"].write(b"incomplete" * len(payload))
                raise subprocess.CalledProcessError(1, args, stderr=b"gh: HTTP 503\n")
            kwargs["stdout"].write(payload)
            return subprocess.CompletedProcess(args, 0)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("research_repo_tools.release_validation.subprocess.run", side_effect=execute),
            patch("research_repo_tools.release_validation.time.sleep") as sleep,
        ):
            destination = Path(directory) / "dist"
            download_artifact(POLICY.repository, artifact, destination, ("wheel.whl",))
            self.assertEqual((destination / "wheel.whl").read_bytes(), b"exact\r\n\xff\x00bytes")
            self.assertEqual(attempts, 3)
            self.assertEqual([call.args for call in sleep.call_args_list], [(1,), (2,)])

    def test_download_retry_exhaustion_and_permanent_errors_never_publish(self):
        artifact = {"id": 42, "digest": f"sha256:{'0' * 64}", "size_in_bytes": 100}
        for diagnostic, attempts in ((b"gh: HTTP 503\n", 3), ("gh: HTTP 502\n", 3), (b"gh: HTTP 404\n", 1), (b"gh: HTTP 401\n", 1)):
            with (
                self.subTest(diagnostic=diagnostic),
                tempfile.TemporaryDirectory() as directory,
                patch("research_repo_tools.release_validation.subprocess.run", side_effect=subprocess.CalledProcessError(1, "gh", stderr=diagnostic)) as run,
                patch("research_repo_tools.release_validation.time.sleep") as sleep,
            ):
                destination = Path(directory) / "dist"
                with self.assertRaises(subprocess.CalledProcessError):
                    download_artifact(POLICY.repository, artifact, destination, ("wheel.whl",))
                self.assertEqual(run.call_count, attempts)
                self.assertEqual(sleep.call_count, attempts - 1)
                self.assertFalse(destination.exists())

    def test_original_zip_member_names_cannot_hide_nul_suffixes(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("wanted.jsonXhidden", b"{}")
        serialized = stream.getvalue()
        self.assertEqual(serialized.count(b"wanted.jsonXhidden"), 2)
        payload = serialized.replace(b"wanted.jsonXhidden", b"wanted.json\0hidden")
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual([entry.orig_filename for entry in archive.infolist()], ["wanted.json\0hidden"])
            self.assertEqual(archive.namelist(), ["wanted.json"])
        artifact = {"id": 42, "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}", "size_in_bytes": len(payload)}

        def execute(args, **kwargs):
            kwargs["stdout"].write(payload)
            return subprocess.CompletedProcess(args, 0)

        with tempfile.TemporaryDirectory() as directory, patch("research_repo_tools.release_validation.subprocess.run", side_effect=execute):
            destination = Path(directory) / "artifact"
            with self.assertRaisesRegex(ValueError, "exactly"):
                download_artifact(POLICY.repository, artifact, destination, ("wanted.json",))
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
