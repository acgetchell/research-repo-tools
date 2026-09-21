"""Integrity, provenance, and failure-atomic promotion regressions."""

import json
from pathlib import Path

import pytest

from research_repo_tools import files
from research_repo_tools.evidence import (
    Evidence,
    Provenance,
    compare_provenance,
    deterministic_json,
    fingerprint_files,
    parse_evidence,
    publish_evidence,
    serialize_evidence,
)


def evidence() -> Evidence:
    return Evidence(b"retained\r\n\x00\xff", "consumer/v1", (("current", Provenance("a" * 40)),))


@pytest.mark.parametrize("value", [{1: "bad"}, {"value": float("nan")}, {"value": float("inf")}, {"value": object()}])
def test_serializer_rejects_lossy_json(value: object) -> None:
    with pytest.raises(ValueError, match="finite JSON"):
        deterministic_json(value)


def test_cyclic_json_and_unpaired_surrogates_fail_at_the_boundary() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(ValueError, match="cyclic"):
        deterministic_json(cyclic)
    with pytest.raises(ValueError, match="UTF-8"):
        Evidence(b"data", "bad\ud800", (("current", Provenance("a" * 40)),))


def test_corruption_unknown_schema_and_provenance_fail_before_use() -> None:
    payload, manifest = serialize_evidence(evidence())
    with pytest.raises(ValueError, match="SHA-256"):
        parse_evidence(payload.replace(b"\r\n", b"\n"), manifest)
    raw = json.loads(manifest)
    for document in (
        {**raw, "schema": "unknown/v1"},
        {**raw, "sources": {}},
        {**raw, "payload_sha256": "A" * 64},
        {**raw, "unexpected": None},
        {**raw, "sources": {"current": {}}},
    ):
        with pytest.raises(ValueError):
            parse_evidence(payload, json.dumps(document).encode())
    with pytest.raises(ValueError, match="duplicate"):
        parse_evidence(payload, b'{"schema":"x","schema":"y"}')


@pytest.mark.parametrize("fields", [(), ("revision", "revision"), ("unknown",), ("context.",), "revision"])
def test_compatibility_requires_explicit_nonempty_fields(fields) -> None:
    with pytest.raises(ValueError):
        compare_provenance(Provenance("a" * 40), Provenance("a" * 40), fields=fields)


def test_provenance_validation_and_mutable_inputs_cannot_invalidate_models() -> None:
    for revision in ("main", "abc1234", "A" * 40):
        with pytest.raises(ValueError, match="full lowercase"):
            Provenance(revision)
    with pytest.raises(ValueError, match="unique"):
        Provenance("a" * 40, context=(("cpu", "one"), ("cpu", "two")))
    with pytest.raises(ValueError, match="SHA-256"):
        Provenance("a" * 40, source_sha256="bad")
    contexts = [["cpu", "first"]]
    source = Provenance("a" * 40, context=contexts)  # ty: ignore[invalid-argument-type]
    contexts[0][1] = "later"
    assert source.context == (("cpu", "first"),)
    sources = [["current", source]]
    retained = Evidence(b"bytes", "consumer/v1", sources)  # ty: ignore[invalid-argument-type]
    sources[0][0] = "changed"
    assert retained.sources == (("current", source),)


def test_failed_promotion_restores_payload_manifest_and_report(tmp_path: Path, monkeypatch) -> None:
    payload, manifest, report = (tmp_path / name for name in ("data.csv", "data.json", "PERFORMANCE.md"))
    before = {payload: b"old data", manifest: b"old manifest", report: b"old report"}
    for path, data in before.items():
        path.write_bytes(data)
    replace = files._replace_path

    def fail(source: Path, destination: Path) -> None:
        if destination == report and source.suffix == ".tmp":
            raise OSError("injected promotion failure")
        replace(source, destination)

    monkeypatch.setattr(files, "_replace_path", fail)
    with pytest.raises(OSError, match="promotion failure"):
        publish_evidence(evidence(), payload, manifest, reports={report: b"new report"})
    assert {path: path.read_bytes() for path in before} == before
    assert set(tmp_path.iterdir()) == set(before)


def test_incomplete_promotion_rollback_exposes_recovery_bytes(tmp_path: Path, monkeypatch) -> None:
    payload, manifest = tmp_path / "data", tmp_path / "manifest"
    payload.write_bytes(b"old data")
    manifest.write_bytes(b"old manifest")
    replace = files._replace_path

    def fail(source: Path, destination: Path) -> None:
        if source.suffix == ".bak" or destination == manifest:
            raise OSError("injected failure")
        replace(source, destination)

    monkeypatch.setattr(files, "_replace_path", fail)
    with pytest.raises(ExceptionGroup) as caught:
        publish_evidence(evidence(), payload, manifest)
    recovery = caught.value.exceptions[1]
    assert isinstance(recovery, files.RecoveryError)
    assert recovery.backup is not None
    assert recovery.backup.read_bytes() == b"old data"
    assert manifest.read_bytes() == b"old manifest"


def test_publication_rejects_collisions_overlaps_and_unknown_immutable_paths(tmp_path: Path) -> None:
    target = tmp_path / "data"
    cases = [
        (target, target, None, ()),
        (target, tmp_path / "sidecar", {target: b"report"}, ()),
        (target, target / "manifest", None, ()),
        (target, tmp_path / "sidecar", None, (tmp_path / "other",)),
    ]
    for payload, manifest, reports, immutable in cases:
        with pytest.raises(ValueError):
            publish_evidence(evidence(), payload, manifest, reports=reports, immutable=immutable)
        assert not list(tmp_path.iterdir())


def test_fingerprint_rejects_traversal_aliases_and_empty_inventory(tmp_path: Path) -> None:
    (tmp_path / "file").write_bytes(b"source")
    for paths in ((), (Path("../file"),), (tmp_path / "file",), (Path("file"), Path("file")), (Path("absent"),)):
        with pytest.raises(ValueError):
            fingerprint_files(tmp_path, paths)


def test_stale_source_and_harness_report_separately() -> None:
    recorded = Provenance("a" * 40, "b" * 64, "c" * 64)
    measured = Provenance("a" * 40, "d" * 64, "e" * 64)
    result = compare_provenance(recorded, measured, fields=("revision", "source_sha256", "harness_sha256"))
    assert not result.compatible
    assert tuple(item.field for item in result.differences) == ("harness_sha256", "source_sha256")


def test_fingerprint_rejects_portable_case_and_unicode_aliases(tmp_path: Path) -> None:
    for first, second in (("file", "FILE"), ("café", "cafe\u0301")):
        for name in (first, second):
            (tmp_path / name).write_bytes(b"source")
        with pytest.raises(ValueError, match="duplicate fingerprint"):
            fingerprint_files(tmp_path, (Path(first), Path(second)))
