"""Complete independent acquisitions through public interfaces and native children.

Synthetic timings verify workflow correctness only. Native Git fixtures honor the
shared opt-out before creating any disposable repository.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from research_repo_tools.cli import main
from research_repo_tools.common_measurement import CompleteMeasurementPlan, measure_prepared_pair, validate_run_policy
from research_repo_tools.complete_runs import parse_run, ratio_incompatibilities, render_run, validate_run_evidence
from research_repo_tools.evidence import parse_evidence, serialize_evidence
from research_repo_tools.host_metadata import HostMetadata
from research_repo_tools.measurement import load_measurement
from research_repo_tools.release_discovery import PublishedRelease
from research_repo_tools.release_pairs import ReleasePair
from research_repo_tools.run_reports import load_latest_run
from research_repo_tools.source_exports import capture_source, export_source

COUNTS = (10, 12, 15, 20, 25, 100)
PROGRAM = """import json, shutil, sys
from pathlib import Path
config = json.loads(Path('cases.json').read_bytes())
if sys.argv[1] == 'list':
    for name in config:
        print(name + ': benchmark')
elif sys.argv[1] == 'gate':
    assert Path('marker').read_bytes() == EXPECTED
    if EXPECTED == b'baseline':
        assert Path('bench-baseline-only').read_bytes() == b'original'
else:
    assert Path('marker').read_bytes() == EXPECTED
    for name, count in config.items():
        target = Path('target/criterion') / name / 'new'
        target.mkdir(parents=True)
        values = {'benchmark.json': {'full_id': name},
                  'sample.json': {'iters': [1] * count, 'times': [5] * count},
                  'estimates.json': {s: {'point_estimate': 5, 'confidence_interval': {'confidence_level': .95, 'lower_bound': 4, 'upper_bound': 6}} for s in ('mean', 'median')},
                  'tukey.json': [1, 2, 3, 4]}
        for filename, value in values.items():
            (target / filename).write_bytes((json.dumps(value) + '\\r\\n').encode())
        shutil.copytree(target, target.parent / 'saved')
