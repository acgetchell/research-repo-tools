"""Dependency-audit failures retain the export tool's captured diagnostics."""

import runpy
import subprocess
from pathlib import Path

import pytest

from research_repo_tools import process


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(2, ["uv", "export"], output=b"partial export", stderr=b"lockfile is stale"),
        subprocess.TimeoutExpired(["uv", "export"], 120, output=b"partial export", stderr=b"still resolving"),
    ],
)
def test_export_failure_preserves_diagnostics(error, monkeypatch, capsys) -> None:
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(process, "run_safe_command", fail)
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/audit_dependencies.py"), run_name="__main__")
    assert caught.value.code == 1
    output = capsys.readouterr()
    assert not output.out
    assert "partial export" in output.err
    assert error.stderr.decode() in output.err
