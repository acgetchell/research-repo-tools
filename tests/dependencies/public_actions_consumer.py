"""Public Actions CLI contracts, also run from isolated distributions."""

import base64
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import override
from unittest.mock import patch

from research_repo_tools import action_updates, cli, files

OLD = "1" * 40
NEW = "2" * 40


class TestActionsConsumer(unittest.TestCase):
    @override
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.policy = self.root / "selected.json"
        self.policy.write_bytes(json.dumps({"github_owned_allowed": False, "verified_allowed": False, "patterns_allowed": ["approved/action@*"]}).encode())
        self.workflow = self.root / "ci.yml"
        self.workflow.write_bytes(
            (
                f"name: example\r\non: push\r\npermissions: {{}}\r\njobs:\r\n  test:\r\n    steps:\r\n      - uses: 'approved/action@{OLD}' # v1.0.0 keep\r\n"
                f"      - uses: '$/.github/actions/setup' # v1.0.0 {OLD}\r\n"
                f"  same_repository:\r\n    uses: $/.github/workflows/check.yml # v1.0.0 {OLD}\r\n"
            ).encode()
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
        self.assertIn("ci.yml:12", error)
        self.assertIn("no approved identity 'other/repo/.github/workflows/test.yml'", error)
        self.assertEqual(self.workflow.read_bytes(), before)

    def test_publishing_policy_retains_exact_authentication_commit(self):
        auth = "rust-lang/crates-io-auth-action"
        sha = "c6f97d42243bad5fab37ca0427f495c86d5b1a18"
        self.policy.write_bytes(
            json.dumps({"github_owned_allowed": False, "verified_allowed": False, "patterns_allowed": ["actions/checkout@*", f"{auth}@{sha}"]}).encode()
        )
        original = (
            "name: publish\non: workflow_dispatch\npermissions: {}\njobs:\n  publish:\n"
            "    runs-on: ubuntu-latest\n    permissions:\n      contents: read\n      id-token: write\n"
            f"    steps:\n      - uses: actions/checkout@{OLD}\n      - uses: {auth}@{sha}\n"
        ).encode()
        self.workflow.write_bytes(original)
        args = ("allowlist", "--policy", str(self.policy), str(self.workflow))
        status, _, error = self.invoke(*args)
        self.assertEqual(status, 0, error)
        changed = original.replace(f"{auth}@{sha}".encode(), f"{auth}@{NEW}".encode())
        self.workflow.write_bytes(changed)
        status, output, error = self.invoke(*args)
        self.assertEqual(status, 1)
        self.assertEqual(output, "")
        self.assertIn(f"permitted references: {auth}@{sha}", error)
        self.assertNotIn("@*", error)
        self.assertEqual(self.workflow.read_bytes(), changed)

    def test_exact_constraints_on_steps_job_calls_and_composites(self):
        sha = "abcdef01" * 5
        action = "approved/action/Nested"
        self.policy.write_bytes(
            json.dumps(
                {"github_owned_allowed": False, "verified_allowed": False, "patterns_allowed": [f"APPROVED/Action/Nested@{sha.upper()}", "approved/action@*"]}
            ).encode()
        )
        for prefix, context in [
            ("jobs:\n  call:\n    uses:", "jobs.call.uses"),
            ("jobs:\n  check:\n    steps:\n      - uses:", "jobs.check.steps[0].uses"),
            ("runs:\n  using: composite\n  steps:\n    - uses:", "runs.steps[0].uses"),
        ]:
            for ref in (sha, sha.upper(), NEW, sha[:7], "v1", "main", "bad ref", f"{sha}@other"):
                with self.subTest(context=context, ref=ref):
                    before = f"{prefix} '{action}@{ref}'\n".encode()
                    self.workflow.write_bytes(before)
                    status, _, error = self.invoke("allowlist", "--policy", str(self.policy), str(self.workflow))
                    expected = 0 if ref.lower() == sha else 1
                    self.assertEqual(status, expected, error)
                    if expected:
                        self.assertIn("ci.yml:", error)
                        self.assertIn(f"{action}@{ref}", error)
                        self.assertNotIn("@*", error)
                    self.assertEqual(self.workflow.read_bytes(), before)

    def test_composite_allowlist_handles_styles_aliases_and_exact_nested_identity(self):
        composite = self.root / "action.yml"
        original = (
            "name: setup\ndescription: nested setup action\nruns:\n  using: composite\n  steps:\n"
            f"    - uses: &approved 'approved/action@{OLD}'\n    - uses: *approved\n"
            f'    - name: quoted\n      uses: "approved/action@{OLD}"\n'
            f"    - uses: >-\n        approved/action@{OLD}\n"
            "    - uses: ./local\n    - uses: '$/.github/actions/setup'\n    - uses: docker://image:latest\n"
            "    - run: echo 'uses are data here'\n      shell: bash\n"
            f"    - uses: approved/action/nested@{OLD}\n"
        ).encode()
        composite.write_bytes(original)
        args = ("allowlist", "--policy", str(self.policy), str(self.workflow), str(composite))
        status, _, error = self.invoke(*args)
        self.assertEqual(status, 1)
        self.assertIn("action.yml:17", error)
        self.assertIn("runs.steps[8].uses", error)
        self.assertIn("no approved identity 'approved/action/nested'", error)
        self.policy.write_bytes(
            json.dumps(
                {"github_owned_allowed": False, "verified_allowed": False, "patterns_allowed": [f"approved/action@{OLD}", f"approved/action/nested@{OLD}"]}
            ).encode()
        )
        status, _, error = self.invoke(*args)
        self.assertEqual(status, 0, error)
        composite.write_bytes(original.replace(f"approved/action@{OLD}".encode(), f"approved/action@{NEW}".encode(), 1))
        status, _, error = self.invoke(*args)
        self.assertEqual(status, 1)
        self.assertIn("action.yml:6", error)
        self.assertIn("runs.steps[0].uses", error)
        self.assertIn("runs.steps[1].uses", error)

    def test_composite_update_previews_and_publishes_only_selected_reference_bytes(self):
        directory = self.root / "actions" / "setup"
        directory.mkdir(parents=True)
        composite = directory / "action.yaml"
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=repr(newline)):
                original = (
                    (
                        "\ufeffname: café setup\ndescription: 🧪 keep these bytes\nruns:\n  using: composite\n  steps:\n"
                        f'    - uses: "approved/action@{OLD}" # v1.0.0 keep\n'
                        f"    - uses: approved/action/nested@{OLD} # v1.0.0 keep\n"
                        f"    - uses: '$/.github/actions/setup' # v1.0.0 {OLD}\n"
                    )
                    .replace("\n", newline)
                    .encode("utf-8")
                )
                composite.write_bytes(original)
                before_workflow = self.workflow.read_bytes()
                args = ("update", "--policy", str(self.update_policy), str(self.workflow), str(directory.parent))
                with patch.object(action_updates, "resolve", return_value=(NEW, "v2.0.0")):
                    self.assertEqual(self.invoke(*args, "--dry-run")[0], 0)
                    self.assertEqual(self.invoke(*args, "--check")[0], 1)
                    self.assertEqual(composite.read_bytes(), original)
                    self.assertEqual(self.workflow.read_bytes(), before_workflow)
                    status, report, error = self.invoke(*args)
                    self.assertEqual(status, 0, error)
                    self.assertIn("runs.steps[0].uses", report)
                    self.assertIn("Skipped", report)
                    expected = original.replace(f'approved/action@{OLD}" # v1.0.0'.encode(), f'approved/action@{NEW}" # v2.0.0'.encode())
                    self.assertEqual(composite.read_bytes(), expected)
                    expected_workflow = before_workflow.replace(f"approved/action@{OLD}' # v1.0.0".encode(), f"approved/action@{NEW}' # v2.0.0".encode())
                    self.assertEqual(self.workflow.read_bytes(), expected_workflow)
                    self.assertEqual(self.invoke(*args, "--check")[0], 0)
                    self.assertEqual(composite.read_bytes(), expected)
                self.workflow.write_bytes(before_workflow)

    def test_invalid_composite_aborts_updates_to_all_inputs(self):
        # Sort after ci.yml so a valid workflow candidate is prepared first.
        composite = self.root / "z-action.yml"
        workflow_before = self.workflow.read_bytes()
        for scalar in (
            "approved/action@v1 # v1",
            f"approved/action@{OLD}",
            f"&pin approved/action@{OLD} # v1",
            f">-\n        approved/action@{OLD}\n      # v1",
        ):
            with self.subTest(scalar=scalar):
                before = f"runs:\n  using: composite\n  steps:\n    - uses: {scalar}\n".encode()
                composite.write_bytes(before)
                with patch.object(action_updates, "resolve", return_value=(NEW, "v2.0.0")):
                    status, output, error = self.invoke("update", "--policy", str(self.update_policy), str(self.workflow), str(composite))
                self.assertEqual(status, 1, error)
                self.assertIn("action.yml:", error)
                self.assertNotIn("Applied", output)
                self.assertNotIn("Update ", output)
                self.assertEqual(composite.read_bytes(), before)
                self.assertEqual(self.workflow.read_bytes(), workflow_before)

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
            expected = before.replace(f"approved/action@{OLD}' # v1.0.0".encode(), f"approved/action@{NEW}' # v2.0.0".encode())
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
