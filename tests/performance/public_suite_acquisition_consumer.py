"""Installed availability/resume contracts with native, tiny synthetic workloads."""

import base64
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import ExitStack, chdir, contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any, get_type_hints, override
from unittest.mock import patch

from research_repo_tools.archives import ArchiveLimits, extract_archive
from research_repo_tools.benchmark_reuse import ReusePolicy, parse_reuse_policy, plan_reuse_archive, serialize_reuse_policy, verify_reuse_archive
from research_repo_tools.candidate_publication import plan_candidate_publication
from research_repo_tools.candidate_reuse import (
    CandidatePolicy,
    acquisition_assertion_digest,
    parse_candidate_policy,
    plan_candidate_archive,
    serialize_candidate_policy,
    verify_candidate_archive,
    verify_candidate_release,
)
from research_repo_tools.cli import main, parser
from research_repo_tools.common_measurement import DiscoveredSeries, DiscoveryPolicy, parse_plan, validate_run_policy
from research_repo_tools.complete_runs import render_run, run_identity, validate_run_evidence
from research_repo_tools.evidence import deterministic_json, parse_evidence, serialize_evidence
from research_repo_tools.host_metadata import HostMetadata
from research_repo_tools.measurement import load_measurement, measure_pair
from research_repo_tools.measurement_checkpoints import open_checkpoint
from research_repo_tools.performance_acquisition import measure_and_retain
from research_repo_tools.performance_progress import ProgressReporter
from research_repo_tools.process import format_exception_diagnostics, run_command, run_command_live
from research_repo_tools.publication import MarkerPair, plan_publication, publish_publication
from research_repo_tools.publication_config import load_publication
from research_repo_tools.release_assets import ReleaseTarget, require_release_checkout
from research_repo_tools.release_evidence import EvidencePolicy
from research_repo_tools.release_pairs import ReleasePair
from research_repo_tools.release_validation import ValidationPolicy
from research_repo_tools.run_presentation import RunLayout, RunRow, SeriesSelection, render_run_ratios, render_run_table, serialize_run_csv
from research_repo_tools.run_reports import load_run_report_plan
from research_repo_tools.scheduled_runs import ScheduledRun, parse_scheduled_run, select_trial
from research_repo_tools.source_exports import export_source
from research_repo_tools.suite_measurement import AcquisitionSuite, SuiteMeasurementPlan
from research_repo_tools.suite_runs import SuiteRun, comparison_incompatibilities, parse_suite_run
from research_repo_tools.trial_schedule import TrialSchedule, parse_schedule

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
            return ((tag if tag in keys.values() else keys[tag]) + "\n").encode()
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

    def reuse_fixture(self, *, mode="common", working_tree=False):
        selected = plan(self.log, mode=mode)
        selected = SuiteMeasurementPlan(
            tuple(
                replace(
                    suite,
                    measurement=replace(
                        suite.measurement,
                        measurement=replace(suite.measurement.measurement, context=(("repository", "example/library"), ("acquisition-owner", "local"))),
                    ),
                )
                for suite in selected.suites
            )
        )
        evidence = measure_pair(self.root, selected, PAIR, working_tree=working_tree, progress=self.quiet)
        # This fixture is the trusted acquisition owner. Snapshot its handoff
        # before any candidate corruption; production consumers cannot derive
        # their acceptance policy from an untrusted candidate archive.
        run = parse_suite_run(evidence)
        policy = ReusePolicy(
            ReleaseTarget("example/library", "v1.1.0", 7, "b" * 40),
            "local",
            run_identity(evidence),
            selected,
            tuple((suite, phase, source) for suite, phase, _, source in run.observations),
        )
        self.write_reuse(evidence, policy)
        return evidence, policy

    def write_reuse(self, evidence, policy):
        payload, manifest = serialize_evidence(evidence)
        (self.root / "run.json").write_bytes(payload)
        (self.root / "evidence.json").write_bytes(manifest)
        (self.root / "reuse-policy.json").write_bytes(serialize_reuse_policy(policy))

    def reuse_plan(self, **kwargs):
        return plan_reuse_archive(self.root, "run.json", "evidence.json", "reuse-policy.json", "archive.tar.gz", **kwargs)

    def test_reuse_renders_and_packages_one_acquisition_with_exact_provenance(self) -> None:
        evidence, policy = self.reuse_fixture()
        # Preserve a valid noncanonical envelope, including CRLF and whitespace.
        payload, manifest = serialize_evidence(evidence)
        manifest = b" \r\n" + manifest.replace(b"\n", b"\r\n")
        evidence = parse_evidence(payload, manifest)
        policy = replace(policy, identity=run_identity(evidence), comparisons=((("baseline", "shared/case"), ("current", "shared/case")),))
        self.write_reuse(evidence, policy)
        before = self.log.read_bytes()
        with (
            patch("research_repo_tools.common_measurement.run_command_live", side_effect=AssertionError("no native rerun")),
            patch("research_repo_tools.measurement.capture_host", side_effect=AssertionError("no host recapture")),
            patch("research_repo_tools.source_exports.capture_source", side_effect=AssertionError("no source recapture")),
        ):
            candidate = self.reuse_plan()
            self.assertFalse((self.root / "archive.tar.gz").exists())
            publish_publication(candidate)
            retained = verify_reuse_archive(self.root / "archive.tar.gz", parse_reuse_policy(serialize_reuse_policy(policy)))
            self.assertEqual(serialize_evidence(retained), (payload, manifest))
            self.assertEqual(publish_publication(self.reuse_plan()), ())
            report = render_run(retained)
            self.assertIn(policy.identity.encode(), report)
            self.assertTrue(report.startswith(b"<!-- research-repo-tools/run-report/v3 -->"))
            self.assertLessEqual(max(map(len, report.decode().splitlines())), 160)
            original = b"intro\r\n<!-- begin -->\r\nold\r\n<!-- end -->\r\nend\xff"
            (self.root / "README.md").write_bytes(original)
            readme = plan_publication(
                self.root,
                "README.md",
                MarkerPair("<!-- begin -->", "<!-- end -->"),
                report.decode(),
                inputs={"run.json": payload, "evidence.json": manifest},
            )
            publish_publication(readme)
            self.assertTrue((self.root / "README.md").read_bytes().endswith(b"<!-- end -->\r\nend\xff"))
        self.assertEqual(self.log.read_bytes(), before)
        extract_archive(self.root / "archive.tar.gz", self.root / "extracted")
        receipt = json.loads((self.root / "extracted/reuse.json").read_bytes())
        self.assertEqual(receipt["target"]["commit"], "b" * 40)
        self.assertEqual({item["suite"] for item in receipt["projection"]}, {"shared", "new"})
        for _, phase, sample, _ in parse_suite_run(evidence).observations:
            if phase == "current":
                for case in sample.cases:
                    for name, content in (
                        ("benchmark.json", case.benchmark),
                        ("estimates.json", case.estimates),
                        ("sample.json", case.sample),
                        *case.raw_files,
                    ):
                        self.assertEqual((self.root / f"extracted/criterion/{case.storage_path}/new/{name}").read_bytes(), content)
        self.assertFalse((self.root / "extracted/criterion/shared/v1.1.0").exists())

    def test_reuse_rejects_independent_assertion_drift_and_malformed_evidence(self) -> None:
        evidence, policy = self.reuse_fixture()
        first = policy.observations[0]
        context = dict(first[2].context)
        changed_plan = replace(policy.measurement.suites[0], contract="another-contract")
        for change in (
            replace(policy, identity="0" * 64),
            replace(policy, target=replace(policy.target, commit="c" * 40)),
            replace(policy, target=replace(policy.target, tag="v1.2.0")),
            replace(policy, target=replace(policy.target, repository="other/library")),
            replace(policy, acquisition_owner="github-actions"),
            replace(policy, observations=policy.observations[1:]),
            replace(policy, observations=((first[0], first[1], replace(first[2], source_sha256="0" * 64)), *policy.observations[1:])),
            replace(
                policy,
                observations=((first[0], first[1], replace(first[2], context=tuple({**context, "tool.python": '"other"'}.items()))), *policy.observations[1:]),
            ),
            replace(policy, measurement=SuiteMeasurementPlan((changed_plan, *policy.measurement.suites[1:]))),
        ):
            self.write_reuse(evidence, change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.reuse_plan()
            self.assertFalse((self.root / "archive.tar.gz").exists())
        for field, value in (("cases", []), ("policy", {})):
            raw = json.loads(evidence.payload)
            raw["units"][0]["observations"]["current"]["sample"][field] = value
            self.write_reuse(replace(evidence, payload=deterministic_json(raw)), policy)
            with self.assertRaises(ValueError):
                self.reuse_plan()
        for key, value in (("gate-status", "failed"), ("repository", "other/library")):
            raw = json.loads(evidence.payload)
            raw["units"][0]["observations"]["current"]["source"]["context"][key] = value
            self.write_reuse(replace(evidence, payload=deterministic_json(raw)), policy)
            with self.assertRaises(ValueError):
                self.reuse_plan()
        self.write_reuse(replace(evidence, payload_schema="research-repo-tools/complete-run/v3"), policy)
        with self.assertRaisesRegex(ValueError, "v4"):
            self.reuse_plan()

    def test_reuse_revalidates_inputs_outputs_limits_and_rolls_back(self) -> None:
        evidence, policy = self.reuse_fixture()
        for name in ("run.json", "evidence.json", "reuse-policy.json", "archive.tar.gz"):
            candidate = self.reuse_plan()
            path = self.root / name
            before = path.read_bytes() if path.exists() else None
            path.write_bytes(b"concurrent replacement")
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "changed"):
                publish_publication(candidate)
            self.assertEqual(path.read_bytes(), b"concurrent replacement")
            if before is None:
                path.unlink()
            else:
                path.write_bytes(before)
        with patch("research_repo_tools.files._replace_path", side_effect=OSError("interrupted")), self.assertRaises(OSError):
            publish_publication(self.reuse_plan())
        self.assertFalse((self.root / "archive.tar.gz").exists())
        for output in ("run.json", "RUN.JSON", "run.json/nested", "../escaped.tar.gz"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                plan_reuse_archive(self.root, "run.json", "evidence.json", "reuse-policy.json", output)
        for limits in (ArchiveLimits(members=2), ArchiveLimits(content_bytes=20), ArchiveLimits(archive_bytes=20)):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                self.reuse_plan(limits=limits)
        (self.root / "archive.tar.gz").write_bytes(b"previous immutable archive")
        with self.assertRaisesRegex(ValueError, "immutable output differs"):
            self.reuse_plan()
        self.assertEqual((self.root / "archive.tar.gz").read_bytes(), b"previous immutable archive")
        self.assertEqual((self.root / "run.json").read_bytes(), evidence.payload)
        self.assertEqual(serialize_reuse_policy(parse_reuse_policy((self.root / "reuse-policy.json").read_bytes())), serialize_reuse_policy(policy))

    def test_reuse_verifier_rejects_tampering_extra_members_and_unsafe_paths(self) -> None:
        _, policy = self.reuse_fixture()
        publish_publication(self.reuse_plan())
        archive = self.root / "archive.tar.gz"
        with tarfile.open(archive) as source:
            files = {}
            for member in source.getmembers():
                stream = source.extractfile(member)
                assert stream is not None
                files[member.name] = stream.read()
        for name, content in (
            ("reuse.json", b"{}"),
            ("criterion/shared/new/sample.json", b"{}"),
            ("extra", b"extra"),
            ("../escape", b"unsafe"),
            ("EVIDENCE/extra", b"alias"),
        ):
            changed = {**files, name: content}
            candidate = self.root / "changed.tar.gz"
            with tarfile.open(candidate, "w:gz") as output:
                for path, data in changed.items():
                    member = tarfile.TarInfo(path)
                    member.size = len(data)
                    output.addfile(member, io.BytesIO(data))
            with tarfile.open(candidate) as source:
                self.assertIn(name, source.getnames())
            with self.subTest(name=name), self.assertRaises(ValueError):
                verify_reuse_archive(candidate, policy)
        with self.assertRaises(ValueError):
            verify_reuse_archive(archive, replace(policy, identity="0" * 64))

    def test_reuse_preserves_absolute_cross_host_results_but_rejects_requested_ratios(self) -> None:
        evidence, policy = self.reuse_fixture(mode="independent")
        raw = json.loads(evidence.payload)
        for unit in raw["units"]:
            if "current" in unit["observations"]:
                context = unit["observations"]["current"]["source"]["context"]
                host = json.loads(context["host"])
                host["cpu"] = "different collection host"
                context["host"] = json.dumps(host)
        evidence = replace(evidence, payload=deterministic_json(raw))
        policy = replace(
            policy, identity=run_identity(evidence), observations=tuple((s, p, source) for s, p, _, source in parse_suite_run(evidence).observations)
        )
        self.write_reuse(evidence, policy)
        publish_publication(self.reuse_plan())
        self.assertEqual(serialize_evidence(verify_reuse_archive(self.root / "archive.tar.gz", policy)), serialize_evidence(evidence))
        for pair in ((("baseline", "shared/case"), ("current", "shared/case")), (("current", "new/case"), ("current", "shared/case"))):
            with self.subTest(pair=pair), self.assertRaisesRegex(ValueError, "ineligible"):
                verify_reuse_archive(self.root / "archive.tar.gz", replace(policy, comparisons=(pair,)))

    def test_reuse_rejects_prospective_source_even_with_matching_release_label(self) -> None:
        original = self.revisions["v1.1.0"]
        for name, data in original.items():
            (self.root / name).write_bytes(data)
        (self.root / "revision").write_bytes(b"prospective")
        with patch("research_repo_tools.source_exports.select_files", return_value=tuple(sorted(original))):
            self.reuse_fixture(working_tree=True)
        with self.assertRaisesRegex(ValueError, "working-tree"):
            self.reuse_plan()
        self.assertFalse((self.root / "archive.tar.gz").exists())

    def test_reuse_rejects_cross_suite_storage_aliases(self) -> None:
        evidence, policy = self.reuse_fixture()
        for storage in ("shared", "SHARED"):
            raw = json.loads(evidence.payload)
            raw["units"][1]["observations"]["current"]["sample"]["cases"][0]["storage_path"] = storage
            changed = replace(evidence, payload=deterministic_json(raw))
            self.write_reuse(changed, replace(policy, identity=run_identity(changed)))
            with self.subTest(storage=storage), self.assertRaises(ValueError):
                self.reuse_plan()
            self.assertFalse((self.root / "archive.tar.gz").exists())

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
                    suite = destination.name.rsplit("-", 1)[0]
                    self.assertTrue(all(not path.exists() for name, path in exports.items() if name.rsplit("-", 1)[0] != suite))
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

    def test_late_gate_failure_precedes_all_timing_and_releases_exports(self) -> None:
        selected = plan(self.log, ("shared", "later"))
        first, later = selected.suites
        failing = replace(later.measurement.current, gate=(sys.executable, "-c", "raise SystemExit(1)"))
        selected = SuiteMeasurementPlan((first, replace(later, measurement=replace(later.measurement, current=failing))))
        exports = []

        @contextmanager
        def tracked_export(capture, destination):
            exports.append(destination)
            with export_source(capture, destination) as directory:
                yield directory

        events = []
        with patch("research_repo_tools.suite_measurement.export_source", side_effect=tracked_export), self.assertRaisesRegex(ValueError, "gate failed"):
            measure_pair(self.root, selected, PAIR, progress=ProgressReporter(mode="none", observer=events.append))
        rejected = [event for event in events if event["suite"] == "later" and event["phase"] == "current" and event["state"] == "gate"]
        self.assertEqual([event["event"] for event in rejected], ["started", "failed"])
        self.assertEqual(events[-1], rejected[-1])
        self.assertTrue(all(event["completed"] == 0 and event["remaining"] is None for event in events))
        self.assertIn("shared gate new", self.log.read_text())
        self.assertNotIn(" measure ", self.log.read_text())
        self.assertTrue(all(not path.exists() for path in exports))

    def test_gate_interruption_preserves_discovered_budget_and_empty_checkpoint(self) -> None:
        selected = plan(self.log, ("shared",))
        for phase in ("baseline", "current"):
            with self.subTest(phase=phase):
                self.log.write_bytes(b"")
                events = []
                reporter = ProgressReporter(mode="none", observer=events.append)
                checkpoint = self.root / f"checkpoint-{phase}"
                interruption = KeyboardInterrupt("gate interrupted")

                def interrupt(command, args=(), **kwargs):
                    result = run_command_live(command, args, **kwargs)
                    # The second preparation has a discovered budget but has
                    # not accepted a sample or checkpoint unit yet.
                    if "gate" in args and reporter.total is not None and reporter.phase == phase:
                        raise interruption
                    return result

                with patch("research_repo_tools.common_measurement.run_command_live", side_effect=interrupt), self.assertRaises(KeyboardInterrupt) as caught:
                    measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, progress=reporter)
                self.assertIs(caught.exception, interruption)
                last = events[-1]
                self.assertEqual((last["event"], last["state"], last["suite"], last["phase"]), ("interrupted", "gate", "shared", phase))
                self.assertEqual((last["completed"], last["remaining"], last["total"]), (0, 2, 2))
                self.assertEqual([item["remaining"] for item in last["inventory"]], [1, 1])
                rejected = [
                    event["event"]
                    for event in events
                    if event["state"] == "gate" and event["phase"] == phase and event["total"] is not None and event["event"] != "budget"
                ]
                self.assertEqual(rejected, ["started", "interrupted"])
                self.assertNotIn(" measure ", self.log.read_text())
                self.assertEqual(list(checkpoint.glob("unit-*.json")), [])
                self.assertFalse((checkpoint / ".writer").exists())

    def test_cli_rejected_gates_preserve_native_diagnostics_and_existing_evidence(self) -> None:
        # Run the CLI in a child so native inherited stdout/stderr are captured
        # at the real process boundary in source, wheel and sdist consumers.
        program = r"""import json, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from public_suite_acquisition_consumer import HOST, PAIR, git_model, main, plan, sources

root, kind, phase = Path(sys.argv[2]), sys.argv[3], sys.argv[4]
revisions = {PAIR.baseline: sources(('shared',), 'old'), PAIR.current: sources(('shared',), 'new')}
for name, data in revisions[PAIR.current].items():
    (root / name).write_bytes(data)
document = plan(root / 'calls.log', ('shared',)).document()
measurement = document['suites'][0]['measurement']
measurement['phases'][phase]['gate'] = [sys.executable, '-c',
    "import sys; sys.stdout.buffer.write(b'gate stdout\\r\\n\\xff\\n'); sys.stdout.buffer.flush(); "
    "sys.stderr.buffer.write(b'gate stderr\\r\\n\\xfe\\n'); sys.stderr.buffer.flush(); raise SystemExit(7)"]
if kind == 'pair':
    document = measurement
def toml(value):
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(key) + '=' + toml(item) for key, item in value.items()) + '}'
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(toml(item) for item in value) + ']'
    return json.dumps(value)
(root / 'measurement.toml').write_text(''.join(key + '=' + toml(value) + '\n' for key, value in document.items()), encoding='utf-8', newline='\n')
with patch('research_repo_tools.source_exports._git', side_effect=git_model(revisions)), patch('research_repo_tools.measurement.capture_host', return_value=HOST):
    status = main(['--root', str(root), 'performance', 'measure', 'measurement.toml', PAIR.current, PAIR.baseline,
                   '--payload', 'run.json', '--manifest', 'evidence.json', '--report', 'report.md', '--progress', 'json'])
sys.exit(status)
"""
        for kind in ("pair", "suite"):
            for phase in ("baseline", "current"):
                with self.subTest(kind=kind, phase=phase), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary).resolve()
                    original = {"run.json": b"retained payload\r\n", "evidence.json": b"retained manifest\r\n"}
                    for name, data in original.items():
                        (root / name).write_bytes(data)
                    result = subprocess.run(
                        [sys.executable, "-I", "-X", "utf8", "-c", program, str(Path(__file__).resolve().parent), str(root), kind, phase],
                        capture_output=True,
                        check=False,
                        timeout=60,
                    )
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertEqual(result.stdout, b"gate stdout\r\n\xff\n", result.stderr)
                    self.assertIn(b"gate stderr\r\n\xfe\n", result.stderr)
                    self.assertIn(f"{phase} preflight gate failed".encode(), result.stderr)
                    events = [json.loads(line) for line in result.stderr.splitlines() if line.startswith(b"{")]
                    last = events[-1]
                    self.assertEqual(
                        (last["event"], last["state"], last["suite"], last["phase"]), ("failed", "gate", "shared" if kind == "suite" else None, phase)
                    )
                    rejected = [event["event"] for event in events if event["state"] == "gate" and event["phase"] == phase]
                    self.assertEqual(rejected, ["started", "failed"])
                    self.assertTrue(all(event["completed"] == 0 and event["remaining"] is None for event in events))
                    self.assertFalse(any(event["state"] in {"acquisition", "retention"} for event in events))
                    self.assertNotIn(" measure ", (root / "calls.log").read_text())
                    for name, data in original.items():
                        self.assertEqual((root / name).read_bytes(), data)
                    self.assertFalse((root / "report.md").exists())

    def test_recreated_exports_reject_changed_host_or_discovery_before_timing(self) -> None:
        for changed in ("host", "discovery"):
            with self.subTest(changed=changed):
                self.log.write_bytes(b"")
                exports = []
                preparations = 0

                @contextmanager
                def tracked_export(capture, destination):
                    nonlocal preparations
                    if destination.name == "shared-baseline":
                        preparations += 1
                    exports.append(destination)
                    with export_source(capture, destination) as directory:
                        yield directory

                def host(*args, **kwargs):
                    return replace(HOST, cpu="changed") if changed == "host" and preparations > 1 else HOST

                def discovery(command, args=(), **kwargs):
                    if changed == "discovery" and preparations > 1:
                        return run_command(sys.executable, ("-c", "print('shared/case: benchmark\\nshared/extra: benchmark')"), **kwargs)
                    return run_command(command, args, **kwargs)

                with (
                    patch("research_repo_tools.suite_measurement.export_source", side_effect=tracked_export),
                    patch("research_repo_tools.measurement.capture_host", side_effect=host),
                    patch("research_repo_tools.criterion_inventory.run_command", side_effect=discovery),
                    self.assertRaisesRegex(ValueError, "preparation changed before measurement"),
                ):
                    measure_pair(self.root, plan(self.log, ("shared",)), PAIR, progress=self.quiet)
                self.assertNotIn(" measure ", self.log.read_text())
                self.assertTrue(all(not path.exists() for path in exports))

    def test_session_environment_changes_allow_resume_and_other_changes_reject(self) -> None:
        selected = plan(self.log, ("shared",))
        first = selected.suites[0]
        configured = replace(
            first.measurement,
            baseline=replace(first.measurement.baseline, environment=(("SSH_TTY", "/dev/declared-tty"),)),
            current=replace(first.measurement.current, environment=(("SSH_TTY", "/dev/declared-tty"),)),
        )
        selected = SuiteMeasurementPlan((replace(first, measurement=configured),))
        checkpoint = self.root / "checkpoint"
        ignored = (
            "_",
            "SHLVL",
            "TERM_SESSION_ID",
            "ITERM_SESSION_ID",
            "SSH_CONNECTION",
            "SSH_CLIENT",
            "SSH_TTY",
            "SSH_AUTH_SOCK",
            "SSH_AGENT_PID",
            "TMUX",
            "TMUX_PANE",
            "STY",
            "WINDOW",
            "WINDOWID",
        )

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
            self.assertEqual(self.log.read_text().splitlines().count("shared measure new"), 1)
            for name in ("RRT_UNKNOWN_SESSION_VALUE", "SSH_UNKNOWN_VALUE", "OMP_NUM_THREADS", "PATH", "TERM"):
                with self.subTest(name=name), patch.dict(os.environ, {name: "changed"}), self.assertRaisesRegex(ValueError, "environment-sha256"):
                    measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, resume=True, progress=self.quiet)
            changed = SuiteMeasurementPlan((replace(first, measurement=replace(configured, current=replace(configured.current, environment=()))),))
            with self.assertRaisesRegex(ValueError, "declaration"):
                measure_pair(self.root, changed, PAIR, checkpoint=checkpoint, resume=True, progress=self.quiet)

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
        assert isinstance(run, SuiteRun)
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
        self._cli_checkpoint_offline_promotion_and_readme_retry()

    def test_cli_scheduled_measurement_report_and_explicit_trial_readme(self) -> None:
        self._cli_checkpoint_offline_promotion_and_readme_retry(scheduled=True)

    def _cli_checkpoint_offline_promotion_and_readme_retry(self, *, scheduled=False) -> None:
        def toml(value):
            if isinstance(value, dict):
                return "{" + ", ".join(json.dumps(key) + "=" + toml(item) for key, item in value.items()) + "}"
            if isinstance(value, (tuple, list)):
                return "[" + ", ".join(toml(item) for item in value) + "]"
            return json.dumps(value)

        selected = replace(plan(self.log), schedule=TrialSchedule()) if scheduled else plan(self.log)
        for name, data in self.revisions["v1.1.0"].items():
            (self.root / name).write_bytes(data)
        (self.root / "target").mkdir()
        configuration = f"schema={selected.schema}\nsuites=" + toml(selected.document()["suites"]) + "\n"
        if scheduled:
            configuration += 'schedule={order="alternating",trials=2}\n'
        (self.root / "measurement.toml").write_text(configuration, encoding="utf-8", newline="\n")
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
        if scheduled:
            publication = "trial=2\n" + publication
        (self.root / "publication.toml").write_text(publication + "\n", encoding="utf-8", newline="\n")
        with patch("research_repo_tools.common_measurement.run_command_live", side_effect=AssertionError("README retry must not acquire")):
            self.assertEqual(main([*args, "publish", "publication.toml"]), 0)
            self.assertEqual(main([*args, "publish", "publication.toml"]), 0)
        before = (self.root / "README.md").read_bytes()
        self.assertTrue(before.startswith(b"head\r\n"))
        self.assertTrue(before.endswith(b"tail\r\n"))
        if scheduled:
            self.assertIn(b"Trial 2", before)
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
        run = validate_run_policy(evidence, selected)
        self.assertEqual(len(run.phases), 2)

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

    def scheduled_fixture(self, schedule=None, *, outputs=(), working_tree=False, checkpoint=None):
        selected = plan(self.log, ("shared",))
        selected = replace(
            selected,
            schedule=schedule or TrialSchedule(),
            outputs=outputs,
            suites=tuple(
                replace(
                    suite,
                    measurement=replace(
                        suite.measurement,
                        measurement=replace(suite.measurement.measurement, context=(("repository", "example/library"), ("acquisition-owner", "local"))),
                    ),
                )
                for suite in selected.suites
            ),
        )
        evidence = measure_pair(self.root, selected, PAIR, working_tree=working_tree, checkpoint=checkpoint, progress=self.quiet)
        return selected, evidence

    def test_versioned_schedule_vectors_and_counterbalance(self):
        # Fixed version-1 rank vectors, independently checked using OpenSSL's
        # SHA-256 command over the ASCII schedule records (no ambient RNG).
        for seed, expected in ((0, "CBCCBB"), (42, "BBCCCB"), (2**64 - 1, "CBBCCB")):
            with self.subTest(seed=seed):
                schedule = TrialSchedule(6, "randomized", seed)
                phases = tuple(("current", "baseline") if first == "C" else ("baseline", "current") for first in expected)
                self.assertEqual(schedule.phases, phases)
                self.assertEqual(parse_schedule(schedule.document()).phases, phases)
        for count in (2, 6, 1000):
            with self.subTest(count=count):
                self.assertEqual(TrialSchedule(count).phases, (("baseline", "current"), ("current", "baseline")) * (count // 2))
                for seed in (0, 42, 2**64 - 1):
                    phases = TrialSchedule(count, "randomized", seed).phases
                    self.assertEqual(len(phases), count)
                    self.assertEqual(phases.count(("baseline", "current")), count // 2)
                    self.assertEqual(phases.count(("current", "baseline")), count // 2)

    def test_schedules_execute_declared_order_and_keep_original_trial_bytes(self):
        for schedule in (TrialSchedule(), TrialSchedule(4, "randomized", 42), TrialSchedule(1, "sequential")):
            self.log.unlink(missing_ok=True)
            selected, evidence = self.scheduled_fixture(schedule)
            observed = [line.split()[-1] for line in self.log.read_text().splitlines() if " measure " in line]
            self.assertEqual(observed, ["old" if phase == "baseline" else "new" for pair in schedule.phases for phase in pair])
            self.assertEqual(parse_schedule(schedule.document()), schedule)
            run = validate_run_evidence(evidence)
            assert isinstance(run, ScheduledRun)
            self.assertEqual(run.plan.document(), selected.document())
            # A caller selects a trial explicitly before using phase/series views.
            self.assertEqual(len(parse_suite_run(select_trial(evidence, 1)).phases), 2)
            if schedule.seed is None:
                document = schedule.document()
                del document["seed"]
                self.assertEqual(parse_schedule(document), schedule)
                document["phases"] = [["current", "current"]]
                with self.assertRaisesRegex(ValueError, "phases differ"):
                    parse_schedule(document)
            for index, trial in enumerate(run.trials):
                self.assertEqual(serialize_evidence(select_trial(evidence, index + 1)), serialize_evidence(trial))
                for _, _, sample, _ in parse_suite_run(trial).observations:
                    self.assertTrue(sample.cases[0].sample.endswith(b"\r\n"))
            with patch("research_repo_tools.common_measurement.run_command_live", side_effect=AssertionError("read-only consumer")):
                report = render_run(evidence, title="Trial <script> & notes")
                self.assertIn(b"Within-phase samples are not independent trials", report)
                self.assertIn(b"Trial 1", report)
                self.assertIn(b"Trial &lt;script&gt; &amp; notes", report)
        self.assertEqual(TrialSchedule(4, "randomized", 42).phases, TrialSchedule(4, "randomized", 42).phases)
        self.assertEqual(sum(order[0] == "current" for order in TrialSchedule(10, "randomized", 9).phases), 5)
        for args in ((3, "alternating"), (2, "randomized"), (2, "sequential", 1), (True, "sequential")):
            with self.assertRaises(ValueError):
                TrialSchedule(*args)

    def test_scheduled_report_embeds_trial_bodies_with_one_evidence_header(self):
        _, evidence = self.scheduled_fixture()
        before = serialize_evidence(evidence)
        for directory in ("data/", "data/" + "nested/" * 24):
            with self.subTest(directory=directory):
                report = render_run(
                    evidence,
                    title="Trial <script> & notes",
                    report_path="docs/report.md",
                    payload_path=directory + "run.json",
                    manifest_path=directory + "evidence.json",
                ).decode("utf-8")
                self.assertEqual(report.count("<!-- research-repo-tools/run-report/"), 1)
                self.assertEqual([line for line in report.splitlines() if line.startswith("# ")], ["# Trial &lt;script&gt; &amp; notes"])
                self.assertEqual(report.count("Evidence:"), 1)
                self.assertEqual(report.count("[run payload]"), 1)
                self.assertEqual(report.count("[evidence envelope]"), 1)
                for index in (1, 2):
                    self.assertIn(f"## Trial {index}:", report)
                self.assertEqual(report.count("Semantic contract: shared-v1."), 2)
                self.assertEqual(report.count("Ratios are point estimates within one accepted paired suite."), 2)
                for name in ("run.json", "evidence.json"):
                    self.assertEqual(report.count("../" + directory + name), 1)
                if directory != "data/":
                    self.assertIn("[run payload][rrt-performance-link-1]", report)
                    self.assertIn("[evidence envelope][rrt-performance-link-2]", report)
        self.assertEqual(serialize_evidence(evidence), before)

    def test_schedule_rejects_changed_receipts_trials_and_declarations(self):
        selected, evidence = self.scheduled_fixture()
        changes = [
            lambda raw: raw["trials"].pop(),
            lambda raw: raw["trials"].reverse(),
            lambda raw: raw["trials"][1].update(session=raw["trials"][0]["session"]),
            lambda raw: raw["trials"][1].update(started_ns=0),
            lambda raw: raw["trials"][1]["receipts"].reverse(),
            lambda raw: raw["trials"][0]["receipts"].pop(),
            lambda raw: raw["trials"][0]["receipts"][0].update(finished_ns=0),
            lambda raw: raw["trials"][0].update(identity="0" * 64),
            lambda raw: raw["declaration"]["schedule"].update(order="sequential"),
        ]
        for change in changes:
            raw = json.loads(evidence.payload)
            change(raw)
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_scheduled_run(replace(evidence, payload=deterministic_json(raw)))
        with self.assertRaisesRegex(ValueError, "declaration changed"):
            validate_run_policy(evidence, replace(selected, schedule=TrialSchedule(2, "randomized", 1)))
        with self.assertRaisesRegex(ValueError, "explicit trial"):
            render_run_table(
                evidence, RunLayout((SeriesSelection("shared/current", "Current", (("row", "shared/case"),)),), (RunRow("row", "Row"),), "median", "ns")
            )

    def test_schedule_resume_retains_exact_bytes_and_rejects_interrupted_trial(self):
        checkpoint = self.root / "scheduled"
        selected, evidence = self.scheduled_fixture(checkpoint=checkpoint)
        original = {path.name: path.read_bytes() for path in checkpoint.glob("*.json")}

        def no_measure(command, args=(), **kwargs):
            if "measure" in args:
                raise AssertionError("completed resume must not measure")
            return run_command_live(command, args, **kwargs)

        with patch("research_repo_tools.common_measurement.run_command_live", side_effect=no_measure):
            resumed = measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, resume=True, progress=self.quiet)
            self.assertEqual(serialize_evidence(resumed), serialize_evidence(evidence))
            with self.assertRaisesRegex(ValueError, "identity changed"):
                measure_pair(self.root, replace(selected, schedule=TrialSchedule(4)), PAIR, checkpoint=checkpoint, resume=True, progress=self.quiet)
        self.assertEqual(original, {path.name: path.read_bytes() for path in checkpoint.glob("*.json")})
        before_calls = self.log.read_bytes()
        with (
            patch("research_repo_tools.measurement.capture_host", return_value=replace(HOST, cpu="other CPU")),
            self.assertRaisesRegex(ValueError, "checkpoint observations"),
        ):
            measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, resume=True, progress=self.quiet)
        self.assertEqual(
            [line for line in self.log.read_bytes().splitlines() if b" measure " in line], [line for line in before_calls.splitlines() if b" measure " in line]
        )
        (checkpoint / "unit-trial-2.json").unlink()
        with (
            patch("research_repo_tools.common_measurement.run_command_live", side_effect=AssertionError("must fail before gate")),
            self.assertRaisesRegex(ValueError, "interrupted trial"),
        ):
            measure_pair(self.root, selected, PAIR, checkpoint=checkpoint, resume=True, progress=self.quiet)
        # A failure after real output exists preserves the started marker and
        # never invents a completed receipt or repeats the hidden observations.
        failed = self.root / "failed-schedule"

        def fail(command, args=(), **kwargs):
            result = run_command_live(command, args, **kwargs)
            if "measure" in args:
                raise RuntimeError("after timing output")
            return result

        with patch("research_repo_tools.common_measurement.run_command_live", side_effect=fail), self.assertRaises(RuntimeError):
            measure_pair(self.root, selected, PAIR, checkpoint=failed, progress=self.quiet)
        self.assertTrue((failed / "unit-started-trial-1.json").exists())
        self.assertFalse((failed / "unit-trial-1.json").exists())

    def candidate_fixture(self, *, generated=False):
        original = {**self.revisions["v1.1.0"], "README.md": b"before\r\n"}
        if generated:
            # A previous independently retained run establishes a nonempty
            # history before this release's single acquisition begins.
            _, historical = self.scheduled_fixture()
            for name, data in zip(("prior-run.json", "prior-evidence.json"), serialize_evidence(historical), strict=True):
                (self.root / name).write_bytes(data)
            (self.root / "report.toml").write_bytes(
                b'schema=2\ncurrent="docs/performance.md"\narchive="docs/performance-runs"\ntitle="Complete measurements"\n'
            )
            publish_publication(load_run_report_plan(self.root, "report.toml", payload="prior-run.json", manifest="prior-evidence.json"))
            original.update({path.relative_to(self.root).as_posix(): path.read_bytes() for path in (self.root / "docs").rglob("*") if path.is_file()})
            original["report.toml"] = (self.root / "report.toml").read_bytes()
        for name, data in original.items():
            (self.root / name).write_bytes(data)
        (self.root / "revision").write_bytes(b"candidate")
        self.acquisition_tree = {name: (self.root / name).read_bytes() for name in original}
        inventory = set(original)
        self.stack.enter_context(patch("research_repo_tools.source_exports.select_files", side_effect=lambda root, **kwargs: tuple(sorted(inventory))))
        exceptions = ("README.md",)
        if generated:
            exceptions += ("docs/performance-runs/runs/unindexed/report.md",)
            exceptions += tuple(name for name in original if "/runs/" in name and name.endswith("report.md"))
        selected, evidence = self.scheduled_fixture(outputs=exceptions, working_tree=True)
        if generated:
            payload, envelope = serialize_evidence(evidence)
            self.original_evidence_bytes = (payload, b" \r\n" + envelope.replace(b"\n", b"\r\n"))
            evidence = parse_evidence(*self.original_evidence_bytes)
        # This test acts as the acquisition owner: freeze assertions before
        # final-tree edits or any candidate/archive corruption.
        policy = CandidatePolicy(
            "example/library",
            PAIR.current,
            None,
            "local",
            run_identity(evidence),
            selected,
            dict(evidence.sources)["current"],
            tuple(
                (index + 1, suite, phase, source)
                for index, trial in enumerate(parse_scheduled_run(evidence).trials)
                for suite, phase, _, source in parse_suite_run(trial).observations
            ),
            report="report.toml" if generated else None,
        )
        # Plans retain tuple ordering in memory; canonical JSON sorts context
        # object keys. The handoff contract is canonical bytes, not dataclass equality.
        pending_bytes = serialize_candidate_policy(policy)
        pending = parse_candidate_policy(pending_bytes)
        self.assertIsNone(pending.commit)
        self.assertEqual(serialize_candidate_policy(pending), pending_bytes)
        acquisition_digest = acquisition_assertion_digest(policy)
        before = self.log.read_bytes()
        with patch("research_repo_tools.common_measurement.run_command_live", side_effect=AssertionError("no read-only acquisition")):
            if generated:
                for name, data in zip(("run.json", "evidence.json"), serialize_evidence(evidence), strict=True):
                    (self.root / name).write_bytes(data)
                publication, receipt = plan_candidate_publication(self.root, "run.json", "evidence.json", policy, acquisition_sha256=acquisition_digest)
                self.publication_receipt = receipt
                self.publication_digest = hashlib.sha256(receipt).hexdigest()
                self.prior_publication = dict(publication.originals)
                self.prior_history = {name: data for name, data in original.items() if "/runs/" in name}
                publish_publication(publication)
                inventory.update(name for name, _ in publication.outputs)
            report = render_run(evidence)
            (self.root / "README.md").write_bytes(report)
        final = {name: (self.root / name).read_bytes() for name in inventory}
        mapping = {**self.revisions, "final": final}
        policy = replace(policy, commit="c" * 40)
        self.assertEqual(acquisition_assertion_digest(policy), acquisition_digest)
        self.stack.enter_context(patch("research_repo_tools.source_exports._git", side_effect=git_model(mapping)))
        self.checkout_guard = self.stack.enter_context(patch("research_repo_tools.candidate_reuse.require_release_checkout"))
        native = EvidencePolicy(
            ValidationPolicy("example/library", ".github/workflows/ci.yml", "CI", (("native", ("Checks",)),)),
            "fixture",
            "evidence",
            "signed-source",
            ("sign", ("Sign",)),
        )
        provider = self.stack.enter_context(
            patch(
                "research_repo_tools.candidate_reuse.verify_release_evidence",
                return_value=deterministic_json(
                    {
                        "artifact": {"id": 123, "digest": "sha256:" + "a" * 64},
                        "commit": "c" * 40,
                        "package": {"name": "fixture", "version": "1.1.0"},
                        "run": {"id": 123, "run_attempt": 2, "head_sha": "c" * 40, "name": "CI", "path": ".github/workflows/ci.yml"},
                        "policy": {
                            "repository": "example/library",
                            "event": "push",
                            "ref": "refs/heads/main",
                            "guarantee": "signed-source",
                            "signing_job": {"sign": ["Sign"]},
                        },
                    }
                ),
            )
        )
        accepted = verify_candidate_release(
            self.root,
            evidence,
            policy,
            native,
            lambda endpoint: {},
            acquisition_sha256=acquisition_assertion_digest(policy),
            **({"publication_receipt": self.publication_receipt, "publication_sha256": self.publication_digest} if generated else {}),
        )
        self.assertEqual(before, self.log.read_bytes())
        return evidence, policy, native, provider, accepted, mapping

    def test_committed_publisher_evidence_reuses_one_acquisition_and_preserves_history(self):
        evidence, policy, native, provider, accepted, _ = self.candidate_fixture(generated=True)
        receipt = json.loads(self.publication_receipt)
        run_directory = f"docs/performance-runs/runs/{policy.identity}"
        self.assertEqual(
            {name: output["role"] for name, output in receipt["outputs"].items()},
            {
                f"{run_directory}/run.json": "payload",
                f"{run_directory}/evidence.json": "envelope",
                f"{run_directory}/report.md": "report",
                "docs/performance.md": "current",
                "docs/performance-runs/index.json": "index",
                "docs/performance-runs/latest.json": "latest",
                "docs/performance-runs/README.md": "history",
            },
        )
        for name, raw in zip(("run.json", "evidence.json"), self.original_evidence_bytes, strict=True):
            self.assertEqual((self.root / run_directory / name).read_bytes(), raw)
        for name, output in receipt["outputs"].items():
            raw = (self.root / name).read_bytes()
            self.assertEqual((output["sha256"], output["size"], output["executable"]), (hashlib.sha256(raw).hexdigest(), len(raw), False))
        self.assertEqual(json.loads(accepted)["binding"]["publication_sha256"], self.publication_digest)
        self.assertEqual(receipt["identity"], run_identity(evidence))
        for name, data in self.prior_history.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        for name, data in (("policy.json", serialize_candidate_policy(policy)), ("accepted.json", accepted)):
            (self.root / name).write_bytes(data)
        before = self.log.read_bytes()
        with patch("research_repo_tools.common_measurement.run_command_live", side_effect=AssertionError("inspection/retry must not build, gate or measure")):
            for _ in range(2):
                self.assertEqual(
                    verify_candidate_release(
                        self.root,
                        evidence,
                        policy,
                        native,
                        lambda endpoint: {},
                        acquisition_sha256=acquisition_assertion_digest(policy),
                        publication_receipt=self.publication_receipt,
                        publication_sha256=self.publication_digest,
                        expected=accepted,
                    ),
                    accepted,
                )
                self.assertEqual(provider.call_args.kwargs["expected"], deterministic_json(json.loads(accepted)["native"]))
                # A rendering retry uses the existing publisher and retained
                # history, never captures a replacement trusted receipt.
                self.assertEqual(publish_publication(load_run_report_plan(self.root, "report.toml")), ())
                publish_publication(plan_candidate_archive(self.root, "run.json", "evidence.json", "policy.json", "accepted.json", "release.tar.gz"))
                retained = verify_candidate_archive(self.root / "release.tar.gz", policy, accepted)
                self.assertEqual(serialize_evidence(retained), self.original_evidence_bytes)
                with tarfile.open(self.root / "release.tar.gz", "r:gz") as archive:
                    for name, raw in zip(("run.json", "evidence.json"), self.original_evidence_bytes, strict=True):
                        member = archive.extractfile(f"evidence/{name}")
                        assert member is not None
                        self.assertEqual(member.read(), raw)
        for name, data in self.prior_history.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertEqual(before, self.log.read_bytes())

    def test_candidate_publication_public_annotations_resolve_at_runtime(self):
        hints = get_type_hints(plan_candidate_publication)
        self.assertIs(hints["policy"], CandidatePolicy)
        self.assertIs(hints["root"], Path)
        self.assertEqual(hints["acquisition_sha256"], str)

    def test_publication_receipt_parses_before_checkout_or_provider_effects(self):
        evidence, policy, native, provider, _, _ = self.candidate_fixture(generated=True)
        changes = (
            ("schema", lambda d: d.update(schema="unknown"), "differs from independent acquisition"),
            ("extra field", lambda d: d.update(extra="unknown"), "publication receipt fields"),
            ("missing outputs", lambda d: d.pop("outputs"), "publication receipt fields"),
            ("configuration type", lambda d: d.update(configuration=42), "configuration must be UTF-8 text"),
            ("prior type", lambda d: d.update(prior=[]), "original publication inputs must be a JSON object"),
            ("publisher", lambda d: d.update(publisher={"name": "other", "version": "1"}), "unsupported generated-evidence publisher"),
            ("boolean size", lambda d: d["outputs"]["docs/performance.md"].update(size=True), "invalid generated output role, size or mode"),
            ("executable", lambda d: d["outputs"]["docs/performance.md"].update(executable=True), "invalid generated output role, size or mode"),
            ("digest", lambda d: d["outputs"]["docs/performance.md"].update(sha256="bad"), "generated output digest must be a lowercase SHA-256"),
        )
        for scenario, change, diagnostic in changes:
            document = json.loads(self.publication_receipt)
            change(document)
            payload = deterministic_json(document)
            provider.reset_mock()
            with (
                self.subTest(scenario=scenario),
                patch("research_repo_tools.candidate_reuse.require_release_checkout", side_effect=AssertionError("not parsed")),
                self.assertRaisesRegex(ValueError, diagnostic),
            ):
                verify_candidate_release(
                    self.root,
                    evidence,
                    policy,
                    native,
                    lambda endpoint: {},
                    acquisition_sha256=acquisition_assertion_digest(policy),
                    publication_receipt=payload,
                    publication_sha256=hashlib.sha256(payload).hexdigest(),
                )
            provider.assert_not_called()
        mutable_receipt: Any = bytearray(self.publication_receipt)
        with (
            patch("research_repo_tools.candidate_reuse.require_release_checkout", side_effect=AssertionError("mutable receipt reached checkout")),
            self.assertRaisesRegex(TypeError, "immutable bytes"),
        ):
            verify_candidate_release(
                self.root,
                evidence,
                policy,
                native,
                lambda endpoint: {},
                acquisition_sha256=acquisition_assertion_digest(policy),
                publication_receipt=mutable_receipt,
                publication_sha256=self.publication_digest,
            )
        provider.assert_not_called()

    def test_committed_candidate_cli_summary_uses_frozen_verification_without_acquisition(self):
        evidence, policy, _, _, accepted, _ = self.candidate_fixture(generated=True)
        (self.root / "policy.json").write_bytes(serialize_candidate_policy(policy))
        (self.root / "publication.json").write_bytes(self.publication_receipt)
        (self.root / "native.toml").write_bytes(
            b'schema=1\nrepository="example/library"\npackage="fixture"\nworkflow=".github/workflows/ci.yml"\nworkflow-name="CI"\nartifact-prefix="evidence"\nguarantee="signed-source"\njobs=[{name="native",steps=["Checks"]}]\n[signing-job]\nname="sign"\nsteps=["Sign"]\n'
        )
        args = [
            "--root",
            str(self.root),
            "release",
            "evidence",
            "candidate",
            "native.toml",
            "--checkout",
            ".",
            "--payload",
            "run.json",
            "--manifest",
            "evidence.json",
            "--policy",
            "policy.json",
            "--acquisition-sha256",
            acquisition_assertion_digest(policy),
            "--publication-receipt",
            "publication.json",
            "--publication-sha256",
            self.publication_digest,
        ]
        with (
            patch("research_repo_tools.release_interfaces.require_release_checkout"),
            patch("research_repo_tools.common_measurement.run_command_live", side_effect=AssertionError("no acquisition or build/test")),
            patch("sys.stdout", new_callable=io.StringIO) as output,
            patch("sys.stderr", new_callable=io.StringIO) as diagnostic,
        ):
            self.assertEqual(main([*args, "--output", "step-two.json", "--format", "summary"]), 0)
            self.assertEqual((self.root / "step-two.json").read_bytes(), accepted)
            for expected in (policy.identity, "c" * 40, "b" * 40, "Benchmark evidence: passed", "Acquisition owner: local", "7 exact publisher outputs"):
                self.assertIn(expected, output.getvalue())
            output.seek(0)
            output.truncate()
            self.assertEqual(main([*args, "--output", "approval.json", "--acceptance", "step-two.json", "--format", "json"]), 0)
            self.assertEqual(output.getvalue().encode(), accepted)
            self.assertEqual((self.root / "approval.json").read_bytes(), accepted)
            self.assertEqual(serialize_evidence(evidence)[1], (self.root / "evidence.json").read_bytes())
            self.assertEqual(diagnostic.getvalue(), "")

    def test_committed_evidence_rejects_tampering_inputs_history_indexes_and_approval_drift(self):
        evidence, policy, native, provider, accepted, mapping = self.candidate_fixture(generated=True)
        options: dict[str, Any] = {
            "acquisition_sha256": acquisition_assertion_digest(policy),
            "publication_receipt": self.publication_receipt,
            "publication_sha256": self.publication_digest,
            "expected": accepted,
        }
        generated = json.loads(self.publication_receipt)["outputs"]
        generated_diagnostic = "committed generated output differs from trusted publication"
        undeclared_diagnostic = "performance-relevant or undeclared final-tree differences"
        damages = [(name, mapping["final"][name] + b" ", generated_diagnostic) for name in generated]
        # Without a source policy, the release gate now parses Cargo.toml before
        # binding. Preserve the malformed input and separately prove that valid
        # manifest edits still reach the final-tree tampering guard.
        damages += [
            (name, b"changed", "release dependency provenance: Expected '='" if name == "Cargo.toml" else undeclared_diagnostic)
            for name in ("bench.py", "Cargo.toml", "input-shared.json", "report.toml", "revision")
        ]
        damages += [("Cargo.toml", mapping["final"]["Cargo.toml"] + b"\n# changed\n", undeclared_diagnostic)]
        damages += [(name, b"changed history", "historical benchmark run bytes or modes changed") for name in self.prior_history]
        damages += [("docs/performance-runs/unapproved.json", b"{}", undeclared_diagnostic)]
        damages += [("docs/Performance-Runs/extra.json", b"{}", "portable directory aliases")]
        damages += [("docs/performance-runs/runs/unindexed/report.md", b"unindexed report", undeclared_diagnostic)]
        damages += [("docs/performance.md", None, generated_diagnostic)]
        with patch("research_repo_tools.common_measurement.run_command_live", side_effect=AssertionError("must not reacquire or build")):
            for name, data, diagnostic in damages:
                files = dict(mapping["final"])
                path = self.root / name
                original = path.read_bytes() if path.exists() else None
                path.parent.mkdir(parents=True, exist_ok=True)
                if data is None:
                    files.pop(name)
                    path.unlink()
                else:
                    files[name] = data
                    path.write_bytes(data)
                provider.reset_mock()
                damaged_tree = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
                try:
                    with (
                        self.subTest(path=name),
                        patch("research_repo_tools.source_exports._git", side_effect=git_model({**mapping, "final": files})),
                        patch("research_repo_tools.source_exports.select_files", return_value=tuple(sorted(files))),
                        self.assertRaisesRegex(ValueError, diagnostic),
                    ):
                        verify_candidate_release(self.root, evidence, policy, native, lambda endpoint: {}, **options)
                    provider.assert_not_called()
                    self.assertEqual({path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}, damaged_tree)
                finally:
                    if original is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_bytes(original)
            for changes, diagnostic in (
                ({"publication_receipt": self.publication_receipt + b" "}, "publication receipt differs from trusted Step 1"),
                ({"publication_sha256": "0" * 64}, "publication receipt differs from trusted Step 1"),
                ({"publication_receipt": None}, "requires a publication receipt and independently trusted"),
            ):
                changed_options: dict[str, Any] = {**options, **changes}
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, diagnostic):
                    verify_candidate_release(self.root, evidence, policy, native, lambda endpoint: {}, **changed_options)
            # Even a newly asserted digest cannot turn an arbitrary JSON path
            # into a supported publisher role or redefine its configuration.
            forged = json.loads(self.publication_receipt)
            forged["outputs"]["tooling/settings.json"] = next(iter(forged["outputs"].values()))
            data = deterministic_json(forged)
            forged_options: dict[str, Any] = {**options, "publication_receipt": data, "publication_sha256": hashlib.sha256(data).hexdigest()}
            with self.assertRaisesRegex(ValueError, "generated outputs fields"):
                verify_candidate_release(
                    self.root,
                    evidence,
                    policy,
                    native,
                    lambda endpoint: {},
                    **forged_options,
                )
            provider.assert_not_called()
            # Every rejected case leaves the original approved state usable.
            self.assertEqual(verify_candidate_release(self.root, evidence, policy, native, lambda endpoint: {}, **options), accepted)
            provider.return_value = deterministic_json({"artifact": {"id": 124}, "commit": "c" * 40})
            with self.assertRaisesRegex(ValueError, "approved"):
                verify_candidate_release(self.root, evidence, policy, native, lambda endpoint: {}, **options)

    @unittest.skipIf(os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1", "Git-mutating fixture disabled")
    def test_committed_evidence_native_commit_and_merge(self):
        from research_repo_tools.process import run_git_bytes
        from research_repo_tools.selection import select_files

        evidence, policy, native, provider, _, mapping = self.candidate_fixture(generated=True)
        checkout = self.root / "native merge café"
        checkout.mkdir()
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

        def git(*args):
            return run_git_bytes(
                [
                    "--no-pager",
                    "-c",
                    "core.autocrlf=false",
                    "-c",
                    "commit.gpgsign=false",
                    "-c",
                    f"core.hooksPath={checkout / 'disabled-hooks'}",
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    *args,
                ],
                cwd=checkout,
                env=environment,
            ).stdout

        def write_tree(files):
            for name, data in files.items():
                path = checkout / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

        git("init", "--template=", "-b", "main")
        write_tree(self.acquisition_tree)
        git("add", ".")
        git("commit", "-m", "original source and history")
        git("switch", "-c", "test/release-preparation")
        write_tree(mapping["final"])
        git("add", ".")
        git("commit", "-m", "generated evidence and reports")
        git("switch", "main")
        git("merge", "--no-ff", "test/release-preparation", "-m", "merge release outputs")
        final = git("rev-parse", "HEAD").decode("ascii").strip()
        policy = replace(policy, commit=final)
        provider.return_value = deterministic_json({"artifact": {"id": 123}, "commit": final})
        before = self.log.read_bytes()
        with (
            patch(
                "research_repo_tools.source_exports._git",
                side_effect=lambda root, *args, input=None: (
                    run_git_bytes(["--no-pager", "--no-replace-objects", *args], cwd=root, env=environment, input=input).stdout
                ),
            ),
            patch("research_repo_tools.source_exports.select_files", side_effect=select_files),
            patch("research_repo_tools.candidate_reuse.require_release_checkout", side_effect=require_release_checkout),
            patch("research_repo_tools.common_measurement.run_command_live", side_effect=AssertionError("no acquisition or native gate retry")),
        ):
            accepted = verify_candidate_release(
                checkout,
                evidence,
                policy,
                native,
                lambda endpoint: {},
                acquisition_sha256=acquisition_assertion_digest(policy),
                publication_receipt=self.publication_receipt,
                publication_sha256=self.publication_digest,
            )
            self.assertEqual(json.loads(accepted)["binding"]["final"]["revision"], final)
        self.assertEqual(before, self.log.read_bytes())

    def test_publication_capture_rejects_changed_original_history_and_self_derived_evidence(self):
        evidence, policy, _, _, _, _ = self.candidate_fixture(generated=True)
        # Restore the exact pre-promotion output states from the independently
        # captured original tree, while keeping the acquired scratch bytes.
        for name, prior in self.prior_publication.items():
            path = self.root / name
            if prior is None:
                path.unlink()
            else:
                path.write_bytes(prior)
        history_index = self.root / "docs/performance-runs/README.md"
        original_index = history_index.read_bytes()
        history_index.write_bytes(original_index + b"altered original history\r\n")
        # Even structurally valid history must retain the acquisition tree's
        # original bytes; it cannot become a new trusted publication baseline.
        with self.assertRaisesRegex(ValueError, "publication input"):
            plan_candidate_publication(self.root, "run.json", "evidence.json", policy, acquisition_sha256=acquisition_assertion_digest(policy))
        history_index.write_bytes(original_index)
        with self.assertRaisesRegex(ValueError, "trusted Step 1"):
            plan_candidate_publication(self.root, "run.json", "evidence.json", policy, acquisition_sha256="0" * 64)
        payload, _ = self.original_evidence_bytes
        (self.root / "run.json").write_bytes(payload + b" ")
        with self.assertRaisesRegex(ValueError, "payload does not match its recorded SHA-256 digest"):
            plan_candidate_publication(self.root, "run.json", "evidence.json", policy, acquisition_sha256=acquisition_assertion_digest(policy))
        (self.root / "run.json").write_bytes(payload)
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        _, receipt = plan_candidate_publication(self.root, "run.json", "evidence.json", policy, acquisition_sha256=acquisition_assertion_digest(policy))
        self.assertEqual(receipt, self.publication_receipt)
        self.assertEqual({path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}, before)

    def test_candidate_assertions_remain_frozen_after_caller_mutates_observations(self):
        _, original, _, _, _, _ = self.candidate_fixture()
        observations = [list(item) for item in original.observations]
        policy = replace(original, observations=observations)
        retained = serialize_candidate_policy(policy)
        digest = acquisition_assertion_digest(policy)
        observations[0][0] = 0
        observations[0][2] = "invalid phase"
        observations.clear()
        self.assertEqual(serialize_candidate_policy(policy), retained)
        self.assertEqual(acquisition_assertion_digest(policy), digest)
        self.assertEqual(policy.observations, original.observations)

    def test_candidate_requires_final_commit_before_release_or_archive_operations(self):
        evidence, final, native, provider, accepted, _ = self.candidate_fixture()
        pending = replace(final, commit=None)
        provider.reset_mock()
        with self.assertRaisesRegex(ValueError, "selected final commit"):
            verify_candidate_release(self.root, evidence, pending, native, lambda endpoint: {}, acquisition_sha256=acquisition_assertion_digest(pending))
        with self.assertRaisesRegex(ValueError, "selected final commit"):
            verify_candidate_archive(self.root / "not-opened.tar.gz", pending, accepted)
        provider.assert_not_called()

    @unittest.skipIf(os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1", "Git-mutating fixture disabled")
    def test_candidate_public_api_rejects_staged_changes_with_clean_working_bytes(self):
        evidence, policy, native, provider, _, _ = self.candidate_fixture()
        checkout = self.root / "index-checkout"
        checkout.mkdir()

        def git(*arguments):
            environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
            environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
            return subprocess.run(
                [
                    "git",
                    "--no-pager",
                    "-c",
                    "core.autocrlf=false",
                    "-c",
                    "commit.gpgsign=false",
                    "-c",
                    f"core.hooksPath={checkout / 'disabled-hooks'}",
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    *arguments,
                ],
                cwd=checkout,
                env=environment,
                check=True,
                capture_output=True,
            ).stdout

        git("init", "--template=")
        tracked = checkout / "source.txt"
        tracked.write_bytes(b"original\r\n")
        git("add", "source.txt")
        git("commit", "-m", "fixture")
        policy = replace(policy, commit=git("rev-parse", "HEAD").decode("ascii").strip())
        tracked.write_bytes(b"staged\r\n")
        git("add", "source.txt")
        tracked.write_bytes(b"original\r\n")
        self.assertEqual(git("diff", "HEAD", "--", "source.txt"), b"")
        self.assertTrue(git("diff", "--cached", "--", "source.txt"))
        provider.reset_mock()
        with (
            patch("research_repo_tools.candidate_reuse.require_release_checkout", wraps=require_release_checkout),
            self.assertRaisesRegex(ValueError, "checkout is dirty"),
        ):
            verify_candidate_release(checkout, evidence, policy, native, lambda endpoint: {}, acquisition_sha256=acquisition_assertion_digest(policy))
        provider.assert_not_called()

    def test_candidate_rechecks_checkout_after_native_verification(self):
        evidence, policy, native, provider, _, _ = self.candidate_fixture()
        provider.reset_mock()
        self.checkout_guard.side_effect = [None, ValueError("checkout changed during verification")]
        with self.assertRaisesRegex(ValueError, "checkout changed"):
            verify_candidate_release(self.root, evidence, policy, native, lambda endpoint: {}, acquisition_sha256=acquisition_assertion_digest(policy))
        provider.assert_called_once()

    def test_candidate_forwards_cargo_reproduction_and_preserves_native_rejection(self):
        evidence, policy, native, provider, accepted, _ = self.candidate_fixture()
        archive = self.root / "reproduced.crate"
        tools = {"cargo": "fixture-cargo", "rustc": "fixture-rustc"}
        native = replace(native, guarantee="cargo-archive")
        self.assertEqual(
            verify_candidate_release(
                self.root,
                evidence,
                policy,
                native,
                lambda endpoint: {},
                acquisition_sha256=acquisition_assertion_digest(policy),
                expected=accepted,
                reproduced_archive=archive,
                reproduced_tools=tools,
            ),
            accepted,
        )
        self.assertEqual(provider.call_args.args[0], native)
        self.assertEqual(provider.call_args.kwargs["expected"], deterministic_json(json.loads(accepted)["native"]))
        self.assertEqual(provider.call_args.kwargs["reproduced_archive"], archive)
        self.assertEqual(provider.call_args.kwargs["reproduced_tools"], tools)
        provider.side_effect = ValueError("Cargo reproduction bytes or tools differ from retained validation")
        with self.assertRaisesRegex(ValueError, "Cargo reproduction"):
            verify_candidate_release(
                self.root,
                evidence,
                policy,
                native,
                lambda endpoint: {},
                acquisition_sha256=acquisition_assertion_digest(policy),
                reproduced_archive=archive,
                reproduced_tools=tools,
            )

    def test_candidate_binding_reuses_one_acquisition_after_final_merge(self):
        evidence, policy, native, provider, accepted, mapping = self.candidate_fixture()
        self.assertEqual(serialize_candidate_policy(parse_candidate_policy(serialize_candidate_policy(policy))), serialize_candidate_policy(policy))
        receipt = json.loads(accepted)
        self.assertEqual(receipt["binding"]["changed"], ["README.md"])
        self.assertEqual(receipt["binding"]["candidate"]["revision"], "b" * 40)
        self.assertTrue(receipt["binding"]["candidate"]["dirty"])
        self.assertEqual(receipt["binding"]["final"]["revision"], "c" * 40)
        payload, manifest = serialize_evidence(evidence)
        for name, data in (
            ("run.json", payload),
            ("evidence.json", manifest),
            ("policy.json", serialize_candidate_policy(policy)),
            ("accepted.json", accepted),
        ):
            (self.root / name).write_bytes(data)
        before = self.log.read_bytes()
        with patch("research_repo_tools.common_measurement.run_command_live", side_effect=AssertionError("no acquisition or preflight")):
            self.assertEqual(
                verify_candidate_release(
                    self.root, evidence, policy, native, lambda endpoint: {}, acquisition_sha256=acquisition_assertion_digest(policy), expected=accepted
                ),
                accepted,
            )
            self.assertEqual(provider.call_args.kwargs["expected"], deterministic_json(receipt["native"]))
            for _ in range(2):
                publish_publication(plan_candidate_archive(self.root, "run.json", "evidence.json", "policy.json", "accepted.json", "release.tar.gz"))
                retained = verify_candidate_archive(
                    self.root / "release.tar.gz", policy, accepted, target=ReleaseTarget("example/library", PAIR.current, 7, "c" * 40)
                )
                self.assertEqual(serialize_evidence(retained), (payload, manifest))
            (self.root / "release.tar.gz").write_bytes(b"replaced before approval")
            with self.assertRaisesRegex(ValueError, "artifact bytes changed"):
                verify_candidate_archive(self.root / "release.tar.gz", policy, accepted)
        self.assertEqual(before, self.log.read_bytes())

    def test_candidate_cli_freezes_and_revalidates_step_two_without_acquisition(self):
        evidence, policy, native, provider, accepted, _ = self.candidate_fixture()
        payload, manifest = serialize_evidence(evidence)
        for name, data in (("run.json", payload), ("evidence.json", manifest), ("policy.json", serialize_candidate_policy(policy))):
            (self.root / name).write_bytes(data)
        (self.root / "native.toml").write_bytes(
            b'schema=1\nrepository="example/library"\npackage="fixture"\nworkflow=".github/workflows/ci.yml"\nworkflow-name="CI"\nartifact-prefix="evidence"\nguarantee="signed-source"\njobs=[{name="native",steps=["Checks"]}]\n[signing-job]\nname="sign"\nsteps=["Sign"]\n'
        )
        args = [
            "--root",
            str(self.root),
            "release",
            "evidence",
            "candidate",
            "native.toml",
            "--checkout",
            ".",
            "--payload",
            "run.json",
            "--manifest",
            "evidence.json",
            "--policy",
            "policy.json",
            "--acquisition-sha256",
            acquisition_assertion_digest(policy),
        ]
        before = self.log.read_bytes()
        with (
            tempfile.TemporaryDirectory(prefix="candidate caller ") as caller,
            chdir(caller),
            patch("research_repo_tools.release_interfaces.require_release_checkout"),
            patch("research_repo_tools.common_measurement.run_command_live", side_effect=AssertionError("Step 2 must not gate or measure")),
        ):
            self.assertEqual(main([*args, "--output", "step-two.json"]), 0)
            self.assertEqual((self.root / "step-two.json").read_bytes(), accepted)
            self.assertEqual(main([*args, "--acceptance", "step-two.json", "--output", "approval.json"]), 0)
            self.assertEqual((self.root / "approval.json").read_bytes(), accepted)
            # Explicit Python identity must also reach the candidate command,
            # even while the benchmark source retains its Cargo package.
            (self.root / "pyproject.toml").write_text(
                f'[project]\nname="python-fixture"\nversion="{policy.tag.removeprefix("v")}"\n', encoding="utf-8", newline="\n"
            )
            configuration = self.root / "native.toml"
            configuration.write_bytes(b'package-kind="python"\n' + configuration.read_bytes().replace(b'package="fixture"', b'package="python-fixture"'))
            self.assertEqual(main([*args, "--output", "python-step-two.json"]), 0)
            self.assertEqual(provider.call_args.args[0].package_kind, "python")
            self.assertEqual(provider.call_args.args[0].package, "python-fixture")
            self.assertEqual(list(Path(caller).iterdir()), [])
        self.assertEqual(before, self.log.read_bytes())

    def test_candidate_binding_rejects_final_inputs_and_stale_independent_assertions(self):
        evidence, policy, native, provider, accepted, mapping = self.candidate_fixture()
        for name, data in (
            ("revision", b"changed"),
            ("bench.py", b"changed"),
            ("Cargo.toml", b"changed"),
            ("Cargo.toml", (self.root / "Cargo.toml").read_bytes() + b"\n# changed\n"),
            ("input-shared.json", b"changed"),
            ("new-build-input", b"added"),
            ("input-shared.json", None),
        ):
            files = dict(mapping["final"])
            path = self.root / name
            original = path.read_bytes() if path.exists() else None
            if data is None:
                files.pop(name)
                path.unlink()
            else:
                files[name] = data
                path.write_bytes(data)
            changed = {**mapping, "final": files}
            try:
                with (
                    patch("research_repo_tools.source_exports._git", side_effect=git_model(changed)),
                    patch("research_repo_tools.source_exports.select_files", return_value=tuple(sorted(files))),
                    self.subTest(name=name, data=data),
                    self.assertRaisesRegex(
                        ValueError, "release dependency provenance: Expected '='" if name == "Cargo.toml" and data == b"changed" else "undeclared final-tree"
                    ),
                ):
                    verify_candidate_release(self.root, evidence, policy, native, lambda endpoint: {}, acquisition_sha256=acquisition_assertion_digest(policy))
            finally:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(original)
        for changed in (
            replace(policy, identity="0" * 64),
            replace(policy, commit="d" * 40),
            replace(policy, observations=policy.observations[:-1]),
            replace(policy, measurement=replace(policy.measurement, outputs=())),
            replace(policy, acquisition_owner="github-actions"),
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                verify_candidate_release(
                    self.root, evidence, changed, native, lambda endpoint: {}, acquisition_sha256=acquisition_assertion_digest(policy), expected=accepted
                )
        provider.return_value = deterministic_json({"artifact": {"id": 124}, "commit": "c" * 40})
        with self.assertRaisesRegex(ValueError, "approved"):
            verify_candidate_release(
                self.root, evidence, policy, native, lambda endpoint: {}, acquisition_sha256=acquisition_assertion_digest(policy), expected=accepted
            )
        with self.assertRaisesRegex(ValueError, "report/document"):
            replace(policy.measurement, outputs=("bench.py",))
        for name in ("Cargo.toml", "settings.json", "build.py", "CMakeLists.txt"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                replace(policy.measurement, outputs=(name,))
        suite = policy.measurement.suites[0]
        with self.assertRaisesRegex(ValueError, "overlaps"):
            replace(
                policy.measurement,
                suites=(
                    replace(
                        suite,
                        measurement=replace(
                            suite.measurement, measurement=replace(suite.measurement.measurement, harness=(*suite.measurement.harness, "readme.md"))
                        ),
                    ),
                ),
                outputs=("README.md",),
            )
        # A candidate cannot substitute its own freshly computed acceptance
        # assertions for the Step 1 pin retained by the acquisition owner.
        self_derived = replace(policy, acquisition_owner="github-actions")
        with self.assertRaisesRegex(ValueError, "trusted Step 1"):
            verify_candidate_release(self.root, evidence, self_derived, native, lambda endpoint: {}, acquisition_sha256=acquisition_assertion_digest(policy))


class TestProgress(unittest.TestCase):
    def test_preparation_failure_survives_failed_terminal_reporting(self) -> None:
        reporting = OSError("progress stream failed")

        def observer(event):
            if event["event"] == "failed":
                raise reporting

        reporter = ProgressReporter(mode="none", observer=observer)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with self.assertRaises(ExceptionGroup) as caught:
                measure_pair(root, plan(root / "calls.log"), PAIR, resume=True, progress=reporter)
            original, secondary = caught.exception.exceptions
            self.assertIsInstance(original, ValueError)
            self.assertIn("resume requires an explicit checkpoint", str(original))
            self.assertIs(secondary, reporting)
            self.assertEqual(list(root.iterdir()), [])

    def test_failed_progress_reporting_preserves_failure_and_interruption(self) -> None:
        for original in (ValueError("acquisition failed"), KeyboardInterrupt("acquisition interrupted")):
            reporting = OSError("progress stream failed")

            def observer(event):
                if event["event"] in {"failed", "interrupted"}:
                    raise reporting

            reporter = ProgressReporter(mode="none", observer=observer)
            with self.subTest(original=original), self.assertRaises(BaseExceptionGroup) as caught:
                with reporter.stage("acquisition"):
                    raise original
            self.assertEqual(caught.exception.exceptions, (original, reporting))

    def test_heartbeat_errors_reach_the_caller_after_the_worker_stops(self) -> None:
        for original in (None, ValueError("native command failed"), KeyboardInterrupt("native command interrupted")):
            observed = Event()
            events = []
            reporting = OSError("heartbeat observer failed")

            def observer(event):
                events.append(event["event"])
                if event["event"] == "heartbeat":
                    observed.set()
                    raise reporting

            reporter = ProgressReporter(mode="none", cadence=1, observer=observer)
            with (
                self.subTest(original=original),
                patch("threading.excepthook") as uncaught,
                self.assertRaises(OSError if original is None else BaseExceptionGroup) as caught,
            ):
                with reporter.stage("acquisition"):
                    self.assertTrue(observed.wait(10), "heartbeat did not run")
                    if original is not None:
                        raise original
            if original is None:
                self.assertIs(caught.exception, reporting)
            else:
                assert isinstance(caught.exception, BaseExceptionGroup)
                self.assertEqual(caught.exception.exceptions, (original, reporting))
            uncaught.assert_not_called()
            self.assertEqual(events[-1], "interrupted" if isinstance(original, KeyboardInterrupt) else "failed")
            self.assertNotIn("finished", events)

    def test_checkpoint_cleanup_preserves_errors_and_retained_recovery_state(self) -> None:
        for original in (None, ValueError("acquisition failed"), KeyboardInterrupt("interrupted")):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve() / "checkpoint"
                lock = root / ".writer"
                rmdir = Path.rmdir
                cleanup_error = OSError("lock removal failed")
                journal = None

                def fail_cleanup(path):
                    if path == lock:
                        raise cleanup_error
                    return rmdir(path)

                with (
                    patch.object(Path, "rmdir", fail_cleanup),
                    self.assertRaises(OSError if original is None else BaseExceptionGroup) as caught,
                ):
                    with open_checkpoint(root, {"fixture": "one"}, ("shared",), resume=False) as journal:
                        journal.accept("shared", {"complete": True})
                        if original is not None:
                            raise original
                if original is not None:
                    assert isinstance(caught.exception, BaseExceptionGroup)
                    self.assertIs(caught.exception.exceptions[0], original)
                    recovery = caught.exception.exceptions[1]
                else:
                    recovery = caught.exception
                self.assertIs(recovery, cleanup_error)
                self.assertIn(str(lock), " ".join(cleanup_error.__notes__))
                self.assertIn(str(lock), format_exception_diagnostics(caught.exception))
                self.assertTrue(lock.is_dir())
                assert journal is not None
                with self.assertRaisesRegex(ValueError, "closed"):
                    journal.read()
                with self.assertRaisesRegex(ValueError, "unresolved writer"), open_checkpoint(root, {"fixture": "one"}, ("shared",), resume=True):
                    self.fail("cleanup failure silently released ownership")
                lock.rmdir()  # Explicit owner recovery retains the completed unit.
                with open_checkpoint(root, {"fixture": "one"}, ("shared",), resume=True) as resumed:
                    self.assertEqual(resumed.read(), {"shared": {"complete": True}})

    def test_default_heartbeat_waits_five_minutes_and_stage_events_are_immediate(self) -> None:
        now = [0.0]
        events = []
        reporter = ProgressReporter(mode="none", observer=events.append, clock=lambda: now[0])
        reporter.budget({"shared": 1})
        now[0] = 299
        reporter.emit("heartbeat")
        self.assertEqual([event["event"] for event in events], ["budget"])
        now[0] = 300
        reporter.emit("heartbeat")
        self.assertEqual([event["event"] for event in events], ["budget", "heartbeat"])
        with reporter.stage("acquisition", phase="current"):
            self.assertEqual(events[-1]["event"], "started")
        self.assertEqual(events[-1]["event"], "finished")
        self.assertEqual(events[-1]["elapsed_seconds"], 300)

    def test_cli_heartbeat_defaults_and_shorter_or_longer_overrides(self) -> None:
        for command in ("measure", "release"):
            outputs = ["--payload", "run.json", "--manifest", "evidence.json"] if command == "measure" else []
            for arguments, expected in (([], 300), (["--progress-cadence", "60"], 60), (["--progress-cadence", "600"], 600)):
                with self.subTest(command=command, arguments=arguments):
                    options = parser().parse_args(["performance", command, "measurement.toml", *outputs, *arguments])
                    reporter = ProgressReporter(mode="none", cadence=options.progress_cadence)
                    self.assertEqual(reporter.cadence, expected)

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
