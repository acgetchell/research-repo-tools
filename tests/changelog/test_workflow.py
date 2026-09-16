"""Generation publishes the root and minor archives as one recoverable update."""

import subprocess
from pathlib import Path

import pytest

from research_repo_tools import changelog, cli, config, files
from research_repo_tools.process import run_safe_command

HISTORY = """# Changelog

## [Unreleased]

### Added

- Upcoming work.

## [1.0.0] - 2026-09-16

### Added

- Current series.

## [0.9.2] - 2026-09-15

### Fixed

- Read the [guide](docs/guide.md).

## [0.8.1] - 2026-09-14

### Fixed

- Earlier correction.
"""


@pytest.fixture
def consumer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> config.Config:
    (tmp_path / "CHANGELOG.md").write_bytes(b"# Changelog\r\n\r\n## [Unreleased]\r\n\r\n- Existing notes.\r\n")
    monkeypatch.setattr(changelog, "run_safe_command", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, HISTORY, ""))
    return config.Config(tmp_path, {"changelog": {"owner": "example", "repository": "consumer"}})


def snapshot(root: Path) -> dict[Path, bytes | None]:
    return {path.relative_to(root): path.read_bytes() if path.is_file() else None for path in root.rglob("*")}


def test_generation_rotates_minor_series_and_preview_matches_publication(consumer: config.Config) -> None:
    before = snapshot(consumer.root)
    preview = changelog.generate(consumer, dry_run=True)
    assert snapshot(consumer.root) == before
    assert "## [Unreleased]" in preview and "## [1.0.0]" in preview
    assert "## [0.9.2]" not in preview and "## [0.8.1]" not in preview
    assert "[0.9.x](docs/archives/changelog/0.9.md)" in preview
    assert changelog.generate(consumer) == preview
    assert (consumer.root / "CHANGELOG.md").read_text() == preview
    archives = consumer.root / "docs/archives/changelog"
    assert {path.name for path in archives.iterdir()} == {"0.9.md", "0.8.md"}
    assert "[guide](../../../docs/guide.md)" in (archives / "0.9.md").read_text()
    assert "## [0.8.1]" in (archives / "0.8.md").read_text()
    assert changelog.notes(consumer, "v0.9.2")[1] == archives / "0.9.md"
    once = snapshot(consumer.root)
    assert changelog.generate(consumer) == preview
    assert snapshot(consumer.root) == once


@pytest.mark.parametrize("dry_run", [False, True])
def test_generation_rejects_archive_conflicts_without_changes(consumer: config.Config, dry_run: bool) -> None:
    archive = consumer.root / "docs/archives/changelog/0.9.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# Retained history\n\n## [0.9.2] - 2026-09-15\n\n- Different notes.\n")
    before = snapshot(consumer.root)
    with pytest.raises(ValueError, match="conflicting retained release"):
        changelog.generate(consumer, dry_run=dry_run)
    assert snapshot(consumer.root) == before


def test_generation_rolls_back_archives_when_root_publication_fails(consumer: config.Config, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = consumer.root / "docs/archives/changelog/0.9.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# Retained history\n\n## [0.9.1]\n\n- Retained patch.\n")
    before = snapshot(consumer.root)
    replace_path = files._replace_path
    published: list[Path] = []

    def fail_root(source: Path, target: Path) -> None:
        if target == consumer.root / "CHANGELOG.md":
            raise OSError("root publication failed")
        replace_path(source, target)
        published.append(target)

    monkeypatch.setattr(files, "_replace_path", fail_root)
    with pytest.raises(OSError, match="root publication failed"):
        changelog.generate(consumer)
    assert archive in published
    assert snapshot(consumer.root) == before


def test_generation_formats_every_output_before_publication(consumer: config.Config, monkeypatch: pytest.MonkeyPatch) -> None:
    consumer.sections["changelog"]["formatter"] = "rumdl.toml"
    formatted: set[Path] = set()

    def format_candidate(text: str, path: Path, _config: Path) -> str:
        formatted.add(path.relative_to(consumer.root))
        if path.name == "0.8.md":
            raise ValueError("archive formatting failed")
        return text

    monkeypatch.setattr(changelog, "format_markdown", format_candidate)
    before = snapshot(consumer.root)
    with pytest.raises(ValueError, match="archive formatting failed"):
        changelog.generate(consumer)
    assert Path("docs/archives/changelog/0.8.md") in formatted
    assert snapshot(consumer.root) == before
    formatted.clear()
    monkeypatch.setattr(changelog, "format_markdown", lambda text, path, rules: (formatted.add(path.relative_to(consumer.root)), text)[1])
    changelog.generate(consumer)
    assert formatted == {Path("CHANGELOG.md"), Path("docs/archives/changelog/0.9.md"), Path("docs/archives/changelog/0.8.md")}


def test_cli_generation_archives_prospective_release(consumer: config.Config, capsys: pytest.CaptureFixture[str]) -> None:
    settings = consumer.root / "research-repo-tools.toml"
    settings.write_text('[changelog]\nowner="example"\nrepository="consumer"\n')
    args = ["--config", str(settings), "changelog", "generate", "--tag", "v1.0.0", "--date", "2026-09-17"]
    before = snapshot(consumer.root)
    assert cli.main([*args, "--dry-run"]) == 0
    preview = capsys.readouterr().out
    assert "## [1.0.0] - 2026-09-17" in preview
    assert "## [0.9.2]" not in preview
    assert snapshot(consumer.root) == before
    assert cli.main(args) == 0
    assert (consumer.root / "CHANGELOG.md").read_text() == preview
    assert (consumer.root / "docs/archives/changelog/0.9.md").is_file()


@pytest.mark.parametrize("consumer_template", [False, True], ids=["maintainer", "consumer"])
def test_changelog_recipes_expose_the_common_command_surface(tmp_path: Path, consumer_template: bool) -> None:
    justfile = Path(__file__).resolve().parents[2] / "justfile"
    if consumer_template:
        justfile = tmp_path / "justfile"
        justfile.write_text(changelog.template("justfile"))
    commands = [
        (["changelog"], "changelog generate"),
        (["changelog-preview"], "--dry-run"),
        (["changelog-release", "v1.0.0", "2026-09-17"], "--tag"),
        (["changelog-unreleased", "v1.0.0", "2026-09-17"], "--date"),
        (["changelog-archive"], "changelog archive"),
        (["release-notes", "v1.0.0"], "changelog notes"),
        (["tag", "v1.0.0"], "changelog tag"),
        (["tag-force", "v1.0.0"], "--force"),
    ]
    for arguments, expected in commands:
        result = run_safe_command("just", ["--justfile", str(justfile), "--dry-run", *arguments], cwd=tmp_path)
        assert expected in result.stderr
