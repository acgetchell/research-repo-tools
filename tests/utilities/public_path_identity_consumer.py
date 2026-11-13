"""Native path identity contracts shared by source, wheel and sdist checks."""

import errno
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import override
from unittest.mock import patch

from research_repo_tools import path_identity
from research_repo_tools.evidence import fingerprint_files
from research_repo_tools.path_identity import (
    MissingPath,
    NativePathPolicy,
    PathInventory,
    PathObservation,
    PathObservationError,
    PathProblem,
    RegularFile,
    Symlink,
    fingerprint_paths,
    observe_paths,
)


class TestPathIdentity(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="path identity café "))).resolve()

    def link(self, path: Path, target: str | Path, *, directory: bool = False) -> None:
        try:
            path.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                self.skipTest("native Windows symlink privilege unavailable")
            raise

    def test_absence_content_kind_and_executable_cannot_collide(self) -> None:
        path = self.root / "sample"
        missing = observe_paths(self.root, [b"sample"])
        self.assertIsInstance(missing.observations[0].value, MissingPath)
        path.write_bytes(b"missing")
        path.chmod(0o644)
        regular = observe_paths(self.root, [b"sample"])
        self.assertEqual(regular.observations[0].value, RegularFile(b"missing", False))
        digests = {fingerprint_paths(missing), fingerprint_paths(regular)}
        self.assertEqual(len(digests), 2)
        if os.name != "nt":
            path.chmod(0o744)
            executable = observe_paths(self.root, [b"sample"])
            self.assertEqual(executable.observations[0].value, RegularFile(b"missing", True))
            digests.add(fingerprint_paths(executable))
            self.assertEqual(len(digests), 3)
        path.unlink()
        self.link(path, "missing")
        linked = observe_paths(self.root, [b"sample"])
        self.assertFalse(path.exists())
        self.assertEqual(linked.observations[0].value, Symlink(os.fsencode(os.readlink(path))))
        self.assertNotIn(fingerprint_paths(linked), digests)

    def test_native_names_exact_bytes_and_order(self) -> None:
        names = ["space café.txt", "ordinary.txt"]
        if os.name != "nt":
            names += ["line\nbreak"]
        for name in names:
            (self.root / name).write_bytes(b"\x00\xfforiginal\r\n")
        inventory = observe_paths(self.root, names)
        self.assertEqual({item.name for item in inventory.observations}, {os.fsencode(name) for name in names})
        self.assertEqual(fingerprint_paths(inventory), fingerprint_paths(observe_paths(self.root, list(reversed(names)))))
        self.assertTrue(all(item.value == RegularFile(b"\x00\xfforiginal\r\n", False) for item in inventory.observations))
        (self.root / names[0]).write_bytes(b"\x00\xfforiginal\n")
        self.assertNotEqual(fingerprint_paths(inventory), fingerprint_paths(observe_paths(self.root, names)))

    @unittest.skipIf(os.name == "nt", "Windows native filename policy requires UTF-8")
    def test_non_utf8_native_filename_when_filesystem_supports_it(self) -> None:
        raw = b"undecodable-\xff"
        try:
            (self.root / os.fsdecode(raw)).write_bytes(b"native bytes")
        except OSError as error:
            if sys.platform == "darwin" and error.errno in {errno.EILSEQ, errno.ENOTSUP}:
                self.skipTest("native macOS filesystem rejects invalid UTF-8 filenames")
            raise
        self.assertIn(raw, [os.fsencode(path.name) for path in self.root.iterdir()])
        observation = observe_paths(self.root, [raw]).observations[0]
        self.assertEqual(observation, PathObservation(raw, RegularFile(b"native bytes", False)))

    def test_empty_and_missing_parent_are_explicit(self) -> None:
        empty = observe_paths(self.root, [])
        missing = observe_paths(self.root, ["missing/child"])
        self.assertEqual(empty.observations, ())
        self.assertEqual(missing.observations[0].value, MissingPath())
        self.assertNotEqual(fingerprint_paths(empty), fingerprint_paths(missing))

    def test_framing_has_a_fixed_versioned_vector(self) -> None:
        inventory = PathInventory(
            NativePathPolicy.POSIX,
            (PathObservation(b"a", MissingPath()), PathObservation(b"b", RegularFile(b"x\0y", True)), PathObservation(b"c", Symlink(b"target"))),
        )
        # Independent wire fixture, including variant tags and length boundaries.
        fields = [b"posix-bytes-executable-v1", b"a", b"missing", b"b", b"regular", b"1", b"x\0y", b"c", b"symlink", b"target"]

        def frame(value: bytes) -> bytes:
            return len(value).to_bytes(8, "big") + value

        encoded = b"research-repo-tools/path-observations/v1\0" + frame(fields[0]) + (3).to_bytes(8, "big")
        encoded += b"".join(frame(value) for value in fields[1:])
        self.assertEqual(fingerprint_paths(inventory), hashlib.sha256(encoded).hexdigest())
        different = replace(inventory, observations=(PathObservation(b"a", RegularFile(b"missing", False)), *inventory.observations[1:]))
        self.assertNotEqual(fingerprint_paths(inventory), fingerprint_paths(different))

    def test_parent_links_and_directory_leaves_are_rejected(self) -> None:
        directory = self.root / "directory"
        directory.mkdir()
        (directory / "file").write_bytes(b"selected")
        with self.assertRaises(PathObservationError) as rejected:
            observe_paths(self.root, ["directory"])
        self.assertEqual(rejected.exception.problem, PathProblem.UNSUPPORTED)
        alias = self.root / "alias"
        self.link(alias, directory, directory=True)
        with self.assertRaisesRegex(PathObservationError, "parent must be a directory"):
            observe_paths(self.root, ["alias/file"])
        alias.unlink()
        self.link(alias, "absent", directory=True)
        with self.assertRaises(PathObservationError) as rejected:
            observe_paths(self.root, ["alias/child"])
        self.assertEqual(rejected.exception.problem, PathProblem.UNSUPPORTED)

    def test_junction_boundary_model_is_rejected_before_reading(self) -> None:
        parent = self.root / "parent"
        parent.mkdir()
        (parent / "child").write_bytes(b"not selected")
        original = Path.is_junction

        def junction(path: Path) -> bool:
            return path == parent or original(path)

        with patch.object(Path, "is_junction", junction), patch.object(path_identity, "_read_file") as read:
            with self.assertRaisesRegex(PathObservationError, "junction"):
                observe_paths(self.root, ["parent/child"])
        read.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "native Windows junction contract")
    def test_native_junction_parent_and_leaf_are_rejected(self) -> None:
        target = self.root / "target"
        target.mkdir()
        (target / "child").write_bytes(b"do not traverse")
        link = self.root / "junction"
        subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True, timeout=30)
        self.assertTrue(link.is_junction())
        for name in ("junction", "junction/child"):
            with self.subTest(name=name), self.assertRaisesRegex(PathObservationError, "junction"):
                observe_paths(self.root, [name])

    def test_leaf_links_do_not_read_targets_or_escape_root(self) -> None:
        outside = self.root.parent / (self.root.name + "-outside")
        outside.write_bytes(b"outside secret")
        self.addCleanup(outside.unlink)
        self.link(self.root / "link", outside)
        with patch.object(path_identity, "_read_file", side_effect=AssertionError("target read")):
            inventory = observe_paths(self.root, ["link"])
        self.assertEqual(inventory.observations[0].value, Symlink(os.fsencode(os.readlink(self.root / "link"))))

    def test_invalid_names_and_duplicate_inputs_fail(self) -> None:
        for names in ([b"../escape"], [b"/absolute"], [b"a/../b"], [b"a/./b"], [b"a//b"], [b""], [b"nul\0"], [b"a", b"a"]):
            with self.subTest(names=names), self.assertRaises(PathObservationError) as rejected:
                observe_paths(self.root, names)
            self.assertEqual(rejected.exception.problem, PathProblem.INVALID)

    def test_hard_link_aliases_fail(self) -> None:
        first = self.root / "first"
        first.write_bytes(b"same inode")
        (self.root / "second").hardlink_to(first)
        with self.assertRaisesRegex(PathObservationError, "filesystem alias"):
            observe_paths(self.root, ["first", "second"])

    def test_read_errors_never_become_missing(self) -> None:
        (self.root / "file").write_bytes(b"bytes")
        with patch.object(path_identity.os, "open", side_effect=PermissionError("denied")):
            with self.assertRaises(PathObservationError) as rejected:
                observe_paths(self.root, ["file"])
        self.assertEqual(rejected.exception.problem, PathProblem.UNREADABLE)
        self.assertEqual(rejected.exception.path, b"file")
        self.assertIsInstance(rejected.exception.__cause__, PermissionError)

    def test_bounds_stop_before_oversized_reads(self) -> None:
        (self.root / "file").write_bytes(b"12345")
        for options in ({"max_file_bytes": 4}, {"max_total_bytes": 4}, {"max_paths": 0}):
            with self.subTest(options=options), patch.object(path_identity, "_read_file") as read:
                with self.assertRaises(PathObservationError) as rejected:
                    observe_paths(self.root, ["file"], **options)
                self.assertEqual(rejected.exception.problem, PathProblem.LIMIT)
                read.assert_not_called()
        self.assertEqual(observe_paths(self.root, ["file"], max_file_bytes=5).observations[0].value, RegularFile(b"12345", False))
        (self.root / "second").write_bytes(b"6")
        with self.assertRaises(PathObservationError) as rejected:
            observe_paths(self.root, ["file", "second"], max_total_bytes=5)
        self.assertEqual(rejected.exception.problem, PathProblem.LIMIT)

    def test_later_read_cannot_hide_changes_to_earlier_input(self) -> None:
        for name in ("a", "z"):
            (self.root / name).write_bytes(b"original")
        original = path_identity._read_file

        def change(path, name, expected, limit):
            result = original(path, name, expected, limit)
            if name == b"z":
                (self.root / "a").write_bytes(b"modified")
                os.utime(self.root / "a", ns=(1, 1))
            return result

        with patch.object(path_identity, "_read_file", change), self.assertRaises(PathObservationError) as rejected:
            observe_paths(self.root, ["a", "z"])
        self.assertEqual(rejected.exception.problem, PathProblem.CHANGED)

    def test_replaced_file_descriptor_is_rejected_before_read(self) -> None:
        path = self.root / "file"
        path.write_bytes(b"selected")
        other = self.root / "other"
        other.write_bytes(b"substituted")
        original = os.open

        def substituted(selected, flags):
            return original(other, flags)

        with patch.object(path_identity.os, "open", substituted), patch.object(path_identity.os, "read") as read:
            with self.assertRaises(PathObservationError) as rejected:
                observe_paths(self.root, ["file"])
        self.assertEqual(rejected.exception.problem, PathProblem.CHANGED)
        read.assert_not_called()

    def test_missing_path_appearing_during_another_read_is_detected(self) -> None:
        (self.root / "z").write_bytes(b"selected")
        original = path_identity._read_file

        def appear(path, name, expected, limit):
            result = original(path, name, expected, limit)
            (self.root / "a").write_bytes(b"appeared")
            return result

        with patch.object(path_identity, "_read_file", appear), self.assertRaises(PathObservationError) as rejected:
            observe_paths(self.root, ["a", "z"])
        self.assertEqual(rejected.exception.problem, PathProblem.CHANGED)

    @unittest.skipIf(os.name == "nt", "POSIX FIFO fixture; directories are rejected on every host")
    def test_fifo_is_rejected_without_opening_it(self) -> None:
        if os.name != "nt":
            os.mkfifo(self.root / "fifo")
        else:
            self.skipTest("POSIX FIFO fixture")
        with patch.object(path_identity.os, "open") as opened, self.assertRaises(PathObservationError) as rejected:
            observe_paths(self.root, ["fifo"])
        self.assertEqual(rejected.exception.problem, PathProblem.UNSUPPORTED)
        opened.assert_not_called()

    def test_constructor_and_replacement_invariants(self) -> None:
        file = RegularFile(b"payload", False)
        with self.assertRaises(FrozenInstanceError):
            file.content = b"replacement"  # ty: ignore[invalid-assignment] -- frozen public model
        with self.assertRaises(TypeError):
            replace(file, content=bytearray(b"mutable"))
        with self.assertRaises(ValueError):
            Symlink(b"")
        with self.assertRaises(ValueError):
            PathObservation(b"../outside", MissingPath())
        with self.assertRaises(ValueError):
            PathInventory(NativePathPolicy.POSIX, (PathObservation(b"a", file),) * 2)
        with self.assertRaises(TypeError):
            PathInventory("unknown", ())  # ty: ignore[invalid-argument-type] -- raw public boundary
        for name in (b"C:/outside", b"name:stream", b"a\\b", b"bad-\xff", b"CON", b"file.", b"line\nbreak"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                PathInventory(NativePathPolicy.WINDOWS, (PathObservation(name, MissingPath()),))
        with self.assertRaises(ValueError):
            PathInventory(NativePathPolicy.WINDOWS, (PathObservation(b"a", RegularFile(b"", True)),))
        with self.assertRaises(ValueError):
            PathInventory(NativePathPolicy.WINDOWS, (PathObservation(b"a", MissingPath()), PathObservation(b"A", MissingPath())))

    def test_existing_regular_file_fingerprint_wire_format_is_unchanged(self) -> None:
        (self.root / "source").write_bytes(b"exact\r\n")
        expected = hashlib.sha256(b"research-repo-tools/files/v1\0" + (6).to_bytes(8, "big") + b"source" + (7).to_bytes(8, "big") + b"exact\r\n")
        self.assertEqual(fingerprint_files(self.root, [Path("source")]), expected.hexdigest())
        with self.assertRaises(ValueError):
            fingerprint_files(self.root, [Path("missing")])
        self.link(self.root / "link", "source")
        with self.assertRaises(ValueError):
            fingerprint_files(self.root, [Path("link")])


if __name__ == "__main__":
    unittest.main()
