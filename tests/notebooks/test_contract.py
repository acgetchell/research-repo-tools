"""Small synthetic notebook regressions, including real fresh-kernel execution."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import nbformat
import pytest

from research_repo_tools import cli, config, files, notebooks, toolchain


def write(path, source="value = 42\n", *, cell_id="calculate", metadata=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    node = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(source, id=cell_id, metadata=metadata or {})])
    path.write_text(nbformat.writes(node), encoding="utf-8")
    return path


@pytest.fixture
def consumer(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python=">=3.14"\n[dependency-groups]\nnotebook=[]\ndev=[]\n[tool.uv]\nrequired-version="==0.12.16"\n'
    )
    (tmp_path / ".python-version").write_text("3.14\n")
    (tmp_path / "uv.lock").write_text("version=1\n")
    # Exercise real project/runtime parsing; the test interpreter is explicitly
    # selected as the disposable consumer's environment without a package install.
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", sys.prefix)
    monkeypatch.setattr(toolchain.Runtime, "uv_status", lambda self: toolchain.Status("uv", "0.12.16", "0.12.16", "uv", True))
    return config.load(root=tmp_path)


@pytest.mark.parametrize(
    "alter,match",
    [
        (lambda node: node.update(nbformat=True), "expected nbformat 4"),
        (lambda node: node.update(nbformat_minor=4), "stable cell IDs"),
        (lambda node: node.cells[0].pop("id"), "existing.*cell ID"),
        (lambda node: node.cells.append(node.cells[0]), "duplicate cell ID"),
        (lambda node: node.cells[0].update(id="invalid id"), "existing.*cell ID"),
        (lambda node: node.cells[0].update(source=123), "invalid notebook structure"),
        (lambda node: node.cells[0].update(outputs=[{"output_type": "unknown"}]), "invalid notebook structure"),
        (lambda node: node.cells[0].update(execution_count=True), "invalid notebook structure"),
        (lambda node: node.cells[0].update(unexpected="field"), "invalid notebook structure"),
    ],
)
def test_invalid_structure_is_never_repaired(tmp_path, alter, match):
    path = write(tmp_path / "input.ipynb")
    node = nbformat.from_dict(json.loads(path.read_bytes()))
    alter(node)
    original = notebooks.serialize(node)
    path.write_bytes(original)
    with pytest.raises(ValueError, match=match):
        notebooks.check([path])
    assert path.read_bytes() == original


@pytest.mark.parametrize("text,match", [("[]", "nbformat"), ('{"nbformat":4,"nbformat":4}', "duplicate JSON"), ('{"x":NaN}', "non-finite")])
def test_invalid_json_boundary(tmp_path, text, match):
    path = tmp_path / "input.ipynb"
    path.write_text(text)
    with pytest.raises(ValueError, match=match):
        notebooks.load(path)


@pytest.mark.parametrize("number", ["1e999", "-1e999", "1.7976931348623159e308"])
def test_numeric_overflow_rejects_whole_selection_before_effects(consumer, monkeypatch, capsys, number):
    good = write(consumer.root / "notebooks/good.ipynb")
    bad = write(consumer.root / "notebooks/overflow.ipynb")
    document = json.loads(bad.read_bytes())
    document["metadata"]["custom"] = {"values": ["OVERFLOW"]}
    bad.write_text(json.dumps(document).replace('"OVERFLOW"', number))
    originals = {path: path.read_bytes() for path in (good, bad)}
    monkeypatch.setattr(notebooks, "_execute", lambda *args, **kwargs: pytest.fail("kernel started before numeric validation"))
    monkeypatch.setattr(files, "replace_many", lambda *args, **kwargs: pytest.fail("files published before numeric validation"))
    for action in ("check", "clear", "execute", "lint"):
        assert cli.main(["--root", str(consumer.root), "notebooks", action, str(good), str(bad)]) == 1
        captured = capsys.readouterr()
        assert str(bad) in captured.err and "non-finite JSON number" in captured.err
        assert captured.out == ""
    assert {path: path.read_bytes() for path in originals} == originals
    assert not (consumer.root / "target").exists()


@pytest.mark.parametrize("number", [1.7976931348623157e308, -1.7976931348623157e308, 5e-324, -0.0])
def test_finite_metadata_survives_notebook_loading(tmp_path, number):
    path = write(tmp_path / "finite.ipynb")
    document = json.loads(path.read_bytes())
    document["metadata"]["number"] = number
    path.write_text(json.dumps(document))
    assert notebooks.load(path).node.metadata.number == number
    assert json.loads(notebooks.serialize(notebooks.load(path).node))["metadata"]["number"] == number


@pytest.mark.parametrize("group", ["notebook", "analysis"])
def test_group_lookup_uses_configuration_without_notebook_dependencies(tmp_path, monkeypatch, capsys, group):
    settings = tmp_path / "settings.toml"
    settings.write_text(f'[notebooks]\ngroup="{group}"\n')
    monkeypatch.setattr(notebooks, "dependency", lambda *args: pytest.fail("group lookup imported notebook dependencies"))
    monkeypatch.setattr(notebooks, "runtime", lambda *args: pytest.fail("group lookup inspected or synchronized tools"))
    assert cli.main(["--config", str(settings), "notebooks", "group"]) == 0
    captured = capsys.readouterr()
    assert captured.out == f"{group}\n" and captured.err == ""


def test_clear_preserves_sources_ids_attachments_and_user_metadata(tmp_path):
    path = write(tmp_path / "input.ipynb")
    node = notebooks.load(path).node
    node.cells.append(nbformat.v4.new_markdown_cell("![figure](attachment:plot.png)", id="explain", attachments={"plot.png": {"image/png": "aGVsbG8="}}))
    node.cells[0].outputs = [nbformat.v4.new_output("stream", name="stdout", text="old output")]
    node.cells[0].execution_count = 7
    node.cells[0].metadata = {"execution": {"timing": "old"}, "tags": ["keep"]}
    node.metadata["widgets"] = {"old": "state"}
    node.metadata["custom"] = "preserve"
    path.write_bytes(notebooks.serialize(node))
    with pytest.raises(ValueError, match="must be cleared"):
        notebooks.check([path])
    notebooks.check([path], outputs="preserve")
    notebooks.clear([path])
    clean = notebooks.load(path).node
    assert clean.cells[0].id == "calculate" and clean.cells[0].source == "value = 42\n"
    assert clean.cells[0].outputs == [] and clean.cells[0].execution_count is None
    assert clean.cells[0].metadata == {"tags": ["keep"]}
    assert clean.cells[1] == node.cells[1]
    assert clean.metadata == {"custom": "preserve"}
    original = path.read_bytes()
    notebooks.clear([path])
    assert path.read_bytes() == original


def test_clear_validates_entire_selection_before_writing(tmp_path):
    good = write(tmp_path / "good.ipynb")
    node = notebooks.load(good).node
    node.cells[0].execution_count = 1
    good.write_bytes(notebooks.serialize(node))
    bad = tmp_path / "bad.ipynb"
    bad.write_text("{}")
    original = good.read_bytes()
    with pytest.raises(ValueError):
        notebooks.clear([good, bad])
    assert good.read_bytes() == original


def test_clear_rolls_back_partial_publication(tmp_path, monkeypatch):
    paths = [write(tmp_path / name) for name in ("one.ipynb", "two.ipynb")]
    for path in paths:
        node = notebooks.load(path).node
        node.cells[0].execution_count = 1
        path.write_bytes(notebooks.serialize(node))
    originals = [path.read_bytes() for path in paths]
    replace = files._replace_path

    def fail(source, destination):
        if destination == paths[1] and source.suffix == ".tmp":
            raise PermissionError("cannot publish second notebook")
        replace(source, destination)

    monkeypatch.setattr(files, "_replace_path", fail)
    with pytest.raises(PermissionError):
        notebooks.clear(paths)
    assert [path.read_bytes() for path in paths] == originals


def test_real_kernel_uses_project_interpreter_cwd_and_fresh_state(consumer):
    working = consumer.root / "work with spaces"
    working.mkdir()
    source = (
        "import json, os, sys\nfrom pathlib import Path\n"
        "print(json.dumps({'python': sys.executable, 'cwd': str(Path.cwd()), 'backend': os.environ['MPLBACKEND']}))\n"
        "assert 'old_variable' not in globals()\nold_variable = 1\n"
    )
    path = write(consumer.root / "notebooks" / "success.ipynb", source)
    node = notebooks.load(path).node
    node.metadata.kernelspec = {"name": "missing-kernel", "display_name": "Wrong interpreter", "language": "python"}
    path.write_bytes(notebooks.serialize(node))
    original = path.read_bytes()
    for _ in range(2):
        assert notebooks.execute(consumer, [path], cwd="work with spaces", timeout=30) == 0
    output = consumer.root / "target/notebooks/notebooks/success.ipynb"
    executed = notebooks.load(output).node
    observed = json.loads(executed.cells[0].outputs[0].text)
    assert Path(observed["python"]) == Path(sys.executable)
    assert Path(observed["cwd"]) == working and observed["backend"] == "Agg"
    report = json.loads(output.with_suffix(".report.json").read_text())
    assert report["status"] == "passed" and report["failed_cell"] is None
    assert report["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert report["timeout"] == 30 and report["packages"]["nbclient"]
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "source,metadata,error",
    [
        ("raise ValueError('synthetic failure')", {"tags": ["raises-exception"]}, "CellExecutionError"),
        ("import time\ntime.sleep(10)", {}, "CellTimeoutError"),
        ("raise ValueError('skip tag cannot conceal errors')", {"tags": ["skip-execution"]}, "CellExecutionError"),
        ("raise ValueError('sentinel tag cannot conceal errors')", {"tags": ["research-repo-tools-never-skip"]}, "CellExecutionError"),
    ],
)
def test_real_cell_failure_and_timeout_publish_failed_report_and_stop(consumer, source, metadata, error):
    path = write(consumer.root / "notebooks" / "failure.ipynb", source, cell_id="fail-here", metadata=metadata)
    next_path = write(consumer.root / "notebooks" / "not-run.ipynb")
    original = path.read_bytes()
    assert notebooks.execute(consumer, [path, next_path], timeout=1) == 1
    output = consumer.root / "target/notebooks/notebooks/failure.ipynb"
    report = json.loads(output.with_suffix(".report.json").read_bytes())
    assert report["status"] == "failed" and report["error"]["type"] == error
    assert report["failed_cell"] == {"index": 1, "id": "fail-here"}
    assert not (output.parent / "not-run.ipynb").exists()
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "change,match",
    [
        ({"timeout": 0}, "positive integer"),
        ({"timeout": -1}, "positive integer"),
        ({"cwd": "missing"}, "working directory"),
        ({"output_dir": "notebooks/output"}, "outside source directory"),
    ],
)
def test_execution_preflight_rejects_invalid_options_without_kernel(consumer, monkeypatch, change, match):
    path = write(consumer.root / "notebooks/input.ipynb")
    monkeypatch.setattr(notebooks, "_execute", lambda *args, **kwargs: pytest.fail("kernel started before preflight"))
    with pytest.raises(ValueError, match=match):
        notebooks.execute(consumer, [path], **change)
    assert not (consumer.root / "target").exists()


def test_execution_rejects_wrong_environment(consumer, monkeypatch):
    path = write(consumer.root / "notebooks/input.ipynb")
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT")
    with pytest.raises(ValueError, match="locked project environment"):
        notebooks.execute(consumer, [path])


def test_sync_uses_locked_groups_and_project_only_kernel(consumer, monkeypatch):
    calls = []
    for name in ("UV_PROJECT", "UV_WORKING_DIR", "UV_WORKING_DIRECTORY", "UV_ENV_FILE"):
        monkeypatch.setenv(name, "/unrelated-project")
    monkeypatch.setattr(notebooks, "run_safe_command", lambda command, args, **kwargs: calls.append((command, args, kwargs)))
    notebooks.sync(consumer)
    assert calls[0][1] == ["sync", "--locked", "--managed-python", "--group", "dev", "--group", "notebook"]
    assert calls[1][1] == [
        "-m",
        "ipykernel",
        "install",
        "--prefix",
        str(Path(sys.prefix).resolve()),
        "--name",
        "research-repo-tools",
        "--display-name",
        "Research project (locked Python)",
    ]
    assert calls[1][2]["cwd"] == consumer.root
    assert not {"UV_PROJECT", "UV_WORKING_DIR", "UV_WORKING_DIRECTORY", "UV_ENV_FILE"}.intersection(calls[0][2]["env"])
    assert calls[0][2]["env"]["UV_PROJECT_ENVIRONMENT"] == sys.prefix


def test_failed_sync_never_installs_kernel(consumer, monkeypatch):
    calls = []

    def failed(command, args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, [command, *args])

    monkeypatch.setattr(notebooks, "run_safe_command", failed)
    with pytest.raises(subprocess.CalledProcessError):
        notebooks.sync(consumer)
    assert len(calls) == 1


def test_missing_optional_dependency_has_cli_guidance(consumer, monkeypatch, capsys):
    path = write(consumer.root / "input.ipynb")

    def missing(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(notebooks, "import_module", missing)
    assert cli.main(["--root", str(consumer.root), "notebooks", "check", str(path)]) == 1
    assert "research-repo-tools[notebooks]" in capsys.readouterr().err


@pytest.mark.parametrize("field,value", [("timeout", True), ("timeout", 0), ("outputs", "ignore"), ("cwd", ""), ("group", "")])
def test_notebook_config_rejects_invalid_settings(tmp_path, field, value):
    with pytest.raises(ValueError, match="notebooks"):
        config.parse({"notebooks": {field: value}}, root=tmp_path)


def test_explicit_selection_required():
    with pytest.raises(SystemExit) as error:
        cli.main(["notebooks", "execute"])
    assert error.value.code == 2


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs elevated Windows privileges")
def test_artifacts_cannot_follow_symlink_parent_into_sources(consumer, monkeypatch):
    path = write(consumer.root / "notebooks/input.ipynb")
    output = consumer.root / "target/notebooks"
    output.mkdir(parents=True)
    (output / "notebooks").symlink_to(path.parent, target_is_directory=True)
    monkeypatch.setattr(notebooks, "_execute", lambda *args, **kwargs: pytest.fail("unsafe output started kernel"))
    with pytest.raises(ValueError, match="unsafe notebook artifact"):
        notebooks.execute(consumer, [path])
