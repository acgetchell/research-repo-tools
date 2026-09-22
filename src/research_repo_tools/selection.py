"""Tracked and nonignored input selection and bounded argument-vector batches."""

import os
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath

from research_repo_tools.files import _validate_distinct_paths
from research_repo_tools.process import resolve_executable, run_command_live, run_git_bytes
from research_repo_tools.publication import _path

__all__ = ["argument_batches", "run_selected", "select_files"]


def select_files(root: Path, *, include: Sequence[str] = (), exclude: Sequence[str] = ()) -> tuple[str, ...]:
    """Select existing regular tracked and nonignored untracked files, sorted.

    Includes use Git pathspec syntax; exclusions use case-sensitive POSIX globs.
    Deleted files are omitted. Links (including parent links), special files,
    unsafe portable names, and aliases fail. Git failures never become an empty
    selection. NUL transport preserves whitespace and newlines in Git output,
    although control characters in portable input names are rejected.
    """
    root = root.resolve(strict=True)
    for patterns in (include, exclude):
        if isinstance(patterns, (str, bytes)) or any(not isinstance(item, str) or not item or "\0" in item for item in patterns):
            raise ValueError("file patterns must be nonempty strings without NUL")
    result = run_git_bytes(["--no-pager", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *include], cwd=root)
    if result.stdout and not result.stdout.endswith(b"\0"):
        raise ValueError("Git file inventory is not NUL terminated")
    selected = []
    for name in sorted({os.fsdecode(value) for value in result.stdout.split(b"\0") if value}):
        if any(PurePosixPath(name).full_match(pattern) for pattern in exclude):
            continue
        path = _path(root, name)
        if not path.exists():
            continue
        if not path.is_file():
            raise ValueError(f"selected input is not a regular file: {name}")
        selected.append(name)
    _validate_distinct_paths(tuple(root / name for name in selected))
    return tuple(selected)


def _argument_size(args: Sequence[str]) -> int:
    # Bound both Windows' UTF-16 command line (including quoting) and POSIX's
    # encoded argument bytes. Leave room for the environment and launcher.
    windows = len(subprocess.list2cmdline(args).encode("utf-16-le")) // 2 + 1
    return max(windows, sum(len(os.fsencode(arg)) + 1 for arg in args))


def argument_batches(command: Sequence[str], files: Iterable[str], *, batch_size: int = 100, argument_limit: int = 24000) -> tuple[tuple[str, ...], ...]:
    """Append whole file arguments without shell quoting or splitting names.

    Validate all batches before execution. Empty input produces no invocation;
    an individual oversized argument fails instead of relying on platform limits.
    """
    if not command or isinstance(command, (str, bytes)) or any(not isinstance(arg, str) or "\0" in arg for arg in command):
        raise ValueError("command must be a nonempty argument vector without NUL")
    if type(batch_size) is not int or batch_size < 1 or type(argument_limit) is not int or argument_limit < 1:
        raise ValueError("batch size and argument limit must be positive integers")
    if _argument_size(command) > argument_limit:
        raise ValueError("command exceeds the argument limit")
    batches = []
    batch: list[str] = []
    for name in files:
        if not isinstance(name, str) or not name or "\0" in name:
            raise ValueError("file arguments must be nonempty strings without NUL")
        # ./ prevents a leading dash from being interpreted as a tool option.
        argument = "./" + name
        if _argument_size([*command, argument]) > argument_limit:
            raise ValueError(f"file argument exceeds the argument limit: {name!r}")
        if batch and (len(batch) >= batch_size or _argument_size([*command, *batch, argument]) > argument_limit):
            batches.append(tuple([*command, *batch]))
            batch = []
        batch.append(argument)
    if batch:
        batches.append(tuple([*command, *batch]))
    return tuple(batches)


def run_selected(
    root: Path,
    command: Sequence[str],
    *,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    batch_size: int = 100,
    env: Mapping[str, str] | None = None,
    timeout: float | None = 300,
) -> int:
    """Run selected files in bounded batches, stopping on the first failure."""
    files = select_files(root, include=include, exclude=exclude)
    if not command:
        raise ValueError("files run requires a command after --")
    if not files:
        argument_batches(command, (), batch_size=batch_size)
        return 0
    executable = resolve_executable(command[0], cwd=root, env=env)
    batches = argument_batches([str(executable), *command[1:]], files, batch_size=batch_size)
    for batch in batches:
        run_command_live(batch[0], batch[1:], cwd=root, env=env, timeout=timeout)
    return len(files)
