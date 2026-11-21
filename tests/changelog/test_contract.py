"""Independent common contracts, including real Git mutations in disposable repos."""

import re
import shutil
import subprocess
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from research_repo_tools import archive_changelog, changelog, changelog_issues, cli, config, postprocess_changelog, update_release
from research_repo_tools.markdown import relocate_links
from research_repo_tools.process import run_git_command, run_safe_command


def settings(root: Path, **changes: object) -> config.Config:
    return config.parse({"changelog": {"owner": "example", "repository": "research", **changes}}, root=root)


@pytest.mark.parametrize("separator", ["\x85", "\u2028", "\u2029"], ids=["nel", "line-separator", "paragraph-separator"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("final_newline", [False, True], ids=["unterminated", "terminated"])
def test_sync_changelog_date_preserves_unicode_prose_and_exact_bytes(tmp_path, separator, newline, final_newline):
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('[project]\nname="consumer"\nversion="1.2.0"\n', encoding="utf-8", newline="\n")
    citation = tmp_path / "CITATION.cff"
    citation.write_text("cff-version: 1.2.0\nversion: 1.2.0\ndate-released: 2026-09-07\n", encoding="utf-8", newline="\n")
    path = tmp_path / "CHANGELOG.md"
    text = (
        f"# Changelog\n\nPreserve pre{separator}amble.\n\n## [Unreleased]\n\n- Earlier body{separator}text.\n\n"
        "## [1.2.0](https://example.org/v1.2.0) - 2000-01-01\n\n- Target notes." + ("\n" if final_newline else "")
    ).replace("\n", newline)
    path.write_bytes(text.encode("utf-8"))
    expected = text.replace(" - 2000-01-01", " - 2026-09-07").encode("utf-8")
    authorities = {authority: authority.read_bytes() for authority in (manifest, citation)}
    args = ["v1.2.0", "--repo-root", str(tmp_path), "--sync-changelog-date"]
    assert update_release.main(args) == 0
    assert path.read_bytes() == expected
    assert update_release.main(args) == 0
    assert path.read_bytes() == expected
    assert {authority: authority.read_bytes() for authority in authorities} == authorities


@pytest.mark.parametrize("candidate", ["# Changelog\n", "## [9.0.0] - 2026-09-07\n", "## [1.2.3] - 2025-09-07\n"])
def test_normalize_rejects_changed_release_identity_before_publication(tmp_path, monkeypatch, capsys, candidate):
    (tmp_path / "pyproject.toml").write_text('[tool.research-repo-tools.changelog]\nformatter="rumdl.toml"\n', newline="\n")
    (tmp_path / "rumdl.toml").write_text("[global]\n", newline="\n")
    path = tmp_path / "CHANGELOG.md"
    original = b"# Changelog\r\n\r\n## [1.2.3] - 2026-09-07\r\n\r\n- Retained notes.\r\n"
    path.write_bytes(original)
    before = {item.name: item.read_bytes() for item in tmp_path.iterdir()}
    monkeypatch.setattr(
        postprocess_changelog,
        "run_safe_command",
        lambda _command, args, **_kwargs: subprocess.CompletedProcess([], 0, candidate if "--fix" in args else "", ""),
    )
    assert cli.main(["--root", str(tmp_path), "changelog", "normalize"]) == 1
    assert "release headings" in capsys.readouterr().err
    assert {item.name: item.read_bytes() for item in tmp_path.iterdir()} == before


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


@pytest.mark.skipif(shutil.which("rumdl") is None, reason="external rumdl required")
def test_packaged_policy_detects_duplicate_headings_and_unused_references(tmp_path: Path) -> None:
    rules = tmp_path / "rumdl.toml"
    rules.write_text(changelog.template("rumdl.toml"), encoding="utf-8", newline="\n")
    source = "# Changelog\n\n## Release\n\n### Duplicate\n\nBody.\n\n### Duplicate\n\n[unused]: https://example.com\n"
    result = run_safe_command(
        "rumdl", ["check", "--config", str(rules), "--no-cache", "--stdin", "--stdin-filename", "CHANGELOG.md"], input=source, check=False
    )
    assert result.returncode == 1
    assert "[MD024]" in result.stdout and "[MD053]" in result.stdout


@pytest.mark.parametrize("component", ['x"\n[malicious]', "../x", "x/y", "..", "https://example.com"])
def test_template_rejects_interpolation_and_path_syntax(component: str) -> None:
    with pytest.raises(ValueError):
        changelog.template("cliff.toml", owner=component, repository="repo")


@pytest.mark.parametrize(
    "tag,accepted",
    [(tag, True) for tag in ("v0.0.0", "v1.2.3", "v1.2.3-rc.1+build.007", "v1.2.3-0", "v1.2.3-01a", "v1.2.3+01")]
    + [
        (tag, False)
        for tag in (
            "preview",
            "dev",
            "v",
            "v1",
            "v1.2",
            "v01.2.3",
            "1.2.3",
            "release-v1.2.3",
            "v1.2.3junk",
            "v1.2.3-01",
            "v1.2.3-rc..1",
            "v1.2.3+build..1",
            "v1.2.3\n",
            "v1.2.3-junk!",
            "v1.2.3-β",
        )
    ],
)
def test_template_selects_only_complete_semver_tags(tag: str, accepted: bool) -> None:
    pattern = tomllib.loads(changelog.template("cliff.toml"))["git"]["tag_pattern"]
    assert (re.search(pattern, tag) is not None) is accepted


def test_changelog_filename_is_a_shared_convention(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "config.toml").write_text("[changelog]\n", newline="\n")
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [1.2.0] - 2026-09-07\n\n- Note with [reference].\n\n[reference]: https://example.com/issue\n", newline="\n"
    )
    assert cli.main(["--config", str(tmp_path / "config.toml"), "changelog", "notes", "v1.2.0"]) == 0
    assert capsys.readouterr().out == "- Note with [reference].\n\n[reference]: https://example.com/issue\n"
    (tmp_path / "config.toml").write_text('[changelog]\npath="CUSTOM.md"\n', newline="\n")
    with pytest.raises(ValueError, match="invalid or unknown fields in changelog"):
        config.load(tmp_path / "config.toml")


def test_archiving_ignores_fenced_release_examples_and_preserves_body_references(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## [1.2.0] - 2026-09-07\n\n- Current.\n\n## [1.1.0](https://example.com/v1.1.0) - 2026-08-01\n\n- Historical [issue].\n\n~~~~markdown\n## [0.1.0]\n- example\n~~~\n~~~~\n\n[issue]: https://example.com/42\n",
        newline="\n",
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
    assert "#### fixed: generated body\n\n- Detail.\n" in once


def test_incremental_archives_retain_older_patches_links_and_introduction(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    archive = tmp_path / "docs/archives/changelog"
    archive.mkdir(parents=True)
    (archive / "1.0.md").write_text(
        "# Changelog - 1.0.x\n\nHand-curated [context].\n\n## [1.0.0]\n\n- First patch.\n\n[context]: https://example.com/guide\n", newline="\n"
    )
    path.write_text("# Changelog\n\n## [1.1.0]\n\n- Current.\n\n## [1.0.1]\n\n- Fix [method](docs/method.md#proof).\n", newline="\n")
    archive_changelog.archive_changelog(path)
    retained = (archive / "1.0.md").read_text()
    assert "Hand-curated [context]." in retained
    assert "[context]: https://example.com/guide" in retained
    assert "## [1.0.1]" in retained and "## [1.0.0]" in retained
    assert "[method](../../../docs/method.md#proof)" in retained
    path.write_text("# Changelog\n\n## [1.2.0]\n\n- New.\n\n## [1.1.0]\n\n- Current.\n", newline="\n")
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
    archive.write_text("# Changelog - 1.0.x\n\n## [1.0.0]\n\n- Original.\n", newline="\n")
    path.write_text("# Changelog\n\n## [1.1.0]\n\n- New.\n\n## [1.0.0]\n\n- Different.\n", newline="\n")
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
    rules.write_text("", newline="\n")
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


@pytest.mark.parametrize("output", ["# Unexpected producer output\n", "# Changelog\n\n## [Unreleased\n", "# Changelog\n\n## [1.2.03]\n"])
def test_successful_producer_cannot_replace_history_with_invalid_output(tmp_path, monkeypatch, output):
    path = tmp_path / "CHANGELOG.md"
    original = b"# Changelog\r\n\r\n## [1.0.0]\r\n\r\n- Existing history.\r\n"
    path.write_bytes(original)
    monkeypatch.setattr(changelog, "run_safe_command", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, output, ""))
    with pytest.raises(ValueError):
        changelog.generate(settings(tmp_path))
    assert path.read_bytes() == original


def test_generator_accepts_an_unreleased_only_project(tmp_path, monkeypatch):
    output = "# Changelog\n\n## [Unreleased]\n\n- Initial work.\n"
    monkeypatch.setattr(changelog, "run_safe_command", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, output, ""))
    assert changelog.generate(settings(tmp_path)) == output


def test_prospective_generation_dates_linked_heading_without_rewriting_example(tmp_path, monkeypatch):
    example = "```markdown\n## [1.2.0] - 1999-01-01\n```"
    output = f"# Changelog\n\n{example}\n\n## [1.2.0](https://example.org/v1.2.0) - 2000-01-01\n\n- Release.\n"
    monkeypatch.setattr(changelog, "run_safe_command", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, output, ""))
    result = changelog.generate(settings(tmp_path), tag="v1.2.0", released="2026-09-07")
    assert example in result
    assert "## [1.2.0](https://example.org/v1.2.0) - 2026-09-07" in result


