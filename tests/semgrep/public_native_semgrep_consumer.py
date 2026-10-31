"""Pinned native Semgrep regressions, run from source and installed distributions."""

import collections
import contextlib
import io
import json
import os
import sys
import sysconfig
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import override
from unittest.mock import patch
from urllib.parse import unquote

from research_repo_tools import config, security, semgrep_scan, semgrep_tool
from research_repo_tools.process import run_command_bytes


class TestNativeSemgrepConsumer(unittest.TestCase):
    @classmethod
    @override
    def setUpClass(cls):
        semgrep_tool.setup(Path.cwd())

    @override
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="native semgrep café ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(patch.stopall)

    def test_shared_scanner_runs_offline_and_ignores_ambient_scanner_and_project_redirects(self):
        self.assertEqual(sys.version_info[:2], (3, 15))
        self.assertEqual(semgrep_tool.setup(self.root).parent, Path(sysconfig.get_path("scripts")).resolve())
        ambient = self.root / "ambient tools"
        ambient.mkdir()
        fake = ambient / ("semgrep.cmd" if os.name == "nt" else "semgrep")
        fake.write_bytes(b"@echo ambient scanner invoked\r\n@exit /b 92\r\n" if os.name == "nt" else b"#!/bin/sh\necho ambient scanner invoked\nexit 92\n")
        fake.chmod(0o755)
        self.write("uv.toml", "invalid = [\n")
        self.write(".python-version", "0.0.0\n")
        source = self.write("example.py", 'print("native")\n')
        settings = self.settings([{"id": "python.print", "languages": ["python"], "pattern": "print(...)", "message": "print", "severity": "WARNING"}])
        with patch.dict(
            os.environ,
            {
                "PATH": str(ambient) + os.pathsep + os.environ.get("PATH", ""),
                "UV_OFFLINE": "1",
                "UV_CONFIG_FILE": str(self.root / "missing.toml"),
                "UV_PROJECT": str(self.root / "missing-project"),
                "UV_PYTHON": "0.0.0",
                "UV_OVERRIDE": str(self.root / "missing-overrides.txt"),
                "VIRTUAL_ENV": str(self.root / "missing-environment"),
                "PYTHONPATH": str(self.root),
            },
        ):
            # uv's disposable tool environments can change paths, including on
            # Windows. Offline native execution proves warm-cache reuse without
            # depending on the cache's internal environment identity.
            with patch.object(semgrep_scan, "security_inventory", return_value=("example.py",)):
                self.assertEqual(semgrep_scan.scan(settings, include=("*.py",)), 1)
        report = json.loads((self.root / "target/security/semgrep/semgrep.json").read_bytes())
        self.assertEqual([(item["check_id"], Path(item["path"]), item["start"]["line"]) for item in report["results"]], [("python.print", source, 1)])
        self.assertFalse((self.root / "uv.lock").exists())
        self.assertFalse((self.root / ".venv").exists())

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def settings(self, rules):
        self.write("rules.json", json.dumps({"rules": rules}))
        return config.parse({"semgrep": {"config": "rules.json", "batch-size": 2, "jobs": 1, "inline-suppressions": True}}, root=self.root)

    def test_native_engine_reports_a_finding_with_the_shared_dependencies(self):
        """Expose engine startup failures without logging real consumer inputs."""
        binary = semgrep_tool.setup(self.root)
        self.settings([{"id": "native.probe", "languages": ["rust"], "pattern": "println!(...)", "message": "native probe", "severity": "WARNING"}])
        source = self.write("probe.rs", 'fn main() { println!("native probe"); }\n')
        report = self.root / "report.json"
        result = run_command_bytes(
            binary,
            [
                "scan",
                "--config",
                str(self.root / "rules.json"),
                "--metrics",
                "off",
                "--disable-version-check",
                "--strict",
                "--no-rewrite-rule-ids",
                "--jobs",
                "1",
                "--error",
                "--json",
                "--output",
                str(report),
                str(source),
            ],
            cwd=self.root,
            env={**semgrep_tool._environment(), "SEMGREP_SETTINGS_FILE": str(self.root / "settings.yml"), "SEMGREP_LOG_FILE": str(self.root / "scanner.log")},
            timeout=60,
            check=False,
        )
        payload = report.read_bytes() if report.is_file() else result.stdout
        diagnostic = (payload + b"\n" + result.stderr).decode("utf-8", errors="replace")[:12000]
        self.assertEqual(result.returncode, 1, diagnostic)
        data = json.loads(payload)
        self.assertEqual(data["errors"], [], diagnostic)
        self.assertEqual([(item["check_id"], Path(item["path"]).resolve(), item["start"]["line"]) for item in data["results"]], [("native.probe", source, 1)])

    def scoped_rules(self):
        def rule(name, pattern, representations, **extra):
            return {
                "id": name,
                "languages": ["rust"],
                "message": name,
                "severity": "WARNING",
                "pattern": pattern,
                "metadata": {"research-repo-tools": {"representations": representations, **extra}},
            }

        return [
            rule("production.print", "println!(...)", ["source"]),
            {
                **rule("example.panic", "panic!(...)", ["rust-block"], **{"rust-block-paths": {"include": ["/src/**/*.rs", "/docs/**/*.md"]}}),
                "paths": {"include": ["/src/**/*.rs"]},
            },
            {"id": "prose.bad", "languages": ["generic"], "pattern-regex": "PROSE_BAD", "message": "prose", "severity": "WARNING"},
        ]

    def test_representation_rules_and_explicit_extraction_scope_preserve_original_prose(self):
        settings = self.settings(self.scoped_rules())
        settings = replace(settings, semgrep=replace(settings.semgrep, rustdoc_include=("src/**", "docs/**"), rustdoc_exclude=("docs/archive/**",)))
        sources = [
            self.write(
                "src/lib.rs",
                'fn main() { println!("production"); }\r\n/// ```rust\r\n/// println!("allowed example");\r\n/// ```\r\n/// ```rust\r\n/// # panic!("later");\r\n/// ```\r\n',
            ),
            self.write("docs/guide café 100%.md", '```rust\nprintln!("allowed");\n```\n```rust\npanic!("later");\n```\n'),
            self.write("docs/archive/sketch.md", "PROSE_BAD\n```rust\nnot valid Rust ...\n"),
        ]
        original = {path: path.read_bytes() for path in sources}
        with patch.object(semgrep_scan, "security_inventory", return_value=tuple(path.relative_to(self.root).as_posix() for path in sources)):
            self.assertEqual(semgrep_scan.scan(settings, include=("*.rs", "*.md"), rust_docs=True, batch_size=1), 1)
        output = self.root / "target/security/semgrep"
        semgrep_scan.require_current_reports(output)
        data = json.loads((output / "semgrep.json").read_bytes())
        self.assertEqual(
            collections.Counter((item["check_id"], Path(item["path"]).relative_to(self.root).as_posix(), item["start"]["line"]) for item in data["results"]),
            collections.Counter(
                {
                    ("production.print", "src/lib.rs", 1): 1,
                    ("example.panic", "src/lib.rs", 6): 1,
                    ("example.panic", "docs/guide café 100%.md", 5): 1,
                    ("prose.bad", "docs/archive/sketch.md", 1): 1,
                }
            ),
        )
        self.assertEqual(len(data["batches"]), 7)
        self.assertEqual(
            [(item["path"], item["block"]) for item in data["input_coverage"] if item["representation"] == "rust-block"],
            [("src/lib.rs", 1), ("docs/guide café 100%.md", 1), ("src/lib.rs", 2), ("docs/guide café 100%.md", 2)],
        )
        self.assertEqual(
            data["extraction_scope"],
            {
                "enabled": True,
                "include": ["src/**", "docs/**"],
                "exclude": ["docs/archive/**"],
                "selected_paths": ["src/lib.rs", "docs/guide café 100%.md"],
                "excluded_paths": ["docs/archive/sketch.md"],
            },
        )
        sarif = json.loads((output / "semgrep.sarif").read_bytes())
        self.assertEqual(len(sarif["runs"]), 1)
        self.assertEqual(len(sarif["runs"][0]["results"]), 4)
        for item in sarif["runs"][0]["results"]:
            if "ruleIndex" in item:
                self.assertEqual(sarif["runs"][0]["tool"]["driver"]["rules"][item["ruleIndex"]]["id"], item["ruleId"])
        self.assertEqual({path: path.read_bytes() for path in sources}, original)

    def test_source_comment_and_extracted_fixture_ownership_share_representation_policy(self):
        rules = self.scoped_rules()
        rules.append(
            {
                "id": "comment.import",
                "languages": ["rust"],
                "pattern-regex": r"(?m)^/// use forbidden::",
                "message": "raw comment",
                "severity": "WARNING",
                "metadata": {"research-repo-tools": {"representations": ["source"]}},
            }
        )
        settings = self.settings(rules)
        settings = replace(settings, semgrep=replace(settings.semgrep, fixtures="fixtures"))
        path = self.write(
            "fixtures/mixed.rs",
            '/// ```rust\n/// // ruleid: comment.import\n/// use forbidden::Thing;\n/// // ruleid: example.panic\n/// panic!("example");\n/// // ok: example.panic\n/// println!("allowed");\n/// ```\n// ruleid: production.print\nfn main() { println!("native"); }\n',
        )
        prose = self.write(
            "fixtures/guide.md",
            '<!-- ruleid: prose.bad -->\nPROSE_BAD\n```rust\n// ruleid: example.panic\npanic!("later");\n// ok: example.panic\nprintln!("allowed");\n```\n',
        )
        original = {item: item.read_bytes() for item in (path, prose)}
        self.assertEqual(semgrep_scan.check_documentation_fixtures(settings), 0)
        self.assertEqual({item: item.read_bytes() for item in (path, prose)}, original)
        path.write_bytes(original[path].replace(b'println!("allowed");', b'panic!("unannotated");'))
        with self.assertRaisesRegex(ValueError, "unexpected finding"):
            semgrep_scan.check_documentation_fixtures(settings)

    def test_retention_after_real_later_block_parse_error_preserves_pair_but_invalidates_upload(self):
        rules = self.scoped_rules()
        # Force native parsing even when the malformed block contains no tokens
        # from the finding rules. Semgrep's token prefilter otherwise omits it.
        rules.append(
            {
                "id": "rust.parse",
                "languages": ["rust"],
                "patterns": [{"pattern": "$X"}, {"metavariable-regex": {"metavariable": "$X", "regex": "NEVER_MATCH_PARSE_PROBE"}}],
                "message": "parse",
                "severity": "WARNING",
            }
        )
        settings = self.settings(rules)
        source = self.write("src/lib.rs", 'fn main() {}\n/// ```rust\n/// println!("allowed");\n/// ```\n/// ```rust\n/// fn later() {}\n/// ```\n')
        output = self.root / "target/security/semgrep"
        with patch.object(semgrep_scan, "security_inventory", return_value=("src/lib.rs",)):
            self.assertEqual(semgrep_scan.scan(settings, include=("*.rs",), rust_docs=True, failure_reports="retain"), 0)
            previous = {path: path.read_bytes() for path in semgrep_scan.require_current_reports(output)}
            source.write_bytes(source.read_bytes().replace(b"fn later() {}", b"fn later( {"))
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertNotEqual(semgrep_scan.scan(settings, include=("*.rs",), rust_docs=True, failure_reports="retain"), 0)
        self.assertIn("Rust block 2", error.getvalue())
        self.assertIn("native Semgrep errors", error.getvalue())
        self.assertEqual({path: path.read_bytes() for path in output.iterdir()}, previous)
        with self.assertRaisesRegex(ValueError, "not current"):
            semgrep_scan.require_current_reports(output)

    def test_shared_dependency_overrides_support_native_fixture_autofix_validation(self):
        from research_repo_tools import semgrep

        settings = self.settings(
            [{"id": "python.print", "languages": ["python"], "pattern": "print(...)", "fix": "pass", "message": "print", "severity": "WARNING"}]
        )
        settings = replace(settings, semgrep=replace(settings.semgrep, fixtures="fixtures"))
        path = self.write("fixtures/print.py", '# ruleid: python.print\nprint("example")\n')
        fixed = self.write("fixtures/print.fixed.py", "# ruleid: python.print\npass\n")
        original = {item: item.read_bytes() for item in (path, fixed)}
        self.assertEqual(semgrep.check(settings), 0)
        self.assertEqual({item: item.read_bytes() for item in (path, fixed)}, original)

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

    def test_unicode_and_control_separators_do_not_shift_native_source_lines(self):
        settings = self.settings(
            [
                {"id": "rust.print", "languages": ["rust"], "pattern": "println!(...)", "message": "print", "severity": "WARNING"},
                {"id": "prose.coverage", "languages": ["generic"], "pattern-regex": "UNREACHABLE", "message": "coverage", "severity": "WARNING"},
            ]
        )
        source = self.write("docs/guide.md", 'before\v\f\x1c\x1d\x1e\x85\u2028\u2029\rafter\r\n```rust\r\nprintln!("example");\r\n```\r\n')
        original = source.read_bytes()
        with patch.object(semgrep_scan, "security_inventory", return_value=("docs/guide.md",)):
            self.assertEqual(semgrep_scan.scan(settings, include=("*.md",), rust_docs=True), 1)
        output = self.root / "target/security/semgrep"
        data = json.loads((output / "semgrep.json").read_bytes())
        self.assertEqual(len(data["results"]), 1)
        finding = data["results"][0]
        self.assertEqual(finding["start"], {"line": 3, "col": 1, "offset": original.index(b"println!")})
        self.assertTrue(original[finding["start"]["offset"] : finding["end"]["offset"]].startswith(b"println!"))
        sarif = json.loads((output / "semgrep.sarif").read_bytes())
        self.assertEqual(len(sarif["runs"][0]["results"]), 1)
        region = sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]
        self.assertEqual((region["startLine"], region["startColumn"]), (3, 1))
        self.assertEqual(source.read_bytes(), original)

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

    def test_rustdoc_only_annotations_still_reject_unannotated_native_matches(self):
        rules = [
            {"id": "rust.println", "languages": ["rust"], "pattern": "println!(...)", "message": "print", "severity": "WARNING"},
            {"id": "rust.regex", "languages": ["rust"], "pattern-regex": "println!", "message": "print", "severity": "WARNING"},
        ]
        self.settings(rules)
        settings = config.parse({"semgrep": {"config": "rules.json", "fixtures": "fixtures"}}, root=self.root)
        for native in ("", "// ok: rust.regex\nfn clean() {}\n"):
            with self.subTest(native=native):
                source = self.write(
                    "fixtures/example.rs",
                    native + '//! ```rust\n//! // ruleid: rust.println, rust.regex\n//! println!("doc");\n//! ```\nfn main() {}\n',
                )
                original = source.read_bytes()
                self.assertEqual(semgrep_scan.check_documentation_fixtures(settings), 0)
                self.assertEqual(source.read_bytes(), original)
                changed = original.replace(b"fn main() {}", b'fn main() { println!("unexpected"); }')
                source.write_bytes(changed)
                with self.assertRaisesRegex(ValueError, r"example.rs: .*rust.println.*unexpected finding.*rust.regex.*unexpected finding"):
                    semgrep_scan.check_documentation_fixtures(settings)
                self.assertEqual(source.read_bytes(), changed)

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
