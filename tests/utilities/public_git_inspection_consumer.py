"""Public Git inspection contracts, also run from installed wheel and sdist."""

import hashlib
import os
import shlex
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest.mock import patch

from research_repo_tools.git_inspect import git_diff_paths_bytes, git_status_bytes
from research_repo_tools.process import run_git_bytes


class TestInspectionBoundary(unittest.TestCase):
    def test_invalid_arguments_fail_before_launch(self) -> None:
        with patch("subprocess.run", side_effect=AssertionError("must not launch")):
            for query in (git_status_bytes, git_diff_paths_bytes):
                for paths in ("file", b"file", [Path("file")]):
                    with self.subTest(query=query, paths=paths), self.assertRaises(TypeError):
                        query(paths=paths)  # ty: ignore[invalid-argument-type] -- malformed public boundary input
                for paths in ([""], ["nul\0path"]):
                    with self.subTest(query=query, paths=paths), self.assertRaises(ValueError):
                        query(paths=paths)
                for timeout in (0, -1, float("inf"), float("nan"), None):
                    with self.subTest(query=query, timeout=timeout), self.assertRaises(ValueError):
                        query(timeout=timeout)  # ty: ignore[invalid-argument-type] -- bounded inspection rejects None
            for base in ("", "HEAD\0", 1):
                with self.assertRaises(ValueError):
                    git_diff_paths_bytes(base=base)  # ty: ignore[invalid-argument-type] -- malformed public boundary input
            with self.assertRaises(TypeError):
                git_diff_paths_bytes(cached="yes")  # ty: ignore[invalid-argument-type] -- malformed public boundary input

    def test_environment_overrides_and_difference_exit_do_not_change_byte_transport(self) -> None:
        environment = {
            "PATH": os.environ.get("PATH", os.defpath),
            "GIT_GLOB_PATHSPECS": "1",
            "GIT_NOGLOB_PATHSPECS": "1",
            "GIT_LITERAL_PATHSPECS": "0",
            "GIT_ICASE_PATHSPECS": "1",
            "GIT_OPTIONAL_LOCKS": "1",
            "GIT_INDEX_FILE": "selected-index",
        }
        before = dict(environment)
        output = b"literal\xff\r\n\0"
        result = subprocess.CompletedProcess(["git"], 1, b"-\t-\t" + output, b"warning\xff\r\n")
        with patch("research_repo_tools.git_inspect.run_git_bytes", return_value=result) as execute:
            selected = git_diff_paths_bytes(paths=["--output=literal"], env=environment, timeout=7)
        arguments = execute.call_args.args[0]
        supplied = execute.call_args.kwargs["env"]
        self.assertEqual(environment, before)
        self.assertNotIn("HOME", supplied)
        for key in ("GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS"):
            self.assertNotIn(key, supplied)
        self.assertEqual(supplied["GIT_INDEX_FILE"], "selected-index")
        self.assertIn("--no-optional-locks", arguments)
        self.assertIn("diff.autoRefreshIndex=false", arguments)
        self.assertIn("core.fsmonitor=false", arguments)
        self.assertEqual(arguments[-2:], ["--", "--output=literal"])
        self.assertEqual(execute.call_args.kwargs["timeout"], 7)
        self.assertEqual(selected.returncode, 1)
        self.assertEqual(selected.stdout, output)
        self.assertEqual(selected.stderr, b"warning\xff\r\n")

    def test_stat_only_exit_and_malformed_numstat_are_not_silent_differences(self) -> None:
        result = subprocess.CompletedProcess(["git"], 1, b"", b"")
        with patch("research_repo_tools.git_inspect.run_git_bytes", return_value=result):
            selected = git_diff_paths_bytes()
        self.assertEqual((selected.returncode, selected.stdout), (0, b""))
        for output in (b"malformed\0", b"0\t0\t\0"):
            result = subprocess.CompletedProcess(["git"], 0, output, b"")
            with patch("research_repo_tools.git_inspect.run_git_bytes", return_value=result), self.assertRaisesRegex(ValueError, "numstat"):
                git_diff_paths_bytes()

    def test_fatal_results_and_timeouts_keep_original_diagnostics(self) -> None:
        for query in (git_status_bytes, git_diff_paths_bytes):
            codes = (1, 128, -9) if query is git_status_bytes else (2, 128, -9)
            for code in codes:
                result = subprocess.CompletedProcess(["git", "query"], code, b"partial\xff", b"fatal\xff\r\n")
                with (
                    self.subTest(query=query, code=code),
                    patch("research_repo_tools.git_inspect.run_git_bytes", return_value=result),
                    self.assertRaises(subprocess.CalledProcessError) as raised,
                ):
                    query()
                self.assertEqual((raised.exception.returncode, raised.exception.stdout, raised.exception.stderr), (code, result.stdout, result.stderr))
            error = subprocess.TimeoutExpired(["git", "query"], 7, output=b"partial\xff", stderr=b"waiting\r\n")
            with patch("research_repo_tools.git_inspect.run_git_bytes", side_effect=error), self.assertRaises(subprocess.TimeoutExpired) as raised:
                query(timeout=7)
            self.assertIs(raised.exception, error)


