"""Inert installed-distribution observation; never start a consumer interpreter."""

import hashlib
from importlib.metadata import Distribution, PackagePath, distributions
from pathlib import Path, PureWindowsPath

MAX_INSTALLED_BYTES = 32 * 1024 * 1024
MAX_INSTALLED_FILES = 10000


def _hashes(dist: Distribution, files: list[PackagePath], *, limit: int = MAX_INSTALLED_BYTES, shared_files: frozenset[str] = frozenset()) -> dict[str, str]:
    if type(limit) is not int or not 0 < limit <= 256 * 1024 * 1024:
        raise ValueError("installed package byte limit must be positive and at most 256 MiB")
    base = Path(str(dist.locate_file("")))
    names = set()
    roots = set()
    for path in files:
        if path.is_absolute() or PureWindowsPath(str(path)).drive or "\\" in str(path):
            raise ValueError("installed RECORD contains an unsafe path")
        # Entry-point wrappers can live outside site-packages. They are generated
        # by the installer and are deliberately not publication evidence.
        if ".." in path.parts or path.suffix == ".pyc":
            continue
        names.add(str(path))
        if len(path.parts) > 1 and not path.parts[0].endswith(".dist-info"):
            roots.add(path.parts[0])
    for root in sorted(roots):
        directory = base / root
        if directory.is_symlink():
            raise ValueError("published dependency proof does not support symlinked package directories")
        for path in directory.rglob("*"):
            if path.is_symlink() and path.is_dir():
                raise ValueError("published dependency proof does not support symlinked package directories")
            if path.is_file() and path.suffix != ".pyc":
                relative = path.relative_to(base).as_posix()
                if relative not in shared_files:
                    names.add(relative)
            if len(names) > MAX_INSTALLED_FILES:
                raise ValueError("installed package exceeds file limits")
    if len(names) > MAX_INSTALLED_FILES:
        raise ValueError("installed package exceeds file limits")
    result = {}
    remaining = limit
    for name in sorted(names):
        with (base / name).open("rb") as stream:
            data = stream.read(remaining + 1)
        remaining -= len(data)
        if remaining < 0:
            raise ValueError("installed package exceeds byte limits")
        result[name] = hashlib.sha256(data).hexdigest()
    return result


def inspect_installation(
    name: str, *, paths: tuple[Path, ...] | None = None, limit: int = MAX_INSTALLED_BYTES, shared_files: frozenset[str] = frozenset()
) -> dict[str, object]:
    """Observe metadata and installed bytes without importing the selected package."""
    import re

    def normalized(value: str) -> str:
        return re.sub(r"[-_.]+", "-", value).lower()

    candidates = distributions() if paths is None else distributions(path=[str(path) for path in paths])
    matches = [dist for dist in candidates if normalized(dist.metadata.get("Name", "")) == normalized(name)]
    if len(matches) != 1:
        raise ValueError(f"{name}: expected exactly one installed distribution, found {len(matches)}")
    dist = matches[0]
    direct = dist.read_text("direct_url.json")
    files = dist.files
    if files is None:
        raise ValueError(f"{name}: installed RECORD is missing")
    # Direct sources are identified by PEP 610, never by their version label.
    hashes = {} if direct is not None else _hashes(dist, files, limit=limit, shared_files=shared_files)
    return {
        "name": name,
        "version": dist.version,
        "direct_url": direct,
        "wheel": dist.read_text("WHEEL"),
        "files": hashes,
        "location": str(dist.locate_file("")),
    }