"""


def fixture(root: Path, marker: str, *, disjoint: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "marker").write_bytes(marker.encode())
    (root / "bench.py").write_text("EXPECTED = " + repr(marker.encode()) + "\n" + PROGRAM, encoding="utf-8", newline="\n")
    if marker == "baseline":
        (root / "bench-baseline-only").write_bytes(b"original")
    cases = {f"{marker if disjoint else 'case'}/{n}": n for n in COUNTS}
    (root / "cases.json").write_bytes(json.dumps(cases).encode())
    (root / "Cargo.toml").write_bytes(b'[package]\nname="fixture"\nversion="1.1.0"\n')
    (root / ".gitignore").write_bytes(b"target/\n")


def declaration(root: Path, *, disjoint: bool = False, harness_mode: str = "independent") -> CompleteMeasurementPlan:
    python = json.dumps(sys.executable)
    lines = [
        "schema=4",
        f'harness-mode="{harness_mode}"',
        'backend="export"',
        'sources=["marker", "cases.json"]',
        'workloads=["cases.json"]',
        'harness=["bench*"]',
        'saved-sample="saved"',
        'raw-files=["tukey.json"]',
        "[context]",
        'workload="synthetic-v1"',
        "[probes]",
        f'python=[{python}, "--version"]',
    ]
    for phase in ("baseline", "current"):
        prefix = phase if disjoint else "case"
        counts = ", ".join(json.dumps(f"{prefix}/{n}") + "=" + str(n) for n in COUNTS)
        required = ", ".join(json.dumps(f"{prefix}/{n}") for n in COUNTS)
        lines.extend(
            [
                f"[phases.{phase}]",
                f'command=[{python}, "bench.py", "measure"]',
                f'gate=[{python}, "bench.py", "gate"]',
                f"sample-counts={{{counts}}}",
                f"[phases.{phase}.discovery]",
                f"required=[{required}]",
                f"[[phases.{phase}.discovery.suites]]",
                'name="synthetic"',
                f'command=[{python}, "bench.py", "list"]',
            ]
        )
    for phase in ("baseline", "current"):
        lines.extend(["[[series]]", f'name="{phase}"', f'phase="{phase}"', 'select={include=["*"]}'])
    (root / "measurement.toml").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    plan = load_measurement(root, "measurement.toml")
    assert isinstance(plan, CompleteMeasurementPlan)
    return plan


class TestAcquisitionConsumer(unittest.TestCase):
    def test_independent_harness_disjoint_union_raw_retention_and_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, current = root / "baseline", root / "current"
            fixture(baseline, "baseline", disjoint=True)
            fixture(current, "current", disjoint=True)
            plan = replace(declaration(current, disjoint=True), backend="worktree")
            with patch("research_repo_tools.measurement.resolve_revision", side_effect=lambda path, _: ("a" if path == baseline else "b") * 40):
                evidence = measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
            self.assertEqual((baseline / "bench-baseline-only").read_bytes(), b"original")
            self.assertIn(b"EXPECTED = b'baseline'", (baseline / "bench.py").read_bytes())
            shutil.rmtree(baseline)
            shutil.rmtree(current)
            evidence = parse_evidence(*serialize_evidence(evidence))
            run = validate_run_policy(evidence, plan)
            self.assertEqual(run.harness_mode, "independent")
            self.assertEqual([case.sample_count for case in dict(run.phases)["baseline"].cases], sorted(COUNTS, key=lambda count: f"baseline/{count}"))
            for _, sample in run.phases:
                for case in sample.cases:
                    self.assertEqual(dict(case.raw_files)["tukey.json"], b"[1, 2, 3, 4]\r\n")
            report = render_run(evidence)
            self.assertIn(b"baseline/100", report)
            self.assertIn(b"current/100", report)
            self.assertIn("no common benchmark IDs", ratio_incompatibilities(evidence))
            with self.assertRaises(ValueError):
                validate_run_policy(evidence, replace(plan, harness_mode="common"))
            with self.assertRaises(ValueError):
                validate_run_policy(evidence, plan, release=True)
            altered = json.loads(evidence.payload)
            altered["harness_mode"] = "common"
            with self.assertRaises(ValueError):
                validate_run_evidence(replace(evidence, payload=json.dumps(altered).encode()))

    def test_export_installed_cli_to_offline_document_with_read_only_git_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture(root, "baseline")
            baseline = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            fixture(root, "current")
            (root / "bench-baseline-only").unlink()
            current = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            declaration(root)

            def git(directory: Path, *args: str, input: bytes | None = None) -> bytes:
                if args == ("rev-parse", "--show-prefix"):
                    return b""
                if args[0] == "rev-parse":
                    return (b"a" if "v1.0.0" in args[-1] else b"b") * 40 + b"\n"
                files = baseline if args[-1] == "a" * 40 else current

                def oid(data: bytes) -> bytes:
                    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest().encode()

                if args[0] == "ls-tree":
                    return b"".join(b"100644 blob " + oid(data) + b"\t" + name.encode() + b"\0" for name, data in files.items())
                self.assertEqual(args, ("cat-file", "--batch"))
                assert input is not None
                blobs = {oid(data): data for data in (*baseline.values(), *current.values())}
                return b"".join(key + b" blob " + str(len(blobs[key])).encode() + b"\n" + blobs[key] + b"\n" for key in input.splitlines())

            def selected(directory: Path, **kwargs) -> tuple[str, ...]:
                return tuple(
                    sorted(
                        path.relative_to(directory).as_posix()
                        for path in directory.rglob("*")
                        if path.is_file() and "target" not in path.relative_to(directory).parts
                    )
                )

            args = ["--root", str(root), "performance"]
            with (
                patch("research_repo_tools.source_exports._git", side_effect=git),
                patch("research_repo_tools.source_exports.select_files", side_effect=selected),
                patch("research_repo_tools.measurement.resolve_revision", return_value="b" * 40),
            ):
                self.assertEqual(
                    main([*args, "measure", "measurement.toml", "v1.1.0", "v1.0.0", "--payload", "target/run.json", "--manifest", "target/evidence.json"]), 0
                )
            retained = parse_evidence((root / "target/run.json").read_bytes(), (root / "target/evidence.json").read_bytes())
            self.assertEqual(dict(retained.sources)["baseline"].revision, "a" * 40)
            self.assertIn("source-export", dict(dict(retained.sources)["current"].context))
            current_source = dict(retained.sources)["current"]
            for missing in ("measurement-declaration", "tool.python", "harness-modes", "workload-sha256", "source-export"):
                with self.subTest(missing=missing):
                    changed = replace(current_source, context=tuple((key, value) for key, value in current_source.context if key != missing))
                    with self.assertRaises(ValueError):
                        validate_run_evidence(replace(retained, sources=(("baseline", dict(retained.sources)["baseline"]), ("current", changed))))
            with self.assertRaises(ValueError):
                validate_run_evidence(
                    replace(retained, sources=(("baseline", dict(retained.sources)["baseline"]), ("current", replace(current_source, harness_sha256=None))))
                )
            # Independent acquisition permits genuinely different recorded tools;
            # internal agreement between the host record and tool field is required.
            context = dict(current_source.context)
            host = json.loads(context["host"])
            host["tools"]["python"] = "different Python"
            context["host"] = json.dumps(host)
            context["tool.python"] = json.dumps("different Python")
            different = replace(
                retained, sources=(("baseline", dict(retained.sources)["baseline"]), ("current", replace(current_source, context=tuple(context.items()))))
            )
            validate_run_evidence(different)
            self.assertTrue(any("context.tool.python" in reason for reason in ratio_incompatibilities(different)))
            (root / "report.toml").write_bytes(
                b'schema=2\ncurrent="target/current.md"\narchive="target/history"\ntitle="Synthetic timings"\nmeasurement="measurement.toml"\n'
            )
            self.assertEqual(main([*args, "promote", "report.toml", "--payload", "target/run.json", "--manifest", "target/evidence.json"]), 0)
            (root / "target/run.json").unlink()
            (root / "target/evidence.json").unlink()
            self.assertEqual(main([*args, "promote", "report.toml"]), 0)
            self.assertEqual(load_latest_run(root, "target/history").payload, retained.payload)
            (root / "README.md").write_bytes(b"head\r\n<!-- BEGIN -->\r\nold\r\n<!-- END -->\r\ntail\r\n")
            (root / "releases.txt").write_bytes(b"current=v1.1.0\nprevious=v1.0.0\n")
            publication = "\n".join(
                [
                    "schema=2",
                    'document="README.md"',
                    'begin="<!-- BEGIN -->"',
                    'end="<!-- END -->"',
                    'archive="target/history"',
                    'measurement="measurement.toml"',
                    "release=false",
                    'unit="ns"',
                    'statistic="median"',
                    'rows=[{key="ten", label="Ten", coordinate=10}]',
                    'series=[{name="baseline", label="Before", rows={ten="case/10"}}, {name="current", label="After", rows={ten="case/10"}}]',
                    'comparisons=[{numerator="baseline", denominator="current"}]',
                    'references=[{path="Cargo.toml", pattern=\'^version="(?P<value>[^"]+)"$\', source="version"}, {path="releases.txt", pattern=\'^current=(?P<value>.*)$\', source="tag"}, {path="releases.txt", pattern=\'^previous=(?P<value>.*)$\', source="previous-tag"}]',
                    "[provenance.baseline]",
                    'release="v1.0.0"',
                    'revision="' + "a" * 40 + '"',
                    "[provenance.current]",
                    'release="v1.1.0"',
                    'revision="' + "b" * 40 + '"',
                ]
            )
            (root / "publication.toml").write_text(publication, encoding="utf-8", newline="\n")
            self.assertEqual(main([*args, "publish", "publication.toml"]), 0)
            document = (root / "README.md").read_bytes()
            self.assertIn(b"Cross-phase ratios suppressed", document)
            self.assertTrue(document.startswith(b"head\r\n"))
            self.assertTrue(document.endswith(b"tail\r\n"))

    def test_common_mode_overlays_and_retains_strict_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, current = root / "baseline", root / "current"
            fixture(baseline, "baseline")
            fixture(current, "current")
            (baseline / "marker").write_bytes(b"current")
            plan = replace(declaration(current, harness_mode="common"), backend="worktree")
            host = HostMetadata("OS", "arch", "CPU", 4, 8, 1024, (("python", "fixture-python"),))
            with (
                patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40),
                patch("research_repo_tools.measurement.capture_host", return_value=host),
            ):
                evidence = measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
            self.assertFalse((baseline / "bench-baseline-only").exists())
            self.assertEqual((baseline / "bench.py").read_bytes(), (current / "bench.py").read_bytes())
            self.assertEqual(ratio_incompatibilities(evidence), ())
            source = dict(evidence.sources)["current"]
            changed = replace(source, harness_sha256="f" * 64)
            with self.assertRaisesRegex(ValueError, "provenance differs"):
                validate_run_evidence(replace(evidence, sources=(("baseline", dict(evidence.sources)["baseline"]), ("current", changed))))

    def test_native_children_fail_closed_for_gates_inputs_counts_and_raw_races(self) -> None:
        changes = {
            "failed gate": "if sys.argv[1] == 'gate': raise SystemExit(4)\n",
            "gate output": "if sys.argv[1] == 'gate': Path('target/criterion').mkdir(parents=True)\n",
            "workload change": "if sys.argv[1] == 'measure': Path('cases.json').write_bytes(b'{}')\n",
            "wrong count": "if sys.argv[1] == 'measure': Path('target/criterion/case/10/new/sample.json').write_bytes(b'{\"iters\":[1,1],\"times\":[5,5]}')\n",
            "malformed raw": "if sys.argv[1] == 'measure': Path('target/criterion/case/10/new/tukey.json').write_bytes(b'not-json')\n",
        }
        for name, change in changes.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                baseline, current = root / "baseline", root / "current"
                fixture(baseline, "baseline")
                fixture(current, "current")
                plan = replace(declaration(current), backend="worktree")
                with (current / "bench.py").open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write("\n" + change)
                with patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40), self.assertRaises(ValueError):
                    measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, current = root / "baseline", root / "current"
            fixture(baseline, "baseline")
            fixture(current, "current")
            plan = replace(declaration(current), backend="worktree")
            target = baseline / "target/criterion/case/10/new/tukey.json"
            with (current / "bench.py").open("a", encoding="utf-8", newline="\n") as stream:
                stream.write("\nif sys.argv[1] == 'measure': Path(" + repr(str(target)) + ").write_bytes(b'[]')\n")
            with patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40), self.assertRaisesRegex(ValueError, "observations changed"):
                measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))

    def test_saved_tukey_mismatch_rejects_successful_timer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, current = root / "baseline", root / "current"
            fixture(baseline, "baseline")
            fixture(current, "current")
            plan = replace(declaration(current), backend="worktree")
            with (current / "bench.py").open("a", encoding="utf-8", newline="\n") as stream:
                stream.write("\nif sys.argv[1] == 'measure':\n    Path('target/criterion/case/10/saved/tukey.json').write_bytes(b'[]')\n")
            with patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40), self.assertRaisesRegex(ValueError, "differs from fresh"):
                measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))

    @unittest.skipIf(os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1", "Git mutations disabled before fixture setup")
    def test_native_export_cli_dirty_tree_retention_and_offline_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture(root, "baseline")

            def git(*args: str) -> bytes:
                return subprocess.run(["git", "--no-pager", *args], cwd=root, check=True, capture_output=True).stdout

            git("init")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")
            git("add", ".")
            git("commit", "-m", "baseline")
            git("tag", "v1.0.0")
            fixture(root, "current")
            (root / "bench-baseline-only").unlink()
            git("add", ".")
            git("commit", "-m", "current")
            git("tag", "v1.1.0")
            declaration(root)
            (root / "new.bin").write_bytes(b"\xff\0\r\n")
            (root / "Cargo.toml").unlink()
            source = capture_source(root, working_tree=True)
            self.assertTrue(source.dirty)
            with export_source(source, root.parent / (root.name + "-export")) as exported:
                self.assertEqual((exported / "new.bin").read_bytes(), b"\xff\0\r\n")
                self.assertFalse((exported / "Cargo.toml").exists())
            # Restore package metadata for CLI selection; leave dirty/new input.
            (root / "Cargo.toml").write_bytes(b'[package]\nname="fixture"\nversion="1.1.0"\n')
            before = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            args = ["--root", str(root), "performance"]
            self.assertEqual(
                main([*args, "measure", "measurement.toml", "v1.1.0", "v1.0.0", "--payload", "target/run.json", "--manifest", "target/evidence.json"]), 0
            )
            with patch(
                "research_repo_tools.performance_workflows.published_releases", return_value=(PublishedRelease("v1.0.0", datetime(2026, 1, 1, tzinfo=UTC)),)
            ):
                self.assertEqual(
                    main(
                        [
                            *args,
                            "measure",
                            "measurement.toml",
                            "--mode",
                            "current-vs-latest",
                            "--payload",
                            "target/dirty-run.json",
                            "--manifest",
                            "target/dirty-evidence.json",
                        ]
                    ),
                    0,
                )
            dirty = parse_evidence((root / "target/dirty-run.json").read_bytes(), (root / "target/dirty-evidence.json").read_bytes())
            receipt = json.loads(dict(dict(dirty.sources)["current"].context)["source-export"])
            self.assertEqual(receipt["mode"], "working-tree")
            self.assertTrue(receipt["dirty"])
            self.assertIn("new.bin", [entry[0] for entry in receipt["files"]])
            for name, data in before.items():
                self.assertEqual((root / name).read_bytes(), data, name)
            (root / "report.toml").write_bytes(b'schema=2\ncurrent="target/current.md"\narchive="target/history"\ntitle="Synthetic timings"\n')
            self.assertEqual(main([*args, "promote", "report.toml", "--payload", "target/run.json", "--manifest", "target/evidence.json"]), 0)
            self.assertEqual(parse_run(load_latest_run(root, "target/history").payload).harness_mode, "independent")


if __name__ == "__main__":
    unittest.main()
