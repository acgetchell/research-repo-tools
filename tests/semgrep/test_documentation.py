"""Rust fence extraction retains source lines and delegates rule assertions."""

from typing import Any

import pytest

from research_repo_tools.semgrep_docs import rust_blocks


@pytest.mark.parametrize(
    "suffix,source",
    [
        (".md", "# Guide\n\n```rust,no_run\n# let value = 1;\nassert!(value > 0);\n```\n"),
        (".rs", "//! Guide\n//!\n//! ```no_run\n//! # let value = 1;\n//! assert!(value > 0);\n//! ```\n"),
        (".rs", "/** Guide\n *\n * ```rust\n * # let value = 1;\n * assert!(value > 0);\n * ```\n */\n"),
    ],
)
def test_rust_fences_keep_line_numbers(tmp_path, suffix, source):
    path = tmp_path / ("source" + suffix)
    path.write_text(source, encoding="utf-8", newline="\n")
    assert rust_blocks(path) == ("\n\n\nlet value = 1;\nassert!(value > 0);\n",)


def test_other_languages_excluded_and_unclosed_rust_blocks(tmp_path):
    path = tmp_path / "guide.md"
    path.write_bytes(b"```python\nraise Exception()\n```\n")
    assert rust_blocks(path) == ()
    path.write_bytes(b"```rust\nlet x = 1;\n")
    with pytest.raises(ValueError, match="unclosed"):
        rust_blocks(path)


def test_windows_snippet_paths_map_native_json_and_both_sarif_uri_forms():
    from pathlib import PureWindowsPath

    from research_repo_tools.semgrep_scan import _source_mapping

    mapping = _source_mapping(PureWindowsPath("D:/temporary inputs/block.rs"), PureWindowsPath("C:/repository/tests/source.rs"))
    assert mapping == {
        "D:\\temporary inputs\\block.rs": "C:\\repository\\tests\\source.rs",
        "D:/temporary inputs/block.rs": "C:/repository/tests/source.rs",
        "D%3A/temporary%20inputs/block.rs": "C%3A/repository/tests/source.rs",
        "file:///D:/temporary%20inputs/block.rs": "file:///C:/repository/tests/source.rs",
    }


def test_documentation_fixture_assertions_use_shared_checker(tmp_path, monkeypatch):
    from research_repo_tools import config, semgrep, semgrep_scan

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    source = fixtures / "bad.md"
    source.write_bytes(b"```rust\n// ruleid: local.rule\nunsafe { operation() }\n```\n")
    settings = config.Config(root=tmp_path, semgrep=config.SemgrepSettings(config="rules.yml", fixtures="fixtures"))
    original = source.read_bytes()

    def check(adapted, *, rust_docs):
        assert adapted is settings
        assert rust_docs is True
        raise ValueError("local.rule: expected finding not reported")

    monkeypatch.setattr(semgrep, "check", check)
    with pytest.raises(ValueError, match="expected finding"):
        semgrep_scan.check_documentation_fixtures(settings)
    assert source.read_bytes() == original


def test_optional_native_metavariable_positions_map_to_original_bytes(tmp_path):
    from research_repo_tools.semgrep_docs import documentation_blocks
    from research_repo_tools.semgrep_scan import ScanInput, _project_positions

    source = tmp_path / "source.rs"
    original = '//! docs\r\n//! ```rust\r\n//! println!("café");\r\n//! ```\r\n'.encode("utf-8")
    source.write_bytes(original)
    block = documentation_blocks(source)[0]
    physical = tmp_path / "input.rs"
    data: dict[str, Any] = {
        "results": [
            {
                "path": str(physical),
                "start": {"line": 1, "col": 1, "offset": 0},
                "end": {"line": 1, "col": 9, "offset": 8},
                "extra": {"metavars": {"$MESSAGE": {"start": {"line": 1, "col": 10, "offset": 9}, "end": {"line": 1, "col": 17, "offset": 16}}}},
            }
        ]
    }
    # Some native report versions include metavariables; the pinned scanner's
    # default JSON omits them. Keep the optional projection contract explicit.
    _project_positions(data, {"runs": []}, {physical: ScanInput(physical, source, "source.rs", block)}, tmp_path)
    finding: dict[str, Any] = data["results"][0]
    assert finding["start"] == {"line": 3, "col": 5, "offset": original.index(b"println!")}
    argument = finding["extra"]["metavars"]["$MESSAGE"]
    assert argument["start"]["line"] == argument["end"]["line"] == 3
    assert original[argument["start"]["offset"] : argument["end"]["offset"]] == '"café"'.encode("utf-8")


def test_empty_and_hidden_blank_rustdoc_lines_map_after_removed_markers(tmp_path):
    from research_repo_tools.semgrep_docs import documentation_blocks

    source = tmp_path / "source.rs"
    source.write_bytes(b"//! ```rust\n//! \n//! #\n//! # \n//!   # \n//! ```\n")
    block = documentation_blocks(source)[0]
    assert block.code == "\n\n\n\n"
    assert [block.source_column(line, 1) for line in (2, 3, 4, 5)] == [5, 6, 7, 9]
