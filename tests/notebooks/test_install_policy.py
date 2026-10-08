"""Literal notebook installation policy without executing user cells."""

import warnings

import nbformat
import pytest

from research_repo_tools import config, notebook_lint, notebook_policy, notebooks
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
        "!sudo env PIP_INDEX_URL=https://example.invalid/simple pip install numpy",
        "!env MODE=test sudo env PIP_INDEX_URL=https://example.invalid/simple pip install numpy",
        "!PIP.EXE install numpy",
        r"!C:\Tools\Python\Scripts\pip.exe install numpy",
        r'!"C:\Program Files\Python\Scripts\PIP.EXE" install numpy',
        "%matplotlib inline\nimport subprocess\nsubprocess.run(['pip', 'install', 'numpy'])",
        "%%time\nimport subprocess\nsubprocess.run(['pip', 'install', 'numpy'])",
        "paths = !echo hello\nimport os\nos.system('pip install numpy')",
        "if True:\n    %matplotlib inline\n    subprocess.run(['pip', 'install', 'numpy'])",
        "%matplotlib inline\nvalue = (3\n% 2)\nsubprocess.run(['pip', 'install', 'numpy'])",
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
        "import subprocess\nsubprocess.run(['echo', ';', 'pip', 'install', 'numpy'], timeout=10)",
        "import subprocess\nsubprocess.run(('echo', '&&', 'pip', 'install', 'numpy'), timeout=10)",
        "import subprocess\nsubprocess.run(['echo', '||', 'uv', 'sync'], timeout=10)",
        "import subprocess\nsubprocess.run(['echo', '|', 'conda', 'install', 'numpy'], timeout=10)",
        "import subprocess\nsubprocess.run(['echo', '&', 'pip', 'install', 'numpy'], timeout=10)",
        "%%bash\necho pip install numpy",
        "%%bash\nsubprocess.run(['pip', 'install', 'numpy'])",
        "%%javascript\nsubprocess.run(['pip', 'install', 'numpy'])",
        'message = """\n%matplotlib inline\nsubprocess.run(["pip", "install", "numpy"])\n"""',
        r'!"C:\Program Files\echo.exe" pip install numpy',
        "!sudo env MODE=test pip show install",
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


def test_magic_cell_preserves_multiline_python_call_line(tmp_path):
    item = notebook(tmp_path, "%matplotlib inline\r\n\r\nsubprocess.run(\r\n    args=['pip', 'install', 'numpy'],\r\n)\r\n")
    diagnostics = install_diagnostics(item)
    assert [diagnostic.line for diagnostic in diagnostics] == [3]
    assert item.path.read_bytes() == item.original


