"""Install-free, read-only observation of release checkout identity and dirtiness."""

import os
import shlex
import shutil
from collections.abc import Callable
from pathlib import Path

READ_ONLY_OPTIONS = ("--no-pager", "--no-optional-locks", "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "diff.autoRefreshIndex=false")


def git_context(checkout: Path) -> tuple[Path, dict[str, str]]:
    """Pin Git and its child PATH outside the source being inspected."""
    root = checkout.resolve()
    paths = [entry for entry in os.get_exec_path() if Path(entry).is_absolute() and not Path(entry).resolve().is_relative_to(root)]
    environment = {key: value for key, value in os.environ.items() if key.upper() != "PATH"}
    environment["PATH"] = os.pathsep.join(paths)
    # An explicit absolute candidate prevents Windows' implicit cwd search.
    for entry in paths:
        executable = shutil.which(str(Path(entry) / "git"))
        if executable is not None and not Path(executable).resolve().is_relative_to(root):
            return Path(executable), environment
    raise ValueError("release checkout requires Git on an absolute PATH entry outside the checkout")


def require_checkout(commit: str, run: Callable[[list[str]], bytes], *, executable: Path) -> None:
    """Require ordinary index entries, all submodules clean, and stable HEAD."""

    def git(*args: str) -> bytes:
        return run([*READ_ONLY_OPTIONS, *args])

    head = git("rev-parse", "HEAD").decode("ascii").strip()
    for args in (
        ("ls-files", "-v", "-z"),
        ("submodule", "foreach", "--quiet", "--recursive", shlex.join((executable.as_posix(), *READ_ONLY_OPTIONS, "ls-files", "-v", "-z"))),
    ):
        # Recursive ls-files alone omits inactive submodules and gitlink flags.
        entries = git(*args).split(b"\0")
        if any(entry[:1].islower() or entry[:1] == b"S" for entry in entries):
            raise ValueError("observed release checkout has assume-unchanged or skip-worktree index entries")
    dirty = git("status", "--porcelain", "-z", "--untracked-files=all", "--ignore-submodules=none")
    current = git("rev-parse", "HEAD").decode("ascii").strip()
    if head != commit or current != commit or dirty:
        raise ValueError("observed release checkout is dirty or differs from the captured commit")
