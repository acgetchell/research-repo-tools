"""Tests for release metadata and version synchronization checks."""

from typing import TYPE_CHECKING

import pytest

from research_repo_tools import release_metadata as release_check

if TYPE_CHECKING:
    from pathlib import Path
_VERSION = "1.2.3"
_DOI = "10.5281/zenodo.12345"
_RELEASE_DATE = "2026-08-04"
_CARGO_TOML = f'[package]\nname = "consumer"\nversion = "{_VERSION}"\nrepository = "https://github.com/example/consumer"\n'


def _write_project(
    root: Path, *, metadata_version: str = _VERSION, readme: str | None = None, citation_doi: str = _DOI, citation_date: str = _RELEASE_DATE
) -> None:
    """Write a minimal repository for release-check tests."""
    readme_text = (
        readme
        if readme is not None
        else f'[![DOI](https://badgen.net/badge/DOI/10.5281%2Fzenodo.12345/blue)](https://doi.org/{_DOI})\nconsumer = "{_VERSION}"\ncargo add consumer@{_VERSION}\n[tagged](https://github.com/example/consumer/blob/v{_VERSION}/README.md)\n'
    )
    files = {
        "Cargo.toml": _CARGO_TOML,
        "Cargo.lock": f'version = 4\n\n[[package]]\nname = "consumer"\nversion = "{metadata_version}"\n',
        "pyproject.toml": f'[project]\nname = "consumer-tooling"\nversion = "{metadata_version}"\n',
        "uv.lock": f'version = 1\n\n[[package]]\nname = "consumer-tooling"\nversion = "{metadata_version}"\nsource = {{ editable = "." }}\n',
        "CITATION.cff": f"cff-version: 1.2.0\nversion: {metadata_version}\ndoi: {citation_doi}\ndate-released: {citation_date}\n",
        "CHANGELOG.md": f"# Changelog\n\n## [{_VERSION}] - {_RELEASE_DATE}\n\n- Release\n\n[{_VERSION}]: https://github.com/example/consumer/compare/v1.2.2...v{_VERSION}\n",
        "README.md": readme_text,
        "REFERENCES.md": f"- DOI: <https://doi.org/{_DOI}>\n",
    }
    for filename, content in files.items():
        (root / filename).write_text(content, encoding="utf-8")


def test_find_version_mismatches_accepts_synchronized_release(tmp_path: Path) -> None:
    """Matching structured metadata and active docs pass."""
    _write_project(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "benchmarking.md").write_text("just custom-command v1.2.3 v1.2.2\njust custom-assets v9.0.0 v8.0.0\n", encoding="utf-8")
    assert release_check.find_version_mismatches(tmp_path) == []
    assert release_check.find_release_metadata_mismatches(tmp_path) == []


def test_find_release_metadata_mismatches_reports_stale_date_and_dois(tmp_path: Path) -> None:
    """Citation dates and the stable concept DOI agree with release documentation."""
    _write_project(tmp_path, citation_doi="10.5281/zenodo.87654321", citation_date="2026-08-03")
    mismatches = release_check.find_release_metadata_mismatches(tmp_path)
    assert [(mismatch.reference.kind, mismatch.reference.value, mismatch.expected) for mismatch in mismatches] == [
        (release_check.MetadataKind.CITATION_DATE, "2026-08-03", _RELEASE_DATE),
        (release_check.MetadataKind.README_DOI, _DOI, "10.5281/zenodo.87654321"),
        (release_check.MetadataKind.REFERENCES_DOI, _DOI, "10.5281/zenodo.87654321"),
    ]


def test_release_date_uses_the_current_package_heading_not_the_first_release(tmp_path: Path) -> None:
    """Date synchronization targets Cargo's version even if another release is listed first."""
    _write_project(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [1.2.4] - 2026-08-05\n\n- Newer\n\n## [{_VERSION}] - {_RELEASE_DATE}\n\n- Current\n", encoding="utf-8"
    )
    assert release_check.find_release_metadata_mismatches(tmp_path) == []


def test_release_metadata_rejects_a_missing_citation_date(tmp_path: Path) -> None:
    _write_project(tmp_path)
    citation = tmp_path / "CITATION.cff"
    citation.write_text(citation.read_text(encoding="utf-8").replace(f"date-released: {_RELEASE_DATE}\n", ""), encoding="utf-8")
    with pytest.raises(release_check.ReleaseCheckError, match="CITATION\\.cff must contain exactly one top-level date-released; found 0"):
        release_check.find_release_metadata_mismatches(tmp_path)


