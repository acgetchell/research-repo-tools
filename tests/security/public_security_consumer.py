"""Public capability contracts, repeated from isolated wheel/sdist environments."""

import importlib.metadata
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

from research_repo_tools import config, python_baseline, security, semgrep_scan
from research_repo_tools.cli import main
from research_repo_tools.semgrep_docs import rust_blocks
from research_repo_tools.toolchain_config import load


class TestSharedCapabilities(unittest.TestCase):
    @staticmethod
    def _fixture_history(args, **kwargs):
        if "rev-list" in args:
            return subprocess.CompletedProcess(args, 0, b"a" * 40 + b"\n", b"")
        return subprocess.CompletedProcess(args, 0, b"false\n", b"")

    def test_gitleaks_findings_requires_both_redacted_reports_and_shows_only_identity(self):
        with tempfile.TemporaryDirectory(prefix="gitleaks findings café ") as directory:
            root = Path(directory).resolve()
            reports = root / "downloaded reports"
            reports.mkdir()
            history = reports / "gitleaks-history.json"
            working = reports / "gitleaks-working.json"
            finding = self.secret_finding("a" * 40, "logs/run café.json", 5)
            current = self.secret_finding("", "/tmp/research-secret-scan-fixture/working/logs/current.json", 3)
            history.write_bytes(json.dumps([finding]).encode())
            working.write_bytes(json.dumps([current, self.secret_finding("", r"C:\Temp\research-secret-scan-fixture\working\logs\windows.json", 4)]).encode())
            arguments = ["--root", str(root), "security", "findings", "--reports", reports.name]
            settings = config.parse({}, root=root)
            with (
                patch("research_repo_tools.cli.config.load", return_value=settings),
                patch.object(security, "_binary", side_effect=AssertionError("report summary invoked a scanner")),
                redirect_stdout(io.StringIO()) as stdout,
            ):
                self.assertEqual(main(arguments), 0)
                self.assertIn('history "logs/run café.json":9:5-9:8 "fixture-rule" at commit ' + "a" * 40, stdout.getvalue())
                self.assertIn('working "logs/current.json":9:3-9:6 "fixture-rule"', stdout.getvalue())
                self.assertIn('working "logs/windows.json":9:4-9:7 "fixture-rule"', stdout.getvalue())
                self.assertNotIn("research-secret-scan-", stdout.getvalue())
                self.assertNotIn("_SECRET", stdout.getvalue())
                working.write_bytes(json.dumps([{**current, "Secret": "UNREDACTED_SECRET"}]).encode())
                with redirect_stdout(io.StringIO()) as failed, redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(main(arguments), 1)
                self.assertEqual(failed.getvalue(), "")
                self.assertIn("unredacted secret", errors.getvalue())
                self.assertNotIn("_SECRET", errors.getvalue())
                valid = json.dumps([{**current, "MATCH_SECRET": 1}]).encode()
                working.write_bytes(valid)
                self.assertEqual(main(arguments), 0)
                working.write_bytes(valid.replace(b'"MATCH_SECRET": 1', b'"MATCH_SECRET": 2, "MATCH_SECRET": 1'))
                with redirect_stdout(io.StringIO()) as failed, redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(main(arguments), 1)
                self.assertEqual(failed.getvalue(), "")
                self.assertIn(working.name, errors.getvalue())
                self.assertNotIn("MATCH_SECRET", errors.getvalue())
                working.unlink()
                with redirect_stdout(io.StringIO()) as failed, redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(main(arguments), 1)
                self.assertEqual(failed.getvalue(), "")
                self.assertIn(working.name, errors.getvalue())

    @staticmethod
    def secret_finding(commit, path, column, rule="fixture-rule"):
        return {
            "Commit": commit,
            "File": path,
            "StartLine": 9,
            "StartColumn": column,
            "EndLine": 9,
            "EndColumn": column + 3,
            "RuleID": rule,
            "Secret": "REDACTED",
            "Match": "MATCH_SECRET",
            "Description": "DESCRIPTION_SECRET",
            "Message": "COMMIT_SECRET",
        }

    def test_gitleaks_exception_review_compares_one_snapshot_and_requires_complete_scans(self):
        with tempfile.TemporaryDirectory(prefix="exception review café ") as directory:
            root = Path(directory).resolve()
            source = root / "run café.json"
            source.write_bytes(b"retained fixture\r\n")
            baseline, candidate = root / ".gitleaks.toml", root / "candidate policy.toml"
            (root / "policy inputs").mkdir()
            leaf = root / "inherited leaf.toml"
            leaf_bytes = b"# synthetic inherited policy\n"
            leaf.write_bytes(leaf_bytes)
            (root / "policy inputs" / "parent.toml").write_bytes(b"extend.path = 'inherited leaf.toml'\n")
            baseline.write_bytes(b"[extend]\npath = 'policy inputs/parent.toml'\n")
            candidate_bytes = b"title = 'proposed'\nExtend = { Path = 'policy inputs/parent.toml' }\n"
            candidate.write_bytes(candidate_bytes)
            calls = []

            def git(args, *, cwd, env):
                nonlocal history, captured_refs
                self.assertNotIn("GIT_DIR", env)
                self.assertNotIn("GIT_WORK_TREE", env)
                if "--is-shallow-repository" in args:
                    self.assertEqual(cwd, root)
                    output = b"false\n"
                elif "show-ref" in args:
                    self.assertIn(cwd, (root, history))
                    output = refs if cwd == root else captured_refs
                elif "rev-list" in args:
                    self.assertIn(cwd, (root, history))
                    output = b"a" * 40 + b"\n"
                elif "clone" in args:
                    self.assertEqual(cwd, root)
                    self.assertIn("--mirror", args)
                    self.assertEqual(Path(args[-2]), root)
                    history, captured_refs = Path(args[-1]), refs
                    output = b""
                else:
                    self.assertEqual(cwd, root)
                    self.assertEqual(args, ["--no-pager", "rev-parse", "--verify", "HEAD"])
                    output = b"a" * 40 + b"\n"
                return subprocess.CompletedProcess(args, 0, output, b"")

            def native(binary, args, **kwargs):
                nonlocal refs
                mode, target = args[0], kwargs["cwd"]
                policy = Path(args[args.index("--config") + 1])
                document = tomllib.loads(policy.read_text(encoding="utf-8"))
                is_candidate = document.get("title") == "proposed"
                extension = document["Extend"] if is_candidate else document["extend"]
                retained_leaf = None
                if extension.get("useDefault"):
                    self.assertTrue(create_baseline)
                    baseline.write_bytes(b"[extend]\nuseDefault = true\n")
                else:
                    parent = Path(extension["Path"] if is_candidate else extension["path"])
                    retained_leaf = Path(tomllib.loads(parent.read_text(encoding="utf-8"))["extend"]["path"])
                    self.assertTrue(parent.is_absolute() and retained_leaf.is_absolute())
                    self.assertNotEqual(retained_leaf, leaf)
                    self.assertEqual(retained_leaf.read_bytes(), leaf_bytes)
                calls.append((mode, target, is_candidate, retained_leaf))
                self.assertIn("--exit-code=3", args)
                self.assertIn("--redact=100", args)
                self.assertNotIn("--timeout=300", args)
                self.assertEqual(kwargs["timeout"], 300)
                self.assertEqual(kwargs["env"], {"FIXTURE": "1"})
                if mode == "dir":
                    self.assertEqual(args[1], ".")
                    self.assertEqual((target / source.name).read_bytes(), b"retained fixture\r\n")
                    path, commit = source.name, ""
                else:
                    self.assertEqual(target, history)
                    self.assertEqual(Path(args[1]), history)
                    path, commit = source.name, "a" * 40
                records = [self.secret_finding(commit, path, 7)]
                if not is_candidate:
                    records.append(self.secret_finding(commit, path, 2))
                    # Files can change after the working snapshot has been captured.
                    source.write_bytes(b"changed source\n")
                    if change_extension:
                        leaf.write_bytes(b"# changed inherited policy\n")
                else:
                    records.append(self.secret_finding(commit, path, 9, "new-rule"))
                    if move_ref:
                        refs = b"changed refs"
                Path(args[args.index("--report-path") + 1]).write_bytes(json.dumps(records).encode())
                return subprocess.CompletedProcess(args, exit_code, b"RAW_SECRET", b"RAW_SECRET")

            settings = config.parse({}, root=root)
            arguments = ["--root", str(root), "security", "check-exceptions", candidate.name, "--exclude", "**/*.bin"]
            history, captured_refs = root, b""
            with (
                patch("research_repo_tools.cli.config.load", return_value=settings),
                patch.dict(os.environ, {"GIT_DIR": str(root / ".git"), "GIT_WORK_TREE": str(root)}),
                patch.object(security, "security_inventory", return_value=(source.name,)) as inventory,
                patch.object(security, "_binary", return_value=(root / "gitleaks", {"GIT_DIR": "ambient", "GITLEAKS_CONFIG": "ambient", "FIXTURE": "1"})),
                patch.object(security, "run_git_bytes", side_effect=git),
                patch.object(security, "run_command_bytes", side_effect=native),
            ):
                refs = b"initial refs"
                exit_code = 3
                move_ref = False
                change_extension = False
                create_baseline = False
                with redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(main(arguments), 0)
                output = stdout.getvalue()
                self.assertEqual(inventory.call_count, 1)
                self.assertEqual(inventory.call_args.args, (root,))
                self.assertEqual(inventory.call_args.kwargs["exclude"], ("**/*.bin",))
                self.assertNotIn("GIT_DIR", inventory.call_args.kwargs["env"])
                self.assertEqual([(mode, proposed) for mode, _, proposed, _ in calls], [("git", False), ("dir", False), ("git", True), ("dir", True)])
                self.assertEqual(calls[1][1], calls[3][1])
                self.assertEqual(len({retained for _, _, _, retained in calls}), 1)
                self.assertIn('Suppressed: 2 finding(s)\n  history "run café.json":9:2', output)
                self.assertIn('Introduced: 2 finding(s)\n  history "run café.json":9:9', output)
                self.assertIn('Matching locations: 2 finding(s)\n  history "run café.json":9:7', output)
                self.assertIn("Review matching rule IDs and locations whenever a detection rule changes.", output)
                self.assertNotIn("_SECRET", output)
                self.assertNotIn(str(calls[1][1]), output)
                self.assertEqual(candidate.read_bytes(), candidate_bytes)
                self.assertFalse((root / "target").exists())

                exit_code = 1
                with redirect_stdout(io.StringIO()) as failed, redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(main(arguments), 1)
                self.assertEqual(failed.getvalue(), "")
                self.assertIn("history scan failed during policy comparison", errors.getvalue())
                self.assertNotIn("_SECRET", errors.getvalue())

                exit_code = 3
                move_ref = True
                source.write_bytes(b"retained fixture\r\n")
                with redirect_stdout(io.StringIO()) as failed, redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(main([*arguments, "--baseline", str(baseline)]), 1)
                self.assertEqual(failed.getvalue(), "")
                self.assertIn("Git refs changed during Gitleaks scanning", errors.getvalue())

                move_ref = False
                change_extension = True
                source.write_bytes(b"retained fixture\r\n")
                with redirect_stdout(io.StringIO()) as failed, redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(main(arguments), 1)
                self.assertEqual(failed.getvalue(), "")
                self.assertIn("Gitleaks configuration changed during policy comparison", errors.getvalue())
                self.assertIn(leaf.name, errors.getvalue())
                self.assertNotIn("# changed inherited policy", errors.getvalue())

                leaf.write_bytes(b"[invalid")
                with redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(main(arguments), 1)
                self.assertIn(leaf.name, errors.getvalue())
                self.assertNotIn("[invalid", errors.getvalue())
                leaf.write_bytes(leaf_bytes)
                candidate.write_bytes(b'"extend.path" = "policy inputs/parent.toml"\n')
                with redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(main(arguments), 1)
                self.assertIn(candidate.name, errors.getvalue())
                self.assertIn("[extend] table", errors.getvalue())
                missing = "missing candidate.toml"
                with redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(main(["--root", str(root), "security", "check-exceptions", missing]), 1)
                self.assertIn(missing, errors.getvalue())
                candidate.write_bytes(candidate_bytes)
                baseline.unlink()
                baseline.mkdir()
                with redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(main(arguments), 1)
                self.assertIn(baseline.name, errors.getvalue())
                self.assertIn("regular file", errors.getvalue())
                baseline.rmdir()
                change_extension = False
                create_baseline = True
                source.write_bytes(b"retained fixture\r\n")
                with redirect_stdout(io.StringIO()) as failed, redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(main(arguments), 1)
                self.assertEqual(failed.getvalue(), "")
                self.assertIn("Gitleaks configuration changed during policy comparison", errors.getvalue())
                self.assertIn(baseline.name, errors.getvalue())

    def test_gitleaks_preflight_failure_and_partial_scan_invalidate_old_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "source.txt").write_bytes(b"fixture\n")
            policy = root / ".gitleaks.toml"
            settings = config.parse({}, root=root)
            output = root / "reports"
            output.mkdir()
            notes = output / "notes.json"
            notes.write_bytes(b"unrelated report\n")

            def native(binary, args, **kwargs):
                self.assertIn("--exit-code=3", args)
                fmt = args[args.index("--report-format") + 1]
                result = [] if fmt == "json" else {"version": "2.1.0", "runs": [{"tool": {}, "results": []}]}
                if fmt == "json" and native_status == 3:
                    result = [self.secret_finding("", "source.txt", 1)]
                Path(args[args.index("--report-path") + 1]).write_bytes(json.dumps(result).encode())
                return subprocess.CompletedProcess(args, native_status, b"RAW_SECRET", b"RAW_SECRET")

            with (
                patch.object(security, "security_inventory", return_value=("source.txt",)),
                patch.object(security, "_binary", return_value=(root / "gitleaks", {})),
                patch.object(security, "run_git_bytes", side_effect=self._fixture_history),
                patch.object(security, "run_command_bytes", side_effect=native) as run,
            ):
                for failure in ("invalid policy", "partial scan", "invalid history identity"):
                    with self.subTest(failure=failure), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
                        native_status = 0
                        self.assertEqual(security.scan_secrets(settings, output=output.name), 0)
                        self.assertIsNone(security.show_secret_findings(settings, reports=output.name))
                        run.reset_mock()
                        if failure == "invalid policy":
                            policy.write_bytes(b"[invalid")
                            with self.assertRaisesRegex(ValueError, "valid UTF-8 TOML"):
                                security.scan_secrets(settings, output=output.name)
                            run.assert_not_called()
                            policy.unlink()
                        elif failure == "partial scan":
                            native_status = 1
                            self.assertEqual(security.scan_secrets(settings, output=output.name), 2)
                            self.assertIn("scan did not complete", errors.getvalue())
                        else:
                            native_status = 3
                            self.assertEqual(security.scan_secrets(settings, output=output.name), 2)
                            self.assertIn("incomplete", errors.getvalue())
                        expected = {notes} if native_status != 3 else {notes, output / "gitleaks-working.json"}
                        self.assertEqual(set(output.iterdir()), expected)
                        self.assertNotIn("RAW_SECRET", errors.getvalue())
                        with self.assertRaisesRegex(ValueError, "gitleaks-history.json"):
                            security.show_secret_findings(settings, reports=output.name)

    def test_gitleaks_findings_do_not_mask_a_later_native_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "source.txt").write_bytes(b"fixture\n")
            settings = config.parse({}, root=root)

            def native(binary, args, **kwargs):
                history = args[0] == "git"
                fmt = args[args.index("--report-format") + 1]
                if history and fmt == "sarif":
                    self.assertEqual(kwargs["timeout"], 300)
                    self.assertNotIn("--timeout=300", args)
                    raise subprocess.TimeoutExpired(binary, kwargs["timeout"])
                result = [self.secret_finding("a" * 40, "source.txt", 1)] if history else []
                if fmt == "sarif":
                    result = {"version": "2.1.0", "runs": [{"tool": {}, "results": []}]}
                Path(args[args.index("--report-path") + 1]).write_bytes(json.dumps(result).encode())
                return subprocess.CompletedProcess(args, 3 if history else 0, b"", b"")

            with (
                patch.object(security, "security_inventory", return_value=("source.txt",)),
                patch.object(security, "_binary", return_value=(root / "gitleaks", {})),
                patch.object(security, "run_git_bytes", side_effect=self._fixture_history),
                patch.object(security, "run_command_bytes", side_effect=native),
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as errors,
            ):
                self.assertEqual(security.scan_secrets(settings), 124)
                self.assertIn("Gitleaks (history): 1 finding(s)", stdout.getvalue())
                self.assertIn("scan timed out", errors.getvalue())
            self.assertEqual(
                {path.name for path in (root / "target" / "security").iterdir()}, {"gitleaks-history.json", "gitleaks-working.json", "gitleaks-working.sarif"}
            )

    def test_cli_findings_are_actionable_once_per_scan_and_never_echo_matches(self):
        for scanner in ("osv", "secrets", "semgrep"):
            for behavior, expected in (
                ("findings", 1),
                ("clean", 0),
                ("error", 23),
                ("malformed", 1),
                ("malformed-json", 23),
                ("missing-json", 23),
                ("timeout", 124),
            ):
                with self.subTest(scanner=scanner, behavior=behavior), tempfile.TemporaryDirectory(prefix="scan café ") as directory:
                    if scanner == "secrets" and behavior == "malformed":
                        expected = 2
                    root = Path(directory).resolve()
                    (root / "uv.lock").write_bytes(b"version=1\n")
                    (root / "source café.py").write_bytes(b"value = 1\n")
                    (root / "rules.yml").write_bytes(b"rules: [{id: FAKE-RULE, languages: [python], pattern: value, message: fixture, severity: WARNING}]\n")
                    settings = config.parse({"semgrep": {"config": "rules.yml"}}, root=root)
                    fallback = behavior in {"malformed-json", "missing-json"}
                    found = behavior == "findings" or fallback

                    def native(binary, args, **kwargs):
                        if behavior == "timeout":
                            raise subprocess.TimeoutExpired("scanner", 600, output=b"RAW_SECRET", stderr=b"RAW_SECRET")
                        flag = "--output-file" if scanner == "osv" else "--report-path" if scanner == "secrets" else "--output"
                        destination = Path(args[args.index(flag) + 1])
                        fmt = (
                            args[args.index("--format") + 1]
                            if scanner == "osv"
                            else args[args.index("--report-format") + 1]
                            if scanner == "secrets"
                            else "json"
                            if "--json" in args
                            else "sarif"
                        )
                        if fallback and fmt == "json":
                            if behavior == "malformed-json":
                                destination.write_bytes(b"malformed MATCH_SECRET")
                            return subprocess.CompletedProcess([], 23, b"RAW_SECRET", b"RAW_SECRET")
                        sarif = {
                            "version": "2.1.0",
                            "runs": [
                                {
                                    "tool": {"driver": {"name": "Semgrep"}},
                                    "results": [
                                        {
                                            "ruleId": "FAKE-RULE",
                                            "message": {"text": "MESSAGE_SECRET"},
                                            "locations": [
                                                {
                                                    "physicalLocation": {
                                                        "artifactLocation": {"uri": "source café.py"},
                                                        "region": {"startLine": 7, "snippet": {"text": "REDACTED"}},
                                                    }
                                                }
                                            ],
                                            "partialFingerprints": {"commitMessage": "COMMIT_SECRET"},
                                        }
                                    ]
                                    if found
                                    else [],
                                }
                            ],
                        }
                        if fmt == "sarif":
                            value = sarif
                        elif scanner == "osv":
                            value = {
                                "results": [
                                    {
                                        "source": {"path": str(root / "uv.lock")},
                                        "packages": [
                                            {
                                                "package": {"name": "fixture", "version": "1.2.3"},
                                                "vulnerabilities": [{"id": "FAKE-VULN", "summary": "SUMMARY_SECRET"}] if found else [],
                                            }
                                        ],
                                    }
                                ]
                            }
                        elif scanner == "secrets":
                            value = (
                                [
                                    {
                                        "RuleID": "FAKE-RULE",
                                        "File": "source café.py",
                                        "StartLine": 7,
                                        "StartColumn": 1,
                                        "EndLine": 7,
                                        "EndColumn": 2,
                                        "Commit": "a" * 40 if args[0] == "git" else "",
                                        "Secret": "REDACTED",
                                        "Match": "MATCH_SECRET",
                                        "Message": "COMMIT_SECRET",
                                        "Description": "DESCRIPTION_SECRET",
                                        "Fragment": "FRAGMENT_SECRET",
                                    }
                                ]
                                if found
                                else []
                            )
                        else:
                            value = {
                                "results": [
                                    {
                                        "check_id": "FAKE-RULE",
                                        "path": str(root / "source café.py"),
                                        "start": {"line": 7},
                                        "end": {"line": 7},
                                        "extra": {"message": "MESSAGE_SECRET", "lines": "MATCH_SECRET"},
                                    }
                                ]
                                if found
                                else [],
                                "errors": [],
                                "paths": {"scanned": [str(root / "source café.py")]},
                            }
                        destination.write_bytes(b"malformed" if behavior == "malformed" else json.dumps(value).encode())
                        if scanner == "semgrep":
                            Path(args[args.index("--sarif-output") + 1]).write_bytes(json.dumps(sarif).encode())
                        status = 23 if behavior == "error" else 3 if scanner == "secrets" and found else 0
                        return subprocess.CompletedProcess([], status, b"RAW_SECRET", b"RAW_SECRET")

                    stdout, stderr = io.StringIO(), io.StringIO()
                    with (
                        patch("research_repo_tools.cli.config.load", return_value=settings),
                        patch.object(security, "security_inventory", return_value=("uv.lock", "source café.py")),
                        patch.object(semgrep_scan, "security_inventory", return_value=("source café.py",)),
                        patch.object(security, "_binary", return_value=(root / "scanner", {})),
                        patch.object(semgrep_scan.semgrep_tool, "setup", return_value=root / "scanner"),
                        patch.object(security, "run_git_bytes", side_effect=self._fixture_history),
                        patch.object(security, "run_command_bytes", side_effect=native),
                        redirect_stdout(stdout),
                        redirect_stderr(stderr),
                    ):
                        command = (
                            ["semgrep", "scan", "--include", "*.py"]
                            if scanner == "semgrep"
                            else ["security", scanner, *(["uv.lock"] if scanner == "osv" else [])]
                        )
                        self.assertEqual(main(["--root", str(root), *command]), expected)
                    output = stdout.getvalue() + stderr.getvalue()
                    self.assertNotIn("_SECRET", output)
                    if fallback and scanner == "semgrep":
                        self.assertIn("no report published", output)
                        self.assertEqual(list((root / "target").rglob("*.sarif")), [])
                    elif found:
                        self.assertIn("1 finding(s)", output)
                        self.assertIn("exit 1", output)
                        self.assertNotIn("exit 0", output)
                        self.assertIn(".sarif", output)
                        if fallback:
                            self.assertIn("exit 23", output)
                            self.assertEqual(list((root / "target").rglob("*.json")), [])
                            self.assertEqual(len(list((root / "target").rglob("*.sarif"))), 2 if scanner == "secrets" else 1)
                        else:
                            self.assertIn(".json", output)
                        if scanner == "osv" and not fallback:
                            self.assertIn("uv.lock", output)
                            self.assertIn("fixture", output)
                            self.assertIn("1.2.3", output)
                            self.assertEqual(output.count("FAKE-VULN"), 1)
                        else:
                            self.assertIn("source café.py", output)
                            self.assertIn(":7", output)
                            self.assertEqual(output.count("FAKE-RULE"), 2 if scanner == "secrets" else 1)
                            if scanner == "secrets":
                                self.assertIn("Gitleaks (history)", output)
                                self.assertIn("Gitleaks (working tree)", output)
                    elif scanner == "secrets" and behavior == "error":
                        self.assertIn("exit 23); no report published", output)
                        self.assertEqual(list((root / "target").rglob("gitleaks-*")), [])
                    elif behavior in {"clean", "error"}:
                        self.assertIn("0 finding(s)", output)
                        self.assertIn(f"exit {expected}", output)
                    elif behavior == "timeout":
                        self.assertIn("timed out", output)
                    else:
                        self.assertIn("no report published", output)

    def test_report_directories_reject_links_before_mutation(self):
        with tempfile.TemporaryDirectory(prefix="report links ") as directory:
            root = Path(directory).resolve()
            (root / "uv.lock").write_bytes(b"version = 1\n")
            (root / "source.py").write_bytes(b"value = 1\n")
            (root / "rules.yml").write_bytes(b"rules: [{id: fixture, languages: [python], pattern: value, message: fixture, severity: WARNING}]\n")
            external = root / "external"
            (external / "nested").mkdir(parents=True)
            for parent in (external, external / "nested"):
                for name in ("osv-12.json", "osv-12.sarif", "gitleaks-history.json", "gitleaks-history.sarif", "12.json", "12.sarif"):
                    (parent / name).write_bytes(b"external report\n")
            original = {path.relative_to(external): path.read_bytes() for path in external.rglob("*") if path.is_file()}
            alias = root / "output link"
            if os.name == "nt":
                subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(external)], capture_output=True, check=True)
                self.assertTrue(alias.is_junction())
            else:
                alias.symlink_to(external, target_is_directory=True)
                self.assertTrue(alias.is_symlink())
            self.assertEqual(alias.resolve(), external)
            settings = config.parse({"semgrep": {"config": "rules.yml"}}, root=root)
            for scanner in ("osv", "secrets", "semgrep"):
                for suffix in ("", "nested", "new/child"):
                    with self.subTest(scanner=scanner, suffix=suffix):
                        output = str(alias / suffix)
                        with (
                            patch.object(security, "security_inventory", return_value=("uv.lock",)),
                            patch.object(security, "_binary", return_value=(root / "osv-scanner", {})),
                            patch.object(semgrep_scan, "security_inventory", return_value=("source.py",)),
                            patch.object(semgrep_scan.semgrep_tool, "setup", return_value=root / "semgrep"),
                            patch.object(security, "run_command_bytes", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run,
                        ):
                            with self.assertRaisesRegex(ValueError, "report directory.*(?:symlink|junction)"):
                                if scanner == "osv":
                                    security.scan_osv(settings, ("uv.lock",), output=output)
                                elif scanner == "secrets":
                                    security.scan_secrets(settings, output=output)
                                else:
                                    semgrep_scan.scan(settings, include=("*.py",), output=output)
                            run.assert_not_called()
                        self.assertFalse((external / "new").exists())
                        self.assertEqual(original, {path.relative_to(external): path.read_bytes() for path in external.rglob("*") if path.is_file()})

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
            (root / "rules.yml").write_bytes(b"rules: [{id: fixture, languages: [python], pattern: value, message: fixture, severity: WARNING}]\n")
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
                        if scanner == "osv":
                            self.assertTrue(all(not path.exists() and not path.is_symlink() for path in stale))
                        else:
                            self.assertTrue(all(path.exists() or path.is_symlink() for path in stale))
                        self.assertTrue(all(path.read_bytes() == b"previous report\n" for path in preserved))
                        # A failed native scan must not leave reports from a larger inventory.
                        return subprocess.CompletedProcess([], 19, b"", b"")

                    with (
                        patch.object(security, "security_inventory", return_value=("uv.lock",)),
                        patch.object(security, "_binary", return_value=(root / "osv-scanner", {})),
                        patch.object(semgrep_scan, "security_inventory", return_value=("source.py",)),
                        patch.object(semgrep_scan.semgrep_tool, "setup", return_value=root / "semgrep"),
                        patch.object(security, "run_command_bytes", side_effect=native) as run,
                    ):
                        status = (
                            security.scan_osv(settings, ("uv.lock",), output=scanner)
                            if prefix
                            else semgrep_scan.scan(settings, include=("*.py",), output=scanner)
                        )
                    self.assertEqual(status, 19)
                    self.assertEqual(run.call_count, 2 if scanner == "osv" else 1)
                    if scanner == "osv":
                        self.assertEqual((nested / f"{prefix}12.json").read_bytes(), b"nested report\n")
                    else:
                        self.assertEqual(list(output.iterdir()), [])
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

    def test_malformed_osv_sarif_invocations_fail_without_publishing(self):
        with tempfile.TemporaryDirectory(prefix="scanner consumer ") as directory:
            root = Path(directory).resolve()
            (root / "uv.lock").write_bytes(b"version = 1\n")
            settings = config.parse({}, root=root)

            def native(_binary, args, **_kwargs):
                output = args[args.index("--output-file") + 1]
                value = (
                    {"results": [{"source": {"path": str(root / "uv.lock")}, "packages": [{"package": {"name": "example"}}]}]}
                    if args[args.index("--format") + 1] == "json"
                    else {"version": "2.1.0", "runs": [{"tool": {}, "results": [], "invocations": [{"executionSuccessful": "false"}]}]}
                )
                Path(output).write_bytes(json.dumps(value).encode())
                return subprocess.CompletedProcess([], 0, b"", b"")

            with (
                patch.object(security, "security_inventory", return_value=("uv.lock",)),
                patch.object(security, "_binary", return_value=(root / "osv-scanner", {})),
                patch.object(security, "run_command_bytes", side_effect=native),
            ):
                self.assertEqual(security.scan_osv(settings, ("uv.lock",)), 1)
                self.assertFalse((root / "target/security/osv-0.sarif").exists())


if __name__ == "__main__":
    unittest.main()
