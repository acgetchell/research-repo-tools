"""Batched Semgrep behavior through the installed public API and CLI."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
            if self.behavior != "clean":
                json_findings.append({"check_id": "project.rule", "path": path, "start": {"line": 1}, "end": {"line": 1}, "extra": {"is_ignored": suppressed}})
                sarif_findings.append(
                    {
                        "ruleId": "project.rule",
                        "ruleIndex": 1,
                        "message": {"text": "PRIVATE_MATCH"},
                        "locations": [{"physicalLocation": {"artifactLocation": {"uri": Path(path).as_posix()}, "region": {"startLine": 1}}}],
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
            run["artifacts"] = [{"location": {"uri": Path(path).as_posix()}} for path in paths]
            for index, finding in enumerate(run["results"]):
                finding["ruleIndex"] = rule_index
                finding["invocationIndex"] = 0
                finding["locations"][0]["physicalLocation"]["artifactLocation"]["index"] = index
        if self.behavior == "late-incomplete" and len(self.calls) == 2:
            data["paths"]["scanned"] = []
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
        self.assertEqual(run["automationDetails"]["id"], "repository-rules")
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
        self.assertTrue(all("--disable-nosem" in args for args in self.calls[2:]))

    def test_invalid_coverage_or_report_agreement_and_timeouts_publish_empty_generation(self):
        output = self.root / "target/security/semgrep"
        output.mkdir(parents=True)
        for behavior in ("late-incomplete", "mismatch", "parse-error", "timeout"):
            with self.subTest(behavior=behavior):
                self.calls.clear()
                self.behavior = behavior
                (output / "old.sarif").write_bytes(b"stale")
                self.assertEqual(semgrep_scan.scan(self.settings, include=("*.rs",)), 124 if behavior == "timeout" else 1)
                self.assertEqual(list(output.iterdir()), [])

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
        self.assertEqual([result["invocationIndex"] for result in run["results"]], [0, 0, 1])
        self.assertEqual([result["locations"][0]["physicalLocation"]["artifactLocation"]["index"] for result in run["results"]], [0, 1, 2])
        self.assertEqual(run["tool"]["driver"]["rules"][1]["relationships"][0]["target"]["index"], 1)


if __name__ == "__main__":
    unittest.main()
