"""Install-free, read-only observation of release checkout identity and dirtiness."""

from collections.abc import Callable

READ_ONLY_OPTIONS = ("--no-pager", "--no-optional-locks", "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "diff.autoRefreshIndex=false")


def require_checkout(commit: str, run: Callable[[list[str]], bytes]) -> None:
    """Require ordinary index entries, all submodules clean, and stable HEAD."""

    def git(*args: str) -> bytes:
        return run([*READ_ONLY_OPTIONS, *args])

    head = git("rev-parse", "HEAD").decode("ascii").strip()
    for args in (
        ("ls-files", "-v", "-z"),
        ("submodule", "foreach", "--quiet", "--recursive", "git --no-pager --no-optional-locks ls-files -v -z"),
    ):
        # Recursive ls-files alone omits inactive submodules and gitlink flags.
        entries = git(*args).split(b"\0")
        if any(entry[:1].islower() or entry[:1] == b"S" for entry in entries):
            raise ValueError("observed release checkout has assume-unchanged or skip-worktree index entries")
    dirty = git("status", "--porcelain", "-z", "--untracked-files=all", "--ignore-submodules=none")
    current = git("rev-parse", "HEAD").decode("ascii").strip()
    if head != commit or current != commit or dirty:
        raise ValueError("observed release checkout is dirty or differs from the captured commit")
