"""Installed availability/resume contracts with native, tiny synthetic workloads."""

import base64
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import override
from unittest.mock import patch

from research_repo_tools.cli import main
from research_repo_tools.common_measurement import DiscoveredSeries, DiscoveryPolicy, parse_plan, validate_run_policy
from research_repo_tools.complete_runs import render_run, validate_run_evidence
from research_repo_tools.evidence import deterministic_json, parse_evidence, serialize_evidence
from research_repo_tools.host_metadata import HostMetadata
from research_repo_tools.measurement import load_measurement, measure_pair
from research_repo_tools.measurement_checkpoints import open_checkpoint
from research_repo_tools.performance_acquisition import measure_and_retain
from research_repo_tools.performance_progress import ProgressReporter
from research_repo_tools.process import run_command_live
from research_repo_tools.publication import publish_publication
from research_repo_tools.publication_config import load_publication
from research_repo_tools.release_pairs import ReleasePair
from research_repo_tools.run_presentation import RunLayout, RunRow, SeriesSelection, render_run_ratios, render_run_table, serialize_run_csv
from research_repo_tools.source_exports import export_source
from research_repo_tools.suite_measurement import AcquisitionSuite, SuiteMeasurementPlan
from research_repo_tools.suite_runs import comparison_incompatibilities, parse_suite_run

PROGRAM = """import json, sys
from pathlib import Path
suite, action, log = sys.argv[1:4]
supported = json.loads(Path('apis.json').read_bytes())
assert suite in supported, 'unsupported API was compiled/invoked'
with Path(log).open('ab') as output:
    output.write((suite + ' ' + action + ' ' + Path('revision').read_text() + '\\n').encode())
case = suite + '/case'
if action == 'list':
    print(case + ': benchmark')
elif action == 'measure':
    target = Path('target/criterion') / suite / 'new'
    target.mkdir(parents=True)
    data = {
        'benchmark.json': {'full_id': case},
        'sample.json': {'iters': [1, 1], 'times': [5, 5]},
        'estimates.json': {s: {'point_estimate': 5, 'confidence_interval': {'confidence_level': .95, 'lower_bound': 4, 'upper_bound': 6}} for s in ('mean', 'median')},
        'tukey.json': [1, 2, 3, 4],
    }
    for name, value in data.items():
        (target / name).write_bytes((json.dumps(value) + '\\r\\n').encode())
"""

HOST = HostMetadata("fixture-os", "fixture-arch", "fixture-cpu", 4, 8, 1024, (("python", "fixture-python"),))
PAIR = ReleasePair("v1.1.0", "v1.0.0")


def sources(apis: tuple[str, ...], revision: str) -> dict[str, bytes]:
    return {
        "bench.py": PROGRAM.encode(),
        "apis.json": json.dumps(apis).encode(),
        "revision": revision.encode(),
        "Cargo.toml": b'[package]\nname="fixture"\nversion="1.1.0"\n',
        **{f"input-{name}.json": b"[1, 2]\r\n" for name in ("shared", "new", "removed", "later")},
    }


def plan(log: Path, names: tuple[str, ...] = ("shared", "new", "removed"), *, mode: str = "common") -> SuiteMeasurementPlan:
    suites = []
    for name in names:
        command = [sys.executable, "bench.py", name]
        phases = {
            phase: {
                "command": [*command, "measure", str(log)],
                "gate": [*command, "gate", str(log)],
                "discovery": {"required": [f"{name}/case"], "suites": [{"name": name, "command": [*command, "list", str(log)]}]},
            }
            for phase in ("baseline", "current")
        }
        measured = parse_plan(
            {
                "schema": 4,
                "harness-mode": mode,
                "backend": "export",
                "sources": ["revision", "apis.json"],
                "harness": ["bench.py", f"input-{name}.json"],
                "workloads": [f"input-{name}.json"],
                "sample-count": 2,
                "probes": {"python": [sys.executable, "--version"]},
                "phases": phases,
                "series": [{"name": phase, "phase": phase, "select": {"include": ["*"]}} for phase in phases],
            }
        )
        suites.append(
            AcquisitionSuite(
                name,
                name + "-v1",
                measured,
                since="v1.1.0" if name == "new" else None,
                before="v1.1.0" if name == "removed" else None,
                compilation=tuple((phase, (*command, "compile", str(log))) for phase in phases),
            )
        )
    return SuiteMeasurementPlan(tuple(suites))


def git_model(revisions: dict[str, dict[str, bytes]]):
    def oid(data: bytes) -> bytes:
        return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest().encode()

    keys = {tag: chr(ord("a") + index) * 40 for index, tag in enumerate(revisions)}
    blobs = {oid(data): data for files in revisions.values() for data in files.values()}

    def git(directory: Path, *args: str, input: bytes | None = None) -> bytes:
        if args == ("rev-parse", "--show-prefix"):
            return b""
        if args[0] == "rev-parse":
            tag = args[-1].removeprefix("refs/tags/").removesuffix("^{commit}")
            if tag == "HEAD":
                tag = next(reversed(keys))
            return (keys[tag] + "\n").encode()
        if args[0] == "ls-tree":
            files = next(files for tag, files in revisions.items() if keys[tag] == args[-1])
            return b"".join(b"100644 blob " + oid(data) + b"\t" + name.encode() + b"\0" for name, data in sorted(files.items()))
        if args != ("cat-file", "--batch") or input is None:
            raise AssertionError(f"unexpected Git operation: {args}")
        return b"".join(key + b" blob " + str(len(blobs[key])).encode() + b"\n" + blobs[key] + b"\n" for key in input.splitlines())

    return git


