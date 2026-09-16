"""Coverage reporting uses unique source lines and explicit input paths."""

from pathlib import Path, PurePosixPath, PureWindowsPath
from xml.etree import ElementTree as ET

import pytest

from research_repo_tools import cli, coverage


def test_classes_for_one_file_merge_duplicate_source_lines() -> None:
    root = ET.fromstring(
        '<coverage><class filename="src/a.rs"><lines><line number="1" hits="0"/><line number="2" hits="1"/></lines></class><class filename="src/a.rs"><lines><line number="2" hits="3"/><line number="3" hits="0"/></lines></class></coverage>'
    )
    (entry,) = coverage.coverage_entries(root)
    assert entry.path == Path("src/a.rs")
    assert entry.coverable == 3 and entry.covered == 1
    assert entry.coverage == pytest.approx(100 / 3)


@pytest.mark.parametrize("attributes", ['number="0" hits="1"', 'number="a" hits="1"', 'number="2" hits="-1"'])
def test_invalid_coverage_counts_are_rejected(attributes: str) -> None:
    root = ET.fromstring(f'<coverage><class filename="src/a.rs"><lines><line {attributes}/></lines></class></coverage>')
    with pytest.raises(ValueError, match="src/a.rs"):
        list(coverage.coverage_entries(root))


@pytest.mark.parametrize("consumer_root", [PurePosixPath("/consumer"), PureWindowsPath("C:/consumer")], ids=["posix", "windows"])
def test_formatted_report_uses_forward_slashes_on_both_platforms(consumer_root, monkeypatch: pytest.MonkeyPatch) -> None:
    # Model native path parsing without depending on the host filesystem.
    monkeypatch.setattr(coverage, "Path", type(consumer_root))
    report = ET.fromstring(f'<coverage><class filename="{consumer_root.as_posix()}/src/a.rs"><lines><line number="1" hits="1"/></lines></class></coverage>')
    (entry,) = coverage.coverage_entries(report)
    assert entry.format(relative_to=consumer_root) == "100.00%  src/a.rs"


def test_cli_uses_consumer_root_and_deterministic_ties(tmp_path: Path, capsys) -> None:
    report = tmp_path / "coverage/cobertura.xml"
    report.parent.mkdir()
    report.write_text(
        '<coverage><class filename="src/z.rs"><lines><line number="1" hits="1"/></lines></class><class filename="src/a.rs"><lines><line number="1" hits="1"/></lines></class></coverage>'
    )
    assert cli.main(["--root", str(tmp_path), "coverage", "report", "--prefix", "src", "--limit", "1"]) == 0
    assert capsys.readouterr().out == "100.00%  src/a.rs\n"
    assert cli.main(["--root", str(tmp_path), "coverage", "report", "--limit", "-1"]) == 1
