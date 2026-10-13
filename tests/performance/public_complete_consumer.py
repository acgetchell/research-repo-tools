"""Complete-run public contracts, also run against isolated wheel and sdist installs."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import replace
from html import unescape
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote

from research_repo_tools import process
from research_repo_tools.cli import main
from research_repo_tools.common_measurement import CommonHarnessPlan, DiscoveryPolicy, measure_prepared_pair
from research_repo_tools.complete_runs import (
    RUN_SCHEMA,
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
from research_repo_tools.criterion_inventory import DiscoveryCommand, DiscoveryPlan, parse_inventory
from research_repo_tools.evidence import Evidence, Provenance, deterministic_json, parse_evidence, publish_evidence, serialize_evidence
from research_repo_tools.host_metadata import HostMetadata, capture_host, capture_profile, parse_host, serialize_host
from research_repo_tools.measurement import load_measurement
from research_repo_tools.publication import publish_publication
from research_repo_tools.release_pairs import ReleasePair
from research_repo_tools.run_reports import load_latest_run, load_run_report_plan

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

# A small renderer-1 report fixture, independent of the current renderer. Its
# identity comes from exact evidence bytes; the historical presentation is fixed.
LEGACY_REPORT = """# Benchmark timings

Run: `{identity}`

Intervals are marginal timing intervals. No significance or ratio interval is inferred.

- Library baseline: measured in **baseline** phase (v1.0.0), revision `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`.
- Library current: measured in **current** phase (v1.1.0), revision `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`.
- Reference reused: measured in **baseline** phase (v1.0.0), revision `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`.

## Mean (ns)

| Case | Library baseline | Library current | Reference reused |
| --- | --- | --- | --- |
| size 2 | 10 ns [9, 11] (0.95 confidence) | 5 ns [4.5, 5.5] (0.95 confidence) | 10 ns [9, 11] (0.95 confidence) |

## Median (ns)

| Case | Library baseline | Library current | Reference reused |
| --- | --- | --- | --- |
| size 2 | 10 ns [9, 11] (0.95 confidence) | 5 ns [4.5, 5.5] (0.95 confidence) | 10 ns [9, 11] (0.95 confidence) |
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


def retained_fixture(*, baseline_point: float = 10) -> Evidence:
    """Synthetic retained inputs for rendering/publication, without acquisition."""
    phases, sources = [], []
    for phase, point, identities, revision in (
        ("baseline", baseline_point, ("library / 2", "reference / 2"), "a" * 40),
        ("current", 5, ("library / 2",), "b" * 40),
    ):
        cases = []
        for identity in identities:
            estimates = {
                stat: {"point_estimate": point, "confidence_interval": {"lower_bound": point * 0.9, "upper_bound": point * 1.1, "confidence_level": 0.95}}
                for stat in ("mean", "median")
            }
            native = (
                {"full_id": identity},
                estimates,
                {"iters": [1, 2, 3], "times": [point, point * 2, point * 3]},
            )
            cases.append(CompleteCase(identity.replace(" / ", "_"), *((json.dumps(value) + "\r\n").encode("utf-8") for value in native)))
        phases.append((phase, CompleteSample(CompletePolicy(identities, sample_count=3), tuple(cases))))
        context = {
            "phase": phase,
            "release": "v1.0.0" if phase == "baseline" else "v1.1.0",
            "mode": "tag",
            "command": json.dumps(["python", "bench.py"]),
            "gate-command": json.dumps(["python", "gate.py"]),
            "gate-status": "passed",
            "environment": json.dumps({"PHASE": phase}),
            "host": json.dumps(json.loads(serialize_host(HostMetadata("fixture OS", "fixture arch", None, None, None, None, (("python", "Python fixture"),))))),
            "source-inventory": json.dumps(["src/point.txt", "Cargo.toml"]),
            "original-source-inventory": json.dumps(["src/point.txt", "Cargo.toml"]),
            "harness-inventory": json.dumps(["bench.py", "gate.py", "Cargo.lock", "Cargo.toml", "rust-toolchain.toml"]),
            "original-source-sha256": "1" * 64 if phase == "baseline" else "2" * 64,
            "fingerprint-schema": "research-repo-tools/files/v1",
            "tool.python": "Python fixture",
            "dependency.reference": "2.0.0",
        }
        sources.append((phase, Provenance(revision, "3" * 64 if phase == "baseline" else "2" * 64, "4" * 64, tuple(context.items()))))
    series = (
        RunSeries("Library baseline", "baseline", (("size 2", "library / 2"),)),
        RunSeries("Library current", "current", (("size 2", "library / 2"),)),
        RunSeries("Reference reused", "baseline", (("size 2", "reference / 2"),)),
    )
    run = CompleteRun(tuple(phases), series, ("harness_sha256", "context.tool.python", "context.dependency.reference"))
    evidence = Evidence(serialize_run(run), RUN_SCHEMA, tuple(sources))
    validate_run_evidence(evidence)
    return evidence


