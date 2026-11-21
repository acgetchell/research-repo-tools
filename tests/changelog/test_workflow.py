"""Generation publishes the root and minor archives as one recoverable update."""

import shutil
import subprocess
from dataclasses import replace
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
    return config.parse({"changelog": {"owner": "example", "repository": "consumer"}}, root=tmp_path)


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


@pytest.mark.parametrize("cross_file", [False, True])
def test_heading_fragments_are_numbered_in_their_output_file(consumer, monkeypatch, cross_file):
    archive_link = "[Second archived check](docs/archives/changelog/1.0.md#verify-1)."
    local_link = "[First](#verify), [second](#verify-1)."
    history = (
        "# Changelog\n\n## [2.0.0]\n\n### Added\n\n#### Verify\n\nCurrent check.\n\n"
        f"{archive_link if cross_file else ''}\n\n## [1.0.0]\n\n### Added\n\n{'' if cross_file else local_link}\n\n"
        "#### Verify\n\nFirst archived check.\n\n#### Verify\n\nSecond archived check.\n"
    )
    monkeypatch.setattr(changelog, "_run_cliff", lambda *args, **kwargs: history)
    root = changelog.generate(consumer)
    assert "#### Verify\n\nCurrent check." in root
    archive_path = consumer.root / "docs/archives/changelog/1.0.md"
    archive = archive_path.read_text(encoding="utf-8")
    assert "#### Verify\n\nFirst archived check." in archive
    assert "#### Verify (1)\n\nSecond archived check." in archive
    assert (archive_link in root) if cross_file else (local_link in archive)
    once = snapshot(consumer.root)
    assert changelog.generate(consumer) == root
    assert snapshot(consumer.root) == once


def test_custom_template_retains_closing_directives_in_root_and_archive(consumer, monkeypatch):
    history = HISTORY.replace("Upcoming work.", "Upcoming work.\n\n  Closes #12\n").replace("Earlier correction.", "Earlier correction.\n\n  Closes #13\n")
    (consumer.root / "cliff.toml").write_bytes(b'[changelog]\nbody = "custom"\n')
    consumer = replace(consumer, changelog=replace(consumer.changelog, cliff_config="cliff.toml"))
    monkeypatch.setattr(changelog, "run_safe_command", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, history, ""))
    for _ in range(2):
        assert "Closes #12" in changelog.generate(consumer)
        assert "Closes #13" in (consumer.root / "docs/archives/changelog/0.8.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("dry_run", [False, True])
def test_generation_rejects_archive_conflicts_without_changes(consumer: config.Config, dry_run: bool) -> None:
    archive = consumer.root / "docs/archives/changelog/0.9.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# Retained history\n\n## [0.9.2] - 2026-09-15\n\n- Different notes.\n", newline="\n")
    before = snapshot(consumer.root)
    with pytest.raises(ValueError, match="conflicting retained release"):
        changelog.generate(consumer, dry_run=dry_run)
    assert snapshot(consumer.root) == before


def test_generation_rolls_back_archives_when_root_publication_fails(consumer: config.Config, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = consumer.root / "docs/archives/changelog/0.9.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# Retained history\n\n## [0.9.1]\n\n- Retained patch.\n", newline="\n")
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
    consumer = replace(consumer, changelog=replace(consumer.changelog, formatter="rumdl.toml"))
    formatted: set[Path] = set()

    def format_candidate(text: str, path: Path, _config: Path) -> str:
        formatted.add(path.relative_to(consumer.root))
        if path.name == "0.8.md":
            raise ValueError("archive formatting failed")
        return text

    monkeypatch.setattr(changelog, "fix_markdown", format_candidate)
    before = snapshot(consumer.root)
    with pytest.raises(ValueError, match="archive formatting failed"):
        changelog.generate(consumer)
    assert Path("docs/archives/changelog/0.8.md") in formatted
    assert snapshot(consumer.root) == before
    formatted.clear()
    checked = {}
    monkeypatch.setattr(changelog, "check_markdown", lambda text, path, rules, candidates: checked.update(candidates))
    monkeypatch.setattr(changelog, "fix_markdown", lambda text, path, rules: (formatted.add(path.relative_to(consumer.root)), text)[1])
    changelog.generate(consumer)
    assert {path.relative_to(consumer.root) for path in checked} == formatted
    assert formatted == {Path("CHANGELOG.md"), Path("docs/archives/changelog/0.9.md"), Path("docs/archives/changelog/0.8.md")}


