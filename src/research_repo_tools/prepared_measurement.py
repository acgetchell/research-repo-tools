"""Executable and storage checks for private, in-process suite preparation."""

import hashlib
import stat
from collections.abc import Mapping
from pathlib import Path

from research_repo_tools.criterion_inventory import _overlay_environment
from research_repo_tools.process import resolve_executable
from research_repo_tools.publication import _path, _publication_name


def private_environment(root: Path, environment: Mapping[str, str], criterion: str) -> dict[str, str]:
    """Keep build and benchmark output paths inside the phase export and separate from each other."""
    inherited = {key.casefold(): value for key, value in environment.items()}
    settings = (
        ("CARGO_TARGET_DIR", inherited.get("cargo_target_dir", "target/build")),
        ("CARGO_BUILD_BUILD_DIR", inherited.get("cargo_build_build_dir", "target/build/intermediates")),
        ("CRITERION_HOME", inherited.get("criterion_home", criterion)),
    )
    target, build, output = (_path(root, name, directory=True) for _, name in settings)
    if output != root / criterion or any(path.is_relative_to(output) or output.is_relative_to(path) for path in (target, build)):
        raise ValueError("retained preparation requires distinct compiler and Criterion output trees inside each phase export")
    return _overlay_environment(environment, tuple((key, str(path)) for (key, _), path in zip(settings, (target, build, output), strict=True)))


def is_export_executable(name: str) -> bool:
    """Check whether a command name has the form of an export-relative executable path."""
    if "/" not in name:
        return False
    try:
        _publication_name(name)
    except ValueError:
        return False
    return True


def executable_digest(path: Path) -> str:
    """Hash the compiled executable from its raw bytes."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def executable_identity(root: Path, name: str, environment: Mapping[str, str]) -> tuple[Path, str]:
    """Require the named compiled executable inside the export and return its path and SHA-256."""
    path = _path(root, name)
    resolved = resolve_executable(path, cwd=root, env=environment)
    if not path.is_file() or resolved != path:
        raise ValueError(f"retained preparation requires the exact compiled executable {name}")
    return path, executable_digest(path)


def retained_size(root: Path, budget: int) -> int | None:
    """Sum file sizes, returning None for links, special files, or a total above the byte budget."""
    if root.is_symlink() or root.is_junction():
        return None
    size = 0
    for directory, subdirectories, files in root.walk(on_error=_walk_error):
        if any((directory / name).is_symlink() or (directory / name).is_junction() for name in subdirectories):
            return None
        for name in files:
            path = directory / name
            if path.is_junction():
                return None
            status = path.lstat()
            if not stat.S_ISREG(status.st_mode):
                return None
            size += status.st_size
            if size > budget:
                return None
    return size


def _walk_error(error: OSError) -> None:
    raise error
