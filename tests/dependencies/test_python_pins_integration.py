"""Exercise dependency updates with real uv and an offline wheel registry."""

import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

from research_repo_tools import cli


def wheel(directory: Path, version: str, requires_python: str) -> None:
    metadata = f"Metadata-Version: 2.3\nName: fixture-tool\nVersion: {version}\nRequires-Python: {requires_python}\n"
    info = f"fixture_tool-{version}.dist-info"
    with zipfile.ZipFile(directory / f"fixture_tool-{version}-py3-none-any.whl", "w") as archive:
        archive.writestr(f"{info}/METADATA", metadata)
        archive.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(f"{info}/RECORD", "")


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "wheels"
    directory.mkdir()
    for name in ("UV_PROJECT", "UV_WORKING_DIR", "UV_WORKING_DIRECTORY", "UV_CONFIG_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("UV_FIND_LINKS", str(directory))
    monkeypatch.setenv("UV_NO_INDEX", "1")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    wheel(directory, "1.0.0", ">=3.14")
    return directory


def consumer(root: Path, requires_python: str = ">=3.14", retained: str = "") -> Path:
    root.mkdir()
    manifest = root / "pyproject.toml"
    manifest.write_text(
        f'[project]\nname="{root.name}"\nversion="0.1.0"\nrequires-python="{requires_python}"\n[dependency-groups]\ndev=["fixture-tool==1.0.0"{retained}]\n',
        encoding="utf-8",
    )
    return manifest


@pytest.mark.parametrize("override", ["UV_PROJECT", "UV_WORKING_DIR", "UV_WORKING_DIRECTORY"])
def test_cli_updates_only_selected_project(registry: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: str) -> None:
    wheel(registry, "2.0.0", ">=3.14")
    selected = consumer(tmp_path / "selected")
    other = consumer(tmp_path / "other")
    other_lock = other.with_name("uv.lock")
    other_lock.write_bytes(b"unrelated lock must remain untouched\n")
    before = other.read_bytes(), other_lock.read_bytes()
    monkeypatch.setenv(override, str(other.parent))

    assert cli.main(["--root", str(selected.parent), "deps", "update-python"]) == 0
    assert tomllib.loads(selected.read_text())["dependency-groups"]["dev"] == ["fixture-tool==2.0.0"]
    assert (other.read_bytes(), other_lock.read_bytes()) == before
    assert selected.with_name("uv.lock").is_file()
    assert not selected.parent.joinpath(".venv").exists()


@pytest.mark.parametrize(
    "requires_python,retained",
    [
        (">=3.14.1", ""),
        (">=3.14,<3.15", ", \"fixture-tool<2; python_version >= '3.15'\""),
        (">=3.14,!=3.14.1", ""),
    ],
)
def test_cli_preserves_full_python_range(registry: Path, tmp_path: Path, requires_python: str, retained: str) -> None:
    wheel(registry, "2.0.0", ">=3.14.1" if requires_python == ">=3.14.1" else ">=3.14")
    manifest = consumer(tmp_path / "consumer", requires_python, retained)

    assert cli.main(["--root", str(manifest.parent), "deps", "update-python"]) == 0
    document = tomllib.loads(manifest.read_text())
    assert document["project"]["requires-python"] == requires_python
    assert document["dependency-groups"]["dev"][0] == "fixture-tool==2.0.0"
    assert manifest.with_name("uv.lock").is_file()


def test_resolution_uses_consumer_uv_settings(registry: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wheel(registry, "2.0.0", ">=3.14")
    manifest = consumer(tmp_path / "consumer")
    manifest.write_text(manifest.read_text() + '[tool.uv]\nno-index=true\nfind-links=["../wheels"]\n', encoding="utf-8")
    monkeypatch.delenv("UV_FIND_LINKS")
    monkeypatch.delenv("UV_NO_INDEX")

    assert cli.main(["--root", str(manifest.parent), "deps", "update-python"]) == 0
    document = tomllib.loads(manifest.read_text())
    assert document["dependency-groups"]["dev"] == ["fixture-tool==2.0.0"]
    assert document["tool"]["uv"] == {"no-index": True, "find-links": ["../wheels"]}
