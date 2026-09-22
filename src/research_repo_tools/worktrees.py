"""Explicit isolated Git measurement checkouts and exact working-tree snapshots."""

import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from research_repo_tools.files import _validate_distinct_paths
from research_repo_tools.process import run_git_bytes
from research_repo_tools.publication import _path, _publication_name
from research_repo_tools.selection import select_files

__all__ = ["TreeSnapshot", "apply_snapshot", "capture_snapshot", "temporary_worktree"]


@dataclass(frozen=True, slots=True)
class TreeSnapshot:
    """Commit, exact binary patch, and regular untracked bytes/modes."""

    revision: str
    patch: bytes
    untracked: tuple[tuple[str, bytes, int], ...]

    def __post_init__(self) -> None:
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", self.revision) is None:
            raise ValueError("snapshot requires a full lowercase Git revision")
        if not isinstance(self.patch, bytes):
            raise TypeError("snapshot patch must be exact bytes")
        object.__setattr__(self, "untracked", tuple(tuple(item) for item in self.untracked))
        for name, payload, mode in self.untracked:
            _publication_name(name)
            if not isinstance(payload, bytes) or type(mode) is not int or mode < 0 or mode > 0o777:
                raise ValueError("snapshot files require exact bytes and ordinary permission bits")
        if len({name for name, _, _ in self.untracked}) != len(self.untracked):
            raise ValueError("snapshot contains duplicate untracked paths")


def _git(root: Path, args: list[str], **kwargs):
    return run_git_bytes(["--no-pager", "--no-replace-objects", *args], cwd=root, **kwargs)


def capture_snapshot(root: Path) -> TreeSnapshot:
    """Capture tracked changes and nonignored new files without changing Git.

    Reject symlinks and submodules rather than silently following them. Verify
    the patch, revision, untracked inventory and bytes twice to detect ordinary
    concurrent edits. Exclusive source writer control is still required.
    """
    root = root.resolve(strict=True)

    def capture() -> TreeSnapshot:
        if _git(root, ["rev-parse", "--show-prefix"]).stdout.strip():
            raise ValueError("snapshot requires the Git repository root")
        select_files(root)  # Preflight all current inputs, including links.
        revision = _git(root, ["rev-parse", "--verify", "HEAD^{commit}"]).stdout.decode("ascii").strip()
        patch = _git(root, ["diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--"]).stdout
        inventory = _git(root, ["ls-files", "--others", "--exclude-standard", "-z", "--"]).stdout
        if inventory and not inventory.endswith(b"\0"):
            raise ValueError("untracked inventory must be NUL terminated")
        untracked = []
        for name in sorted({os.fsdecode(item) for item in inventory.split(b"\0") if item}):
            path = _path(root, name)
            mode = stat.S_IMODE(path.stat().st_mode) & 0o777
            untracked.append((name, path.read_bytes(), mode))
        return TreeSnapshot(revision, patch, tuple(untracked))

    snapshot = capture()
    if snapshot != capture():
        raise ValueError("working tree changed during snapshot capture; retry with exclusive writer control")
    return snapshot


def apply_snapshot(checkout: Path, snapshot: TreeSnapshot) -> None:
    """Apply only to a newly created isolated checkout at the captured revision.

    Uses exact binary stdin. Failures can leave the disposable checkout partly
    modified; its owning temporary_worktree context handles cleanup.
    """
    checkout = checkout.resolve(strict=True)
    revision = _git(checkout, ["rev-parse", "--verify", "HEAD^{commit}"]).stdout.decode("ascii").strip()
    if revision != snapshot.revision:
        raise ValueError("snapshot checkout revision does not match captured HEAD")
    paths = [(_path(checkout, name), payload, mode) for name, payload, mode in snapshot.untracked]
    _validate_distinct_paths(tuple(path for path, _, _ in paths))
    if any(path.exists() for path, _, _ in paths):
        raise ValueError("snapshot untracked input would overwrite a checkout file")
    if snapshot.patch:
        _git(checkout, ["apply", "--binary", "--whitespace=nowarn", "-"], input=snapshot.patch)
    for path, payload, mode in paths:
        # Recheck after applying the patch, which could have created a parent.
        _path(checkout, path.relative_to(checkout).as_posix())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(payload)
        path.chmod(mode)


@contextmanager
def temporary_worktree(root: Path, destination: Path, revision: str, *, allow_git_mutations: bool = False) -> Iterator[Path]:
    """Create/remove a detached measurement checkout only with explicit opt-in.

    The caller supplies an absent destination and retains its parent on cleanup
    failure. Cleanup failures name the recovery path and preserve any body error.
    No force-prune or broad cleanup is performed.
    """
    if not allow_git_mutations:
        raise ValueError("measurement requires explicit permission to create and remove Git worktrees")
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision) is None:
        raise ValueError("worktree requires a resolved full lowercase commit ID")
    root = root.resolve(strict=True)
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink() or any(parent.is_symlink() for parent in destination.parents):
        raise ValueError(f"worktree destination must be absent and have no symlink parents: {destination}")
    try:
        _git(root, ["worktree", "add", "--detach", str(destination), revision], timeout=600)
    except BaseException as original:
        if destination.exists():
            try:
                _git(root, ["worktree", "remove", "--force", str(destination)], timeout=600)
            except BaseException as cleanup:
                raise BaseExceptionGroup(
                    "worktree creation and cleanup failed", [original, RuntimeError(f"retained worktree {destination}: {cleanup}")]
                ) from cleanup
        raise
    body_error = None
    try:
        yield destination
    except BaseException as error:
        body_error = error
        raise
    finally:
        try:
            _git(root, ["worktree", "remove", "--force", str(destination)], timeout=600)
        except BaseException as error:
            recovery = RuntimeError(f"worktree cleanup failed; retained checkout {destination}: {error}")
            if body_error is not None:
                raise BaseExceptionGroup("measurement and worktree cleanup failed", [body_error, recovery]) from error
            raise recovery from error