def test_release_metadata_rejects_a_malformed_citation_date_with_its_line(tmp_path: Path) -> None:
    _write_project(tmp_path)
    citation = tmp_path / "CITATION.cff"
    citation.write_text(citation.read_text(encoding="utf-8").replace(_RELEASE_DATE, "2026/08/04"), encoding="utf-8")
    with pytest.raises(release_check.ReleaseCheckError, match="CITATION\\.cff:4: top-level date-released"):
        release_check.find_release_metadata_mismatches(tmp_path)


def test_release_metadata_rejects_duplicate_citation_dates_with_both_lines(tmp_path: Path) -> None:
    _write_project(tmp_path)
    citation = tmp_path / "CITATION.cff"
    citation.write_text(citation.read_text(encoding="utf-8") + f"date-released: {_RELEASE_DATE}\n", encoding="utf-8")
    with pytest.raises(release_check.ReleaseCheckError, match="CITATION\\.cff:4 .*found 2 at lines 4, 5"):
        release_check.find_release_metadata_mismatches(tmp_path)


def test_release_metadata_rejects_a_malformed_current_changelog_date_with_its_line(tmp_path: Path) -> None:
    _write_project(tmp_path)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(changelog.read_text(encoding="utf-8").replace(_RELEASE_DATE, "2026/08/04"), encoding="utf-8")
    with pytest.raises(release_check.ReleaseCheckError, match="CHANGELOG\\.md:.*(?:line 3|3: current-version heading)"):
        release_check.find_release_metadata_mismatches(tmp_path)


@pytest.mark.parametrize(
    "heading",
    [f"## [{_VERSION} - {_RELEASE_DATE}", f"## {_VERSION}] - {_RELEASE_DATE}", f"## [v{_VERSION} - {_RELEASE_DATE}", f"## v{_VERSION}] - {_RELEASE_DATE}"],
)
def test_release_metadata_rejects_asymmetric_current_changelog_brackets(tmp_path: Path, heading: str) -> None:
    _write_project(tmp_path)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(changelog.read_text(encoding="utf-8").replace(f"## [{_VERSION}] - {_RELEASE_DATE}", heading), encoding="utf-8")
    with pytest.raises(release_check.ReleaseCheckError, match="CHANGELOG\\.md:.*(?:line 3|3: current-version heading)"):
        release_check.find_release_metadata_mismatches(tmp_path)


def test_release_metadata_rejects_a_current_changelog_heading_without_a_date(tmp_path: Path) -> None:
    _write_project(tmp_path)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(changelog.read_text(encoding="utf-8").replace(f"## [{_VERSION}] - {_RELEASE_DATE}", f"## [{_VERSION}]"), encoding="utf-8")
    with pytest.raises(release_check.ReleaseCheckError, match="CHANGELOG\\.md:.*(?:line 3|3: current-version heading)"):
        release_check.find_release_metadata_mismatches(tmp_path)


def test_release_metadata_rejects_an_impossible_current_changelog_date(tmp_path: Path) -> None:
    _write_project(tmp_path)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(changelog.read_text(encoding="utf-8").replace(_RELEASE_DATE, "2026-02-30"), encoding="utf-8")
    with pytest.raises(release_check.ReleaseCheckError, match="CHANGELOG\\.md: Invalid release date at line 3"):
        release_check.find_release_metadata_mismatches(tmp_path)


def test_release_metadata_rejects_duplicate_current_changelog_dates_with_both_lines(tmp_path: Path) -> None:
    _write_project(tmp_path)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(changelog.read_text(encoding="utf-8") + f"\n## [{_VERSION}] - {_RELEASE_DATE}\n", encoding="utf-8")
    with pytest.raises(release_check.ReleaseCheckError, match="CHANGELOG\\.md: Duplicate release heading .* at line 9; first seen at line 3"):
        release_check.find_release_metadata_mismatches(tmp_path)


def test_find_version_mismatches_reports_all_structured_metadata(tmp_path: Path) -> None:
    """Cargo, Python, uv, and citation metadata must agree."""
    _write_project(tmp_path, metadata_version="1.2.2")
    mismatches = release_check.find_version_mismatches(tmp_path)
    assert [(mismatch.reference.kind, mismatch.reference.path.name, mismatch.reference.version) for mismatch in mismatches] == [
        (release_check.ReferenceKind.CARGO_LOCK, "Cargo.lock", "1.2.2"),
        (release_check.ReferenceKind.PYPROJECT, "pyproject.toml", "1.2.2"),
        (release_check.ReferenceKind.UV_LOCK, "uv.lock", "1.2.2"),
        (release_check.ReferenceKind.CITATION, "CITATION.cff", "1.2.2"),
    ]


