"""Independent common contracts, including real Git mutations in disposable repos."""

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from research_repo_tools import archive_changelog, changelog, cli, config, postprocess_changelog
from research_repo_tools.markdown import relocate_links
from research_repo_tools.process import run_git_command


def settings(root: Path, **changes: object) -> config.Config:
    return config.Config(root, {"changelog": {"owner": "example", "repository": "research", **changes}})


def test_templates_are_common_valid_package_resources(tmp_path: Path) -> None:
    for name in changelog.TEMPLATES:
        rendered = changelog.template(name, owner="example", repository="research")
        assert rendered.strip()
        if name.endswith(".toml"):
            tomllib.loads(rendered)
        assert "__OWNER__" not in rendered
        assert "__REPOSITORY__" not in rendered
    target = tmp_path / "justfile"
    target.write_bytes(b"existing:\r\n    custom-command\r\n")
    with pytest.raises(FileExistsError):
        changelog.write_template(target, changelog.template("justfile"))
    assert target.read_bytes() == b"existing:\r\n    custom-command\r\n"


@pytest.mark.parametrize("component", ['x"\n[malicious]', "../x", "x/y", "..", "https://example.com"])
def test_template_rejects_interpolation_and_path_syntax(component: str) -> None:
    with pytest.raises(ValueError):
        changelog.template("cliff.toml", owner=component, repository="repo")


def test_changelog_filename_is_a_shared_convention(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "config.toml").write_text("[changelog]\n")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.0] - 2026-09-07\n\n- Note with [reference].\n\n[reference]: https://example.com/issue\n")
    assert cli.main(["--config", str(tmp_path / "config.toml"), "changelog", "notes", "v1.2.0"]) == 0
    assert capsys.readouterr().out == "- Note with [reference].\n\n[reference]: https://example.com/issue\n"
    (tmp_path / "config.toml").write_text('[changelog]\npath="CUSTOM.md"\n')
    with pytest.raises(ValueError, match="invalid or unknown fields in changelog"):
        config.load(tmp_path / "config.toml")


def test_archiving_ignores_fenced_release_examples_and_preserves_body_references(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## [1.2.0] - 2026-09-07\n\n- Current.\n\n## [1.1.0](https://example.com/v1.1.0) - 2026-08-01\n\n- Historical [issue].\n\n~~~~markdown\n## [0.1.0]\n- example\n~~~\n~~~~\n\n[issue]: https://example.com/42\n"
    )
    archive = tmp_path / "docs/archives/changelog"
    archive_changelog.archive_changelog(path, archive)
    archived = (archive / "1.1.md").read_text()
    assert "[issue]: https://example.com/42" in archived
    assert "~~~~markdown\n## [0.1.0]\n- example\n~~~\n~~~~" in archived
    assert not (archive / "0.1.md").exists()
    body, source, _heading = changelog.notes(settings(tmp_path), "v1.1.0")
    assert source == archive / "1.1.md"
    assert "[issue]: https://example.com/42" in body
    before = {p: p.read_bytes() for p in (path, archive / "1.1.md")}
    archive_changelog.archive_changelog(path, archive)
    assert before == {p: p.read_bytes() for p in before}


def test_normalization_preserves_all_fenced_prose_and_is_idempotent() -> None:
    code = "~~~markdown\n## [9.9.9]\n\n- feat: scientific <T> *content* (#4)\n\n---\nupdated-dependencies:\n- dependency-name: x\n...\n~~~"
    original = "# Changelog\n\n## [1.0.0]\n\n### Fixed\n\n- fixed: generated body\n\n  - Detail.\n\n" + code + "\n"
    once = postprocess_changelog.postprocess_text(original)
    assert code in once
    assert postprocess_changelog.postprocess_text(once) == once
    assert "\n  - Detail.\n" in once