@pytest.mark.parametrize("version", ["1٢.2.3", "1.2.3-1٢"])
def test_invalid_unicode_versions_cannot_publish_archives(tmp_path, version):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(f"# Changelog\n\n## [2.0.0]\n\n- Current.\n\n## [{version}]\n\n- Invalid.\n", newline="\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unrecognized changelog heading"):
        archive_changelog.archive_changelog(path)
    assert path.read_bytes() == before
    assert not (tmp_path / "docs").exists()


@pytest.fixture
def git_consumer(tmp_path: Path, git_mutations_allowed: None) -> Path:
    run_git_command(["init", "-q"], cwd=tmp_path)
    run_git_command(["config", "user.name", "Test Author"], cwd=tmp_path)
    run_git_command(["config", "user.email", "test@example.invalid"], cwd=tmp_path)
    run_git_command(["config", "commit.gpgsign", "false"], cwd=tmp_path)
    run_git_command(["config", "tag.gpgsign", "false"], cwd=tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname="consumer"\nversion="1.2.0"\n', newline="\n")
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


@pytest.mark.skipif(shutil.which("git-cliff") is None, reason="external git-cliff is required for the native generation integration test")
def test_backfill_links_original_commits_in_retained_and_current_releases(git_consumer: Path) -> None:
    cfg = settings(git_consumer)
    body = (
        "Closes #11\nCloses #12\nCloses #13\n\n"
        "Fixes EXAMPLE/research#11, resolves other/tool#14.\n"
        "Closed: https://github.com/other/tool/issues/14\n"
        "Refs #15\n> Fixes #16\n    Fixes #17\n\n~~~~text\nCloses #18\n~~~~\n\n"
        "Example ``first\nResolves #20\nlast``.\n\nAn unmatched ` delimiter.\n\nFixes #21\n\n    ~~~text\n\nFixes #22"
    )
    run_git_command(["commit", "--allow-empty", "-qm", "Ship\u2028reporting (#42)", "-m", body], cwd=git_consumer)
    oid = run_git_command(["rev-parse", "HEAD"], cwd=git_consumer).stdout.strip()
    reference = f"See the original [`{oid[:7]}`](https://github.com/example/research/commit/{oid})"
    run_git_command(["commit", "--allow-empty", "-qm", "Add a usage reference (#45)", "-m", f"* docs: related work\n\n- {reference}"], cwd=git_consumer)
    run_git_command(["tag", "v1.0.0"], cwd=git_consumer)
    run_git_command(["commit", "--allow-empty", "-qm", "fix: keep reports readable (#43)", "-m", "Fixed #19"], cwd=git_consumer)
    current = changelog.generate(cfg, tag="v1.1.0", released="2026-09-07")
    assert current.count("[#19](https://github.com/example/research/issues/19)") == 1
    changelog.check(cfg)
    assert "      ~~~text" in changelog.notes(cfg, "v1.0.0")[0]
    archive = git_consumer / "docs/archives/changelog/1.0.md"
    original = archive.read_bytes()
    assert b"- See the original" in original
    note = re.search(rb"^  Closes \[.*?\)\.$", original, flags=re.MULTILINE | re.DOTALL)
    assert note is not None
    for number in (11, 12, 13, 21, 22):
        assert original.count(f"[#{number}](https://github.com/example/research/issues/{number})".encode()) == 1
    assert original.count(b"[other/tool#14](https://github.com/other/tool/issues/14)") == 1
    assert b"Fixes EXAMPLE/research#11" not in original and b"Closed: https://github.com/other/tool/issues/14" not in original
    for number in (15, 16, 17, 18, 19, 20):
        assert f"/issues/{number})".encode() not in original
    archive.write_bytes(original[: note.start()] + original[note.end() + 1 :])
    assert changelog.generate(cfg, tag="v1.1.0", released="2026-09-07") == current
    assert archive.read_bytes() == original
    damaged = original.replace(b"/issues/13)", b"/issues/123)")
    archive.write_bytes(damaged)
    with pytest.raises(ValueError, match="conflicting"):
        changelog.generate(cfg, tag="v1.1.0", released="2026-09-07")
    assert archive.read_bytes() == damaged


@pytest.mark.skipif(shutil.which("git-cliff") is None, reason="external git-cliff required")
def test_generation_recovers_title_only_archive_from_the_same_history(git_consumer: Path) -> None:
    cfg = settings(git_consumer)
    run_git_command(["commit", "--allow-empty", "-qm", "Ship reporting (#42)", "-m", "Record café observations."], cwd=git_consumer)
    run_git_command(["commit", "--allow-empty", "-qm", "fix: read reports", "-m", "Read samples.\nCloses #31\nPreserve metadata."], cwd=git_consumer)
    run_git_command(["tag", "v1.0.0"], cwd=git_consumer)
    run_git_command(["commit", "--allow-empty", "-qm", "fix: next release"], cwd=git_consumer)
    changelog.generate(cfg, tag="v1.1.0", released="2026-09-07")
    archive = git_consumer / "docs/archives/changelog/1.0.md"
    current = archive.read_text(encoding="utf-8").replace("# Changelog - 1.0.x\n", "# Changelog - 1.0.x\n\nRetained introduction.\n")
    assert current.count("Record café observations.") == current.count("Preserve metadata.") == 1
    link = "  Closes [#31](https://github.com/example/research/issues/31).\n"
    assert current.count(link) == 1 and "  Closes #31\n" not in current
    title_only = current.replace("\n  Record café observations.\n", "").replace(link, "").replace("  Read samples.\n", "  Read samples.\n  Closes #31\n")
    assert "Record café observations." not in title_only and "  Closes #31\n" in title_only
    archive.write_text(title_only, encoding="utf-8", newline="\n")
    changelog.generate(cfg, tag="v1.1.0", released="2026-09-07", dry_run=True)
    assert archive.read_text(encoding="utf-8") == title_only
    for _ in range(2):
        changelog.generate(cfg, tag="v1.1.0", released="2026-09-07")
        assert archive.read_text(encoding="utf-8") == current
    for changed in (
        current.replace("Record café observations.", "Hand-edited."),
        title_only.replace("Preserve metadata.", "Hand-edited."),
        title_only.replace("Closes #31", "Closes #99"),
    ):
        archive.write_text(changed, encoding="utf-8", newline="\n")
        with pytest.raises(ValueError, match="conflicting retained release"):
            changelog.generate(cfg, tag="v1.1.0", released="2026-09-07")
        assert archive.read_text(encoding="utf-8") == changed


@pytest.mark.parametrize("separator", ["", "\n", "\n\n"])
def test_wrapped_first_issue_link_is_idempotent_and_rejects_drift(separator: str) -> None:
    oid = "a" * 40
    remote = "example/" + "long-name-" * 9 + "tools"
    note = f"  Closes\n  [{remote}#7](https://github.com/{remote}/issues/7)."
    notes = {oid: note}
    entry = f"- Report [`{oid[:7]}`](https://github.com/example/research/commit/{oid})\n"
    annotated = changelog_issues.add_closing_notes(entry, notes, "example/research")
    assert annotated == entry + note + "\n"
    annotated = entry + separator + note + "\n"
    assert changelog_issues.add_closing_notes(annotated, notes, "example/research") == annotated
    with pytest.raises(ValueError, match="conflicting closed issue links"):
        changelog_issues.add_closing_notes(annotated.replace("/issues/7)", "/issues/8)"), notes, "example/research")


def test_strip_only_directives_with_replacement_links_for_that_entry() -> None:
    first, second = "a" * 40, "b" * 40
    notes = {first: "  Closes [#7](https://github.com/example/research/issues/7)."}
    text = (
        f"- First [`{first[:7]}`](https://github.com/example/research/commit/{first})\n\n"
        "  Fixes #7\n  Closes #7, #8\n  Closes #9\n\n"
        f"- Second [`{second[:7]}`](https://github.com/example/research/commit/{second})\n\n  Closes #7\n"
    )
    assert changelog_issues.strip_linked_directives(text, notes, "example/research") == text.replace("  Fixes #7\n", "")
    summary = "### ⚠️ Breaking Changes\n\n- Changed API.\n  Fixes #7\n\n### Changed\n\n"
    history = "## [Unreleased]\n\n" + summary + text + "\n## [1.0.0]\n\n" + summary + "- Historical detail.\n"
    before, after = changelog_issues.strip_linked_directives(history, notes, "example/research").split("## [1.0.0]")
    assert "  Fixes #7\n" not in before and "  Fixes #7\n" in after


@pytest.mark.parametrize("link", ["[Second](CHANGELOG.md#verify-1)", "[Second][again]\n\n[again]: ./CHANGELOG.md#verify-1"])
def test_path_qualified_heading_navigation_survives_regeneration(link: str) -> None:
    source = f"# Changelog\n\n## [Unreleased]\n\n### Added\n\n{link}\n\n#### Verify\n\nFirst.\n\n#### Verify\n\nSecond.\n"
    result = postprocess_changelog.postprocess_text(source)
    assert "#### Verify (1)\n\nSecond." in result
    assert link in result
    assert postprocess_changelog.postprocess_text(result) == result


def test_reference_heading_reserves_the_rendered_fragment() -> None:
    source = "# Changelog\n\n## [Unreleased]\n\n### Added\n\n[Second](#verify-2)\n\n#### Verify-1\n\n#### [Verify][v]\n\nFirst.\n\n#### [Verify][v]\n\nSecond.\n\n[v]: https://example.com\n"
    result = postprocess_changelog.postprocess_text(source)
    assert "#### [Verify][v] (2)\n\nSecond." in result
    assert postprocess_changelog.postprocess_text(result) == result


@pytest.mark.skipif(shutil.which("rumdl") is None, reason="external rumdl validates the generated heading targets")
def test_repeated_headings_preserve_local_navigation(tmp_path: Path) -> None:
    source = (
        "# Changelog\n\n## [Unreleased]\n\n### Added\n\n[First](#verify), [second][again], [reserved](#verify-1), and [later](#verify-2-1).\n\n"
        "#### Verify-1\n\nReserved.\n\n#### Verify\n\nFirst details.\n\n#### Verify\n\nSecond details.\n\n#### Verify (2)\n\nLater details.\n\n[again]: #verify-2\n"
    )
    result = postprocess_changelog.postprocess_text(source)
    assert "#### Verify\n\nFirst details." in result
    assert "#### Verify (2)\n\nSecond details." in result
    assert postprocess_changelog.postprocess_text(result) == result
    rules = tmp_path / "rules.toml"
    rules.write_text(changelog.template("rumdl.toml"), encoding="utf-8", newline="\n")
    postprocess_changelog.check_markdown(result, tmp_path / "CHANGELOG.md", rules)


def test_formatted_heading_collisions_use_rendered_text() -> None:
    source = (
        "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n[Third](#verify-2) and [identifier](#raw_name).\n\n"
        "#### Verify\n\nFirst.\n\n#### _Verify_\n\nSecond.\n\n#### _Verify_\n\nThird.\n\n#### `raw_name`\n\nLiteral underscore.\n"
    )
    result = postprocess_changelog.postprocess_text(source)
    assert "#### _Verify_ (1)\n\nSecond." in result
    assert "#### _Verify_ (2)\n\nThird." in result
    assert "#### `raw_name`\n" in result
    assert postprocess_changelog.postprocess_text(result) == result


def test_tag_preserves_utf8_notes_and_force_replaces_atomically(git_consumer: Path) -> None:
    cfg = settings(git_consumer)
    cfg = replace(cfg, release=config.ReleasePolicy(date_policy="declared"))
    (git_consumer / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.0] - 2026-09-07\n\n- Preserve β → γ.\n", newline="\n")
    preview = changelog.tag(cfg, "v1.2.0", dry_run=True)
    assert run_git_command(["tag", "--list"], cwd=git_consumer).stdout == ""
    changelog.tag(cfg, "v1.2.0")
    obj = run_git_command(["cat-file", "tag", "v1.2.0"], cwd=git_consumer).stdout
    assert obj.partition("\n\n")[2] == preview
    old = run_git_command(["rev-parse", "refs/tags/v1.2.0"], cwd=git_consumer).stdout
    with pytest.raises(ValueError, match="already exists"):
        changelog.tag(cfg, "v1.2.0")
    (git_consumer / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.0] - 2026-09-07\n\n- Revised β → γ.\n", newline="\n")
    changelog.tag(cfg, "v1.2.0", force=True)
    assert run_git_command(["rev-parse", "refs/tags/v1.2.0"], cwd=git_consumer).stdout != old
