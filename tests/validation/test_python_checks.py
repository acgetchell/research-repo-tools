"""Preflight and failure behavior for the shared Python gate."""

import subprocess
from pathlib import Path

import pytest

from research_repo_tools import config, python_checks
from research_repo_tools.cli import main
from research_repo_tools.process import ExecutableNotFoundError
from research_repo_tools.selection import argument_batches


@pytest.fixture
def gate(tmp_path, monkeypatch):
    monkeypatch.setattr(python_checks, "select_files", lambda *a, **kw: ("nested/café space.py", "-option.pyi", "fixture.py"))
    monkeypatch.setattr(python_checks, "resolve_executable", lambda name, **kw: Path(name))
    return config.load(root=tmp_path)


def test_missing_validator_fails_before_any_execution(gate, monkeypatch):
    def resolve(name, **kwargs):
        if name == "ty":
            raise ExecutableNotFoundError("ty missing")
        return Path(name)

    monkeypatch.setattr(python_checks, "resolve_executable", resolve)
    monkeypatch.setattr(python_checks, "run_command_live", lambda *a, **kw: pytest.fail("ran before preflight"))
    with pytest.raises(ExecutableNotFoundError, match="ty missing"):
        python_checks.run(gate, "check")


@pytest.mark.parametrize("action", ["check", "fix", "typecheck"])
def test_empty_inventory_succeeds_without_requiring_tools(gate, monkeypatch, capsys, action):
    monkeypatch.setattr(python_checks, "select_files", lambda *a, **kw: ())
    monkeypatch.setattr(python_checks, "resolve_executable", lambda *a, **kw: pytest.fail("resolved empty inventory"))
    assert python_checks.run(gate, action) == 0
    assert "No Python files" in capsys.readouterr().out


def test_empty_inventory_checks_inherited_declarations_without_requiring_executables(gate, monkeypatch):
    from research_repo_tools import python_tools

    settings = config.parse({"toolchain": {"inherit-python-tools": True}}, root=gate.root)
    monkeypatch.setattr(python_checks, "select_files", lambda *a, **kw: ())
    calls = []
    monkeypatch.setattr(python_tools, "check", lambda root, *, executables=True: calls.append(executables))
    assert python_checks.run(settings, "check") == 0
    assert calls == [False]


def test_batches_preserve_names_and_first_failure_and_continue(gate, monkeypatch):
    monkeypatch.setattr(python_checks, "argument_batches", lambda command, names: argument_batches(command, names, batch_size=2))
    calls = []

    def execute(command, args, **kwargs):
        calls.append((command, args, kwargs))
        return subprocess.CompletedProcess([command, *args], 7 if len(calls) == 1 else 1)

    monkeypatch.setattr(python_checks, "run_command_live", execute)
    assert main(["--root", str(gate.root), "python", "check", "--timeout", "9"]) == 7
    assert len(calls) == 6
    assert calls[0][1][-2:] == ("./nested/café space.py", "./-option.pyi")
    assert calls[-1][1][-1] == "./fixture.py"
    assert all(call[2] == {"cwd": gate.root, "timeout": 9, "check": False} for call in calls)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_fails_before_discovery(gate, monkeypatch, timeout):
    monkeypatch.setattr(python_checks, "select_files", lambda *a, **kw: pytest.fail("discovered before validation"))
    with pytest.raises(ValueError, match="positive and finite"):
        python_checks.run(gate, "check", timeout=timeout)


def test_baseline_drift_fails_before_running_tools(tmp_path, monkeypatch):
    settings = config.parse({"toolchain": {"inherit-python": True}}, root=tmp_path)
    monkeypatch.setattr(python_checks.python_baseline, "check", lambda *_: (_ for _ in ()).throw(ValueError("shared Python drift")))
    monkeypatch.setattr(python_checks, "select_files", lambda *a, **kw: pytest.fail("discovered with drift"))
    with pytest.raises(ValueError, match="shared Python drift"):
        python_checks.run(settings, "check")
