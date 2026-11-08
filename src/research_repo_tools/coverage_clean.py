"""Preview and remove retained coverage shards without discarding current reports.

Callers must stop coverage writers before applying a plan. Filesystem snapshots
detect stale previews; they do not lock independently managed coverage processes.
"""

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

__all__ = ["CleanPlan", "FileRemoval", "RunRemoval", "apply_clean", "plan_clean"]


@dataclass(frozen=True)
class FileRemoval:
    """A regular coverage file and its observed size and filesystem identity."""

    path: Path
    size: int
    identity: tuple[int, int, int, int]


@dataclass(frozen=True)
class RunRemoval:
    """A flat run directory containing only recognized coverage artifacts."""

    path: Path
    identity: tuple[int, int, int, int]
    files: tuple[FileRemoval, ...]

    @property
    def size(self) -> int:
        """Return the total file bytes selected inside this run directory."""
        return sum(file.size for file in self.files)


@dataclass(frozen=True)
class CleanPlan:
    """A snapshot to revalidate before deletion; preserved paths are not removed."""

    root: Path
    removals: tuple[FileRemoval | RunRemoval, ...]
    preserved: tuple[Path, ...]

    @property
    def size(self) -> int:
        """Return selected file bytes, without estimating filesystem allocation."""
        return sum(removal.size for removal in self.removals)


def _plain(path: Path) -> None:
    if path.is_symlink() or path.is_junction():
        raise ValueError(f"coverage cleanup refuses links or junctions: {path}")


def _identity(path: Path) -> tuple[int, int, int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns


def _file(path: Path) -> FileRemoval | None:
    _plain(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        return None
    return FileRemoval(path, info.st_size, (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns))


def plan_clean(root: Path) -> CleanPlan:
    """Select root ``.coverage.*`` files and flat ``coverage/{failed-,.run-}*`` runs.

    Preserve the root database, current reports, and directories with unfamiliar
    contents. Reject symlinks/junctions at selected boundaries without following
    them. No configuration is executed and no file contents need to be loaded.
    """
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"coverage cleanup root must be a directory: {root}")
    removals: list[FileRemoval | RunRemoval] = []
    preserved: list[Path] = []
    for path in sorted(root.iterdir()):
        if path.name.startswith(".coverage."):
            if (file := _file(path)) is not None:
                removals.append(file)
            else:
                preserved.append(path)
    directory = root / "coverage"
    _plain(directory)
    if directory.exists():
        if not directory.is_dir():
            raise ValueError(f"expected coverage directory: {directory}")
        for path in sorted(directory.iterdir()):
            if not path.name.startswith(("failed-", ".run-")):
                continue
            _plain(path)
            if not path.is_dir():
                preserved.append(path)
                continue
            files = []
            recognized = True
            for child in sorted(path.iterdir()):
                if child.name not in {".coverage", "cobertura.xml"} and not child.name.startswith(".coverage."):
                    recognized = False
                    continue
                file = _file(child)
                if file is None:
                    recognized = False
                else:
                    files.append(file)
            if recognized:
                removals.append(RunRemoval(path, _identity(path), tuple(files)))
            else:
                preserved.append(path)
    return CleanPlan(root, tuple(removals), tuple(preserved))


def apply_clean(plan: CleanPlan) -> None:
    """Recheck the whole plan, then delete listed files and empty run directories.

    Refuse stale plans before removing anything. An I/O failure stops cleanup and
    reports partial progress; already removed artifacts cannot be rolled back.
    The caller must keep coverage writers stopped throughout this operation.
    """
    if plan_clean(plan.root) != plan:
        raise ValueError("coverage inventory changed; stop coverage writers and preview again")
    for removal in plan.removals:
        try:
            _plain(plan.root / "coverage")
            _plain(removal.path)
            match removal:
                case FileRemoval():
                    removal.path.unlink()
                case RunRemoval():
                    for file in removal.files:
                        _plain(file.path)
                        file.path.unlink()
                    # Do not recursively delete new or unrecognized contents.
                    removal.path.rmdir()
                case _:
                    assert_never(removal)
        except OSError as error:
            raise RuntimeError(
                f"coverage cleanup failed at {removal.path}; earlier removals remain applied; close coverage processes and preview again"
            ) from error


def print_plan(plan: CleanPlan) -> None:
    """Render a deterministic inventory without reading coverage databases."""
    for removal in plan.removals:
        print(f"Would remove coverage: {removal.path} ({removal.size:,} bytes)")
    for path in plan.preserved:
        print(f"Preserve unrecognized coverage entry: {path}")
    print(f"Selected {len(plan.removals)} coverage entries ({plan.size:,} bytes); current database and reports are preserved.")
