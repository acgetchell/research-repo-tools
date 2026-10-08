"""Opt-in locked JupyterLab launch and preview-first source notebook restoration."""

import ntpath
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from research_repo_tools.config import Config
from research_repo_tools.files import _paths_alias, _validate_distinct_paths
from research_repo_tools.notebooks import KERNEL, project_environment, runtime
from research_repo_tools.process import format_exception_diagnostics, run_command_live, run_git_bytes
from research_repo_tools.toolchain_config import home

__all__ = ["NotebookResetPlan", "launch", "reset"]


@dataclass(frozen=True, slots=True)
class NotebookResetPlan:
    """Absolute restore/delete paths and pinned tree (None means the index)."""

    sources: tuple[Path, ...]
    cleanup: tuple[Path, ...]
    revision: str | None


def _overlap(first: Path, second: Path) -> bool:
    return any(_paths_alias(parent, second) for parent in (first, *first.parents)) or any(_paths_alias(first, parent) for parent in second.parents)


def _validate_windows_parts(parts: Sequence[str]) -> None:
    for part in parts:
        if ntpath.isreserved(part):
            raise ValueError(f"notebook workflow path component is reserved on Windows: {part!r}")


def _safe_path(root: Path, value: str | Path) -> Path:
    raw = os.fspath(value)
    path = Path(raw)
    if not raw or "\0" in raw or ".." in path.parts or path.drive and not path.is_absolute():
        raise ValueError(f"unsafe notebook workflow path: {raw!r}")
    path = path if path.is_absolute() else root / path
    if path == root or not path.is_relative_to(root) or any(part.casefold() == ".git" for part in path.relative_to(root).parts):
        raise ValueError(f"notebook workflow paths must be below the consumer root and outside .git: {raw!r}")
    if os.name == "nt":
        _validate_windows_parts(path.relative_to(root).parts)
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink() or part.is_junction():
            raise ValueError(f"notebook workflow path contains a symlink or junction: {part}")
        if part != path and part.exists() and not part.is_dir():
            raise ValueError(f"notebook workflow parent is not a directory: {part}")
    if path.resolve() != path:
        raise ValueError(f"notebook workflow path aliases another location: {raw!r}")
    return path


def _protected(settings: Config) -> tuple[Path, ...]:
    return (
        settings.root / ".git",
        settings.root / "pyproject.toml",
        settings.root / "uv.lock",
        settings.root / ".python-version",
        home(),
        project_environment(settings.root),
    )


def launch(settings: Config, *, browser: bool | None = None, scratch_dir: str | None = None) -> int:
    """Stream JupyterLab from uv's locked managed project environment until exit.

    Consumers declare JupyterLab in their notebook group and synchronize their
    project kernel. All session caches live in a private temporary directory
    beneath scratch_dir; the caller's process environment is preserved.
    """
    if browser is not None and type(browser) is not bool:
        raise ValueError("notebook browser policy must be a boolean")
    options = settings.notebooks
    environment = _safe_path(settings.root, os.environ.get("UV_PROJECT_ENVIRONMENT") or ".venv")
    scratch = _safe_path(settings.root, options.lab.scratch_dir if scratch_dir is None else scratch_dir)
    if any(_overlap(scratch, path) for path in _protected(settings)):
        raise ValueError(f"notebook scratch directory overlaps a protected project path: {scratch}")
    for source in options.reset.sources:
        if _overlap(scratch, _safe_path(settings.root, source)):
            raise ValueError(f"notebook scratch directory overlaps declared sources: {scratch}")
    working = settings.path(options.cwd).resolve()
    if not working.is_dir() or not working.is_relative_to(settings.root):
        raise ValueError(f"notebook working directory must exist inside the consumer root: {working}")
    managed = runtime(settings)
    env = managed.project_environment()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    for key in ("UV_FROZEN", "UV_NO_SYNC", "UV_NO_PROJECT", "UV_NO_MANAGED_PYTHON", "UV_PYTHON", "UV_ACTIVE"):
        env.pop(key, None)
    # Resolve relative overrides at the consumer root before uv changes cwd.
    env["UV_PROJECT_ENVIRONMENT"] = str(environment)
    open_browser = options.lab.browser if browser is None else browser
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lab-", dir=scratch) as temporary:
        for key in ("IPYTHONDIR", "MPLCONFIGDIR", "JUPYTER_CONFIG_DIR", "JUPYTER_DATA_DIR", "JUPYTER_RUNTIME_DIR"):
            directory = Path(temporary) / key.lower()
            directory.mkdir()
            env[key] = str(directory)
        env["JUPYTER_PREFER_ENV_PATH"] = "1"
        try:
            result = run_command_live(
                managed.uv_status().path,
                [
                    "run",
                    "--locked",
                    "--managed-python",
                    "--python",
                    managed.plan.python,
                    "--project",
                    str(settings.root),
                    "--group",
                    "dev",
                    "--group",
                    options.group,
                    "python",
                    "-m",
                    "jupyterlab",
                    f"--ServerApp.open_browser={open_browser}",
                    f"--ServerApp.root_dir={working}",
                    f"--MappingKernelManager.default_kernel_name={KERNEL}",
                ],
                cwd=working,
                env=env,
                timeout=None,
                check=False,
            )
        except KeyboardInterrupt:
            print("JupyterLab launch interrupted.", file=sys.stderr)
            return 130
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


