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
