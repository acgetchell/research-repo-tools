"""Pinned native Semgrep regressions, run from source and installed distributions."""

import collections
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest.mock import patch
from urllib.parse import unquote

from research_repo_tools import config, security, semgrep_scan

SEMGREP_REQUIREMENT = "semgrep==1.182.0"


class TestNativeSemgrepConsumer(unittest.TestCase):
    @classmethod
    @override
    def setUpClass(cls):
        uv = shutil.which("uv")
        if uv is None:
            raise AssertionError("native Semgrep tests require uv to provision the pinned scanner")
        result = subprocess.run(
            [uv, "tool", "run", "--python", "3.14", "--from", SEMGREP_REQUIREMENT, "python", "-c", "import shutil; print(shutil.which('semgrep'))"],
            capture_output=True,
            encoding="utf-8",
            timeout=300,
        )
        if result.returncode:
            raise AssertionError(f"could not provision {SEMGREP_REQUIREMENT}: {result.stderr}")
        cls.binary = Path(result.stdout.strip())
        if not cls.binary.is_file():
            raise AssertionError(f"missing native Semgrep executable: {cls.binary}")

    @override
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="native semgrep café ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"PATH": str(self.binary.parent) + os.pathsep + os.environ.get("PATH", "")}).start()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def settings(self, rules):
        self.write("rules.json", json.dumps({"rules": rules}))
        return config.parse({"semgrep": {"config": "rules.json", "batch-size": 2, "jobs": 1, "inline-suppressions": True}}, root=self.root)

    def test_logical_filters_and_independent_blocks_keep_native_prose_and_suppression(self):
        rust_scope = {"include": ["/src/**/*.rs", "/docs/**/*.md"]}
        rules = [
            {
                "id": "rust.println",
                "languages": ["rust"],
                "pattern": "println!(...)",
                "message": "print",
                "severity": "WARNING",
                "paths": {**rust_scope, "exclude": ["/src/excluded.rs"]},
            },
            {
                "id": "rust.coverage",
                "languages": ["rust"],
                "pattern": "unreachable_marker!(...)",
                "message": "coverage",
                "severity": "WARNING",
                "paths": rust_scope,
            },
            {
                "id": "prose.bad",
                "languages": ["generic"],
                "pattern-regex": "PROSE_BAD",
                "message": "prose",
                "severity": "WARNING",
                "paths": {"include": ["/docs/**/*.md"], "exclude": ["/docs/excluded.md"]},
            },
            {
                "id": "prose.coverage",
                "languages": ["generic"],
                "pattern-regex": "UNREACHABLE_PROSE",
                "message": "coverage",
                "severity": "WARNING",
                "paths": {"include": ["/docs/**/*.md"]},
            },
        ]
        settings = self.settings(rules)
        paths = [
            self.write(
                "src/lib.rs",
                'fn main() { println!("native"); }\r\n/// ```rust\r\n/// fn first() {}\r\n/// ```\r\n/// ```rust\r\n/// fn later() { let café = 1; println!("{café}"); }\r\n/// ```\r\n',
            ),
            self.write("src/excluded.rs", 'fn main() { println!("excluded"); }\n/// ```rust\n/// fn example() { println!("excluded"); }\n/// ```\n'),
            self.write(
                "docs/guide café 100%.md", 'PROSE_BAD\n```rust\n// PROSE_BAD\nfn example() { println!("suppressed"); } // nosemgrep: rust.println\n```\n'
            ),
            self.write("docs/prose.md", "PROSE_BAD\n"),
            self.write("docs/excluded.md", "PROSE_BAD\n"),
        ]
        originals = {path: path.read_bytes() for path in paths}
        # Only Git discovery is supplied by the fixture. The scanner, rule
        # filters, extraction, batching, reports and publication are real.
        with (
            patch.object(semgrep_scan, "security_inventory", return_value=tuple(path.relative_to(self.root).as_posix() for path in paths)),
            patch.object(security, "run_command_bytes", wraps=security.run_command_bytes) as native,
        ):
            self.assertEqual(semgrep_scan.scan(settings, include=("*.rs", "*.md"), rust_docs=True), 1)
        for call in native.call_args_list:
            if call.kwargs["cwd"] != self.root:
                self.assertFalse(call.kwargs["cwd"].exists())
        output = self.root / "target/security/semgrep"
        self.assertEqual({path.name for path in output.iterdir()}, {"semgrep.json", "semgrep.sarif"})
        data = json.loads((output / "semgrep.json").read_bytes())
        sarif = json.loads((output / "semgrep.sarif").read_bytes())
        expected = collections.Counter(
            {
                ("rust.println", "src/lib.rs", 1): 1,
                ("rust.println", "src/lib.rs", 6): 1,
                ("prose.bad", "docs/guide café 100%.md", 1): 1,
                ("prose.bad", "docs/guide café 100%.md", 3): 1,
                ("prose.bad", "docs/prose.md", 1): 1,
            }
        )
        actual = collections.Counter(
            (item["check_id"], Path(item["path"]).relative_to(self.root).as_posix(), item["start"]["line"])
            for item in data["results"]
            if not item.get("extra", {}).get("is_ignored", False)
        )
        self.assertEqual(actual, expected)
        for item in data["results"]:
            if item["check_id"] == "rust.println":
                self.assertTrue(originals[Path(item["path"])][item["start"]["offset"] : item["end"]["offset"]].startswith(b"println!"))
        self.assertEqual(len(data["batches"]), 6)
        self.assertEqual(len(data["input_coverage"]), 9)
        blocks = {
            (item["path"], item["block"], item["start_line"], item["end_line"]) for item in data["input_coverage"] if item["representation"] == "rust-block"
        }
        self.assertEqual(blocks, {("src/lib.rs", 1, 3, 3), ("src/lib.rs", 2, 6, 6), ("src/excluded.rs", 1, 3, 3), ("docs/guide café 100%.md", 1, 3, 4)})
        self.assertEqual(len(sarif["runs"]), 1)
        run = sarif["runs"][0]
        self.assertEqual(run["automationDetails"]["id"], "semgrep/")
        sarif_actual = collections.Counter()
        suppressed = 0
        for finding in run["results"]:
            rule = finding["ruleId"]
            self.assertIn(rule, {descriptor["id"] for descriptor in run["tool"]["driver"]["rules"]})
            if "ruleIndex" in finding:
                self.assertEqual(run["tool"]["driver"]["rules"][finding["ruleIndex"]]["id"], rule)
            if finding.get("suppressions"):
                suppressed += 1
                continue
            location = finding["locations"][0]["physicalLocation"]
            path = Path(unquote(location["artifactLocation"]["uri"]))
            sarif_actual[rule, path.relative_to(self.root).as_posix(), location["region"]["startLine"]] += 1
        self.assertEqual(sarif_actual, expected)
        self.assertEqual(suppressed, 1)
        self.assertEqual({path: path.read_bytes() for path in paths}, originals)
        self.assertNotIn("research-semgrep-scan-", json.dumps(data))
        self.assertNotIn("research-semgrep-scan-", json.dumps(sarif))

    def test_prose_only_markdown_still_runs_without_rust_rules_or_fences(self):
        source = self.write("docs/guide.md", "PROSE_BAD\n")
        settings = self.settings(
            [
                {
                    "id": "prose.bad",
                    "languages": ["generic"],
                    "pattern-regex": "PROSE_BAD",
                    "message": "prose",
                    "severity": "WARNING",
                    "paths": {"include": ["/docs/**/*.md"]},
                }
            ]
        )
        with patch.object(semgrep_scan, "security_inventory", return_value=("docs/guide.md",)):
            self.assertEqual(semgrep_scan.scan(settings, include=("*.md",), rust_docs=True), 1)
        report = json.loads((self.root / "target/security/semgrep/semgrep.json").read_bytes())
        self.assertEqual([(item["check_id"], Path(item["path"]), item["start"]["line"]) for item in report["results"]], [("prose.bad", source, 1)])

    def test_overlapping_regex_findings_keep_distinct_spans_and_source_byte_positions(self):
        rules = [{"id": "rust.regex", "languages": ["rust"], "pattern-regex": "println!", "message": "print", "severity": "WARNING"}]
        settings = self.settings(rules)
        source = self.write("src/lib.rs", '//! café\r\n//! ```rust\r\n//! # println!("one"); println!("two");\r\n//! ```\r\n')
        original = source.read_bytes()
        with patch.object(semgrep_scan, "security_inventory", return_value=("src/lib.rs",)):
            self.assertEqual(semgrep_scan.scan(settings, include=("*.rs",), rust_docs=True), 1)
        output = self.root / "target/security/semgrep"
        data = json.loads((output / "semgrep.json").read_bytes())
        sarif = json.loads((output / "semgrep.sarif").read_bytes())
        expected = [(3, 7, 15), (3, 24, 32)]
        self.assertEqual([(item["start"]["line"], item["start"]["col"], item["end"]["col"]) for item in data["results"]], expected)
        self.assertEqual([len(batch["results"]) for batch in data["batches"]], [2, 0])
        self.assertEqual(len(data["input_coverage"]), 2)
        for finding in data["results"]:
            self.assertEqual(original[finding["start"]["offset"] : finding["end"]["offset"]], b"println!")
        regions = [item["locations"][0]["physicalLocation"]["region"] for item in sarif["runs"][0]["results"]]
        self.assertEqual([(item["startLine"], item["startColumn"], item["endColumn"]) for item in regions], expected)
        self.assertEqual(source.read_bytes(), original)

    def test_native_filtered_block_fails_with_its_identity_and_removes_stale_reports(self):
        settings = self.settings(
            [
                {
                    "id": "rust.print",
                    "languages": ["rust"],
                    "pattern": "println!(...)",
                    "message": "print",
                    "severity": "WARNING",
                    "paths": {"include": ["/src/**/*.rs"]},
                },
                {"id": "prose.coverage", "languages": ["generic"], "pattern-regex": "UNREACHABLE", "message": "coverage", "severity": "WARNING"},
            ]
        )
        source = self.write("docs/guide.md", '```rust\nfn example() { println!("missed"); }\n```\n')
        original = source.read_bytes()
        stale = self.write("target/security/semgrep/old.sarif", "stale")
        error = io.StringIO()
        with patch.object(semgrep_scan, "security_inventory", return_value=("docs/guide.md",)), contextlib.redirect_stderr(error):
            self.assertEqual(semgrep_scan.scan(settings, include=("*.md",), rust_docs=True), 1)
        self.assertIn("missing: 'docs/guide.md (Rust block 1, lines 2-2)'", error.getvalue())
        self.assertNotIn("research-semgrep-scan-", error.getvalue())
        self.assertEqual(list(stale.parent.iterdir()), [])
        self.assertEqual(source.read_bytes(), original)

    def test_regex_anchors_scan_code_without_artificial_padding_and_restore_source_positions(self):
        settings = self.settings(
            [
                {"id": "rust.anchored", "languages": ["rust"], "pattern-regex": r"(?m)^\s*println!", "message": "print", "severity": "WARNING"},
                {"id": "rust.padding", "languages": ["rust"], "pattern-regex": r"\A\n", "message": "padding", "severity": "WARNING"},
                {"id": "rust.argument", "languages": ["rust"], "pattern": "println!($MESSAGE)", "message": "print", "severity": "WARNING"},
            ]
        )
        source = self.write("src/lib.rs", '//! Example\r\n//! ```rust\r\n//! println!("example");\r\n//! ```\r\n')
        original = source.read_bytes()
        with patch.object(semgrep_scan, "security_inventory", return_value=("src/lib.rs",)):
            self.assertEqual(semgrep_scan.scan(settings, include=("*.rs",), rust_docs=True), 1)
        output = self.root / "target/security/semgrep"
        data = json.loads((output / "semgrep.json").read_bytes())
        results = {item["check_id"]: item for item in data["results"]}
        self.assertEqual(set(results), {"rust.anchored", "rust.argument"})
        for finding in results.values():
            self.assertEqual(finding["start"], {"line": 3, "col": 5, "offset": original.index(b"println!")})
        sarif = json.loads((output / "semgrep.sarif").read_bytes())
        self.assertEqual({item["ruleId"] for item in sarif["runs"][0]["results"]}, set(results))
        for item in sarif["runs"][0]["results"]:
            region = item["locations"][0]["physicalLocation"]["region"]
            self.assertEqual((region["startLine"], region["startColumn"]), (3, 5))
        self.assertEqual(source.read_bytes(), original)

    def test_mixed_fixture_annotations_belong_to_native_prose_or_one_rust_block(self):
        rules = [
            {
                "id": "rust.println",
                "languages": ["rust"],
                "pattern": "println!(...)",
                "message": "print",
                "severity": "WARNING",
                "paths": {"exclude": ["fixtures/**"]},
            },
            {"id": "prose.bad", "languages": ["generic"], "pattern-regex": "PROSE_BAD", "message": "prose", "severity": "WARNING"},
            {"id": "python.print", "languages": ["python"], "pattern": "print(...)", "message": "print", "severity": "WARNING"},
            {"id": "rust.regex", "languages": ["rust"], "pattern-regex": "println!", "message": "print", "severity": "WARNING"},
        ]
        self.settings(rules)
        settings = config.parse({"semgrep": {"config": "rules.json", "fixtures": "fixtures"}}, root=self.root)
        paths = [
            self.write(
                "fixtures/example.rs",
                '// ruleid: rust.println, rust.regex\nfn main() { println!("native"); }\n/// // ruleid: rust.println, rust.regex\n/// ```rust\n/// fn example() { println!("doc"); }\n/// ```\n',
            ),
            self.write(
                "fixtures/guide.md",
                '<!-- ruleid: prose.bad -->\nPROSE_BAD\n<!-- ruleid: rust.println, rust.regex -->\n```rust\nfn example() { println!("doc"); }\n```\n```rust\n// ok: rust.println, rust.regex\nfn clean() {}\n```\n',
            ),
            self.write("fixtures/prose-only.md", "<!-- ruleid: prose.bad -->\nPROSE_BAD\n<!-- ok: prose.bad -->\nclean\n"),
            self.write("fixtures/native.py", '# ruleid: python.print\nprint("native")\n# ok: python.print\npass\n'),
        ]
        originals = {path: path.read_bytes() for path in paths}
        self.assertEqual(semgrep_scan.check_documentation_fixtures(settings), 0)
        self.assertEqual({path: path.read_bytes() for path in paths}, originals)
        guide = self.root / "fixtures/guide.md"
        guide.write_bytes(originals[guide].replace(b"fn clean() {}", b'fn clean() { println!("unexpected"); }'))
        with self.assertRaisesRegex(ValueError, r"guide.md \(Rust block 2.*rust.println.*unexpected finding"):
            semgrep_scan.check_documentation_fixtures(settings)


if __name__ == "__main__":
    unittest.main()
