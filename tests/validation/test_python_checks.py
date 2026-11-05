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


def test_batches_preserve_names_and_stop_at_first_failure(gate, monkeypatch):
    monkeypatch.setattr(python_checks, "argument_batches", lambda command, names, **kw: argument_batches(command, names, batch_size=2))
    calls = []

    def execute(command, args, **kwargs):
        calls.append((command, args, kwargs))
        return subprocess.CompletedProcess([command, *args], 7 if len(calls) == 1 else 1)

    monkeypatch.setattr(python_checks, "run_command_live", execute)
    assert main(["--root", str(gate.root), "python", "check", "--timeout", "9"]) == 7
    assert len(calls) == 1
    assert calls[0][1][-2:] == ("./nested/café space.py", "./-option.pyi")
    assert all(call[2] == {"cwd": gate.root, "timeout": 9, "check": False} for call in calls)


@pytest.mark.parametrize(
    "action, policies",
    [("check", [("ruff", "format"), ("ruff", "check"), ("ty", "check")]), ("fix", [("ruff", "check"), ("ruff", "format")]), ("typecheck", [("ty", "check")])],
)
def test_one_inventory_preflight_and_canonical_order(gate, monkeypatch, action, policies):
    names = tuple(f"nested/file{index}.py" for index in range(468))
    inventories = []
    resolved = []
    calls = []
    monkeypatch.setattr(python_checks, "select_files", lambda *a, **kw: inventories.append(kw) or names)
    monkeypatch.setattr(python_checks, "resolve_executable", lambda name, **kw: resolved.append(name) or Path(name))

    def run(command, args, **kwargs):
        assert resolved == list(dict.fromkeys(name for name, _ in policies))
        calls.append((command, args))
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(python_checks, "run_command_live", run)
    assert python_checks.run(gate, action) == 0
    assert inventories == [{"include": ("*.py", "*.pyi")}]
    assert [(name, args[0]) for name, args in calls] == policies
    assert all(args[-468:] == tuple("./" + name for name in names) for _, args in calls)
    if action != "fix":
        assert "--error" in calls[-1][1] and "all" in calls[-1][1]


@pytest.mark.parametrize("failed_batch", [1, 2, 3, 4, 5, 6])
def test_argument_limited_batches_stop_at_any_failed_policy(gate, monkeypatch, failed_batch):
    monkeypatch.setattr(python_checks, "argument_batches", lambda command, names, **kw: argument_batches(command, names, batch_size=2))
    calls = []
    monkeypatch.setattr(
        python_checks, "run_command_live", lambda *a, **kw: calls.append(a) or subprocess.CompletedProcess([], -15 if len(calls) == failed_batch else 0)
    )
    assert python_checks.run(gate, "check") == 143
    assert len(calls) == failed_batch


def test_oversized_last_argument_fails_before_any_validator(gate, monkeypatch):
    monkeypatch.setattr(python_checks, "select_files", lambda *a, **kw: ("short.py", "long" * 10000 + ".py"))
    monkeypatch.setattr(python_checks, "run_command_live", lambda *a, **kw: pytest.fail("executed partial preflight"))
    with pytest.raises(ValueError, match="argument limit"):
        python_checks.run(gate, "check")


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
