"""Promotion composes the shared transaction without losing prior evidence."""

from pathlib import Path

import pytest

from research_repo_tools import files
from research_repo_tools.evidence import publish_evidence
from research_repo_tools.performance_reports import load_report_plan
from research_repo_tools.publication import publish_publication
from tests.performance.public_workflows_consumer import retained


def test_index_failure_restores_current_and_removes_partial_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "report.toml").write_bytes(b'schema=1\ncurrent="docs/current.md"\narchive="docs/evidence"\ntitle="Timings"\n')
    publish_evidence(retained(), tmp_path / "input.json", tmp_path / "input.evidence.json")
    publish_publication(load_report_plan(tmp_path, "report.toml", payload="input.json", manifest="input.evidence.json"))
    publish_evidence(retained("v1.2.0", "v1.1.0"), tmp_path / "input.json", tmp_path / "input.evidence.json")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    plan = load_report_plan(tmp_path, "report.toml", payload="input.json", manifest="input.evidence.json")
    original = files._replace_path

    def fail_index(source, destination):
        if destination.name == "README.md" and source.suffix == ".tmp":
            raise OSError("injected index failure")
        original(source, destination)

    monkeypatch.setattr(files, "_replace_path", fail_index)
    with pytest.raises(OSError, match="injected index"):
        publish_publication(plan)
    assert {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
