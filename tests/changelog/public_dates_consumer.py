"""Installed citation date parsing and transactional generation contracts."""

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from research_repo_tools import changelog, cli, config


class TestCitationDates(unittest.TestCase):
    def test_tag_checks_configured_and_root_citation_dates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = config.parse({"changelog": {"citation": "prepared.cff"}, "release": {"date-policy": "declared"}}, root=root)
            (root / "pyproject.toml").write_bytes(b'[project]\nname="fixture"\nversion="1.2.3"\n')
            (root / "CHANGELOG.md").write_bytes(b"# Changelog\n\n## [1.2.3] - 2024-02-29\n\n- Prepared release.\n")
            citations = (root / "prepared.cff", root / "CITATION.cff")
            for citation in citations:
                citation.write_bytes(b"version: 1.2.3\ndate-released: 2024-02-29\n")
            with patch("research_repo_tools.changelog.run_git_command", return_value=subprocess.CompletedProcess([], 1, "")):
                self.assertIn("Prepared release.", changelog.tag(settings, "v1.2.3", dry_run=True))
            for citation in citations:
                with self.subTest(citation=citation):
                    citation.write_bytes(b"version: 1.2.3\ndate-released: 2024-03-01\n")
                    with (
                        patch("research_repo_tools.changelog.run_git_command", side_effect=AssertionError("invalid citation reached Git")),
                        self.assertRaisesRegex(ValueError, "date and changelog release date differ"),
                    ):
                        changelog.tag(settings, "v1.2.3", dry_run=True)
                    citation.write_bytes(b"version: 1.2.3\ndate-released: 2024-02-29\n")

    def test_quoted_unquoted_leap_dates_and_configured_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = config.parse({"changelog": {"date-source": "citation", "citation": "prepared.cff"}, "release": {"date-policy": "declared"}}, root=root)
            (root / "pyproject.toml").write_bytes(b'[project]\nname="fixture"\nversion="1.2.3"\n')
            (root / "CHANGELOG.md").write_bytes(b"# Changelog\n\n## [1.2.3] - 2024-02-29\n\n- Prepared release.\n")
            for key_quote in ("", "'", '"'):
                for quote in ("", "'", '"'):
                    with self.subTest(key_quote=key_quote, quote=quote):
                        (root / "prepared.cff").write_bytes(f"version: '1.2.3'\r\n{key_quote}date-released{key_quote}: {quote}2024-02-29{quote}\r\n".encode())
                        before = (root / "prepared.cff").read_bytes()
                        self.assertEqual(changelog.resolve_date(settings, tag="v1.2.3"), "2024-02-29")
                        with patch("research_repo_tools.changelog.run_git_command", return_value=subprocess.CompletedProcess([], 1, "")):
                            self.assertIn("Prepared release.", changelog.tag(settings, "v1.2.3", dry_run=True))
                        self.assertEqual((root / "prepared.cff").read_bytes(), before)

    def test_tag_checks_resolved_citation_path_only_once(self):
        from research_repo_tools.release_metadata import _citation_date_reference

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            settings = config.parse({"changelog": {"citation": "alias.cff"}, "release": {"date-policy": "declared"}}, root=root)
            (root / "pyproject.toml").write_bytes(b'[project]\nname="fixture"\nversion="1.2.3"\n')
            (root / "CHANGELOG.md").write_bytes(b"# Changelog\n\n## [1.2.3] - 2024-02-29\n\n- Prepared release.\n")
            (root / "CITATION.cff").write_bytes(b"version: 1.2.3\ndate-released: 2024-02-29\n")
            resolve = Path.resolve
            # Model a resolved alias without requiring native symlink privileges.
            with (
                patch.object(Path, "resolve", lambda path: root / "CITATION.cff" if path == root / "alias.cff" else resolve(path)),
                patch("research_repo_tools.release_metadata._citation_date_reference", wraps=_citation_date_reference) as read,
                patch("research_repo_tools.changelog.run_git_command", return_value=subprocess.CompletedProcess([], 1, "")),
            ):
                self.assertIn("Prepared release.", changelog.tag(settings, "v1.2.3", dry_run=True))
            self.assertEqual(read.call_count, 1)

    def test_generation_and_tagging_reject_ambiguous_date_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = config.parse({"changelog": {"date-source": "citation"}, "release": {"date-policy": "declared"}}, root=root)
            (root / "pyproject.toml").write_bytes(b'[project]\nname="fixture"\nversion="1.2.3"\n')
            (root / "CHANGELOG.md").write_bytes(b"# Changelog\n\n## [1.2.3] - 2024-02-29\n\n- Prepared release.\n")
            for fields in (
                b'date-released: 2024-02-29\n"date-released": 2024-02-29\n',
                b"\"date-released\": 2024-02-29\n'date-released': 2024-03-01\n",
                b"date-released: 2024-02-29\n<<: {date-released: 2024-03-01}\n",
            ):
                with self.subTest(fields=fields):
                    (root / "CITATION.cff").write_bytes(b"version: 1.2.3\n" + fields)
                    with self.assertRaises(ValueError):
                        changelog.resolve_date(settings, tag="v1.2.3")
                    with (
                        patch("research_repo_tools.changelog.run_git_command", side_effect=AssertionError("ambiguous date reached Git")),
                        self.assertRaises(ValueError),
                    ):
                        changelog.tag(settings, "v1.2.3", dry_run=True)

    def test_every_invalid_citation_fails_before_generator_and_preserves_files(self):
        invalid = (
            None,
            b"",
            b"[]",
            b"version: [",
            b"version: 1.2.3\n",
            b"date-released: 2024-02-29\n",
            b"version: 1.2.3\nversion: 1.2.3\ndate-released: 2024-02-29\n",
            b"version: 1.2.3\ndate-released: 2024-02-29\ndate-released: 2024-02-29\n",
            b"version: 1.2.3\nnested:\n  date-released: 2024-02-29\n",
            *(
                f"version: {version}\ndate-released: {date}\n".encode()
                for version, date in (
                    ("1.2.4", "2024-02-29"),
                    ("[]", "2024-02-29"),
                    ("''", "2024-02-29"),
                    ("1.2.3", "''"),
                    ("1.2.3", "[]"),
                    ("1.2.3", "{date: 2024-02-29}"),
                    ("1.2.3", "2023-02-29"),
                    ("1.2.3", "2024-2-29"),
                    ("1.2.3", "2024-02-29T00:00:00Z"),
                    ("1.2.3", "null"),
                    ("1.2.3", "|\n  2024-02-29"),
                )
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "CHANGELOG.md").write_bytes(b"# Changelog\n\n## [1.2.2] - 2020-01-01\n\n- Existing.\n")
            archives = root / "docs/archives/changelog"
            archives.mkdir(parents=True)
            (archives / "0.9.md").write_bytes(b"# Archive\n\n## [0.9.0] - 2019-01-01\n\n- Retained.\n")
            settings = config.parse({"changelog": {"date-source": "citation", "owner": "example", "repository": "test"}}, root=root)
            for value in invalid:
                with self.subTest(value=value):
                    citation = root / "CITATION.cff"
                    if value is None:
                        citation.unlink(missing_ok=True)
                    else:
                        citation.write_bytes(value)
                    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
                    with patch("research_repo_tools.changelog.run_safe_command", side_effect=AssertionError("invalid input reached generator")):
                        for dry_run in (False, True):
                            with self.assertRaises(ValueError):
                                changelog.generate(settings, tag="v1.2.3", dry_run=dry_run)
                    self.assertEqual({path: path.read_bytes() for path in root.rglob("*") if path.is_file()}, before)

    def test_explicit_and_untagged_calls_never_read_citation(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = config.parse({"changelog": {"date-source": "citation"}}, root=Path(temporary))
            with patch.object(Path, "read_bytes", side_effect=AssertionError("citation lookup")):
                self.assertEqual(changelog.resolve_date(settings, tag="v1.2.3", released="2024-02-29"), "2024-02-29")
                self.assertIsNone(changelog.resolve_date(settings))
            for value in ("", "2023-02-29", "2024-2-29"):
                with self.assertRaises(ValueError):
                    changelog.resolve_date(settings, tag="v1.2.3", released=value)

    def test_rebuild_reviews_changes_before_replacing_retained_history(self):
        from research_repo_tools import files

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = root / "research.toml"
            policy.write_bytes(b'[changelog]\ncliff-config="cliff.toml"\n')
            (root / "cliff.toml").write_bytes(b"[changelog]\n")
            args = ["--config", str(policy), "changelog"]
            settings = config.load(root=root, path=policy)
            path = root / "CHANGELOG.md"
            path.write_bytes(b"# Changelog\r\r## [1.2.0] - 2020-04-01\r\r- New.\r")
            archives = root / "docs/archives/changelog"
            archives.mkdir(parents=True)
            old = archives / "1.0.md"
            old.write_bytes(b"# Archive\n\n## [1.0.0] - 2019-01-01\n\n- Truncated.\n")
            obsolete = archives / "1.2.md"
            obsolete.write_bytes(b"# Obsolete archive\n")
            # Only ASCII numeric filenames belong to the generated archive inventory.
            notes = archives / "1.2\N{ARABIC-INDIC DIGIT THREE}.md"
            notes.write_bytes(b"# Maintainer notes\n")
            generated = "# Changelog\n\n## [1.2.0] - 2022-01-01\n\n- New.\n\n## [1.1.0] - 2019-08-09\n\n- Middle.\n\n## [1.0.0] - 2018-01-01\n\n- Complete continuation.\n"

            def snapshot():
                return {target: target.read_bytes() for target in (path, *archives.glob("*.md"))}

            before = snapshot()
            for prospective in (False, True):
                with (
                    patch("research_repo_tools.changelog._run_cliff", return_value=generated),
                    self.assertRaisesRegex(ValueError, "conflicting retained release"),
                ):
                    changelog.generate(settings, **({"tag": "v1.2.0", "released": "2020-04-01"} if prospective else {}))
            with patch("research_repo_tools.changelog._run_cliff", return_value=generated):
                preview = io.StringIO()
                with redirect_stdout(preview):
                    self.assertEqual(cli.main([*args, "rebuild"]), 0)
                diff = preview.getvalue()
                self.assertIn("a/CHANGELOG.md", diff)
                self.assertIn("--- /dev/null\n+++ b/docs/archives/changelog/1.1.md", diff)
                self.assertIn("--- a/docs/archives/changelog/1.2.md\n+++ /dev/null", diff)
                self.assertIn("-- Truncated.", diff)
                self.assertIn("+- Complete continuation.", diff)
                self.assertEqual(snapshot(), before)
                replace_path = files._replace_path

                def interrupt_archive(source, destination):
                    if destination == old.resolve():
                        self.assertFalse(obsolete.exists())
                        raise OSError("publication interrupted")
                    replace_path(source, destination)

                with patch.object(files, "_replace_path", side_effect=interrupt_archive), self.assertRaisesRegex(OSError, "interrupted"):
                    changelog.rebuild(settings, apply=True)
                self.assertEqual(snapshot(), before)
                self.assertEqual(changelog.rebuild(settings, apply=True), diff)
                self.assertFalse(obsolete.exists())
                self.assertEqual(notes.read_bytes(), before[notes])
                self.assertIn(b"2019-01-01", old.read_bytes())
                self.assertIn(b"2020-04-01", path.read_bytes())
                self.assertEqual(changelog.rebuild(settings), "")
            published = snapshot()
            for damaged, error in (
                (generated.split("## [1.0.0]")[0], "drops retained releases"),
                (generated + "\n## [1.0.0]\n", "Duplicate release"),
            ):
                with patch("research_repo_tools.changelog._run_cliff", return_value=damaged), self.assertRaisesRegex(ValueError, error):
                    changelog.rebuild(settings, apply=True)
                self.assertEqual(snapshot(), published)

    def test_rebuild_preserves_a_concurrent_edit_while_replacements_are_staged(self):
        from research_repo_tools import files

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = config.parse({"changelog": {"cliff-config": "cliff.toml"}}, root=root)
            (root / "cliff.toml").write_bytes(b"[changelog]\n")
            path = root / "CHANGELOG.md"
            path.write_bytes(b"# Changelog\n\n## [1.2.0]\n\n- Original.\n")
            obsolete = root / "docs/archives/changelog/1.2.md"
            obsolete.parent.mkdir(parents=True)
            obsolete.write_bytes(b"# Obsolete\n")
            edit = b"# Changelog\n\n## [1.2.0]\n\n- Concurrent edit.\n"
            stage = files._stage_writes

            def stage_and_edit(writes):
                result = stage(writes)
                path.write_bytes(edit)
                return result

            with (
                patch.object(changelog, "_run_cliff", return_value="# Changelog\n\n## [1.2.0]\n\n- Generated.\n"),
                patch.object(files, "_stage_writes", side_effect=stage_and_edit),
                self.assertRaisesRegex(ValueError, "changed during rebuild"),
            ):
                changelog.rebuild(settings, apply=True)
            self.assertEqual(path.read_bytes(), edit)
            self.assertEqual(obsolete.read_bytes(), b"# Obsolete\n")

    def test_cli_preview_and_failure_use_same_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "research.toml").write_bytes(b'[changelog]\nowner="example"\nrepository="test"\ndate-source="citation"\n')
            (root / "CITATION.cff").write_bytes(b"version: 1.2.3\ndate-released: 2024-02-29\n")
            path = root / "CHANGELOG.md"
            path.write_bytes(b"# Changelog\n\n## [Unreleased]\n\n- Original.\n")
            args = ["--config", str(root / "research.toml"), "changelog", "generate", "--tag", "v1.2.3"]
            generated = "# Changelog\n\n## [1.2.3] - 2000-01-01\n\n- New.\n"
            before = path.read_bytes()
            with (
                patch("research_repo_tools.changelog.run_safe_command", return_value=subprocess.CompletedProcess([], 0, generated)),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cli.main([*args, "--dry-run"]), 0)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(cli.main(args), 0)
                published = path.read_bytes()
                self.assertIn(b"2024-02-29", published)
                self.assertEqual(cli.main(args), 0)
                self.assertEqual(path.read_bytes(), published)
                (root / "CITATION.cff").write_bytes(b"version: 1.2.3\ndate-released: 2024-03-01\n")
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(cli.main(args), 1)
                self.assertEqual(path.read_bytes(), published)


if __name__ == "__main__":
    unittest.main()
