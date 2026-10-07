"""Public Actions CLI contracts, also run from isolated distributions."""

import base64
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from research_repo_tools import action_updates, cli, files

OLD = "1" * 40
NEW = "2" * 40


class TestActionsConsumer(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.policy = self.root / "selected.json"
        self.policy.write_bytes(json.dumps({"github_owned_allowed": False, "verified_allowed": False, "patterns_allowed": ["approved/action@*"]}).encode())
        self.workflow = self.root / "ci.yml"
        self.workflow.write_bytes(
            f"name: example\r\non: push\r\npermissions: {{}}\r\njobs:\r\n  test:\r\n    steps:\r\n      - uses: 'approved/action@{OLD}' # v1.0.0 keep\r\n".encode()
        )
        self.update_policy = self.root / "updates.toml"
        self.update_policy.write_bytes(b'[actions]\n"approved/action" = "latest"\n')

    def invoke(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(["--root", str(self.root), "actions", *arguments])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_allowlist_checks_job_calls_and_sequence_steps(self):
        original = self.workflow.read_bytes()
        self.assertEqual(self.invoke("allowlist", "--policy", str(self.policy), str(self.workflow))[0], 0)
        self.workflow.write_bytes(original + f"  call:\r\n    uses: other/repo/.github/workflows/test.yml@{OLD}\r\n".encode())
        before = self.workflow.read_bytes()
        status, _, error = self.invoke("allowlist", "--policy", str(self.policy), str(self.workflow))
        self.assertEqual(status, 1)
        self.assertIn("ci.yml:9", error)
        self.assertIn("other/repo/.github/workflows/test.yml@*", error)
        self.assertEqual(self.workflow.read_bytes(), before)

    def test_preview_check_apply_and_idempotence_keep_other_bytes(self):
        def run(command, arguments, **_kwargs):
            self.assertEqual(command, "gh")
            self.assertEqual(arguments[0], "api")
            endpoint = arguments[1]
            if endpoint.endswith("/releases/latest"):
                value = {"draft": False, "prerelease": False, "tag_name": "v2.0.0"}
            else:
                self.assertTrue(endpoint.endswith("/git/ref/tags/v2.0.0"), endpoint)
                value = {"ref": "refs/tags/v2.0.0", "object": {"type": "commit", "sha": NEW}}
            return subprocess.CompletedProcess([command, *arguments], 0, json.dumps(value), "")

        before = self.workflow.read_bytes()
        args = ("update", "--policy", str(self.update_policy), str(self.workflow))
        with patch.object(action_updates, "run_safe_command", side_effect=run):
            self.assertEqual(self.invoke(*args, "--dry-run")[0], 0)
            self.assertEqual(self.invoke(*args, "--check")[0], 1)
            self.assertEqual(self.workflow.read_bytes(), before)
            status, report, error = self.invoke(*args)
            self.assertEqual(status, 0, error)
            self.assertIn(f"{OLD} (# v1.0.0) -> {NEW} (# v2.0.0)", report)
            expected = before.replace(OLD.encode(), NEW.encode()).replace(b"# v1.0.0", b"# v2.0.0")
            self.assertEqual(self.workflow.read_bytes(), expected)
            self.assertEqual(self.invoke(*args, "--check")[0], 0)
            self.assertEqual(self.workflow.read_bytes(), expected)

    def test_retained_wrapper_uses_upstream_support_and_authoritative_tool_pin(self):
        wrapper = "example/wrapper"
        self.workflow.write_bytes(f"jobs:\n  check:\n    steps:\n      - uses: {wrapper}@{OLD} # v1\n".encode())
        (self.root / "pyproject.toml").write_bytes(b'[dependency-groups]\ndev=["scanner==1.2.3"]\n')
        self.update_policy.write_bytes(b'[actions]\n[compatibility."example/wrapper"]\npath="support/versions"\ntool="scanner"\n')
        args = ("update", "--policy", str(self.update_policy), str(self.workflow), "--check")
        for content, status in [(b"1.2.2 sha\n", 1), (b"1.2.3 sha\n", 0)]:
            data = {"encoding": "base64", "content": base64.b64encode(content).decode()}
            with patch.object(action_updates, "run_safe_command", return_value=subprocess.CompletedProcess([], 0, json.dumps(data), "")):
                result, _, error = self.invoke(*args)
            self.assertEqual(result, status, error)
            if status:
                self.assertIn("does not support tool version 1.2.3", error)

    def test_failed_lookup_preserves_workflow_and_has_no_completion_report(self):
        before = self.workflow.read_bytes()
        with patch.object(action_updates, "run_safe_command", return_value=subprocess.CompletedProcess([], 0, "invalid JSON", "")):
            status, output, error = self.invoke("update", "--policy", str(self.update_policy), str(self.workflow))
        self.assertEqual(status, 1)
        self.assertIn("GitHub", error)
        self.assertNotIn("Applied", output)
        self.assertEqual(self.workflow.read_bytes(), before)

    def test_decoder_limits_report_policy_path_and_preserve_workflow(self):
        before = self.workflow.read_bytes()
        for payload in (b"[" * 2000 + b"0" + b"]" * 2000, b'{"number":' + b"1" * 10000 + b"}"):
            with self.subTest(payload_length=len(payload)):
                self.policy.write_bytes(payload)
                status, output, error = self.invoke("allowlist", "--policy", str(self.policy), str(self.workflow))
                self.assertEqual(status, 1)
                self.assertIn("selected.json", error)
                self.assertNotIn("Traceback", error)
                self.assertEqual(output, "")
                self.assertEqual(self.workflow.read_bytes(), before)

    def test_staging_edit_to_unchanged_input_prevents_all_publication(self):
        before = self.workflow.read_bytes()
        other = self.root / "unchanged.yml"
        original_other = f"jobs:\n  call:\n    uses: other/action@{OLD} # v1\n".encode()
        other.write_bytes(original_other)
        edited = original_other + b"# editor save\r\n"
        names = sorted(path.name for path in self.root.iterdir())
        stage = files._stage_bytes

        def edit_during_staging(target, payload):
            staged = stage(target, payload)
            other.write_bytes(edited)
            return staged

        with (
            patch.object(action_updates, "resolve", return_value=(NEW, "v2.0.0")),
            patch.object(files, "_stage_bytes", side_effect=edit_during_staging),
        ):
            status, output, error = self.invoke("update", "--policy", str(self.update_policy), str(self.workflow), str(other))
        self.assertEqual(status, 1)
        self.assertIn("file changed before publication", error)
        self.assertNotIn("Applied", output)
        self.assertNotIn("Update ", output)
        self.assertEqual(self.workflow.read_bytes(), before)
        self.assertEqual(other.read_bytes(), edited)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), names)


if __name__ == "__main__":
    unittest.main()