class TestCompleteConsumer(unittest.TestCase):
    def test_required_discovery_compatibility_is_checked_before_gates(self):
        for matching in (True, False, "missing"):
            with self.subTest(matching=matching), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                current, baseline = root / "current", root / "baseline"
                plan = fixture(current)
                bench = BENCHMARK.replace("(['reference / 2'] if os.environ['PHASE'] == 'baseline' else [])", "['reference / 2']")
                (current / "bench.py").write_text(bench, encoding="utf-8", newline="\n")
                fixture(baseline)
                command = DiscoveryCommand("suite", (sys.executable, "-c", "print('library / 2: benchmark'); print('reference / 2: benchmark')"))
                policy = DiscoveryPolicy(DiscoveryPlan((command,)), sample_count=3)
                plan = replace(
                    plan,
                    measurement=replace(plan.measurement, compatible=(*plan.measurement.compatible, "context.discovery-inventory")),
                    baseline=replace(plan.baseline, policy=policy),
                    current=replace(
                        plan.current, policy=policy if matching is True else DiscoveryPolicy(DiscoveryPlan((replace(command, name="other"),)), sample_count=3)
                    ),
                )
                if matching == "missing":
                    plan = replace(plan, current=replace(plan.current, policy=CompletePolicy(("library / 2", "reference / 2"), sample_count=3)))
                with patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40):
                    if matching is True:
                        evidence = measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
                        validate_run_evidence(evidence)
                        self.assertEqual(
                            dict(evidence.sources[0][1].context)["discovery-inventory"], dict(evidence.sources[1][1].context)["discovery-inventory"]
                        )
                    else:
                        with patch("research_repo_tools.common_measurement.run_command_live") as live, self.assertRaisesRegex(ValueError, "before timing"):
                            measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
                        live.assert_not_called()

    def test_mixed_case_phase_path_reaches_probes_discovery_gates_and_measurement(self):
        with tempfile.TemporaryDirectory(prefix="phase path café ") as directory:
            root = Path(directory)
            current, baseline = root / "current", root / "baseline"
            plan = fixture(current)
            fixture(baseline)
            python = Path(sys.executable)
            selected = str(python.parent)
            check = f"import os\nassert os.environ['PATH'] == {selected!r}\n"
            for name in ("bench.py", "gate.py"):
                path = current / name
                path.write_text(check + path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
            phases = {}
            for phase in ("baseline", "current"):
                original = getattr(plan, phase)
                listing = (
                    "import os; assert os.environ['PATH'] == "
                    + repr(selected)
                    + "; "
                    + "; ".join(f"print({identity + ': benchmark'!r})" for identity in original.policy.expected)
                )
                discovery = DiscoveryPolicy(DiscoveryPlan((DiscoveryCommand("suite", (python.name, "-c", listing)),)), sample_count=3)
                phases[phase] = replace(
                    original,
                    command=(python.name, *original.command[1:]),
                    gate=(python.name, *original.gate[1:]),
                    environment=(*original.environment, ("Path", selected)),
                    policy=discovery,
                )
            plan = replace(plan, measurement=replace(plan.measurement, probes=(("python", (python.name, "--version")),)), **phases)
            with patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40), patch.dict(os.environ, {"PATH": str(root / "absent")}):
                evidence = measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
            self.assertEqual(len(dict(validate_run_evidence(evidence).phases)["current"].cases), 1)

    def test_retained_environments_preserve_constructor_invariants(self):
        evidence = retained_fixture()
        for environment in ({"A": "1", "a": "2"}, {"INVALID=KEY": "1"}, {" KEY": "1"}, {"KEY\v": "1"}, {"KEY": "\0"}):
            with self.subTest(environment=environment):
                name, source = evidence.sources[0]
                changed = replace(source, context=tuple({**dict(source.context), "environment": json.dumps(environment)}.items()))
                with self.assertRaises(ValueError):
                    validate_run_evidence(replace(evidence, sources=((name, changed), evidence.sources[1])))
        name, source = evidence.sources[0]
        changed = replace(source, context=tuple({**dict(source.context), "environment": '{"EMPTY":""}'}.items()))
        validate_run_evidence(replace(evidence, sources=((name, changed), evidence.sources[1])))

    def test_common_plan_native_discovery_before_gates_retains_inventory(self):
        with tempfile.TemporaryDirectory(prefix="discovered café ") as directory:
            root = Path(directory)
            current, baseline = root / "current", root / "baseline"
            fixture(current)
            listing = """import sys
if '--list' in sys.argv:
    from pathlib import Path
    import os
    home = Path(os.environ['CRITERION_HOME'])
    (home / 'private-list-output').write_bytes(b'discovery')
    for identity in ['library / 2'] + (['reference / 2'] if os.environ['PHASE'] == 'baseline' else []):
        sys.stdout.buffer.write((identity + ': benchmark\\r\\n').encode('utf-8'))
    sys.exit(0)
"""
            (current / "bench.py").write_text(listing + BENCHMARK, encoding="utf-8", newline="\n")
            config = (current / "benchmark.toml").read_text(encoding="utf-8")
            command = json.dumps([sys.executable, "-X", "utf8", "bench.py", "--list"])
            for expected in ('["library / 2", "reference / 2"]', '["library / 2"]'):
                config = config.replace(f"expected={expected}", 'discovery={suites=[{name="native", command=' + command + ", timeout=20}]}")
            (current / "benchmark.toml").write_text(config, encoding="utf-8", newline="\n")
            plan = load_measurement(current, "benchmark.toml")
            assert isinstance(plan, CommonHarnessPlan)
            self.assertIsInstance(plan.baseline.policy, DiscoveryPolicy)
            shutil.copytree(current, baseline)
            (baseline / "src/point.txt").write_bytes(b"10")
            with patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40):
                evidence = measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
            run = validate_run_evidence(evidence)
            for phase, source in evidence.sources:
                inventory = parse_inventory(dict(source.context)["discovery-inventory"].encode("utf-8"))
                self.assertEqual(inventory.expected, dict(run.phases)[phase].policy.expected)
                self.assertFalse(any((root / phase).rglob("private-list-output")))
            context = dict(evidence.sources[0][1].context)
            context["discovery-inventory"] = context["discovery-inventory"].replace("library / 2", "wrong ID")
            altered = replace(evidence.sources[0][1], context=tuple(context.items()))
            with self.assertRaisesRegex(ValueError, "discovered inventory differs"):
                validate_run_evidence(replace(evidence, sources=((evidence.sources[0][0], altered), evidence.sources[1])))

    def test_discovery_missing_series_or_leaked_output_stops_before_gates(self):
        for failure in ("missing series", "leaked output"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                current, baseline = root / "current", root / "baseline"
                plan = fixture(current)
                fixture(baseline)
                code = (
                    "print('unselected: benchmark')"
                    if failure == "missing series"
                    else (
                        "from pathlib import Path; Path('target/criterion').mkdir(parents=True); "
                        "print('library / 2: benchmark'); print('reference / 2: benchmark')"
                    )
                )
                policy = DiscoveryPolicy(DiscoveryPlan((DiscoveryCommand("suite", (sys.executable, "-c", code)),)), sample_count=3)
                plan = replace(plan, baseline=replace(plan.baseline, policy=policy))
                with (
                    patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40),
                    patch("research_repo_tools.common_measurement.run_command_live") as live,
                    self.assertRaisesRegex(ValueError, "omits series IDs|discovery produced timing output"),
                ):
                    measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
                live.assert_not_called()
                if failure == "leaked output":
                    self.assertTrue((baseline / "target/criterion").is_dir())

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

    def test_recorded_provenance_unknowns_escaping_redaction_and_offline_determinism(self):
        with patch("research_repo_tools.common_measurement.capture_provenance", side_effect=AssertionError("retained setup must not probe")):
            evidence = retained_fixture()
        evidence = replace(evidence, payload=serialize_run(replace(validate_run_evidence(evidence), compatible=("harness_sha256",))))
        sources = []
        for phase, source in evidence.sources:
            host = HostMetadata(
                "measured <OS>",
                "recorded-arch",
                None,
                None,
                8,
                4096,
                (("rustc", "rustc 1.90\r\nhost: <arm64>|`*\x1b"), ("missing", None)) if phase == "baseline" else (),
            )
            context = {**dict(source.context), "host": json.dumps(json.loads(serialize_host(host)))}
            context.pop("mode")
            context["private-note"] = "PRIVATE CONTEXT"
            context["environment"] = json.dumps({"API_TOKEN": "recorded-secret", "OMP_NUM_THREADS": "77"})
            context["command"] = json.dumps(
                [
                    "cargo",
                    "bench",
                    "--phase",
                    phase,
                    "--token",
                    "recorded-secret",
                    "--password=another-secret",
                    "Authorization: Bearer header-secret",
                    "https://user:pass@example.test/signed?token=value",
                    "<script>|`*",
                ]
            )
            context["gate-command"] = json.dumps(["python", "gate.py", "--phase", phase, "-c", "INLINE SECRET"])
            if phase == "current":
                context.pop("dependency.reference")
            sources.append((phase, replace(source, context=tuple(context.items()))))
        evidence = replace(evidence, sources=tuple(sources))
        original = serialize_evidence(evidence)
        with (
            patch("research_repo_tools.host_metadata.capture_host", side_effect=AssertionError("must not capture publisher")),
            patch("research_repo_tools.measurement.capture_provenance", side_effect=AssertionError("must not probe")),
            patch.dict(os.environ, {"API_TOKEN": "publisher-secret"}),
        ):
            report = render_run(evidence).decode()
            self.assertEqual(render_run(evidence).decode(), report)
        self.assertEqual(serialize_evidence(evidence), original)
        self.assertIn("Reference reused: measured in **baseline**", report)
        baseline, current = report.split("### Baseline phase", 1)[1].split("### Current phase", 1)
        self.assertIn("| Dependency: reference | 2.0.0 |", baseline)
        self.assertIn("| Dependency versions | not recorded |", current)
        self.assertIn("| Tools / toolchain | not recorded |", current)
        self.assertIn("| Tool: missing | not recorded |", baseline)
        self.assertIn("| Measurement mode | not recorded |", baseline)
        self.assertIn("| CPU | not recorded |", report)
        self.assertIn("| Logical threads | 8 |", report)
        self.assertIn("rustc 1.90<br>host: &lt;arm64&gt;&#124;&#96;&#42;", report)
        self.assertIn("&lt;script&gt;&#124;&#96;&#42;", report)
        self.assertIn('"--phase", "baseline"', baseline.replace("&quot;", '"'))
        self.assertIn('"--phase", "current"', current.replace("&quot;", '"'))
        self.assertEqual(report.count("| Independent correctness gate | passed |"), 2)
        for secret in (
            "recorded-secret",
            "another-secret",
            "header-secret",
            "INLINE SECRET",
            "user:pass",
            "token=value",
            "publisher-secret",
            "PRIVATE CONTEXT",
        ):
            self.assertNotIn(secret, report)
        self.assertNotIn("77", report.split("| Environment overrides", 1)[1].split("\n", 1)[0])
        self.assertNotIn("\x1b", report)
        self.assertIn("Evidence links: publication paths not supplied.", report)
        for phase, source in evidence.sources:
            section = baseline if phase == "baseline" else current
            self.assertIn(source.source_sha256, section)
            self.assertIn(dict(source.context)["original-source-sha256"], section)
            self.assertIn(source.harness_sha256, section)
        with self.assertRaisesRegex(ValueError, "paths together"):
            render_run(evidence, report_path="report.md")
        for path in ("../report.md", "C:/report.md", "docs\\report.md", "https://example.test/report.md"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                render_run(evidence, report_path=path, payload_path="run.json", manifest_path="evidence.json")
        with self.assertRaises(ValueError):
            render_run(evidence, title="Injected\n# heading")

    def test_publication_relative_links_resolve_to_exact_retained_bytes(self):
        with tempfile.TemporaryDirectory(prefix="links café ") as directory:
            root = Path(directory).resolve()
            evidence = retained_fixture()
            (root / "report.toml").write_text(
                'schema=2\ncurrent="reports/deep/café report.md"\narchive="history/café retained (#96)"\ntitle="Timing"\n', encoding="utf-8", newline="\n"
            )
            publish_evidence(evidence, root / "new.json", root / "manifest.json")
            publish_publication(load_run_report_plan(root, "report.toml", payload="new.json", manifest="manifest.json"))
            identity = run_identity(evidence)
            archive = root / "history/café retained (#96)"
            expected = serialize_evidence(evidence)
            for report_path in (root / "reports/deep/café report.md", archive / f"runs/{identity}/report.md"):
                report = report_path.read_text(encoding="utf-8")
                links = [report.split(f"[{label}](", 1)[1].split(")", 1)[0] for label in ("run payload", "evidence envelope")]
                linked = [(report_path.parent / unquote(link)).resolve() for link in links]
                self.assertEqual(linked, [archive / f"runs/{identity}/run.json", archive / f"runs/{identity}/evidence.json"])
                self.assertEqual(tuple(path.read_bytes() for path in linked), expected)
                self.assertTrue(all("\\" not in link and " " not in link and "#" not in link for link in links))
            self.assertIn("../../history/caf%C3%A9%20retained%20%28%2396%29/", (root / "reports/deep/café report.md").read_text(encoding="utf-8"))
            (root / "new.json").unlink()
            (root / "manifest.json").unlink()
            with patch("research_repo_tools.host_metadata.capture_host", side_effect=AssertionError("offline must not probe")):
                self.assertFalse(load_run_report_plan(root, "report.toml").changed_paths)
            index = json.loads((archive / "index.json").read_bytes())
            self.assertEqual(index["schema"], "research-repo-tools/run-index/v2")
            self.assertEqual(index["runs"][0]["report_renderer"], 2)
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            for renderer in (True, 0, 3, "2", None):
                with self.subTest(renderer=renderer):
                    index["runs"][0]["report_renderer"] = renderer
                    (archive / "index.json").write_bytes(deterministic_json(index))
                    with self.assertRaisesRegex(ValueError, "renderer"):
                        load_run_report_plan(root, "report.toml")
                    self.assertEqual(
                        {path: path.read_bytes() for path in before if path.name != "index.json"},
                        {path: data for path, data in before.items() if path.name != "index.json"},
                    )
            (archive / "index.json").write_bytes(before[archive / "index.json"])
            retained = archive / f"runs/{identity}/report.md"
            retained.write_bytes(retained.read_bytes().replace(b"run.json", b"other.json"))
            with self.assertRaisesRegex(ValueError, "report do not match"):
                load_latest_run(root, "history/café retained (#96)")

    def test_known_credentials_are_redacted_before_argv_serialization(self):
        evidence = retained_fixture()
        for secret in ('opaque"value', "opaque\\value"):
            with self.subTest(secret=secret):
                sources = []
                for phase, source in evidence.sources:
                    context = {**dict(source.context), "environment": json.dumps({"API_TOKEN": secret})}
                    context["command"] = json.dumps(["bench", "--input", f"prefix:{secret}:suffix"])
                    context["gate-command"] = json.dumps(["gate", secret])
                    sources.append((phase, replace(source, context=tuple(context.items()))))
                retained = replace(evidence, sources=tuple(sources))
                original = serialize_evidence(retained)
                report = unescape(render_run(retained).decode())
                self.assertIn("prefix:[redacted]:suffix", report)
                self.assertNotIn(json.dumps(secret)[1:-1], report)
                self.assertNotIn(secret, report)
                self.assertEqual(serialize_evidence(retained), original)

    def test_access_and_private_key_values_are_redacted_from_recorded_commands(self):
        evidence = retained_fixture()
        for name in ("access-key", "private-key", "ACCESS_KEY", "PRIVATE_KEY", "accesskey", "privatekey"):
            with self.subTest(name=name):
                sources = []
                for phase, source in evidence.sources:
                    context = {**dict(source.context), "environment": json.dumps({name.upper().replace("-", "_"): "env-value"})}
                    context["command"] = json.dumps(["bench", f"--{name}", "option-value", f"--{name}=assignment-value", "env-value"])
                    context["gate-command"] = json.dumps(["gate", f"--{name}", "gate-value", f"{name}=gate-assignment"])
                    sources.append((phase, replace(source, context=tuple(context.items()))))
                retained = replace(evidence, sources=tuple(sources))
                original = serialize_evidence(retained)
                report = unescape(render_run(retained).decode())
                for label, expected in (
                    ("Measurement command (argv)", ["bench", f"--{name}", "[redacted]", f"--{name}=[redacted]", "[redacted]"]),
                    ("Gate command (argv)", ["gate", f"--{name}", "[redacted]", f"{name}=[redacted]"]),
                ):
                    commands = [line.removeprefix(f"| {label} | ").removesuffix(" |") for line in report.splitlines() if line.startswith(f"| {label} | ")]
                    self.assertEqual([json.loads(command) for command in commands], [expected, expected])
                self.assertEqual(serialize_evidence(retained), original)

    def test_powershell_inline_programs_are_omitted_for_native_parameter_spellings(self):
        evidence = retained_fixture()
        for executable in ("pwsh", "C:\\Windows\\powershell.exe"):
            for switch in ("-command", "-COMMAND", "-Com", "-c", "-encodedcommand", "-EncodedCommand", "-enc", "-ec", "-e", "-cwa"):
                with self.subTest(executable=executable, switch=switch):
                    sources = []
                    for phase, source in evidence.sources:
                        argv = json.dumps([executable, switch, "OPAQUE BODY", "OPAQUE REST"])
                        context = {**dict(source.context), "command": argv, "gate-command": argv}
                        sources.append((phase, replace(source, context=tuple(context.items()))))
                    retained = replace(evidence, sources=tuple(sources))
                    original = serialize_evidence(retained)
                    report = render_run(retained).decode()
                    self.assertNotIn("OPAQUE BODY", report)
                    self.assertNotIn("OPAQUE REST", report)
                    self.assertIn(switch, report)
                    self.assertEqual(serialize_evidence(retained), original)

    def test_shell_inline_programs_are_omitted_for_combined_and_native_switches(self):
        evidence = retained_fixture()
        commands = (
            (["bash", "-lc", "printf OPAQUE BODY", "argument"], ["bash", "-lc", "[redacted]", "[redacted]"]),
            (["/bin/sh", "-xec", "echo OPAQUE BODY"], ["/bin/sh", "-xec", "[redacted]"]),
            (["C:\\Tools\\bash.exe", "-cl", "printf OPAQUE BODY"], ["C:\\Tools\\bash.exe", "-cl", "[redacted]"]),
            (["bash", "-lc", "--", "printf OPAQUE BODY"], ["bash", "-lc", "[redacted]", "[redacted]"]),
            (["/bin/sh", "-c", "-l", "--", "echo OPAQUE BODY"], ["/bin/sh", "-c", "[redacted]", "[redacted]", "[redacted]"]),
            (["bash", "-l", "script.sh", "argument"], ["bash", "-l", "script.sh", "argument"]),
            (["bench", "-lc", "argument"], ["bench", "-lc", "argument"]),
            (["cmd", "/c", "echo OPAQUE BODY", "OPAQUE REST"], ["cmd", "/c", "[redacted]", "[redacted]"]),
            (["C:\\Windows\\CMD.EXE", "/C", "echo OPAQUE BODY"], ["C:\\Windows\\CMD.EXE", "/C", "[redacted]"]),
            (["cmd.exe", "/k", "echo OPAQUE BODY", "OPAQUE REST"], ["cmd.exe", "/k", "[redacted]", "[redacted]"]),
            (["cmd.exe", "/d", "script.cmd", "argument"], ["cmd.exe", "/d", "script.cmd", "argument"]),
        )
        for argv, expected in commands:
            with self.subTest(argv=argv):
                sources = []
                for phase, source in evidence.sources:
                    context = {**dict(source.context), "command": json.dumps(argv), "gate-command": json.dumps(argv)}
                    sources.append((phase, replace(source, context=tuple(context.items()))))
                retained = replace(evidence, sources=tuple(sources))
                original = serialize_evidence(retained)
                report = unescape(render_run(retained).decode())
                for label in ("Measurement command (argv)", "Gate command (argv)"):
                    recorded = [line.removeprefix(f"| {label} | ").removesuffix(" |") for line in report.splitlines() if line.startswith(f"| {label} | ")]
                    self.assertEqual([json.loads(command) for command in recorded], [expected, expected])
                self.assertEqual(serialize_evidence(retained), original)

    def test_current_report_cannot_alias_reserved_history_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = retained_fixture()
            publish_evidence(evidence, root / "new.json", root / "manifest.json")
            for archive, current in (("history", "history/RUNS/current.md"), ("history", "HISTORY/runs/current.md"), ("café", "cafe\u0301/runs/current.md")):
                with self.subTest(archive=archive, current=current):
                    (root / "report.toml").write_bytes(f'schema=2\ncurrent="{current}"\narchive="{archive}"\ntitle="Timing"\n'.encode("utf-8"))
                    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
                    with self.assertRaisesRegex(ValueError, "overlapping target paths"):
                        load_run_report_plan(root, "report.toml", payload="new.json", manifest="manifest.json")
                    self.assertEqual({path: path.read_bytes() for path in root.rglob("*") if path.is_file()}, before)
                    self.assertFalse((root / archive / "runs").exists())

    def test_cli_render_file_links_external_evidence_and_stdout_is_unlinked(self):
        with tempfile.TemporaryDirectory(prefix="render café ") as directory:
            root = Path(directory).resolve()
            evidence = retained_fixture()
            payload, manifest = root / "evidence/run (#96).json", root / "evidence/envelope.json"
            publish_evidence(evidence, payload, manifest)
            consumer = root / "consumer"
            consumer.mkdir()
            base = ["--root", str(consumer), "performance", "render", str(payload), str(manifest)]
            for name in ("reports/deep/report.md", str(root / "external/report.md")):
                with self.subTest(output=name), patch("research_repo_tools.host_metadata.capture_host", side_effect=AssertionError("offline must not probe")):
                    self.assertEqual(main([*base, "--output", name]), 0)
                    output = Path(name) if Path(name).is_absolute() else consumer / name
                    report = output.read_text(encoding="utf-8")
                    for label, expected in (("run payload", payload), ("evidence envelope", manifest)):
                        link = report.split(f"[{label}](", 1)[1].split(")", 1)[0]
                        self.assertEqual((output.parent / unquote(link)).resolve(), expected)
                        self.assertEqual((output.parent / unquote(link)).read_bytes(), expected.read_bytes())
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(main(base), 0)
            self.assertIn("publication paths not supplied", stream.getvalue())
            # Model commonpath's native Windows different-drive rejection at
            # the owning seam; global OS identity and real paths stay intact.
            with patch("research_repo_tools.performance.commonpath", side_effect=ValueError("different drives")):
                self.assertEqual(main([*base, "--output", "unlinked.md"]), 0)
            self.assertIn("publication paths not supplied", (consumer / "unlinked.md").read_text(encoding="utf-8"))

    def test_legacy_history_migrates_index_and_current_without_rewriting_retained_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = retained_fixture()
            payload, manifest = serialize_evidence(evidence)
            # Deliberately retain a noncanonical envelope's original bytes.
            manifest = manifest.replace(b"\n", b"\r\n")
            evidence = parse_evidence(payload, manifest)
            identity = run_identity(evidence)
            entry = {"id": identity, "baseline": "v1.0.0", "current": "v1.1.0"}
            retained = root / f"history/runs/{identity}"
            retained.mkdir(parents=True)
            for name, data in (("run.json", payload), ("evidence.json", manifest), ("report.md", LEGACY_REPORT.format(identity=identity).encode())):
                (retained / name).write_bytes(data)
            (root / "history/index.json").write_bytes(deterministic_json({"schema": "research-repo-tools/run-index/v1", "runs": [entry]}))
            (root / "history/latest.json").write_bytes(deterministic_json({"schema": "research-repo-tools/latest-run/v1", "run": entry}))
            (root / "report.toml").write_bytes(b'schema=2\ncurrent="current.md"\narchive="history"\ntitle="Timing"\n')
            before = {path: path.read_bytes() for path in retained.iterdir()}
            with patch("research_repo_tools.host_metadata.capture_host", side_effect=AssertionError("offline must not probe")):
                self.assertEqual(load_latest_run(root, "history"), evidence)
                publish_publication(load_run_report_plan(root, "report.toml"))
                self.assertFalse(load_run_report_plan(root, "report.toml").changed_paths)
            self.assertEqual({path: path.read_bytes() for path in retained.iterdir()}, before)
            self.assertIn(b"Recorded measurement provenance", (root / "current.md").read_bytes())
            index = json.loads((root / "history/index.json").read_bytes())
            self.assertEqual(index["runs"], [{**entry, "report_renderer": 1}])
            publish_evidence(evidence, root / "new.json", root / "manifest.json")
            publish_publication(load_run_report_plan(root, "report.toml", payload="new.json", manifest="manifest.json"))
            self.assertEqual({path: path.read_bytes() for path in retained.iterdir()}, before)
            second = retained_fixture(baseline_point=12)
            publish_evidence(second, root / "new.json", root / "manifest.json")
            publish_publication(load_run_report_plan(root, "report.toml", payload="new.json", manifest="manifest.json"))
            self.assertEqual(load_latest_run(root, "history"), second)
            self.assertEqual({path: path.read_bytes() for path in retained.iterdir()}, before)
            index = json.loads((root / "history/index.json").read_bytes())
            self.assertEqual({item["report_renderer"] for item in index["runs"]}, {1, 2})

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
            measured_report = (root / "target/report.md").read_text(encoding="utf-8")
            self.assertIn("[run payload](run.json)", measured_report)
            self.assertIn("[evidence envelope](evidence.json)", measured_report)
            self.assertEqual(main([*base, "promote", "report.toml", "--payload", "target/run.json", "--manifest", "target/evidence.json"]), 0)
            first = load_latest_run(root, "docs/runs")
            first_files = {path: path.read_bytes() for path in (root / "docs/runs/runs").rglob("*") if path.is_file()}
            self.assertEqual(main([*base, "promote", "report.toml", "--payload", "target/run.json", "--manifest", "target/evidence.json"]), 0)
            # Change complete measurements while preserving the release pair.
            second = retained_fixture(baseline_point=12)
            publish_evidence(second, root / "target/run.json", root / "target/evidence.json")
            self.assertEqual(main([*base, "promote", "report.toml", "--payload", "target/run.json", "--manifest", "target/evidence.json"]), 0)
            self.assertNotEqual(run_identity(first), run_identity(second))
            self.assertEqual(len(list((root / "docs/runs/runs").iterdir())), 2)
            self.assertEqual(first_files, {path: path.read_bytes() for path in first_files})
            shutil.rmtree(root / "target")
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
