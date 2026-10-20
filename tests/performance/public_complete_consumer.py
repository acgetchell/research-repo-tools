"""Complete-run public contracts, also run against isolated wheel and sdist installs."""

import csv
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import xml.etree.ElementTree as ET
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import replace
from decimal import Inexact, Rounded, localcontext
from html import unescape
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote

from research_repo_tools import process
from research_repo_tools.cli import main
from research_repo_tools.common_measurement import CommonHarnessPlan, DiscoveredSeries, DiscoveryPolicy, measure_prepared_pair, validate_run_policy
from research_repo_tools.complete_runs import (
    LEGACY_RUN_SCHEMA,
    RUN_SCHEMA,
    RUN_SCHEMA_V2,
    CompleteCase,
    CompletePolicy,
    CompleteRun,
    CompleteSample,
    RunSeries,
    collect_complete_sample,
    parse_policy,
    parse_run,
    render_run,
    run_identity,
    serialize_policy,
    serialize_run,
    validate_run_evidence,
)
from research_repo_tools.criterion_baselines import load_saved_sample, validate_saved_sample
from research_repo_tools.criterion_inventory import DiscoveryCommand, DiscoveryPlan, parse_inventory
from research_repo_tools.evidence import Evidence, Provenance, deterministic_json, parse_evidence, publish_evidence, serialize_evidence
from research_repo_tools.host_metadata import HostMetadata, capture_host, capture_profile, parse_host, serialize_host
from research_repo_tools.measurement import load_measurement
from research_repo_tools.publication import preview_publication, publish_publication
from research_repo_tools.publication_config import load_publication
from research_repo_tools.release_pairs import ReleasePair
from research_repo_tools.run_presentation import (
    CoordinatePlot,
    RunLayout,
    RunRow,
    RunTableStyle,
    SeriesSelection,
    render_coordinate_svg,
    render_run_ratios,
    render_run_table,
    serialize_run_csv,
)
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


def retained_fixture(*, baseline_point: float = 10, mean_offset: float = 0, confidence_level: float = 0.95) -> Evidence:
    """Synthetic retained inputs for rendering/publication, without acquisition."""
    phases, sources = [], []
    for phase, point, identities, revision in (
        ("baseline", baseline_point, ("library / 2", "reference / 2"), "a" * 40),
        ("current", 5, ("library / 2",), "b" * 40),
    ):
        cases = []
        for identity in identities:
            estimates = {}
            for stat in ("mean", "median"):
                stat_point = point + mean_offset if stat == "mean" else point
                estimates[stat] = {
                    "point_estimate": stat_point,
                    "confidence_interval": {"lower_bound": stat_point * 0.9, "upper_bound": stat_point * 1.1, "confidence_level": confidence_level},
                }
            native = (
                {"full_id": identity},
                estimates,
                {"iters": [1, 2, 3], "times": [point, point * 2, point * 3]},
            )
            cases.append(CompleteCase(identity.replace(" / ", "_"), *((json.dumps(value) + "\r\n").encode("utf-8") for value in native)))
        phases.append((phase, CompleteSample(CompletePolicy(identities, sample_count=3, confidence_level=confidence_level), tuple(cases))))
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


def presentation_layout() -> RunLayout:
    return RunLayout(
        (
            SeriesSelection("Library baseline", "Library old", (("two", "library / 2"),)),
            SeriesSelection("Library current", "Library <new> & fast", (("two", "library / 2"),)),
            SeriesSelection("Reference reused", "Peer", (("two", "reference / 2"),)),
        ),
        (RunRow("two", "Size | two", 2),),
        "median",
        "ns",
        comparisons=(("Library baseline", "Library current"),),
    )


def presentation_fixture(root: Path, *, archive: bool = False) -> str:
    evidence = retained_fixture()
    payload, manifest = serialize_evidence(evidence)
    (root / "run.json").write_bytes(payload)
    (root / "envelope.json").write_bytes(manifest)
    (root / "README.md").write_bytes(b"\xef\xbb\xbf# Fixture\r\n<!-- BEGIN -->\r\nold\r\n<!-- END -->\r\n\xfftail\r\n")
    (root / "Cargo.toml").write_bytes(b'version="1.1.0"\n')
    (root / "releases.md").write_bytes(b"Previous: v1.0.0\r\nCurrent: v1.1.0\r\n")
    if archive:
        (root / "report.toml").write_bytes(b'schema=2\ncurrent="full.md"\narchive="history"\ntitle="Full timings"\n')
        publish_publication(load_run_report_plan(root, "report.toml", payload="run.json", manifest="envelope.json"))
    selection = 'archive="history"' if archive else 'payload="run.json"\nmanifest="envelope.json"'
    config = f'''schema=2
document="README.md"
{selection}
begin="<!-- BEGIN -->"
end="<!-- END -->"
unit="ns"
statistic="median"
svg="figures/ratios.svg"
csv="exports/timings.csv"
rows=[{{key="two", label="Size | two", coordinate=2}}]
series=[
  {{name="Library baseline", label="Library old", rows={{two="library / 2"}}}},
  {{name="Library current", label="Library <new> & fast", rows={{two="library / 2"}}}},
  {{name="Reference reused", label="Peer", rows={{two="reference / 2"}}}},
]
comparisons=[{{numerator="Library baseline", denominator="Library current"}}]
plot={{path="figures/coordinates.svg", title="Timings <&>", x-label="Dimension", y-label="Timing", x-scale="log", y-scale="log"}}
[provenance.baseline]
revision="{"a" * 40}"
release="v1.0.0"
[provenance.current]
revision="{"b" * 40}"
release="v1.1.0"
[[references]]
path="Cargo.toml"
pattern='^version="(?P<value>[^"]+)"$'
source="version"
[[references]]
path="releases.md"
pattern='^Previous: (?P<value>[^\\r\\n]+)'
source="previous-tag"
[[references]]
path="releases.md"
pattern='^Current: (?P<value>[^\\r\\n]+)'
source="tag"
'''
    (root / "publication.toml").write_text(config, encoding="utf-8", newline="\n")
    return config


