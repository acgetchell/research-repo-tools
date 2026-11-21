"""Dependency-audit failures retain the export tool's captured diagnostics."""

import runpy
import subprocess
from pathlib import Path

import pytest

from research_repo_tools import process


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(
            2, ["uv", "export"], output=b"partial export", stderr=b"lockfile is stale https://example.invalid/index?token=fixture-private"
        ),
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
    assert error.stderr.decode().split(" https:")[0] in output.err
    assert "fixture-private" not in output.err


@pytest.mark.parametrize("requirements", ["# generated\n", "optional-fixture==1.0 --hash=sha256:" + "a" * 64 + "\n"])
def test_audit_handoff_requires_nonempty_locked_export_including_extras(requirements, monkeypatch, capsys):
    """Pass the actual exported inventory to the auditor and preserve its failure status."""
    audits = []

    def run(program, args, **kwargs):
        if program == "uv":
            assert {"--locked", "--all-extras", "--all-groups", "--no-emit-project"} <= set(args)
            Path(args[args.index("--output-file") + 1]).write_bytes(requirements.encode())
            return subprocess.CompletedProcess(args, 0)
        assert "--require-hashes" in args
        audits.append(Path(args[args.index("--requirement") + 1]).read_bytes())
        return subprocess.CompletedProcess(args, 9)

    monkeypatch.setattr(process, "run_safe_command", run)
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/audit_dependencies.py"), run_name="__main__")
    if requirements.startswith("#"):
        assert caught.value.code == 1 and not audits
        assert "no third-party requirements" in capsys.readouterr().err
    else:
        assert caught.value.code == 9 and audits == [requirements.encode()]
