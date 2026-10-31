"""Installed shared-environment scanner selection and failure contracts."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from importlib.metadata import version
from pathlib import Path
from typing import override
from unittest.mock import patch

from packaging.requirements import Requirement

from research_repo_tools import _semgrep_launcher, cli, python_baseline, semgrep_tool


class TestSemgrepTool(unittest.TestCase):
    def test_unlimited_stack_uses_the_prior_finite_budget_without_changing_finite_limits(self):
        for infinity in (-1, (1 << 64) - 1):
            with self.subTest(infinity=infinity):
                self.assertEqual(_semgrep_launcher.stack_limits(8 << 20, infinity, infinity), (8 << 20, 1_000_000_000))
                self.assertEqual(_semgrep_launcher.stack_limits(32 << 20, infinity, infinity), (32 << 20, 100 * (32 << 20)))
                self.assertEqual(_semgrep_launcher.stack_limits(infinity, infinity, infinity), (1_000_000_000, 1_000_000_000))
                self.assertEqual(_semgrep_launcher.stack_limits(8 << 20, 64 << 20, infinity), (8 << 20, 64 << 20))
        infinity = (1 << 64) - 1
        self.assertEqual(_semgrep_launcher.stack_limits(infinity - 1, infinity, infinity), (infinity - 1, infinity - 1))

    def test_scanner_child_limit_guard_preserves_the_callers_limits(self):
        if os.name == "nt":
            self.assertEqual(semgrep_tool.command(self.binary), (str(self.binary),))
            return
        import resource

        before = resource.getrlimit(resource.RLIMIT_STACK)
        probe = self.root / "probe.py"
        probe.write_bytes(b"import json, resource, sys\nprint(json.dumps([sys.argv[1:], resource.getrlimit(resource.RLIMIT_STACK)]))\n")
        invocation = (sys.executable, "-I", "-X", "utf8", "-m", "research_repo_tools._semgrep_launcher", sys.executable)
        result = subprocess.run([*invocation, str(probe), "literal café"], capture_output=True, check=True, encoding="utf-8", timeout=30)
        arguments, selected = json.loads(result.stdout)
        self.assertEqual(arguments, ["literal café"])
        self.assertEqual(tuple(selected), _semgrep_launcher.stack_limits(*before, resource.RLIM_INFINITY))
        self.assertEqual(resource.getrlimit(resource.RLIMIT_STACK), before)

    @override
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="managed scanner café ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.binary = self.root / ("semgrep.exe" if os.name == "nt" else "semgrep")
        self.binary.write_bytes(b"fixture")
        self.binary.chmod(0o755)
        self.metadata = {"path": str(self.binary), "version": semgrep_tool.VERSION, "python": python_baseline.DEVELOPMENT_PYTHON}
        self.actual = semgrep_tool.VERSION
        self.calls = []
        self.python = Path(sys.executable)

    def native(self, command, args, **kwargs):
        self.calls.append((command, args, kwargs))
        if args[0] == "-I":
            self.assertEqual(Path(command), self.python)
            self.assertNotIn("uv", args)
            self.assertEqual(args[args.index("-X") + 1], "utf8")
            payload = json.dumps(self.metadata).encode("utf-8")
        else:
            self.assertEqual(Path(command), self.binary)
            self.assertEqual(args, ["--disable-version-check", "--version"])
            payload = (self.actual + "\r\n").encode("utf-8")
        self.assertEqual(kwargs["cwd"], self.root)
        return subprocess.CompletedProcess([str(command), *args], 0, payload, b"")

    def test_shared_interpreter_selection_checks_version_and_keeps_project_policy(self):
        injected = {
            "PYTHONPATH": "foreign",
            "PYTHONHOME": "foreign",
            "GH_TOKEN": "private",
            "github_token": "private",
        }
        preserved = {
            "VIRTUAL_ENV": "foreign",
            "UV_PROJECT": "foreign",
            "UV_CONFIG_FILE": "foreign",
            "UV_ENV_FILE": "foreign",
            "UV_PYTHON": "0.0.0",
            "UV_OVERRIDE": "foreign",
            "UV_CONSTRAINT": "foreign",
            "UV_BUILD_CONSTRAINT": "foreign",
            "UV_CACHE_DIR": str(self.root / "cache"),
            "UV_OFFLINE": "1",
            "SSL_CERT_FILE": str(self.root / "cert.pem"),
        }
        with patch.dict(os.environ, injected | preserved), patch.object(semgrep_tool, "run_command_bytes", side_effect=self.native):
            before = dict(os.environ)
            self.assertEqual(semgrep_tool.setup(self.root), self.binary)
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(len(self.calls), 2)
        for _, _, kwargs in self.calls:
            self.assertTrue(set(injected).isdisjoint(kwargs["env"]))
            self.assertEqual({key: kwargs["env"][key] for key in preserved}, preserved)
            self.assertEqual(kwargs["env"]["PYTHONSAFEPATH"], "1")
        self.assertFalse(Path(self.calls[-1][2]["env"]["SEMGREP_SETTINGS_FILE"]).parent.exists())
        self.assertFalse((self.root / "uv.lock").exists())
        self.assertFalse((self.root / ".venv").exists())

    def test_explicit_python_interpreter_is_used_for_the_metadata_query(self):
        self.python = self.root / ("python.exe" if os.name == "nt" else "python")
        shutil.copy2(sys.executable, self.python)
        with patch.object(semgrep_tool, "run_command_bytes", side_effect=self.native):
            self.assertEqual(semgrep_tool.setup(self.root, python=self.python), self.binary)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][0], self.python)

    def test_missing_python_interpreter_fails_before_running_commands(self):
        with patch.object(semgrep_tool, "run_command_bytes") as run:
            with self.assertRaisesRegex(RuntimeError, "Semgrep .* setup failed"):
                semgrep_tool.setup(self.root, python=self.root / "absent-python")
        run.assert_not_called()

    def test_returned_executable_has_one_canonical_cache_identity(self):
        alias = self.root / "alias"
        alias.mkdir()
        self.metadata["path"] = str(alias / ".." / self.binary.name)
        with patch.object(semgrep_tool, "run_command_bytes", side_effect=self.native):
            self.assertEqual(semgrep_tool.setup(self.root), self.binary)

    def test_wrong_package_python_path_and_executable_version_fail_without_fallback(self):
        valid = self.metadata.copy()
        for changed in ({"version": "0.0.0"}, {"python": "3.14"}, {"path": "relative/semgrep"}, {"path": str(self.root / "absent")}, {"path": str(self.root)}):
            with self.subTest(changed=changed), patch.object(semgrep_tool, "run_command_bytes", side_effect=self.native):
                self.calls.clear()
                self.metadata = valid | changed
                with self.assertRaisesRegex(RuntimeError, "Semgrep .* setup failed"):
                    semgrep_tool.setup(self.root)
                self.assertEqual(len(self.calls), 1)
        self.metadata = valid
        self.actual = "0.0.0"
        with patch.object(semgrep_tool, "run_command_bytes", side_effect=self.native):
            with self.assertRaisesRegex(RuntimeError, "executable reported '0.0.0'"):
                semgrep_tool.setup(self.root)

    def test_provisioning_failure_and_timeout_are_actionable_and_redact_release_credentials(self):
        for error in (subprocess.CalledProcessError(2, ["uv"], stderr=b"offline PRIVATE_TOKEN"), subprocess.TimeoutExpired("uv", 300)):
            with (
                self.subTest(error=error),
                patch.dict(os.environ, {"GH_TOKEN": "PRIVATE_TOKEN"}),
                patch.object(semgrep_tool, "run_command_bytes", side_effect=error),
            ):
                with self.assertRaisesRegex(RuntimeError, "Semgrep .* setup failed") as raised:
                    semgrep_tool.setup(self.root)
                self.assertNotIn("PRIVATE_TOKEN", str(raised.exception))

    def test_setup_cli_reports_the_pinned_scanner_and_preserves_failure_status(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(semgrep_tool, "setup", return_value=self.binary) as provision, contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["--root", str(self.root), "semgrep", "setup"]), 0)
            provision.assert_called_once_with(self.root)
        self.assertIn(f"Semgrep {semgrep_tool.VERSION}", out.getvalue())
        with patch.object(semgrep_tool, "setup", side_effect=RuntimeError("setup unavailable")), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["--root", str(self.root), "semgrep", "setup"]), 1)
        self.assertIn("setup unavailable", err.getvalue())

    def test_shared_override_adoption_is_idempotent_and_preserves_unrelated_constraints(self):
        original = f'[project]\nname="example"\nrequires-python=">=3.15"\n[dependency-groups]\ndev=["research-repo-tools[semgrep]=={version("research-repo-tools")}"]\n'
        original += '[tool.uv]\noverride-dependencies=["unrelated==1"] # retained\n'
        adopted = semgrep_tool.adopt_manifest(original)
        self.assertEqual(
            tomllib.loads(adopted)["tool"]["uv"]["override-dependencies"],
            ["unrelated==1", *[str(Requirement(raw)) for raw in semgrep_tool.constraints()]],
        )
        self.assertIn("# retained", adopted)
        self.assertEqual(semgrep_tool.adopt_manifest(adopted), adopted)
        self.assertEqual(semgrep_tool.adopt_manifest(original.replace("[semgrep]", "")), original.replace("[semgrep]", ""))
        with self.assertRaisesRegex(ValueError, "conflicts with the shared Semgrep override"):
            semgrep_tool.adopt_manifest(original.replace("unrelated==1", "pywin32==311"))


if __name__ == "__main__":
    unittest.main()
