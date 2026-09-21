"""Notebook lint behavior through real checkers and isolated process failures."""

import json
import subprocess
import sys
from importlib.metadata import PackageNotFoundError

import nbformat
import pytest

from research_repo_tools import cli, config, notebook_lint, notebooks


@pytest.fixture
def consumer(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python=">=3.14"\n[tool.ruff]\nline-length=160\n[tool.ruff.lint]\nselect=["E4", "E7", "E9", "F"]\n', newline="\n"
    )
    return config.load(root=tmp_path)


def write(settings, cells, *, name="analysis.ipynb"):
    path = settings.root / "notebooks" / name
    path.parent.mkdir(exist_ok=True)
    node = nbformat.v4.new_notebook(
        cells=[
            nbformat.v4.new_markdown_cell("# Analysis", id="overview"),
            *[nbformat.v4.new_code_cell(source.removesuffix("\n"), id=cell_id) for cell_id, source in cells],
        ]
    )
    path.write_text(nbformat.writes(node), encoding="utf-8", newline="\n")
    return path


def run(settings, path, *args):
    return cli.main(["--root", str(settings.root), "notebooks", "lint", str(path), *args])


def test_real_checkers_keep_cross_cell_references_and_files_unchanged(consumer, capsys):
    path = write(consumer, [("define", "value = 1\n"), ("use", "print(value + 1)\n")])
    originals = {p: p.read_bytes() for p in consumer.root.rglob("*") if p.is_file()}
    assert run(consumer, path) == 0
    captured = capsys.readouterr()
    assert captured.err == "" and captured.out == f"OK linted {path}\n"
    assert {p: p.read_bytes() for p in consumer.root.rglob("*") if p.is_file()} == originals


@pytest.mark.parametrize(
    "source,expected",
    [
        ("if True print(1)\n", "invalid-syntax"),
        ("print(missing_value)\n", "F821"),
        ('value: int = "wrong"\n', "invalid-assignment"),
        ("value=  1\n", "unformatted"),
    ],
)
def test_real_failures_identify_original_cell_id_and_position(consumer, capsys, source, expected):
    path = write(consumer, [("problem", source)])
    before = path.read_bytes()
    assert run(consumer, path) == 1
    captured = capsys.readouterr()
    assert f"{path}: cell 2 (problem):1:" in captured.err
    assert expected in captured.err and "OK linted" not in captured.out
    assert path.read_bytes() == before


def test_real_python_cell_cannot_continue_a_block_in_the_next_cell(consumer, capsys):
    path = write(consumer, [("start", "if True:\n"), ("finish", "    print(1)\n")])
    assert run(consumer, path) == 1
    diagnostics = capsys.readouterr().err
    assert "cell 2 (start):1:" in diagnostics and "cell 3 (finish):1:" in diagnostics
    assert "invalid-syntax" in diagnostics


@pytest.mark.parametrize(
    "cells",
    [
        [("magic", "%matplotlib inline\nvalue = 1\n"), ("use", "print(value)\n")],
        [("shell", "files = !printf example\n"), ("use", "print(files)\n")],
        [("assignment", "directory = %pwd\n"), ("use", "print(directory)\n")],
        [("timed", "%%time\nvalue = 1\n"), ("use", "print(value)\n")],
        [("literal", 'message = """start\n%this is string content\n!also string content\n"""\nprint(message)\n')],
        [("async", "import asyncio\n\nawait asyncio.sleep(0)\n")],
        [("foreign", "%%bash\nthis is not Python {\n")],
    ],
)
def test_real_native_ipython_forms_are_checked_without_execution(consumer, capsys, cells):
    path = write(consumer, cells)
    before = path.read_bytes()
    assert run(consumer, path) == 0, capsys.readouterr().err
    assert path.read_bytes() == before


@pytest.mark.parametrize("body,expected", [("if True print(1)", "invalid-syntax"), ('value: int = "wrong"', "invalid-assignment")])
def test_real_python_body_inside_cell_magic_is_not_silently_skipped(consumer, capsys, body, expected):
    path = write(consumer, [("timed", f"%%time\n{body}\n")])
    assert run(consumer, path) == 1
    diagnostics = capsys.readouterr().err
    assert "cell 2 (timed):2:" in diagnostics and expected in diagnostics


def test_real_checks_cannot_run_cell_side_effects(consumer):
    sentinel = consumer.root / "must-not-exist"
    path = write(consumer, [("side-effect", f"from pathlib import Path\n\nPath({json.dumps(str(sentinel))}).touch()\n")])
    assert run(consumer, path) == 0
    assert not sentinel.exists()


def test_real_consumer_rule_and_format_configuration_are_honored(consumer, capsys):
    (consumer.root / "notebooks").mkdir()
    (consumer.root / "notebooks/ruff.toml").write_text('line-length=160\n[lint]\nselect=["F401"]\n[format]\nquote-style="single"\n', newline="\n")
    path = write(consumer, [("format", 'print("double quotes")\n')])
    assert run(consumer, path) == 1
    assert "ruff format [unformatted]" in capsys.readouterr().err
    path = write(consumer, [("configured", "import os\n\nprint('single quotes')\n")])
    assert run(consumer, path) == 1
    assert "F401" in capsys.readouterr().err
    (consumer.root / "notebooks/ruff.toml").write_text(
        'line-length=160\n[lint]\nselect=["F401"]\nignore=["F401"]\n[format]\nquote-style="single"\n', newline="\n"
    )
    assert run(consumer, path) == 0, capsys.readouterr().err


def test_real_ty_uses_consumer_project_rules_and_installed_environment(consumer, capsys):
    (consumer.root / "ty.toml").write_text('[rules]\ninvalid-assignment="ignore"\n', newline="\n")
    path = write(consumer, [("typed", 'import nbformat\n\nvalue: int = "wrong"\nprint(nbformat.__version__, value)\n')])
    assert run(consumer, path) == 0, capsys.readouterr().err