class TestGitInspection(unittest.TestCase):
    @override
    def setUp(self) -> None:
        if os.environ.get("RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS") == "1":
            self.skipTest("Git mutations disabled by RESEARCH_REPO_TOOLS_SKIP_GIT_MUTATIONS=1")
        temporary = tempfile.TemporaryDirectory(prefix="git-inspection-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "consumer café"
        self.root.mkdir()
        # Isolate user/system Git policy and repository-selection variables.
        self.env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        self.env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1"})
        self.git("init", "--quiet", "--object-format=sha1", "--template=")
        for key, value in {
            "user.name": "Test Author",
            "user.email": "test@example.invalid",
            "commit.gpgsign": "false",
            "core.autocrlf": "false",
            "core.trustctime": "false",
            "core.checkstat": "minimal",
            "core.fsmonitor": "false",
            "core.untrackedCache": "false",
            "index.version": "2",
        }.items():
            self.git("config", key, value)
        self.index = self.root / ".git/index"

    def git(self, *args: str) -> bytes:
        return run_git_bytes(["--no-pager", *args], cwd=self.root, env=self.env, timeout=30).stdout

    def commit(self) -> None:
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", "Fixture")

    def snapshot(self) -> tuple[bytes, bytes]:
        return self.index.read_bytes(), self.git("ls-files", "--stage", "-z")

    def test_stale_and_racy_metadata_preserve_index_and_select_content_changes(self) -> None:
        names = ("clean.txt", "deleted.txt", "partial.txt", "racy.txt", "staged.txt", "unstaged.txt")
        stamp = 946_684_800_000_000_000
        for name in names:
            path = self.root / name
            path.write_bytes(b"base\n")
            os.utime(path, ns=(stamp, stamp))
        self.commit()
        for name in ("partial.txt", "staged.txt"):
            (self.root / name).write_bytes(b"staged\n")
        self.git("add", "partial.txt", "staged.txt")
        (self.root / "partial.txt").write_bytes(b"worktree\n")
        (self.root / "unstaged.txt").write_bytes(b"unstaged\n")
        (self.root / "deleted.txt").unlink()
        (self.root / "new.txt").write_bytes(b"untracked\n")
        # Verify serialized v2 metadata, then force a stat-only discrepancy and
        # a same-size, same-mtime edit in the index's racy timestamp window.
        data = self.index.read_bytes()
        self.assertEqual(struct.unpack("!4sII", data[:12]), (b"DIRC", 2, len(names)))
        self.assertEqual(hashlib.sha1(data[:-20]).digest(), data[-20:])
        cursor = 12
        cached = {}
        for _ in names:
            start = cursor
            metadata = struct.unpack("!10I", data[cursor : cursor + 40])
            end = data.index(b"\0", cursor + 62)
            cached[os.fsdecode(data[cursor + 62 : end])] = metadata
            cursor = start + ((end - start + 8) // 8) * 8
        for name in ("clean.txt", "racy.txt"):
            self.assertEqual(cached[name][2:4], (stamp // 1_000_000_000, 0))
            self.assertEqual(cached[name][9], 5)
        os.utime(self.root / "clean.txt", ns=(stamp + 2_000_000_000, stamp + 2_000_000_000))
        (self.root / "racy.txt").write_bytes(b"edit\n")
        os.utime(self.root / "racy.txt", ns=(stamp, stamp))
        os.utime(self.index, ns=(stamp, stamp))
        self.assertEqual((self.root / "racy.txt").stat().st_mtime_ns, self.index.stat().st_mtime_ns)
        self.assertNotEqual((self.root / "clean.txt").stat().st_mtime_ns, stamp)
        before = self.snapshot()
        queries = (
            (None, False, {"deleted.txt", "partial.txt", "racy.txt", "unstaged.txt"}),
            (None, True, {"partial.txt", "staged.txt"}),
            ("HEAD", False, {"deleted.txt", "partial.txt", "racy.txt", "staged.txt", "unstaged.txt"}),
            ("HEAD", True, {"partial.txt", "staged.txt"}),
        )
        for base, cached, expected in queries:
            with self.subTest(base=base, cached=cached):
                result = git_diff_paths_bytes(self.root, env=self.env, base=base, cached=cached)
                self.assertEqual(result.returncode, 1)
                self.assertEqual({os.fsdecode(path) for path in result.stdout.split(b"\0") if path}, expected)
                self.assertEqual(self.snapshot(), before)
        status = git_status_bytes(self.root, env=self.env)
        self.assertEqual(status.returncode, 0)
        self.assertEqual(
            set(status.stdout.split(b"\0")) - {b""},
            {b" D deleted.txt", b"MM partial.txt", b" M racy.txt", b"M  staged.txt", b" M unstaged.txt", b"?? new.txt"},
        )
        self.assertEqual(self.snapshot(), before)
        # The clean selection must not inherit diff's stat-only false positives.
        clean = git_diff_paths_bytes(self.root, paths=["clean.txt"], env=self.env)
        self.assertEqual((clean.returncode, clean.stdout), (0, b""))
        self.assertEqual(git_status_bytes(self.root, paths=["clean.txt"], env=self.env).stdout, b"")
        self.assertEqual(self.snapshot(), before)

    def test_literal_paths_nested_cwd_and_config_overrides(self) -> None:
        names = ("-dash.txt", "[literal].txt", "l.txt", "space café.txt", "nested/tracked.txt", "old.txt")
        (self.root / "nested").mkdir()
        for name in names:
            (self.root / name).write_bytes(b"old\r\n\xff")
        self.commit()
        for name in names[:-1]:
            (self.root / name).write_bytes(b"new\r\n\xff")
        (self.root / "old.txt").rename(self.root / "renamed.txt")
        self.git("add", "--all")
        self.git("config", "diff.relative", "true")
        self.git("config", "diff.autoRefreshIndex", "true")
        self.git("config", "diff.renames", "true")
        self.git("config", "status.renames", "true")
        env = {**self.env, "GIT_OPTIONAL_LOCKS": "1", "GIT_GLOB_PATHSPECS": "1", "GIT_ICASE_PATHSPECS": "1"}
        before = self.snapshot()
        for name in names[:-1]:
            with self.subTest(name=name):
                expected = os.fsencode(name) + b"\0"
                self.assertEqual(git_diff_paths_bytes(self.root, paths=[name], cached=True, env=env).stdout, expected)
                self.assertEqual(git_status_bytes(self.root, paths=[name], env=env).stdout, b"M  " + expected)
        nested = self.root / "nested"
        self.assertEqual(git_diff_paths_bytes(nested, paths=["tracked.txt"], cached=True, env=env).stdout, b"nested/tracked.txt\0")
        self.assertEqual(git_status_bytes(nested, paths=["tracked.txt"], env=env).stdout, b"M  nested/tracked.txt\0")
        self.assertEqual(git_diff_paths_bytes(self.root, paths=["old.txt", "renamed.txt"], cached=True, env=env).stdout, b"old.txt\0renamed.txt\0")
        self.assertEqual(git_status_bytes(self.root, paths=["old.txt", "renamed.txt"], env=env).stdout, b"D  old.txt\0A  renamed.txt\0")
        self.assertEqual(self.snapshot(), before)

    def test_unborn_index_alternate_index_and_fatal_revision(self) -> None:
        (self.root / "first.txt").write_bytes(b"first\n")
        self.git("add", "first.txt")
        original = self.snapshot()
        alternate = self.root / ".git/alternate-index"
        alternate.write_bytes(original[0])
        (self.root / "second.txt").write_bytes(b"second\n")
        self.git("add", "second.txt")
        before = self.snapshot()
        env = {**self.env, "GIT_INDEX_FILE": str(alternate)}
        self.assertEqual(git_diff_paths_bytes(self.root, cached=True, env=env).stdout, b"first.txt\0")
        self.assertEqual(git_status_bytes(self.root, env=env).stdout, b"A  first.txt\0?? second.txt\0")
        for base in ("absent-revision", "--output=must-not-exist"):
            with self.subTest(base=base), self.assertRaises(subprocess.CalledProcessError) as raised:
                git_diff_paths_bytes(self.root, base=base, env=env)
            self.assertNotEqual(raised.exception.returncode, 1)
            self.assertIn(b"fatal:", raised.exception.stderr)
        self.assertFalse((self.root / "must-not-exist").exists())
        self.assertEqual(alternate.read_bytes(), original[0])
        self.assertEqual(self.snapshot(), before)

    def test_byte_paths_empty_files_and_mode_changes(self) -> None:
        names = ["binary.txt", "mode.txt"]
        if os.name != "nt":
            # These names cannot be represented by the Windows filesystem.
            names.extend(["tab\tname", "line\nname"])
        if sys.platform.startswith("linux"):
            # macOS and Windows filesystems require representable Unicode names.
            names.append(os.fsdecode(b"byte-\xff"))
        for name in names:
            (self.root / name).write_bytes(b"before\0\xff\r\n")
        self.commit()
        for name in names:
            if name != "mode.txt":
                (self.root / name).write_bytes(b"after\0\xff\r\n")
        (self.root / "empty.txt").write_bytes(b"")
        self.git("add", "--all")
        self.git("update-index", "--chmod=+x", "mode.txt")
        before = self.snapshot()
        result = git_diff_paths_bytes(self.root, cached=True, env=self.env)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(set(result.stdout.split(b"\0")), {b"", b"empty.txt", *(os.fsencode(name) for name in names)})
        self.assertEqual(self.snapshot(), before)

    def test_clean_filters_apply_but_external_diff_and_textconv_do_not_run(self) -> None:
        script = self.root / "filter.py"
        script.write_text(
            "import pathlib,sys\n"
            "if len(sys.argv) > 1:\n"
            "    pathlib.Path('unexpected-helper').write_bytes(b'ran')\n"
            "    sys.exit(97)\n"
            "sys.stdout.buffer.write(sys.stdin.buffer.read().removeprefix(b'WORK:'))\n",
            encoding="utf-8",
            newline="\n",
        )
        command = f"{shlex.quote(Path(sys.executable).as_posix())} {shlex.quote(script.as_posix())}"
        self.git("config", "filter.fixture.clean", command)
        self.git("config", "filter.fixture.required", "true")
        (self.root / ".gitattributes").write_bytes(b"data.txt filter=fixture diff=fixture -text\n")
        (self.root / "data.txt").write_bytes(b"same\r\n\xff")
        self.commit()
        self.git("config", "diff.external", command + " external")
        self.git("config", "diff.fixture.command", command + " external")
        self.git("config", "diff.fixture.textconv", command + " textconv")
        (self.root / "data.txt").write_bytes(b"WORK:same\r\n\xff")
        before = self.snapshot()
        result = git_diff_paths_bytes(self.root, paths=["data.txt"], env=self.env)
        self.assertEqual((result.returncode, result.stdout), (0, b""))
        # Native status short-circuits on the size discrepancy, whereas numstat
        # compares the filtered bytes. Preserve both native status and accurate
        # diff selection rather than treating their contracts as identical.
        native_status = self.git("--no-optional-locks", "status", "--porcelain=v1", "-z", "--", "data.txt")
        self.assertEqual(native_status, b" M data.txt\0")
        self.assertEqual(git_status_bytes(self.root, paths=["data.txt"], env=self.env).stdout, native_status)
        self.assertEqual(self.snapshot(), before)
        (self.root / "data.txt").write_bytes(b"WORK:different\r\n\xff")
        self.assertEqual(git_diff_paths_bytes(self.root, paths=["data.txt"], env=self.env).stdout, b"data.txt\0")
        self.assertFalse((self.root / "unexpected-helper").exists())
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
