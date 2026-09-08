"""Consumer-facing release policy and non-mutating previews."""

from pathlib import Path

import pytest

from research_repo_tools import cli, update_release
from tests.releases.test_metadata import _write_project


def snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_cargo_dry_run_validates_complete_candidate_without_replacing_files(tmp_path: Path, capsys) -> None:
    _write_project(tmp_path)
    config = tmp_path / "research-repo-tools.toml"
    config.write_text("[release]\n")
    original = snapshot(tmp_path)
    assert cli.main(["--config", str(config), "release", "update", "1.2.4", "--previous-release", "v1.2.3", "--date", "2026-09-07", "--dry-run"]) == 0
    assert "Would update: Cargo.toml" in capsys.readouterr().out
    assert snapshot(tmp_path) == original


def test_canonical_citation_doi_is_used_for_validation(tmp_path: Path, capsys) -> None:
    _write_project(tmp_path)
    config = tmp_path / "alternate.toml"
    config.write_text("[release]\n")
    (tmp_path / "CITATION.cff").write_text((tmp_path / "CITATION.cff").read_text().replace("12345", "99999999"))
    original = snapshot(tmp_path)
    assert cli.main(["--config", str(config), "release", "check"]) == 1
    assert "99999999" in capsys.readouterr().err
    assert cli.main(["--config", str(config), "release", "update", "1.2.4", "--previous-release", "v1.2.3", "--date", "2026-09-07"]) == 1
    assert snapshot(tmp_path) == original


def test_final_release_requires_generated_current_heading_before_update(tmp_path: Path, capsys) -> None:
    _write_project(tmp_path)
    config = tmp_path / "alternate.toml"
    config.write_text("[release]\n")
    original = snapshot(tmp_path)
    assert cli.main(["--config", str(config), "release", "update", "1.2.4", "--previous-release", "v1.2.3", "--final-release"]) == 1
    assert "final release requires" in capsys.readouterr().err
    assert snapshot(tmp_path) == original


def test_dry_run_and_publication_have_identical_changed_path_inventory(tmp_path: Path) -> None:
    _write_project(tmp_path)
    options = {"previous_tag": "v1.2.3", "release_date": "2026-09-07"}
    preview = update_release.update_release_version(tmp_path, "v1.2.4", dry_run=True, **options)
    published = update_release.update_release_version(tmp_path, "v1.2.4", **options)
    assert preview == published
    assert not update_release.update_release_version(tmp_path, "v1.2.4", **options).changed_paths


def test_standard_cargo_release_needs_no_release_configuration(tmp_path: Path) -> None:
    _write_project(tmp_path)
    project = tmp_path / "pyproject.toml"
    project.write_text(project.read_text().split("[tool.research-repo-tools.release]")[0])
    assert cli.main(["--root", str(tmp_path), "release", "check"]) == 0
    before = snapshot(tmp_path)
    assert cli.main(["--root", str(tmp_path), "release", "update", "1.2.4", "--previous-release", "v1.2.3", "--date", "2026-09-07", "--dry-run"]) == 0
    assert snapshot(tmp_path) == before


def test_standard_python_release_needs_no_configuration(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="consumer"\nversion="1.2.3"\n')
    (tmp_path / "uv.lock").write_text('version=1\n[[package]]\nname="consumer"\nversion="1.2.3"\nsource={virtual="."}\n')
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3] - 2026-09-01\n\n- Current.\n")
    (tmp_path / "README.md").write_text("# Consumer\n")
    assert cli.main(["--root", str(tmp_path), "release", "check"]) == 0
    assert cli.main(["--root", str(tmp_path), "release", "update", "1.2.4", "--previous-release", "v1.2.3", "--date", "2026-09-07"]) == 0
    assert 'version="1.2.4"' in (tmp_path / "pyproject.toml").read_text()
    assert 'version="1.2.4"' in (tmp_path / "uv.lock").read_text()


def test_workspace_release_updates_all_inherited_versions_but_not_dependencies(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers=["crates/*"]\n[workspace.package]\nversion="1.2.3"\nrepository="https://github.com/example/consumer"\n'
    )
    (tmp_path / "pyproject.toml").write_text('[project]\nname="consumer"\nversion="1.2.3"\n')
    (tmp_path / "uv.lock").write_text('version=1\n[[package]]\nname="consumer"\nversion="1.2.3"\nsource={editable="."}\n')
    for name in ("first", "second"):
        path = tmp_path / f"crates/{name}/Cargo.toml"
        path.parent.mkdir(parents=True)
        path.write_text(f'[package]\nname="{name}"\nversion.workspace=true\n')
    (tmp_path / "Cargo.lock").write_text(
        'version=4\n[[package]]\nname="first"\nversion="1.2.3"\n[[package]]\nname="second"\nversion="1.2.3"\n[[package]]\nname="dep"\nversion="1.2.3"\nsource="registry+https://example.invalid"\n'
    )
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3] - 2026-09-01\n\n- Current.\n")
    (tmp_path / "README.md").write_text("# Consumer\n")
    assert cli.main(["--root", str(tmp_path), "release", "check"]) == 0
    assert cli.main(["--root", str(tmp_path), "release", "update", "1.2.4", "--previous-release", "v1.2.3", "--date", "2026-09-07"]) == 0
    lock = (tmp_path / "Cargo.lock").read_text()
    assert 'name="first"\nversion="1.2.4"' in lock
    assert 'name="second"\nversion="1.2.4"' in lock
    assert 'name="dep"\nversion="1.2.3"' in lock


@pytest.mark.parametrize("metadata", ["cargo", "python", "tool-only"])
def test_release_without_lockfiles_or_repository_url(tmp_path, metadata):
    if metadata in {"cargo", "tool-only"}:
        manifest = tmp_path / "Cargo.toml"
        manifest.write_text('[package]\nname="consumer"\nversion="1.2.3"\n')
        if metadata == "tool-only":
            (tmp_path / "pyproject.toml").write_text('[dependency-groups]\ndev=["pytest==9.1.1"]\n')
    else:
        manifest = tmp_path / "pyproject.toml"
        manifest.write_text('[project]\nname="consumer"\nversion="1.2.3"\n')
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3] - 2026-09-01\n\n- Current.\n")
    assert cli.main(["--root", str(tmp_path), "release", "check"]) == 0
    assert cli.main(["--root", str(tmp_path), "release", "update", "1.2.4", "--previous-release", "v1.2.3"]) == 0
    assert 'version="1.2.4"' in manifest.read_text()
    assert not (tmp_path / "Cargo.lock").exists()
    assert not (tmp_path / "uv.lock").exists()


def test_workspace_root_and_members_follow_one_inherited_version(tmp_path):
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text('[package]\nname="root-package"\nversion.workspace=true\n[workspace]\nmembers=["child"]\n[workspace.package]\nversion="1.2.3"\n')
    (tmp_path / "child").mkdir()
    (tmp_path / "child/Cargo.toml").write_text('[package]\nname="child"\nversion.workspace=true\n')
    lock = tmp_path / "Cargo.lock"
    lock.write_text('version=4\n[[package]]\nname="root-package"\nversion="1.2.3"\n[[package]]\nname="child"\nversion="1.2.3"\n')
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3] - 2026-09-01\n\n- Current.\n")
    assert cli.main(["--root", str(tmp_path), "release", "check"]) == 0
    assert cli.main(["--root", str(tmp_path), "release", "update", "1.2.4", "--previous-release", "v1.2.3"]) == 0
    assert '[workspace.package]\nversion="1.2.4"' in manifest.read_text()
    assert lock.read_text().count('version="1.2.4"') == 2
