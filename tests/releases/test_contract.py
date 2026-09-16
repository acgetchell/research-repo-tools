"""Consumer-facing release policy and non-mutating previews."""

from pathlib import Path

import pytest

from research_repo_tools import cli, release_metadata, update_release
from research_repo_tools.config import ReleasePolicy
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
    preview = update_release.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3", release_date="2026-09-07", dry_run=True)
    published = update_release.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3", release_date="2026-09-07")
    assert preview == published
    assert not update_release.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3", release_date="2026-09-07").changed_paths


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


@pytest.mark.parametrize("name", ["My_Tools", "my..tools", "MY-tools"])
@pytest.mark.parametrize("source", ["editable", "virtual"])
def test_release_accepts_normalized_python_distribution_names(tmp_path: Path, name: str, source: str) -> None:
    manifest = tmp_path / "pyproject.toml"
    lock = tmp_path / "uv.lock"
    manifest.write_text(f'[project]\nname="{name}"\nversion="1.2.3"\n', encoding="utf-8")
    lock.write_text(
        f'version=1\n[[package]]\nname="my-tools"\nversion="1.2.3"\nsource={{{source}="."}}\n'
        '[[package]]\nname="my-tools"\nversion="9.8.7"\nsource={registry="https://example.invalid"}\n',
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3] - 2026-09-15\n\n- Current.\n", encoding="utf-8")
    assert release_metadata.check(tmp_path) == 0
    update_release.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3", release_date="2026-09-15")
    assert f'name="{name}"' in manifest.read_text(encoding="utf-8")
    assert 'version="1.2.4"' in lock.read_text(encoding="utf-8")
    assert 'version="9.8.7"' in lock.read_text(encoding="utf-8")


def test_release_rejects_normalized_local_package_ambiguity(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="My_Tools"\nversion="1.2.3"\n', encoding="utf-8")
    lock = tmp_path / "uv.lock"
    lock.write_text(
        'version=1\n[[package]]\nname="My_Tools"\nversion="1.2.3"\nsource={editable="."}\n'
        '[[package]]\nname="my-tools"\nversion="1.2.3"\nsource={virtual="."}\n',
        encoding="utf-8",
    )
    with pytest.raises(release_metadata.ReleaseCheckError, match="found 2"):
        release_metadata.python_version_references(tmp_path)


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


def test_contained_dotdot_workspace_members_stay_inside_validation_tree(tmp_path, monkeypatch):
    root = tmp_path / "consumer"
    root.mkdir()
    (root / "Cargo.toml").write_text('[workspace]\nmembers=["../consumer/member"]\n[workspace.package]\nversion="1.2.3"\n')
    member = root / "member/Cargo.toml"
    member.parent.mkdir()
    member.write_text('[package]\nname="member"\nversion.workspace=true\n')
    (root / "Cargo.lock").write_text('version=4\n[[package]]\nname="member"\nversion="1.2.3"\n')
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3] - 2026-09-01\n\n- Current.\n")
    validation = tmp_path / "validation"
    validation.mkdir()
    sentinel = validation / "consumer/member/Cargo.toml"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b"outside sentinel")
    before = snapshot(tmp_path)
    temporary_directory = update_release.tempfile.TemporaryDirectory
    monkeypatch.setattr(update_release.tempfile, "TemporaryDirectory", lambda **kwargs: temporary_directory(dir=validation, **kwargs))
    result = update_release.update_release_version(root, "v1.2.4", previous_tag="v1.2.3", release_date="2026-09-07", dry_run=True)
    assert result.changed_paths
    assert snapshot(tmp_path) == before
    assert sentinel.read_bytes() == b"outside sentinel"


@pytest.mark.parametrize("link", ["", "(https://example.org/releases/v1.2.3)"])
def test_release_update_preserves_heading_links_and_fenced_example_bytes(tmp_path, link):
    _write_project(tmp_path)
    changelog = tmp_path / "CHANGELOG.md"
    example = "```markdown\r\n## [1.2.3] - 1999-01-01\r\n```\r\n\r\n"
    text = f"# Changelog\r\n\r\n{example}## [1.2.3]{link} - 2026-09-01\r\n\r\n- Current.\r\n"
    changelog.write_bytes(text.encode())
    assert release_metadata.find_version_mismatches(tmp_path) == []
    update_release.update_release_version(tmp_path, "v1.2.3", previous_tag="v1.2.2", release_date="2026-09-07", policy=ReleasePolicy(final_changelog=True))
    assert changelog.read_bytes() == text.replace("2026-09-01", "2026-09-07").encode()
    assert release_metadata.check(tmp_path, policy=ReleasePolicy(final_changelog=True)) == 0


