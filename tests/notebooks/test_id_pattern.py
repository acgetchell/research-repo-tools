"""Strict spelling policy composes with existing identity and advisory gates."""

import json

import pytest

from research_repo_tools import cli, config, notebook_advice, notebook_lint
from tests.notebooks.public_notebook_consumer import code

KEBAB = "[a-z0-9]+(?:-[a-z0-9]+)*"


@pytest.fixture
def quiet_checkers(monkeypatch):
    monkeypatch.setattr(notebook_lint, "require_checkers", lambda: None)
    monkeypatch.setattr(notebook_lint, "_run", lambda *args: [])


def write(root, cells):
    path = root / "spelling.ipynb"
    path.write_bytes((json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": cells}) + "\r\n").encode())
    return path


@pytest.mark.usefixtures("quiet_checkers")
@pytest.mark.parametrize("cell_id", ["explain-results", "plot-2d", "a", "a" * 64, "123"])
def test_lowercase_kebab_pattern_accepts_full_ids_without_rewriting(tmp_path, cell_id):
    path = write(tmp_path, [code(cell_id=cell_id)])
    before = path.read_bytes()
    settings = config.parse({"notebooks": {"id-pattern": KEBAB}}, root=tmp_path)
    assert notebook_lint.lint(settings, [path]) == 0
    assert path.read_bytes() == before


@pytest.mark.usefixtures("quiet_checkers")
@pytest.mark.parametrize("cell_id", ["Plot-results", "plot_results", "-plot", "plot-", "plot--results"])
def test_rejected_spelling_reports_existing_identity_and_preserves_all_cell_types(tmp_path, capsys, cell_id):
    cells = [
        code(cell_id=cell_id),
        {"cell_type": "markdown", "id": "Explain_Results", "metadata": {}, "source": "# Results"},
        {"cell_type": "raw", "id": "Raw-Data", "metadata": {}, "source": "raw"},
    ]
    path = write(tmp_path, cells)
    before = path.read_bytes()
    assert notebook_lint.lint(config.parse({"notebooks": {"id-pattern": KEBAB}}, root=tmp_path), [path]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    for number, cell in enumerate(cells, 1):
        assert f"{path}: cell {number} ({cell['id']})" in output.err
    assert "id-pattern" in output.err and path.read_bytes() == before


@pytest.mark.usefixtures("quiet_checkers")
def test_broad_default_and_cli_override_keep_the_nbformat_contract(tmp_path, capsys):
    path = write(tmp_path, [code(cell_id="Plot_Results")])
    before = path.read_bytes()
    assert cli.main(["--root", str(tmp_path), "notebooks", "lint", str(path)]) == 0
    settings = config.parse({"notebooks": {"id-pattern": KEBAB}}, root=tmp_path)
    assert notebook_lint.lint(settings, [path], id_pattern="[A-Za-z_]+") == 0
    assert cli.main(["--root", str(tmp_path), "notebooks", "lint", str(path), "--id-pattern", KEBAB]) == 1
    assert "id-pattern" in capsys.readouterr().err and path.read_bytes() == before


@pytest.mark.usefixtures("quiet_checkers")
@pytest.mark.parametrize("ids,expected", [(["same", "same"], "duplicate"), (["a" * 65], "1-64"), (["bad id"], "ASCII"), ([None], "existing")])
def test_pattern_never_relaxes_presence_uniqueness_length_or_ascii(tmp_path, capsys, ids, expected):
    cells = [code(cell_id=value) for value in ids]
    if ids == [None]:
        del cells[0]["id"]
    path = write(tmp_path, cells)
    before = path.read_bytes()
    assert cli.main(["--root", str(tmp_path), "notebooks", "lint", str(path), "--id-pattern", ".*"]) == 1
    assert expected in capsys.readouterr().err and path.read_bytes() == before


@pytest.mark.parametrize("pattern", ["[", "", 123, True])
def test_bad_patterns_fail_at_configuration_boundary(tmp_path, pattern):
    with pytest.raises(ValueError, match="id-pattern"):
        config.parse({"notebooks": {"id-pattern": pattern}}, root=tmp_path)


def test_bad_cli_pattern_stops_before_loading_or_checking(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(notebook_lint, "python_notebooks", lambda *args: pytest.fail("loaded a notebook before pattern validation"))
    assert cli.main(["--root", str(tmp_path), "notebooks", "lint", "missing.ipynb", "--id-pattern", "["]) == 1
    assert "valid regular expression" in capsys.readouterr().err


@pytest.mark.usefixtures("quiet_checkers")
def test_spelling_and_descriptive_advice_remain_independent(tmp_path, capsys):
    path = write(tmp_path, [code(cell_id="cell-1")])
    settings = config.parse({"notebooks": {"id-pattern": KEBAB}}, root=tmp_path)
    assert notebook_lint.lint(settings, [path]) == 0
    assert notebook_advice.advise(settings, [path], strict=True) == 1
    assert "descriptive-id" in capsys.readouterr().err
