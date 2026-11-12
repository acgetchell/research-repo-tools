"""Public Actions CLI contracts, also run from isolated distributions."""

import base64
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import override
from unittest.mock import patch

from research_repo_tools import action_updates, cli, files
from research_repo_tools.changelog import template
from research_repo_tools.process import get_safe_executable

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

    def consistency_policy(self, target="latest"):
        self.update_policy.write_bytes(f'[actions]\n"approved/action" = "{target}"\n[consistency]\nactions = ["APPROVED/Action"]\n'.encode())

    def test_offline_consistency_accepts_synchronized_workflows_and_composites_without_writes(self):
        self.consistency_policy()
        composite = self.root / "composites" / "café setup" / "action.yaml"
        composite.parent.mkdir(parents=True)
        sha = "abcdef01" * 5
        self.workflow.write_bytes(self.workflow.read_bytes().replace(OLD.encode(), sha.upper().encode()))
        composite.write_bytes(
            (
                "\ufeffname: café setup\r\nruns:\r\n  using: composite\r\n  steps:\r\n"
                f"    - uses: &pin approved/action@{sha} # v1.0.0 another explanation\r\n"
                "    - uses: *pin\r\n"
                f"    - uses: approved/action/nested@{NEW} # v2\r\n"
                "    - uses: ./local\r\n    - uses: '$/.github/actions/setup'\r\n    - uses: docker://image:latest\r\n"
            ).encode("utf-8")
        )
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        # No subprocess (including gh), sockets, or pin resolution may be used.
        with (
            patch.object(subprocess, "Popen", side_effect=AssertionError("offline check launched a process")),
            patch("socket.socket", side_effect=AssertionError("offline check opened a socket")),
            patch.object(action_updates, "resolve", side_effect=AssertionError("offline check resolved latest")),
        ):
            status, output, error = self.invoke("check", "--policy", str(self.update_policy), "ci.yml", "composites", str(composite))
        self.assertEqual(status, 0, error)
        self.assertIn("1 identities in 2 files", output)
        self.assertEqual({path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}, before)

    def test_workflow_only_bump_reports_stale_composite_and_each_kind_of_drift(self):
        self.consistency_policy()
        composite = self.root / "action.yml"
        composite.write_bytes(f"runs:\n  using: composite\n  steps:\n    - uses: approved/action@{OLD} # v1.0.0\n".encode())
        original = self.workflow.read_bytes()
        for sha, tag in ((NEW, "v1.0.0"), (OLD, "v2.0.0"), (NEW, "v2.0.0")):
            with self.subTest(sha=sha, tag=tag):
                changed = original.replace(OLD.encode(), sha.encode()).replace(b"v1.0.0", tag.encode())
                self.workflow.write_bytes(changed)
                status, output, error = self.invoke("check", "--policy", str(self.update_policy), "ci.yml", "action.yml")
                self.assertEqual(status, 1)
                self.assertEqual(output, "")
                for diagnostic in ("inconsistent approved/action", "ci.yml:7", "action.yml:4", OLD, sha, "v1.0.0", tag):
                    self.assertIn(diagnostic, error)
                self.assertEqual(self.workflow.read_bytes(), changed)
                self.assertIn(f"approved/action@{OLD} # v1.0.0".encode(), composite.read_bytes())

    def test_explicit_policy_tag_must_match_even_with_a_single_workflow_pin(self):
        before = self.workflow.read_bytes()
        for target, expected in (("v1.0.0", 0), ("latest", 0), ("v2.0.0", 1), ("1.0.0", 1)):
            with self.subTest(target=target):
                self.consistency_policy(target)
                status, _, error = self.invoke("check", "--policy", str(self.update_policy), "ci.yml")
                self.assertEqual(status, expected, error)
                if expected:
                    for diagnostic in ("ci.yml:7", "version comment 'v1.0.0'", f"explicit policy tag '{target}'", "updates.toml"):
                        self.assertIn(diagnostic, error)
                self.assertEqual(self.workflow.read_bytes(), before)

    def test_consistency_identities_are_independent_of_update_and_compatibility_targets(self):
        self.update_policy.write_bytes(
            b'[actions]\n"unrelated/target"="v3"\n[consistency]\nactions=["approved/action"]\n'
            b'[compatibility."unrelated/wrapper"]\npath="support/versions"\ntool="scanner"\n'
        )
        status, _, error = self.invoke("check", "--policy", str(self.update_policy), "ci.yml")
        self.assertEqual(status, 0, error)

    def test_missing_identity_names_the_declared_scan_scope(self):
        self.update_policy.write_bytes(b'[actions]\n[consistency]\nactions=["approved/action/Nested"]\n')
        for scope in (("ci.yml",), ("empty workflows",)):
            with self.subTest(scope=scope):
                (self.root / "empty workflows").mkdir(exist_ok=True)
                status, _, error = self.invoke("check", "--policy", str(self.update_policy), *scope)
                self.assertEqual(status, 1)
                self.assertIn("approved/action/Nested", error)
                self.assertIn("configured scope", error)
                # Config resolves the CLI root, including Windows short names.
                self.assertIn(str(self.root.resolve() / scope[0]), error)

    def test_missing_identity_reports_scope_from_a_resolved_root_alias(self):
        (self.root / "nested").mkdir()
        alias = self.root / "nested" / ".."
        self.assertNotEqual(str(alias), str(alias.resolve()))
        self.assertTrue(alias.samefile(self.root))
        with patch.object(self, "root", alias):
            self.test_missing_identity_names_the_declared_scan_scope()

    def test_invalid_policy_identities_name_the_file_and_section(self):
        for section, declaration in (
            ("actions", '[actions]\n"approved/*"="latest"\n'),
            ("compatibility", '[actions]\n[compatibility."approved/*"]\npath="support/versions"\ntool="scanner"\n'),
        ):
            with self.subTest(section=section):
                self.update_policy.write_bytes(declaration.encode())
                for command in ("check", "update"):
                    status, output, error = self.invoke(command, "--policy", str(self.update_policy), "ci.yml")
                    self.assertEqual(status, 1)
                    self.assertEqual(output, "")
                    self.assertIn(str(self.update_policy), error)
                    self.assertIn(f"{section}.approved/*", error)
                    self.assertIn("expected exact owner/repo", error)
                    self.assertNotIn("Traceback", error)

    def test_packaged_check_uses_provisioned_environment_without_sync(self):
        self.consistency_policy()
        workflows = self.root / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_bytes(self.workflow.read_bytes())
        (workflows.parent / "action-updates.toml").write_bytes(self.update_policy.read_bytes())
        (self.root / "justfile").write_text(template("justfile"), encoding="utf-8", newline="\n")
        # Synchronizing this manifest would require unavailable dependencies.
        (self.root / "pyproject.toml").write_bytes(
            b'[project]\nname="offline-consumer"\nversion="0.0.0"\nrequires-python=">=3.15"\n'
            b'[dependency-groups]\ntooling=["rrt-offline-fixture-unavailable==0.0.0"]\n[tool.uv]\npackage=false\n'
        )
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with tempfile.TemporaryDirectory() as cache:
            env = {
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "UV_CACHE_DIR": cache,
                "UV_OFFLINE": "1",
                "UV_PROJECT_ENVIRONMENT": sys.prefix,
                "UV_PYTHON_DOWNLOADS": "never",
            }
            result = subprocess.run(
                [get_safe_executable("just"), "--justfile", str(self.root / "justfile"), "actions-check"],
                cwd=self.root,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Actions pin consistency passed", result.stdout)
        self.assertEqual({path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}, before)

    def test_malformed_selected_pins_report_file_and_line(self):
        self.consistency_policy()
        for scalar, diagnostic in (
            ("approved/action@v1 # v1", "full commit SHA"),
            (f"approved/action@{OLD[:7]} # v1", "full commit SHA"),
            (f"approved/action@{'g' * 40} # v1", "full commit SHA"),
            (f"approved/action@{OLD}", "inline version comment"),
            (f"approved/action@{OLD} # v1-beta", "inline version comment"),
            (f"approved/action@{OLD} # v01.0", "invalid stable version comment"),
            (f">-\n        approved/action@{OLD}\n    # v1", "inline version comment"),
            ("approved/action", "unsupported uses reference"),
            (f"approved/action/../bad@{OLD} # v1", "expected exact"),
        ):
            with self.subTest(scalar=scalar):
                before = f"jobs:\n  call:\n    uses: {scalar}\n".encode()
                self.workflow.write_bytes(before)
                status, output, error = self.invoke("check", "--policy", str(self.update_policy), "ci.yml")
                self.assertEqual(status, 1)
                self.assertEqual(output, "")
                self.assertIn("ci.yml:3", error)
                self.assertIn(diagnostic, error)
                self.assertNotIn("Traceback", error)
                self.assertEqual(self.workflow.read_bytes(), before)

    def test_packaged_recipes_share_workflow_only_or_explicit_composite_scope(self):
        source = template("justfile")
        justfile = self.root / "justfile"
        for composite in (None, ".github/actions/café setup/action.yml"):
            with self.subTest(composite=composite):
                configured = source
                if composite is not None:
                    configured = configured.replace(
                        'actions_inputs := quote(".github/workflows")',
                        f'actions_inputs := quote(".github/workflows") + " " + quote("{composite}")',
                    )
                justfile.write_text(configured, encoding="utf-8", newline="\n")
                for recipe, command in (("actions-check", "check"), ("update-actions", "update")):
                    result = subprocess.run(
                        [get_safe_executable("just"), "--justfile", str(justfile), "--dry-run", recipe],
                        cwd=self.root,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        check=True,
                    )
                    arguments = shlex.split(result.stderr)
                    self.assertEqual(
                        arguments[arguments.index("actions") :],
                        [
                            "actions",
                            command,
                            "--policy",
                            ".github/action-updates.toml",
                            ".github/workflows",
                            *([composite] if composite else []),
                            *(["$@"] if command == "update" else []),
                        ],
                    )

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
        for payload in (
            "invalid JSON",
            '{"draft":true,"draft":false,"prerelease":false,"tag_name":"v2.0.0"}',
            '{"draft":false,"prerelease":false,"tag_name":"v2.0.0","unknown":NaN}',
            "[" * 2000 + "0" + "]" * 2000,
        ):
            with self.subTest(payload=payload[:80]):
                with patch.object(action_updates, "run_safe_command", return_value=subprocess.CompletedProcess([], 0, payload, "")) as command:
                    status, output, error = self.invoke("update", "--policy", str(self.update_policy), str(self.workflow))
                self.assertEqual(status, 1)
                self.assertIn("GitHub API returned invalid JSON", error)
                self.assertEqual(command.call_count, 1)
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