def test_fenced_target_cannot_satisfy_final_release_or_change_examples(tmp_path):
    _write_project(tmp_path)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n~~~markdown\n## [1.2.4] - 1999-01-01\n~~~\n\n" + changelog.read_text())
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="final release requires"):
        update_release.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3", policy=ReleasePolicy(final_changelog=True))
    assert snapshot(tmp_path) == before
    update_release.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3")
    assert changelog.read_bytes() == before["CHANGELOG.md"]
    assert release_metadata.check(tmp_path, policy=ReleasePolicy(final_changelog=True)) == 1


@pytest.mark.parametrize("quote", ['"', "'"])
def test_release_update_handles_commented_headers_and_quoted_keys_without_touching_examples(tmp_path, quote):
    _write_project(tmp_path)
    examples = f'example = {quote * 3}\n[project]\nversion = "9.9.9"\n[[package]]\nversion = "9.9.9"\n{quote * 3}\n'
    originals = {}
    for name in ("Cargo.toml", "pyproject.toml", "Cargo.lock", "uv.lock"):
        path = tmp_path / name
        text = path.read_text().replace("[project]", f"[{quote}project{quote}] # metadata")
        text = text.replace("[[package]]", f"[[{quote}package{quote}]] # locked package").replace("[package]", f"[{quote}package{quote}] # metadata")
        key = r'"\u0076ersion"' if quote == '"' else "'version'"
        text = text.replace('version = "1.2.3"', f'{key} = "1.2.3" # release')
        originals[name] = (examples + text).replace("\n", "\r\n").encode()
        path.write_bytes(originals[name])
    assert release_metadata.check(tmp_path) == 0
    update_release.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3", release_date="2026-09-07")
    for name, original in originals.items():
        assert (tmp_path / name).read_bytes() == original.replace(b"1.2.3", b"1.2.4")


def test_optional_doi_references_do_not_restrict_unrelated_bibliographies(tmp_path):
    _write_project(tmp_path, readme="# Consumer\n")
    references = tmp_path / "REFERENCES.md"
    references.write_text("- Author. Method. https://doi.org/10.1234/other-paper\n")
    before = references.read_bytes()
    assert release_metadata.check(tmp_path) == 0
    update_release.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3")
    assert references.read_bytes() == before


@pytest.mark.parametrize("filename,text", [("README.md", "[![DOI](badge)](broken)"), ("REFERENCES.md", "- DOI: broken")])
def test_malformed_optional_doi_references_fail_without_publication(tmp_path, filename, text):
    _write_project(tmp_path)
    (tmp_path / filename).write_text(text + "\n")
    before = snapshot(tmp_path)
    assert release_metadata.check(tmp_path) == 1
    with pytest.raises(ValueError, match="malformed"):
        update_release.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3")
    assert snapshot(tmp_path) == before


def test_workspace_release_respects_excluded_glob_matches(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers=["crates/*"]\nexclude=["crates/fixtures"]\n[workspace.package]\nversion="1.2.3"\n')
    member = tmp_path / "crates/member/Cargo.toml"
    member.parent.mkdir(parents=True)
    member.write_text('[package]\nname="member"\nversion.workspace=true\n')
    excluded = tmp_path / "crates/fixtures/README.md"
    excluded.parent.mkdir()
    excluded.write_text("Fixture data, not a workspace crate.\n")
    lock = tmp_path / "Cargo.lock"
    lock.write_text('version=4\n[[package]]\nname="member"\nversion="1.2.3"\n')
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3] - 2026-09-01\n\n- Current.\n")
    before = snapshot(tmp_path)
    assert release_metadata.check(tmp_path) == 0
    update_release.update_release_version(tmp_path, "v1.2.4", previous_tag="v1.2.3", release_date="2026-09-16")
    assert lock.read_bytes() == before["Cargo.lock"].replace(b"1.2.3", b"1.2.4")
    assert member.read_bytes() == before["crates/member/Cargo.toml"]
    assert excluded.read_bytes() == before["crates/fixtures/README.md"]
