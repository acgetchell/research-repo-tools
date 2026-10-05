"""Small notebook integration fixtures using the caller's locked interpreter.

These helpers execute trusted notebook code, not sandboxed code. They never
synchronize environments, install tools, or register kernels. Only the yielded
temporary workspace is owned and removed by the context manager.
"""

import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from research_repo_tools import config, notebooks
from research_repo_tools.notebooks import NotebookExecution as NotebookExecution

__all__ = ["NotebookExecution", "NotebookProject", "isolated_project"]

_PROJECT_FILES = (Path(".python-version"), Path("pyproject.toml"), Path("uv.lock"))


def _inside(root: Path, path: Path) -> Path:
    candidate = root / path
    if ".." in path.parts or not candidate.is_relative_to(root):
        raise ValueError(f"fixture path must be inside {root}: {path}")
    for item in (candidate, *candidate.parents):
        if item == root:
            break
        if item.is_symlink() or item.is_junction():
            raise ValueError(f"fixture paths must not contain links: {path}")
    if not candidate.resolve().is_relative_to(root):
        raise ValueError(f"fixture path escapes {root}: {path}")
    return candidate


@dataclass(frozen=True, slots=True)
class NotebookProject:
    """A context-owned project; use root for consumer-specific inputs/assertions."""

    root: Path
    artifacts: Path
    environment: Path
    _settings: config.Config
    _active: bool = True

    def execute(
        self,
        notebook: Path,
        *,
        cwd: Path | None = None,
        timeout: int | None = None,
        env: Mapping[str, str | None] | None = None,
    ) -> NotebookExecution:
        """Execute a fixture notebook and return its artifact paths and report.

        Paths are relative to root unless absolute inside it. env overlays the
        kernel environment; None values remove variables, without touching the
        parent process. Cell errors/timeouts return returncode=1 with a report;
        invalid setup or publication errors raise. Artifacts last until context
        exit. The caller must run in the explicitly selected environment.
        """
        if not self._active:
            raise ValueError("notebook fixture context has ended")
        if self.root.is_symlink() or self.root.is_junction() or self.artifacts.is_symlink() or self.artifacts.is_junction():
            raise ValueError("notebook fixture directories must not be replaced with links")
        source = _inside(self.root, notebook)
        working = _inside(self.root, Path(self._settings.notebooks.cwd) if cwd is None else cwd)
        if env is not None and (
            not isinstance(env, Mapping)
            or any(
                not isinstance(key, str) or not key or "=" in key or "\0" in key or (value is not None and (not isinstance(value, str) or "\0" in value))
                for key, value in env.items()
            )
        ):
            raise ValueError("kernel env must map nonempty variable names to strings or None, without NUL or '=' in names")
        (result,) = notebooks._execute_reports(
            self._settings,
            [source],
            cwd=str(working),
            output_dir=str(self.artifacts),
            timeout=timeout,
            environment=self.environment,
            kernel_env=env,
            runtime_dir=self.artifacts,
        )
        return result


@contextmanager
def isolated_project(
    source_root: Path,
    paths: Sequence[Path],
    *,
    parent: Path,
    environment: Path,
) -> Iterator[NotebookProject]:
    """Copy explicit files into a new temporary project under an existing parent.

    Copies pyproject.toml, uv.lock and .python-version byte-for-byte, plus paths
    relative to source_root (absolute paths inside it also work). No recursive
    discovery or links are allowed. Rust declarations are omitted and managed
    Cargo/binary settings are disabled in memory, leaving the copied TOML intact.

    environment must identify the already synchronized environment running this
    process (normally Path(sys.prefix)). The caller owns lock synchronization;
    execution checks its Python declaration and records the copied lock digest.
    No resolution, installation or environment recreation occurs here.
    """
    source_root, parent, environment = source_root.resolve(), parent.resolve(), environment.resolve()
    if not source_root.is_dir() or not parent.is_dir():
        raise ValueError("source_root and parent must be existing directories")
    if parent.is_relative_to(source_root) or parent.is_relative_to(environment):
        raise ValueError("fixture parent must be outside the source project and borrowed environment")
    if environment != Path(sys.prefix).resolve():
        raise ValueError("run notebook tests with the explicitly selected locked environment's interpreter")
    if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes)) or any(not isinstance(path, Path) for path in paths):
        raise TypeError("paths must be an explicit sequence of Path objects")
    payloads = {}
    for path in (*_PROJECT_FILES, *paths):
        source = _inside(source_root, path)
        relative = source.relative_to(source_root)
        if relative == Path("rust-toolchain.toml") or any(part.casefold() in {".git", ".venv"} for part in relative.parts):
            raise ValueError(f"notebook fixtures exclude toolchain/environment state: {path}")
        if not source.is_file():
            raise ValueError(f"fixture input must be a regular file: {path}")
        payloads[relative] = source.read_bytes()
    with tempfile.TemporaryDirectory(prefix="notebook-project-", dir=parent) as temporary:
        workspace = Path(temporary)
        root, artifacts = workspace / "project", workspace / "artifacts"
        root.mkdir()
        artifacts.mkdir()
        for relative, content in payloads.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        settings = config.load(root=root)
        settings = replace(settings, toolchain=replace(settings.toolchain, cargo={}, binaries={}))
        project = NotebookProject(root, artifacts, environment, settings)
        try:
            yield project
        finally:
            object.__setattr__(project, "_active", False)
