"""Literal notebook installation policy without executing user cells."""

import nbformat
import pytest

from research_repo_tools import config, notebook_lint, notebooks
from research_repo_tools.notebook_policy import install_diagnostics


def notebook(tmp_path, source):
    path = tmp_path / "policy.ipynb"
    node = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(source, id="environment-setup")])
    path.write_bytes(nbformat.writes(node).encode("utf-8"))
    return notebooks.load(path)


@pytest.mark.parametrize(
    "source",
    [
        "%pip install numpy",
        "!pip install numpy",
        "python -m pip install numpy",
        "!uv pip install numpy",
        "%conda install numpy",
        "!uv add numpy",
        "!uv sync",
        "!conda env update --file env.yml",
        "%%bash\npython -m pip install numpy",
        "%%script sh\nuv pip install numpy",
        "%%time\n%pip install numpy",
        "packages = !pip install numpy",
        "!echo before && pip install numpy",
        "import subprocess\nsubprocess.run(['python', '-m', 'pip', 'install', 'numpy'])",
        "import subprocess\nsubprocess.run(args=['python', '-m', 'pip', 'install', 'numpy'])",
        "import subprocess\nsubprocess.Popen(args=('uv', 'pip', 'install', 'numpy'))",
        "import os\nos.system('uv pip install numpy')",
    ],
)
def test_literal_installs_have_stable_source_diagnostics_and_do_not_change_bytes(tmp_path, source):
    item = notebook(tmp_path, source)
    diagnostics = install_diagnostics(item)
    assert len(diagnostics) == 1
    assert "cell 1 (environment-setup)" in diagnostics[0].render(item)
    assert diagnostics[0].line == len(source.splitlines())
    assert item.path.read_bytes() == item.original


@pytest.mark.parametrize(
    "source",
    [
        "value = 1",
        "%pip list",
        "!pip show install",
        "!uv lock",
        "# !pip install numpy",
        'message = "pip install numpy"',
        'message = """example\n%pip install numpy\n!uv pip install numpy\n"""',
        'message = f"""example\n%pip install numpy\n"""',
        "import subprocess\nsubprocess.run(['echo', 'pip', 'install'])",
        "%%bash\necho pip install numpy",
    ],
)
def test_noninstalling_code_and_multiline_strings_are_not_flagged(tmp_path, source):
    assert install_diagnostics(notebook(tmp_path, source)) == []


def test_policy_is_opt_in_and_blocks_lint(tmp_path, monkeypatch, capsys):
    item = notebook(tmp_path, "%pip install numpy")
    monkeypatch.setattr(notebook_lint, "_run", lambda *args: [])
    assert notebook_lint.lint(config.parse({}, root=tmp_path), [item.path]) == 0
    settings = config.parse({"notebooks": {"prohibit-installs": True}}, root=tmp_path)
    assert notebook_lint.lint(settings, [item.path]) == 1
    assert "dependency-install" in capsys.readouterr().err
    assert item.path.read_bytes() == item.original


def test_policy_rejects_nonboolean_configuration(tmp_path):
    with pytest.raises(ValueError, match="must be a boolean"):
        config.parse({"notebooks": {"prohibit-installs": "true"}}, root=tmp_path)
