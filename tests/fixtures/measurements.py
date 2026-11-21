"""Prepared benchmark trees and measurements for complete performance runs."""

import shutil
import sys
from pathlib import Path
from unittest.mock import patch

from research_repo_tools.common_measurement import CommonHarnessPlan, measure_prepared_pair
from research_repo_tools.evidence import Evidence
from research_repo_tools.measurement import load_measurement
from research_repo_tools.release_pairs import ReleasePair
from research_repo_tools.toml_source import string_literal

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
    """Write a runnable two-phase benchmark checkout and load its measurement plan."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / "src/point.txt").write_bytes(b"5")
    (root / "Cargo.toml").write_bytes(b'[package]\nname="fixture"\nversion="1.1.0"\n')
    (root / "Cargo.lock").write_bytes(b'[[package]]\nname="reference"\nversion="2.0.0"\n')
    (root / "rust-toolchain.toml").write_bytes(b'[toolchain]\nchannel="1.90.0"\n')
    (root / "bench.py").write_text(BENCHMARK, encoding="utf-8", newline="\n")
    (root / "gate.py").write_text(GATE, encoding="utf-8", newline="\n")
    python = string_literal(sys.executable)
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
    """Measure two prepared trees using the current harness and deterministic revision identities."""
    current, baseline = parent / "current", parent / "baseline"
    plan = fixture(current)
    shutil.copytree(current, baseline)
    (baseline / "src/point.txt").write_bytes(point)
    (baseline / "bench.py").write_bytes(b'raise RuntimeError("old harness must not run")\n')
    (baseline / "Cargo.lock").write_bytes(b'[[package]]\nname="reference"\nversion="1.0.0"\n')
    (baseline / "Cargo.toml").write_bytes(b'[package]\nname="fixture"\nversion="1.0.0"\n')
    with patch("research_repo_tools.measurement.resolve_revision", side_effect=lambda root, ref: ("a" if root.name == "baseline" else "b") * 40):
        return measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.1.0" if same_label else "v1.0.0"))
