"""Read-only native discovery on each host, plus deterministic platform models."""

import contextlib
import io
import json
import os
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_repo_tools import tectonic
from research_repo_tools.cli import main
from research_repo_tools.process import ExecutableNotFoundError
from research_repo_tools.tectonic import discover_environment


class TestNativeDiscovery(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="native libraries café ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_native_host_discovery_export_and_unchanged_environment(self) -> None:
        env = dict(os.environ)
        if platform.system() == "Windows":
            (self.root / "installed" / "x64-windows-static-md").mkdir(parents=True)
            env.update(TECTONIC_DEP_BACKEND="vcpkg", VCPKG_ROOT=str(self.root))
            env.pop("VCPKGRS_TRIPLET", None)
        else:
            self.assertIsNotNone(shutil.which("pkg-config"), "native tests require provisioned pkg-config (pkgconf on macOS)")
            metadata = self.root / "lib/pkgconfig"
            metadata.mkdir(parents=True)
            for package in ("fontconfig", "freetype2", "graphite2", "icu-uc", "libpng", "openssl", "zlib"):
                (metadata / f"{package}.pc").write_text(
                    f"Name: {package}\nDescription: synthetic discovery fixture\nVersion: 1.0\nLibs:\nCflags:\n", encoding="utf-8", newline="\n"
                )
            env.update(TECTONIC_DEP_BACKEND="pkg-config", PKG_CONFIG_PATH=str(metadata), PKG_CONFIG_LIBDIR=str(metadata))
        snapshot = dict(env)
        assignments = discover_environment(environment=env)
        self.assertEqual(env, snapshot)
        self.assertIn("TECTONIC_DEP_BACKEND", assignments)
        command_file = self.root / "github environment"
        with patch.dict(os.environ, env, clear=True):
            process_snapshot = dict(os.environ)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["tectonic", "discover"]), 0)
            self.assertEqual(json.loads(out.getvalue()), assignments)
            self.assertEqual(main(["tectonic", "export", "--file", str(command_file)]), 0)
            self.assertEqual(dict(os.environ), process_snapshot)
        self.assertEqual(command_file.read_bytes(), "".join(f"{key}={value}\n" for key, value in sorted(assignments.items())).encode("utf-8"))

    def test_pkg_config_models_preserve_sysroot_and_report_all_missing(self) -> None:
        env = {"PATH": os.environ.get("PATH", ""), "PKG_CONFIG_PATH": "declared metadata", "PKG_CONFIG_SYSROOT_DIR": "declared root"}
        before = dict(env)
        calls = []

        def probe(command, args, **kwargs):
            calls.append((command, args, kwargs["env"]))
            return subprocess.CompletedProcess([command, *args], 1 if args[-1] in {"graphite2", "zlib"} else 0, "", "")

        with patch.object(tectonic, "run_command", side_effect=probe):
            with self.assertRaisesRegex(ValueError, "graphite2, zlib"):
                discover_environment(environment=env, system="Linux")
        self.assertEqual(
            [args for _, args, _ in calls], [["--exists", name] for name in ("freetype2", "graphite2", "icu-uc", "libpng", "zlib", "fontconfig", "openssl")]
        )
        self.assertTrue(all(environment["PKG_CONFIG_SYSROOT_DIR"] == "declared root" for _, _, environment in calls))
        self.assertEqual(env, before)

    def test_windows_triplet_models_and_host_prerequisite_failures(self) -> None:
        env = {"TECTONIC_DEP_BACKEND": "vcpkg", "VCPKG_ROOT": str(self.root)}
        with self.assertRaisesRegex(ValueError, "provisioned"):
            discover_environment(environment=env, system="Windows")
        (self.root / "installed" / "arm64-windows-static-md").mkdir(parents=True)
        env["VCPKGRS_TRIPLET"] = "arm64-windows-static-md"
        self.assertEqual(discover_environment(environment=env, system="Windows")["VCPKGRS_TRIPLET"], "arm64-windows-static-md")
        for triplet in ("../escape", "C:/escape", "", "x64\nwindows"):
            with self.subTest(triplet=triplet), self.assertRaisesRegex(ValueError, "triplet"):
                discover_environment(environment={**env, "VCPKGRS_TRIPLET": triplet}, system="Windows")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            discover_environment(environment={}, system="Unknown")
        with self.assertRaisesRegex(ValueError, "pkg-config"):
            discover_environment(environment={"TECTONIC_DEP_BACKEND": "vcpkg"}, system="Linux")
        with self.assertRaises(ExecutableNotFoundError):
            discover_environment(environment={"PATH": ""}, system="Linux")

    def test_macos_existing_prefix_and_sdk_shims_are_only_read(self) -> None:
        prefix = self.root / "brew prefix"
        (prefix / "lib/pkgconfig").mkdir(parents=True)
        (prefix / "Library/Homebrew/os/mac/pkgconfig/26").mkdir(parents=True)
        calls = []

        def probe(command, args, **kwargs):
            calls.append((command, args))
            output = str(prefix) + "\n" if command == "brew" else "26.0\n" if command == "xcrun" else ""
            return subprocess.CompletedProcess([command, *args], 0, output, "")

        with patch.object(tectonic, "run_command", side_effect=probe), patch.object(shutil, "which", return_value="available"):
            environment = discover_environment(environment={"PATH": "test"}, system="Darwin", prefixes=(prefix,))
        self.assertIn(str(prefix / "lib/pkgconfig"), environment["PKG_CONFIG_PATH"].split(os.pathsep))
        self.assertIn(str(prefix / "Library/Homebrew/os/mac/pkgconfig/26"), environment["PKG_CONFIG_PATH"].split(os.pathsep))
        self.assertEqual([command for command, _ in calls], ["brew", "xcrun"] + ["pkg-config"] * 5)
        self.assertFalse(any("install" in args for _, args in calls))


if __name__ == "__main__":
    unittest.main()
