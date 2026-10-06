"""Complete-run public contracts, also run against isolated wheel and sdist installs."""

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from research_repo_tools import process
from research_repo_tools.cli import main
from research_repo_tools.common_measurement import CommonHarnessPlan, measure_prepared_pair
from research_repo_tools.complete_runs import (
    CompleteCase,
    CompletePolicy,
    CompleteRun,
    CompleteSample,
    RunSeries,
    collect_complete_sample,
    parse_run,
    render_run,
    run_identity,
    serialize_run,
    validate_run_evidence,
)
from research_repo_tools.evidence import Evidence, deterministic_json, publish_evidence
from research_repo_tools.host_metadata import HostMetadata, capture_host, capture_profile, parse_host, serialize_host
from research_repo_tools.measurement import load_measurement
from research_repo_tools.release_pairs import ReleasePair
from research_repo_tools.run_reports import load_latest_run

BENCHMARK = """import json, os
from pathlib import Path
point = float(Path('src/point.txt').read_text(encoding='utf-8'))
for identity in ['library / 2'] + (['reference / 2'] if os.environ['PHASE'] == 'baseline' else []):
    target = Path('target/criterion') / identity.replace(' / ', '_') / 'new'
    target.mkdir(parents=True)
    def write(name, value):
        (target / name).write_bytes((json.dumps(value) + '\\r\\n').encode('utf-8'))
    write('benchmark.json', {'full_id': identity})
    write('sample.json', {'iters': [1, 2, 3], 'times': [point, point * 2, point * 3]})
    write('estimates.json', {stat: {'point_estimate': point, 'confidence_interval': {'lower_bound': point * .9, 'upper_bound': point * 1.1, 'confidence_level': .95}} for stat in ('mean', 'median')})
"""
GATE = """import os, tomllib
from pathlib import Path
assert os.environ['PHASE'] in {'baseline', 'current'}
assert tomllib.loads(Path('rust-toolchain.toml').read_text(encoding='utf-8'))['toolchain']['channel'] == '1.90.0'
assert tomllib.loads(Path('Cargo.lock').read_text(encoding='utf-8'))['package'][0]['version'] == '2.0.0'
assert float(Path('src/point.txt').read_text(encoding='utf-8')) > 0
"""


def fixture(root: Path) -> CommonHarnessPlan:
    root.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / "src/point.txt").write_bytes(b"5")
    (root / "Cargo.toml").write_bytes(b'[package]\nname="fixture"\nversion="1.1.0"\n')
    (root / "Cargo.lock").write_bytes(b'[[package]]\nname="reference"\nversion="2.0.0"\n')
    (root / "rust-toolchain.toml").write_bytes(b'[toolchain]\nchannel="1.90.0"\n')
    (root / "bench.py").write_text(BENCHMARK, encoding="utf-8", newline="\n")
    (root / "gate.py").write_text(GATE, encoding="utf-8", newline="\n")
    python = json.dumps(sys.executable)
    config = f"""schema=2
sources=["src/*.txt", "Cargo.toml"]
harness=["bench.py", "gate.py", "Cargo.lock", "Cargo.toml", "rust-toolchain.toml"]
sample-count=3
[probes]
python=[{python}, "--version"]
[dependencies]
reference="Cargo.lock"
[phases.baseline]
command=[{python}, "bench.py"]
gate=[{python}, "gate.py"]
expected=["library / 2", "reference / 2"]
environment={{PHASE="baseline"}}
[phases.current]
command=[{python}, "bench.py"]
gate=[{python}, "gate.py"]
expected=["library / 2"]
environment={{PHASE="current"}}
[[series]]
name="Library baseline"
phase="baseline"
rows={{"size 2"="library / 2"}}
[[series]]
name="Library current"
phase="current"
rows={{"size 2"="library / 2"}}
[[series]]
name="Reference reused"
phase="baseline"
rows={{"size 2"="reference / 2"}}
"""
    (root / "benchmark.toml").write_text(config, encoding="utf-8", newline="\n")
    plan = load_measurement(root, "benchmark.toml")
    assert isinstance(plan, CommonHarnessPlan)
    return plan


