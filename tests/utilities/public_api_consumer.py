"""Consumer contract suite, also copied outside the checkout for wheel/sdist checks.

Only documented package imports are used; filesystem failures are injected at
standard-library boundaries. No pytest dependency is needed in installations.
"""

import hashlib
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_repo_tools.files import RecoveryError, replace_many
from research_repo_tools.process import ExecutableNotFoundError, format_exception_diagnostics, resolve_executable, run_command, run_command_bytes, run_git_bytes


class ConsumerCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="public-api-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "consumer café"
        self.root.mkdir()


class TestProcess(ConsumerCase):
    def test_resolution_and_literal_arguments(self) -> None:
        python = resolve_executable(sys.executable)
        self.assertTrue(python.is_absolute())
        self.assertEqual(resolve_executable(python.name, env={"PATH": str(python.parent)}), python)
        relative = Path(os.path.relpath(python, self.root))
        self.assertEqual(resolve_executable(relative, cwd=self.root).resolve(), python.resolve())
        with self.assertRaises(ExecutableNotFoundError):
            resolve_executable("absent-public-api-command", env={"PATH": str(self.root)})
        with self.assertRaises(ExecutableNotFoundError):
            run_command("absent-public-api-command")
        probe = "import os,sys; print(os.getcwd()); print(sys.argv[1]); print(os.environ['API_MARKER'])"
        result = run_command(python, ["-c", probe, "a b; $(literal)"], cwd=self.root, env={**os.environ, "API_MARKER": "é"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), [str(self.root), "a b; $(literal)", "é"])
        self.assertEqual(result.stderr, "")

    def test_bytes_and_explicit_text_encoding(self) -> None:
        echo = ["-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"]
        for payload in (b"line\n", b"line\r\n", b"mixed\r\n\x00\xff"):
            with self.subTest(payload=payload):
                self.assertEqual(run_command_bytes(sys.executable, echo, input=payload).stdout, payload)
        self.assertEqual(run_command(sys.executable, echo, input="café\r\n", encoding="latin-1").stdout, "café\r\n")
        bad = ["-c", "import sys; sys.stdout.buffer.write(b'\\xff')"]
        with self.assertRaises(UnicodeDecodeError):
            run_command(sys.executable, bad)
        self.assertEqual(run_command(sys.executable, bad, errors="replace").stdout, "�")
        self.assertEqual(run_command(sys.executable, ["-c", "print('ok')"], timeout=None).stdout.strip(), "ok")

    def test_command_failures_preserve_raw_diagnostics(self) -> None:
        fail = ["-c", "import sys; sys.stdout.buffer.write(b'partial\\xff'); sys.stderr.buffer.write(b'failed\\xff'); sys.exit(17)"]
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            run_command(sys.executable, fail)
        error = raised.exception
        self.assertEqual(error.returncode, 17)
        self.assertEqual(error.stdout, b"partial\xff")
        self.assertEqual(error.stderr, b"failed\xff")
        self.assertIn(str(resolve_executable(sys.executable)), error.cmd)
        self.assertIn("failed�", format_exception_diagnostics(error))
        self.assertEqual(run_command_bytes(sys.executable, fail, check=False).returncode, 17)
        self.assertEqual(run_command(sys.executable, fail, check=False, errors="replace").returncode, 17)

    def test_timeout_retains_partial_output(self) -> None:
        args = ["-c", "import sys,time; print('started', flush=True); print('waiting', file=sys.stderr, flush=True); time.sleep(30)"]
        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            run_command(sys.executable, args, timeout=2)
        self.assertIn(b"started", raised.exception.stdout or b"")
        self.assertIn(b"waiting", raised.exception.stderr or b"")
        self.assertIn("timed out", format_exception_diagnostics(raised.exception))

    def test_invalid_options_do_not_launch(self) -> None:
        marker = self.root / "launched"
        args = ["-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker)]
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                run_command_bytes(sys.executable, args, timeout=timeout)
        with self.assertRaises(LookupError):
            run_command(sys.executable, args, encoding="missing-codec")
        with self.assertRaises(LookupError):
            run_command(sys.executable, args, errors="missing-error-handler")
        with self.assertRaises(LookupError):
            run_command(sys.executable, args, encoding="hex")
        with self.assertRaises(TypeError):
            run_command_bytes(sys.executable, "--version")
        self.assertFalse(marker.exists())

    def test_git_bytes_and_clean_filters(self) -> None:
        # hash-object without -w can apply configured filters outside a Git
        # repository: no index, ref, config, or object-store mutations are needed.
        source = self.root / "données.txt"
        filter_script = self.root / "filter.py"
        filter_script.write_text(
            "import pathlib,sys\npayload = sys.stdin.buffer.read()\npathlib.Path('filter-input').write_bytes(payload)\nsys.stdout.buffer.write(b'clean:' + payload)\n",
            encoding="utf-8",
        )
        attributes = self.root / "attributes"
        attributes.write_bytes(b"*.txt filter=fixture -text\n")
        clean = f"{shlex.quote(Path(sys.executable).as_posix())} {shlex.quote(filter_script.as_posix())}"
        for payload in (b"line\n", b"line\r\n", b"caf\xc3\xa9\r\n\xff"):
            source.write_bytes(payload)
            raw = run_git_bytes(["--no-pager", "hash-object", "--no-filters", "--stdin"], cwd=self.root, input=payload).stdout.strip()
            expected = hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest().encode()
            self.assertEqual(raw, expected)
            args = [
                "--no-pager",
                "-c",
                f"core.attributesfile={attributes}",
                "-c",
                f"filter.fixture.clean={clean}",
                "-c",
                "filter.fixture.required=true",
                "hash-object",
            ]
            stdin = run_git_bytes([*args, f"--path={source.name}", "--stdin"], cwd=self.root, input=payload).stdout
            self.assertEqual((self.root / "filter-input").read_bytes(), payload)
            disk = run_git_bytes([*args, source.name], cwd=self.root).stdout
            self.assertEqual(stdin, disk)
            self.assertNotEqual(stdin.strip(), raw)
        self.assertNotEqual(run_git_bytes(["--no-pager", "invalid-public-api-command"], cwd=self.root, check=False).returncode, 0)


class TestPublication(ConsumerCase):
    def test_publication_preserves_bytes_and_permissions(self) -> None:
        first, second = self.root / "existing", self.root / "nested/données.bin"
        first.write_bytes(b"original")
        first.chmod(0o640)
        mode = stat.S_IMODE(first.stat().st_mode)
        replace_many({first: b"new\r\n\xff", second: b"new\x00\n"})
        self.assertEqual(first.read_bytes(), b"new\r\n\xff")
        self.assertEqual(second.read_bytes(), b"new\x00\n")
        self.assertEqual(stat.S_IMODE(first.stat().st_mode), mode)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(second.stat().st_mode), 0o600)
        self.assertEqual(set(self.root.rglob("*")), {first, second, second.parent})
        replace_many({})

    def test_invalid_targets_leave_no_directories(self) -> None:
        target = self.root / "new/file"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            replace_many({target: b"a", target.parent / "../new/file": b"b"})
        with self.assertRaisesRegex(ValueError, "overlapping"):
            replace_many({target.parent: b"a", target: b"b"})
        with self.assertRaises(IsADirectoryError):
            replace_many({self.root: b"a"})
        with self.assertRaisesRegex(TypeError, "payloads must be bytes"):
            replace_many({target: "text"})  # ty: ignore[invalid-argument-type]
        self.assertEqual(list(self.root.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "Windows symlinks require privileges; Windows CI exercises ordinary paths")
    def test_symlinks_and_resolved_parent_aliases(self) -> None:
        target, link = self.root / "original", self.root / "link"
        target.write_bytes(b"original")
        link.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            replace_many({link: b"candidate"})
        link.unlink()
        link.symlink_to(self.root / "absent")
        with self.assertRaisesRegex(ValueError, "symlink"):
            replace_many({link: b"candidate"})
        parent_link = self.root / "directory-link"
        parent_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            replace_many({target: b"one", parent_link / target.name: b"two"})
        replace_many({parent_link / target.name: b"changed"})
        self.assertEqual(target.read_bytes(), b"changed")
        self.assertTrue(parent_link.is_symlink())

    def test_staging_failure_keeps_original_and_cleans_up(self) -> None:
        target = self.root / "existing"
        target.write_bytes(b"original")
        failure = OSError("sync failed")
        with patch("os.fsync", side_effect=failure), self.assertRaises(OSError) as raised:
            replace_many({target: b"candidate", self.root / "nested/new": b"new"})
        self.assertIs(raised.exception, failure)
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(list(self.root.iterdir()), [target])

    def test_cleanup_failure_does_not_mask_staging_error(self) -> None:
        target = self.root / "existing"
        target.write_bytes(b"original")
        failure = OSError("sync failed")
        with (
            patch("os.fsync", side_effect=failure),
            patch.object(Path, "unlink", side_effect=PermissionError("cleanup denied")),
            self.assertLogs(level="WARNING") as messages,
            self.assertRaises(OSError) as raised,
        ):
            replace_many({target: b"candidate"})
        self.assertIs(raised.exception, failure)
        self.assertEqual(target.read_bytes(), b"original")
        self.assertTrue(any("cleanup denied" in message for message in messages.output))

    def test_partial_parent_creation_is_cleaned_up(self) -> None:
        target = self.root / "new/blocked/file"
        original = Path.mkdir
        failure = PermissionError("parent creation denied")

        def mkdir(path: Path, *args, **kwargs) -> None:
            if path == target.parent:
                raise failure
            original(path, *args, **kwargs)

        with patch.object(Path, "mkdir", mkdir), self.assertRaises(PermissionError) as raised:
            replace_many({target: b"candidate"})
        self.assertIs(raised.exception, failure)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_replacement_rolls_back_in_reverse(self) -> None:
        first, new, last = self.root / "first", self.root / "nested/new", self.root / "last"
        first.write_bytes(b"original\r\n\xff")
        original = Path.replace
        failure = PermissionError("replacement denied")

        def replace(source: Path, target: Path) -> Path:
            if target == first and source.suffix == ".tmp":
                self.assertTrue(list(new.parent.glob("*.tmp")))
                self.assertEqual(next(self.root.glob("*.bak")).read_bytes(), b"original\r\n\xff")
            if target == last:
                raise failure
            return original(source, target)

        with patch.object(Path, "replace", replace), self.assertRaises(PermissionError) as raised:
            replace_many({first: b"candidate", new: b"new", last: b"last"})
        self.assertIs(raised.exception, failure)
        self.assertEqual(first.read_bytes(), b"original\r\n\xff")
        self.assertEqual(list(self.root.iterdir()), [first])

    def test_incomplete_rollback_has_structured_recovery(self) -> None:
        first, last = self.root / "first", self.root / "last"
        first.write_bytes(b"original\xff")
        original = Path.replace
        failure, recovery = OSError("publish failed"), PermissionError("restore failed")

        def replace(source: Path, target: Path) -> Path:
            if target == last:
                raise failure
            if source.suffix == ".bak":
                raise recovery
            return original(source, target)

        with patch.object(Path, "replace", replace), self.assertRaises(ExceptionGroup) as raised:
            replace_many({first: b"candidate", last: b"candidate"})
        self.assertIs(raised.exception.exceptions[0], failure)
        detail = raised.exception.exceptions[1]
        self.assertIsInstance(detail, RecoveryError)
        assert isinstance(detail, RecoveryError) and detail.backup is not None
        self.assertEqual(detail.target, first)
        self.assertEqual(detail.backup.read_bytes(), b"original\xff")
        self.assertIs(detail.__cause__, recovery)
        self.assertEqual(first.read_bytes(), b"candidate")
        diagnostic = format_exception_diagnostics(raised.exception)
        self.assertIn(str(detail.backup), diagnostic)
        self.assertIn("publish failed", diagnostic)
        self.assertIn("restore failed", diagnostic)
        self.assertEqual(set(self.root.iterdir()), {first, detail.backup})

    def test_new_file_recovery_failure_reports_target_without_backup(self) -> None:
        first, last = self.root / "first", self.root / "last"
        original_replace, original_unlink = Path.replace, Path.unlink
        failure = OSError("publish failed")

        def replace(source: Path, target: Path) -> Path:
            if target == last:
                raise failure
            return original_replace(source, target)

        def unlink(path: Path, *args, **kwargs) -> None:
            if path == first:
                raise PermissionError("removal denied")
            original_unlink(path, *args, **kwargs)

        with patch.object(Path, "replace", replace), patch.object(Path, "unlink", unlink), self.assertRaises(ExceptionGroup) as raised:
            replace_many({first: b"new", last: b"new"})
        self.assertIs(raised.exception.exceptions[0], failure)
        detail = raised.exception.exceptions[1]
        assert isinstance(detail, RecoveryError)
        self.assertEqual(detail.target, first)
        self.assertIsNone(detail.backup)
        self.assertEqual(first.read_bytes(), b"new")
        self.assertEqual(list(self.root.iterdir()), [first])


if __name__ == "__main__":
    unittest.main()
