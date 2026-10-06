"""Toolchain contracts repeated against isolated wheel and sdist installations."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from research_repo_tools import archives, cli, config, prebuilt_tools, process, python_adoption, python_baseline, toolchain, toolchain_clean
from research_repo_tools.toolchain_clean import apply_clean, plan_clean
from research_repo_tools.toolchain_config import load


class TestToolchainConsumer(unittest.TestCase):
    def test_adoption_keeps_helper_permissions_and_refuses_linked_environment(self):
        with tempfile.TemporaryDirectory(prefix="adoption consumer ") as directory:
            root = Path(directory).resolve()
            authority = python_baseline.baseline()
            (root / "pyproject.toml").write_bytes(
                (
                    '[project]\nname="consumer"\nversion="1.0.0"\nrequires-python=">=3.13"\n'
                    '[tool.uv]\npackage=true\nrequired-version="==0.12.19"\n'
                    f'[dependency-groups]\ntooling=["research-repo-tools=={authority.package_version}"]\n'
                    "[tool.research-repo-tools.toolchain]\ninherit-python=true\n"
                    "[tool.uv.dependency-groups]"
                ).encode()
            )
            (root / ".python-version").write_bytes(b"3.13\r\n")
            helper = root / "build-helper"
            helper.write_bytes(b"#!/bin/sh\nexit 0\n")
            helper.chmod(0o755)
            inventory = (".python-version", "build-helper", "pyproject.toml")
            before = {name: (root / name).read_bytes() for name in inventory}

            def execute(_command, args, *, cwd, **_kwargs):
                if args[0] == "lock":
                    self.assertEqual((cwd / helper.name).stat().st_mode & 0o111, helper.stat().st_mode & 0o111)
                    self.assertEqual((cwd / helper.name).read_bytes(), helper.read_bytes())
                    (cwd / "uv.lock").write_bytes(b"candidate lock\n")
                return subprocess.CompletedProcess([], 0, "", "")

            with (
                patch.object(python_adoption, "select_files", return_value=inventory),
                patch.object(python_adoption, "get_safe_executable", return_value="mock-uv"),
                patch.object(toolchain.Runtime, "uv_status", return_value=toolchain.Status("uv", "0.12.19", "0.12.19", "mock-uv", True)),
                patch.object(python_adoption, "run_safe_command", side_effect=execute) as runner,
            ):
                plan = python_adoption.plan_python_adoption(config.load(root=root))
                other = root / "external environment"
                other.mkdir()
                (other / "pyvenv.cfg").write_bytes(b"external environment\r\n")
                environment = root / ".venv"
                if os.name == "nt":
                    subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(environment), str(other)], capture_output=True, check=True)
                    self.assertTrue(environment.is_junction())
                else:
                    environment.symlink_to(other, target_is_directory=True)
                    self.assertTrue(environment.is_symlink())
                self.assertEqual(environment.resolve(), other)
                runner.reset_mock()
                with self.assertRaisesRegex(ValueError, "regular project virtual environment"):
                    python_adoption.apply_python_adoption(plan)
                runner.assert_not_called()
            self.assertEqual(before, {name: (root / name).read_bytes() for name in inventory})
            self.assertEqual((other / "pyvenv.cfg").read_bytes(), b"external environment\r\n")
            self.assertFalse((root / "uv.lock").exists())

    def test_relative_retention_root_uses_consumer_root(self):
        with tempfile.TemporaryDirectory(prefix="retention roots ") as directory:
            root = Path(directory).resolve()
            consumer = root / "consumer"
            retained = root / "retained"
            for path, version in ((consumer, "2.14.1"), (retained, "2.13.0")):
                path.mkdir()
                (path / "pyproject.toml").write_bytes(
                    (f'[tool.uv]\nrequired-version="==0.12.19"\n[tool.research-repo-tools.toolchain.cargo]\ngit-cliff="{version}"\n').encode()
                )
                (path / ".python-version").write_bytes(b"3.14\r\n")
                (path / "rust-toolchain.toml").write_bytes(b'[toolchain]\nchannel="1.98.0"\n')
            with patch.dict(os.environ, {"RESEARCH_REPO_TOOLS_HOME": str(root / "managed")}):
                settings = config.load(root=consumer)
                runtime = toolchain.Runtime(load(settings))
                obsolete = runtime.cargo_root(runtime.plan.cargo[0]).with_name("2.13.0")
                obsolete.mkdir(parents=True)
                self.assertEqual([item.path for item in plan_clean(settings).removals], [obsolete])
                self.assertEqual(plan_clean(settings, keep_roots=(Path("../retained"),)).removals, ())
                self.assertEqual(cli.main(["--root", str(consumer), "toolchain", "clean", "--keep-root", "../retained", "--apply"]), 0)
                self.assertTrue(obsolete.is_dir())

    def test_private_python_alias_removal_preserves_user_store(self):
        with tempfile.TemporaryDirectory(prefix="python cleanup spaces ") as directory:
            root = Path(directory).resolve()
            (root / "pyproject.toml").write_bytes(b'[tool.uv]\nrequired-version="==0.12.19"\n')
            (root / ".python-version").write_bytes(b"3.14\r\n")
            with patch.dict(os.environ, {"RESEARCH_REPO_TOOLS_HOME": str(root / "managed"), "UV_TOOL_DIR": str(root / "tools")}):
                settings = config.load(root=root)
                runtime = toolchain.Runtime(load(settings))
                system = "windows" if "windows" in runtime.host else "macos" if "darwin" in runtime.host else "linux"
                arch = runtime.host.split("-", 1)[0]
                key = f"cpython-3.13.7-{system}-{arch}-none"
                old = runtime.base / "python" / key
                old.mkdir(parents=True)
                (old / "sentinel").write_bytes(b"obsolete private interpreter")
                alias = old.with_name(key.replace("3.13.7", "3.13"))
                if os.name == "nt":
                    subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(old)], capture_output=True, check=True)
                    self.assertTrue(alias.is_junction())
                else:
                    alias.symlink_to(old, target_is_directory=True)
                    self.assertTrue(alias.is_symlink())
                self.assertEqual(alias.resolve(), old)
                user = root / "user Python"
                user.mkdir()
                (user / "sentinel").write_bytes(b"user-owned")
                entry = dict(key=key, version="3.13.7", path=str(alias / "sentinel"), os=system, arch=arch, implementation="cpython", variant="default")

                def inventory(_command, args, **kwargs):
                    if args[:2] == ["python", "list"]:
                        self.assertEqual(kwargs["env"]["UV_PYTHON_INSTALL_DIR"], str(runtime.base / "python"))
                        output = json.dumps([entry])
                    else:
                        self.assertEqual(args[:2], ["tool", "dir"])
                        output = str(root / "tools")
                    return subprocess.CompletedProcess([], 0, output, "")

                with (
                    patch.object(toolchain.Runtime, "uv_status", return_value=toolchain.Status("uv", "0.12.19", "0.12.19", "mock-uv", True)),
                    patch.object(toolchain_clean, "run_safe_command", side_effect=inventory),
                ):
                    preview = plan_clean(settings)
                    self.assertEqual([entry.path for entry in preview.removals], [alias, old])
                    apply_clean(preview, settings)
                self.assertFalse(alias.exists() or alias.is_symlink() or alias.is_junction())
                self.assertFalse(old.exists())
                self.assertEqual((user / "sentinel").read_bytes(), b"user-owned")

    def test_preview_and_apply_keep_current_and_unowned_installations(self):
        with tempfile.TemporaryDirectory(prefix="cleanup consumer spaces ") as directory:
            root = Path(directory).resolve()
            (root / "pyproject.toml").write_bytes(
                b'[tool.uv]\nrequired-version="==0.12.19"\n'
                b'[tool.research-repo-tools.toolchain.cargo]\ngit-cliff="2.14.1"\n'
                b'[tool.research-repo-tools.toolchain.binaries]\ngitleaks="8.30.1"\n'
            )
            (root / ".python-version").write_bytes(b"3.14\r\n")
            (root / "rust-toolchain.toml").write_bytes(b'[toolchain]\r\nchannel="1.98.0"\r\n')
            with patch.dict(os.environ, {"RESEARCH_REPO_TOOLS_HOME": str(root / "managed")}):
                settings = config.load(root=root)
                runtime = toolchain.Runtime(load(settings))
                cargo = runtime.cargo_root(runtime.plan.cargo[0])
                binary = runtime.binary_path(runtime.plan.binaries[0]).parent.parent
                stale = [cargo.with_name("2.13.0"), binary.with_name("8.29.0")]
                retained = [cargo, cargo.with_name("2.15.0"), binary, root / "user cargo", runtime.base / "unknown"]
                for path in [*stale, *retained]:
                    path.mkdir(parents=True)
                    (path / "sentinel").write_bytes(b"owned fixture\r\n")
                preview = plan_clean(settings)
                self.assertEqual({entry.path for entry in preview.removals}, set(stale))
                self.assertTrue(all(path.is_dir() for path in stale))
                self.assertEqual(cli.main(["--root", str(root), "toolchain", "clean"]), 0)
                self.assertTrue(all(path.is_dir() for path in stale))
                apply_clean(preview, settings)
                self.assertTrue(all(not path.exists() for path in stale))
                self.assertTrue(all((path / "sentinel").read_bytes() == b"owned fixture\r\n" for path in retained))
                self.assertEqual(plan_clean(settings).removals, ())


class TestManagedReleaseConsumer(unittest.TestCase):
    """The supported CLI authenticates only metadata and repairs verified caches."""

    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix="managed release consumer "))
        self.root = Path(directory).resolve()
        (self.root / "pyproject.toml").write_bytes(
            b'[tool.uv]\nrequired-version="==0.12.19"\n[tool.research-repo-tools.toolchain.binaries]\nosv-scanner="2.6.0"\n'
        )
        (self.root / ".python-version").write_bytes(b"3.14\n")
        self.originals = {path: path.read_bytes() for path in self.root.iterdir()}
        self.enterContext(
            patch.dict(os.environ, {"RESEARCH_REPO_TOOLS_HOME": str(self.root / "managed"), "GITHUB_TOKEN": "synthetic-github", "GH_TOKEN": "synthetic-gh"})
        )
        runtime = toolchain.Runtime(load(config.load(root=self.root)))
        tool = runtime.plan.binaries[0]
        self.target = runtime.binary_path(tool)
        self.payload = b"checksum-verified synthetic scanner"
        asset = prebuilt_tools.asset_name(tool, runtime.host)
        self.url = f"https://github.com/google/osv-scanner/releases/download/v2.6.0/{asset}"
        self.metadata = {
            "draft": False,
            "prerelease": False,
            "tag_name": "v2.6.0",
            "assets": [{"name": asset, "digest": "sha256:" + hashlib.sha256(self.payload).hexdigest(), "browser_download_url": self.url}],
        }
        self.failure = None
        self.requests = []
        self.downloads = []
        self.probes = []

        def metadata(request, *, timeout):
            self.assertEqual(request.full_url, "https://api.github.com/repos/google/osv-scanner/releases/tags/v2.6.0")
            self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-github")
            self.requests.append(request.full_url)
            if self.failure == "metadata":
                raise urllib.error.URLError("synthetic-github and synthetic-gh denied")
            return io.BytesIO(json.dumps(self.metadata).encode("utf-8"))

        class AssetResponse(io.BytesIO):
            def geturl(response):
                return self.url

        class AssetOpener:
            def open(opener, url, *, timeout):
                self.assertEqual(url, self.url)  # Asset downloads carry no authenticated Request.
                self.downloads.append(url)
                return AssetResponse(b"damaged transfer" if self.failure == "checksum" else self.payload)

        def probe(command, args, *, cwd, env, **kwargs):
            self.assertEqual(args, ["--version"])
            self.probes.append(Path(command))
            if self.failure == "probe":
                raise subprocess.CalledProcessError(19, [command, *args], stderr="synthetic-github and synthetic-gh probe failure")
            code = (
                "import os, pathlib, sys; "
                "assert 'GITHUB_TOKEN' not in os.environ and 'GH_TOKEN' not in os.environ; "
                "version = '2.6.0' if pathlib.Path(sys.argv[1]).read_bytes() == b'checksum-verified synthetic scanner' else '0.0.0'; "
                "print('osv-scanner version: ' + version)"
            )
            return process.run_command(sys.executable, ["-I", "-X", "utf8", "-c", code, command], cwd=cwd, env=env)

        self.enterContext(patch.object(prebuilt_tools.urllib.request, "urlopen", side_effect=metadata))
        self.enterContext(patch.object(archives.urllib.request, "build_opener", return_value=AssetOpener()))
        self.enterContext(patch.object(prebuilt_tools, "run_safe_command", side_effect=probe))
        # Binary-only sync must work even when ordinary tools are unavailable.
        for name in ("sync", "inspect", "uv_status", "python_status"):
            self.enterContext(patch.object(toolchain.Runtime, name, side_effect=AssertionError(f"binary sync called {name}")))

    def invoke(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(["--root", str(self.root), "toolchain", "sync-binaries"])
        self.assertEqual({path: path.read_bytes() for path in self.originals}, self.originals)
        self.assertFalse((self.root / ".venv").exists())
        self.assertEqual(os.environ["GITHUB_TOKEN"], "synthetic-github")
        self.assertEqual(os.environ["GH_TOKEN"], "synthetic-gh")
        self.assertNotIn("synthetic-github", stdout.getvalue() + stderr.getvalue())
        self.assertNotIn("synthetic-gh", stdout.getvalue() + stderr.getvalue())
        return status, stdout.getvalue(), stderr.getvalue()

    def test_cold_warm_and_damaged_caches_use_exact_pins_and_credential_free_children(self):
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(self.target.read_bytes(), self.payload)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(len(self.probes), 2)  # Staged candidate, then published file.
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(self.probes[-1], self.target)
        self.target.write_bytes(b"damaged cached binary")
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(self.target.read_bytes(), self.payload)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(len(self.downloads), 2)
        self.assertEqual(len(self.probes), 6)

    def test_lookup_checksum_and_probe_failures_preserve_cache_and_redact_diagnostics(self):
        self.target.parent.mkdir(parents=True)
        for failure, expected in (("metadata", "lookup failed"), ("checksum", "does not match its recorded SHA-256 digest"), ("probe", "exit status 19")):
            with self.subTest(failure=failure):
                self.failure = failure
                self.target.write_bytes(b"previous cached binary")
                self.probes.clear()
                status, stdout, stderr = self.invoke()
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertIn(expected, stderr)
                if failure in {"metadata", "probe"}:
                    self.assertIn("[REDACTED]", stderr)
                self.assertEqual(self.target.read_bytes(), b"previous cached binary")
                self.assertEqual(list(self.target.parent.iterdir()), [self.target])
                if failure in {"metadata", "checksum"}:
                    self.assertEqual(self.probes, [self.target])
        self.failure = None
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(self.target.read_bytes(), self.payload)


if __name__ == "__main__":
    unittest.main()
