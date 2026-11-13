"""Native path observations with versioned, exact-byte identities.

This is a bounded filesystem observation, not an atomic snapshot, authentication,
Git selection, or permission check. Callers own inventory completeness and reuse.
"""

import hashlib
import ntpath
import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import assert_never

__all__ = [
    "MissingPath",
    "NativePathPolicy",
    "PathInventory",
    "PathObservation",
    "PathObservationError",
    "PathProblem",
    "PathValue",
    "RegularFile",
    "Symlink",
    "fingerprint_paths",
    "observe_paths",
]


class NativePathPolicy(StrEnum):
    POSIX = "posix-bytes-executable-v1"
    WINDOWS = "windows-utf8-no-executable-v1"


class PathProblem(StrEnum):
    INVALID = "invalid-inventory"
    UNSUPPORTED = "unsupported-kind"
    UNREADABLE = "unreadable"
    CHANGED = "changed-during-observation"
    LIMIT = "limit-exceeded"


class PathObservationError(ValueError):
    """A failed observation; absence is represented only by MissingPath."""

    def __init__(self, problem: PathProblem, path: bytes, detail: str) -> None:
        self.problem = problem
        self.path = path
        super().__init__(f"{problem.value}: {path!r}: {detail}")


@dataclass(frozen=True, slots=True)
class MissingPath:
    """The selected leaf or an ancestor did not exist when observed."""


@dataclass(frozen=True, slots=True)
class RegularFile:
    content: bytes
    executable: bool

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes) or type(self.executable) is not bool:
            raise TypeError("regular-file observations require bytes and a bool")


@dataclass(frozen=True, slots=True)
class Symlink:
    """Native os.readlink target encoded with os.fsencode, without resolution."""

    target: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.target, bytes) or not self.target or b"\0" in self.target:
            raise ValueError("symlink target must be nonempty native bytes without NUL")


type PathValue = MissingPath | RegularFile | Symlink


@dataclass(frozen=True, slots=True)
class PathObservation:
    name: bytes
    value: PathValue

    def __post_init__(self) -> None:
        if not isinstance(self.name, bytes) or b"\0" in self.name or any(part in {b"", b".", b".."} for part in self.name.split(b"/")):
            raise ValueError("observation name must be relative native bytes without traversal or empty components")
        if not isinstance(self.value, MissingPath | RegularFile | Symlink):
            raise TypeError("unknown path observation variant")


@dataclass(frozen=True, slots=True)
class PathInventory:
    policy: NativePathPolicy
    observations: tuple[PathObservation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.policy, NativePathPolicy):
            raise TypeError("path inventory requires a native path policy")
        observations = tuple(self.observations)
        if any(not isinstance(item, PathObservation) for item in observations):  # ty: ignore[redundant-condition-strict] -- protect untyped public callers
            raise TypeError("path inventory requires path observations")
        names = [item.name for item in observations]
        if len(set(names)) != len(names):
            raise ValueError("duplicate path observation")
        if self.policy is NativePathPolicy.WINDOWS:
            native_names = [ntpath.normcase(name.decode("utf-8")) for name in names]
            if len(set(native_names)) != len(native_names):
                raise ValueError("duplicate Windows path observation")
            for item in observations:
                if _name(item.name, self.policy) != item.name:
                    raise ValueError("Windows observation names must use slash separators")
                if isinstance(item.value, RegularFile) and item.value.executable:
                    raise ValueError("Windows observations do not infer executable permissions")
                if isinstance(item.value, Symlink):
                    item.value.target.decode("utf-8")
        object.__setattr__(self, "observations", tuple(sorted(observations, key=lambda item: item.name)))


def _name(path: bytes | str | Path, policy: NativePathPolicy) -> bytes:
    raw = os.fsencode(path)
    if policy is NativePathPolicy.WINDOWS:
        # Git supplies UTF-8 names with slash separators on Windows. Native
        # callers may supply backslashes; the stored identity always uses '/'.
        try:
            text = raw.decode("utf-8")
        except UnicodeError as error:
            raise PathObservationError(PathProblem.INVALID, raw, "Windows names must be valid UTF-8") from error
        if ntpath.isreserved(text):
            raise PathObservationError(PathProblem.INVALID, raw, "reserved Windows path")
        raw = raw.replace(b"\\", b"/")
        if ntpath.splitdrive(raw)[0] or b":" in raw:
            raise PathObservationError(PathProblem.INVALID, raw, "drive/UNC/stream paths are not relative names")
    if b"\0" in raw or any(part in {b"", b".", b".."} for part in raw.split(b"/")):
        raise PathObservationError(PathProblem.INVALID, raw, "require a relative name without traversal or empty components")
    return raw


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _chain(root: Path, name: bytes) -> tuple[tuple[Path, tuple[int, ...]], ...]:
    result = []
    current = root
    parts = name.split(b"/")
    for index, part in enumerate((b"", *parts)):
        if part:
            current /= os.fsdecode(part)
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        if current.is_junction():
            raise PathObservationError(PathProblem.UNSUPPORTED, name, "junction traversal is not supported")
        if index < len(parts) and not stat.S_ISDIR(info.st_mode):
            raise PathObservationError(PathProblem.UNSUPPORTED, name, "parent must be a directory, not a link or file")
        result.append((current, _stamp(info)))
    return tuple(result)


