"""The standalone bootstrap must run before importing the target release."""

import ast
import json
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_repo_tools.changelog import template

SCRIPT = Path(__file__).resolve().parents[2] / "src/research_repo_tools/templates/python-bootstrap.py"


def test_bootstrap_supports_old_python_syntax_and_packaged_recipes():
    source = template("python-bootstrap.py")
    ast.parse(source, feature_version=(3, 8))
    recipes = template("justfile")
    for mode in ("dry-run", "apply"):
        assert f'uv run --no-config --no-project --isolated --managed-python --script python-bootstrap.py "$1" --{mode}' in recipes


@pytest.mark.parametrize("mode", ["--dry-run", "--apply"])
def test_bootstrap_uses_exact_release_metadata_before_installed_authority(tmp_path, monkeypatch, mode):
    metadata = tmp_path / "release.json"
    metadata.write_text(
        json.dumps({"info": {"name": "research-repo-tools", "version": "0.9.1", "requires_python": ">=3.15.2,<3.17"}}), encoding="utf-8", newline="\n"
    )
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.chdir(tmp_path)
    main = runpy.run_path(str(SCRIPT))["main"]
    assert main(["0.9.1", "--metadata-file", str(metadata), mode]) == 7
    arguments, options = calls[0]
    assert arguments[arguments.index("--python") + 1] == ">=3.15.2,<3.17"
    assert arguments[arguments.index("--from") + 1] == "research-repo-tools==0.9.1"
    assert arguments[-4:] == [str(tmp_path), "toolchain", "adopt", mode]
    assert options["check"] is False
    assert list(tmp_path.iterdir()) == [metadata]


@pytest.mark.parametrize(
    ("version", "info"),
    [
        (">=0.9.1", {"name": "research-repo-tools", "version": ">=0.9.1", "requires_python": ">=3.15"}),
        ("0.9.1", {"name": "research-repo-tools", "version": "0.9.2", "requires_python": ">=3.15"}),
        ("0.9.1", {"name": "other-package", "version": "0.9.1", "requires_python": ">=3.15"}),
        ("0.9.1", {"name": "research-repo-tools", "version": "0.9.1", "requires_python": None}),
    ],
)
def test_bootstrap_rejects_ambiguous_target_before_startup(tmp_path, monkeypatch, capsys, version, info):
    metadata = tmp_path / "release.json"
    metadata.write_text(json.dumps({"info": info}), encoding="utf-8", newline="\n")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("invalid release must not start uvx"))
    main = runpy.run_path(str(SCRIPT))["main"]
    assert main([version, "--metadata-file", str(metadata), "--dry-run"]) == 1
    assert "Python adoption bootstrap:" in capsys.readouterr().err


def test_bootstrap_reads_exact_pypi_release(tmp_path, monkeypatch):
    import io
    import urllib.request

    urls = []

    def open_release(url, **kwargs):
        urls.append((url, kwargs))
        return io.BytesIO(json.dumps({"info": {"name": "research-repo-tools", "version": "0.9.1", "requires_python": ">=3.15"}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", open_release)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.chdir(tmp_path)
    main = runpy.run_path(str(SCRIPT))["main"]
    assert main(["0.9.1", "--dry-run"]) == 0
    assert urls == [("https://pypi.org/pypi/research-repo-tools/0.9.1/json", {"timeout": 30})]
    assert not list(tmp_path.iterdir())