class TestCompleteConsumer(unittest.TestCase):
    @unittest.skipIf(os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1", "Git mutations disabled by local policy")
    def test_installed_cli_declarative_measure_save_retain_render_publish(self):
        self._declarative_workflow(native=True)

    def test_prepared_sources_then_installed_cli_save_retain_render_publish(self):
        self._declarative_workflow(native=False)

    def _declarative_workflow(self, *, native):
        with tempfile.TemporaryDirectory(prefix="consumer café ") as directory:
            root = Path(directory).resolve() / "consumer"
            fixture(root)
            script = (
                """import os, sys
if '--list' in sys.argv:
    for identity in ['library / 2'] + (['reference / 2'] if os.environ['PHASE'] == 'baseline' else []):
        print(identity + ': benchmark')
    sys.exit()
"""
                + BENCHMARK
                + """
if len(sys.argv) == 2:
    import shutil
    for fresh in Path('target/criterion').glob('*/new'):
        (fresh / 'tukey.json').write_bytes(b'[1, 2, 3, 4]\\r\\n')
        shutil.copytree(fresh, fresh.parent / sys.argv[1])
"""
            )
            (root / "bench.py").write_text(script, encoding="utf-8", newline="\n")
            config = (root / "benchmark.toml").read_text(encoding="utf-8")
            command = json.dumps([sys.executable, "-X", "utf8", "bench.py", "--list"])
            for expected in ('["library / 2", "reference / 2"]', '["library / 2"]'):
                config = config.replace(
                    f"expected={expected}",
                    f'discovery={{required={expected}, suites=[{{name="native", command={command}}}]}}',
                )
            (root / "benchmark.toml").write_text(config, encoding="utf-8", newline="\n")
            (root / ".gitignore").write_bytes(b"target/\nscratch/\n")
            (root / ".gitattributes").write_bytes(b"* -text\n")
            (root / "report.toml").write_bytes(b'schema=2\ncurrent="full.md"\narchive="history"\ntitle="Timings"\nmeasurement="benchmark.toml"\nrelease=true\n')
            (root / "README.md").write_bytes(b"# Fixture\n<!-- BEGIN -->\nold\n<!-- END -->\n")
            (root / "publish.toml").write_bytes(b"""schema=2
archive="history"
measurement="benchmark.toml"
document="README.md"
begin="<!-- BEGIN -->"
end="<!-- END -->"
statistic="median"
unit="ns"
csv="figures/timings.csv"
rows=[{key="two", label="Dimension 2", coordinate=2}]
series=[
  {name="Library current", label="Library", label-version="release", rows={two="library / 2"}},
  {name="Reference reused", label="Peer", label-version="dependency.reference", rows={two="reference / 2"}},
]
plot={path="figures/timings.svg", title="Timings", x-label="Dimension", y-label="ns", y-scale="log"}
[provenance.baseline]
[provenance.current]
[[references]]
path="Cargo.toml"
pattern='^version="(?P<value>[^"]+)"$'
source="version"
[[references]]
path="full.md"
pattern='^- Library baseline: measured in \\*\\*baseline\\*\\* phase \\((?P<value>[^)]+)\\)'
source="previous-tag"
[[references]]
path="full.md"
pattern='^- Library current: measured in \\*\\*current\\*\\* phase \\((?P<value>[^)]+)\\)'
source="tag"
""")

            def git(*args):
                return (
                    subprocess.run(
                        ["git", "--no-pager", "-c", "commit.gpgsign=false", *args],
                        cwd=root,
                        check=True,
                        capture_output=True,
                        timeout=30,
                    )
                    .stdout.decode("utf-8")
                    .strip()
                )

            def cli(*args, status=0):
                result = subprocess.run(
                    [sys.executable, "-X", "utf8", "-m", "research_repo_tools", "--root", str(root), "performance", *args],
                    cwd=root,
                    capture_output=True,
                    timeout=120,
                )
                self.assertEqual(result.returncode, status, result.stdout.decode("utf-8") + result.stderr.decode("utf-8"))
                return result

            if native:
                git("init")
                git("config", "user.name", "Fixture")
                git("config", "user.email", "fixture@example.invalid")
                (root / "src/point.txt").write_bytes(b"10")
                (root / "Cargo.toml").write_bytes(b'[package]\nname="fixture"\nversion="1.0.0"\n')
                git("add", ".")
                git("commit", "-m", "baseline")
                baseline = git("rev-parse", "HEAD")
                git("tag", "v1.0.0")
                (root / "src/point.txt").write_bytes(b"5")
                (root / "Cargo.toml").write_bytes(b'[package]\nname="fixture"\nversion="1.1.0"\n')
                git("add", ".")
                git("commit", "-m", "current")
                current = git("rev-parse", "HEAD")
                git("tag", "v1.1.0")
                cli(
                    "measure",
                    "benchmark.toml",
                    "v1.1.0",
                    "v1.0.0",
                    "--allow-git-mutations",
                    "--payload",
                    "scratch/run.json",
                    "--manifest",
                    "scratch/evidence.json",
                )
            else:
                baseline, current = "a" * 40, "b" * 40
                old, new = root.parent / "baseline", root.parent / "current"
                shutil.copytree(root, old)
                shutil.copytree(root, new)
                (old / "src/point.txt").write_bytes(b"10")
                plan = load_measurement(root, "benchmark.toml")
                assert isinstance(plan, CommonHarnessPlan)
                with patch("research_repo_tools.measurement.resolve_revision", side_effect=lambda path, _: baseline if path == old else current):
                    retained = measure_prepared_pair(old, new, plan, ReleasePair("v1.1.0", "v1.0.0"))
                publish_evidence(retained, root / "scratch/run.json", root / "scratch/evidence.json")
            cli("verify", "scratch/run.json", "scratch/evidence.json", "--measurement", "benchmark.toml")
            cli("promote", "report.toml", "--payload", "scratch/run.json", "--manifest", "scratch/evidence.json")
            shutil.rmtree(root / "scratch")
            cli("promote", "report.toml")
            identities = ("--baseline-release", "v1.0.0", "--baseline-revision", baseline, "--current-release", "v1.1.0", "--current-revision", current)
            cli("publish", "publish.toml", *identities, "--artifacts-only")
            self.assertIn(b"\nold\n", (root / "README.md").read_bytes())
            cli("publish", "publish.toml", *identities)
            self.assertIn(b"history/runs/", (root / "README.md").read_bytes())
            self.assertIn(b"Peer 2.0.0", (root / "figures/timings.svg").read_bytes())
            cli("publish", "publish.toml", *identities, "--check")
            expected_identities = {"baseline": ("v1.0.0", baseline), "current": ("v1.1.0", current)}
            # Every source/harness membership edit invalidates a planned publication.
            for target in ("src/point.txt", "gate.py"):
                path = root / target
                original = path.read_bytes()
                for operation in ("edit", "remove"):
                    pending = load_publication(root, "publish.toml", identities=expected_identities)
                    if operation == "edit":
                        path.write_bytes(original + b"\n")
                    else:
                        path.unlink()
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        publish_publication(pending)
                    path.write_bytes(original)
            pending = load_publication(root, "publish.toml", identities=expected_identities)
            policy_bytes = (root / "benchmark.toml").read_bytes()
            (root / "benchmark.toml").write_bytes(policy_bytes + b"\n")
            with self.assertRaisesRegex(ValueError, "changed"):
                publish_publication(pending)
            (root / "benchmark.toml").write_bytes(policy_bytes)
            # Exercise discovery and saved verification separately from isolated measurement.
            (root / "discovery.toml").write_text(
                f'schema=1\nrequired=["library / 2"]\n[[suites]]\nname="native"\ncommand={command}\nenvironment={{PHASE="current"}}\n',
                encoding="utf-8",
                newline="\n",
            )
            cli("discover", "discovery.toml", "--output", "target/inventory.json")
            subprocess.run([sys.executable, "-X", "utf8", "bench.py", "v1.1.0"], cwd=root, env=os.environ | {"PHASE": "current"}, check=True, timeout=30)
            (root / "saved.toml").write_bytes(b"""schema=2
inventory="target/inventory.json"
required=["library / 2"]
sample-count=3
raw-files=["benchmark.json", "estimates.json", "sample.json", "tukey.json"]
[storage-paths]
"library / 2"="library_2"
""")
            cli("verify-saved", "saved.toml", "--saved-sample", "v1.1.0", "--criterion-dir", "target/criterion")
            before = (root / "README.md").read_bytes()
            (root / "src/added.txt").write_bytes(b"new source")
            cli("publish", "publish.toml", *identities, status=1)
            self.assertEqual((root / "README.md").read_bytes(), before)
            cli("promote", "report.toml", "--payload", "scratch/run.json", "--manifest", "scratch/evidence.json", status=1)

    def test_independent_measurement_policy_rejects_self_consistent_but_ineligible_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = measured(root)
            plan = load_measurement(root / "current", "benchmark.toml")
            assert isinstance(plan, CommonHarnessPlan)
            self.assertEqual(validate_run_policy(evidence, plan), validate_run_evidence(evidence))
            run = validate_run_evidence(evidence)
            alternatives = [
                replace(
                    plan,
                    baseline=replace(plan.baseline, policy=CompletePolicy(("library / 2", "reference / 2"), sample_count=4)),
                    current=replace(plan.current, policy=CompletePolicy(("library / 2",), sample_count=4)),
                ),
                replace(plan, baseline=replace(plan.baseline, command=("other",))),
                replace(plan, baseline=replace(plan.baseline, gate=("other",))),
                replace(plan, baseline=replace(plan.baseline, environment=())),
                replace(plan, measurement=replace(plan.measurement, context=(("suite", "full"),))),
                replace(plan, measurement=replace(plan.measurement, compatible=(*plan.measurement.compatible, "context.os"))),
                replace(plan, series=(run.series[0], replace(run.series[1], phase="baseline"), run.series[2])),
            ]
            for field, value in (("statistics", ("mean",)), ("confidence_level", 0.9), ("unit", "us")):
                alternatives.append(
                    replace(
                        plan,
                        baseline=replace(plan.baseline, policy=replace(plan.baseline.policy, **{field: value})),
                        current=replace(plan.current, policy=replace(plan.current.policy, **{field: value})),
                    )
                )
            for candidate in alternatives:
                with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                    validate_run_policy(evidence, candidate)
            same_source = dict(evidence.sources)
            same_source["current"] = replace(
                same_source["current"],
                source_sha256=same_source["baseline"].source_sha256,
                context=tuple((key, "v1.0.0" if key == "release" else value) for key, value in same_source["current"].context),
            )
            with self.assertRaisesRegex(ValueError, "distinct source"):
                validate_run_evidence(replace(evidence, sources=tuple(same_source.items())))
            same_source["current"] = replace(same_source["current"], source_sha256="7" * 64)
            with self.assertRaisesRegex(ValueError, "cannot be promoted"):
                validate_run_policy(replace(evidence, sources=tuple(same_source.items())), plan, release=True)

    def test_discovered_selectors_retain_diagnostics_and_independent_required_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current, baseline = root / "current", root / "baseline"
            plan = fixture(current)
            shutil.copytree(current, baseline)
            phases = {}
            for phase in ("baseline", "current"):
                selected = getattr(plan, phase)
                assert isinstance(selected.policy, CompletePolicy)
                listing = "\n".join(f"{identity}: benchmark" for identity in selected.policy.expected)
                discovery = DiscoveryPlan(
                    (DiscoveryCommand("native", (sys.executable, "-X", "utf8", "-c", f"print({listing!r})")),),
                    required=selected.policy.expected,
                )
                phases[phase] = replace(selected, policy=DiscoveryPolicy(discovery, sample_count=3))
            plan = replace(plan, **phases, series=(*plan.series, DiscoveredSeries("all library", "current", ("*",), ("reference*",))))
            with patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40):
                evidence = measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
            run = validate_run_policy(evidence, plan)
            self.assertEqual(run.series[-1].rows, (("library / 2", "library / 2"),))
            changed = json.loads(evidence.payload)
            changed["phases"]["baseline"]["cases"] = changed["phases"]["baseline"]["cases"][:1]
            changed["phases"]["baseline"]["policy"]["expected"] = ["library / 2"]
            changed["series"] = [item for item in changed["series"] if item["name"] != "Reference reused"]
            sources = dict(evidence.sources)
            context = dict(sources["baseline"].context)
            inventory = json.loads(context["discovery-inventory"])
            inventory["suites"]["native"] = ["library / 2"]
            context["discovery-inventory"] = json.dumps(inventory)
            sources["baseline"] = replace(sources["baseline"], context=tuple(context.items()))
            shortened = replace(evidence, payload=deterministic_json(changed), sources=tuple(sources.items()))
            validate_run_evidence(shortened)  # The payload's own policy is self-consistent.
            with self.assertRaisesRegex(ValueError, "required IDs"):
                validate_run_policy(shortened, plan)
            with self.assertRaises(ValueError):
                DiscoveredSeries("empty", "current", ("absent*",)).resolve(("library / 2",))

    def test_reusable_publication_identities_recorded_labels_and_render_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = presentation_fixture(root, archive=True)
            config = config.replace(f'revision="{"a" * 40}"\nrelease="v1.0.0"', "")
            config = config.replace(f'revision="{"b" * 40}"\nrelease="v1.1.0"', "")
            config = config.replace('label="Peer"', 'label="Peer", label-version="dependency.reference"')
            config = config.replace('label="Library old"', 'label="Library", label-version="release"')
            (root / "publication.toml").write_text(config, encoding="utf-8", newline="\n")
            identities = {"baseline": ("v1.0.0", "a" * 40), "current": ("v1.1.0", "b" * 40)}
            # Today's dependency file is deliberately unrelated to retained peer labels.
            (root / "Cargo.lock").write_bytes(b'[[package]]\nname="reference"\nversion="99.0.0"\n')
            with self.assertRaisesRegex(ValueError, "independent"):
                load_publication(root, "publication.toml")
            original = (root / "README.md").read_bytes()
            for name in ("run.json", "envelope.json"):
                (root / name).unlink()
            plan = load_publication(root, "publication.toml", identities=identities, artifacts_only=True)
            publish_publication(plan)
            self.assertEqual((root / "README.md").read_bytes(), original)
            self.assertIn(b"Peer 2.0.0", (root / "figures/coordinates.svg").read_bytes())
            self.assertIn(b"Library v1.0.0", (root / "figures/coordinates.svg").read_bytes())
            (root / "publication.toml").write_text(config.replace('csv="exports/timings.csv"', 'csv="readme.md"'), encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValueError, "alias|duplicate"):
                load_publication(root, "publication.toml", identities=identities, artifacts_only=True)
            self.assertEqual((root / "README.md").read_bytes(), original)
            (root / "publication.toml").write_text(config, encoding="utf-8", newline="\n")
            for candidate in (
                identities | {"baseline": ("v9.0.0", "a" * 40)},
                identities | {"current": ("v1.1.0", "c" * 40)},
            ):
                with self.assertRaisesRegex(ValueError, "provenance differs"):
                    load_publication(root, "publication.toml", identities=candidate)
            (root / "README.md").unlink()
            publish_publication(load_publication(root, "publication.toml", identities=identities, artifacts_only=True))
            self.assertFalse((root / "README.md").exists())
            pending = load_publication(root, "publication.toml", identities=identities, artifacts_only=True)
            (root / "history/latest.json").write_bytes((root / "history/latest.json").read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "changed"):
                publish_publication(pending)

    def test_formatter_failure_and_changed_configuration_preserve_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = presentation_fixture(root)
            (root / "README.md").write_bytes(b"# Fixture\r\n<!-- BEGIN -->\r\nold\r\n<!-- END -->\r\n")
            (root / "format.toml").write_bytes(b"[global]\n")
            (root / "publication.toml").write_text(config.replace("schema=2", 'schema=2\nformatter="format.toml"'), encoding="utf-8", newline="\n")
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            with patch("research_repo_tools.postprocess_changelog.format_markdown", side_effect=RuntimeError("formatter failed")):
                with self.assertRaisesRegex(RuntimeError, "formatter failed"):
                    load_publication(root, "publication.toml")
            self.assertEqual({path: path.read_bytes() for path in root.rglob("*") if path.is_file()}, before)
            with patch("research_repo_tools.postprocess_changelog.format_markdown", side_effect=lambda text, *_: text.replace("\r\n", "\n")) as formatter:
                plan = load_publication(root, "publication.toml")
            formatter.assert_called_once()
            self.assertNotIn(b"\r", dict(plan.outputs)["README.md"])
            (root / "format.toml").write_bytes(b"[global]\nline-length=80\n")
            with self.assertRaisesRegex(ValueError, "changed"):
                publish_publication(plan)
            self.assertEqual((root / "README.md").read_bytes(), before[root / "README.md"])

    def test_presentation_template_is_available_from_installed_cli(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["templates", "complete-publication.toml"]), 0)
        raw = tomllib.loads(output.getvalue())
        self.assertEqual(raw["schema"], 2)
        self.assertEqual(len(raw["series"]), 3)
        self.assertEqual(raw["plot"]["y-scale"], "log")

    def test_presentation_preserves_phases_intervals_identity_and_bytes(self):
        evidence, layout = retained_fixture(), presentation_layout()
        table = render_run_table(evidence, layout)
        self.assertIn("Peer: baseline phase, v1.0.0, revision " + "a" * 40, table)
        self.assertIn("5 ns [4.5, 5.5] (0.95 confidence)", table)
        self.assertIn("| 2 | 50 |", table)
        self.assertIn("1 phase cases omitted", table)
        self.assertIn(run_identity(evidence), table)
        csv_bytes = serialize_run_csv(evidence, layout)
        self.assertNotIn(b"\r", csv_bytes)
        records = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))
        peer = records[-1]
        self.assertEqual((peer["phase"], peer["revision"], peer["release"], peer["run_id"]), ("baseline", "a" * 40, "v1.0.0", run_identity(evidence)))
        self.assertEqual((peer["point"], peer["lower"], peer["upper"], peer["confidence_level"]), ("10.0", "9.0", "11.0", "0.95"))
        plot = CoordinatePlot("Timing <&>", "Size", "Timing", "log", "log")
        svg = render_coordinate_svg(evidence, layout, plot)
        self.assertEqual(svg, render_coordinate_svg(evidence, layout, plot))
        self.assertEqual(csv_bytes, serialize_run_csv(evidence, layout))
        self.assertNotIn(b"\r", svg)
        tree = ET.fromstring(svg)
        self.assertEqual(tree.attrib["data-run-id"], run_identity(evidence))
        self.assertEqual(tree.findtext("{http://www.w3.org/2000/svg}title"), "Timing <&>")
        self.assertEqual(len(tree.findall(".//{http://www.w3.org/2000/svg}circle")), 3)
        self.assertIn("Peer: baseline phase", tree.findtext("{http://www.w3.org/2000/svg}desc") or "")
        self.assertEqual(ET.fromstring(render_run_ratios(evidence, layout)).attrib["data-run-id"], run_identity(evidence))

    def test_table_default_output_is_preserved(self):
        evidence, layout = retained_fixture(), presentation_layout()
        expected = f"""Run: `{run_identity(evidence)}`. Statistic: median. Unit: ns.

- Library old: baseline phase, v1.0.0, revision {"a" * 40}.
- Library &lt;new&gt; &amp; fast: current phase, v1.1.0, revision {"b" * 40}.
- Peer: baseline phase, v1.0.0, revision {"a" * 40}.

| Case | Library old | Library &lt;new&gt; &amp; fast | Peer | Library old / Library &lt;new&gt; &amp; fast | Library old → Library &lt;new&gt; &amp; fast time reduction (%) |
| --- | --- | --- | --- | --- | --- |
| Size &#124; two | 10 ns [9, 11] (0.95 confidence) | 5 ns [4.5, 5.5] (0.95 confidence) | 10 ns [9, 11] (0.95 confidence) | 2 | 50 |

Intervals are recorded marginal timing intervals; ratios are point estimates, with no ratio intervals or significance claims.
Time reduction is 100 × (numerator − denominator) / numerator; positive means less denominator time.

Selection coverage:
- Library old: 1 selected; 0 unavailable rows; 1 phase cases omitted. Complete evidence is retained independently.
- Library &lt;new&gt; &amp; fast: 1 selected; 0 unavailable rows; 0 phase cases omitted. Complete evidence is retained independently.
- Peer: 1 selected; 0 unavailable rows; 1 phase cases omitted. Complete evidence is retained independently.
"""
        self.assertEqual(render_run_table(evidence, layout), expected)
        self.assertEqual(render_run_table(evidence, layout, style=RunTableStyle()), expected)

    def test_table_compact_ratio_only_headings_and_unchanged_evidence(self):
        evidence, initial = retained_fixture(), presentation_layout()
        layout = replace(initial, comparisons=(*initial.comparisons, ("Reference reused", "Library current")))
        style = RunTableStyle(
            row_heading="Dimension | <n>",
            intervals="below",
            timing_legend=True,
            comparison_columns=("ratio",),
            ratio_headings=("Old / new", "Peer <&> | speedup"),
            ratio_suffix="×",
        )
        before, csv_before = serialize_evidence(evidence), serialize_run_csv(evidence, layout)
        table = render_run_table(evidence, layout, style=style)
        self.assertIn(
            "| Dimension &#124; &lt;n&gt; | Library old | Library &lt;new&gt; &amp; fast | Peer | Old / new | Peer &lt;&amp;&gt; &#124; speedup |", table
        )
        self.assertIn("| Size &#124; two | 10<br>[9, 11] | 5<br>[4.5, 5.5] | 10<br>[9, 11] | 2× | 2× |", table)
        self.assertEqual(table.count("95%"), 1)
        self.assertEqual(table.count(" ns"), 1)
        self.assertNotIn("time reduction", table.lower())
        self.assertIn("numerator time / denominator time", table)
        self.assertIn("Peer &lt;&amp;&gt; &#124; speedup: ratio; numerator = Peer; denominator = Library &lt;new&gt; &amp; fast.", table)
        self.assertIn("Peer: baseline phase", table)
        self.assertIn("recorded marginal timing intervals; ratios are point estimates", table)
        self.assertIn(run_identity(evidence), table)
        self.assertEqual(table, render_run_table(evidence, layout, style=style))
        self.assertEqual(serialize_evidence(evidence), before)
        self.assertEqual(serialize_run_csv(evidence, layout), csv_before)

    def test_table_statistic_confidence_and_interval_placement(self):
        evidence, initial = retained_fixture(mean_offset=2, confidence_level=0.9), presentation_layout()
        for statistic, timing, ratio, reduction in (("median", "10", "2", "50"), ("mean", "12", "1.71429", "41.6667")):
            for intervals in ("inline", "below"):
                for legend in (False, True):
                    with self.subTest(statistic=statistic, intervals=intervals, legend=legend):
                        layout = replace(initial, statistic=statistic)
                        style = RunTableStyle(intervals=intervals, timing_legend=legend)
                        table = render_run_table(evidence, layout, style=style)
                        separator = "<br>" if intervals == "below" else " "
                        self.assertIn(f"| {timing}{'' if legend else ' ns'}{separator}[", table)
                        self.assertIn(f"Statistic: {statistic}.", table)
                        self.assertIn("Confidence level: 90%." if legend else "(0.9 confidence)", table)
                        self.assertNotIn("95%", table)
                        self.assertNotIn("0.95 confidence", table)
                        self.assertIn(f"| {ratio} | {reduction} |", table)
        for level, label in ((0.9555, "95.55%"), (0.9999999, "99.99999%")):
            with self.subTest(level=level):
                table = render_run_table(retained_fixture(confidence_level=level), initial, style=RunTableStyle(timing_legend=True))
                self.assertIn(f"Confidence level: {label}.", table)

    def test_table_confidence_is_independent_of_decimal_context(self):
        layout, style = presentation_layout(), RunTableStyle(timing_legend=True)
        for level, label in (
            (0.9, "90"),
            (0.95, "95"),
            (0.9555, "95.55"),
            (0.9999999, "99.99999"),
            (0.9999999999999999, "99.99999999999999"),
            (1e-8, "0.000001"),
        ):
            evidence = retained_fixture(confidence_level=level)
            expected = render_run_table(evidence, layout, style=style)
            self.assertIn(f"Confidence level: {label}%.", expected)
            for precision in (2, 6):
                with self.subTest(level=level, precision=precision), localcontext(prec=precision, Emin=-1, Emax=1) as context:
                    context.traps[Inexact] = True
                    context.traps[Rounded] = True
                    context.clear_flags()
                    self.assertEqual(render_run_table(evidence, layout, style=style), expected)
                    self.assertFalse(any(context.flags.values()))

    def test_table_independent_comparison_columns_and_unavailable_rows(self):
        evidence, initial = retained_fixture(), presentation_layout()
        layout = replace(
            initial,
            rows=(*initial.rows, RunRow("eight", "Eight")),
            missing="unavailable",
            comparisons=(("Library current", "Library baseline"),),
        )
        for columns, expected in (
            (("ratio",), "| 0.5&#124;&lt;x&gt; |"),
            (("reduction",), "| -100 |"),
            (("reduction", "ratio"), "| -100 | 0.5&#124;&lt;x&gt; |"),
            ((), "(0.95 confidence) |"),
        ):
            with self.subTest(columns=columns):
                style = RunTableStyle(comparison_columns=columns, ratio_suffix="|<x>", reduction_headings=("Reduction <&>",))
                table = render_run_table(evidence, layout, style=style)
                self.assertIn(expected, table)
                self.assertIn("| Eight | " + " | ".join(["unavailable"] * (3 + len(columns))) + " |", table)
                self.assertIn("1 unavailable rows", table)
                if "reduction" in columns:
                    self.assertIn("Reduction &lt;&amp;&gt;: reduction; numerator = Library &lt;new&gt; &amp; fast; denominator = Library old.", table)
                    self.assertIn("100 × (numerator − denominator) / numerator", table)
                else:
                    self.assertNotIn("Reduction &lt;&amp;&gt;", table)

    def test_table_style_rejects_invalid_fields_and_heading_counts(self):
        invalid = (
            {"row_heading": ""},
            {"row_heading": "line\nbreak"},
            {"intervals": "hidden"},
            {"timing_legend": 1},
            {"comparison_columns": ("speedup",)},
            {"comparison_columns": ("ratio", "ratio")},
            {"ratio_headings": ("",)},
            {"ratio_headings": "Gain"},
            {"reduction_headings": ("bad\nheading",)},
            {"ratio_suffix": "\n"},
            {"ratio_suffix": None},
        )
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                RunTableStyle(**fields)  # ty: ignore[invalid-argument-type]  Exercise untyped runtime callers.
        evidence, layout = retained_fixture(), presentation_layout()
        for style in (RunTableStyle(ratio_headings=("A", "B")), RunTableStyle(reduction_headings=("A", "B"))):
            with self.assertRaisesRegex(ValueError, "headings must match"):
                render_run_table(evidence, layout, style=style)
        with self.assertRaises(TypeError):
            render_run_table(evidence, layout, style={})  # ty: ignore[invalid-argument-type]  Exercise untyped runtime callers.

    def test_table_mixed_confidence_evidence_is_rejected_before_rendering(self):
        original, other = retained_fixture(), retained_fixture(confidence_level=0.9)
        raw, changed = json.loads(original.payload), json.loads(other.payload)
        raw["phases"]["current"] = changed["phases"]["current"]
        evidence = replace(original, payload=deterministic_json(raw))
        for intervals in ("inline", "below"):
            for legend in (False, True):
                with self.subTest(intervals=intervals, legend=legend), self.assertRaisesRegex(ValueError, "matching measurement policies"):
                    render_run_table(evidence, presentation_layout(), style=RunTableStyle(intervals=intervals, timing_legend=legend))

    def test_table_style_publication_configuration(self):
        for archive in (False, True):
            with self.subTest(archive=archive), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                config = presentation_fixture(root, archive=archive).replace('svg="figures/ratios.svg"\n', "")
                config = config.replace(
                    'comparisons=[{numerator="Library baseline", denominator="Library current"}]',
                    'comparisons=[{numerator="Library baseline", denominator="Library current"}, {numerator="Reference reused", denominator="Library current"}]',
                )
                config += """
[table]
row-heading="Dimension"
intervals="below"
timing-legend=true
comparison-columns=["ratio"]
ratio-headings=["Old speedup", "Peer speedup"]
ratio-suffix="×"
"""
                (root / "publication.toml").write_text(config, encoding="utf-8", newline="\n")
                if archive:
                    (root / "run.json").unlink()
                    (root / "envelope.json").unlink()
                before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
                plan = load_publication(root, "publication.toml")
                self.assertIn("README.md: SHA-256", preview_publication(plan))
                self.assertEqual(before, {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()})
                self.assertEqual(len(publish_publication(plan)), 3)
                document = (root / "README.md").read_bytes()
                self.assertIn(b"| Dimension |", document)
                self.assertIn(b"| Size &#124; two | 10<br>[9, 11] | 5<br>[4.5, 5.5] | 10<br>[9, 11] | 2\xc3\x97 | 2\xc3\x97 |", document)
                self.assertEqual(document.count(b"Confidence level: 95%."), 1)
                self.assertEqual(publish_publication(load_publication(root, "publication.toml")), ())

    def test_table_style_configuration_rejection_preserves_inputs(self):
        invalid = (
            'row-heading=""',
            'intervals="none"',
            "timing-legend=1",
            'timing-legend="true"',
            'comparison-columns=["ratio", "ratio"]',
            'comparison-columns=["speedup"]',
            'comparison-columns="ratio"',
            "comparison-columns=[true]",
            'ratio-headings=["A", "B"]',
            'ratio-headings="Gain"',
            'reduction-headings=[""]',
            'reduction-headings=["A", "B"]',
            "ratio-suffix=3",
            'unknown="value"',
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = presentation_fixture(root)
            for field in invalid:
                with self.subTest(field=field):
                    (root / "publication.toml").write_text(config + "\n[table]\n" + field + "\n", encoding="utf-8", newline="\n")
                    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
                    with self.assertRaises(ValueError):
                        load_publication(root, "publication.toml")
                    self.assertEqual(before, {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()})

    def test_presentation_multiple_coordinates_order_and_missing_cells(self):
        original = retained_fixture()
        run = validate_run_evidence(original)
        phases = []
        for phase, sample in run.phases:
            cases = list(sample.cases)
            for case in sample.cases:
                identity = case.full_id.replace(" / 2", " / 8")
                cases.append(replace(case, storage_path=case.storage_path + "_8", benchmark=json.dumps({"full_id": identity}).encode()))
            phases.append((phase, CompleteSample(replace(sample.policy, expected=tuple(case.full_id for case in cases)), tuple(cases))))
        views = tuple(replace(view, rows=(*view.rows, ("size 8", dict(view.rows)["size 2"].replace(" / 2", " / 8")))) for view in run.series)
        evidence = replace(original, payload=serialize_run(replace(run, phases=tuple(phases), series=views)))
        initial = presentation_layout()
        series = tuple(replace(item, rows=(*item.rows, ("eight", item.rows[0][1].replace(" / 2", " / 8")))) for item in initial.series)
        layout = replace(initial, series=series, rows=(RunRow("eight", "Eight", 8), *initial.rows))
        table = render_run_table(evidence, layout)
        self.assertLess(table.index("| Eight |"), table.index("| Size &#124; two |"))
        svg = render_coordinate_svg(evidence, layout, CoordinatePlot("Timing", "Size", "Timing"))
        self.assertEqual(len(ET.fromstring(svg).findall(".//{http://www.w3.org/2000/svg}circle")), 6)
        self.assertIn(b'cx="900.000000"', svg)
        missing = replace(layout, missing="unavailable", series=(*series[:2], replace(series[2], rows=(series[2].rows[0],))))
        self.assertIn("unavailable", render_run_table(evidence, missing))
        self.assertIn("unavailable", serialize_run_csv(evidence, missing).decode())
        self.assertEqual(
            len(ET.fromstring(render_coordinate_svg(evidence, missing, CoordinatePlot("T", "X", "Y"))).findall(".//{http://www.w3.org/2000/svg}circle")), 5
        )
        with self.assertRaises(ValueError):
            replace(missing, missing="error")

    def test_presentation_invalid_coordinates_units_and_mappings_fail(self):
        evidence, layout = retained_fixture(), presentation_layout()
        for coordinate in (True, "2", float("nan"), float("inf"), 10**1000):
            with self.subTest(coordinate=coordinate), self.assertRaises(ValueError):
                RunRow("x", "X", coordinate)  # ty: ignore[invalid-argument-type]  Exercise untyped runtime callers.
        for changed in (
            replace(layout, unit="ms"),
            replace(layout, series=(replace(layout.series[0], name="missing"), *layout.series[1:]), comparisons=()),
            replace(layout, series=(replace(layout.series[0], rows=(("two", "reference / 2"),)), *layout.series[1:])),
        ):
            with self.assertRaises(ValueError):
                render_run_table(evidence, changed)
        with self.assertRaises(ValueError):
            replace(layout, statistic="mode")
        with self.assertRaises(ValueError):
            SeriesSelection("X", "X", (("a", "id"), ("b", "id")))
        with self.assertRaises(ValueError):
            CoordinatePlot("T", "X", "Y", "sqrt")  # ty: ignore[invalid-argument-type]  Exercise untyped runtime callers.
        for coordinate in (None, 0, -1):
            with self.assertRaises(ValueError):
                render_coordinate_svg(evidence, replace(layout, rows=(RunRow("two", "Two", coordinate),)), CoordinatePlot("T", "X", "Y", "log"))
        with self.assertRaisesRegex(ValueError, "XML"):
            render_coordinate_svg(evidence, layout, CoordinatePlot("bad\ufffe", "X", "Y"))

    def test_presentation_publication_preview_and_offline_archive_selection(self):
        for archive in (False, True):
            with self.subTest(archive=archive), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                presentation_fixture(root, archive=archive)
                if archive:
                    (root / "run.json").unlink()
                    (root / "envelope.json").unlink()
                original = (root / "README.md").read_bytes()
                plan = load_publication(root, "publication.toml")
                self.assertIn("coordinates.svg", preview_publication(plan))
                self.assertFalse((root / "figures").exists())
                self.assertEqual(len(publish_publication(plan)), 4)
                document = (root / "README.md").read_bytes()
                self.assertTrue(document.startswith(b"\xef\xbb\xbf# Fixture\r\n<!-- BEGIN -->"))
                self.assertTrue(document.endswith(b"<!-- END -->\r\n\xfftail\r\n"))
                self.assertIn(b"Complete run evidence", document)
                if archive:
                    self.assertIn(b"history/runs/", document)
                    load_latest_run(root, "history")
                self.assertNotEqual(document, original)
                self.assertEqual(publish_publication(load_publication(root, "publication.toml")), ())

    def test_presentation_rejected_inputs_and_failed_transaction_preserve_all_files(self):
        invalid = [
            ('statistic="median"', 'statistic="mode"'),
            ('unit="ns"', 'unit="ms"'),
            ("coordinate=2", "coordinate=0"),
            ("coordinate=2", "coordinate=true"),
            ('name="Reference reused"', 'name="Unknown"'),
            ('rows={two="reference / 2"}', 'rows={two="library / 2"}'),
            ('path="figures/coordinates.svg"', 'path="figures/ratios.svg"'),
            ('csv="exports/timings.csv"', 'csv="run.json"'),
            ('payload="run.json"', 'payload="missing.json"'),
            ('manifest="envelope.json"', ""),
            ('release="v1.0.0"', 'release="v0.9.0"'),
        ]
        for old, new in invalid:
            with self.subTest(new=new), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                config = presentation_fixture(root)
                (root / "publication.toml").write_text(config.replace(old, new), encoding="utf-8", newline="\n")
                before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
                with self.assertRaises((ValueError, FileNotFoundError)):
                    load_publication(root, "publication.toml")
                self.assertEqual(before, {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()})
                self.assertFalse((root / "figures").exists())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            presentation_fixture(root)
            plan = load_publication(root, "publication.toml")
            from research_repo_tools import files

            real_replace = files.os.replace
            writes = 0

            def fail_second(source, destination):
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("injected publication failure")
                return real_replace(source, destination)

            before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            with patch("research_repo_tools.files.os.replace", side_effect=fail_second), self.assertRaises(OSError):
                publish_publication(plan)
            self.assertEqual(before, {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()})

    def test_presentation_retained_selection_detects_changed_pointer_and_reserves_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = presentation_fixture(root, archive=True)
            plan = load_publication(root, "publication.toml")
            pointer = root / "history/latest.json"
            pointer.write_bytes(pointer.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "changed after planning"):
                publish_publication(plan)
            self.assertFalse((root / "figures").exists())
            for old, new in [
                ('csv="exports/timings.csv"', 'csv="history/extra.csv"'),
                ('archive="history"', 'archive="history"\npayload="missing.json"\nmanifest="missing-envelope.json"'),
            ]:
                (root / "publication.toml").write_text(config.replace(old, new), encoding="utf-8", newline="\n")
                with self.assertRaises(ValueError):
                    load_publication(root, "publication.toml")

    def test_presentation_retained_selection_rejects_new_partial_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            presentation_fixture(root, archive=True)
            plan = load_publication(root, "publication.toml")
            extra = root / "history/runs" / ("f" * 64)
            extra.mkdir()
            (extra / "run.json").write_bytes(b"partial")
            with self.assertRaisesRegex(ValueError, "partial retained"):
                publish_publication(plan)
            self.assertFalse((root / "figures").exists())

    def test_presentation_freshness_and_future_tag_checks_include_all_artifacts(self):
        for name in ("source.rs", "harness.rs"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                config = presentation_fixture(root)
                (root / "source.rs").write_bytes(b"original source\r\n")
                (root / "harness.rs").write_bytes(b"original source\r\n")
                from research_repo_tools.evidence import fingerprint_files

                digest = fingerprint_files(root, (Path("source.rs"),))
                harness = fingerprint_files(root, (Path("harness.rs"),))
                evidence = retained_fixture()
                sources = dict(evidence.sources)
                for phase in ("baseline", "current"):
                    context = {
                        **dict(sources[phase].context),
                        "mode": "working-tree",
                        "source-inventory": '["source.rs"]',
                        "harness-inventory": '["harness.rs"]',
                    }
                    sources[phase] = replace(sources[phase], source_sha256=digest, harness_sha256=harness, context=tuple(context.items()))
                publish_evidence(replace(evidence, sources=tuple(sources.items())), root / "run.json", root / "envelope.json")
                config = 'repository="example/project"\ntag-policy="prepare"\ncurrent-sources=["source.rs"]\ncurrent-harness=["harness.rs"]\n' + config
                (root / "publication.toml").write_text(config, encoding="utf-8", newline="\n")
                with patch("research_repo_tools.publication.run_git_bytes", return_value=subprocess.CompletedProcess([], 1, b"", b"")):
                    plan = load_publication(root, "publication.toml")
                    self.assertEqual(plan.git_checks[-1].tag, "v1.1.0")
                    for artifact in ("figures/coordinates.svg", "figures/ratios.svg", "exports/timings.csv", "run.json", "envelope.json"):
                        self.assertIn(artifact, plan.git_checks[-1].paths)
                    (root / name).write_bytes(b"changed source\n")
                    with self.assertRaisesRegex(ValueError, "changed after planning"):
                        publish_publication(plan)
                    self.assertFalse((root / "figures").exists())
                    with self.assertRaisesRegex(ValueError, "differ from"):
                        load_publication(root, "publication.toml")
                    (root / name).write_bytes(b"original source\r\n")
                    plan = load_publication(root, "publication.toml")
                with (
                    patch("research_repo_tools.publication.run_git_bytes", return_value=subprocess.CompletedProcess([], 0, b"", b"")),
                    patch("research_repo_tools.publication.verify_tagged_files", side_effect=ValueError("tag bytes differ")),
                    self.assertRaisesRegex(ValueError, "tag bytes differ"),
                ):
                    publish_publication(plan)
                self.assertFalse((root / "figures").exists())

    def test_presentation_current_only_raw_case_is_retained_and_links_are_portable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = presentation_fixture(root)
            evidence = retained_fixture()
            run = validate_run_evidence(evidence)
            phases = dict(run.phases)
            current = phases["current"]
            case = replace(current.cases[0], storage_path="current-only", benchmark=b'{"full_id":"current-only"}\r\n')
            phases["current"] = CompleteSample(replace(current.policy, expected=(*current.policy.expected, "current-only")), (*current.cases, case))
            evidence = replace(evidence, payload=serialize_run(replace(run, phases=tuple(phases.items()))))
            publish_evidence(evidence, root / "run.json", root / "envelope.json")
            (root / "docs").mkdir()
            (root / "README.md").rename(root / "docs/read mé.md")
            config = config.replace('document="README.md"', 'document="docs/read mé.md"').replace('csv="exports/timings.csv"', 'csv="exports/timings mé.csv"')
            (root / "publication.toml").write_text(config, encoding="utf-8", newline="\n")
            original_payload, original_envelope = (root / "run.json").read_bytes(), (root / "envelope.json").read_bytes()
            publish_publication(load_publication(root, "publication.toml"))
            document = (root / "docs/read mé.md").read_bytes()
            self.assertIn(b"../exports/timings%20m%C3%A9.csv", document)
            self.assertIn(b"../run.json", document)
            self.assertIn(b"1 phase cases omitted", document)
            self.assertEqual((root / "run.json").read_bytes(), original_payload)
            self.assertEqual((root / "envelope.json").read_bytes(), original_envelope)
            self.assertIn("current-only", validate_run_evidence(parse_evidence(original_payload, original_envelope)).phases[1][1].policy.expected)

    def test_presentation_axes_remain_finite_and_missing_rows_break_lines(self):
        from research_repo_tools.run_presentation import _axis

        for values in ([-1e308, 1e308], [1e308, math.nextafter(1e308, math.inf)], [5e-324, 1e-323]):
            axis = _axis(values, "linear")
            self.assertEqual((axis(values[0]), axis(values[-1])), (0.0, 1.0))
        layout = replace(
            presentation_layout(), rows=(RunRow("low", "Low", -1e308), RunRow("two", "Two", 0), RunRow("high", "High", 1e308)), missing="unavailable"
        )
        svg = render_coordinate_svg(retained_fixture(), layout, CoordinatePlot("Finite", "X", "Y"))
        self.assertNotIn(b"nan", svg)
        self.assertNotIn(b"inf", svg)
        self.assertEqual(len(ET.fromstring(svg).findall(".//{http://www.w3.org/2000/svg}circle")), 3)
        with self.assertRaisesRegex(ValueError, "unique coordinates"):
            render_coordinate_svg(
                retained_fixture(),
                replace(layout, rows=(RunRow("low", "Low", 0), RunRow("two", "Two", 0), RunRow("high", "High", 1))),
                CoordinatePlot("T", "X", "Y"),
            )

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
            (["env", "bash", "-lc", "printf sk_123"], ["env", "bash", "-lc", "[redacted]"]),
            (["/usr/bin/env", "/bin/sh", "-xec", "echo OPAQUE BODY"], ["/usr/bin/env", "/bin/sh", "-xec", "[redacted]"]),
            (["env", "cmd.exe", "/C", "echo OPAQUE BODY", "OPAQUE REST"], ["env", "cmd.exe", "/C", "[redacted]", "[redacted]"]),
            (["C:\\Tools\\env.exe", "C:\\Windows\\pwsh.exe", "-Com", "OPAQUE BODY"], ["C:\\Tools\\env.exe", "C:\\Windows\\pwsh.exe", "-Com", "[redacted]"]),
            (["env", "bench", "-lc", "argument"], ["env", "bench", "-lc", "argument"]),
            (["env"], ["env"]),
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


COUNTS = (("library / 2", 10), ("geometry café / 15", 15), ("geometry / 20", 20), ("geometry / 25", 25), ("reference / 2", 100))


def mixed_case(identity: str, count: int) -> CompleteCase:
    """Synthetic original native bytes with deliberately unrelated storage names."""
    estimates = (
        b'{"mean":{"point_estimate":2,"confidence_interval":{"lower_bound":1,"upper_bound":3,"confidence_level":0.95}},'
        b'"median":{"point_estimate":2,"confidence_interval":{"lower_bound":1,"upper_bound":3,"confidence_level":0.95}}}\r\n'
    )
    return CompleteCase(
        f"stored_{count}",
        (json.dumps({"full_id": identity}, ensure_ascii=False) + "\r\n").encode("utf-8"),
        estimates,
        (json.dumps({"iters": list(range(1, count + 1)), "times": [2 * i for i in range(1, count + 1)], "sampling_mode": "Linear"}) + "\r\n").encode("utf-8"),
    )


def heterogeneous_plan(root: Path, *, discovery: bool = False) -> CommonHarnessPlan:
    fixture(root)
    configuration = (root / "benchmark.toml").read_text(encoding="utf-8").replace("schema=2", "schema=3").replace("sample-count=3\n", "")
    assignments = "sample-counts=" + json.dumps(dict(COUNTS), ensure_ascii=False).replace(": ", " = ")
    if discovery:
        listing = "; ".join(f"print({identity + ': benchmark'!r})" for identity, _ in COUNTS)
        inventory = 'discovery={suites=[{name="suite", command=' + json.dumps([sys.executable, "-X", "utf8", "-c", listing]) + "}]}"
    else:
        inventory = "expected=" + json.dumps([identity for identity, _ in COUNTS], ensure_ascii=False)
    for old in ('expected=["library / 2", "reference / 2"]', 'expected=["library / 2"]'):
        configuration = configuration.replace(old, inventory + "\n" + assignments)
    (root / "benchmark.toml").write_text(configuration, encoding="utf-8", newline="\n")
    benchmark = BENCHMARK.replace(
        "for identity in ['library / 2'] + (['reference / 2'] if os.environ['PHASE'] == 'baseline' else []):",
        f"for identity, count in {dict(COUNTS)!r}.items():",
    ).replace(
        "{'iters': [1, 2, 3], 'times': [point, point * 2, point * 3]}",
        "{'iters': list(range(1, count + 1)), 'times': [point * i for i in range(1, count + 1)]}",
    )
    (root / "bench.py").write_text(benchmark, encoding="utf-8", newline="\n")
    plan = load_measurement(root, "benchmark.toml")
    assert isinstance(plan, CommonHarnessPlan)
    return plan


def sampling_evidence(run: CompleteRun, sources: tuple[tuple[str, Provenance], ...]) -> Evidence:
    """Explicitly record new policies when constructing synthetic v2 evidence."""
    sources = tuple(
        (
            phase,
            replace(
                source, context=tuple({**dict(source.context), "sampling-policy": serialize_policy(dict(run.phases)[phase].policy).decode("utf-8")}.items())
            ),
        )
        for phase, source in sources
    )
    return Evidence(serialize_run(run), run.schema, sources)


class TestHeterogeneousConsumer(unittest.TestCase):
    def test_default_run_constructor_preserves_uniform_v1_workflow(self):
        original = retained_fixture()
        parsed = parse_run(original.payload)
        run = CompleteRun(parsed.phases, parsed.series, parsed.compatible)
        evidence = Evidence(serialize_run(run), RUN_SCHEMA, original.sources)
        self.assertEqual(RUN_SCHEMA, "research-repo-tools/complete-run/v1")
        self.assertEqual(run.schema, RUN_SCHEMA)
        self.assertEqual(evidence, original)
        self.assertEqual(validate_run_evidence(evidence), run)
        self.assertTrue(all("sampling-policy" not in dict(source.context) for _, source in evidence.sources))

    def test_default_common_plan_preserves_uniform_measurement_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            current, baseline = root / "current", root / "baseline"
            parsed = fixture(current)
            plan = CommonHarnessPlan(parsed.measurement, parsed.baseline, parsed.current, parsed.series)
            self.assertEqual(plan.schema, 2)
            shutil.copytree(current, baseline)
            with patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40):
                evidence = measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
            self.assertEqual(evidence.payload_schema, RUN_SCHEMA)
            self.assertEqual(validate_run_evidence(evidence).schema, RUN_SCHEMA)
            self.assertTrue(all("sampling-policy" not in dict(source.context) for _, source in evidence.sources))

    def test_packaged_measurement_template_declares_exact_counts(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["templates", "common-benchmark.toml"]), 0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "benchmark.toml").write_text(output.getvalue(), encoding="utf-8", newline="\n")
            plan = load_measurement(root, "benchmark.toml")
        self.assertIsInstance(plan, CommonHarnessPlan)
        assert isinstance(plan, CommonHarnessPlan)
        self.assertEqual(plan.schema, 3)
        self.assertEqual(dict(plan.baseline.policy.sample_counts), {"library/workload": 10, "reference/workload": 100})

    def test_exact_counts_and_original_bytes_round_trip_through_public_api(self):
        policy = CompletePolicy(tuple(identity for identity, _ in COUNTS), sample_count=None, sample_counts=COUNTS)
        cases = tuple(mixed_case(identity, count) for identity, count in COUNTS)
        sample = CompleteSample(policy, cases)
        self.assertEqual(tuple(policy.count_for(identity) for identity, _ in COUNTS), (10, 15, 20, 25, 100))
        self.assertEqual(parse_policy(serialize_policy(policy)), policy)
        run = CompleteRun((("baseline", sample), ("current", sample)), (RunSeries("Selected", "current", (("small", "library / 2"),)),), schema=RUN_SCHEMA_V2)
        evidence = sampling_evidence(run, retained_fixture().sources)
        self.assertEqual(validate_run_evidence(evidence), run)
        self.assertEqual(parse_run(serialize_run(run)), run)
        self.assertEqual(evidence.payload_schema, "research-repo-tools/complete-run/v2")
        self.assertEqual({case.full_id: case.sample_count for case in dict(parse_run(evidence.payload).phases)["current"].cases}, dict(COUNTS))
        for actual in dict(parse_run(evidence.payload).phases)["current"].cases:
            self.assertEqual(actual, next(case for case in cases if case.full_id == actual.full_id))
        self.assertIn(run_identity(evidence), render_run(evidence).decode())
        self.assertEqual(len(dict(run.phases)["current"].cases), 5)
        self.assertEqual(policy.count_for("geometry café / 15"), 15)
        with self.assertRaisesRegex(ValueError, "unexpected"):
            policy.count_for("stored_15")
        for uniform in (10, 100):
            with self.assertRaisesRegex(ValueError, "raw samples"):
                CompleteSample(CompletePolicy(policy.expected, sample_count=uniform), cases)

    def test_policy_rejects_missing_extra_overlapping_and_invalid_assignments(self):
        expected = tuple(identity for identity, _ in COUNTS)
        for assignments in ((), COUNTS[:-1], (*COUNTS, ("unexpected", 10)), (*COUNTS, COUNTS[0])):
            with self.subTest(assignments=assignments), self.assertRaises(ValueError):
                CompletePolicy(expected, sample_count=None, sample_counts=assignments)
        for count in (True, 1, 0, -1, 10.0, "10", None):
            with self.subTest(count=count), self.assertRaises(ValueError):
                CompletePolicy(("case",), sample_count=None, sample_counts=(("case", count),))  # ty: ignore[invalid-argument-type]  Exercise untyped input.
        with self.assertRaisesRegex(ValueError, "exactly one"):
            CompletePolicy(expected, sample_counts=COUNTS)
        mutable = list(COUNTS)
        policy = CompletePolicy(expected, None, sample_counts=mutable)  # ty: ignore[invalid-argument-type]  Verify defensive copying.
        mutable.clear()
        self.assertEqual(policy.sample_counts, tuple(sorted(COUNTS)))

    def test_mixed_samples_still_require_inventory_counts_and_complete_intervals(self):
        policy = CompletePolicy(tuple(identity for identity, _ in COUNTS), None, sample_counts=COUNTS)
        cases = tuple(mixed_case(identity, count) for identity, count in COUNTS)
        first = cases[0]
        for changed in (
            cases[:-1],
            (*cases, replace(first, storage_path="duplicate")),
            (replace(first, benchmark=b'{"full_id":"unexpected"}'), *cases[1:]),
            (mixed_case(first.full_id, 9), *cases[1:]),
            (replace(first, estimates=first.estimates.replace(b'"median"', b'"other"')), *cases[1:]),
            (replace(first, estimates=first.estimates.replace(b"0.95", b"0.9")), *cases[1:]),
            (replace(first, estimates=first.estimates.replace(b'"upper_bound":3', b'"upper_bound":0.5')), *cases[1:]),
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                CompleteSample(policy, changed)

    def test_phase_counts_are_independent_and_bound_into_provenance_and_identity(self):
        old = retained_fixture()
        run = replace(parse_run(old.payload), schema=RUN_SCHEMA_V2)
        uniform = sampling_evidence(run, old.sources)
        self.assertEqual(validate_run_evidence(uniform), run)
        phases = tuple(
            (
                phase,
                replace(sample, policy=replace(sample.policy, sample_count=None, sample_counts=tuple((identity, 3) for identity in sample.policy.expected))),
            )
            for phase, sample in run.phases
        )
        explicit_run = replace(run, phases=phases)
        explicit = sampling_evidence(explicit_run, old.sources)
        self.assertNotEqual(run_identity(uniform), run_identity(explicit))
        self.assertEqual([sample.cases for _, sample in run.phases], [sample.cases for _, sample in explicit_run.phases])
        with self.assertRaisesRegex(ValueError, "sampling policy differs"):
            validate_run_evidence(replace(uniform, payload=explicit.payload))
        for missing in ("sampling-policy", "gate-status", "gate-command"):
            sources = tuple(
                (name, replace(source, context=tuple((key, value) for key, value in source.context if key != missing))) for name, source in explicit.sources
            )
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                validate_run_evidence(replace(explicit, sources=sources))
        policy = CompletePolicy(("case",), None, sample_counts=(("case", 10),))
        sample = CompleteSample(policy, (mixed_case("case", 10),))
        other = CompleteSample(replace(policy, sample_counts=(("case", 15),)), (mixed_case("case", 15),))
        independent = CompleteRun((("baseline", sample), ("current", other)), (RunSeries("Selected", "current", (("x", "case"),)),), schema=RUN_SCHEMA_V2)
        validate_run_evidence(sampling_evidence(independent, old.sources))
        with self.assertRaisesRegex(ValueError, "uniform"):
            replace(independent, schema=LEGACY_RUN_SCHEMA)

    def test_old_schema_preserves_its_contract_and_does_not_invent_policy_provenance(self):
        evidence = retained_fixture()
        run = parse_run(evidence.payload)
        self.assertEqual(run.schema, LEGACY_RUN_SCHEMA)
        self.assertEqual(serialize_run(run), evidence.payload)
        self.assertTrue(all("sampling-policy" not in dict(source.context) for _, source in evidence.sources))
        self.assertTrue(all("sample_counts" not in phase["policy"] for phase in json.loads(evidence.payload)["phases"].values()))
        for schema in (RUN_SCHEMA_V2, "legacy/schema-3", "research-repo-tools/complete-run/v3"):
            raw = json.loads(evidence.payload)
            raw["schema"] = schema
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                parse_run(deterministic_json(raw))
        with self.assertRaisesRegex(ValueError, "schemas differ"):
            validate_run_evidence(replace(evidence, payload_schema=RUN_SCHEMA_V2))
        explicit_run = replace(run, schema=RUN_SCHEMA_V2)
        with self.assertRaisesRegex(ValueError, "sampling-policy"):
            validate_run_evidence(Evidence(serialize_run(explicit_run), RUN_SCHEMA_V2, evidence.sources))

    def test_serialized_policy_and_run_reject_missing_extra_and_duplicate_fields(self):
        policy = CompletePolicy(tuple(identity for identity, _ in COUNTS), None, sample_counts=COUNTS)
        valid = json.loads(serialize_policy(policy))
        for changed in (
            {key: value for key, value in valid.items() if key != "sample_counts"},
            {**valid, "sample_counts": {}},
            {**valid, "sample_counts": {**dict(COUNTS), "extra": 10}},
            {**valid, "sample_counts": {"library / 2": 10}},
            {**valid, "sample_count": 10},
            {**valid, "sample_counts": []},
            {**valid, "sample_counts": {identity: True for identity, _ in COUNTS}},
            {**valid, "unit": "unknown"},
            {**valid, "confidence_level": 1},
            {**valid, "statistics": ["mean", "mean"]},
            {**valid, "unexpected": 10},
        ):
            with self.subTest(policy=changed), self.assertRaises(ValueError):
                parse_policy(deterministic_json(changed))
        payload = serialize_policy(policy).replace(b'"library / 2":10', b'"library / 2":10,"library / 2":10')
        self.assertNotEqual(payload, serialize_policy(policy))
        with self.assertRaisesRegex(ValueError, "duplicate JSON field"):
            parse_policy(payload)
        run = replace(parse_run(retained_fixture().payload), schema=RUN_SCHEMA_V2)
        raw = json.loads(serialize_run(run))
        del raw["phases"]["current"]["policy"]["sample_counts"]
        with self.assertRaisesRegex(ValueError, "fields"):
            parse_run(deterministic_json(raw))
        raw = json.loads(retained_fixture().payload)
        raw["phases"]["current"]["policy"]["sample_counts"] = {}
        with self.assertRaisesRegex(ValueError, "fields"):
            parse_run(deterministic_json(raw))

    def test_schema3_defaults_preserve_uniform_policy_and_legacy_plan_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = fixture(root)
            self.assertEqual(old.schema, 2)
            config = (root / "benchmark.toml").read_text(encoding="utf-8").replace("schema=2", "schema=3").replace("sample-count=3\n", "")
            (root / "benchmark.toml").write_text(config, encoding="utf-8", newline="\n")
            new = load_measurement(root, "benchmark.toml")
            assert isinstance(new, CommonHarnessPlan)
            self.assertEqual(new.schema, 3)
            self.assertEqual(new.baseline.policy.sample_count, 100)
            self.assertEqual(new.current.policy.sample_counts, ())
            self.assertEqual(parse_policy(serialize_policy(CompletePolicy(("case",)))), CompletePolicy(("case",)))
            with self.assertRaisesRegex(ValueError, "uniform"):
                replace(new, schema=2, current=replace(new.current, policy=CompletePolicy(("library / 2",), sample_count=10)))

    def test_v2_policy_evidence_flows_through_document_and_figure_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            presentation_fixture(root)
            old = retained_fixture()
            run = replace(parse_run(old.payload), schema=RUN_SCHEMA_V2)
            run = replace(
                run,
                phases=tuple(
                    (
                        phase,
                        replace(
                            sample, policy=replace(sample.policy, sample_count=None, sample_counts=tuple((identity, 3) for identity in sample.policy.expected))
                        ),
                    )
                    for phase, sample in run.phases
                ),
            )
            evidence = sampling_evidence(run, old.sources)
            publish_evidence(evidence, root / "run.json", root / "envelope.json")
            payload, manifest = serialize_evidence(evidence)
            plan = load_publication(root, "publication.toml")
            publish_publication(plan)
            self.assertIn(run_identity(evidence).encode(), (root / "README.md").read_bytes())
            self.assertEqual((root / "run.json").read_bytes(), payload)
            self.assertEqual((root / "envelope.json").read_bytes(), manifest)
            self.assertFalse(load_publication(root, "publication.toml").changed_paths)
            self.assertEqual(ET.fromstring((root / "figures/coordinates.svg").read_bytes()).attrib["data-run-id"], run_identity(evidence))

    def test_native_mixed_measurement_discovery_and_cli_retention_on_installed_package(self):
        for discovery in (False, True):
            with self.subTest(discovery=discovery), tempfile.TemporaryDirectory(prefix="mixed café ") as directory:
                root = Path(directory).resolve()
                current, baseline = root / "current", root / "baseline"
                plan = heterogeneous_plan(current, discovery=discovery)
                shutil.copytree(current, baseline)
                (baseline / "src/point.txt").write_bytes(b"10")
                from research_repo_tools.common_measurement import run_command_live

                with (
                    patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40),
                    patch("research_repo_tools.common_measurement.run_command_live", wraps=run_command_live) as live,
                ):
                    evidence = measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
                self.assertEqual([call.args[1][0] for call in live.call_args_list], ["gate.py", "gate.py", "bench.py", "bench.py"])
                self.assertEqual(evidence.payload_schema, RUN_SCHEMA_V2)
                for phase, sample in validate_run_evidence(evidence).phases:
                    self.assertEqual(dict(sample.policy.sample_counts), dict(COUNTS))
                    self.assertEqual(json.loads(dict(dict(evidence.sources)[phase].context)["sampling-policy"])["sample_counts"], dict(COUNTS))
                    for case in sample.cases:
                        for key in ("benchmark", "estimates", "sample"):
                            self.assertEqual(getattr(case, key), (root / phase / "target/criterion" / case.storage_path / "new" / f"{key}.json").read_bytes())
                publish_evidence(evidence, root / "run.json", root / "envelope.json")
                (root / "report.toml").write_bytes(b'schema=2\ncurrent="docs/current.md"\narchive="history"\ntitle="Mixed timings"\n')
                base = ["--root", str(root), "performance"]
                self.assertEqual(main([*base, "render", "run.json", "envelope.json", "--output", "mixed.md"]), 0)
                self.assertEqual(main([*base, "promote", "report.toml", "--payload", "run.json", "--manifest", "envelope.json"]), 0)
                before = {path: path.read_bytes() for path in (root / "history/runs").rglob("*") if path.is_file()}
                shutil.rmtree(current / "target")
                shutil.rmtree(baseline / "target")
                with patch("research_repo_tools.host_metadata.capture_host", side_effect=AssertionError("offline must not probe")):
                    self.assertEqual(main([*base, "promote", "report.toml", "--check"]), 0)
                    self.assertEqual(load_latest_run(root, "history"), evidence)
                self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_schema3_cli_measure_publishes_complete_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            plan = heterogeneous_plan(root)
            series = []
            for item in plan.series:
                assert isinstance(item, RunSeries)
                series.append(item)
            retained = sampling_evidence(
                CompleteRun(
                    tuple(
                        (phase, CompleteSample(getattr(plan, phase).policy, tuple(mixed_case(identity, count) for identity, count in COUNTS)))
                        for phase in ("baseline", "current")
                    ),
                    tuple(series),
                    schema=RUN_SCHEMA_V2,
                ),
                retained_fixture().sources,
            )
            with patch("research_repo_tools.performance_workflows.measure_pair", return_value=retained) as measure:
                self.assertEqual(
                    main(
                        [
                            "--root",
                            str(root),
                            "performance",
                            "measure",
                            "benchmark.toml",
                            "v1.1.0",
                            "v1.0.0",
                            "--allow-git-mutations",
                            "--payload",
                            "run.json",
                            "--manifest",
                            "envelope.json",
                        ]
                    ),
                    0,
                )
            self.assertEqual(measure.call_args.args[1], plan)
            self.assertEqual(parse_evidence((root / "run.json").read_bytes(), (root / "envelope.json").read_bytes()), retained)

    def test_schema3_configuration_rejects_incomplete_or_ambiguous_maps_before_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            heterogeneous_plan(root)
            config = (root / "benchmark.toml").read_text(encoding="utf-8")
            for changed in (
                config.replace("schema=3", "schema=2"),
                config.replace("schema=3", "schema=3\nsample-count=100"),
                config.replace('"library / 2" = 10, ', ""),
                config.replace('"library / 2" = 10', '"unexpected" = 10'),
                config.replace('"library / 2" = 10', '"library / 2" = true'),
                config.replace('"library / 2" = 10', '"library / 2" = 10.0'),
                config.replace('"library / 2" = 10', '"library / 2" = 1'),
                config.replace('"library / 2" = 10', '"library / 2" = 10, "library / 2" = 10'),
            ):
                (root / "benchmark.toml").write_text(changed, encoding="utf-8", newline="\n")
                with self.subTest(config=changed), patch("research_repo_tools.common_measurement.run_command_live") as live, self.assertRaises(ValueError):
                    load_measurement(root, "benchmark.toml")
                live.assert_not_called()

    def test_discovered_count_coverage_and_required_policy_compatibility_precede_gates(self):
        for failure in ("missing", "extra", "compatibility"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                current, baseline = root / "current", root / "baseline"
                plan = heterogeneous_plan(current, discovery=True)
                policy = plan.baseline.policy
                assert isinstance(policy, DiscoveryPolicy)
                counts = COUNTS[:-1] if failure == "missing" else (*COUNTS, ("extra", 10)) if failure == "extra" else COUNTS
                policy = replace(policy, sample_counts=counts)
                plan = replace(plan, baseline=replace(plan.baseline, policy=policy))
                if failure == "compatibility":
                    plan = replace(
                        plan,
                        current=replace(plan.current, policy=replace(policy, sample_counts=tuple((identity, count + 1) for identity, count in COUNTS))),
                        measurement=replace(plan.measurement, compatible=(*plan.measurement.compatible, "context.sampling-policy")),
                    )
                shutil.copytree(current, baseline)
                with (
                    patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40),
                    patch("research_repo_tools.common_measurement.run_command_live") as live,
                ):
                    with self.assertRaisesRegex(ValueError, "sample count inventory differs" if failure != "compatibility" else "before timing"):
                        measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
                live.assert_not_called()
                self.assertFalse((current / "target/criterion").exists())
                self.assertFalse((baseline / "target/criterion").exists())

    def test_mixed_policy_still_runs_both_independent_gates_before_timing(self):
        for phase in ("baseline", "current"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                current, baseline = root / "current", root / "baseline"
                plan = heterogeneous_plan(current)
                shutil.copytree(current, baseline)
                (root / phase / "src/point.txt").write_bytes(b"-1")
                with (
                    patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40),
                    self.assertRaisesRegex(ValueError, f"{phase} preflight gate failed"),
                ):
                    measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
                self.assertFalse((current / "target/criterion").exists())
                self.assertFalse((baseline / "target/criterion").exists())

    def test_saved_mixed_samples_keep_exact_bytes_and_cli_policy(self):
        with tempfile.TemporaryDirectory(prefix="saved café ") as directory:
            root = Path(directory).resolve()
            policy = CompletePolicy(tuple(identity for identity, _ in COUNTS), None, sample_counts=COUNTS)
            for identity, count in COUNTS:
                case = mixed_case(identity, count)
                for sample in ("new", "v1.1.0"):
                    target = root / "criterion" / case.storage_path / sample
                    target.mkdir(parents=True)
                    for key in ("benchmark", "estimates", "sample"):
                        (target / f"{key}.json").write_bytes(getattr(case, key))
            config = (
                'schema=2\ncriterion-dir="criterion"\nsaved-sample="v1.1.0"\nexpected='
                + json.dumps(policy.expected, ensure_ascii=False)
                + "\nsample-counts="
                + json.dumps(dict(COUNTS), ensure_ascii=False).replace(": ", " = ")
                + "\n"
            )
            (root / "saved.toml").write_text(config, encoding="utf-8", newline="\n")
            self.assertEqual(load_saved_sample(root, "saved.toml").policy, policy)
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(main(["--root", str(root), "performance", "verify-saved", "saved.toml"]), 0)
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            target = root / "criterion/stored_10/v1.1.0/sample.json"
            target.write_bytes(target.read_bytes().replace(b"\r\n", b"\n"))
            with self.assertRaisesRegex(ValueError, "differs from fresh"):
                validate_saved_sample(root / "criterion", "v1.1.0", policy)
            for changed in (config.replace("schema=2", "schema=1"), config.replace("schema=2", "schema=2\nsample-count=100")):
                (root / "saved.toml").write_text(changed, encoding="utf-8", newline="\n")
                with self.assertRaises(ValueError):
                    load_saved_sample(root, "saved.toml")


if __name__ == "__main__":
    unittest.main()
