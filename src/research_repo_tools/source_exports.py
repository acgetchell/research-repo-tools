"""Read-only Git source capture and verified private exports.

Stored blobs bypass attributes and filters. Working trees retain actual regular
file bytes and native permission bits. Exclusive writer control is required;
repeated capture detects ordinary races, not adversarial change-and-restore.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from research_repo_tools.files import _validate_distinct_paths
from research_repo_tools.process import run_git_bytes
from research_repo_tools.publication import _path, _publication_name
from research_repo_tools.selection import select_files

__all__ = ["CapturedSource", "capture_source", "export_source"]


def _environment() -> dict[str, str]:
    # Identity and inventory belong to this repository, not an inherited alternate
    # index/object directory. Disable optional locks, lazy fetch and fsmonitor.
    return {
        **{key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")},
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": "false",
    }


def _git(root: Path, *args: str, input: bytes | None = None) -> bytes:
    return run_git_bytes(["--no-pager", "--no-replace-objects", *args], cwd=root, input=input, env=_environment()).stdout


def _resolve(root: Path, reference: str) -> str:
    if reference == "HEAD" or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", reference):
        selected = reference
    elif re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", reference):
        selected = f"refs/tags/{reference}"
    else:
        raise ValueError("source reference requires HEAD, a full commit ID, or a canonical stable tag")
    result = _git(root, "rev-parse", "--verify", f"{selected}^{{commit}}").decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", result):
        raise ValueError("invalid resolved source commit")
    return result


def _names(root: Path | None, names: tuple[str, ...]) -> None:
    selected = {unicodedata.normalize("NFC", name).casefold() for name in names}
    if len(selected) != len(names) or any("/".join(name.split("/")[:end]) in selected for name in selected for end in range(1, len(name.split("/")))):
        raise ValueError("source paths contain duplicates or overlapping files")
    spellings: dict[str, str] = {}
    for name in names:
        for end in range(1, len(name.split("/")) + 1):
            spelling = "/".join(name.split("/")[:end])
            key = unicodedata.normalize("NFC", spelling).casefold()
            if key in spellings and spellings[key] != spelling:
                raise ValueError("source paths contain portable directory aliases")
            spellings[key] = spelling
        _publication_name(name)
        if any(part.casefold() == ".git" for part in name.split("/")):
            raise ValueError("source export cannot contain Git metadata")
    if root is not None:
        _validate_distinct_paths(tuple(root / name for name in names))


def _files(root: Path, names: tuple[str, ...]) -> tuple[tuple[str, bytes, int], ...]:
    _names(root, names)
    result = []
    for name in names:
        path = _path(root, name)
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"source input must be a regular file: {name}")
        data = path.read_bytes()
        after = _path(root, name).stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_mode) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_mode,
        ):
            raise ValueError(f"source changed during capture: {name}")
        result.append((name, data, stat.S_IMODE(after.st_mode) & 0o777))
    return tuple(result)


def _committed(root: Path, revision: str) -> tuple[tuple[str, bytes, int], ...]:
    tree = _git(root, "ls-tree", "-r", "-z", "--full-tree", revision)
    if tree and not tree.endswith(b"\0"):
        raise ValueError("source tree inventory is not NUL terminated")
    entries = []
    for item in tree.split(b"\0"):
        if not item:
            continue
        header, name = item.split(b"\t", 1)
        mode, kind, oid = header.split(b" ")
        if mode not in (b"100644", b"100755") or kind != b"blob":
            raise ValueError("source exports reject links, submodules and special files")
        entries.append((os.fsdecode(name), mode, oid))
    # Validate before asking for object bytes or creating any destination.
    _names(root, tuple(name for name, _, _ in entries))
    objects = tuple(dict.fromkeys(oid for _, _, oid in entries))
    payload = _git(root, "cat-file", "--batch", input=b"".join(oid + b"\n" for oid in objects)) if objects else b""
    blobs = {}
    offset = 0
    for oid in objects:
        end = payload.index(b"\n", offset)
        actual, kind, size = payload[offset:end].split(b" ")
        length = int(size)
        start = end + 1
        data = payload[start : start + length]
        if actual != oid or kind != b"blob" or length < 0 or len(data) != length or payload[start + length : start + length + 1] != b"\n":
            raise ValueError("invalid Git blob batch response")
        digest = hashlib.new("sha1" if len(oid) == 40 else "sha256", b"blob " + str(length).encode("ascii") + b"\0" + data).hexdigest()
        if digest.encode("ascii") != oid:
            raise ValueError("source blob identity differs from resolved tree")
        blobs[oid] = data
        offset = start + length + 1
    if offset != len(payload):
        raise ValueError("unexpected Git blob batch data")
    return tuple(sorted((name, blobs[oid], 0o755 if mode == b"100755" else 0o644) for name, mode, oid in entries))


def _manifest(files: tuple[tuple[str, bytes, int], ...]) -> str:
    return json.dumps([(name, hashlib.sha256(data).hexdigest(), mode) for name, data, mode in files], ensure_ascii=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True, init=False)
class CapturedSource:
    """Capture-owned receipt; obtain through capture_source, never from a label.

    The receipt owns immutable captured bytes. verify() rechecks the original
    repository, independently of the exported directory and benchmark outputs.
    """

    repository: Path
    reference: str
    revision: str
    working_tree: bool
    dirty: bool
    files: tuple[tuple[str, bytes, int], ...]

    def __new__(cls) -> Self:
        raise TypeError("use capture_source to obtain a verified source receipt")

    @property
    def identity(self) -> str:
        return hashlib.sha256(_manifest(self.files).encode("ascii")).hexdigest()

    def provenance(self) -> str:
        return json.dumps(
            {
                "schema": "research-repo-tools/source-export/v1",
                "revision": self.revision,
                "mode": "working-tree" if self.working_tree else "revision",
                "dirty": self.dirty,
                "sha256": self.identity,
                "files": json.loads(_manifest(self.files)),
            },
            sort_keys=True,
        )

    def verify(self) -> None:
        for _ in range(2):
            if _resolve(self.repository, self.reference) != self.revision:
                raise ValueError("original source changed after capture")
            if self.working_tree and _files(self.repository, select_files(self.repository, env=_environment())) != self.files:
                raise ValueError("original source changed after capture")


def capture_source(root: Path, reference: str = "HEAD", *, working_tree: bool = False) -> CapturedSource:
    """Capture twice without Git mutations; fail on unstable identities or bytes."""
    if type(working_tree) is not bool:
        raise ValueError("working_tree must be a boolean")
    root = root.resolve(strict=True)
    if _git(root, "rev-parse", "--show-prefix").strip():
        raise ValueError("source capture requires the Git repository root")
    if working_tree and reference != "HEAD":
        raise ValueError("working-tree capture requires HEAD")

    def capture() -> tuple[str, tuple[tuple[str, bytes, int], ...], bool]:
        revision = _resolve(root, reference)
        committed = _committed(root, revision)
        if not working_tree:
            return revision, committed, False
        files = _files(root, select_files(root, env=_environment()))
        # Git records only executable mode; Windows cannot express that bit.
        original = {name: (data, mode == 0o755) for name, data, mode in committed}
        actual = {name: (data, bool(mode & 0o111)) for name, data, mode in files}
        dirty = (
            {name: data for name, (data, _) in original.items()} != {name: data for name, (data, _) in actual.items()}
            if os.name == "nt"
            else original != actual
        )
        return revision, files, dirty

    first = capture()
    if first != capture():
        raise ValueError("source changed during capture; retry with exclusive writer control")
    result = object.__new__(CapturedSource)
    for key, value in zip(
        ("repository", "reference", "revision", "working_tree", "dirty", "files"), (root, reference, first[0], working_tree, first[2], first[1]), strict=True
    ):
        object.__setattr__(result, key, value)
    return result


@contextmanager
def export_source(source: CapturedSource, destination: Path) -> Iterator[Path]:
    """Materialize and verify an absent private directory; own cleanup/recovery."""
    destination = destination.absolute()
    if destination.exists() or any(p.is_symlink() or p.is_junction() for p in (destination, *destination.parents)):
        raise ValueError("export destination must be absent without link parents")
    source.verify()
    body_error = None
    destination.mkdir(mode=0o700)
    try:
        for name, data, mode in source.files:
            path = _path(destination, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)
            path.chmod(mode)
        verify_export(source, destination)
        source.verify()
        yield destination
    except BaseException as error:
        body_error = error
        raise
    finally:
        try:
            # Restore writable files for native Windows cleanup.
            for directory, directories, files in destination.walk():
                directories[:] = [name for name in directories if not (directory / name).is_junction() and not (directory / name).is_symlink()]
                for name in files:
                    path = directory / name
                    if path.is_file() and not path.is_symlink() and not path.is_junction():
                        path.chmod(path.stat().st_mode | stat.S_IWRITE)
            shutil.rmtree(destination)
        except BaseException as error:
            recovery = RuntimeError(f"export cleanup failed; retained source {destination}: {error}")
            if body_error is not None:
                raise BaseExceptionGroup("export and cleanup failed", [body_error, recovery]) from error
            raise recovery from error


def validate_source_record(payload: str, revision: str) -> None:
    """Check retained source receipt structure and its content-addressed manifest."""
    from research_repo_tools.evidence import _digest, _object, _string

    record = _object(json.loads(payload), "source export", {"schema", "revision", "mode", "dirty", "sha256", "files"})
    if record["schema"] != "research-repo-tools/source-export/v1" or record["revision"] != revision:
        raise ValueError("source export revision/schema differs from provenance")
    if record["mode"] not in {"revision", "working-tree"} or type(record["dirty"]) is not bool:
        raise ValueError("invalid source export mode/dirty state")
    if record["mode"] == "revision" and record["dirty"]:
        raise ValueError("committed export cannot be dirty")
    if not isinstance(record["files"], list):
        raise ValueError("source manifest must be an array")
    names = []
    for item in record["files"]:
        if not isinstance(item, list) or len(item) != 3:
            raise ValueError("source manifest requires name/digest/mode triples")
        name, digest, mode = item
        names.append(_string(name, "source name"))
        _digest(digest, "source file digest")
        if type(mode) is not int or not 0 <= mode <= 0o777:
            raise ValueError("source file mode must contain ordinary permission bits")
    _names(None, tuple(names))
    if names != sorted(names):
        raise ValueError("source manifest must be sorted")
    digest = hashlib.sha256(json.dumps(record["files"], ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest()
    if record["sha256"] != digest:
        raise ValueError("source manifest identity differs")


def verify_export(source: CapturedSource, root: Path) -> None:
    """Verify a prepared export before any intentional harness transformation."""
    names = []
    for directory, directories, files in root.walk():
        for name in (*directories, *files):
            path = directory / name
            if path.is_symlink() or path.is_junction():
                raise ValueError("prepared export contains a link")
        names.extend((directory / name).relative_to(root).as_posix() for name in files)
    if tuple(sorted(names)) != tuple(name for name, _, _ in source.files):
        raise ValueError("prepared export inventory differs from captured source")
    copied = _files(root, tuple(sorted(names)))
    if os.name == "nt":
        equal = [(n, d, bool(m & 0o200)) for n, d, m in copied] == [(n, d, bool(m & 0o200)) for n, d, m in source.files]
    else:
        equal = copied == source.files
    if not equal:
        raise ValueError("materialized source bytes/modes differ from captured identity")
