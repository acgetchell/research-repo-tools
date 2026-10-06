"""Failure atomicity and platform probe models supplement installed contracts."""

import io
import json
import subprocess
from dataclasses import replace
from unittest.mock import patch

import pytest

from research_repo_tools import files, host_metadata
from research_repo_tools.common_measurement import measure_prepared_pair
from research_repo_tools.complete_runs import render_run, run_identity
from research_repo_tools.evidence import publish_evidence, serialize_evidence
from research_repo_tools.host_metadata import HostMetadata, capture_host, parse_host, serialize_host
from research_repo_tools.performance_reports import load_report_plan
from research_repo_tools.publication import publish_publication
from research_repo_tools.release_pairs import ReleasePair
from research_repo_tools.run_reports import load_latest_run
from research_repo_tools.scanner_output import FindingOutput
from tests.performance.public_complete_consumer import fixture, measured


def test_failed_pointer_promotion_restores_every_file(tmp_path, monkeypatch):
    (tmp_path / "report.toml").write_bytes(b'schema=2\ncurrent="docs/current.md"\narchive="docs/runs"\ntitle="Timing"\n')
    first = measured(tmp_path / "first", same_label=True)
    publish_evidence(first, tmp_path / "new.json", tmp_path / "manifest.json")
    publish_publication(load_report_plan(tmp_path, "report.toml", payload="new.json", manifest="manifest.json"))
    second = measured(tmp_path / "second", point=b"15", same_label=True)
    publish_evidence(second, tmp_path / "new.json", tmp_path / "manifest.json")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    plan = load_report_plan(tmp_path, "report.toml", payload="new.json", manifest="manifest.json")
    original = files._replace_path

    def fail_pointer(source, destination):
        if destination.name == "latest.json" and source.suffix == ".tmp":
            raise OSError("injected pointer failure")
        original(source, destination)

    monkeypatch.setattr(files, "_replace_path", fail_pointer)
    with pytest.raises(OSError, match="injected pointer"):
        publish_publication(plan)
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
    # Rollback may leave empty parent directories. They must not make a retry
    # look like corrupt evidence or block an otherwise complete history.
    monkeypatch.setattr(files, "_replace_path", original)
    publish_publication(load_report_plan(tmp_path, "report.toml", payload="new.json", manifest="manifest.json"))
    assert load_latest_run(tmp_path, "docs/runs") == second
    retained = tmp_path / "docs/runs/runs" / run_identity(second)
    payload, manifest = serialize_evidence(second)
    assert {path.name: path.read_bytes() for path in retained.iterdir()} == {"run.json": payload, "evidence.json": manifest, "report.md": render_run(second)}
    first_root = tmp_path / "docs/runs/runs" / run_identity(first)
    assert {path: path.read_bytes() for path in first_root.iterdir()} == {path: data for path, data in before.items() if path.parent == first_root}


def test_unavailable_required_tool_stops_before_gates(tmp_path):
    current, baseline = tmp_path / "current", tmp_path / "baseline"
    plan = fixture(current)
    fixture(baseline)
    plan = replace(plan, measurement=replace(plan.measurement, compatible=(*plan.measurement.compatible, "context.cpu")))
    host = HostMetadata("host", "arch", None, None, None, None, (("python", "Python 3.14"),))
    with (
        patch("research_repo_tools.measurement.resolve_revision", return_value="a" * 40),
        patch("research_repo_tools.measurement.capture_host", return_value=host),
        patch("research_repo_tools.common_measurement.run_command_live") as run,
    ):
        with pytest.raises(ValueError, match="unknown before timing"):
            measure_prepared_pair(baseline, current, plan, ReleasePair("v1.1.0", "v1.0.0"))
        run.assert_not_called()


def test_platform_count_models_without_changing_os_identity(tmp_path):
    cpuinfo = "\n\n".join(f"processor : {i}\nphysical id : {i // 4}\ncore id : {(i // 2) % 2}" for i in range(8))
    assert host_metadata._linux_counts(cpuinfo, "MemTotal: 2048 kB\n") == (4, 2097152)
    assert host_metadata._linux_counts(cpuinfo + "\n\nprocessor : 8", "MemTotal: invalid kB") == (None, None)
    with patch.object(host_metadata, "_probe", side_effect=["4", "8", "4096"]):
        assert host_metadata._hardware_counts("Darwin", tmp_path, {}, 1) == (4, 8, 4096)
    calls = []

    def windows(command, root, env, timeout):
        calls.append(command)
        assert timeout == 1
        return None if command[0] == "pwsh" else '{"cores":8,"threads":16,"memory":8192}'

    with patch.object(host_metadata, "_probe", side_effect=windows):
        assert host_metadata._hardware_counts("Windows", tmp_path, {}, 1) == (8, 16, 8192)
    assert [command[0] for command in calls] == ["pwsh", "powershell"]
    assert "[long]" in calls[-1][-1]


def test_failed_probes_and_invalid_retained_types_stay_unknown(tmp_path):
    with (
        patch.object(host_metadata, "run_command", side_effect=subprocess.TimeoutExpired("tool", 1)),
        patch.object(host_metadata, "cpu_description", return_value="unavailable"),
    ):
        host = capture_host(tmp_path, probes=(("slow", ("slow", "--version")),), timeout=1)
    assert dict(host.tools)["slow"] is None
    assert host.cpu is None
    for key, value in (("physical_cores", True), ("memory_bytes", -1), ("logical_threads", "8"), ("cpu", 1), ("tools", {"rustc": False})):
        raw = json.loads(serialize_host(host))
        raw[key] = value
        with pytest.raises(ValueError):
            parse_host(json.dumps(raw).encode())


def test_terminal_output_escapes_controls_and_narrow_encodings(tmp_path):
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="ascii", newline="\n")
    with patch("sys.stdout", stream):
        FindingOutput("Semgrep", "source", tmp_path)(
            {"results": [{"check_id": "rule\x1b[2J", "path": "café\u202e.py", "start": {"line": 2}}]}, "json", tmp_path / "café.json", 0, True
        )
    stream.flush()
    output = buffer.getvalue()
    assert b"\x1b" not in output
    assert b"\\u202e" in output
    assert b"\\xe9" in output
