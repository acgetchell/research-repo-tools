"""Reject stale or incomplete publication inputs before installation begins."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("inventory", ["empty", "wheel-only", "sdist-only", "stale-wheel", "stale-sdist"])
def test_install_check_rejects_unexpected_distribution_set(tmp_path: Path, inventory: str) -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    wheel = f"research_repo_tools-{version}-py3-none-any.whl"
    sdist = f"research_repo_tools-{version}.tar.gz"
    names = {
        "empty": [],
        "wheel-only": [wheel],
        "sdist-only": [sdist],
        "stale-wheel": [wheel, sdist, "research_repo_tools-0.0.0-py3-none-any.whl"],
        "stale-sdist": [wheel, sdist, "research_repo_tools-0.0.0.tar.gz"],
    }[inventory]
    for name in names:
        (tmp_path / name).touch()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_install.py"), "--dist", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0
    assert f"Expected only {wheel} and {sdist}" in result.stderr
    assert "BadZipFile" not in result.stderr
    assert "PASS:" not in result.stdout
