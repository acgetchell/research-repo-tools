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

from packaging.markers import default_environment
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
        self.actual = "1.183.0"
        self.markers = dict(default_environment()) | {
            "python_version": python_baseline.DEVELOPMENT_PYTHON,
            "python_full_version": "3.15.1",
            "sys_platform": "darwin",
            "platform_machine": "arm64",
        }
        self.metadata = {"path": str(self.binary), "version": self.actual, "markers": self.markers}
        launch = patch.object(semgrep_tool, "command_for_python", return_value=(str(self.binary),))
        launch.start()
        self.addCleanup(launch.stop)
        self.calls = []
        self.python = Path(sys.executable)

    def native(self, command, args, **kwargs):
        self.calls.append((command, args, kwargs))
        if args[0] == "lock":
            self.assertEqual(command, "uv")
            self.assertIn("--check", args)
            self.assertIn("--offline", args)
            self.assertEqual(args[args.index("--project") + 1], str(self.root))
            self.assertEqual(args[args.index("--python") + 1], str(self.python))
            self.assertNotIn("UV_PROJECT", kwargs["env"])
            payload = b""
        elif args[0] == "-I":
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
        for changed in (
            {"version": "0.0.0"},
            {"version": "2.0.0"},
            {"version": "1.184.0rc1"},
            {"markers": self.markers | {"python_version": "3.14"}},
            {"path": "relative/semgrep"},
            {"path": str(self.root / "absent")},
            {"path": str(self.root)},
        ):
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

    def test_lock_chooses_the_active_branch_and_requires_the_installed_version(self):
        manifest = self.root / "pyproject.toml"
        manifest.write_bytes(b'[project]\nname="scanner"\nversion="0.1.0"\n')
        lock = self.root / "uv.lock"
        lock.write_bytes(
            b"version=1\n"
            b'[[package]]\nname="semgrep"\nversion="1.182.1"\nsource={registry="https://pypi.org/simple"}\n'
            b"resolution-markers=[\"sys_platform == 'darwin' and platform_machine == 'x86_64'\"]\n"
            b'[[package]]\nname="semgrep"\nversion="1.183.0"\nsource={registry="https://pypi.org/simple"}\n'
            b"resolution-markers=[\"sys_platform != 'darwin' or platform_machine != 'x86_64'\"]\n"
        )
        original = lock.read_bytes()
        with patch.dict(os.environ, {"UV_PROJECT": "redirect"}), patch.object(semgrep_tool, "run_command_bytes", side_effect=self.native):
            self.assertEqual(semgrep_tool.verify(self.root), semgrep_tool.VerifiedScanner(self.binary, "1.183.0"))
            self.assertEqual([args[0] for _, args, _ in self.calls], ["-I", "lock", "--disable-version-check"])
            self.assertEqual(lock.read_bytes(), original)
            self.metadata["version"] = self.actual = "1.182.1"
            with self.assertRaisesRegex(RuntimeError, "uv.lock selects 1.183.0.*installed Semgrep is 1.182.1"):
                semgrep_tool.setup(self.root)
            self.metadata["markers"] = self.markers | {"platform_machine": "x86_64"}
            self.assertEqual(semgrep_tool.verify(self.root).version, "1.182.1")
            lock.unlink()
            with self.assertRaisesRegex(RuntimeError, "requires both pyproject.toml and uv.lock"):
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

    def test_setup_cli_reports_the_verified_scanner_and_preserves_failure_status(self):
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(semgrep_tool, "verify", return_value=semgrep_tool.VerifiedScanner(self.binary, self.actual)) as provision,
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(cli.main(["--root", str(self.root), "semgrep", "setup"]), 0)
            provision.assert_called_once_with(self.root)
        self.assertIn(f"Semgrep {self.actual}", out.getvalue())
        with patch.object(semgrep_tool, "verify", side_effect=RuntimeError("setup unavailable")), contextlib.redirect_stderr(err):
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