def _git_environment() -> dict[str, str]:
    # Do not let inherited repository/index or pathspec selectors redirect a
    # restore. Explicit paths and NUL transport retain their literal spelling.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _git(root: Path, env: dict[str, str], args: list[str], *, input: bytes | None = None) -> bytes:
    return run_git_bytes(["--no-pager", "--literal-pathspecs", *args], cwd=root, env=env, input=input).stdout


def _records(output: bytes, *, tree: bool = False) -> tuple[tuple[str, str, str], ...]:
    if output and not output.endswith(b"\0"):
        raise ValueError("Git notebook inventory is not NUL terminated")
    records = []
    for record in output.split(b"\0"):
        if not record:
            continue
        header, separator, name = record.partition(b"\t")
        fields = header.split(b" ")
        if not separator or len(fields) != 3 or not name:
            raise ValueError("Git returned a malformed notebook inventory")
        mode, kind = fields[0].decode("ascii"), fields[1 if tree else 2].decode("ascii")
        spelling = os.fsdecode(name)
        posix = PurePosixPath(spelling)
        if posix.is_absolute() or any(part in ("", ".", "..") for part in spelling.split("/")) or os.name == "nt" and "\\" in spelling:
            raise ValueError(f"Git returned an unsafe notebook path: {spelling!r}")
        records.append((spelling, mode, kind))
    return tuple(records)


def _walk_error(error: OSError) -> None:
    raise error


def _reject_source_alias(path: Path, sources: Sequence[Path]) -> None:
    if path.is_file() and any(source.exists() and path.samefile(source) for source in sources):
        raise ValueError(f"notebook cleanup aliases a source notebook: {path}")


def _validate_cleanup_tree(path: Path, sources: Sequence[Path]) -> None:
    if not path.exists():
        return
    if path.is_file():
        _reject_source_alias(path, sources)
        return
    if not path.is_dir():
        raise ValueError(f"notebook cleanup path is not a regular file or directory: {path}")
    for directory, dirs, names in os.walk(path, followlinks=False, onerror=_walk_error):
        for name in (*dirs, *names):
            child = Path(directory) / name
            if child.is_symlink() or child.is_junction() or not (child.is_dir() or child.is_file()):
                raise ValueError(f"notebook cleanup contains a link or special file: {child}")
            if child.name.casefold() == ".git":
                raise ValueError(f"notebook cleanup contains a Git repository: {child}")
            _reject_source_alias(child, sources)


