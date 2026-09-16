"""Byte-level publication evidence independent of any consumer implementation."""

import os
import stat
from pathlib import Path

import pytest

from research_repo_tools import files


def test_all_candidates_and_backups_are_staged_before_any_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = (tmp_path / "first", tmp_path / "nested/second")
    first.write_bytes(b"old\r\n\xff")
    first.chmod(0o640)
    original_mode = stat.S_IMODE(first.stat().st_mode)
    original = files._replace_path

    def publish(source: Path, target: Path) -> None:
        if target == first:
            assert list(second.parent.glob(".second.*.tmp"))
            assert list(tmp_path.glob(".first.*.bak"))[0].read_bytes() == b"old\r\n\xff"
        original(source, target)

    monkeypatch.setattr(files, "_replace_path", publish)
    files.replace_many({first: b"new\r\n\xff", second: b"new\x00data"})
    assert first.read_bytes() == b"new\r\n\xff"
    assert second.read_bytes() == b"new\x00data"
    assert stat.S_IMODE(first.stat().st_mode) == original_mode
    assert not list(tmp_path.rglob("*.bak"))


@pytest.mark.skipif(os.name == "nt", reason="exact POSIX permission bits are not supported on Windows")
def test_publication_preserves_posix_permissions(tmp_path: Path) -> None:
    path = tmp_path / "private"
    path.write_bytes(b"original")
    path.chmod(0o640)
    files.replace(path, b"updated")
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_late_failure_restores_existing_bytes_and_removes_new_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second, third = (tmp_path / name for name in ("first", "nested/second", "third"))
    first.write_bytes(b"old\r\n\xff")
    original = files._replace_path

    def publish(source: Path, target: Path) -> None:
        if target == third:
            raise OSError("publication failed")
        original(source, target)

    monkeypatch.setattr(files, "_replace_path", publish)
    with pytest.raises(OSError, match="publication failed"):
        files.replace_many({first: b"new", second: b"new", third: b"new"})
    assert first.read_bytes() == b"old\r\n\xff"
    assert set(tmp_path.iterdir()) == {first}


def test_incomplete_rollback_retains_original_and_reports_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = (tmp_path / "first", tmp_path / "second")
    first.write_bytes(b"original\xff")
    original = files._replace_path

    def publish(source: Path, target: Path) -> None:
        if target == second or source.suffix == ".bak":
            raise OSError("filesystem failure")
        original(source, target)

    monkeypatch.setattr(files, "_replace_path", publish)
    with pytest.raises(ExceptionGroup, match="rollback was incomplete") as failure:
        files.replace_many({first: b"candidate", second: b"candidate"})
    (backup,) = tmp_path.glob("*.bak")
    assert backup.read_bytes() == b"original\xff"
    assert str(backup) in str(failure.value)
    assert len(failure.value.exceptions) == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_duplicate_targets_are_rejected_before_directory_creation(tmp_path: Path) -> None:
    destination = tmp_path / "new/file"
    with pytest.raises(ValueError, match="duplicate target"):
        files.replace_many({destination: b"a", destination.parent / "../new/file": b"b"})
    assert not list(tmp_path.iterdir())


def test_external_update_cannot_begin_if_any_backup_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = tmp_path / "manifest", tmp_path / "lock"
    for path in (first, second):
        path.write_bytes(b"original\r\n")
    original = files._stage_backup

    def fail_second_backup(path: Path) -> Path:
        if path == second:
            raise OSError("backup storage unavailable")
        return original(path)

    monkeypatch.setattr(files, "_stage_backup", fail_second_backup)
    with pytest.raises(OSError, match="backup storage unavailable"), files.preserve_files((first, second)):
        pytest.fail("external update began without complete recovery backups")
    assert set(tmp_path.iterdir()) == {first, second}
    assert first.read_bytes() == second.read_bytes() == b"original\r\n"


def test_successful_external_update_removes_backups(tmp_path: Path) -> None:
    first, new = tmp_path / "manifest", tmp_path / "lock"
    first.write_bytes(b"original\r\n")
    with files.preserve_files((first, new)) as originals:
        assert originals == {first: b"original\r\n", new: None}
        backups = set(tmp_path.iterdir()) - {first}
        assert len(backups) == 1
        assert backups.pop().read_bytes() == b"original\r\n"
        first.write_bytes(b"updated\n")
        new.write_bytes(b"new lock\n")
    assert set(tmp_path.iterdir()) == {first, new}
    assert first.read_bytes() == b"updated\n"
    assert new.read_bytes() == b"new lock\n"
