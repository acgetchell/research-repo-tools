"""Extraction validates requested notes; explicit checking validates all history."""

from pathlib import Path

import pytest

from research_repo_tools import changelog, cli, config

TARGET = "## [1.2.0] - 2026-09-18\n\n- Preserve this [reference].\n"
DEFINITION = "\n[reference]: https://example.org/guide\n"


@pytest.fixture
def consumer(tmp_path: Path) -> config.Config:
    return config.parse({}, root=tmp_path)


@pytest.mark.parametrize(
    "history",
    [
        "## [1.0.0]\n\n- Older.\n\n## [1.1.0]\n\n- Misordered.\n",
        "## [1.0.invalid]\n\n- Malformed historical version.\n",
        "## [1.0.0] - 2026-02-30\n\n- Invalid historical date.\n",
        "## [1.0.0]\n\n- Older.\n\n## [1.0.0]\n\n- Duplicate history.\n",
        "## [0.1.0\n\n- Missing bracket but clear level-two boundary.\n",
    ],
)
def test_unrelated_history_does_not_block_notes_but_fails_full_check(consumer, history):
    path = consumer.root / "CHANGELOG.md"
    path.write_text("# Changelog\n\n" + TARGET + "\n" + history + DEFINITION)
    before = path.read_bytes()
    assert changelog.notes(consumer, "v1.2.0") == ("- Preserve this [reference].\n" + DEFINITION, path, TARGET.splitlines()[0])
    with pytest.raises(ValueError):
        changelog.check(consumer)
    assert path.read_bytes() == before


@pytest.mark.parametrize("location", ["same", "archive", "after_archives"])
def test_duplicate_target_is_ambiguous_even_when_identical(consumer, location):
    path = consumer.root / "CHANGELOG.md"
    text = "# Changelog\n\n" + TARGET
    if location == "same":
        text += "\n" + TARGET
    elif location == "after_archives":
        text += "\n## Archives\n\n" + TARGET
    else:
        archive = consumer.root / "docs/archives/changelog/1.2.md"
        archive.parent.mkdir(parents=True)
        archive.write_text(text + DEFINITION)
    path.write_text(text + DEFINITION)
    with pytest.raises(ValueError, match="Duplicate"):
        changelog.notes(consumer, "v1.2.0")


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_fenced_release_examples_and_definitions_are_literal(consumer, fence):
    path = consumer.root / "CHANGELOG.md"
    example = f"{fence}markdown\n{TARGET}\n[reference]: https://example.org/example\n{fence}\n"
    path.write_text("# Changelog\n\n" + TARGET + "\n" + example + DEFINITION)
    body, _path, _heading = changelog.notes(consumer, "v1.2.0")
    assert example in body
    assert body.endswith(DEFINITION)
    changelog.check(consumer)


@pytest.mark.parametrize(
    "defect",
    ["\n```markdown\n## [0.1.0]\n", "\n##[0.1.0]\n\n- Uncertain boundary.\n"],
)
def test_ambiguous_boundaries_fail(consumer, defect):
    (consumer.root / "CHANGELOG.md").write_text("# Changelog\n\n" + TARGET + defect)
    with pytest.raises(ValueError, match="ambiguous"):
        changelog.notes(consumer, "v1.2.0")


@pytest.mark.parametrize("heading", ["## [1.2.0] - invalid", "## [1.2.0] - 2026-02-30", "## [v1.2.0", "## 1.2.0"])
def test_invalid_target_headings_fail(consumer, heading):
    (consumer.root / "CHANGELOG.md").write_text(f"# Changelog\n\n{heading}\n\n- Body.\n")
    with pytest.raises(ValueError, match="heading|date"):
        changelog.notes(consumer, "v1.2.0")


