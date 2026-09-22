"""Future publication is explicit, source-checked, and rechecked when tags appear."""

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from research_repo_tools import publication
from research_repo_tools.evidence import Evidence, fingerprint_files, load_evidence, publish_evidence
from research_repo_tools.publication_config import load_publication
from tests.publication.public_publication_consumer import DOCUMENT, make_fixture


def prepare(root: Path) -> None:
    config = make_fixture(root)
    (root / "source.rs").write_bytes(b"measured source\r\n")
    digest = fingerprint_files(root, (Path("source.rs"),))
    retained = load_evidence(root / "evidence.json", root / "manifest.json")
    sources = dict(retained.sources)
    sources["current"] = replace(
        sources["current"],
        source_sha256=digest,
        harness_sha256=digest,
        context=(("release", "v1.1.0"), ("mode", "working-tree"), ("fingerprint-schema", "research-repo-tools/files/v1")),
    )
    publish_evidence(Evidence(retained.payload, retained.payload_schema, tuple(sources.items())), root / "evidence.json", root / "manifest.json")
    # Keep baseline's pin unchanged; update only the current independently
    # reviewed expectation after capturing the new test harness.
    first, current = config.split("[provenance.current]", 1)
    current = current.replace("c" * 64, digest, 1)
    config = (
        'repository="example/project"\ntag-policy="prepare"\ncurrent-sources=["source.rs"]\ncurrent-harness=["source.rs"]\n'
        + first
        + "[provenance.current]"
        + current
    )
    (root / "publication.toml").write_text(config, encoding="utf-8", newline="\n")


def test_missing_future_tag_is_allowed_but_new_tag_is_verified_before_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare(tmp_path)
    monkeypatch.setattr(publication, "run_git_bytes", lambda *args, **kwargs: subprocess.CompletedProcess([], 1, b"", b""))
    plan = load_publication(tmp_path, "publication.toml")
    monkeypatch.setattr(publication, "run_git_bytes", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, b"", b""))

    def invalid_tag(*args, **kwargs):
        raise ValueError("new tag contains differing bytes")

    monkeypatch.setattr(publication, "verify_tagged_files", invalid_tag)
    with pytest.raises(ValueError, match="differing"):
        publication.publish_publication(plan)
    assert (tmp_path / "README.md").read_bytes() == DOCUMENT


def test_preparation_rejects_source_changes_before_any_output(tmp_path: Path) -> None:
    prepare(tmp_path)
    (tmp_path / "source.rs").write_bytes(b"changed\n")
    with pytest.raises(ValueError, match="differ from"):
        load_publication(tmp_path, "publication.toml")
    assert (tmp_path / "README.md").read_bytes() == DOCUMENT
