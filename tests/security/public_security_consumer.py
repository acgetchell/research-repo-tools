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
