"""Controlled Git boundary models; no version-control mutations are executed."""

import subprocess
from pathlib import Path

import pytest

from research_repo_tools import worktrees

REVISION = "a" * 40


def test_apply_snapshot_transports_exact_binary_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch = b"diff --git a/file b/file\r\nGIT binary patch\n\xff\0\r\n"
    calls = []

    def git(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, REVISION.encode() + b"\n", b"")

    monkeypatch.setattr(worktrees, "run_git_bytes", git)
    worktrees.apply_snapshot(tmp_path, worktrees.TreeSnapshot(REVISION, patch, (("a b/new.bin", b"\xff\r\n", 0o644),)))
    assert calls[1][1]["input"] == patch
    assert (tmp_path / "a b/new.bin").read_bytes() == b"\xff\r\n"


def test_worktree_requires_opt_in_before_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worktrees, "run_git_bytes", lambda *args, **kwargs: pytest.fail("unexpected Git operation"))
    with pytest.raises(ValueError, match="explicit"):
        with worktrees.temporary_worktree(tmp_path, tmp_path / "checkout", REVISION):
            pytest.fail("unexpected body")


def test_cleanup_preserves_body_and_recovery_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def git(args, **kwargs):
        calls.append(args)
        if "remove" in args:
            raise subprocess.CalledProcessError(3, args)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(worktrees, "run_git_bytes", git)
    destination = tmp_path.resolve() / "checkout"
    with pytest.raises(ExceptionGroup) as caught:
        with worktrees.temporary_worktree(tmp_path, destination, REVISION, allow_git_mutations=True):
            raise ValueError("measurement failed")
    assert str(caught.value.exceptions[0]) == "measurement failed"
    assert str(destination) in str(caught.value.exceptions[1])
    assert "remove" in calls[-1]


def test_capture_detects_untracked_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "new.txt"
    path.write_bytes(b"before\r\n")
    monkeypatch.setattr(worktrees, "select_files", lambda *args: ("new.txt",))
    inventories = 0

    def git(args, **kwargs):
        nonlocal inventories
        if "--show-prefix" in args:
            output = b""
        elif "rev-parse" in args:
            output = REVISION.encode()
        elif "diff" in args:
            output = b"binary\r\n"
        else:
            inventories += 1
            if inventories == 2:
                path.write_bytes(b"after\r\n")
            output = b"new.txt\0"
        return subprocess.CompletedProcess(args, 0, output, b"")

    monkeypatch.setattr(worktrees, "run_git_bytes", git)
    with pytest.raises(ValueError, match="changed"):
        worktrees.capture_snapshot(tmp_path)


def test_snapshot_aliases_fail_before_applying_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def git(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, REVISION.encode(), b"")

    monkeypatch.setattr(worktrees, "run_git_bytes", git)
    snapshot = worktrees.TreeSnapshot(REVISION, b"patch", (("File", b"a", 0o644), ("file", b"b", 0o644)))
    with pytest.raises(ValueError, match="alias|duplicate"):
        worktrees.apply_snapshot(tmp_path, snapshot)
    assert len(calls) == 1
    assert not list(tmp_path.iterdir())
