"""Release preparation preserves metadata, evidence, and prior files on failure."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from research_repo_tools import files
from research_repo_tools import release_discovery as release_discovery
from research_repo_tools import release_metadata as release_check
from research_repo_tools import update_release as updater

from .test_metadata import _write_project

if TYPE_CHECKING:
    from pathlib import Path


def _snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("tag", ["1.2.4", "v1.2", "v01.2.4", "v1.2.4-rc.1", "v1.2.4+build", " v1.2.4"])
def test_rejects_non_stable_or_noncanonical_tags_before_changes(tmp_path: Path, tag: str) -> None:
    _write_project(tmp_path)
    original = _snapshot(tmp_path)
    with pytest.raises(ValueError, match="stable"):
        updater.update_release_version(tmp_path, tag, previous_tag="v1.2.3")
    assert _snapshot(tmp_path) == original


def test_prepares_all_metadata_without_upgrading_dependencies_or_rewriting_evidence(tmp_path: Path) -> None:
    _write_project(tmp_path)
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text(cargo_lock.read_text() + '\n[[package]]\nname = "dependency"\nversion = "9.8.7"\n', encoding="utf-8", newline="\n")
    readme = tmp_path / "README.md"
    readme.write_text(
        readme.read_text()
        + "[main guide](https://github.com/example/consumer/blob/main/docs/guide.md)\n[plot](https://raw.githubusercontent.com/example/consumer/v1.2.3/docs/archives/results/v1.2.3-vs-v1.2.2.svg)\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "docs").mkdir()
    guide = tmp_path / "docs" / "guide.md"
    guide.write_text("just custom-command v1.2.3 v1.2.2\nHistorical v1.0.0 remains unchanged.\n", encoding="utf-8", newline="\n")
    previous_changelog = (tmp_path / "CHANGELOG.md").read_bytes()
    result = updater.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3", release_date="2026-08-30")
    assert result.previous_tag == "v1.2.3"
    assert result.release_date == "2026-08-30"
    assert 'version = "9.8.7"' in cargo_lock.read_text()
    assert 'name = "consumer"\nversion = "1.2.4"' in cargo_lock.read_text()
    assert 'version = "1.2.4"' in (tmp_path / "uv.lock").read_text()
    assert "doi: 10.5281/zenodo.12345" in (tmp_path / "CITATION.cff").read_text()
    assert "date-released: 2026-08-30" in (tmp_path / "CITATION.cff").read_text()
    assert "blob/main/docs/guide.md" in readme.read_text()
    assert "v1.2.3/docs/archives/results/" in readme.read_text()
    assert "just custom-command v1.2.3 v1.2.2" in guide.read_text()
    assert "Historical v1.0.0 remains unchanged." in guide.read_text()
    assert (tmp_path / "CHANGELOG.md").read_bytes() == previous_changelog
    assert [m.reference.kind for m in release_check.find_version_mismatches(tmp_path)] == [release_check.ReferenceKind.CHANGELOG]
    assert release_check.find_release_metadata_mismatches(tmp_path) == []
    snapshot = _snapshot(tmp_path)
    assert updater.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3", release_date="2026-08-30").changed_paths == ()
    assert _snapshot(tmp_path) == snapshot


def test_utc_midnight_updates_citation_and_existing_changelog_dates_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_project(tmp_path)

    class Clock:
        @staticmethod
        def now(timezone: object) -> datetime:
            assert timezone is UTC
            return datetime(2026, 8, 31, tzinfo=UTC)

    monkeypatch.setattr(updater, "datetime", Clock)
    result = updater.update_release_version(tmp_path, "v1.2.3", previous_tag="v1.2.2")
    assert {path.name for path in result.changed_paths} == {"CITATION.cff", "CHANGELOG.md"}
    assert "date-released: 2026-08-31" in (tmp_path / "CITATION.cff").read_text()
    assert "## [1.2.3] - 2026-08-31" in (tmp_path / "CHANGELOG.md").read_text()


def test_discovery_excludes_drafts_prereleases_and_handles_published_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    document = [
        {"tagName": tag, "isDraft": draft, "isPrerelease": prerelease, "publishedAt": "2026-08-01T00:00:00Z"}
        for tag, draft, prerelease in [("v1.2.3", False, False), ("v1.2.4", False, False), ("v2.0.0", True, False), ("v2.1.0", False, True)]
    ]
    monkeypatch.setattr(updater, "get_safe_executable", lambda command: command)
    monkeypatch.setattr(updater, "_published_releases", lambda _root: release_discovery.stable_published_releases(document))
    assert updater.infer_previous_release(tmp_path, "v1.2.4") == "v1.2.3"
    with pytest.raises(ValueError, match="older than"):
        updater.infer_previous_release(tmp_path, "v1.2.2")


def test_missing_github_cli_fails_before_discovery_and_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _write_project(tmp_path)
    original = _snapshot(tmp_path)

    def missing_gh(command: str) -> str:
        assert command == "gh"
        msg = "gh is required"
        raise updater.ExecutableNotFoundError(msg)

    monkeypatch.setattr(updater, "get_safe_executable", missing_gh)
    monkeypatch.setattr(updater, "_published_releases", lambda _root: pytest.fail("discovery ran without gh"))
    assert updater.main(["v1.2.4", "--repo-root", str(tmp_path)]) == 1
    assert "gh is required" in capsys.readouterr().err
    assert _snapshot(tmp_path) == original


def test_changelog_generation_sync_uses_prepared_date_without_github(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_project(tmp_path)
    updater.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3", release_date="2026-08-30")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## [1.2.4] - 2026-08-31\n\nGenerated notes.\n\n" + changelog.read_text(), encoding="utf-8", newline="\n")
    monkeypatch.setattr(updater, "infer_previous_release", lambda *_args: pytest.fail("offline date sync queried GitHub"))
    assert updater.main(["v1.2.4", "--repo-root", str(tmp_path), "--sync-changelog-date"]) == 0
    assert changelog.read_text().startswith("## [1.2.4] - 2026-08-30\n\nGenerated notes.")
    assert release_check.find_release_metadata_mismatches(tmp_path) == []
    original = _snapshot(tmp_path)
    updater.sync_changelog_date(tmp_path, "v1.2.4")
    assert _snapshot(tmp_path) == original


@pytest.mark.parametrize("heading", ["## [1.2.4] - 2026-99-99", "## [1.2.4] - 2026-08-30\n\n## [1.2.4] - 2026-08-31"])
def test_malformed_or_duplicate_target_heading_preserves_all_files(tmp_path: Path, heading: str) -> None:
    _write_project(tmp_path)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(heading + "\n\n" + changelog.read_text(), encoding="utf-8", newline="\n")
    original = _snapshot(tmp_path)
    with pytest.raises(ValueError, match="date|heading"):
        updater.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3")
    assert _snapshot(tmp_path) == original


def test_validation_failure_precedes_repository_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_project(tmp_path, citation_doi="10.5281/zenodo.123456")
    original = _snapshot(tmp_path)
    monkeypatch.setattr(updater, "_publish_texts", lambda _outputs: pytest.fail("invalid metadata reached publication"))
    with pytest.raises(ValueError, match="failed validation"):
        updater.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3")
    assert _snapshot(tmp_path) == original


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_mid_transaction_failure_restores_original_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, newline: str) -> None:
    _write_project(tmp_path)
    for path in tmp_path.iterdir():
        path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8", newline=newline)
    original = _snapshot(tmp_path)
    write = files._replace_path
    writes = 0

    def fail_once(path: Path, target: Path) -> None:
        nonlocal writes
        writes += 1
        if writes == 3:
            msg = "simulated replacement failure"
            raise OSError(msg)
        write(path, target)

    monkeypatch.setattr(files, "_replace_path", fail_once)
    with pytest.raises(OSError, match="simulated replacement"):
        updater.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3")
    assert _snapshot(tmp_path) == original


def test_symlinked_lock_rejected_before_github_or_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_project(tmp_path)
    lock = tmp_path / "uv.lock"
    target = tmp_path / "saved.lock"
    lock.rename(target)
    try:
        lock.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this runner")
    original = target.read_bytes()
    monkeypatch.setattr(updater, "infer_previous_release", lambda *_args: pytest.fail("discovery preceded symlink rejection"))
    with pytest.raises(ValueError, match="symbolic link"):
        updater.update_release_version(tmp_path, "v1.2.4")
    assert lock.is_symlink()
    assert target.read_bytes() == original
