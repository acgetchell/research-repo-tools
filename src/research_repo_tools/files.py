"""Shared staged publication and recoverable rollback for files."""

import logging
import os
import secrets
import shutil
import stat
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Never

LOGGER = logging.getLogger(__name__)


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
        staged_path.unlink(missing_ok=True)
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
        backup_path.unlink(missing_ok=True)
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
    path.mkdir(parents=True, exist_ok=True)
    return missing


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
                staged_path.unlink(missing_ok=True)
                raise
            staged_writes.append(_StagedWrite(target, staged_path, backup_path))
    except BaseException:
        _cleanup_temporary_paths(_transaction_temporary_paths(staged_writes))
        _remove_created_directories(created_directories)
        raise
    return staged_writes, created_directories


def _restore_backups(backups: Sequence[tuple[Path, Path | None]]) -> tuple[list[OSError], set[Path]]:
    """Restore committed targets, returning rollback failures and saved backups."""
    rollback_errors: list[OSError] = []
    preserved_backups: set[Path] = set()
    for target, backup in reversed(backups):
        try:
            if backup is None:
                target.unlink(missing_ok=True)
            else:
                _replace_path(backup, target)
        except OSError as rollback_error:
            rollback_errors.append(OSError(f"failed to restore {target}: {rollback_error}"))
            if backup is not None:
                preserved_backups.add(backup)
    return rollback_errors, preserved_backups


def _raise_incomplete_rollback(
    error: BaseException, rollback_errors: list[OSError], backups: Sequence[tuple[Path, Path | None]], preserved: set[Path]
) -> Never:
    message = "File update failed and rollback was incomplete"
    if preserved:
        recovery = "; ".join(f"{target} -> {backup}" for target, backup in backups if backup in preserved)
        message += f"; original content retained at {recovery}"
    raise BaseExceptionGroup(message, [error, *rollback_errors]) from None


def _validate_targets(targets: Sequence[Path]) -> None:
    if len({path.resolve() for path in targets}) != len(targets):
        raise ValueError("A publication transaction cannot contain duplicate target paths")
    for path in targets:
        if path.is_symlink():
            raise ValueError(f"Transaction output must not be a symlink: {path}")
        if path.exists() and not path.is_file():
            raise IsADirectoryError(f"output path exists but is not a file: {path}")


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


def _publish(writes: Sequence[tuple[Path, bytes]]) -> None:
    """Publish byte sequences as one rollback-capable transaction.

    All replacement files and backups are prepared before the first visible
    change. If a later replacement fails, every earlier replacement is restored
    before the original error is re-raised.
    """
    if not writes:
        return

    _validate_targets([path for path, _text in writes])

    staged_writes, created_directories = _stage_writes(writes)

    committed: list[_StagedWrite] = []
    try:
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


def replace_many(updates: Mapping[Path, bytes]) -> None:
    """Stage all replacements and backups, then publish with rollback on failure.

    Targets must be distinct regular files, never symlinks. New files start
    owner-only; existing permissions are preserved. Multiple replacements are
    not crash atomic. Failed rollback preserves and reports recovery backups.
    """
    _publish(tuple(updates.items()))


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
