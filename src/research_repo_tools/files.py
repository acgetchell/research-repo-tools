"""Shared staged publication and recoverable rollback for files."""

import logging
import os
import secrets
import shutil
import stat
import tempfile
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Never

LOGGER = logging.getLogger(__name__)

__all__ = ["RecoveryError", "publish_directory", "replace_many"]


class RecoveryError(OSError):
    """A failed rollback, with the target and any retained original backup.

    In an incomplete-rollback exception group, the first exception is the
    original failure; subsequent exceptions are RecoveryError instances.
    ``backup`` is None when rollback failed to remove a newly created target.
    ``__cause__`` retains the underlying recovery error.
    """

    def __init__(self, target: Path, backup: Path | None, error: BaseException):
        super().__init__(f"failed to restore {target}: {error}")
        self.target = target
        self.backup = backup
        self.__cause__ = error


@dataclass(frozen=True, slots=True)
class _StagedWrite:
    """One fully staged target and its optional pre-publication backup."""

    target: Path
    staged: Path
    backup: Path | None


def _open_sibling_temporary(path: Path, suffix: str) -> tuple[int, Path]:
    """Create an owner-only collision-resistant temporary file beside *path*."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    for _attempt in range(100):
        candidate = path.with_name(f".{path.name}.{secrets.token_hex(12)}{suffix}")
        try:
            return os.open(candidate, flags, 0o600), candidate
        except FileExistsError:
            continue

    msg = f"Could not reserve a temporary file beside {path}"
    raise FileExistsError(msg)


def _stage_bytes(path: Path, payload: bytes) -> Path:
    """Write and sync *payload* to an unpublished sibling of *path*."""
    existing_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    descriptor, staged_path = _open_sibling_temporary(path, ".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            staged_path.chmod(existing_mode)
    except BaseException:
        _cleanup_temporary_paths((staged_path,))
        raise
    return staged_path


def _stage_backup(path: Path) -> Path:
    """Copy and sync *path* to an unpublished rollback backup."""
    descriptor, backup_path = _open_sibling_temporary(path, ".bak")
    os.close(descriptor)
    try:
        shutil.copyfile(path, backup_path)
        with backup_path.open("rb+") as handle:
            os.fsync(handle.fileno())
        shutil.copystat(path, backup_path)
    except BaseException:
        _cleanup_temporary_paths((backup_path,))
        raise
    return backup_path


def _replace_path(source: Path, destination: Path) -> None:
    """Replace *destination* with *source* through a testable seam."""
    source.replace(destination)


def _cleanup_temporary_paths(paths: Sequence[Path], preserved: set[Path] | None = None) -> None:
    """Best-effort cleanup for unpublished transaction files."""
    preserved = preserved or set()
    for path in paths:
        if path in preserved:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as err:
            LOGGER.warning("Could not remove transaction temporary file %s: %s", path, err)


def _remove_created_directories(directories: Sequence[Path]) -> None:
    """Remove transaction-created directories that are still empty."""
    for directory in sorted(set(directories), key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            # A concurrent writer or an incomplete rollback may have populated it.
            continue


def _ensure_parent_directory(path: Path) -> list[Path]:
    """Create *path* and return the directories created by this call."""
    missing: list[Path] = []
    candidate = path
    while not candidate.exists():
        missing.append(candidate)
        candidate = candidate.parent
    created: list[Path] = []
    try:
        for directory in reversed(missing):
            directory.mkdir()
            created.append(directory)
    except BaseException:
        _remove_created_directories(created)
        raise
    return created


def _transaction_temporary_paths(staged_writes: Sequence[_StagedWrite]) -> list[Path]:
    """Return every temporary path owned by *staged_writes*."""
    return [path for item in staged_writes for path in (item.staged, item.backup) if path is not None]


def _stage_writes(writes: Sequence[tuple[Path, bytes]]) -> tuple[list[_StagedWrite], list[Path]]:
    """Prepare replacement files and rollback backups without publishing."""
    created_directories: list[Path] = []
    staged_writes: list[_StagedWrite] = []
    try:
        for target, payload in writes:
            created_directories.extend(_ensure_parent_directory(target.parent))
            staged_path = _stage_bytes(target, payload)
            try:
                backup_path = _stage_backup(target) if target.exists() else None
            except BaseException:
                _cleanup_temporary_paths((staged_path,))
                raise
            staged_writes.append(_StagedWrite(target, staged_path, backup_path))
    except BaseException:
        _cleanup_temporary_paths(_transaction_temporary_paths(staged_writes))
        _remove_created_directories(created_directories)
        raise
    return staged_writes, created_directories


def _restore_backups(backups: Sequence[tuple[Path, Path | None]]) -> tuple[list[RecoveryError], set[Path]]:
    """Restore committed targets, returning rollback failures and saved backups."""
    rollback_errors: list[RecoveryError] = []
    preserved_backups: set[Path] = set()
    for target, backup in reversed(backups):
        try:
            if backup is None:
                target.unlink(missing_ok=True)
            else:
                _replace_path(backup, target)
        except OSError as rollback_error:
            rollback_errors.append(RecoveryError(target, backup, rollback_error))
            if backup is not None:
                preserved_backups.add(backup)
    return rollback_errors, preserved_backups


def _raise_incomplete_rollback(
    error: BaseException, rollback_errors: list[RecoveryError], backups: Sequence[tuple[Path, Path | None]], preserved: set[Path]
) -> Never:
    message = "File update failed and rollback was incomplete"
    if preserved:
        recovery = "; ".join(f"{target} -> {backup}" for target, backup in backups if backup in preserved)
        message += f"; original content retained at {recovery}"
    raise BaseExceptionGroup(message, [error, *rollback_errors]) from None


type _PathKey = tuple[str, ...] | tuple[int, int, tuple[str, ...]]


def _path_keys(path: Path) -> frozenset[_PathKey]:
    """Identify directory entries, including portable aliases of absent names.

    Case/Unicode collisions are rejected even on case-sensitive filesystems.
    Anchor missing components at an existing parent directory's identity too.
    Leaf inodes are deliberately excluded: replacing separate hard links writes
    separate directory entries and leaves the other links unchanged.
    """
    resolved = path.resolve()
    spelling = tuple(unicodedata.normalize("NFC", part).casefold() for part in resolved.parts)
    parent = resolved.parent
    suffix = [spelling[-1]]
    while True:
        try:
            metadata = parent.stat()
            break
        except FileNotFoundError, NotADirectoryError:
            if parent == parent.parent:
                return frozenset((spelling,))
            suffix.append(unicodedata.normalize("NFC", parent.name).casefold())
            parent = parent.parent
    return frozenset((spelling, (metadata.st_dev, metadata.st_ino, tuple(reversed(suffix)))))


def _paths_alias(first: Path, second: Path) -> bool:
    return not _path_keys(first).isdisjoint(_path_keys(second))


def _validate_distinct_paths(targets: Sequence[Path]) -> tuple[Path, ...]:
    if any(not isinstance(path, Path) for path in targets):
        raise TypeError("Transaction targets must be pathlib.Path instances")
    resolved = tuple(path.resolve() for path in targets)
    selected: set[_PathKey] = set()
    for path in resolved:
        keys = _path_keys(path)
        if not selected.isdisjoint(keys):
            raise ValueError("A publication transaction cannot contain duplicate target paths (including case/Unicode aliases)")
        selected.update(keys)
    if any(not selected.isdisjoint(_path_keys(parent)) for path in resolved for parent in path.parents):
        raise ValueError("A publication transaction cannot contain overlapping target paths")
    return resolved


def _validate_targets(targets: Sequence[Path]) -> tuple[Path, ...]:
    resolved = _validate_distinct_paths(targets)
    for path in targets:
        if path.is_symlink():
            raise ValueError(f"Transaction output must not be a symlink: {path}")
        if path.exists() and not path.is_file():
            raise IsADirectoryError(f"output path exists but is not a file: {path}")
    return resolved


@contextmanager
def preserve_files(paths: Sequence[Path]) -> Iterator[Mapping[Path, bytes | None]]:
    """Back up files before external mutation and restore them on caught failures.

    Yield the original bytes (None for absent files). Backups are fully written
    before the caller can mutate files. Incomplete rollback retains and reports
    the original recovery files; successful updates or rollback remove backups.
    """
    _validate_targets(paths)
    backups: list[tuple[Path, Path | None]] = []
    try:
        for path in paths:
            backups.append((path, _stage_backup(path) if path.exists() else None))
        originals = {target: backup.read_bytes() if backup is not None else None for target, backup in backups}
    except BaseException:
        _cleanup_temporary_paths([backup for _, backup in backups if backup is not None])
        raise

    backup_paths = [backup for _, backup in backups if backup is not None]
    try:
        yield originals
    except BaseException as error:
        rollback_errors, preserved = _restore_backups(backups)
        _cleanup_temporary_paths(backup_paths, preserved)
        if rollback_errors:
            _raise_incomplete_rollback(error, rollback_errors, backups, preserved)
        raise
    else:
        _cleanup_temporary_paths(backup_paths)


def _publish(writes: Sequence[tuple[Path, bytes]], *, expected: Mapping[Path, bytes] | None = None) -> None:
    """Publish byte sequences as one rollback-capable transaction.

    All replacement files and backups are prepared before the first visible
    change. If a later replacement fails, every earlier replacement is restored
    before the original error is re-raised.
    """
    if not writes:
        return

    targets = _validate_targets([path for path, _payload in writes])
    if any(not isinstance(payload, bytes) for _path, payload in writes):
        raise TypeError("Transaction payloads must be bytes")
    writes = tuple((target, payload) for target, (_path, payload) in zip(targets, writes, strict=True))
    guards = tuple(expected.items()) if expected is not None else ()
    guard_targets = _validate_targets([path for path, _payload in guards])
    if any(not isinstance(payload, bytes) for _path, payload in guards):
        raise TypeError("Expected publication snapshots must be bytes")
    if expected is not None and set(targets) - set(guard_targets):
        raise ValueError("Expected publication snapshots must cover every replacement target")

    staged_writes, created_directories = _stage_writes(writes)

    committed: list[_StagedWrite] = []
    try:
        if _validate_targets([path for path, _payload in guards]) != guard_targets:
            raise ValueError("file changed before publication: snapshot paths changed; refusing to overwrite them")
        for target, (_path, original) in zip(guard_targets, guards, strict=True):
            if target.read_bytes() != original:
                raise ValueError(f"file changed before publication: {target}; refusing to overwrite it")
        for item in staged_writes:
            _replace_path(item.staged, item.target)
            committed.append(item)
    except BaseException as publication_error:
        backups = [(item.target, item.backup) for item in committed]
        rollback_errors, preserved_backups = _restore_backups(backups)
        _cleanup_temporary_paths(_transaction_temporary_paths(staged_writes), preserved_backups)
        _remove_created_directories(created_directories)

        if rollback_errors:
            _raise_incomplete_rollback(publication_error, rollback_errors, backups, preserved_backups)
        raise

    backup_paths = [item.backup for item in staged_writes if item.backup is not None]
    _cleanup_temporary_paths(backup_paths)


def replace_many(updates: Mapping[Path, bytes], *, expected: Mapping[Path, bytes] | None = None) -> None:
    """Stage all replacements and backups, then publish with rollback on failure.

    Paths resolve relative to the caller's working directory. Targets must be
    distinct, non-overlapping regular files (or absent), never leaf symlinks.
    Parent symlinks are resolved before staging. Payloads must be bytes.
    New files start owner-only; existing permission bits are preserved where
    supported. All candidates and backups are synced before ordered replacement.
    A caught failure rolls back earlier replacements in reverse order. If that
    fails, an exception group retains the original failure first, followed by
    RecoveryError instances pointing to retained backups. Cleanup is best effort.
    This does not serialize concurrent writers or make the group crash atomic.
    Optional expected snapshots cover every replacement and may include unchanged
    input files. They are rechecked after staging, before the first replacement.
    A mismatch rejects the transaction and preserves current source files.
    """
    _publish(tuple(updates.items()), expected=expected)


def _directory_target(destination: Path) -> Path:
    if not isinstance(destination, Path):
        raise TypeError("Directory destination must be a pathlib.Path")
    # Inspect the lexical path before resolving it: resolving hides links.
    for component in (destination, *destination.parents):
        if component.is_symlink() or component.is_junction():
            raise ValueError(f"report directory must not contain symlinks or junctions: {component}")
    target = destination.resolve()
    if target == target.parent:
        raise ValueError("Cannot publish a filesystem root directory")
    if target.exists() and not target.is_dir():
        raise NotADirectoryError(f"Directory destination is not a directory: {destination}")
    return target


def _sync_directory_tree(candidate: Path) -> None:
    """Reject links/special entries and sync each generated regular file."""
    if not candidate.is_dir():
        raise ValueError("Generated directory must remain a directory")
    paths, pending = [], [candidate]
    while pending:
        path = pending.pop()
        if path.is_symlink() or path.is_junction() or not (path.is_dir() or path.is_file()):
            raise ValueError(f"Generated directory contains a link or special entry: {path}")
        paths.append(path)
        if path.is_dir():
            children = list(path.iterdir())
            # Check siblings, including empty directories, for portable aliases.
            _validate_distinct_paths(children)
            pending.extend(children)
    _validate_distinct_paths([path for path in paths if path.is_file()])
    for path in paths:
        if path.is_file():
            mode = stat.S_IMODE(path.stat().st_mode)
            try:
                # Windows needs a writable handle for fsync. The private staged
                # file can temporarily gain owner access without changing the
                # permissions of the published generation.
                path.chmod(mode | stat.S_IRUSR | stat.S_IWUSR)
                with path.open("rb+") as stream:
                    os.fsync(stream.fileno())
            finally:
                path.chmod(mode)


def _cleanup_directory(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except OSError as error:
        LOGGER.warning("Could not remove transaction directory %s: %s", path, error)


@contextmanager
def publish_directory(destination: Path) -> Iterator[Path]:
    """Generate an entire owned directory privately, then replace its contents.

    Yield an empty sibling staging directory. The caller writes or copies the
    complete generation and validates its domain policy before leaving the
    context. An empty generation removes all old members. Only regular files
    and directories are allowed; output components cannot be links/junctions.
    Existing directory permissions are preserved where supported. New staging
    directories start owner-only; caller-created files retain their own modes.
    Files are synced before commit. Caught generation failures preserve the old
    directory; caught commit failures restore it. Incomplete rollback raises an
    exception group with the original failure first and RecoveryError pointing
    to a retained original directory. Cleanup is best effort. Exclusive ownership
    is required: this does not serialize writers or provide crash atomicity, and
    the destination can briefly be absent between the two directory renames.
    """
    target = _directory_target(destination)
    created = _ensure_parent_directory(target.parent)
    try:
        transaction = Path(tempfile.mkdtemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent))
    except BaseException:
        _remove_created_directories(created)
        raise
    candidate, backup = transaction / "candidate", transaction / "previous"
    retained = False
    try:
        candidate.mkdir(mode=0o700)
        yield candidate
        _sync_directory_tree(candidate)
        if _directory_target(destination) != target:
            raise ValueError("Directory destination changed during generation")
        had_destination = target.exists()
        if had_destination:
            candidate.chmod(stat.S_IMODE(target.stat().st_mode))
        try:
            if had_destination:
                _replace_path(target, backup)
            _replace_path(candidate, target)
        except BaseException as error:
            # Signals can be delivered after a rename succeeded but before its
            # Python call returned. Inspect the actual entries, not a flag set
            # after the call. Move a committed candidate aside before restoring
            # a nonempty original directory (required on Windows and POSIX).
            try:
                if backup.exists():
                    if target.exists():
                        _replace_path(target, candidate)
                    _replace_path(backup, target)
                elif not had_destination and target.exists() and not candidate.exists():
                    _replace_path(target, candidate)
            except BaseException as recovery:
                retained = backup.exists()
                # A recovery rename can itself return through an interrupt
                # after succeeding; an absent backup then means restoration
                # completed. Preserve failures only while recovery is needed.
                if retained or (not had_destination and target.exists()):
                    message = "Directory publication failed and rollback was incomplete"
                    if retained:
                        message += f"; original content retained at {backup}"
                    raise BaseExceptionGroup(
                        message,
                        [error, RecoveryError(target, backup if retained else None, recovery)],
                    ) from None
            raise
    finally:
        if retained:
            if candidate.exists():
                _cleanup_directory(candidate)
        else:
            _cleanup_directory(transaction)
        _remove_created_directories(created)


def replace(path: Path, payload: bytes) -> None:
    """Atomically replace bytes, preserving existing permissions and symlinks."""
    replace_many({path.resolve(): payload})


def replace_if_unchanged(path: Path, expected: bytes, payload: bytes) -> None:
    """Stage bytes, then check the source immediately before atomic replacement.

    Preserve permissions and symlinks. This optimistic guard detects edits during
    staging; it does not serialize independent writers with the final rename.
    """
    target = path.resolve()
    _validate_targets((target,))
    staged = _stage_bytes(target, payload)
    try:
        if path.resolve() != target or target.read_bytes() != expected:
            raise ValueError(f"file changed before publication: {path}; refusing to overwrite it")
        _replace_path(staged, target)
    finally:
        _cleanup_temporary_paths((staged,))


def _write_texts_transactionally(writes: Sequence[tuple[Path, str]]) -> None:
    """Publish explicitly UTF-8 text without platform newline translation."""
    _publish(tuple((path, text.encode("utf-8")) for path, text in writes))


def _write_text_atomic(path: Path, text: str) -> None:
    _write_texts_transactionally(((path, text),))
