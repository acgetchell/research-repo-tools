"""Composable commands and diagnostics without benchmark execution."""

import json
from pathlib import Path

import pytest

from research_repo_tools.cli import main, parser
from research_repo_tools.criterion import COMPARISON_SCHEMA, parse_comparison
from research_repo_tools.evidence import Evidence, Provenance, publish_evidence


def test_compare_verify_and_render_retained_data(tmp_path: Path, capsys) -> None:
    for label, point in (("baseline", 100), ("current", 80)):
        estimates = tmp_path / label / "bench" / "new" / "estimates.json"
        estimates.parent.mkdir(parents=True)
        estimates.write_text(json.dumps({"median": {"point_estimate": point}}), newline="\n")
    root = ["--root", str(tmp_path), "performance"]
    assert main([*root, "compare", "baseline", "current", "--output", "comparison.json"]) == 0
    payload = (tmp_path / "comparison.json").read_bytes()
    assert parse_comparison(payload).comparisons[0].speedup == 1.25
    evidence = Evidence(payload, COMPARISON_SCHEMA, (("current", Provenance("a" * 40)),))
    publish_evidence(evidence, tmp_path / "retained.json", tmp_path / "manifest.json")
    assert main([*root, "verify", "retained.json", "manifest.json"]) == 0
    assert "Verified envelope" in capsys.readouterr().out
    assert main([*root, "render", "retained.json", "manifest.json", "--output", "report.md"]) == 0
    assert "| 1.25 | 20 |" in (tmp_path / "report.md").read_text()
    before = {path: path.read_bytes() for path in (tmp_path / "retained.json", tmp_path / "manifest.json")}
    for output in ("retained.json", "RETAINED.json", "MANIFEST.json"):
        assert main([*root, "render", "retained.json", "manifest.json", "--output", output]) == 1
        assert "distinct" in capsys.readouterr().err
        assert {path: path.read_bytes() for path in before} == before
    (tmp_path / "retained.json").write_bytes(b"corrupt")
    assert main([*root, "render", "retained.json", "manifest.json", "--output", "report.md"]) == 1
    assert "SHA-256" in capsys.readouterr().err
    assert "| 1.25 | 20 |" in (tmp_path / "report.md").read_text()


def test_empty_comparison_is_a_clear_cli_failure(tmp_path: Path, capsys) -> None:
    assert main(["--root", str(tmp_path), "performance", "compare", ".", "."]) == 1
    assert "no common Criterion benchmarks" in capsys.readouterr().err


@pytest.mark.parametrize("linked_root", ["baseline", "current"])
def test_compare_rejects_output_below_a_symlinked_input_root(tmp_path: Path, linked_root: str, capsys) -> None:
    originals = {}
    for label in ("baseline", "current"):
        estimates = tmp_path / label / "bench/new/estimates.json"
        estimates.parent.mkdir(parents=True)
        estimates.write_bytes(b'{"median":{"point_estimate":100}}')
        originals[estimates] = estimates.read_bytes()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(tmp_path / linked_root, target_is_directory=True)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise
    assert main(["--root", str(tmp_path), "performance", "compare", "baseline", "current", "--output", "alias/nested/report.json"]) == 1
    assert "outside both Criterion input roots" in capsys.readouterr().err
    assert not (alias / "nested").exists()
    assert {path: path.read_bytes() for path in originals} == originals


def test_help_lists_commands_lexicographically() -> None:
    text = parser().format_help()
    assert "notebooks,performance,release" in text
