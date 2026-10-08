"""Rust fence extraction retains source lines and delegates rule assertions."""

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
    }


def test_documentation_fixture_assertions_use_shared_checker(tmp_path, monkeypatch):
    from research_repo_tools import config, semgrep, semgrep_scan

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    source = fixtures / "bad.md"
    source.write_bytes(b"```rust\n// ruleid: local.rule\nunsafe { operation() }\n```\n")
    settings = config.Config(root=tmp_path, semgrep=config.SemgrepSettings(config="rules.yml", fixtures="fixtures"))
    original = source.read_bytes()

    def check(adapted):
        from pathlib import Path

        generated = Path(adapted.semgrep.fixtures) / "bad.md.block-0.rs"
        assert generated.read_bytes() == b"\n// ruleid: local.rule\nunsafe { operation() }\n"
        raise ValueError("local.rule: expected finding not reported")

    monkeypatch.setattr(semgrep, "check", check)
    with pytest.raises(ValueError, match="expected finding"):
        semgrep_scan.check_documentation_fixtures(settings)
    assert source.read_bytes() == original