def test_find_version_mismatches_reports_stale_active_documentation(tmp_path: Path) -> None:
    """Versioned install instructions and dependency snippets track Cargo."""
    _write_project(
        tmp_path,
        readme='consumer = { version = "1.2.2", features = ["serde"] }\ncargo add --features serde consumer@1.2.1\n[tagged](https://raw.githubusercontent.com/example/consumer/v1.2.0/README.md)\n',
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "benchmarking.md").write_text("just custom-command v1.1.0 v1.0.0\n", encoding="utf-8")
    mismatches = release_check.find_version_mismatches(tmp_path)
    assert [mismatch.reference.kind for mismatch in mismatches] == [release_check.ReferenceKind.DEPENDENCY_SNIPPET, release_check.ReferenceKind.CARGO_ADD]


def test_find_version_mismatches_reports_stale_changelog_release(tmp_path: Path) -> None:
    """The latest generated changelog release must match Cargo.toml."""
    _write_project(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.2] - 2026-08-03\n", encoding="utf-8")
    mismatches = release_check.find_version_mismatches(tmp_path)
    assert len(mismatches) == 1
    assert mismatches[0].reference.kind is release_check.ReferenceKind.CHANGELOG
    assert mismatches[0].reference.version == "1.2.2"


def test_find_version_mismatches_reports_stale_changelog_comparison_target(tmp_path: Path) -> None:
    """The current changelog link must compare through the Cargo version."""
    _write_project(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{_VERSION}] - 2026-08-04\n\n[{_VERSION}]: https://github.com/example/consumer/compare/v1.2.1...v1.2.2\n", encoding="utf-8"
    )
    mismatches = release_check.find_version_mismatches(tmp_path)
    assert len(mismatches) == 1
    assert mismatches[0].reference.kind is release_check.ReferenceKind.CHANGELOG_COMPARISON
    assert mismatches[0].reference.version == "1.2.2"


def test_find_version_mismatches_ignores_historical_surfaces(tmp_path: Path) -> None:
    """Archived docs and test fixtures may retain old release examples."""
    _write_project(tmp_path)
    archive = tmp_path / "docs" / "archive"
    fixtures = tmp_path / "tests" / "fixtures"
    archive.mkdir(parents=True)
    fixtures.mkdir(parents=True)
    stale = 'consumer = "0.1.0"\njust custom-command v0.1.0 v0.0.9\n'
    (archive / "old.md").write_text(stale, encoding="utf-8")
    (fixtures / "example.md").write_text(stale, encoding="utf-8")
    assert release_check.find_version_mismatches(tmp_path) == []


def test_find_version_mismatches_rejects_missing_editable_uv_package(tmp_path: Path) -> None:
    """uv.lock must include the local support package as an editable entry."""
    _write_project(tmp_path)
    (tmp_path / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "consumer-tooling"\nversion = "1.2.3"\nsource = { registry = "https://pypi.org/simple" }\n', encoding="utf-8"
    )
    with pytest.raises(release_check.ReleaseCheckError, match="exactly one uv\\.lock editable package"):
        release_check.find_version_mismatches(tmp_path)


def test_find_version_mismatches_rejects_malformed_citation_version(tmp_path: Path) -> None:
    """Malformed citation versions fail before release."""
    _write_project(tmp_path)
    (tmp_path / "CITATION.cff").write_text('cff-version: 1.2.0\nversion: "\ndoi: 10.5281/zenodo.12345678\ndate-released: 2026-08-04\n', encoding="utf-8")
    with pytest.raises(release_check.ReleaseCheckError, match="CITATION\\.cff:2: top-level version"):
        release_check.find_version_mismatches(tmp_path)


def test_main_reports_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI reports the synchronized release version."""
    _write_project(tmp_path)
    exit_code = release_check.main([str(tmp_path)])
    assert exit_code == 0
    assert "Release metadata is synchronized at 1.2.3" in capsys.readouterr().out


def test_main_reports_mismatches(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI reports mismatches on stderr and exits nonzero."""
    _write_project(tmp_path, metadata_version="1.2.2")
    exit_code = release_check.main([str(tmp_path)])
    assert exit_code == 1
    assert "Release-version references are out of sync" in capsys.readouterr().err