def test_cli_generation_archives_prospective_release(consumer: config.Config, capsys: pytest.CaptureFixture[str]) -> None:
    settings = consumer.root / "research-repo-tools.toml"
    settings.write_text('[changelog]\nowner="example"\nrepository="consumer"\n', newline="\n")
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
        justfile.write_text(changelog.template("justfile"), newline="\n")
    commands = [
        (["changelog"], "changelog generate"),
        (["changelog-preview"], "--dry-run"),
        (["changelog-release", "v1.0.0", "2026-09-17"], "--tag"),
        (["changelog-release", "v1.0.0"], "--tag"),
        (["changelog-archive"], "changelog archive"),
        (["changelog-check"], "changelog check"),
        (["release-notes", "v1.0.0"], "changelog notes"),
        (["tag", "v1.0.0"], "changelog tag"),
        (["tag-preview", "v1.0.0"], "--dry-run"),
    ]
    for arguments, expected in commands:
        result = run_safe_command("just", ["--justfile", str(justfile), "--dry-run", *arguments], cwd=tmp_path)
        assert "research-repo-tools changelog " in result.stderr
        assert expected in result.stderr


@pytest.mark.parametrize("target", ["CHANGELOG.md", "0.9.md"])
@pytest.mark.parametrize("damage", ["drop", "version", "date"])
def test_formatter_cannot_remove_or_change_release_identity(consumer, monkeypatch, target, damage):
    consumer = replace(consumer, changelog=replace(consumer.changelog, formatter="rumdl.toml"))
    # Include a prior archive to prove both existing artifacts survive rejection.
    changelog.generate(replace(consumer, changelog=replace(consumer.changelog, formatter=None)))
    before = snapshot(consumer.root)

    def format_candidate(text, path, _config):
        if path.name != target:
            return text
        if damage == "drop":
            return "# Formatter returned only a title\n"
        if damage == "date":
            return text.replace("2026-09-", "2025-09-")
        return text.replace("[1.0.0]", "[9.0.0]").replace("[0.9.2]", "[0.9.3]")

    monkeypatch.setattr(changelog, "fix_markdown", format_candidate)
    with pytest.raises(ValueError, match="release headings"):
        changelog.generate(consumer)
    assert snapshot(consumer.root) == before


@pytest.mark.parametrize("dry_run", [False, True])
def test_generation_retains_declared_dates_in_root_and_archives(consumer, monkeypatch, dry_run):
    root = consumer.root / "CHANGELOG.md"
    root.write_text(HISTORY.replace("2026-09-16", "2026-08-31").split("## [0.9.2]")[0], newline="\n")
    archive = consumer.root / "docs/archives/changelog/0.9.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# Changelog - 0.9.x\n\n## [0.9.2] - 2026-08-30\n\n### Fixed\n\n- Read the [guide](../../../docs/guide.md).\n", newline="\n")
    before = snapshot(consumer.root)
    preview = changelog.generate(consumer, dry_run=dry_run)
    assert "## [1.0.0] - 2026-08-31" in preview
    if dry_run:
        assert snapshot(consumer.root) == before
    else:
        assert "## [0.9.2] - 2026-08-30" in archive.read_text()
        once = snapshot(consumer.root)
        assert changelog.generate(consumer) == preview
        assert snapshot(consumer.root) == once


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("final_newline", [False, True], ids=["unterminated", "terminated"])
def test_declared_date_insertion_preserves_unicode_prose_and_line_endings(newline, final_newline):
    text = (
        "# Changelog\n\nPreserve pre\u2028amble.\n\n## [1.2.1] - 2000-01-01\n\n"
        "- Earlier body\x85text\u2029continues.\n\n## [1.2.0](https://example.org/v1.2.0) \t" + ("\n" if final_newline else "")
    ).replace("\n", newline)
    expected = text.replace(" - 2000-01-01", " - 2026-09-07").replace("/v1.2.0) \t", "/v1.2.0) - 2026-09-06 \t")
    dates = {"1.2.1": "2026-09-07", "1.2.0": "2026-09-06"}
    assert changelog._preserve_declared_dates(text, dates) == expected
    assert changelog._preserve_declared_dates(expected, dates) == expected


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_generation_keeps_unicode_prose_when_applying_prospective_and_declared_dates(tmp_path, monkeypatch, newline):
    path = tmp_path / "CHANGELOG.md"
    original = "# Changelog\n\n## [1.2.1] - 2026-09-06\n\n- Previous.\n\n## [1.2.0] - 2026-09-05\n\n- First.\n"
    path.write_bytes(original.encode("utf-8"))
    generated = (
        "# Changelog\n\nPreserve pre\u2028amble.\n\n## [1.2.2] - 2000-01-01\n\n"
        "- Latest body\x85text\u2029continues.\n\n## [1.2.1] - 2000-01-02\n\n- Previous.\n\n## [1.2.0]"
    )
    monkeypatch.setattr(changelog, "run_safe_command", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, generated.replace("\n", newline), ""))
    settings = config.parse({"changelog": {"owner": "example", "repository": "consumer"}}, root=tmp_path)
    expected = generated.replace(" - 2000-01-01", " - 2026-09-07").replace(" - 2000-01-02", " - 2026-09-06") + " - 2026-09-05\n"
    assert changelog.generate(settings, tag="v1.2.2", released="2026-09-07", dry_run=True) == expected
    assert path.read_bytes() == original.encode("utf-8")
    assert changelog.generate(settings, tag="v1.2.2", released="2026-09-07") == expected
    assert path.read_bytes() == expected.encode("utf-8")
    assert changelog.generate(settings) == expected
    assert path.read_bytes() == expected.encode("utf-8")


