"""Batched Semgrep behavior through the installed public API and CLI."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote, unquote

from research_repo_tools import config, security, semgrep_scan
from research_repo_tools.cli import main
from research_repo_tools.selection import argument_batches


class TestScanConsumer(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="scan café ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.names = ("src/one.rs", "tests/two.rs", "scripts/tests/three.py")
        for name in self.names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"bad()\r\n")
        self.settings = config.parse(
            {
                "semgrep": {
                    "config": "rules.yml",
                    "inline-suppressions": True,
                    "batch-size": 2,
                    "jobs": 1,
                    "target-timeout": 120,
                    "report-category": "repository-rules",
                }
            },
            root=self.root,
        )
        self.calls = []
        self.behavior = "findings"
        self.invocation_index = 0
        self.addCleanup(patch.stopall)
        self.inventory = patch.object(semgrep_scan, "security_inventory", return_value=self.names).start()
        patch.object(semgrep_scan, "resolve_executable", return_value=self.root / "semgrep").start()
        patch.object(security, "run_command_bytes", side_effect=self.native).start()

    def native(self, binary, args, **kwargs):
        self.calls.append(args)
        paths = args[args.index("--sarif-output") + 2 :]
        self.assertEqual(kwargs["cwd"], self.root)
        self.assertIn("--strict", args)
        self.assertIn("--error", args)
        self.assertIn("--no-rewrite-rule-ids", args)
        self.assertEqual(args[args.index("--jobs") + 1], "1")
        self.assertEqual(args[args.index("--timeout") + 1], "120")
        if self.behavior == "timeout":
            raise subprocess.TimeoutExpired("scanner", 300, output=b"PRIVATE_MATCH")
        json_findings = []
        sarif_findings = []
        for path in paths:
            suppressed = self.behavior == "suppressed"
            line = 1
            uri = Path(path).as_posix()
            if self.behavior == "documentation":
                line = next(index for index, text in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1) if text == "bad()")
                if len(self.calls) == 2:
                    uri = quote(uri, safe="/")
            if self.behavior != "clean":
                json_findings.append(
                    {"check_id": "project.rule", "path": path, "start": {"line": line}, "end": {"line": line}, "extra": {"is_ignored": suppressed}}
                )
                sarif_findings.append(
                    {
                        "ruleId": "project.rule",
                        "ruleIndex": 1,
                        "message": {"text": "PRIVATE_MATCH"},
                        "locations": [{"physicalLocation": {"artifactLocation": {"uri": uri}, "region": {"startLine": line}}}],
                        **({"suppressions": [{"kind": "inSource"}]} if suppressed else {}),
                    }
                )
        data = {"results": json_findings, "errors": [], "paths": {"scanned": paths}, "native_metadata": {"kept": True}}
        sarif = json.loads(
            json.dumps(
                {
                    "version": "2.1.0",
                    "runs": [
                        {
                            "tool": {"driver": {"name": "Semgrep", "rules": [{"id": "project.other"}, {"id": "project.rule"}]}},
                            "results": sarif_findings,
                            "invocations": [{"executionSuccessful": True}],
                        }
                    ],
                }
            )
        )
        if self.behavior == "tables":
            run = sarif["runs"][0]
            rules = run["tool"]["driver"]["rules"]
            rule_index = 1 if len(self.calls) == 1 else 0
            if len(self.calls) > 1:
                rules.reverse()
            rules[rule_index]["relationships"] = [{"target": {"id": "project.rule", "index": rule_index}}]
            run["artifacts"] = [{"location": {"uri": Path(path).as_posix(), "index": index}} for index, path in enumerate(paths)]
            invocation = run["invocations"][0]
            invocation["ruleConfigurationOverrides"] = [{"descriptor": {"id": "project.rule", "index": rule_index}, "configuration": {"level": "warning"}}]
            for field in ("executableLocation", "workingDirectory", "stdin", "stdout", "stderr", "stdoutStderr"):
                invocation[field] = {"index": 0}
            invocation["responseFiles"] = [{"index": 0}]
            for index, finding in enumerate(run["results"]):
                finding["ruleIndex"] = rule_index
                finding["provenance"] = {"invocationIndex": self.invocation_index, "properties": {"invocationIndex": 97}}
                finding["locations"][0]["physicalLocation"]["artifactLocation"]["index"] = index
                finding["analysisTarget"] = {"index": index}
        if self.behavior == "late-incomplete" and len(self.calls) == 2:
            data["paths"]["scanned"] = []
        if self.behavior == "malformed-paths":
            data["paths"] = []
        if self.behavior == "mismatch":
            sarif["runs"][0]["results"] = []
        if self.behavior == "parse-error":
            data["errors"] = [{"type": "ParseError"}]
        for flag, value in (("--output", data), ("--sarif-output", sarif)):
            Path(args[args.index(flag) + 1]).write_bytes(json.dumps(value).encode())
        return subprocess.CompletedProcess([], 1 if self.behavior == "findings" else 0, b"PRIVATE_MATCH", b"PRIVATE_MATCH")

    def test_bounded_scans_emit_one_aggregate_run_and_preserve_rule_ids_and_native_metadata(self):
        self.assertEqual(semgrep_scan.scan(self.settings, include=("*.rs", "*.py"), exclude=("tests/semgrep/**",)), 1)
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all("--enable-nosem" in args and "--disable-nosem" not in args for args in self.calls))
        self.inventory.assert_called_once_with(self.root, include=("*.rs", "*.py"), exclude=("tests/semgrep/**",))
        output = self.root / "target/security/semgrep"
        self.assertEqual({path.name for path in output.iterdir()}, {"semgrep.json", "semgrep.sarif"})
        sarif = json.loads((output / "semgrep.sarif").read_bytes())
        self.assertEqual(len(sarif["runs"]), 1)
        run = sarif["runs"][0]
        # GitHub splits at the last slash into category and optional run ID.
        self.assertEqual(run["automationDetails"]["id"].rpartition("/"), ("repository-rules", "/", ""))
        self.assertEqual([item["ruleId"] for item in run["results"]], ["project.rule"] * 3)
        self.assertEqual([item["ruleIndex"] for item in run["results"]], [1] * 3)
        data = json.loads((output / "semgrep.json").read_bytes())
        self.assertEqual(len(data["results"]), 3)
        self.assertEqual(len(data["batches"]), 2)
        self.assertTrue(all(batch["native_metadata"] == {"kept": True} for batch in data["batches"]))

    def test_cli_and_api_use_the_same_policy_and_numbered_layout_replaces_aggregate_files(self):
        self.behavior = "clean"
        semgrep_scan.scan(self.settings, include=("*.rs",))
        with patch("research_repo_tools.cli.config.load", return_value=self.settings):
            self.assertEqual(main(["semgrep", "scan", "--include", "*.rs", "--report-layout", "numbered", "--no-inline-suppressions"]), 0)
        output = self.root / "target/security/semgrep"
        self.assertEqual({path.name for path in output.iterdir()}, {"0.json", "0.sarif", "1.json", "1.sarif"})
        automation_ids = [json.loads((output / f"{index}.sarif").read_bytes())["runs"][0]["automationDetails"]["id"] for index in range(2)]
        self.assertEqual([identity.rpartition("/") for identity in automation_ids], [("repository-rules-0-0", "/", ""), ("repository-rules-1-0", "/", "")])
        self.assertTrue(all("--disable-nosem" in args for args in self.calls[2:]))

    def test_documentation_scan_publishes_original_paths_and_lines_without_changing_source(self):
        source = self.root / "docs/guide café.md"
        source.parent.mkdir()
        original = b"# Guide\r\n\r\n```rust\r\n# bad()\r\n```\r\n\r\n```rust\r\nbad()\r\n```\r\n"
        source.write_bytes(original)
        self.inventory.return_value = ("docs/guide café.md",)
        self.behavior = "documentation"

        self.assertEqual(semgrep_scan.scan(self.settings, include=("*.md",), rust_docs=True, batch_size=1), 1)

        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(args[-1] != str(source) for args in self.calls))
        output = self.root / "target/security/semgrep"
        data = json.loads((output / "semgrep.json").read_bytes())
        sarif = json.loads((output / "semgrep.sarif").read_bytes())
        self.assertEqual([result["path"] for result in data["results"]], [str(source)] * 2)
        self.assertEqual([result["start"]["line"] for result in data["results"]], [4, 8])
        self.assertEqual([result["end"]["line"] for result in data["results"]], [4, 8])
        self.assertEqual(data["paths"]["scanned"], [str(source)])
        locations = [result["locations"][0]["physicalLocation"] for result in sarif["runs"][0]["results"]]
        self.assertEqual([unquote(location["artifactLocation"]["uri"]) for location in locations], [source.as_posix()] * 2)
        self.assertEqual([location["region"]["startLine"] for location in locations], [4, 8])
        for report in (data, sarif):
            self.assertNotIn("research-semgrep-scan-", json.dumps(report))
        self.assertEqual(source.read_bytes(), original)

    def test_invalid_coverage_or_report_agreement_and_timeouts_publish_empty_generation(self):
        output = self.root / "target/security/semgrep"
        output.mkdir(parents=True)
        for behavior in ("late-incomplete", "malformed-paths", "mismatch", "parse-error", "timeout"):
            with self.subTest(behavior=behavior):
                self.calls.clear()
                self.behavior = behavior
                (output / "old.sarif").write_bytes(b"stale")
                self.assertEqual(semgrep_scan.scan(self.settings, include=("*.rs",)), 124 if behavior == "timeout" else 1)
                self.assertEqual(list(output.iterdir()), [])
        self.behavior = "findings"
        self.assertEqual(semgrep_scan.scan(self.settings, include=("*.rs",)), 1)
        self.assertEqual({path.name for path in output.iterdir()}, {"semgrep.json", "semgrep.sarif"})
        self.assertEqual([result["check_id"] for result in json.loads((output / "semgrep.json").read_bytes())["results"]], ["project.rule"] * 3)

    def test_reviewed_suppressed_findings_do_not_block_but_remain_in_native_reports(self):
        self.behavior = "suppressed"
        self.assertEqual(semgrep_scan.scan(self.settings, include=("*.rs",)), 0)
        sarif = json.loads((self.root / "target/security/semgrep/semgrep.sarif").read_bytes())
        self.assertEqual(len(sarif["runs"][0]["results"]), 3)
        self.assertEqual(semgrep_scan.scan(self.settings, include=("*.rs",), inline_suppressions=False), 1)

    def test_invalid_policies_fail_before_native_execution(self):
        for key, value in (
            ("batch-size", 0),
            ("jobs", True),
            ("target-timeout", -1),
            ("inline-suppressions", "yes"),
            ("report-layout", "file"),
            ("report-category", "../escape"),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                config.parse({"semgrep": {key: value}}, root=self.root)
        self.assertEqual(self.calls, [])

    def test_absolute_file_arguments_are_preserved_and_output_cannot_replace_source_inputs(self):
        paths = [str(self.root / name) for name in self.names]
        self.assertEqual(argument_batches(["scanner"], paths, batch_size=2), (("scanner", *paths[:2]), ("scanner", paths[2])))
        originals = {(self.root / name): (self.root / name).read_bytes() for name in self.names}
        with self.assertRaisesRegex(ValueError, "contain selected inputs"):
            semgrep_scan.scan(self.settings, include=("*.rs",), output=str(self.root))
        self.assertEqual(self.calls, [])
        self.assertTrue(all(path.read_bytes() == payload for path, payload in originals.items()))

    def test_aggregate_reindexes_reordered_rule_and_artifact_tables(self):
        self.behavior = "tables"
        self.assertEqual(semgrep_scan.scan(self.settings, include=("*.rs", "*.py")), 1)
        run = json.loads((self.root / "target/security/semgrep/semgrep.sarif").read_bytes())["runs"][0]
        self.assertEqual([result["ruleIndex"] for result in run["results"]], [1, 1, 1])
        self.assertEqual([result["provenance"]["invocationIndex"] for result in run["results"]], [0, 0, 1])
        self.assertTrue(all(result["provenance"]["properties"] == {"invocationIndex": 97} for result in run["results"]))
        self.assertEqual([result["locations"][0]["physicalLocation"]["artifactLocation"]["index"] for result in run["results"]], [0, 1, 2])
        self.assertEqual(run["tool"]["driver"]["rules"][1]["relationships"][0]["target"]["index"], 1)
        self.assertEqual([artifact["location"]["index"] for artifact in run["artifacts"]], [0, 1, 2])
        self.assertEqual([result["analysisTarget"]["index"] for result in run["results"]], [0, 1, 2])
        for invocation, expected_index in zip(run["invocations"], (0, 2), strict=True):
            self.assertEqual(invocation["ruleConfigurationOverrides"][0]["descriptor"]["index"], 1)
            for field in ("executableLocation", "workingDirectory", "stdin", "stdout", "stderr", "stdoutStderr"):
                self.assertEqual(invocation[field]["index"], expected_index)
            self.assertEqual(invocation["responseFiles"][0]["index"], expected_index)

    def test_case_and_unicode_output_aliases_cannot_replace_selected_inputs(self):
        source = self.root / "café/one.rs"
        source.parent.mkdir()
        source.write_bytes(b"original\r\n\xff")
        self.inventory.return_value = (*self.names, "café/one.rs")
        originals = {(self.root / name): (self.root / name).read_bytes() for name in self.inventory.return_value}
        for output in ("SRC", "src/ONE.rs", "cafe\u0301"):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, "contain selected inputs"):
                semgrep_scan.scan(self.settings, include=("*.rs",), output=output)
            self.assertEqual(self.calls, [])
            self.assertTrue(all(path.read_bytes() == payload for path, payload in originals.items()))

    def test_unknown_invocation_provenance_stays_unknown_after_aggregation(self):
        self.behavior = "tables"
        self.invocation_index = -1
        for layout in ("aggregate", "numbered"):
            with self.subTest(layout=layout):
                self.calls.clear()
                self.assertEqual(semgrep_scan.scan(self.settings, include=("*.rs", "*.py"), report_layout=layout), 1)
                runs = [json.loads(path.read_bytes())["runs"][0] for path in sorted((self.root / "target/security/semgrep").glob("*.sarif"))]
                self.assertEqual([result["provenance"]["invocationIndex"] for run in runs for result in run["results"]], [-1, -1, -1])

    def test_invalid_invocation_provenance_publishes_empty_generation(self):
        self.behavior = "tables"
        output = self.root / "target/security/semgrep"
        output.mkdir(parents=True)
        for layout in ("aggregate", "numbered"):
            for index in (False, -2, 1, "0", None):
                with self.subTest(layout=layout, index=index):
                    self.calls.clear()
                    self.invocation_index = index
                    (output / "old.sarif").write_bytes(b"stale")
                    self.assertEqual(semgrep_scan.scan(self.settings, include=("*.rs", "*.py"), report_layout=layout), 1)
                    self.assertEqual(list(output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
