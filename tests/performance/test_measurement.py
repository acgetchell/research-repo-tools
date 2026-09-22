"""Measurement models exercise real child output and source staleness without Git writes."""

import subprocess
import sys
from pathlib import Path

import pytest

from research_repo_tools import measurement
from research_repo_tools.criterion import Estimate, Sample, parse_sample, serialize_sample
from research_repo_tools.evidence import Provenance
from research_repo_tools.release_assets import package_baseline, read_baseline


def test_measurement_runs_configured_command_and_captures_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    program = tmp_path / "benchmark.py"
    program.write_text(
        "from pathlib import Path\n"
        "p = Path('target/criterion/workload/new/estimates.json')\np.parent.mkdir(parents=True)\n"
        "p.write_bytes(b'{\"median\":{\"point_estimate\":2}}')\nprint('measuring')\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(measurement, "resolve_revision", lambda *args: "a" * 40)
    config = measurement.MeasurementConfig(
        (sys.executable, "benchmark.py"), ("benchmark.py",), ("benchmark.py",), probes=(("python", (sys.executable, "--version")),)
    )
    sample, provenance = measurement.measure_checkout(tmp_path, config, "v1.0.0", mode="tag")
    assert sample == Sample((("workload", Estimate(2)),))
    context = dict(provenance.context)
    assert context["fingerprint-schema"] == "research-repo-tools/files/v1"
    assert context["source-inventory"] == '["benchmark.py"]'
    assert context["tool.python"].startswith('"Python 3.')
    with pytest.raises(ValueError, match="already exists"):
        measurement.measure_checkout(tmp_path, config, "v1.0.0", mode="tag")


def test_source_changes_reject_measurement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "source").write_bytes(b"before")
    monkeypatch.setattr(measurement, "resolve_revision", lambda *args: "a" * 40)

    def changed(*args, **kwargs):
        (tmp_path / "source").write_bytes(b"after")
        path = tmp_path / "target/criterion/example/new/estimates.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(b'{"median":{"point_estimate":1}}')
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(measurement, "run_command_live", changed)
    config = measurement.MeasurementConfig(("benchmark",), ("source",), ("source",))
    with pytest.raises(ValueError, match="changed"):
        measurement.measure_checkout(tmp_path, config, "v1.0.0", mode="tag")


def test_sample_and_baseline_round_trip_preserve_bounds_and_unknown_provenance(tmp_path: Path) -> None:
    sample = Sample((("name", Estimate(2, 1, 4, 0.95)), ("second", Estimate(8))), statistic="mean", unit="us")
    source = Provenance("a" * 40, context=(("release", "v1.0.0"),))
    assert parse_sample(serialize_sample(sample)) == sample
    first, second = tmp_path / "first.tar.gz", tmp_path / "second.tar.gz"
    package_baseline(sample, source, first)
    package_baseline(sample, source, second)
    assert first.read_bytes() == second.read_bytes()
    assert read_baseline(first) == (sample, source)