def test_incremental_archives_retain_older_patches_links_and_introduction(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    archive = tmp_path / "docs/archives/changelog"
    archive.mkdir(parents=True)
    (archive / "1.0.md").write_text("# Changelog - 1.0.x\n\nHand-curated context.\n\n## [1.0.0]\n\n- First patch.\n")
    path.write_text("# Changelog\n\n## [1.1.0]\n\n- Current.\n\n## [1.0.1]\n\n- Fix [method](docs/method.md#proof).\n")
    archive_changelog.archive_changelog(path)
    retained = (archive / "1.0.md").read_text()
    assert "Hand-curated context." in retained
    assert "## [1.0.1]" in retained and "## [1.0.0]" in retained
    assert "[method](../../../docs/method.md#proof)" in retained
    path.write_text("# Changelog\n\n## [1.2.0]\n\n- New.\n\n## [1.1.0]\n\n- Current.\n")
    archive_changelog.archive_changelog(path)
    assert "[1.0.x](docs/archives/changelog/1.0.md)" in path.read_text()
    assert "[1.1.x](docs/archives/changelog/1.1.md)" in path.read_text()
    assert (archive / "1.0.md").read_text() == retained
    before = {p: p.read_bytes() for p in (path, *archive.glob("*.md"))}
    archive_changelog.archive_changelog(path)
    assert before == {p: p.read_bytes() for p in before}


def test_conflicting_retained_release_fails_before_changing_files(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    archive = tmp_path / "docs/archives/changelog/1.0.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# Changelog - 1.0.x\n\n## [1.0.0]\n\n- Original.\n")
    path.write_text("# Changelog\n\n## [1.1.0]\n\n- New.\n\n## [1.0.0]\n\n- Different.\n")
    before = {p: p.read_bytes() for p in (path, archive)}
    with pytest.raises(ValueError, match="conflicting retained release"):
        archive_changelog.archive_changelog(path)
    assert before == {p: p.read_bytes() for p in before}


def test_markdown_relocation_preserves_url_titles_escapes_and_code() -> None:
    text = '[a](docs/a(b).md?q=1#part "title") ![image](<docs/a b.svg>)\n[x]: docs/ref.md "reference title"\n[web](https://example.com) [root](/root) [self](#part)\n`[example](docs/code.md)`\n~~~md\n[example](docs/fenced.md)\n~~~\n'
    expected = (
        text.replace("docs/a(b).md", "../../../docs/a(b).md").replace("docs/a b.svg", "../../../docs/a b.svg").replace("docs/ref.md", "../../../docs/ref.md")
    )
    assert relocate_links(text, "../../..") == expected


def test_final_formatter_validation_preserves_original_on_unfixable_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_bytes(b"# Changelog\r\n\r\n- Existing note\r\n")
    rules = tmp_path / "rumdl.toml"
    rules.write_text("")
    original = path.read_bytes()
    calls = []

    def formatter(executable: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if "--fix" in args:
            return subprocess.CompletedProcess([executable, *args], 0, "# Changelog\n\n- Candidate\n", "")
        raise subprocess.CalledProcessError(1, args, stderr="unfixable Markdown")

    monkeypatch.setattr(postprocess_changelog, "run_safe_command", formatter)
    with pytest.raises(postprocess_changelog.MarkdownFormatError, match="unfixable Markdown"):
        postprocess_changelog.postprocess(path, formatter=rules)
    assert len(calls) == 2
    assert path.read_bytes() == original
    assert set(tmp_path.iterdir()) == {path, rules}


@pytest.fixture
def git_consumer(tmp_path: Path) -> Path:
    run_git_command(["init", "-q"], cwd=tmp_path)
    run_git_command(["config", "user.name", "Test Author"], cwd=tmp_path)
    run_git_command(["config", "user.email", "test@example.invalid"], cwd=tmp_path)
    run_git_command(["config", "commit.gpgsign", "false"], cwd=tmp_path)
    run_git_command(["config", "tag.gpgsign", "false"], cwd=tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname="consumer"\nversion="1.2.0"\n')
    run_git_command(["add", "pyproject.toml"], cwd=tmp_path)
    run_git_command(["commit", "-qm", "feat: add scientific data reader (#7)"], cwd=tmp_path)
    return tmp_path


@pytest.mark.skipif(shutil.which("git-cliff") is None, reason="external git-cliff is required for the native generation integration test")
def test_generate_real_git_history_with_packaged_template(git_consumer: Path) -> None:
    cfg = settings(git_consumer)
    candidate = changelog.generate(cfg, tag="v1.2.0", released="2026-09-07", dry_run=True)
    assert not (git_consumer / "CHANGELOG.md").exists()
    assert "## [1.2.0] - 2026-09-07" in candidate
    assert "scientific data reader" in candidate
    assert "https://github.com/example/research/pull/7" in candidate
    assert changelog.generate(cfg, tag="v1.2.0", released="2026-09-07") == candidate
    assert (git_consumer / "CHANGELOG.md").read_text() == candidate


def test_tag_preserves_utf8_notes_and_force_replaces_atomically(git_consumer: Path) -> None:
    cfg = settings(git_consumer)
    cfg.sections["release"] = {"date-policy": "declared"}
    (git_consumer / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.0] - 2026-09-07\n\n- Preserve β → γ.\n")
    preview = changelog.tag(cfg, "v1.2.0", dry_run=True)
    assert run_git_command(["tag", "--list"], cwd=git_consumer).stdout == ""
    changelog.tag(cfg, "v1.2.0")
    obj = run_git_command(["cat-file", "tag", "v1.2.0"], cwd=git_consumer).stdout
    assert obj.partition("\n\n")[2] == preview
    old = run_git_command(["rev-parse", "refs/tags/v1.2.0"], cwd=git_consumer).stdout
    with pytest.raises(ValueError, match="already exists"):
        changelog.tag(cfg, "v1.2.0")
    (git_consumer / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.0] - 2026-09-07\n\n- Revised β → γ.\n")
    changelog.tag(cfg, "v1.2.0", force=True)
    assert run_git_command(["rev-parse", "refs/tags/v1.2.0"], cwd=git_consumer).stdout != old
