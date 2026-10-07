"""Just formatter input must survive native Git newline conversion."""

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("name", ["justfile", "src/research_repo_tools/templates/justfile"])
def test_justfile_policy_keeps_crlf_out_of_formatter_input(name: str) -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / name).read_bytes()
    assert b"\r" not in source
    crlf = source.replace(b"\n", b"\r\n")
    assert crlf.count(b"\r\n") == source.count(b"\n")

    def git(*arguments: str, payload: bytes | None = None) -> bytes:
        # Read-only: hash-object never receives -w, and per-command settings
        # leave user configuration alone. Disable automatic newline conversion
        # so it cannot mask a missing explicit file rule.
        return subprocess.run(
            ["git", "--no-pager", "-c", "core.autocrlf=false", *arguments],
            cwd=root,
            input=payload,
            capture_output=True,
            check=True,
        ).stdout

    attributes = git("check-attr", "text", "eol", "--", name).decode().splitlines()
    assert f"{name}: text: set" in attributes
    assert f"{name}: eol: lf" in attributes
    expected = git("hash-object", "--no-filters", "--stdin", payload=source)
    assert git("hash-object", "--no-filters", "--stdin", payload=crlf) != expected
    assert git("hash-object", f"--path={name}", "--stdin", payload=crlf) == expected
