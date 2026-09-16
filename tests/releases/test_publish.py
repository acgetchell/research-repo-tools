"""Publication preflight against small synthetic Python projects, without Git."""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_release.py"


def project(root: Path, *, name: str = "research-repo-tools", locked: str = "0.1.0", notes: str = "- Initial release.") -> None:
    (root / "pyproject.toml").write_text(f'[project]\nname = "{name}"\nversion = "0.1.0"\n', encoding="utf-8")
    (root / "uv.lock").write_text(f'[[package]]\nname = "{name}"\nversion = "{locked}"\nsource = {{ editable = "." }}\n', encoding="utf-8")
    (root / "CHANGELOG.md").write_text(f"# Changelog\n\n## [0.1.0] - 2026-09-16\n\n{notes}\n", encoding="utf-8")


def preflight(root: Path, tag: str = "v0.1.0") -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), tag, "--root", str(root)], capture_output=True, text=True, check=False, timeout=30)


def test_accepts_matching_stable_release_without_changing_files(tmp_path: Path) -> None:
    project(tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    result = preflight(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "v0.1.0" in result.stdout
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("tag", ["0.1.0", "v0.2.0", "v0.1.0-rc.1", "v0.1.0+build.1", "v00.1.0", "v0.1.0\n"])
def test_rejects_non_release_tags(tmp_path: Path, tag: str) -> None:
    project(tmp_path)
    result = preflight(tmp_path, tag)
    assert result.returncode == 1
    assert "Release preflight failed:" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    ("options", "diagnostic"),
    [
        ({"name": "another-package"}, "project.name"),
        ({"locked": "0.0.9"}, "uv.lock"),
        ({"notes": ""}, "empty release notes"),
    ],
)
def test_rejects_incomplete_release(tmp_path: Path, options: dict[str, str], diagnostic: str) -> None:
    project(tmp_path, **options)
    result = preflight(tmp_path)
    assert result.returncode == 1
    assert diagnostic in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("heading", ["## [Unreleased]", "## [0.1.0]", "## [0.1.0] - 2026-02-30"])
def test_requires_dated_release_notes(tmp_path: Path, heading: str) -> None:
    project(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text(f"# Changelog\n\n{heading}\n\n- Initial release.\n", encoding="utf-8")
    result = preflight(tmp_path)
    assert result.returncode == 1
    assert "changelog" in result.stderr.lower()
    assert "Traceback" not in result.stderr
