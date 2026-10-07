"""Portable ownership and stale-pin contracts for installed packages."""

import io
import json
import os
import subprocess
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from research_repo_tools import cli, tool_pins, uv_update


class TestOwnersConsumer(unittest.TestCase):
    def invoke(self, root, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(["--root", str(root), *arguments])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_uv_tool_receipt_preview_stale_pin_apply_and_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "pyproject.toml"
            before = b"[tool.uv]\r\nrequired-version = '==0.12.15' # keep\r\n"
            manifest.write_bytes(before)
            tools = root / "tools"
            environment = tools / "uv"
            scripts = environment / ("Scripts" if os.name == "nt" else "bin")
            scripts.mkdir(parents=True)
            binary = scripts / ("uv.exe" if os.name == "nt" else "uv")
            binary.write_bytes(b"native uv fixture")
            (environment / "pyvenv.cfg").write_bytes(b"home = interpreter\n")
            receipt = environment / "uv-receipt.toml"
            # JSON string quoting is also valid TOML for paths.
            receipt.write_bytes(
                (
                    '[tool]\npython="3.14"\nrequirements=[{name="uv"}]\nconstraints=[{name="uv",specifier=">=0.12"}]\n'
                    f'entrypoints=[{{name="uv",install-path={json.dumps(str(binary))},from="uv"}}]\n'
                ).encode()
            )
            original_receipt = receipt.read_bytes()
            version = "0.12.19"
            operations = []

            def run(command, args, **kwargs):
                nonlocal version
                if args == ["--version"]:
                    output = f"uv {version}"
                elif args == ["tool", "dir", "--no-config"]:
                    output = str(tools)
                else:
                    self.assertEqual(args, ["tool", "upgrade", "uv", "--no-config", "--prerelease", "disallow", "--no-python-downloads"])
                    self.assertNotIn("UV_PYTHON", kwargs["env"])
                    operations.append(args)
                    version = "0.12.21"
                    output = ""
                return subprocess.CompletedProcess([command, *args], 0, output, "")

            with (
                patch.object(uv_update.shutil, "which", return_value=str(binary)),
                patch.object(uv_update, "run_safe_command", side_effect=run),
                patch.object(tool_pins, "run_safe_command", side_effect=run),
                patch.dict(os.environ, {"UV_PYTHON": "unrelated interpreter", "AXOUPDATER_CONFIG_PATH": str(root / "standalone")}),
            ):
                status, output, error = self.invoke(root, "deps", "update-uv", "--dry-run")
                self.assertEqual(status, 0, error)
                self.assertIn("uv owner: uv-tool", output)
                self.assertEqual(operations, [])
                self.assertEqual(manifest.read_bytes(), before)
                self.assertEqual(self.invoke(root, "deps", "update-uv")[0], 0)
                expected = before.replace(b"0.12.15", b"0.12.21")
                self.assertEqual(manifest.read_bytes(), expected)
                self.assertEqual(self.invoke(root, "deps", "update-uv")[0], 0)
                self.assertEqual(manifest.read_bytes(), expected)
            self.assertEqual(receipt.read_bytes(), original_receipt)
            self.assertEqual(tomllib.loads(manifest.read_text())["tool"]["uv"]["required-version"], "==0.12.21")

    def test_mixed_cargo_and_homebrew_inventory_preview_and_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            justfile = root / "justfile"
            before = b'cargo_pin := "1.0.0" # keep\r\nformat_pin := "0.1.0"\r\n'
            justfile.write_bytes(before)
            (root / "pyproject.toml").write_bytes(
                b'[tool.research-repo-tools.deps.tools]\ncargo_pin="cargo-nextest"\nformat_pin="dprint"\n'
                b'[tool.research-repo-tools.deps.tool-owners]\ndprint="homebrew"\n'
            )
            prefix = root / "brew" / "dprint"
            binary = prefix / "bin" / "dprint"
            binary.parent.mkdir(parents=True)
            binary.touch()

            def run(command, args, **kwargs):
                outputs = {
                    ("cargo", "install", "--list"): "cargo-nextest v1.2.0:\n    cargo-nextest\n",
                    ("brew", "--prefix", "dprint"): str(prefix),
                    ("brew", "list", "--versions", "dprint"): "dprint 0.60.1",
                    (str(binary), "--version"): "dprint 0.60.1",
                }
                return subprocess.CompletedProcess([], 0, outputs[(command, *args)], "")

            with (
                patch.object(tool_pins, "run_safe_command", side_effect=run),
                patch.object(tool_pins.shutil, "which", return_value=str(binary)),
            ):
                status, output, error = self.invoke(root, "deps", "update-tools", "--dry-run")
                self.assertEqual(status, 0, error)
                self.assertIn("format_pin", output)
                self.assertIn("0.60.1", output)
                self.assertEqual(justfile.read_bytes(), before)
                self.assertEqual(self.invoke(root, "deps", "update-tools")[0], 0)
                self.assertEqual(justfile.read_bytes(), before.replace(b"1.0.0", b"1.2.0").replace(b"0.1.0", b"0.60.1"))
                status, output, error = self.invoke(root, "deps", "update-tools")
                self.assertEqual(status, 0, error)
                self.assertIn("Unchanged format_pin", output)

    def test_managed_just_rejects_competing_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".python-version").write_bytes(b"3.14\n")
            manifest = root / "pyproject.toml"
            before = b'[tool.uv]\nrequired-version="==0.12.23"\n[tool.research-repo-tools.toolchain.binaries]\njust="1.58.0"\n'
            manifest.write_bytes(before)
            status, _, error = self.invoke(root, "toolchain", "check")
            self.assertEqual(status, 1)
            self.assertIn("Just is supplied by rust-just", error)
            self.assertEqual(manifest.read_bytes(), before)

    def test_homebrew_nextest_multiline_version_and_rejection_preserve_pins(self):
        version = "0.9.146"
        native = f"cargo-nextest {version} (abc123 2026-10-01)\nrelease: {version}\ncommit-hash: abc123\n"
        for output, inventory, valid in (
            (native, version, True),
            (native.replace(f"release: {version}", "release: 0.9.145"), version, False),
            (native + f"release: {version}\n", version, False),
            (native, "0.9.145", False),
            (native.replace(version, version + "-rc.1"), version, False),
        ):
            with self.subTest(output=output, inventory=inventory), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                justfile = root / "justfile"
                before = b'nextest_pin := "0.9.100" # keep\r\n'
                justfile.write_bytes(before)
                (root / "pyproject.toml").write_bytes(
                    b'[tool.research-repo-tools.deps.tools]\nnextest_pin="cargo-nextest"\n'
                    b'[tool.research-repo-tools.deps.tool-owners]\ncargo-nextest="homebrew"\n'
                )
                prefix = root / "brew" / "cargo-nextest"
                binary = prefix / "bin" / "cargo-nextest"
                binary.parent.mkdir(parents=True)
                binary.touch()

                def run(command, args, **_kwargs):
                    outputs = {
                        ("brew", "--prefix", "cargo-nextest"): str(prefix),
                        ("brew", "list", "--versions", "cargo-nextest"): f"cargo-nextest {inventory}",
                        (str(binary), "--version"): output,
                    }
                    return subprocess.CompletedProcess([], 0, outputs[(command, *args)], "")

                with patch.object(tool_pins, "run_safe_command", side_effect=run), patch.object(tool_pins.shutil, "which", return_value=str(binary)):
                    status, _, error = self.invoke(root, "deps", "update-tools", "--dry-run")
                    self.assertEqual(status, 0 if valid else 1, error)
                    self.assertEqual(justfile.read_bytes(), before)
                    status, report, error = self.invoke(root, "deps", "update-tools")
                    self.assertEqual(status, 0 if valid else 1, error)
                    self.assertEqual(justfile.read_bytes(), before.replace(b"0.9.100", version.encode()) if valid else before)
                    if valid:
                        self.assertEqual(self.invoke(root, "deps", "update-tools")[0], 0)
                    else:
                        self.assertNotIn("Verified homebrew", report)
                        self.assertNotIn("Traceback", error)


if __name__ == "__main__":
    unittest.main()
