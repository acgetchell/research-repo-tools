"""Publication preflight, rendering boundaries, and multi-output failure regressions."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from research_repo_tools import files
from research_repo_tools.criterion import Estimate, Sample, compare_samples
from research_repo_tools.publication import (
    GitCheck,
    MarkerPair,
    TableLayout,
    plan_publication,
    publish_publication,
    render_svg,
    render_table,
    replace_section,
)
from research_repo_tools.publication_config import load_publication
from tests.publication.public_publication_consumer import DOCUMENT, LAYOUT, MARKERS, comparison, make_fixture


@pytest.mark.parametrize(
    "document",
    [
        b"no markers",
        MARKERS.begin.encode(),
        MARKERS.end.encode(),
        DOCUMENT + MARKERS.begin.encode(),
        DOCUMENT + MARKERS.end.encode(),
        MARKERS.end.encode() + MARKERS.begin.encode(),
    ],
)
def test_requires_exactly_one_ordered_marker_pair(document: bytes) -> None:
    with pytest.raises(ValueError, match="exactly one ordered"):
        replace_section(document, MARKERS, "content")


@pytest.mark.parametrize("content", [MARKERS.begin, MARKERS.end, "prefix\n" + MARKERS.begin])
def test_rejects_markers_in_rendered_content(content: str) -> None:
    with pytest.raises(ValueError, match="must not contain"):
        replace_section(DOCUMENT, MARKERS, content)


@pytest.mark.parametrize("begin,end", [("same", "same"), ("prefix", "prefix:end"), ("", "end"), ("x\ny", "end")])
def test_marker_models_reject_ambiguous_delimiters(begin: str, end: str) -> None:
    with pytest.raises(ValueError):
        MarkerPair(begin, end)


def test_normalize_body_newlines_but_preserve_arbitrary_outer_bytes() -> None:
    document = b"\xff" + DOCUMENT + b"\x00"
    updated = replace_section(document, MARKERS, "alpha\r\nbeta\rgamma\n")
    assert b"alpha\nbeta\ngamma" in updated
    assert updated[:1] == b"\xff" and updated[-1:] == b"\x00"


@pytest.mark.parametrize("document,begin,end", [(b"ABCD", "ABC", "BCD"), (b"ABABA end", "ABA", "end"), (b"begin ABABA", "begin", "ABA")])
def test_overlapping_markers_are_rejected(document: bytes, begin: str, end: str) -> None:
    with pytest.raises(ValueError, match="exactly one ordered"):
        replace_section(document, MarkerPair(begin, end), "body")


@pytest.mark.parametrize("rows", [(), (("step/2", "A"), ("step/2", "B"))])
def test_selection_must_be_nonempty_and_unique(rows) -> None:
    with pytest.raises(ValueError, match="unique benchmarks"):
        replace(LAYOUT, rows=rows)


@pytest.mark.parametrize("renderer", [render_table, render_svg])
def test_selection_and_unit_are_not_inferred(renderer) -> None:
    with pytest.raises(ValueError, match="retained unit"):
        renderer(comparison(), replace(LAYOUT, unit="ms"))
    for name in ("absent", "gone", "added"):
        with pytest.raises(ValueError, match="both baseline and current"):
            renderer(comparison(), replace(LAYOUT, rows=((name, "Selected"),)))


def test_renderers_keep_order_and_escape_markup() -> None:
    layout = replace(LAYOUT, rows=(("tiny", "[tiny]*\\"), ("step/2", "<script>&|`")))
    table = render_table(comparison(), layout)
    assert table.index("&#91;tiny&#93;&#42;&#92;") < table.index("&lt;script&gt;&amp;&#124;&#96;")
    svg = render_svg(comparison(), layout)
    assert b"<script>" not in svg
    assert b"&lt;script&gt;&amp;|`" in svg
    # Rendering duplicate display labels must still produce distinct bars.
    duplicate_labels = replace(layout, rows=(("tiny", "Same"), ("step/2", "Same")))
    assert render_svg(comparison(), duplicate_labels).count(b"<rect ") == 2


def test_svg_finite_geometry_for_extreme_valid_ratios() -> None:
    sample = compare_samples(Sample((("huge", Estimate(1e300)),)), Sample((("huge", Estimate(1)),)))
    svg = render_svg(sample, TableLayout((("huge", "Huge"),), "Baseline", "Current", "ns"))
    assert b'width="360.000000"' in svg
    assert b"inf" not in svg and b"nan" not in svg


@pytest.mark.parametrize("second_exists", [False, True])
def test_late_failure_rolls_back_document_and_figure(tmp_path: Path, second_exists: bool) -> None:
    make_fixture(tmp_path)
    svg = tmp_path / "figures/timing.svg"
    if second_exists:
        svg.parent.mkdir()
        svg.write_bytes(b"old figure")
    plan = load_publication(tmp_path, "publication.toml")
    original_replace = files._replace_path

    def fail_svg(source, destination):
        if destination == svg.resolve() and source.suffix == ".tmp":
            raise OSError("injected figure failure")
        original_replace(source, destination)

    with patch.object(files, "_replace_path", side_effect=fail_svg), pytest.raises(OSError, match="injected figure failure"):
        publish_publication(plan)
    assert (tmp_path / "README.md").read_bytes() == DOCUMENT
    if second_exists:
        assert svg.read_bytes() == b"old figure"
    else:
        assert not svg.parent.exists()
    assert not list(tmp_path.rglob("*.bak"))
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("name", ["README.md", "report.md", "Cargo.toml", "evidence.json", "manifest.json", "publication.toml", "figures/timing.svg"])
def test_changed_snapshots_cannot_publish(tmp_path: Path, name: str) -> None:
    make_fixture(tmp_path)
    plan = load_publication(tmp_path, "publication.toml")
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"concurrent edit")
    with pytest.raises(ValueError, match="changed after planning"):
        publish_publication(plan)
    assert target.read_bytes() == b"concurrent edit"
    if name != "README.md":
        assert (tmp_path / "README.md").read_bytes() == DOCUMENT


@pytest.mark.parametrize("name", ["README.md", "readme.MD", "evidence.json", "EVIDENCE.JSON", "figures", "figures/timing.svg/nested"])
def test_outputs_cannot_alias_or_overlap_inputs_or_each_other(tmp_path: Path, name: str) -> None:
    make_fixture(tmp_path)
    figures = {"figures/timing.svg": b"svg", name: b"bad"}
    with pytest.raises(ValueError, match="duplicate|overlapping"):
        plan_publication(tmp_path, "README.md", MARKERS, "content", inputs={"evidence.json": (tmp_path / "evidence.json").read_bytes()}, figures=figures)
    assert (tmp_path / "README.md").read_bytes() == DOCUMENT
    assert not (tmp_path / "figures").exists()


@pytest.mark.parametrize(
    "name", ["../outside", "/tmp/outside", "C:/outside", "docs\\file", "docs/file:stream", "docs/CON.txt", "docs/file.", ".git/config", ".GIT/config"]
)
def test_rejects_nonportable_or_escaping_paths(tmp_path: Path, name: str) -> None:
    make_fixture(tmp_path)
    with pytest.raises(ValueError):
        plan_publication(tmp_path, "README.md", MARKERS, "content", inputs={"evidence.json": (tmp_path / "evidence.json").read_bytes()}, figures={name: b"bad"})
    assert (tmp_path / "README.md").read_bytes() == DOCUMENT


def test_parent_and_leaf_symlinks_are_rejected(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    directory = tmp_path / "actual"
    directory.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(directory, target_is_directory=True)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege unavailable")
        raise
    with pytest.raises(ValueError, match="symlink"):
        plan_publication(
            tmp_path, "README.md", MARKERS, "content", inputs={"evidence.json": (tmp_path / "evidence.json").read_bytes()}, figures={"linked/out.svg": b"bad"}
        )
    (tmp_path / "alias.md").symlink_to(tmp_path / "README.md")
    with pytest.raises(ValueError, match="symlink"):
        plan_publication(tmp_path, "alias.md", MARKERS, "content", inputs={"evidence.json": (tmp_path / "evidence.json").read_bytes()})


@pytest.mark.parametrize("changed", [b"bad payload", b"{}"])
def test_bad_evidence_is_rejected_before_output(tmp_path: Path, changed: bytes) -> None:
    make_fixture(tmp_path)
    (tmp_path / "evidence.json").write_bytes(changed)
    with pytest.raises(ValueError, match="SHA-256"):
        load_publication(tmp_path, "publication.toml")
    assert not (tmp_path / "figures").exists()
    assert (tmp_path / "README.md").read_bytes() == DOCUMENT


@pytest.mark.parametrize(
    "old,new,expected",
    [
        ("schema = 1", "schema = true", "schema"),
        ('unit = "ns"', 'unit = "ms"', "unit"),
        ("b" * 40, "d" * 40, "provenance differs"),
        ('release = "v1.1.0"', 'release = "v9.9.9"', "provenance differs"),
        ("c" * 64, "d" * 64, "provenance differs"),
        ('source = "version"', 'source = "release-date"', "reference source"),
        ('source = "previous-tag"', 'source = "tag"', "both report tags"),
        ('manifest = "manifest.json"', 'manifest = "EVIDENCE.json"', "duplicate"),
        ('svg = "figures/timing.svg"', 'svg = "publication.toml"', "duplicate"),
        ('unit = "ns"', 'unit = "ns"\nunknown = 1', "optional fields"),
        ('source = "tag"', 'source = "tag"\ncount = true', "count"),
    ],
)
def test_configuration_boundaries(tmp_path: Path, old: str, new: str, expected: str) -> None:
    config = make_fixture(tmp_path)
    assert old in config
    (tmp_path / "publication.toml").write_text(config.replace(old, new), encoding="utf-8", newline="\n")
    with pytest.raises(ValueError, match=expected):
        load_publication(tmp_path, "publication.toml")
    assert (tmp_path / "README.md").read_bytes() == DOCUMENT
    assert not (tmp_path / "figures").exists()


def test_missing_or_duplicate_reference_captures_fail(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    for report in (b"", b"Baseline: v1.0.0\nCurrent: v1.1.0\nCurrent: v1.1.0\n"):
        (tmp_path / "report.md").write_bytes(report)
        with pytest.raises(ValueError, match="exactly 1 matches"):
            load_publication(tmp_path, "publication.toml")
    assert (tmp_path / "README.md").read_bytes() == DOCUMENT


def test_unverified_or_unknown_git_assets_cannot_publish(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    with pytest.raises(ValueError, match="unknown files"):
        plan_publication(
            tmp_path,
            "README.md",
            MARKERS,
            "content",
            inputs={"evidence.json": (tmp_path / "evidence.json").read_bytes()},
            git_checks=(GitCheck("v1.1.0", ("unknown.json",)),),
        )


def test_nested_document_links_are_relative_and_url_encoded(tmp_path: Path) -> None:
    config = make_fixture(tmp_path)
    nested = tmp_path / "docs/Overview.md"
    nested.parent.mkdir()
    nested.write_bytes(DOCUMENT)
    (tmp_path / "a report (new).md").write_bytes(b"linked evidence")
    config = config.replace('document = "README.md"', 'document = "docs/Overview.md"')
    config = config.replace('path = "evidence.json"}', 'path = "a report (new).md"}')
    (tmp_path / "publication.toml").write_text(config, encoding="utf-8", newline="\n")
    plan = load_publication(tmp_path, "publication.toml")
    contents = dict(plan.outputs)["docs/Overview.md"]
    assert b"](../figures/timing.svg)" in contents
    assert b"](../report.md)" in contents
    assert b"](../a%20report%20%28new%29.md)" in contents


def test_table_only_publication_and_input_alias_models(tmp_path: Path) -> None:
    config = make_fixture(tmp_path).replace('svg = "figures/timing.svg"\n', "")
    (tmp_path / "publication.toml").write_text(config, encoding="utf-8", newline="\n")
    plan = load_publication(tmp_path, "publication.toml")
    assert len(plan.outputs) == 1
    assert b"![" not in dict(plan.outputs)["README.md"]
    for name in ("caf\u00e9.svg", "cafe\u0301.svg"):
        # Either normalization spelling independently is supported.
        _ = plan_publication(
            tmp_path, "README.md", MARKERS, "body", inputs={"evidence.json": (tmp_path / "evidence.json").read_bytes()}, figures={name: b"svg"}
        )
    with pytest.raises(ValueError, match="duplicate"):
        plan_publication(
            tmp_path,
            "README.md",
            MARKERS,
            "body",
            inputs={"evidence.json": (tmp_path / "evidence.json").read_bytes()},
            figures={"caf\u00e9.svg": b"one", "cafe\u0301.svg": b"two"},
        )


def test_recovery_files_are_reported_if_rollback_also_fails(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    plan = load_publication(tmp_path, "publication.toml")
    original_replace = files._replace_path

    def fail(source, destination):
        if destination.name == "timing.svg" or source.suffix == ".bak":
            raise OSError("injected replacement failure")
        original_replace(source, destination)

    with patch.object(files, "_replace_path", side_effect=fail), pytest.raises(ExceptionGroup) as caught:
        publish_publication(plan)
    recovery = caught.value.exceptions[1]
    assert isinstance(recovery, files.RecoveryError)
    assert recovery.target == (tmp_path / "README.md").resolve()
    assert recovery.backup is not None and recovery.backup.read_bytes() == DOCUMENT
    assert not (tmp_path / "figures/timing.svg").exists()


def test_svg_rejects_xml_noncharacters() -> None:
    with pytest.raises(ValueError, match="valid XML"):
        render_svg(comparison(), replace(LAYOUT, current_label="invalid\ufffe"))


def test_tagged_source_expectations_are_separate_from_asset_checks(tmp_path: Path) -> None:
    config = make_fixture(tmp_path).replace('release = "v1.1.0"', 'release = "v1.1.0"\nverify-tag = true')
    (tmp_path / "publication.toml").write_text(config, encoding="utf-8", newline="\n")
    with patch("research_repo_tools.publication.verify_tagged_files") as verify:
        plan = load_publication(tmp_path, "publication.toml")
    verify.assert_called_once_with(tmp_path.resolve(), "v1.1.0", {}, revision="b" * 40)
    assert plan.git_checks == (GitCheck("v1.1.0", revision="b" * 40),)


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_documentation_configuration_is_executable(tmp_path: Path, newline: bytes) -> None:
    # Keep the copyable consumer example tied to its documented evidence shape.
    make_fixture(tmp_path)
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    section = readme.split("### Document publication", 1)[1]
    configuration = section.split("```toml\n", 1)[1].split("```", 1)[0]
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "comparison.json").write_bytes((tmp_path / "evidence.json").read_bytes())
    (evidence / "manifest.json").write_bytes((tmp_path / "manifest.json").read_bytes())
    cargo = b'[package]\nversion = "1.1.0"\n'.replace(b"\n", newline)
    (tmp_path / "Cargo.toml").write_bytes(cargo)
    (tmp_path / "PERFORMANCE.md").write_bytes((tmp_path / "report.md").read_bytes())
    (tmp_path / "publication.toml").write_text(configuration, encoding="utf-8", newline="\n")
    assert len(publish_publication(load_publication(tmp_path, "publication.toml"))) == 2
    assert (tmp_path / "Cargo.toml").read_bytes() == cargo