@pytest.mark.parametrize("separator", ["\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_string_separators_preserve_physical_lines_and_token_offsets(tmp_path, separator, newline):
    source = f'message = "before{separator}after"{newline}example = "!pip install ignored"{newline}!pip install numpy'
    item = notebook(tmp_path, source)
    assert [diagnostic.line for diagnostic in install_diagnostics(item)] == [3]
    assert item.path.read_bytes() == item.original
    # A later multiline string must stay masked even after a separator changed
    # the apparent line count under str.splitlines().
    source = f'message = "before{separator}after"{newline}example = """{newline}!pip install ignored{newline}"""'
    item = notebook(tmp_path, source)
    assert install_diagnostics(item) == []
    assert item.path.read_bytes() == item.original


def test_policy_rejects_nonboolean_configuration(tmp_path):
    with pytest.raises(ValueError, match="must be a boolean"):
        config.parse({"notebooks": {"prohibit-installs": "true"}}, root=tmp_path)


@pytest.mark.parametrize("operator", [";", "&&", "||", "|", "&"])
@pytest.mark.parametrize("quote", ['"', "'"])
def test_quoted_shell_operators_remain_literal_arguments(tmp_path, operator, quote):
    for prefix in ("!", "%%bash\n"):
        item = notebook(tmp_path, f"{prefix}echo {quote}{operator}{quote} pip install numpy")
        assert install_diagnostics(item) == []
        assert item.path.read_bytes() == item.original


@pytest.mark.parametrize(
    "source",
    [r"!echo \; pip install numpy", "!echo safe # comment; pip install numpy", "!echo ok;# comment; pip install numpy"],
)
def test_escaped_operators_and_comments_do_not_create_commands(tmp_path, source):
    assert install_diagnostics(notebook(tmp_path, source)) == []


def test_shell_comment_does_not_hide_the_next_line(tmp_path):
    source = "import os\nos.system('echo ok # harmless; pip install ignored\\npip install numpy')"
    assert [item.line for item in install_diagnostics(notebook(tmp_path, source))] == [2]


def test_windows_quoted_caret_does_not_escape_the_closing_quote(tmp_path, monkeypatch):
    monkeypatch.setattr(notebook_policy, "_WINDOWS", True)
    source = "import os\nos.system('echo \"^\" & pip install numpy')"
    assert [item.line for item in install_diagnostics(notebook(tmp_path, source))] == [2]


@pytest.mark.parametrize("windows", [False, True])
def test_system_escapes_use_the_host_shell_but_bash_cells_stay_posix(tmp_path, monkeypatch, windows):
    monkeypatch.setattr(notebook_policy, "_WINDOWS", windows)
    command = "echo # harmless & pip install numpy"
    assert bool(install_diagnostics(notebook(tmp_path, "!" + command))) is windows
    assert install_diagnostics(notebook(tmp_path, "%%bash\n" + command)) == []


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("uses_shell", [False, True])
def test_subprocess_strings_respect_shell_mode(tmp_path, monkeypatch, windows, uses_shell):
    monkeypatch.setattr(notebook_policy, "_WINDOWS", windows)
    for command, expected in (
        ('python -c "print(1)" & pip install numpy', uses_shell),
        ("pip install numpy", uses_shell or windows),
    ):
        item = notebook(tmp_path, f"import subprocess\nsubprocess.run({command!r}, shell={uses_shell!r})")
        assert bool(install_diagnostics(item)) is expected
        assert item.path.read_bytes() == item.original


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("function", ["Popen", "run", "call", "check_call", "check_output"])
@pytest.mark.parametrize("shell", [True, False, 1, 0, None, "yes", ""])
def test_literal_shell_values_and_forwarded_positional_arguments(tmp_path, monkeypatch, windows, function, shell):
    monkeypatch.setattr(notebook_policy, "_WINDOWS", windows)
    command = "echo safe & pip install numpy"
    for arguments in (f"{command!r}, shell={shell!r}", f"{command!r}, -1, None, None, None, None, None, True, {shell!r}"):
        item = notebook(tmp_path, f"import subprocess\nsubprocess.{function}({arguments})")
        assert bool(install_diagnostics(item)) is bool(shell)
        assert item.path.read_bytes() == item.original


def test_dynamic_shell_selection_and_starred_arguments_are_not_evaluated(tmp_path):
    source = 'import subprocess\nsubprocess.Popen("echo safe & pip install numpy", shell=choose_shell())'
    assert install_diagnostics(notebook(tmp_path, source)) == []
    source = 'subprocess.Popen("echo safe & pip install numpy", *options, None, None, None, None, None, True, True)'
    assert install_diagnostics(notebook(tmp_path, source)) == []


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("operator", ["&", "&&", "||", "|"])
def test_shell_argument_vectors_follow_native_command_serialization(tmp_path, monkeypatch, windows, operator):
    monkeypatch.setattr(notebook_policy, "_WINDOWS", windows)
    item = notebook(tmp_path, f"import subprocess\nsubprocess.run(['echo', {operator!r}, 'pip', 'install', 'numpy'], shell=True)")
    diagnostics = install_diagnostics(item)
    assert [item.line for item in diagnostics] == ([2] if windows else [])
    assert item.path.read_bytes() == item.original
    # The first POSIX vector member is itself the shell program; cmd receives
    # this one argument quoted, so it is not the same command on Windows.
    if not windows:
        item = notebook(tmp_path, "subprocess.run(['pip install numpy', 'ignored'], shell=True)")
        assert [item.line for item in install_diagnostics(item)] == [1]


@pytest.mark.parametrize("action", ["always", "error"])
def test_parser_warnings_do_not_hide_later_literal_installs(tmp_path, action):
    source = 'import subprocess\npattern = "\\q"\nsubprocess.run(["pip", "install", "numpy"])'
    item = notebook(tmp_path, source)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter(action, SyntaxWarning)
        filters = list(warnings.filters)
        assert [item.line for item in install_diagnostics(item)] == [3]
        assert warnings.filters == filters
    assert caught == []
    assert item.path.read_bytes() == item.original


def test_real_syntax_errors_are_left_to_native_syntax_checks(tmp_path):
    assert install_diagnostics(notebook(tmp_path, "value = (\nsubprocess.run(['pip', 'install', 'numpy'])")) == []
