"""Installed managed-scanner failure and environment-isolation contracts."""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest.mock import patch

from research_repo_tools import cli, semgrep_tool


class TestSemgrepTool(unittest.TestCase):
    @override
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="managed scanner café ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.binary = self.root / ("semgrep.exe" if os.name == "nt" else "semgrep")
        self.binary.write_bytes(b"fixture")
        self.binary.chmod(0o755)
        self.metadata = {"path": str(self.binary), "version": semgrep_tool.VERSION, "python": semgrep_tool.PYTHON}
        self.actual = semgrep_tool.VERSION
        self.calls = []

    def native(self, command, args, **kwargs):
        self.calls.append((command, args, kwargs))
        if args[0] == "tool":
            self.assertEqual(args[args.index("--from") + 1], f"semgrep=={semgrep_tool.VERSION}")
            self.assertEqual(args[args.index("--python") + 1], semgrep_tool.PYTHON)
            for flag in ("--isolated", "--no-config", "--no-env-file", "--managed-python", "-I"):
                self.assertIn(flag, args)
            self.assertEqual(args[args.index("-X") + 1], "utf8")
            payload = json.dumps(self.metadata).encode("utf-8")
        else:
            self.assertEqual(Path(command), self.binary)
            self.assertEqual(args, ["--disable-version-check", "--version"])
            self.assertEqual(kwargs["env"]["PATH"].split(os.pathsep)[0], str(self.binary.parent))
            payload = (self.actual + "\r\n").encode("utf-8")
        self.assertEqual(kwargs["cwd"], self.root)
        return subprocess.CompletedProcess([str(command), *args], 0, payload, b"")

    def test_isolated_provisioning_checks_real_selected_version_and_keeps_cache_policy(self):
        injected = {
            "PYTHONPATH": "foreign",
            "PYTHONHOME": "foreign",
            "VIRTUAL_ENV": "foreign",
            "UV_PROJECT": "foreign",
            "UV_CONFIG_FILE": "foreign",
            "UV_ENV_FILE": "foreign",
            "UV_PYTHON": "0.0.0",
            "UV_OVERRIDE": "foreign",
            "UV_CONSTRAINT": "foreign",
            "UV_BUILD_CONSTRAINT": "foreign",
            "GH_TOKEN": "private",
            "github_token": "private",
        }
        preserved = {"UV_CACHE_DIR": str(self.root / "cache"), "UV_OFFLINE": "1", "SSL_CERT_FILE": str(self.root / "cert.pem")}
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

    def test_returned_executable_has_one_canonical_cache_identity(self):
        alias = self.root / "alias"
        alias.mkdir()
        self.metadata["path"] = str(alias / ".." / self.binary.name)
        with patch.object(semgrep_tool, "run_command_bytes", side_effect=self.native):
            self.assertEqual(semgrep_tool.setup(self.root), self.binary)

    def test_wrong_package_python_path_and_executable_version_fail_without_fallback(self):
        valid = self.metadata.copy()
        for changed in ({"version": "0.0.0"}, {"python": "3.15"}, {"path": "relative/semgrep"}, {"path": str(self.root / "absent")}, {"path": str(self.root)}):
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


if __name__ == "__main__":
    unittest.main()