def _plan(settings: Config, paths: Sequence[Path], revision: str | None, env: dict[str, str]) -> NotebookResetPlan:
    declared = paths or tuple(Path(value) for value in settings.notebooks.reset.sources)
    if not declared:
        raise ValueError("select notebook reset source files/directories explicitly or configure notebooks.reset.sources")
    if revision is not None and (not isinstance(revision, str) or not revision or "\0" in revision):
        raise ValueError("notebook reset revision must be a nonempty string without NUL")
    root = settings.root.resolve(strict=True)
    selectors = tuple(_safe_path(root, path) for path in declared)
    _validate_distinct_paths(selectors)
    top = _git(root, env, ["rev-parse", "--show-toplevel"]).removesuffix(b"\n")
    if os.name == "nt":
        top = top.removesuffix(b"\r")
    if Path(os.fsdecode(top)).resolve() != root:
        raise ValueError("notebook reset requires the consumer root to be the Git worktree root")
    indexed = _records(_git(root, env, ["ls-files", "--stage", "-z"]))
    tree = None
    inventory = indexed
    if revision is not None:
        resolved = _git(root, env, ["rev-parse", "--verify", "--end-of-options", revision + "^{tree}"]).strip()
        if len(resolved) not in (40, 64) or any(byte not in b"0123456789abcdef" for byte in resolved):
            raise ValueError("Git returned an invalid notebook restore tree")
        tree = resolved.decode("ascii")
        inventory = _records(_git(root, env, ["ls-tree", "-r", "-z", "--full-tree", tree]), tree=True)
    sources = []
    for selector in selectors:
        matches = []
        for name, mode, kind in inventory:
            path = root / name
            if path.suffix != ".ipynb" or ".ipynb_checkpoints" in path.parts or not (path == selector or path.is_relative_to(selector)):
                continue
            if mode not in ("100644", "100755") or kind != ("blob" if tree is not None else "0"):
                raise ValueError(f"notebook restore source must be a regular, merged Git blob: {name!r}")
            path = _safe_path(root, path)
            if path.exists() and not path.is_file():
                raise ValueError(f"notebook restore target is not a regular file: {path}")
            matches.append(path)
        if not matches:
            raise ValueError(f"no tracked source notebooks in the selected restore source: {selector}")
        sources.extend(matches)
    sources = sorted(sources)
    _validate_distinct_paths(sources)
    scratch = tuple(_safe_path(root, value) for value in settings.notebooks.reset.scratch)
    checkpoints = tuple(_safe_path(root, value) for value in settings.notebooks.reset.checkpoints)
    if any(path.name != ".ipynb_checkpoints" for path in checkpoints):
        raise ValueError("notebook checkpoint cleanup paths must name .ipynb_checkpoints directories")
    cleanup = tuple(sorted((*scratch, *checkpoints)))
    _validate_distinct_paths((*sources, *cleanup))
    protected = (*_protected(settings), *(root / name for name, _mode, _kind in indexed))
    for path in cleanup:
        if any(_overlap(path, tracked) for tracked in protected):
            raise ValueError(f"notebook cleanup overlaps a tracked or protected project path: {path}")
        if path in scratch and any(_overlap(path, selector) for selector in selectors):
            raise ValueError(f"notebook scratch cleanup overlaps declared source selection: {path}")
        if path in checkpoints and path.exists() and not path.is_dir():
            raise ValueError(f"notebook checkpoint cleanup must be a directory: {path}")
        _validate_cleanup_tree(path, sources)
    return NotebookResetPlan(tuple(sources), cleanup, tree)


def reset(settings: Config, paths: Sequence[Path] = (), *, revision: str | None = None, apply: bool = False) -> NotebookResetPlan:
    """Preview, or explicitly apply, a fully validated restore and deletion map.

    Files/directories select only regular tracked .ipynb blobs, including deleted
    worktree files. The index is preserved. Cleanup is limited to declared paths;
    restoration must succeed before cleanup starts. Partial failures report the
    completed and remaining cleanup paths; neither phase promises rollback.
    """
    if type(apply) is not bool:
        raise ValueError("notebook reset apply must be a boolean")
    if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes)) or any(not isinstance(path, Path) for path in paths):
        raise TypeError("notebook reset paths must be a sequence of Path objects")
    env = _git_environment()
    plan = _plan(settings, paths, revision, env)
    for path in plan.sources:
        print(f"Restore from {plan.revision or 'index'}: {str(path)!r}")
    for path in plan.cleanup:
        print(f"Delete declared scratch/checkpoint: {str(path)!r}")
    if not apply:
        print("Preview only; pass --apply to restore notebooks and delete the declared paths.")
        return plan
    args = ["restore", "--worktree", "--ignore-skip-worktree-bits"]
    if plan.revision is not None:
        args.append(f"--source={plan.revision}")
    args.extend(["--pathspec-from-file=-", "--pathspec-file-nul"])
    payload = b"".join(os.fsencode(path.relative_to(settings.root).as_posix()) + b"\0" for path in plan.sources)
    try:
        _git(settings.root, env, args, input=payload)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            f"notebook restore failed; worktree may be partially restored; cleanup was not started: {format_exception_diagnostics(error)}"
        ) from error
    completed = []
    for index, path in enumerate(plan.cleanup):
        try:
            # Recheck links/containment immediately before each deletion.
            _safe_path(settings.root, path)
            _validate_cleanup_tree(path, plan.sources)
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except (OSError, ValueError) as error:
            raise RuntimeError(
                f"notebooks restored; cleanup failed at {str(path)!r} (possibly partially deleted); "
                f"completed: {[str(item) for item in completed]!r}; remaining: {[str(item) for item in plan.cleanup[index:]]!r}; "
                f"{format_exception_diagnostics(error)}"
            ) from error
        completed.append(path)
    print(f"Restored {len(plan.sources)} notebook(s); cleaned {len(completed)} declared path(s).")
    return plan