class TestSuiteAcquisition(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.log = self.root / "calls.log"
        self.revisions = {"v1.0.0": sources(("shared", "removed", "later"), "old"), "v1.1.0": sources(("shared", "new", "later"), "new")}
        self.stack.enter_context(patch("research_repo_tools.source_exports._git", side_effect=git_model(self.revisions)))
        self.stack.enter_context(patch("research_repo_tools.measurement.capture_host", return_value=HOST))
        self.quiet = ProgressReporter(mode="none")

    def test_union_ratios_removed_and_advanced_baseline(self) -> None:
        for mode in ("common", "independent"):
            with self.subTest(mode=mode):
                selected = plan(self.log, mode=mode)
                evidence = measure_pair(self.root, selected, PAIR, progress=self.quiet)
                run = parse_suite_run(parse_evidence(*serialize_evidence(evidence)))
                self.assertEqual(dict(run.phases)["baseline"].policy.expected, ("removed/case", "shared/case"))
                self.assertEqual(dict(run.phases)["current"].policy.expected, ("new/case", "shared/case"))
                self.assertEqual(comparison_incompatibilities(evidence, ("baseline", "shared/case"), ("current", "shared/case")), ())
                validate_run_policy(evidence, selected, release=True)
                for _, _, sample, _ in run.observations:
                    self.assertEqual(dict(sample.cases[0].raw_files)["tukey.json"], b"[1, 2, 3, 4]\r\n")
                report = render_run(evidence)
                self.assertIn(b"outside declared release interval", report)
                self.assertIn(b"one-sided workload", report)
        calls = self.log.read_text().splitlines()
        self.assertFalse(any(line.startswith("new ") and line.endswith(" old") for line in calls))
        self.assertFalse(any(line.startswith("removed ") and line.endswith(" new") for line in calls))
        revisions = {"v1.1.0": sources(("new",), "old"), "v1.2.0": sources(("new",), "new")}
        with patch("research_repo_tools.source_exports._git", side_effect=git_model(revisions)):
            evidence = measure_pair(self.root, plan(self.log, ("new",)), ReleasePair("v1.2.0", "v1.1.0"), progress=self.quiet)
        self.assertEqual(len(parse_suite_run(evidence).observations), 2)

    def test_supported_compile_and_missing_case_fail_instead_of_becoming_unavailable(self) -> None:
        selected = plan(self.log, ("new",))
        revisions = {"v1.1.0": sources((), "old"), "v1.2.0": sources(("new",), "new")}
        with patch("research_repo_tools.source_exports._git", side_effect=git_model(revisions)), self.assertRaises(subprocess.CalledProcessError):
            measure_pair(self.root, selected, ReleasePair("v1.2.0", "v1.1.0"), progress=self.quiet)
        first = selected.suites[0]
        policy = first.measurement.current.policy
        assert isinstance(policy, DiscoveryPolicy)
        changed = replace(policy, discovery=replace(policy.discovery, required=("new/missing",)))
        invalid = SuiteMeasurementPlan((replace(first, measurement=replace(first.measurement, current=replace(first.measurement.current, policy=changed))),))
        with self.assertRaisesRegex(ValueError, "required"):
            measure_pair(self.root, invalid, PAIR, progress=self.quiet)

    def test_exports_follow_required_phases_and_release_completed_suites(self) -> None:
        for mode in ("common", "independent"):
            with self.subTest(mode=mode):
                exports = {}

                @contextmanager
                def tracked_export(capture, destination):
                    exports[destination.name] = destination
                    with export_source(capture, destination) as directory:
                        yield directory

                def checked_command(command, args=(), **kwargs):
                    if args[1:3] == ("new", "measure"):
                        self.assertFalse(exports["shared-baseline"].exists())
                        self.assertFalse(exports["shared-current"].exists())
                    return run_command_live(command, args, **kwargs)

                with (
                    patch("research_repo_tools.suite_measurement.export_source", side_effect=tracked_export),
                    patch("research_repo_tools.common_measurement.run_command_live", side_effect=checked_command),
                ):
                    evidence = measure_pair(self.root, plan(self.log, mode=mode), PAIR, progress=self.quiet)
                expected = {"shared-baseline", "shared-current", "new-current", "removed-baseline"}
                if mode == "common":
                    expected.add("removed-current")  # Supplies the current harness, never measured.
                self.assertEqual(set(exports), expected)
                self.assertTrue(all(not path.exists() for path in exports.values()))
                self.assertEqual(len(parse_suite_run(evidence).observations), 4)

    def test_session_environment_changes_allow_resume_and_other_changes_reject(self) -> None:
        selected = plan(self.log, ("shared",))
        checkpoint = self.root / "checkpoint"
        ignored = ("_", "SHLVL", "TERM_SESSION_ID", "ITERM_SESSION_ID")

        def changing_session(command, args=(), **kwargs):
            for name in ignored:
                os.environ[name] += "-next"
            return run_command_live(command, args, **kwargs)

        with patch.dict(os.environ, dict.fromkeys(ignored, "first")):
            with patch("research_repo_tools.common_measurement.run_command_live", side_effect=changing_session):
                measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, progress=self.quiet)
            accepted = (checkpoint / "unit-shared.json").read_bytes()
            for name in ignored:
                os.environ[name] = "second"
            measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, resume=True, progress=self.quiet)
            self.assertEqual((checkpoint / "unit-shared.json").read_bytes(), accepted)
            self.assertEqual(self.log.read_text().splitlines().count("shared measure old"), 1)
            with patch.dict(os.environ, {"RRT_UNKNOWN_SESSION_VALUE": "changed"}), self.assertRaisesRegex(ValueError, "environment-sha256"):
                measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, resume=True, progress=self.quiet)

            def changing_input(command, args=(), **kwargs):
                os.environ["RRT_UNKNOWN_SESSION_VALUE"] = "changed"
                return run_command_live(command, args, **kwargs)

            with (
                patch("research_repo_tools.common_measurement.run_command_live", side_effect=changing_input),
                self.assertRaisesRegex(ValueError, "acquisition environment changed"),
            ):
                measure_pair(self.root, selected, PAIR, progress=self.quiet)

    def test_normalized_same_release_cannot_be_promoted(self) -> None:
        original = self.revisions["v1.1.0"]
        for name, data in original.items():
            (self.root / name).write_bytes(data)
        (self.root / "revision").write_bytes(b"working")
        selected = plan(self.log, ("shared",))
        with patch("research_repo_tools.source_exports.select_files", return_value=tuple(sorted(original))):
            evidence = measure_pair(self.root, selected, ReleasePair("v1.1.0", "v1.1.0"), working_tree=True, progress=self.quiet)
        raw = json.loads(evidence.payload)
        raw["units"][0]["availability"]["current"]["release"] = "1.1.0"
        raw["units"][0]["observations"]["current"]["source"]["context"]["release"] = "1.1.0"
        changed = replace(
            evidence,
            payload=deterministic_json(raw),
            sources=tuple(
                (phase, replace(source, context=tuple({**dict(source.context), "release": "1.1.0"}.items())) if phase == "current" else source)
                for phase, source in evidence.sources
            ),
        )
        validate_run_policy(changed, selected)
        with self.assertRaisesRegex(ValueError, "same-release"):
            validate_run_policy(changed, selected, release=True)

    def test_prepare_artifacts_derive_suite_inventories(self) -> None:
        original = self.revisions["v1.1.0"]
        for name, data in original.items():
            (self.root / name).write_bytes(data)
        selected = plan(self.log, ("shared",))
        with patch("research_repo_tools.source_exports.select_files", return_value=tuple(sorted(original))):
            evidence = measure_pair(self.root, selected, PAIR, working_tree=True, progress=self.quiet)
        payload, manifest = serialize_evidence(evidence)
        (self.root / "run.json").write_bytes(payload)
        (self.root / "evidence.json").write_bytes(manifest)
        config = (
            'schema=2\npayload="run.json"\nmanifest="evidence.json"\n'
            'repository="example/project"\ntag-policy="prepare"\n'
            'unit="ns"\nstatistic="median"\ncsv="result.csv"\n'
            'rows=[{key="case",label="Case"}]\n'
            'series=[{name="shared/current",label="Current",rows={case="shared/case"}}]\n'
            '[provenance.baseline]\nrelease="v1.0.0"\nrevision="' + "a" * 40 + '"\n'
            '[provenance.current]\nrelease="v1.1.0"\nrevision="' + "b" * 40 + '"\n'
        )
        (self.root / "publication.toml").write_text(config, encoding="utf-8", newline="\n")
        tagged_files = None

        def publication_git(args, *, cwd, check=True):
            self.assertEqual(cwd, self.root)
            command = tuple(args)
            status, output = 0, b""
            if command == ("--no-pager", "show-ref", "--verify", "--quiet", "refs/tags/v1.1.0"):
                status = 1 if tagged_files is None else 0
            elif command in (
                ("--no-pager", "--no-replace-objects", "rev-parse", "--show-prefix"),
                ("--no-pager", "--no-replace-objects", "check-ref-format", "refs/tags/v1.1.0"),
            ):
                pass
            elif command == ("--no-pager", "--no-replace-objects", "rev-parse", "--verify", "refs/tags/v1.1.0^{commit}"):
                status, output = (1, b"") if tagged_files is None else (0, ("b" * 40 + "\n").encode())
            elif command[:5] == ("--no-pager", "--no-replace-objects", "ls-tree", "-z", "b" * 40):
                assert tagged_files is not None
                name = command[-1].removeprefix(":(literal)")
                output = b"100644 blob " + b"c" * 40 + b"\t" + name.encode() + b"\0" if name in tagged_files else b""
            elif command[:4] == ("--no-pager", "--no-replace-objects", "cat-file", "blob"):
                assert tagged_files is not None
                output = tagged_files[command[-1].split(":", 1)[1]]
            else:
                raise AssertionError(f"unexpected Git operation: {command}")
            return subprocess.CompletedProcess(args, status, output, b"")

        self.stack.enter_context(patch("research_repo_tools.publication.run_git_bytes", side_effect=publication_git))
        output_path = self.root / "result.csv"
        output_path.write_bytes(b"previous artifact\r\n")
        prepared = load_publication(self.root, "publication.toml", artifacts_only=True)
        self.assertTrue(prepared.glob_checks)
        for name in ("revision", "apis.json", "bench.py", "input-shared.json"):
            self.assertEqual(dict(prepared.originals)[name], original[name])
        expected_files = {
            **{name: original[name] for name in ("revision", "apis.json", "bench.py", "input-shared.json")},
            "run.json": payload,
            "evidence.json": manifest,
            "result.csv": dict(prepared.outputs)["result.csv"],
        }
        tagged_files = dict(expected_files)
        self.assertEqual(load_publication(self.root, "publication.toml", artifacts_only=True).outputs, prepared.outputs)
        for name in ("revision", "run.json", "evidence.json", "result.csv"):
            tagged_files = {**expected_files, name: b"different tagged bytes\r\n"}
            with self.subTest(path=name):
                with self.assertRaisesRegex(ValueError, "exact publication artifact"):
                    load_publication(self.root, "publication.toml", artifacts_only=True)
                with self.assertRaisesRegex(ValueError, "exact publication artifact"):
                    publish_publication(prepared)
                self.assertEqual(output_path.read_bytes(), b"previous artifact\r\n")
        tagged_files = dict(expected_files)
        publish_publication(prepared)
        self.assertEqual(output_path.read_bytes(), expected_files["result.csv"])
        tagged_files = None
        source_inventory = 'current-sources=["revision", "apis.json"]\n'
        harness_inventory = 'current-harness=["bench.py", "input-shared.json"]\n'
        for policy in ("prepare", "existing"):
            for inventory in (source_inventory, harness_inventory, source_inventory + harness_inventory):
                (self.root / "publication.toml").write_text(
                    inventory + config.replace('tag-policy="prepare"', f'tag-policy="{policy}"'), encoding="utf-8", newline="\n"
                )
                with self.subTest(policy=policy, inventory=inventory), self.assertRaisesRegex(ValueError, "suite evidence derives current input checks"):
                    load_publication(self.root, "publication.toml", artifacts_only=True)
        (self.root / "input-shared.json").write_bytes(b"changed after acquisition")
        (self.root / "publication.toml").write_text(config, encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(ValueError, "differs from measured inputs"):
            load_publication(self.root, "publication.toml", artifacts_only=True)
        (self.root / "publication.toml").write_text(config.replace('tag-policy="prepare"', 'tag-policy="existing"'), encoding="utf-8", newline="\n")
        self.assertFalse(load_publication(self.root, "publication.toml", artifacts_only=True).glob_checks)

    def test_resume_reuses_complete_pair_and_recollects_interrupted_pair(self) -> None:
        selected = plan(self.log, ("shared", "later"))
        checkpoint = self.root / "checkpoint"

        def interrupt(command, args=(), **kwargs):
            if args[1:3] == ("later", "measure") and (kwargs["cwd"] / "revision").read_bytes() == b"new":
                raise KeyboardInterrupt()
            return run_command_live(command, args, **kwargs)

        with patch("research_repo_tools.common_measurement.run_command_live", side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, progress=self.quiet)
        self.assertTrue((checkpoint / "unit-shared.json").is_file())
        self.assertFalse((checkpoint / "unit-later.json").exists())
        original = (checkpoint / "unit-shared.json").read_bytes()
        evidence = measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, resume=True, progress=ProgressReporter(mode="none"))
        self.assertEqual((checkpoint / "unit-shared.json").read_bytes(), original)
        calls = self.log.read_text().splitlines()
        self.assertEqual(calls.count("shared measure old"), 1)
        self.assertEqual(calls.count("shared measure new"), 1)
        self.assertEqual(calls.count("later measure old"), 2)
        self.assertEqual(calls.count("later measure new"), 1)
        self.assertEqual(comparison_incompatibilities(evidence, ("baseline", "shared/case"), ("current", "shared/case")), ())
        self.assertTrue(comparison_incompatibilities(evidence, ("current", "shared/case"), ("current", "later/case")))
        validate_run_policy(evidence, selected, release=True)
        with patch("research_repo_tools.common_measurement.run_command_live", side_effect=AssertionError("offline rendering must not run native commands")):
            self.assertIn(b"shared/case", render_run(evidence))

    def test_rejected_resume_preserves_journal_and_detects_ownership_corruption_and_drift(self) -> None:
        selected = plan(self.log, ("shared",))
        checkpoint = self.root / "checkpoint"
        measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, progress=self.quiet)
        original = {path.name: path.read_bytes() for path in checkpoint.iterdir()}
        first = selected.suites[0]
        changed = SuiteMeasurementPlan((replace(first, contract="changed"),))
        with self.assertRaisesRegex(ValueError, "identity changed"):
            measure_pair(self.root, changed, PAIR, checkpoint=checkpoint, resume=True, progress=self.quiet)
        self.assertEqual({path.name: path.read_bytes() for path in checkpoint.iterdir()}, original)
        events = []
        with (
            patch("research_repo_tools.measurement.capture_host", return_value=replace(HOST, cpu="different")),
            self.assertRaisesRegex(ValueError, "checkpoint observations differ"),
        ):
            measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, resume=True, progress=ProgressReporter(mode="none", observer=events.append))
        self.assertEqual((events[-1]["event"], events[-1]["state"], events[-1]["suite"]), ("failed", "checkpoint", "shared"))
        self.assertEqual({path.name: path.read_bytes() for path in checkpoint.iterdir()}, original)
        (checkpoint / ".writer").mkdir()
        with self.assertRaisesRegex(ValueError, "writer"):
            measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, resume=True, progress=self.quiet)
        (checkpoint / ".writer").rmdir()
        for data in (b'{"unit":', original["unit-shared.json"].replace(b"shared-v1", b"changed-v1")):
            (checkpoint / "unit-shared.json").write_bytes(data)
            with self.assertRaises(ValueError):
                measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, resume=True, progress=self.quiet)
            self.assertEqual((checkpoint / "unit-shared.json").read_bytes(), data)

    def test_one_sided_disjoint_semantic_changes_and_presentation(self) -> None:
        selected = plan(self.log, ("new",))
        evidence = measure_pair(self.root, selected, PAIR, progress=self.quiet)
        run = validate_run_evidence(evidence)
        self.assertEqual(dict(run.phases)["baseline"].cases, ())
        self.assertIn(b"new/case", render_run(evidence))
        with self.assertRaisesRegex(ValueError, "no eligible"):
            validate_run_policy(evidence, selected, release=True)
        both = measure_pair(self.root, plan(self.log), PAIR, progress=self.quiet)
        layout = RunLayout(
            (
                SeriesSelection("shared/baseline", "Baseline", (("case", "shared/case"),)),
                SeriesSelection("shared/current", "Current", (("case", "shared/case"),)),
            ),
            (RunRow("case", "Case"),),
            "median",
            "ns",
            comparisons=(("shared/baseline", "shared/current"),),
        )
        self.assertIn("| 1 | 0 |", render_run_table(both, layout))
        self.assertIn(b"<svg", render_run_ratios(both, layout))
        raw = json.loads(both.payload)
        source = raw["units"][0]["observations"]["current"]["source"]
        source["context"]["environment"] = '{"FLAGS":"different"}'
        with self.assertRaises(ValueError):
            validate_run_evidence(replace(both, payload=deterministic_json(raw)))

    def test_schema5_backend_and_duplicate_ownership_rejected(self) -> None:
        selected = plan(self.log, ("shared",))
        first = selected.suites[0]
        with self.assertRaisesRegex(ValueError, "backend export"):
            replace(first, measurement=replace(first.measurement, backend="worktree"))
        duplicate = SuiteMeasurementPlan((first, replace(first, name="duplicate")))
        with self.assertRaisesRegex(ValueError, "duplicate benchmark ownership"):
            measure_pair(self.root, duplicate, PAIR, progress=self.quiet)
        self.assertNotIn(" measure ", self.log.read_text())

    def test_semantically_changed_same_native_id_is_absolute_only(self) -> None:
        first = plan(self.log, ("shared",)).suites[0]
        selected = SuiteMeasurementPlan(
            (replace(first, name="original", before="v1.1.0"), replace(first, name="changed", contract="changed-semantics", since="v1.1.0"))
        )
        evidence = measure_pair(self.root, selected, PAIR, progress=self.quiet)
        self.assertTrue(comparison_incompatibilities(evidence, ("baseline", "shared/case"), ("current", "shared/case")))
        with self.assertRaisesRegex(ValueError, "no eligible"):
            validate_run_policy(evidence, selected, release=True)

    def test_cli_checkpoint_offline_promotion_and_readme_retry(self) -> None:
        def toml(value):
            if isinstance(value, dict):
                return "{" + ", ".join(json.dumps(key) + "=" + toml(item) for key, item in value.items()) + "}"
            if isinstance(value, (tuple, list)):
                return "[" + ", ".join(toml(item) for item in value) + "]"
            return json.dumps(value)

        selected = plan(self.log)
        for name, data in self.revisions["v1.1.0"].items():
            (self.root / name).write_bytes(data)
        (self.root / "target").mkdir()
        (self.root / "measurement.toml").write_text("schema=5\nsuites=" + toml(selected.document()["suites"]) + "\n", encoding="utf-8", newline="\n")
        self.assertEqual(load_measurement(self.root, "measurement.toml"), selected)
        args = ["--root", str(self.root), "performance"]
        acquisition = [
            *args,
            "measure",
            "measurement.toml",
            "v1.1.0",
            "v1.0.0",
            "--payload",
            "target/run.json",
            "--manifest",
            "target/evidence.json",
            "--checkpoint",
            "target/checkpoint",
            "--progress",
            "json",
        ]
        events = io.StringIO()
        with patch("sys.stderr", events):
            self.assertEqual(main(acquisition), 0)
        status = [json.loads(line) for line in events.getvalue().splitlines()]
        self.assertIn("compilation", {item["state"] for item in status})
        self.assertIn("checkpoint", {item["state"] for item in status})
        self.assertTrue(any(item["compatible_eta_seconds"] is not None and item["compatible_eta_seconds"] > 0 for item in status))
        budget = next(item for item in status if item["event"] == "budget")
        inventory = {(item["suite"], item["phase"]): item for item in budget["inventory"]}
        self.assertEqual(inventory[("new", "baseline")]["total"], 0)
        self.assertEqual(inventory[("new", "current")]["total"], 1)
        self.assertEqual(sum(item["remaining"] for item in budget["inventory"]), budget["remaining"])
        self.assertTrue(all(item["remaining"] == 0 for item in status[-1]["inventory"]))
        checkpoint = self.root / "target/checkpoint"
        accepted_units = {path.name: path.read_bytes() for path in checkpoint.glob("unit-*.json")}
        measured = [line for line in self.log.read_text().splitlines() if " measure " in line]
        with patch("sys.stderr", io.StringIO()):
            self.assertEqual(main([*acquisition, "--resume"]), 0)
        self.assertEqual({path.name: path.read_bytes() for path in checkpoint.glob("unit-*.json")}, accepted_units)
        self.assertEqual([line for line in self.log.read_text().splitlines() if " measure " in line], measured)
        (self.root / "report.toml").write_bytes(
            b'schema=2\ncurrent="target/current.md"\narchive="target/history"\ntitle="Fixture"\nmeasurement="measurement.toml"\nrelease=true\n'
        )
        with patch("research_repo_tools.common_measurement.run_command_live", side_effect=AssertionError("retry must not acquire")):
            self.assertEqual(main([*args, "promote", "report.toml", "--payload", "target/run.json", "--manifest", "target/evidence.json"]), 0)
            self.assertEqual(main([*args, "promote", "report.toml"]), 0)
        (self.root / "README.md").write_bytes(b"head\r\n<!-- BEGIN -->\r\nold\r\n<!-- END -->\r\ntail\r\n")
        (self.root / "releases.txt").write_bytes(b"current=v1.1.0\nprevious=v1.0.0\n")
        publication = "\n".join(
            [
                "schema=2",
                'document="README.md"',
                'begin="<!-- BEGIN -->"',
                'end="<!-- END -->"',
                'archive="target/history"',
                'measurement="measurement.toml"',
                "release=true",
                'unit="ns"',
                'statistic="median"',
                'rows=[{key="case", label="Case"}]',
                'series=[{name="shared/baseline",label="Before",rows={case="shared/case"}}, {name="shared/current",label="After",rows={case="shared/case"}}]',
                'comparisons=[{numerator="shared/baseline",denominator="shared/current"}]',
                'references=[{path="Cargo.toml",pattern=\'^version="(?P<value>[^"]+)"$\',source="version"},{path="releases.txt",pattern=\'^current=(?P<value>.*)$\',source="tag"},{path="releases.txt",pattern=\'^previous=(?P<value>.*)$\',source="previous-tag"}]',
                "[provenance.baseline]",
                'release="v1.0.0"',
                'revision="' + "a" * 40 + '"',
                "[provenance.current]",
                'release="v1.1.0"',
                'revision="' + "b" * 40 + '"',
            ]
        )
        (self.root / "publication.toml").write_text(publication + "\n", encoding="utf-8", newline="\n")
        with patch("research_repo_tools.common_measurement.run_command_live", side_effect=AssertionError("README retry must not acquire")):
            self.assertEqual(main([*args, "publish", "publication.toml"]), 0)
            self.assertEqual(main([*args, "publish", "publication.toml"]), 0)
        before = (self.root / "README.md").read_bytes()
        self.assertTrue(before.startswith(b"head\r\n"))
        self.assertTrue(before.endswith(b"tail\r\n"))
        (self.root / "input-shared.json").write_bytes(b"changed")
        self.assertNotEqual(main([*args, "publish", "publication.toml"]), 0)
        self.assertEqual((self.root / "README.md").read_bytes(), before)

    def test_retained_policy_common_harness_and_series_are_validated(self) -> None:
        selected = plan(self.log, ("shared",))
        evidence = measure_pair(self.root, selected, PAIR, progress=self.quiet)
        for field, value in (("sampling-policy", "{}"), ("original-source-sha256", "invalid")):
            raw = json.loads(evidence.payload)
            raw["units"][0]["observations"]["current"]["source"]["context"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_suite_run(replace(evidence, payload=deterministic_json(raw)))
        raw = json.loads(evidence.payload)
        raw["units"][0]["observations"]["current"]["source"]["harness_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "common suite provenance"):
            parse_suite_run(replace(evidence, payload=deterministic_json(raw)))
        first = selected.suites[0]
        empty = replace(first, measurement=replace(first.measurement, series=(DiscoveredSeries("missing", "current", ("missing/*",)),)))
        self.log.write_bytes(b"")
        with self.assertRaises(ValueError):
            measure_pair(self.root, SuiteMeasurementPlan((empty,)), PAIR, progress=self.quiet)
        self.assertNotIn(" measure ", self.log.read_text())
        self.assertNotIn(" gate ", self.log.read_text())
        raw = json.loads(evidence.payload)
        raw["declaration"]["suites"][0]["measurement"]["series"][0]["select"]["include"] = ["missing/*"]
        for phase in ("baseline", "current"):
            raw["units"][0]["observations"][phase]["source"]["context"]["measurement-declaration"] = json.dumps(
                raw["declaration"]["suites"][0]["measurement"], ensure_ascii=False, sort_keys=True
            )
        with self.assertRaises(ValueError):
            parse_suite_run(replace(evidence, payload=deterministic_json(raw)))

    def test_partial_successful_child_and_environment_change_reject_resume(self) -> None:
        selected = plan(self.log, ("shared", "later"))
        checkpoint = self.root / "checkpoint"
        events = []

        def partial(command, args=(), **kwargs):
            result = run_command_live(command, args, **kwargs)
            if args[1:3] == ("later", "measure") and (kwargs["cwd"] / "revision").read_bytes() == b"new":
                (kwargs["cwd"] / "target/criterion/later/new/sample.json").write_bytes(b"{")
            return result

        with patch("research_repo_tools.common_measurement.run_command_live", side_effect=partial), self.assertRaises(ValueError):
            measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, progress=ProgressReporter(mode="none", observer=events.append))
        self.assertEqual(events[-1]["event"], "failed")
        self.assertEqual(events[-1]["phase"], "current")
        self.assertEqual(events[-1]["remaining"], 1)
        original = (checkpoint / "unit-shared.json").read_bytes()
        with patch.dict("os.environ", {"RRT_CHECKPOINT_FIXTURE": "changed"}), self.assertRaisesRegex(ValueError, "environment-sha256"):
            measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, resume=True, progress=self.quiet)
        self.assertEqual((checkpoint / "unit-shared.json").read_bytes(), original)
        with (
            patch("research_repo_tools.source_exports._git", side_effect=git_model({"v0.1.0": sources((), "unused"), **self.revisions})),
            self.assertRaisesRegex(ValueError, "identity changed"),
        ):
            measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, resume=True, progress=self.quiet)
        self.assertEqual((checkpoint / "unit-shared.json").read_bytes(), original)

    def test_explicit_inventory_compatibility_is_preserved(self) -> None:
        first = plan(self.log, ("shared",), mode="independent").suites[0]
        policy = first.measurement.baseline.policy
        assert isinstance(policy, DiscoveryPolicy)
        policy = replace(policy, discovery=replace(policy.discovery, suites=(replace(policy.discovery.suites[0], name="alternate-list"),)))
        measured = replace(first.measurement, baseline=replace(first.measurement.baseline, policy=policy))
        ordinary = SuiteMeasurementPlan((replace(first, measurement=measured),))
        evidence = measure_pair(self.root, ordinary, PAIR, progress=self.quiet)
        self.assertEqual(comparison_incompatibilities(evidence, ("baseline", "shared/case"), ("current", "shared/case")), ())
        strict = SuiteMeasurementPlan(
            (
                replace(
                    first,
                    measurement=replace(
                        measured, measurement=replace(measured.measurement, compatible=(*measured.measurement.compatible, "context.discovery-inventory"))
                    ),
                ),
            )
        )
        evidence = measure_pair(self.root, strict, PAIR, progress=self.quiet)
        self.assertTrue(
            any(
                "context.discovery-inventory" in reason
                for reason in comparison_incompatibilities(evidence, ("baseline", "shared/case"), ("current", "shared/case"))
            )
        )

    def test_different_size_or_compilation_flags_are_absolute_only(self) -> None:
        first = plan(self.log, ("shared",)).suites[0]
        for boundary in ("measurement", "compilation"):
            with self.subTest(boundary=boundary):
                if boundary == "measurement":
                    measured = replace(first.measurement, current=replace(first.measurement.current, command=(*first.measurement.current.command, "--size=5")))
                    suite = replace(first, measurement=measured)
                else:
                    compilation = dict(first.compilation)
                    compilation["current"] = (*compilation["current"], "--features=other")
                    suite = replace(first, compilation=tuple(compilation.items()))
                selected = SuiteMeasurementPlan((suite,))
                events = []
                evidence = measure_pair(self.root, selected, PAIR, progress=ProgressReporter(mode="none", observer=events.append))
                accepted = [event for event in events if event["event"] == "validated"]
                self.assertEqual(accepted[0]["remaining"], 1)
                self.assertEqual(accepted[0]["unknown_cases"], 1)
                self.assertIsNone(accepted[0]["eta_seconds"])
                inventory = {item["phase"]: item for item in accepted[0]["inventory"]}
                self.assertEqual(inventory["baseline"]["completed"], 1)
                self.assertEqual(inventory["current"]["remaining"], 1)
                self.assertEqual(len(parse_suite_run(evidence).observations), 2)
                reasons = comparison_incompatibilities(evidence, ("baseline", "shared/case"), ("current", "shared/case"))
                self.assertTrue(any("command" in reason for reason in reasons))
                with self.assertRaisesRegex(ValueError, "ineligible"):
                    validate_run_policy(evidence, selected, release=True)

    def test_working_tree_availability_uses_original_package_version(self) -> None:
        original = self.revisions["v1.1.0"]
        for name, data in original.items():
            (self.root / name).write_bytes(data)
        (self.root / "revision").write_bytes(b"working")
        manifest = self.root / "Cargo.toml"
        manifest.write_bytes(original["Cargo.toml"].replace(b"1.1.0", b"1.2.0"))
        selected = plan(self.log, ("new",))
        with patch("research_repo_tools.source_exports.select_files", return_value=tuple(sorted(original))):
            with self.assertRaisesRegex(ValueError, "original package version"):
                measure_pair(self.root, selected, PAIR, working_tree=True, progress=self.quiet)
            self.assertFalse(self.log.exists())
            manifest.write_bytes(original["Cargo.toml"])
            evidence = measure_pair(self.root, selected, PAIR, working_tree=True, progress=self.quiet)
        self.assertEqual([(name, phase) for name, phase, _, _ in parse_suite_run(evidence).observations], [("new", "current")])
        current = dict(dict(evidence.sources)["current"].context)
        self.assertEqual(current["mode"], "working-tree")
        self.assertTrue(json.loads(current["source-export"])["dirty"])
        self.assertIn("new measure working", self.log.read_text().splitlines())

    def test_complete_plan_progress_accepts_each_phase(self) -> None:
        selected = plan(self.log, ("shared",)).suites[0].measurement
        events = []
        evidence = measure_pair(self.root, selected, PAIR, progress=ProgressReporter(mode="none", observer=events.append))
        accepted = [event for event in events if event["event"] == "validated"]
        self.assertEqual([(event["phase"], event["completed"], event["remaining"]) for event in accepted], [("baseline", 1, 1), ("current", 2, 0)])
        self.assertIsNone(accepted[0]["eta_seconds"])
        self.assertEqual(len(validate_run_evidence(evidence).phases), 2)

    def test_retention_allows_historical_path_changes_but_protects_inputs(self) -> None:
        revisions = {tag: dict(files) for tag, files in self.revisions.items()}
        revisions["v1.0.0"].update({"legacy": b"former file", "Case.txt": b"old casing"})
        revisions["v1.1.0"].update({"legacy/item": b"new file", "case.txt": b"new casing"})
        for name, data in revisions["v1.1.0"].items():
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        (self.root / "measurement.toml").write_bytes(b"fixture declaration")
        selected = plan(self.log, ("shared",))
        with (
            patch("research_repo_tools.source_exports._git", side_effect=git_model(revisions)),
            patch("research_repo_tools.performance_acquisition.load_measurement", return_value=selected),
        ):
            measure_and_retain(self.root, "measurement.toml", PAIR, payload="target/run.json", manifest="target/evidence.json", progress=self.quiet)
            retained = parse_evidence((self.root / "target/run.json").read_bytes(), (self.root / "target/evidence.json").read_bytes())
            self.assertEqual(len(parse_suite_run(retained).observations), 2)
            with (
                patch("research_repo_tools.common_measurement.run_command_live", side_effect=AssertionError("path rejection must precede native commands")),
                self.assertRaisesRegex(ValueError, "duplicate target paths"),
            ):
                measure_and_retain(self.root, "measurement.toml", PAIR, payload="case.txt", manifest="target/rejected.json", progress=self.quiet)
        self.assertEqual((self.root / "case.txt").read_bytes(), b"new casing")
        self.assertEqual((self.root / "legacy/item").read_bytes(), b"new file")
        self.assertFalse((self.root / "target/rejected.json").exists())

    def test_missing_observation_fields_raise_domain_errors(self) -> None:
        evidence = measure_pair(self.root, plan(self.log, ("shared",)), PAIR, progress=self.quiet)
        for field in ("host", "command", "gate-command", "environment", "source-inventory", "harness-inventory", "original-source-inventory"):
            raw = json.loads(evidence.payload)
            del raw["units"][0]["observations"]["current"]["source"]["context"][field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, f"missing {field}"):
                parse_suite_run(replace(evidence, payload=deterministic_json(raw)))

    def test_envelope_modes_agree_with_original_receipts_including_absent_sides(self) -> None:
        evidence = measure_pair(self.root, plan(self.log, ("new",)), PAIR, progress=self.quiet)
        for phase in ("baseline", "current"):
            for mode in (None, "working-tree"):
                sources = dict(evidence.sources)
                context = dict(sources[phase].context)
                if mode is None:
                    del context["mode"]
                else:
                    context["mode"] = mode
                sources[phase] = replace(sources[phase], context=tuple(context.items()))
                with self.subTest(phase=phase, mode=mode), self.assertRaisesRegex(ValueError, "source mode"):
                    parse_suite_run(replace(evidence, sources=tuple(sources.items())))

    def test_unavailable_csv_provenance_does_not_depend_on_row_order(self) -> None:
        evidence = measure_pair(self.root, plan(self.log, ("shared",)), PAIR, progress=self.quiet)
        rows = (RunRow("present", "Present"), RunRow("absent", "Absent"))
        layout = RunLayout(
            (SeriesSelection("shared/current", "Current", (("present", "shared/case"),)),),
            rows,
            "median",
            "ns",
            missing="unavailable",
        )
        first = {row["row"]: row for row in csv.DictReader(io.StringIO(serialize_run_csv(evidence, layout).decode()))}
        second = {row["row"]: row for row in csv.DictReader(io.StringIO(serialize_run_csv(evidence, replace(layout, rows=tuple(reversed(rows)))).decode()))}
        self.assertEqual(first, second)
        self.assertEqual(first["absent"]["source_sha256"], dict(evidence.sources)["current"].source_sha256)
        self.assertEqual(first["absent"]["harness_sha256"], "")
        self.assertEqual(first["present"]["status"], "available")

    def test_full_report_rejects_nonfinite_derived_ratios(self) -> None:
        evidence = measure_pair(self.root, plan(self.log, ("shared",)), PAIR, progress=self.quiet)
        raw = json.loads(evidence.payload)
        for phase, point in (("baseline", 1e308), ("current", 1e-308)):
            case = raw["units"][0]["observations"][phase]["sample"]["cases"][0]
            estimates = {
                statistic: {"point_estimate": point, "confidence_interval": {"confidence_level": 0.95, "lower_bound": point, "upper_bound": point}}
                for statistic in ("mean", "median")
            }
            case["estimates"] = base64.b64encode(deterministic_json(estimates)).decode("ascii")
            case["sample"] = base64.b64encode(deterministic_json({"iters": [1, 1], "times": [point, point]})).decode("ascii")
        retained = replace(evidence, payload=deterministic_json(raw))
        self.assertEqual(len(parse_suite_run(retained).observations), 2)
        with self.assertRaisesRegex(ValueError, "finite"):
            render_run(retained)


