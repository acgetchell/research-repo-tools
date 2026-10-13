"""Measurement provenance and failure boundaries using small independent fixtures."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/measure_python_workflows.py"


@pytest.fixture
def harness():
    spec = importlib.util.spec_from_file_location("python_workflow_measurements", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_failed_child_reports_both_streams_and_case_context(harness):
    with pytest.raises(RuntimeError) as caught:
        harness.execute(
            [sys.executable, "-c", "import sys; print('worker output'); print('validator problem',file=sys.stderr); sys.exit(7)"],
            env=dict(os.environ),
            label="validation source=fixture interpreter=prepared JIT=off",
        )
    message = str(caught.value)
    assert "exit code 7" in message and "source=fixture" in message
    assert "worker output" in message and "validator problem" in message


def test_timeout_preserves_captured_byte_diagnostic(harness, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1, stderr=b"checker blocked\xff")

    monkeypatch.setattr(harness.subprocess, "run", timeout)
    with pytest.raises(
        RuntimeError,
        match="probe timed out after 1 seconds.*",
    ) as caught:
        harness.execute(["prepared-python"], env={}, label="probe", timeout=1)
    assert "checker blocked\ufffd" in str(caught.value)


def test_python_child_streams_match_utf8_capture_under_legacy_encoding(harness, tmp_path):
    source = tmp_path / "caf\u00e9 source"
    source.mkdir()
    environment = {**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252:backslashreplace"}
    before = dict(environment)
    command = [sys.executable, "-c", "import sys; print(sys.argv[1]); print(sys.argv[1], file=sys.stderr)", str(source)]
    # Capture bytes before decoding: Windows subprocess text readers raise
    # decoding errors in background threads instead of the calling thread.
    legacy = subprocess.run(command, env=environment, check=True, capture_output=True, timeout=30)
    expected = str(source).encode("cp1252", errors="backslashreplace") + os.linesep.encode("ascii")
    assert legacy.stdout == legacy.stderr == expected
    for stream in (legacy.stdout, legacy.stderr):
        with pytest.raises(UnicodeDecodeError):
            stream.decode("utf-8")
    result = harness.execute(
        command,
        env=environment,
        label="historical interpreter transport",
    )
    assert result.stdout == result.stderr == str(source) + "\n"
    assert environment == before


def test_failed_output_write_preserves_evidence_and_cleans_candidate(harness, tmp_path, monkeypatch):
    output = tmp_path / "evidence.json"
    output.write_bytes(b"retained evidence")
    native_fdopen = harness.os.fdopen

    class FailedWrite:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, payload):
            self.stream.write(payload[:3])
            raise OSError("injected partial write")

    monkeypatch.setattr(harness.os, "fdopen", lambda *args, **kwargs: FailedWrite(native_fdopen(*args, **kwargs)))
    with pytest.raises(OSError, match="injected partial write"):
        harness.write_output(output, {"complete": True})
    assert output.read_bytes() == b"retained evidence"
    assert list(tmp_path.iterdir()) == [output]


def test_atomic_output_has_exact_utf8_lf_bytes(harness, tmp_path):
    output = tmp_path / "evidence.json"
    harness.write_output(output, {"complete": True})
    assert output.read_bytes() == b'{\n  "complete": true\n}\n'
    assert list(tmp_path.iterdir()) == [output]


def test_wrong_source_is_rejected_before_workers_and_preserves_output(tmp_path):
    output = tmp_path / "evidence.json"
    output.write_bytes(b"retained evidence")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--source", str(tmp_path), "--files", "1", "--trials", "1", "--output", str(output)],
        check=False,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 2
    assert "source must contain research_repo_tools/__init__.py" in result.stderr
    assert "Traceback" not in result.stderr
    assert output.read_bytes() == b"retained evidence"


def test_worker_rejects_another_import_origin(harness, tmp_path):
    with pytest.raises(ValueError, match="worker imported a different"):
        harness.check_origin(tmp_path)


def test_incomplete_selection_is_rejected_against_fixture_inventory(harness, tmp_path, monkeypatch):
    from research_repo_tools import selection

    names = ["first.py", "second.py"]
    for name in names:
        (tmp_path / name).write_bytes(b"value = 1\n")
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(names), encoding="utf-8", newline="\n")
    monkeypatch.setattr(selection, "select_files", lambda *args, **kwargs: ("first.py",))
    with pytest.raises(ValueError, match="complete benchmark fixture"):
        harness.worker("selection", tmp_path, inventory)


@pytest.fixture
def experiment(harness, tmp_path, monkeypatch):
    source = tmp_path / "src"
    package = source / "research_repo_tools"
    package.mkdir(parents=True)
    origin = package / "__init__.py"
    origin.write_bytes(b"# original source\n")
    tools = {}
    for name in ("ruff", "ty"):
        path = tmp_path / name
        path.write_bytes(b"native tool fixture")
        tools[name] = str(path)
    output = tmp_path / "evidence.json"
    output.write_bytes(b"retained evidence")
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--source", str(source), "--output", str(output), "--files", "1", "--trials", "1"])

    def execute(command, **kwargs):
        if "--version" in command:
            text = f"{Path(command[0]).name} 1.0.0\n"
        elif "--worker" in command:
            text = json.dumps(
                {"seconds": 0.1, "resolve_calls": 1, "validator_invocations": 3, "selected_files": 1, "validator_executables": sorted(tools.values())}
            )
        elif "tool_paths=" in command[-1]:
            text = json.dumps({"python": "3.15.0", "tools": {"ruff": "1.0.0", "ty": "1.0.0"}, "tool_paths": tools})
        elif "print(research_repo_tools.__file__)" in command[-1]:
            text = str(origin)
        else:
            text = "command output\n"
        return subprocess.CompletedProcess(command, 0, text, "")

    monkeypatch.setattr(harness, "execute", execute)
    return source, origin, output, execute


def test_source_edit_between_trials_rejects_publication(harness, experiment, monkeypatch):
    _, origin, output, execute = experiment

    def edit(command, **kwargs):
        result = execute(command, **kwargs)
        if "--worker" in command:
            origin.write_bytes(b"# edited while worker ran\n")
        return result

    monkeypatch.setattr(harness, "execute", edit)
    with pytest.raises(ValueError, match="measured source changed"):
        harness.main()
    assert output.read_bytes() == b"retained evidence"


def test_native_version_mismatch_rejects_publication(harness, experiment, monkeypatch):
    _, _, output, execute = experiment

    def drift(command, **kwargs):
        result = execute(command, **kwargs)
        return subprocess.CompletedProcess(command, 0, "ruff 2.0.0\n", "") if "--version" in command else result

    monkeypatch.setattr(harness, "execute", drift)
    with pytest.raises(ValueError, match="native ruff version differs"):
        harness.main()
    assert output.read_bytes() == b"retained evidence"


def test_source_probe_cannot_silently_use_installed_package(harness, experiment, monkeypatch):
    _, _, output, execute = experiment

    def fallback(command, **kwargs):
        result = execute(command, **kwargs)
        if "print(research_repo_tools.__file__)" in command[-1]:
            return subprocess.CompletedProcess(command, 0, str(SCRIPT), "")
        return result

    monkeypatch.setattr(harness, "execute", fallback)
    with pytest.raises(ValueError, match="imported source differs"):
        harness.main()
    assert output.read_bytes() == b"retained evidence"


def test_complete_experiment_records_bound_source_harness_and_tools(harness, experiment):
    source, _, output, _ = experiment
    harness.main()
    evidence = json.loads(output.read_bytes())
    assert len(evidence["records"]) == 4
    assert all(record["source"] == str(source) and len(record["source_sha256"]) == 64 for record in evidence["records"])
    assert all(record["native_tools"]["ruff"]["version_output"] == "ruff 1.0.0" for record in evidence["records"])
    assert len(evidence["harness_sha256"]) == 64


def test_harness_change_rejects_input_verification(harness, tmp_path):
    script = tmp_path / "worker.py"
    script.write_bytes(b"# replaced harness\n")
    with pytest.raises(ValueError, match="measurement harness changed"):
        harness.verify_inputs({}, script, "0" * 64)