def measured(parent: Path, *, point: bytes = b"10", same_label: bool = False) -> Evidence:
    current, baseline = parent / "current", parent / "baseline"
    plan = fixture(current)
    shutil.copytree(current, baseline)
    (baseline / "src/point.txt").write_bytes(point)
    (baseline / "bench.py").write_bytes(b'raise RuntimeError("old harness must not run")\n')
    (baseline / "Cargo.lock").write_bytes(b'[[package]]\nname="reference"\nversion="1.0.0"\n')
    (baseline / "Cargo.toml").write_bytes(b'[package]\nname="fixture"\nversion="1.0.0"\n')
    with patch("research_repo_tools.measurement.resolve_revision", side_effect=lambda root, ref: ("a" if root.name == "baseline" else "b") * 40):
        return measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.1.0" if same_label else "v1.0.0"))


class TestCompleteConsumer(unittest.TestCase):
    def test_generic_cpu_labels_do_not_become_known_provenance(self):
        # Replace only the process module's provider; global OS identity is intact.
        for reported in ("arm64", "unavailable", "unknown"):
            provider = SimpleNamespace(system=lambda: "Windows", machine=lambda: "arm64", processor=lambda: "arm64")
            with patch.object(process, "platform", provider), patch.dict(process.os.environ, {"PROCESSOR_IDENTIFIER": reported}):
                self.assertEqual(process.cpu_description(), "unavailable")

    def test_common_harness_samples_reference_phase_and_exact_bytes(self):
        with tempfile.TemporaryDirectory(prefix="complete café ") as directory:
            root = Path(directory).resolve()
            evidence = measured(root, same_label=True)
            run = validate_run_evidence(evidence)
            self.assertEqual(parse_run(serialize_run(run)), run)
            baseline, current = dict(run.phases)["baseline"], dict(run.phases)["current"]
            self.assertEqual(len(baseline.cases), 2)
            self.assertEqual(len(current.cases), 1)
            self.assertEqual(baseline.cases[0].full_id, "library / 2")
            self.assertEqual(baseline.cases[0].storage_path, "library_2")
            self.assertTrue(baseline.cases[0].sample.endswith(b"\r\n"))
            self.assertEqual(baseline.cases[0].estimate("mean").point, 10)
            self.assertEqual(current.cases[0].estimate("median").point, 5)
            sources = dict(evidence.sources)
            self.assertEqual(sources["baseline"].harness_sha256, sources["current"].harness_sha256)
            self.assertNotEqual(dict(sources["baseline"].context)["original-source-sha256"], sources["baseline"].source_sha256)
            report = render_run(evidence).decode()
            self.assertIn("Reference reused: measured in **baseline**", report)
            self.assertIn("Mean", report)
            self.assertIn("Median", report)
            with self.assertRaisesRegex(ValueError, "unmeasured"):
                CompleteRun(run.phases, (RunSeries("false rerun", "current", (("size 2", "reference / 2"),)),))
            wrong = replace(sources["baseline"], context=tuple({**dict(sources["baseline"].context), "phase": "current"}.items()))
            with self.assertRaisesRegex(ValueError, "attributed"):
                validate_run_evidence(replace(evidence, sources=(("baseline", wrong), ("current", sources["current"]))))
            with self.assertRaisesRegex(ValueError, "unknown"):
                run_unknown = replace(run, compatible=("harness_sha256", "context.unknown"))
                validate_run_evidence(replace(evidence, payload=serialize_run(run_unknown)))

    def test_cli_measure_promote_repeat_runs_offline_and_corrupt_pointer(self):
        with tempfile.TemporaryDirectory(prefix="retention café ") as directory:
            root = Path(directory).resolve()
            fixture(root)
            (root / "report.toml").write_bytes(b'schema=2\ncurrent="docs/current.md"\narchive="docs/runs"\ntitle="Timings"\n')

            @contextmanager
            def worktree(source, destination, revision, **kwargs):
                fixture(destination)
                (destination / "src/point.txt").write_bytes(b"10" if destination.name == "baseline" else b"5")
                try:
                    yield destination
                finally:
                    shutil.rmtree(destination)

            base = ["--root", str(root), "performance"]
            with (
                patch("research_repo_tools.measurement.temporary_worktree", worktree),
                patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40),
            ):
                self.assertEqual(
                    main(
                        [
                            *base,
                            "measure",
                            "benchmark.toml",
                            "v1.1.0",
                            "v1.0.0",
                            "--allow-git-mutations",
                            "--payload",
                            "target/run.json",
                            "--manifest",
                            "target/evidence.json",
                            "--report",
                            "target/report.md",
                        ]
                    ),
                    0,
                )
            self.assertEqual(main([*base, "promote", "report.toml", "--payload", "target/run.json", "--manifest", "target/evidence.json"]), 0)
            first = load_latest_run(root, "docs/runs")
            first_files = {path: path.read_bytes() for path in (root / "docs/runs/runs").rglob("*") if path.is_file()}
            self.assertEqual(main([*base, "promote", "report.toml", "--payload", "target/run.json", "--manifest", "target/evidence.json"]), 0)
            # Change complete measurements while preserving the release pair.
            second = measured(root / "scratch", point=b"12")
            publish_evidence(second, root / "target/run.json", root / "target/evidence.json")
            self.assertEqual(main([*base, "promote", "report.toml", "--payload", "target/run.json", "--manifest", "target/evidence.json"]), 0)
            self.assertNotEqual(run_identity(first), run_identity(second))
            self.assertEqual(len(list((root / "docs/runs/runs").iterdir())), 2)
            self.assertEqual(first_files, {path: path.read_bytes() for path in first_files})
            shutil.rmtree(root / "target")
            shutil.rmtree(root / "scratch")
            with patch("research_repo_tools.host_metadata.capture_host", side_effect=AssertionError("offline must not probe")):
                self.assertEqual(main([*base, "promote", "report.toml", "--check"]), 0)
                self.assertEqual(load_latest_run(root, "docs/runs"), second)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main([*base, "promote", "report.toml", "--payload", "target/run.json", "--manifest", "target/evidence.json"]), 1)
            pointer = root / "docs/runs/latest.json"
            pointer.write_bytes(pointer.read_bytes().replace(run_identity(second).encode(), b"0" * 64))
            before = (root / "docs/current.md").read_bytes()
            with self.assertRaisesRegex(ValueError, "pointer"):
                load_latest_run(root, "docs/runs")
            self.assertEqual((root / "docs/current.md").read_bytes(), before)

    def test_completeness_rejects_missing_samples_statistics_intervals_and_duplicate_ids(self):
        policy = CompletePolicy(("semantic / id",), sample_count=3)
        benchmark = b'{"full_id":"semantic / id"}'
        estimates = deterministic_json(
            {stat: {"point_estimate": 2, "confidence_interval": {"lower_bound": 1, "upper_bound": 3, "confidence_level": 0.95}} for stat in ("mean", "median")}
        )
        sample = b'{"iters":[1,2,3],"times":[2,4,6]}'
        valid = CompleteCase("sanitized", benchmark, estimates, sample)
        self.assertEqual(CompleteSample(policy, (valid,)).cases, (valid,))
        variants = [
            replace(valid, benchmark=b'{"full_id":"stale"}'),
            replace(valid, sample=b'{"iters":[1,2],"times":[2,4]}'),
            replace(valid, estimates=estimates.replace(b"0.95", b"0.9")),
            replace(valid, estimates=b'{"median":{"point_estimate":2}}'),
        ]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                CompleteSample(policy, (variant,))
        with self.assertRaisesRegex(ValueError, "unique"):
            CompleteSample(policy, (valid, replace(valid, storage_path="another")))
        for raw in (b'{"iters":[1,2],"times":[2]}', b'{"iters":[0,2],"times":[2,4]}', b'{"iters":[1.5,2],"times":[2,4]}', b'{"iters":[1,2],"times":[true,4]}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                replace(valid, sample=raw)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "sanitized/new"
            path.mkdir(parents=True)
            (path / "estimates.json").write_bytes(estimates)
            with self.assertRaisesRegex(ValueError, "incomplete"):
                collect_complete_sample(root, "new", policy)

    def test_failed_gate_runs_no_timing_and_source_mutation_rejects(self):
        for failure in ("baseline-gate", "current-gate", "source", "harness", "scratch", "other-scratch"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                current, baseline = root / "current", root / "baseline"
                plan = fixture(current)
                shutil.copytree(current, baseline)
                if failure.endswith("-gate"):
                    (root / failure.removesuffix("-gate") / "src/point.txt").write_bytes(b"-1")
                elif failure in {"source", "harness"}:
                    target = "src/point.txt" if failure == "source" else "Cargo.lock"
                    with (current / "bench.py").open("a", encoding="utf-8", newline="\n") as stream:
                        stream.write(f"\nPath({target!r}).write_bytes(b'changed')\n")
                elif failure == "other-scratch":
                    with (current / "bench.py").open("a", encoding="utf-8", newline="\n") as stream:
                        stream.write(f"\nif os.environ['PHASE'] == 'baseline':\n    Path({str(current / 'target/criterion')!r}).mkdir(parents=True)\n")
                else:
                    (baseline / "target/criterion/partial").mkdir(parents=True)
                with (
                    patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40),
                    self.assertRaises((ValueError, subprocess.CalledProcessError)) as raised,
                ):
                    measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
                if failure.endswith("-gate"):
                    self.assertIn(f"{failure.removesuffix('-gate')} preflight gate failed", str(raised.exception))
                    self.assertFalse((current / "target").exists())
                    self.assertFalse((baseline / "target").exists())

    def test_declared_environment_compatibility_precedes_gates_and_timing(self):
        for matching in (True, False):
            with self.subTest(matching=matching), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                current, baseline = root / "current", root / "baseline"
                plan = fixture(current)
                (current / "bench.py").write_text(BENCHMARK.replace("os.environ['PHASE']", "Path.cwd().name"), encoding="utf-8", newline="\n")
                (current / "gate.py").write_text(GATE.replace("os.environ['PHASE']", "Path.cwd().name"), encoding="utf-8", newline="\n")
                shutil.copytree(current, baseline)
                plan = replace(
                    plan,
                    measurement=replace(plan.measurement, compatible=(*plan.measurement.compatible, "context.environment", "context.gate-command")),
                    baseline=replace(plan.baseline, environment=(("OMP_NUM_THREADS", "1"),)),
                    current=replace(plan.current, environment=(("OMP_NUM_THREADS", "1" if matching else "2"),)),
                )
                with patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40):
                    if matching:
                        evidence = measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
                        validate_run_evidence(evidence)
                        for _, source in evidence.sources:
                            self.assertEqual(json.loads(dict(source.context)["environment"]), {"OMP_NUM_THREADS": "1"})
                            self.assertEqual(dict(source.context)["gate-status"], "passed")
                    else:
                        with patch("research_repo_tools.common_measurement.run_command_live") as run, self.assertRaisesRegex(ValueError, "before timing"):
                            measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
                        run.assert_not_called()
                        self.assertFalse((current / "target").exists())
                        self.assertFalse((baseline / "target").exists())

    def test_sample_names_can_also_name_groups_and_output_roots(self):
        for root_name, storage in (("criterion", "new/case"), ("new", "case"), ("criterion", "new")):
            with self.subTest(root_name=root_name, storage=storage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / root_name
                sample = root / storage / "new"
                sample.mkdir(parents=True)
                (sample / "benchmark.json").write_bytes(b'{"full_id":"semantic / id"}')
                (sample / "sample.json").write_bytes(b'{"iters":[1,2,3],"times":[2,4,6]}')
                (sample / "estimates.json").write_bytes(
                    deterministic_json(
                        {
                            stat: {"point_estimate": 2, "confidence_interval": {"lower_bound": 1, "upper_bound": 3, "confidence_level": 0.95}}
                            for stat in ("mean", "median")
                        }
                    )
                )
                policy = CompletePolicy(("semantic / id",), sample_count=3)
                self.assertEqual(collect_complete_sample(root, "new", policy).cases[0].storage_path, storage)
                (sample / "sample.json").unlink()
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    collect_complete_sample(root, "new", policy)

    def test_profile_retains_native_toml_scalars_and_exact_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native = (
                b"[package.metadata]\r\nrecorded = 2026-10-05\r\n"
                b"local = 2026-10-05T12:34:56.123456\r\noffset = 2026-10-05T12:34:56-07:00\r\n"
                b"nested = [{time = 12:34:56.123456, floats = [nan, +inf, -inf]}]\r\n"
            )
            (root / "Cargo.toml").write_bytes(native)
            (root / "profile.toml").write_bytes(b'schema=1\ncargo-manifest="Cargo.toml"\n[probes]\n')
            unknown = HostMetadata(None, None, None, None, None, None)
            with patch("research_repo_tools.host_metadata.capture_host", return_value=unknown):
                payload = capture_profile(root, "profile.toml")
                self.assertEqual(capture_profile(root, "profile.toml"), payload)
            declaration = json.loads(payload)["declarations"]["cargo-manifest"]
            self.assertEqual(declaration["text"].encode("utf-8"), native)
            metadata = declaration["toml"]["package"]["metadata"]
            for key, kind, value in (
                ("recorded", "date", "2026-10-05"),
                ("local", "datetime", "2026-10-05T12:34:56.123456"),
                ("offset", "datetime", "2026-10-05T12:34:56-07:00"),
            ):
                self.assertEqual(metadata[key], {"toml_type": kind, "value": value})
            nested = metadata["nested"][0]
            self.assertEqual(nested["time"], {"toml_type": "time", "value": "12:34:56.123456"})
            self.assertEqual(nested["floats"], [{"toml_type": "float", "value": value} for value in ("nan", "inf", "-inf")])

    def test_host_unknowns_profile_native_declarations_and_cli_output(self):
        unknown = HostMetadata(None, None, None, None, None, None, (("missing", None),))
        with patch("research_repo_tools.host_metadata.capture_host", side_effect=AssertionError("must not recapture")):
            self.assertEqual(parse_host(serialize_host(unknown)), unknown)
        with tempfile.TemporaryDirectory(prefix="profiling café ") as directory:
            root = Path(directory).resolve()
            (root / "rust-toolchain.toml").write_bytes(b"[toolchain]\nchannel = '1.90.0' # native TOML\ncomponents=['clippy']\n")
            (root / "Cargo.toml").write_bytes(b"[workspace.package]\nrust-version='1.90'\n[package]\nrust-version.workspace=true\n")
            (root / "profile.toml").write_text(
                'schema=1\nrust-toolchain="rust-toolchain.toml"\ncargo-manifest="Cargo.toml"\n[context]\nmode="development"\nfilter="case café"\n[probes]\npython='
                + json.dumps([sys.executable, "--version"])
                + '\nmissing=["rrt-nonexistent-executable"]\n',
                encoding="utf-8",
                newline="\n",
            )
            profile = json.loads(capture_profile(root, "profile.toml"))
            self.assertIsNone(profile["host"]["tools"]["missing"])
            self.assertIn("Python", profile["host"]["tools"]["python"])
            self.assertEqual(profile["declarations"]["cargo-manifest"]["toml"]["package"]["rust-version"], {"workspace": True})
            self.assertEqual(profile["declarations"]["rust-toolchain"]["toml"]["toolchain"]["channel"], "1.90.0")
            host = capture_host(root)
            self.assertTrue(host.logical_threads is None or host.logical_threads > 0)
            base = ["--root", str(root), "performance"]
            self.assertEqual(main([*base, "profile", "profile.toml", "--output", "results café/profile.json"]), 0)
            self.assertEqual(main([*base, "host", "--output", "results café/host.json"]), 0)
            parse_host((root / "results café/host.json").read_bytes())
            before = (root / "Cargo.toml").read_bytes()
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main([*base, "profile", "profile.toml", "--output", "Cargo.toml"]), 1)
            self.assertEqual((root / "Cargo.toml").read_bytes(), before)
            fixture(root)
            configuration = (root / "profile.toml").read_text(encoding="utf-8").replace("schema=1", 'schema=1\nmeasurement="benchmark.toml"\nrelease="v1.1.0"')
            (root / "profile.toml").write_text(configuration, encoding="utf-8", newline="\n")
            with patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40):
                captured = json.loads(capture_profile(root, "profile.toml"))
                self.assertEqual(captured["source"]["revision"], "a" * 40)
                self.assertEqual(captured["source"]["context"]["mode"], "profiling")
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(main([*base, "profile", "profile.toml", "--output", "src/point.txt"]), 1)
                self.assertEqual((root / "src/point.txt").read_bytes(), b"5")


if __name__ == "__main__":
    unittest.main()
