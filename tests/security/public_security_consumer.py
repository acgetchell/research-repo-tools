"""Public capability contracts, repeated from isolated wheel/sdist environments."""

import importlib.metadata
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_repo_tools import config, python_baseline, security, semgrep_scan
from research_repo_tools.semgrep_docs import rust_blocks
from research_repo_tools.toolchain_config import load


class TestSharedCapabilities(unittest.TestCase):
    def test_numbered_reports_are_removed_before_scanning(self):
        self._assert_numbered_reports_removed(linked=False)

    def test_numbered_report_symlinks_are_removed_without_following_targets(self):
        self._assert_numbered_reports_removed(linked=True)

    def _assert_numbered_reports_removed(self, *, linked):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "uv.lock").write_bytes(b"version = 1\n")
            (root / "source.py").write_bytes(b"value = 1\n")
            external = root / "external.json"
            external.write_bytes(b"external report\n")
            settings = config.parse({"semgrep": {"config": "rules.yml"}}, root=root)
            for scanner, prefix in (("osv", "osv-"), ("semgrep", "")):
                with self.subTest(scanner=scanner):
                    output = root / scanner
                    output.mkdir()
                    stale = [output / f"{prefix}{index}.{fmt}" for index in (0, 1, 12) for fmt in ("json", "sarif")]
                    preserved = [output / name for name in ("notes.json", "gitleaks-history.sarif", f"{prefix}1.json.bak", f"{prefix}latest.json")]
                    preserved.append(output / ("0.json" if prefix else "osv-0.json"))
                    for index, path in enumerate(stale):
                        if linked:
                            try:
                                path.symlink_to(external if index % 2 else root / "absent.json")
                            except OSError as error:
                                if getattr(error, "winerror", None) == 1314:
                                    self.skipTest("native symlink fixtures require Windows symlink privileges")
                                raise
                            self.assertTrue(path.is_symlink())
                            self.assertEqual(path.exists(), bool(index % 2))
                        else:
                            path.write_bytes(b"previous report\n")
                    for path in preserved:
                        path.write_bytes(b"previous report\n")
                    nested = output / "nested"
                    nested.mkdir()
                    (nested / f"{prefix}12.json").write_bytes(b"nested report\n")

                    def native(_binary, _args, **_kwargs):
                        self.assertTrue(all(not path.exists() and not path.is_symlink() for path in stale))
                        self.assertTrue(all(path.read_bytes() == b"previous report\n" for path in preserved))
                        # A failed native scan must not leave reports from a larger inventory.
                        return subprocess.CompletedProcess([], 19, b"", b"")

                    with (
                        patch.object(security, "security_inventory", return_value=("uv.lock",)),
                        patch.object(security, "_binary", return_value=(root / "osv-scanner", {})),
                        patch.object(semgrep_scan, "security_inventory", return_value=("source.py",)),
                        patch.object(semgrep_scan, "resolve_executable", return_value=root / "semgrep"),
                        patch.object(security, "run_command_bytes", side_effect=native) as run,
                    ):
                        status = (
                            security.scan_osv(settings, ("uv.lock",), output=scanner)
                            if prefix
                            else semgrep_scan.scan(settings, include=("*.py",), output=scanner)
                        )
                    self.assertEqual(status, 19)
                    self.assertEqual(run.call_count, 2)
                    self.assertEqual((nested / f"{prefix}12.json").read_bytes(), b"nested report\n")
                    self.assertEqual(external.read_bytes(), b"external report\n")

    def test_installed_baseline_authority_and_nonmutating_drift(self):
        authority = python_baseline.baseline()
        self.assertEqual(authority.requirement, importlib.metadata.metadata("research-repo-tools")["Requires-Python"])
        self.assertEqual(authority.package_version, importlib.metadata.version("research-repo-tools"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = (
                f'[project]\nrequires-python={json.dumps(authority.requirement)}\n[tool.uv]\npackage=false\nrequired-version="==0.12.19"\n'
                f'[dependency-groups]\ntooling=["research-repo-tools=={authority.package_version}"]\n'
                "[tool.research-repo-tools.toolchain]\ninherit-python=true\n"
                '[tool.research-repo-tools.toolchain.binaries]\ngitleaks="8.30.1"\nosv-scanner="2.6.0"\n'
            )
            (root / "pyproject.toml").write_text(manifest, encoding="utf-8", newline="\n")
            (root / ".python-version").write_bytes(authority.selected.encode() + b"\r\n")
            self.assertEqual(python_baseline.drift(root), ())
            tools = load(config.load(root=root))
            self.assertEqual([tool.name for tool in tools.binaries], ["gitleaks", "osv-scanner"])
            (root / ".python-version").write_bytes(b"3.13\r\n")
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            with self.assertRaisesRegex(ValueError, "shared Python drift"):
                load(config.load(root=root))
            self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})

    def test_lockfile_coverage_and_native_status(self):
        with tempfile.TemporaryDirectory(prefix="consumer with spaces ") as directory:
            root = Path(directory).resolve()
            (root / "uv.lock").write_bytes(b"version = 1\n")
            args_seen = []

            def native(binary, args, **kwargs):
                args_seen.append(args)
                self.assertEqual(args[args.index("--lockfile") + 1], ":" + str(root / "uv.lock"))
                value = (
                    {"results": [{"source": {"path": str(root / "uv.lock")}, "packages": [{"package": {"name": "example"}}]}]}
                    if args[args.index("--format") + 1] == "json"
                    else {"version": "2.1.0", "runs": [{"tool": {}, "results": []}]}
                )
                Path(args[args.index("--output-file") + 1]).write_bytes(json.dumps(value).encode("utf-8"))
                return subprocess.CompletedProcess([], 19, b"", b"")

            with (
                patch.object(security, "security_inventory", return_value=("uv.lock",)),
                patch.object(security, "_binary", return_value=(Path("osv-scanner"), {})),
                patch.object(security, "run_command_bytes", side_effect=native),
            ):
                self.assertEqual(security.scan_osv(config.parse({}, root=root), ("uv.lock",)), 19)
            self.assertEqual(len(args_seen), 2)
            self.assertEqual(len(list((root / "target/security").iterdir())), 2)

    def test_documentation_bytes_and_original_line_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "docs.rs"
            original = b"//! Documentation\r\n//! ```rust\r\n//! # let value = 1;\r\n//! assert_eq!(value, 1);\r\n//! ```\r\n"
            path.write_bytes(original)
            self.assertEqual(rust_blocks(path), ("\n\nlet value = 1;\nassert_eq!(value, 1);\n",))
            self.assertEqual(path.read_bytes(), original)

    def test_malformed_scanner_reports_fail_without_publishing(self):
        with tempfile.TemporaryDirectory(prefix="scanner consumer ") as directory:
            root = Path(directory).resolve()
            (root / "uv.lock").write_bytes(b"version = 1\n")
            (root / "source.py").write_bytes(b"value = 1\n")
            settings = config.parse({"semgrep": {"config": "rules.yml"}}, root=root)

            def native(_binary, args, **_kwargs):
                if "--output-file" in args:
                    output = args[args.index("--output-file") + 1]
                    value = (
                        {"results": [{"source": {"path": str(root / "uv.lock")}, "packages": [{"package": {"name": "example"}}]}]}
                        if args[args.index("--format") + 1] == "json"
                        else {"version": "2.1.0", "runs": [{"tool": {}, "results": [], "invocations": [{"executionSuccessful": "false"}]}]}
                    )
                else:
                    output = args[args.index("--output") + 1]
                    value = {"results": [], "errors": [], "paths": []} if "--json" in args else {"version": "2.1.0", "runs": [{"tool": {}, "results": []}]}
                Path(output).write_bytes(json.dumps(value).encode())
                return subprocess.CompletedProcess([], 0, b"", b"")

            with (
                patch.object(security, "security_inventory", return_value=("uv.lock",)),
                patch.object(security, "_binary", return_value=(root / "osv-scanner", {})),
                patch.object(semgrep_scan, "security_inventory", return_value=("source.py",)),
                patch.object(semgrep_scan, "resolve_executable", return_value=root / "semgrep"),
                patch.object(security, "run_command_bytes", side_effect=native),
            ):
                self.assertEqual(security.scan_osv(settings, ("uv.lock",)), 1)
                self.assertFalse((root / "target/security/osv-0.sarif").exists())
                self.assertEqual(semgrep_scan.scan(settings, include=("*.py",)), 1)
                self.assertFalse((root / "target/security/semgrep/0.json").exists())


if __name__ == "__main__":
    unittest.main()
