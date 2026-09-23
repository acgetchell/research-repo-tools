"""Detection and false-positive evidence for the portable newline guard."""

import importlib.util
import io
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check_newlines.py"
CASES = re.split(r"^# (reject|accept): (.+)\n", (Path(__file__).parent / "fixtures/newlines.txt").read_text(encoding="utf-8"), flags=re.MULTILINE)


@pytest.fixture
def checker():
    spec = importlib.util.spec_from_file_location("check_newlines", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("expectation,label,source", list(zip(CASES[1::3], CASES[2::3], CASES[3::3], strict=True)), ids=CASES[2::3])
def test_writer_policy(checker, expectation, label, source):
    findings = checker.violations(source, "fixture.py")
    if expectation == "reject":
        assert len(findings) == 1, (label, findings)
        assert findings[0].startswith("fixture.py:")
        assert "implicit-newline:" in findings[0]
    else:
        assert findings == [], (label, findings)


def test_removed_newline_policy_fails_cli_with_location(tmp_path):
    source = tmp_path / "fixture.py"
    safe = 'from pathlib import Path\nPath("output").write_text("first\\nsecond\\r\\n", newline="\\n")\n'
    source.write_text(safe, encoding="utf-8", newline="\n")
    command = [sys.executable, str(CHECKER), str(tmp_path)]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 0 and result.stderr == ""
    source.write_text(safe.replace(', newline="\\n"', ""), encoding="utf-8", newline="\n")
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 1
    assert f"{source}:2:1: implicit-newline:" in result.stderr
    assert not (tmp_path / "output").exists(), "the guard must parse source without executing it"


def test_embedded_child_reports_outer_and_inner_locations(checker):
    source = CASES[CASES.index("adjacent child literals") + 1]
    assert checker.violations(source, "parent.py")[0].startswith("parent.py:2:literal:2:6: implicit-newline:")


def test_invalid_source_and_missing_paths_fail_closed(checker, tmp_path, monkeypatch, capsys):
    source = tmp_path / "broken.py"
    source.write_text("def broken(\n", encoding="utf-8", newline="\n")
    missing = tmp_path / "missing.py"
    monkeypatch.setattr(sys, "argv", [str(CHECKER), str(source), str(missing)])
    assert checker.main() == 1
    errors = capsys.readouterr().err
    assert f"{source}:1: invalid Python:" in errors
    assert f"{missing}: input does not exist" in errors


@pytest.mark.parametrize("newline", ["\n", ""])
def test_explicit_policy_preserves_mixed_bytes_on_native_path_and_windows_model(tmp_path, newline):
    text = "first\nsecond\r\n分析\n"
    expected = text.encode("utf-8")
    path = tmp_path / "fixture.txt"
    path.write_text(text, encoding="utf-8", newline=newline)
    assert path.read_bytes() == expected

    def windows_write(policy):
        buffer = io.BytesIO()
        with io.TextIOWrapper(buffer, encoding="utf-8", newline="\r\n" if policy is None else policy) as stream:
            stream.write(text)
            stream.flush()
            return buffer.getvalue()

    assert windows_write(newline) == expected
    translated = windows_write(None)
    assert translated != expected and b"\r\r\n" in translated


def test_final_gate_includes_newline_guard():
    result = subprocess.run(["just", "--dry-run", "ci"], cwd=ROOT, capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 0, result.stderr
    commands = result.stderr.splitlines()
    assert "uv run --locked python scripts/check_newlines.py" in commands
    lint_index = next(index for index, command in enumerate(commands) if "files run" in command and " -- ruff check " in command)
    assert commands.index("uv run --locked python scripts/check_newlines.py") < lint_index
