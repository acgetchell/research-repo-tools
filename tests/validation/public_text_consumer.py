"""Public raw-line contracts, also run from isolated installed distributions."""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from research_repo_tools import config, selection
from research_repo_tools.cli import main
from research_repo_tools.text_lines import check_lines, inspect_lines


class TestRawLines(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="raw text café ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source with spaces.md"

    def inventory(self, names: bytes):
        return patch.object(selection, "run_git_bytes", return_value=subprocess.CompletedProcess([], 0, names, b""))

    def test_every_raw_line_and_unicode_count_without_final_newline(self) -> None:
        payload = "é🗺\r\n```\r\n12345\r\n```\r\n|abc|\r\nhttps://x\r\na\u2028b\r\ne\u0301 \tX".encode("utf-8")
        self.source.write_bytes(payload)
        with self.inventory(b"source with spaces.md\0"):
            result = check_lines(self.root, limit=4, include=("*.md",))
        self.assertEqual(result.files, 1)
        self.assertEqual([(item.line, item.length) for item in result.violations], [(3, 5), (5, 5), (6, 9), (8, 5)])
        self.assertEqual(str(result.violations[0]), "source with spaces.md:3: line length 5 exceeds 4")
        self.assertEqual(self.source.read_bytes(), payload)

    def test_empty_selection_and_invalid_limits(self) -> None:
        with self.inventory(b""):
            self.assertEqual(check_lines(self.root, limit=1).violations, ())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["--root", str(self.root), "files", "check-lines", "--limit", "1"]), 0)
            self.assertEqual(output.getvalue(), "No matching files.\n")
        for limit in (0, -1, True, 1.2):
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, "positive integer"):
                check_lines(self.root, limit=cast(Any, limit))

    def test_configuration_override_and_violation_status(self) -> None:
        self.source.write_bytes(b"12345\n67890")
        manifest = self.root / "pyproject.toml"
        manifest.write_text('[tool.research-repo-tools.text]\nline-limit=4\ninclude=["*.md"]\nexclude=["history/**"]\n', encoding="utf-8", newline="\n")
        original = manifest.read_bytes(), self.source.read_bytes()
        error = io.StringIO()
        with self.inventory(b"source with spaces.md\0"), contextlib.redirect_stderr(error):
            self.assertEqual(main(["--root", str(self.root), "files", "check-lines"]), 1)
            self.assertEqual(main(["--root", str(self.root), "files", "check-lines", "--limit", "5"]), 0)
        self.assertEqual(
            error.getvalue().splitlines(), ["source with spaces.md:1: line length 5 exceeds 4", "source with spaces.md:2: line length 5 exceeds 4"]
        )
        self.assertEqual((manifest.read_bytes(), self.source.read_bytes()), original)
        for data in ({"line-limit": True}, {"include": "*.md"}, {"unknown": 1}, {"line-limit": 0}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                config.parse({"text": data}, root=self.root)

    def test_discovery_and_decode_fail_closed(self) -> None:
        with patch.object(selection, "run_git_bytes", side_effect=subprocess.CalledProcessError(8, ["git"])):
            with self.assertRaises(subprocess.CalledProcessError):
                check_lines(self.root, limit=10)
        for inventory in (b"unterminated", b"../escape\0"):
            with self.inventory(inventory), self.assertRaises(ValueError):
                check_lines(self.root, limit=10)
        self.source.write_bytes(b"bad\xff")
        with self.inventory(b"source with spaces.md\0"), self.assertRaisesRegex(ValueError, "source with spaces.md.*UTF-8"):
            check_lines(self.root, limit=10)
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            inspect_lines(self.root / "missing", limit=10)

    @unittest.skipIf(os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1", "Git mutations disabled")
    def test_native_git_selection_tracked_new_ignored_and_excluded(self) -> None:
        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, timeout=30)

        git("init")
        self.source.write_bytes(b"12345")
        git("add", "--", self.source.name)
        (self.root / "new café.md").write_bytes(b"123456")
        (self.root / "history.md").write_bytes(b"long long long")
        (self.root / ".gitignore").write_bytes(b"ignored.md\n")
        (self.root / "ignored.md").write_bytes(b"long long long")
        before = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=self.root, timeout=30)
        result = check_lines(self.root, limit=4, include=("*.md",), exclude=("history.md",))
        self.assertEqual(result.files, 2)
        self.assertEqual([item.path for item in result.violations], ["new café.md", self.source.name])
        self.assertEqual(subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=self.root, timeout=30), before)


if __name__ == "__main__":
    unittest.main()
