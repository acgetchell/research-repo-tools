"""Invalid advisory policy and checker failures must not pass as warnings."""

import json
import subprocess
from importlib.metadata import PackageNotFoundError

import pytest

from research_repo_tools import cli, config, notebook_lint, notebooks
from tests.notebooks.public_notebook_consumer import code


@pytest.mark.parametrize(
    "policy",
    [
        [],
        {"unknown": True},
        {"strict": 1},
        {"descriptive-ids": "true"},
        {"subprocess-timeout": []},
        {"ruff-rules": "ANN"},
        {"ruff-rules": [False]},
        {"ruff-rules": ["--fix"]},
        {"ruff-rules": ["ANN,ALL"]},
        {"ruff-rules": ["ANN", "ANN"]},
    ],
)
def test_invalid_configuration_fails_at_boundary(tmp_path, policy):
    with pytest.raises(ValueError, match="notebooks.advice"):
        config.parse({"notebooks": {"advice": policy}}, root=tmp_path)


@pytest.fixture
def consumer(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.research-repo-tools.notebooks.advice]\nruff-rules=["ANN"]\n', newline="\n")
    path = tmp_path / "analysis.ipynb"
    path.write_bytes(json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [code()]}).encode("utf-8"))
    return ["--root", str(tmp_path), "notebooks", "advise", str(path)]


@pytest.mark.parametrize("failure", ["timeout", "crash", "unavailable", "invalid-json", "invalid-cell", "stderr", "empty-failure"])
def test_checker_operational_errors_fail_warning_mode(consumer, monkeypatch, capsys, failure):
    def execute(command, args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 30)
        if failure == "unavailable":
            raise OSError("checker missing")
        if failure == "crash":
            return subprocess.CompletedProcess(args, 17, "", "synthetic crash")
        if failure == "stderr":
            return subprocess.CompletedProcess(args, 0, "[]", "checker problem")
        if failure == "invalid-cell":
            return subprocess.CompletedProcess(
                args, 1, json.dumps([{"message": "bad cell", "code": "ANN001", "cell": 99, "location": {"row": 1, "column": 1}}])
            )
        return subprocess.CompletedProcess(args, 1, "[]" if failure == "empty-failure" else "not json", "")

    monkeypatch.setattr(notebook_lint, "run_safe_command", execute)
    assert cli.main(consumer) == 1
    assert "ERROR" in capsys.readouterr().err


def test_missing_ruff_is_actionable_and_ty_is_not_required(consumer, monkeypatch, capsys):
    def missing(name):
        assert name == "ruff", "advice tried to require ty"
        raise PackageNotFoundError(name)

    monkeypatch.setattr(notebook_lint, "version", missing)
    assert cli.main(consumer) == 1
    assert "notebooks advise requires ruff>=0.16.8" in capsys.readouterr().err


def test_inspection_needs_no_optional_dependencies_but_advice_reports_them(consumer, monkeypatch, capsys):
    def missing(name):
        raise ImportError(name)

    monkeypatch.setattr(notebooks, "import_module", missing)
    assert cli.main(consumer) == 1
    assert "research-repo-tools[notebooks]" in capsys.readouterr().err
    assert cli.main([*consumer[:3], "inspect", consumer[-1], "--no-preview"]) == 0


def test_timeout_validation_precedes_checker_execution(consumer, monkeypatch, capsys):
    monkeypatch.setattr(notebook_lint, "run_safe_command", lambda *args, **kwargs: pytest.fail("started checker"))
    assert cli.main([*consumer, "--timeout", "0"]) == 1
    assert "positive integer" in capsys.readouterr().err
