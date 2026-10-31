"""Fixed Git status/diff queries that leave the selected index bytes unchanged.

Git attributes and clean filters still apply. This is an inspection contract for
trusted repositories and executables, not a sandbox for arbitrary Git execution.
"""

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from research_repo_tools.process import run_git_bytes

__all__ = ["git_diff_paths_bytes", "git_status_bytes"]

_PATHSPEC_ENVIRONMENT = frozenset({"GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS"})


def _paths(paths: Sequence[str]) -> list[str]:
    if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes)) or any(not isinstance(path, str) for path in paths):  # ty: ignore[redundant-condition-strict]  Protect untyped callers before launching Git.
        raise TypeError("paths must be a sequence of strings")
    if any(not path or "\0" in path for path in paths):
        raise ValueError("paths must be nonempty and contain no NUL; use '.' for the current directory")
    return ["--", *paths]


def _inspect(
    arguments: Sequence[str], *, cwd: Path | None, env: Mapping[str, str] | None, timeout: float, differences: bool = False
) -> subprocess.CompletedProcess[bytes]:
    # A replacement mapping stays a replacement. Never modify the caller's
    # mapping or os.environ. Git's pathspec environment switches can conflict
    # with --literal-pathspecs, so remove them even when explicitly supplied.
    environment = dict(os.environ if env is None else env)
    for key in tuple(environment):
        if (key.upper() if os.name == "nt" else key) in _PATHSPEC_ENVIRONMENT:
            del environment[key]
    if timeout is None:
        raise ValueError("inspection timeout must be positive and finite")
    result = run_git_bytes(
        [
            "--no-pager",
            "--no-optional-locks",
            "--literal-pathspecs",
            "-c",
            "diff.autoRefreshIndex=false",
            "-c",
            "core.fsmonitor=false",
            *arguments,
        ],
        cwd=cwd,
        env=environment,
        input=b"",
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0 and not (differences and result.returncode == 1):
        result.check_returncode()
    return result


def git_status_bytes(
    cwd: Path | None = None, *, paths: Sequence[str] = (), env: Mapping[str, str] | None = None, timeout: float = 30
) -> subprocess.CompletedProcess[bytes]:
    """Return porcelain-v1 NUL status, including individual untracked files.

    Renames are reported as deletion/addition; paths are literal, relative to
    cwd, and output paths are repository-relative. Empty paths selects all.
    Optional index writes and filesystem-monitor hooks are disabled. env replaces
    the environment, apart from the documented inspection overrides. Nonzero
    exits raise CalledProcessError with raw diagnostics; timeout is bounded.
    See docs/process-api.md for the index guarantee and configuration limits.
    """
    return _inspect(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames", "--ignore-submodules=none", *_paths(paths)],
        cwd=cwd,
        env=env,
        timeout=timeout,
    )


def git_diff_paths_bytes(
    cwd: Path | None = None,
    *,
    base: str | None = None,
    cached: bool = False,
    paths: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
    timeout: float = 30,
) -> subprocess.CompletedProcess[bytes]:
    """Return NUL-separated changed paths using Git's content comparison.

    Default compares index to worktree. base compares that tree to worktree;
    cached=True compares HEAD (or base) to index, including an unborn index.
    Untracked files are excluded. Renames report both paths. Output is relative
    to the repository root, including when cwd is nested. Literal paths use cwd.
    Return code is normalized to 1 for selected paths, 0 for none; fatal Git
    exits raise with raw diagnostics. External diff, textconv, automatic index
    refresh and optional index writes are disabled. Clean filters and normal Git stat-cache rules
    still apply; this is not a raw-byte hash of every worktree file.
    """
    boundary = _paths(paths)
    if base is not None and (not isinstance(base, str) or not base or "\0" in base):
        raise ValueError("base must be a nonempty revision without NUL, or None")
    if not isinstance(cached, bool):
        raise TypeError("cached must be bool")
    result = _inspect(
        [
            "diff",
            "--numstat",
            "-z",
            "--exit-code",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--no-relative",
            "--ignore-submodules=none",
            *(["--cached"] if cached else []),
            "--end-of-options",
            *([] if base is None else [base]),
            *boundary,
        ],
        cwd=cwd,
        env=env,
        timeout=timeout,
        differences=True,
    )
    # --name-only --exit-code checks contents only while external diffs are
    # enabled in Git 2.56. Disabling them loses that pass. --numstat compares
    # contents internally, omitting stat-only candidates even with --no-ext-diff.
    # With renames disabled every record is added<TAB>deleted<TAB>literal path.
    paths_out = []
    for record in result.stdout.split(b"\0"):
        if record:
            fields = record.split(b"\t", 2)
            if len(fields) != 3 or not fields[2]:
                raise ValueError("unexpected Git numstat record")
            paths_out.append(fields[2] + b"\0")
    # Git's native exit code can still be 1 for an eliminated stat-only
    # candidate. Report the content-based selection through this narrow API.
    return subprocess.CompletedProcess(result.args, int(bool(paths_out)), b"".join(paths_out), result.stderr)
