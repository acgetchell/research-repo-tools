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

from research_repo_tools.release_validation import ValidationPolicy, download_artifact, select_main_run, validate_jobs, validate_run

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

    def test_nonportable_names_fail_before_transport(self):
        for names in (("../escape",), ("CON.txt",), ("name.",), ("Case", "case"), ("a/b",), ("x:y",)):
            with self.subTest(names=names), patch("research_repo_tools.release_validation.subprocess.run", side_effect=AssertionError("unexpected transport")):
                with self.assertRaisesRegex(ValueError, "portable plain filenames"):
                    download_artifact(POLICY.repository, {"id": 42}, Path("unused"), names)


if __name__ == "__main__":
    unittest.main()