def _read_file(path: Path, name: bytes, expected: tuple[int, ...], limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        if _stamp(os.fstat(descriptor)) != expected:
            raise PathObservationError(PathProblem.CHANGED, name, "opened file differs from the observed leaf")
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if not remaining:
            raise PathObservationError(PathProblem.LIMIT, name, "file exceeds the byte budget")
        if _stamp(os.fstat(descriptor)) != expected:
            raise PathObservationError(PathProblem.CHANGED, name, "file changed while being read")
        content = b"".join(chunks)
        if len(content) != expected[3]:
            raise PathObservationError(PathProblem.CHANGED, name, "read length differs from the observed size")
        return content
    finally:
        os.close(descriptor)


def observe_paths(
    root: Path,
    paths: Sequence[bytes | str | Path],
    *,
    max_file_bytes: int = 64 * 1024 * 1024,
    max_total_bytes: int = 256 * 1024 * 1024,
    max_paths: int = 100000,
) -> PathInventory:
    """Observe an explicit native inventory, rejecting unsafe/ambiguous entries.

    Resolve the caller-selected root once. Reject links/junctions below that root
    except a leaf symlink, whose target is recorded without following it. Missing
    ancestors mean absence; other read errors never do. Directories (including
    submodule roots) are unsupported leaves. Git/submodule inventory selection
    remains the caller's responsibility; no implicit recursion occurs.

    POSIX executable means any 0111 bit. Windows records False (neither ACLs nor
    PATHEXT are permissions), and requires UTF-8 names. POSIX names retain native
    bytes, including undecodable filenames where the filesystem supports them.
    Existing filesystem aliases and duplicate native spellings are rejected.
    Before/after stat checks detect ordinary concurrent changes, not change-and-
    restore attacks or a globally atomic snapshot. Stop writers for stable use.
    """
    for bound in (max_file_bytes, max_total_bytes, max_paths):
        if type(bound) is not int or bound < 0:
            raise ValueError("observation bounds must be nonnegative integers")
    if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes)):
        raise TypeError("paths must be an explicit sequence of native names")
    if len(paths) > max_paths:
        raise PathObservationError(PathProblem.LIMIT, b"", "inventory exceeds the path budget")
    policy = NativePathPolicy.WINDOWS if os.name == "nt" else NativePathPolicy.POSIX
    names = tuple(sorted(_name(path, policy) for path in paths))
    if len(set(names)) != len(names):
        raise PathObservationError(PathProblem.INVALID, b"", "duplicate inventory name")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise PathObservationError(PathProblem.INVALID, b"", "observation root must be a directory")
    observations = []
    chains = []
    identities = set()
    native_names = set()
    total = 0
    for name in names:
        try:
            chain = _chain(root, name)
            path = root / os.fsdecode(name)
            if path in native_names:
                raise PathObservationError(PathProblem.INVALID, name, "native path alias")
            native_names.add(path)
            value: PathValue
            if len(chain) != len(name.split(b"/")) + 1:
                value = MissingPath()
            else:
                stamp = chain[-1][1]
                device, inode, mode, size, *_ = stamp
                if (device, inode) in identities:
                    raise PathObservationError(PathProblem.INVALID, name, "filesystem alias")
                identities.add((device, inode))
                if stat.S_ISREG(mode):
                    limit = min(max_file_bytes, max_total_bytes - total)
                    if size > limit:
                        raise PathObservationError(PathProblem.LIMIT, name, "file exceeds the byte budget")
                    content = _read_file(path, name, stamp, limit)
                    value = RegularFile(content, os.name != "nt" and bool(mode & 0o111))
                    total += len(content)
                elif stat.S_ISLNK(mode):
                    target = os.fsencode(os.readlink(path))
                    total += len(target)
                    if len(target) > max_file_bytes or total > max_total_bytes:
                        raise PathObservationError(PathProblem.LIMIT, name, "link target exceeds the byte budget")
                    value = Symlink(target)
                else:
                    raise PathObservationError(PathProblem.UNSUPPORTED, name, "expected missing path, regular file or symlink")
            if _chain(root, name) != chain:
                raise PathObservationError(PathProblem.CHANGED, name, "path or ancestry changed while being observed")
            observations.append(PathObservation(name, value))
            chains.append(chain)
        except OSError as error:
            raise PathObservationError(PathProblem.UNREADABLE, name, str(error)) from error
    for name, chain in zip(names, chains, strict=True):
        try:
            if _chain(root, name) != chain:
                raise PathObservationError(PathProblem.CHANGED, name, "inventory changed before observation completed")
        except OSError as error:
            raise PathObservationError(PathProblem.UNREADABLE, name, str(error)) from error
    return PathInventory(policy, tuple(observations))


def fingerprint_paths(inventory: PathInventory) -> str:
    """SHA-256 of v1 framing; order-independent, including empty inventories."""
    if not isinstance(inventory, PathInventory):
        raise TypeError("fingerprinting requires a PathInventory")
    digest = hashlib.sha256(b"research-repo-tools/path-observations/v1\0")

    def frame(value: bytes) -> None:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    frame(inventory.policy.value.encode("ascii"))
    digest.update(len(inventory.observations).to_bytes(8, "big"))
    for item in inventory.observations:
        frame(item.name)
        match item.value:
            case MissingPath():
                frame(b"missing")
            case RegularFile(content, executable):
                frame(b"regular")
                frame(b"1" if executable else b"0")
                frame(content)
            case Symlink(target):
                frame(b"symlink")
                frame(target)
            case _ as unreachable:
                assert_never(unreachable)
    return digest.hexdigest()