@pytest.mark.parametrize("required", [False, True])
def test_only_required_reference_conflicts_block_extraction(consumer, required):
    label = "reference" if required else "unrelated"
    path = consumer.root / "CHANGELOG.md"
    path.write_text(f"# Changelog\n\n[{label}]: https://example.org/one\n\n{TARGET}{DEFINITION}\n[{label}]: https://example.org/two\n")
    if required:
        with pytest.raises(ValueError, match="Conflicting reference"):
            changelog.notes(consumer, "v1.2.0")
    else:
        assert changelog.notes(consumer, "v1.2.0")[0] == "- Preserve this [reference].\n" + DEFINITION
    with pytest.raises(ValueError, match="Conflicting reference"):
        changelog.check(consumer)


def test_exact_semver_and_archive_lookup(consumer):
    (consumer.root / "CHANGELOG.md").write_text("# Changelog\n\n## [2.0.0]\n\n- New.\n")
    archive = consumer.root / "docs/archives/changelog/1.2.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# Archive\n\n## [1.2.0-rc.1+build.7]\n\n- Exact.\n\n## [1.2.0-rc.1]\n\n- Other.\n")
    assert changelog.notes(consumer, "v1.2.0-rc.1+build.7")[:2] == ("- Exact.\n", archive)
    with pytest.raises(ValueError, match="not found"):
        changelog.notes(consumer, "v1.2.0")


def test_check_cli_is_read_only_and_checks_archives(consumer, capsys):
    path = consumer.root / "CHANGELOG.md"
    path.write_text("# Changelog\n\n" + TARGET + DEFINITION)
    archive = consumer.root / "docs/archives/changelog/1.0.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("# Archive\n\n## [1.0.0]\n\n- Old.\n")
    before = {p: p.read_bytes() for p in (path, archive)}
    args = ["--root", str(consumer.root), "changelog", "check"]
    assert cli.main(args) == 0
    assert {p: p.read_bytes() for p in before} == before
    archive.write_text(archive.read_text().replace("[1.0.0]", "[0.9.0]"))
    assert cli.main(args) == 1
    assert "outside its minor series" in capsys.readouterr().err


@pytest.mark.parametrize("example", ["`[unused]`", "[unused](https://example.org)", "```md\n[unused]\n```", r"\[unused]"])
def test_reference_conflicts_in_literal_examples_are_unrelated(consumer, example):
    (consumer.root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n{TARGET}\n{example}\n{DEFINITION}\n[unused]: https://example.org/one\n[unused]: https://example.org/two\n"
    )
    body = changelog.notes(consumer, "v1.2.0")[0]
    assert example in body
    assert "[unused]:" not in body


def test_full_and_collapsed_reference_links_keep_only_their_definitions(consumer):
    (consumer.root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [1.2.0]\n\n- [display][actual] and [collapsed][].\n\n"
        "[display]: https://example.org/not-used\n[display]: https://example.org/conflict\n"
        "[actual]: https://example.org/actual\n[collapsed]: https://example.org/collapsed\n"
    )
    body = changelog.notes(consumer, "v1.2.0")[0]
    assert "[actual]: https://example.org/actual" in body
    assert "[collapsed]: https://example.org/collapsed" in body
    assert "[display]:" not in body


def test_full_check_rejects_release_after_archives_index(consumer):
    (consumer.root / "CHANGELOG.md").write_text("# Changelog\n\n" + TARGET + "\n## Archives\n\n## [1.0.0]\n\n- Misplaced.\n" + DEFINITION)
    with pytest.raises(ValueError, match="after Archives"):
        changelog.check(consumer)


def test_tag_keeps_strict_history_validation(consumer):
    (consumer.root / "CHANGELOG.md").write_text("# Changelog\n\n" + TARGET + "\n## [bad]\n\n- Invalid.\n" + DEFINITION)
    assert changelog.notes(consumer, "v1.2.0")[0]
    with pytest.raises(ValueError, match="Unrecognized"):
        changelog.tag(consumer, "v1.2.0", dry_run=True)