def test_real_explicit_selection_overrides_excludes_and_fixes_are_disabled(consumer, capsys):
    (consumer.root / "ruff.toml").write_text('fix=true\nfix-only=true\nforce-exclude=true\nexclude=["*.ipynb"]\n[lint]\nselect=["F401"]\n', newline="\n")
    (consumer.root / "ty.toml").write_text('[src]\nexclude=["*.ipynb"]\n', newline="\n")
    path = write(consumer, [("ignored", 'import os\n\nvalue: int = "wrong"\n')])
    original = path.read_bytes()
    assert run(consumer, path) == 1
    diagnostics = capsys.readouterr().err
    assert "F401" in diagnostics and "invalid-assignment" in diagnostics
    assert path.read_bytes() == original and not (consumer.root / ".ruff_cache").exists()


def test_output_policy_is_shared_with_structure_check(consumer, monkeypatch, capsys):
    path = write(consumer, [("value", "value = 1\n")])
    node = notebooks.load(path).node
    node.cells[1].execution_count = 1
    path.write_bytes(notebooks.serialize(node))
    monkeypatch.setattr(notebook_lint, "_run", lambda *args: [])
    assert run(consumer, path) == 1
    assert "must be cleared" in capsys.readouterr().err
    settings = config.parse({"notebooks": {"outputs": "preserve"}}, root=consumer.root)
    assert notebook_lint.lint(settings, [path]) == 0


@pytest.mark.parametrize("package,installed", [("ruff", None), ("ty", None), ("ruff", "0.15.0"), ("ty", "0.0.66")])
def test_missing_or_old_project_checkers_fail_without_using_path(consumer, monkeypatch, capsys, package, installed):
    path = write(consumer, [("valid", "value = 1\n")])

    def version(name):
        if name == package:
            if installed is None:
                raise PackageNotFoundError(name)
            return installed
        return notebook_lint.MINIMUM_VERSIONS[name]

    monkeypatch.setattr(notebook_lint, "version", version)
    monkeypatch.setattr(notebook_lint, "run_safe_command", lambda *args, **kwargs: pytest.fail("ran a checker before prerequisite validation"))
    assert run(consumer, path) == 1
    assert f"requires {package}>=" in capsys.readouterr().err


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("timeout", "timed out after 7 seconds"),
        ("missing", "unavailable"),
        ("crash", "exit code 17"),
        ("empty_failure", "without diagnostics"),
        ("malformed", "invalid diagnostics"),
    ],
)
def test_failed_checker_never_becomes_a_clean_result(consumer, monkeypatch, capsys, failure, expected):
    path = write(consumer, [("valid", "value = 1\n")])
    calls = []

    def execute(command, args, **kwargs):
        calls.append((command, args, kwargs))
        if args[1] == "ty":
            return subprocess.CompletedProcess(args, 0, "All checks passed!\n", "")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 7)
        if failure == "missing":
            raise OSError("checker binary missing")
        if failure == "crash":
            return subprocess.CompletedProcess(args, 17, "", "synthetic crash")
        return subprocess.CompletedProcess(args, 1, "[]" if failure == "empty_failure" else "not json", "")

    monkeypatch.setattr(notebook_lint, "run_safe_command", execute)
    assert run(consumer, path, "--timeout", "7") == 1
    assert expected in capsys.readouterr().err
    assert len(calls) == 3 and all(call[0] == sys.executable and call[2]["timeout"] == 7 for call in calls)
    assert calls[-1][1][calls[-1][1].index("--python") + 1] == sys.prefix


@pytest.mark.parametrize("cell,row", [(True, 1), (0, 1), (99, 1), (2, False), (2, 0)])
def test_malformed_checker_location_cannot_misattribute_diagnostics(consumer, cell, row):
    notebook = notebooks.load(write(consumer, [("valid", "value = 1\n")]))
    output = json.dumps([{"message": "bad", "code": "F821", "cell": cell, "location": {"row": row, "column": 1}}])
    with pytest.raises(ValueError, match="invalid"):
        notebook_lint.ruff_diagnostics(output, notebook, "ruff check")


@pytest.mark.parametrize("timeout", ["0", "-1"])
def test_invalid_timeout_stops_before_checkers(consumer, monkeypatch, capsys, timeout):
    path = write(consumer, [("valid", "value = 1\n")])
    monkeypatch.setattr(notebook_lint, "run_safe_command", lambda *args, **kwargs: pytest.fail("checker started"))
    assert run(consumer, path, "--timeout", timeout) == 1
    assert "positive integer" in capsys.readouterr().err


def test_non_python_notebook_cannot_be_silently_skipped(consumer, monkeypatch, capsys):
    path = write(consumer, [("not-python", "unparseable Python {")])
    node = notebooks.load(path).node
    node.metadata.language_info = {"name": "julia"}
    path.write_bytes(notebooks.serialize(node))
    monkeypatch.setattr(notebook_lint, "run_safe_command", lambda *args, **kwargs: pytest.fail("checker started"))
    assert run(consumer, path) == 1
    assert "requires Python notebook metadata" in capsys.readouterr().err


def test_validates_whole_selection_before_starting_checkers(consumer, monkeypatch, capsys):
    path = write(consumer, [("valid", "value = 1\n")])
    bad = consumer.root / "bad.ipynb"
    bad.write_text("{}", newline="\n")
    monkeypatch.setattr(notebook_lint, "run_safe_command", lambda *args, **kwargs: pytest.fail("checker started before parsing selection"))
    assert run(consumer, path, str(bad)) == 1
    assert str(bad) in capsys.readouterr().err
