"""Command-file values must be validated as a batch before any append."""

from pathlib import Path

import pytest

from research_repo_tools.ci import export_environment


def test_export_preserves_spaces_windows_paths_and_existing_crlf(tmp_path: Path) -> None:
    target = tmp_path / "environment"
    target.write_bytes(b"EXISTING=yes\r\n")
    export_environment(target, ["PATH", "CARGO_HOME"], environment={"PATH": "C:\\Program Files\\bin;D:\\tools", "CARGO_HOME": "C:\\Users\\A B"})
    assert target.read_bytes() == b"EXISTING=yes\r\nPATH=C:\\Program Files\\bin;D:\\tools\nCARGO_HOME=C:\\Users\\A B\n"


@pytest.mark.parametrize("value", [None, "", "a\nb", "a\rb", "a\r\nb", "a\0b"])
def test_export_rejects_entire_batch_before_writing(tmp_path: Path, value: str | None) -> None:
    target = tmp_path / "environment"
    target.write_bytes(b"original\n")
    values = {"GOOD": "valid"}
    if value is not None:
        values["BAD"] = value
    with pytest.raises(ValueError, match="BAD"):
        export_environment(target, ["GOOD", "BAD"], environment=values)
    assert target.read_bytes() == b"original\n"


@pytest.mark.parametrize("name", ["X\nINJECT", "X=Y", "X<<EOF", "9X", "GITHUB_TOKEN", "runner_temp", "NODE_OPTIONS"])
def test_export_rejects_invalid_and_reserved_names(tmp_path: Path, name: str) -> None:
    target = tmp_path / "environment"
    with pytest.raises(ValueError):
        export_environment(target, [name], environment={name: "value"})
    assert not target.exists()