class TestProgress(unittest.TestCase):
    def test_checkpoint_handle_expires_with_its_exclusive_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "checkpoint"
            with open_checkpoint(root, {"fixture": "one"}, ("shared",), resume=False) as expired:
                self.assertEqual(expired.read(), {})
            with open_checkpoint(root, {"fixture": "one"}, ("shared",), resume=True) as current:
                with self.assertRaisesRegex(ValueError, "closed"):
                    expired.accept("shared", {"fixture": "expired"})
                with self.assertRaisesRegex(ValueError, "closed"):
                    expired.read()
                self.assertEqual(current.read(), {})
                current.accept("shared", {"fixture": "current"})
                self.assertEqual(current.read(), {"shared": {"fixture": "current"}})

    def test_unknown_zero_count_work_is_rejected_without_mutation(self) -> None:
        events = []
        reporter = ProgressReporter(mode="none", observer=events.append)
        reporter.suite, reporter.phase = "suite", "current"
        reporter.budget({"known": 1}, inventory={("suite", "current"): 1})
        with self.assertRaisesRegex(ValueError, "inventory"):
            reporter.accept("unknown", 0, 0)
        reporter.emit("snapshot", force=True)
        self.assertEqual(events[-1]["completed"], 0)
        self.assertEqual(events[-1]["remaining"], 1)
        reporter.accept("known", 1, 1)
        self.assertEqual(events[-1]["remaining"], 0)

    def test_fake_clock_counts_unknown_eta_cadence_and_json_contract(self) -> None:
        now = [0.0]
        output = io.StringIO()
        reporter = ProgressReporter(mode="json", stream=output, clock=lambda: now[0], cadence=5)
        reporter.budget({"shared": 4, "unseen": 2}, 30)
        for _ in range(100):
            reporter.emit("heartbeat")
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        reporter.accept("shared", 2, 10)
        event = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(event["completed"], 2)
        self.assertIsNone(event["eta_seconds"])
        reporter.accept("unseen", 2, 20)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["eta_seconds"], 10)
        now[0] = 6
        reporter.emit("heartbeat")
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["elapsed_seconds"], 6)
        with self.assertRaisesRegex(ValueError, "exceeds"):
            reporter.accept("shared", 3, 1)
        with self.assertRaises(KeyboardInterrupt), reporter.stage("acquisition", suite="shared", phase="current"):
            raise KeyboardInterrupt()
        last = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual((last["event"], last["state"], last["remaining"]), ("interrupted", "acquisition", 2))

    def test_native_failure_partial_files_never_advance_completion(self) -> None:
        events = []
        reporter = ProgressReporter(mode="none", observer=events.append)
        reporter.budget({"work": 2})
        with self.assertRaises(subprocess.CalledProcessError), reporter.stage("acquisition", phase="current"):
            run_command_live(sys.executable, ("-c", "raise SystemExit(3)"))
        self.assertEqual(events[-1]["event"], "failed")
        self.assertEqual(events[-1]["completed"], 0)
        self.assertEqual(events[-1]["remaining"], 2)

    def test_checkpoint_path_and_interrupted_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "checkpoint"
            with open_checkpoint(root, {"fixture": "one"}, ("shared",), resume=False) as journal:
                with self.assertRaisesRegex(ValueError, "writer"):
                    with open_checkpoint(root, {"fixture": "one"}, ("shared",), resume=True):
                        self.fail("concurrent owner accepted")
                with patch("research_repo_tools.files._replace_path", side_effect=OSError("interrupted commit")), self.assertRaises(OSError):
                    journal.accept("shared", {"fixture": True})
                self.assertEqual(journal.read(), {})


if __name__ == "__main__":
    unittest.main()