def test_regenerated_declared_date_still_passes_release_metadata_check(tmp_path, monkeypatch):
    from research_repo_tools import release_metadata
    from tests.releases.metadata_support import write_project

    write_project(tmp_path)
    path = tmp_path / "CHANGELOG.md"
    generated = path.read_text().replace("2026-08-04", "2026-08-05")
    monkeypatch.setattr(changelog, "run_safe_command", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, generated, ""))
    settings = config.parse({"changelog": {"owner": "example", "repository": "consumer"}}, root=tmp_path)
    citation = (tmp_path / "CITATION.cff").read_bytes()
    changelog.generate(settings)
    assert release_metadata.check(tmp_path) == 0
    assert (tmp_path / "CITATION.cff").read_bytes() == citation


@pytest.mark.parametrize("authority", ["archive", "prospective"])
def test_conflicting_date_authorities_fail_without_changes(consumer, authority):
    (consumer.root / "CHANGELOG.md").write_text(HISTORY, newline="\n")
    tag = released = None
    if authority == "archive":
        archive = consumer.root / "docs/archives/changelog/0.9.md"
        archive.parent.mkdir(parents=True)
        archive.write_text("# Changelog\n\n## [0.9.2] - 2000-01-01\n\n- Retained.\n", newline="\n")
    else:
        tag, released = "v1.0.0", "2000-01-01"
    before = snapshot(consumer.root)
    with pytest.raises(ValueError, match="conflict.*date"):
        changelog.generate(consumer, tag=tag, released=released)
    assert snapshot(consumer.root) == before


def test_generation_retains_date_after_prospective_release_is_tagged(consumer):
    first = changelog.generate(consumer, tag="v1.0.0", released="2026-08-31")
    before = snapshot(consumer.root)
    # Producer returns its original Git-derived date on later ordinary runs.
    assert changelog.generate(consumer) == first
    assert snapshot(consumer.root) == before


@pytest.mark.parametrize(
    "formatted", [False, "stub", pytest.param("rumdl", marks=pytest.mark.skipif(shutil.which("rumdl") is None, reason="external rumdl required"))]
)
def test_formatted_regeneration_and_next_release_are_stable(consumer, monkeypatch, formatted):
    # List-marker conversion and prose wrapping reproduce the comparison across
    # raw generated blocks and formatter-produced retained archives.
    history = HISTORY.replace("docs/guide.md", r"docs/guide\(notes\)%23v1.md\#guide").replace(
        "Earlier correction.", " ".join(["Long historical explanation."] * 12)
    )
    history = history.replace(
        "- Read the [guide]",
        "- Outer\n  - Inner\n    - Use [data](docs/first&amp;last.csv).\n      Continue with the [data](docs/first&#38;last.csv).\n\n- Read the [guide]",
    )
    monkeypatch.setattr(changelog, "run_safe_command", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, history, ""))
    if formatted:
        consumer = replace(consumer, changelog=replace(consumer.changelog, formatter="rumdl.toml"))
        (consumer.root / "docs").mkdir()
        (consumer.root / "docs/guide(notes)#v1.md").write_bytes(b"# Guide\n")
        (consumer.root / "docs/first&last.csv").write_bytes(b"x,y\n")
        (consumer.root / "rumdl.toml").write_text(changelog.template("rumdl.toml") + '\n[MD004]\nstyle = "asterisk"\n', newline="\n")
    if formatted == "stub":
        monkeypatch.setattr(changelog, "check_markdown", lambda *args: None)
        monkeypatch.setattr(changelog, "fix_markdown", lambda text, path, rules: text.replace("\n- ", "\n* "))
    if formatted == "rumdl":
        guide = consumer.root / "docs/guide(notes)#v1.md"
        guide.unlink()
        without_guide = snapshot(consumer.root)
        with pytest.raises(RuntimeError, match="MD057"):
            changelog.generate(consumer)
        assert snapshot(consumer.root) == without_guide
        guide.write_bytes(b"# Guide\n")
    changelog.generate(consumer)
    before = snapshot(consumer.root)
    changelog.generate(consumer)
    assert snapshot(consumer.root) == before
    if formatted:
        assert "* Long historical" in (consumer.root / "docs/archives/changelog/0.8.md").read_text()
    history = history.replace("## [Unreleased]", "## [1.1.0] - 2026-09-18")
    changelog.generate(consumer, tag="v1.1.0", released="2026-09-18")
    assert (consumer.root / "docs/archives/changelog/1.0.md").exists()
    after_release = snapshot(consumer.root)
    changelog.generate(consumer)
    assert snapshot(consumer.root) == after_release
    history = history.replace("Current series.", "Conflicting history.")
    with pytest.raises(ValueError, match="conflicting retained release"):
        changelog.generate(consumer)
    assert snapshot(consumer.root) == after_release
