"""Inert installed-distribution observation; never start a consumer interpreter."""

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from importlib.metadata import Distribution, distributions
from pathlib import Path, PurePosixPath, PureWindowsPath

MAX_INSTALLED_BYTES = 32 * 1024 * 1024
# Full native environments include engines exceeding 256 MiB on Linux.
# Callers must opt in; selected-tool observations retain their smaller default.
MAX_NATIVE_BYTES = 512 * 1024 * 1024
MAX_INSTALLED_FILES = 10000


def _normalized(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


@dataclass(frozen=True, slots=True, init=False)
class DistributionSnapshot:
    """Parsed metadata for one observation pass, not durable filesystem proof.

    Identity and safe RECORD paths are captured together and cannot be changed
    independently. File bytes remain live; a later pass loads new snapshots.
    """

    name: str
    version: str
    location: Path
    records: tuple[PurePosixPath, ...]
    direct_url: str | None
    wheel: str | None

    def __init__(self, dist: Distribution) -> None:
        metadata = dist.metadata
        name, version = metadata.get("Name"), metadata.get("Version")
        if not name or not version or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", name) is None:
            raise ValueError("installed distribution requires a package name and version")
        files = dist.files
        if files is None:
            raise ValueError(f"{name}: installed RECORD is missing")
        records = tuple(PurePosixPath(str(path).replace(os.sep, "/")) for path in files)
        if any(path.is_absolute() or PureWindowsPath(str(path)).drive or "\\" in str(path) for path in records):
            raise ValueError(f"{name}: installed RECORD contains an unsafe path")
        object.__setattr__(self, "name", _normalized(name))
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "location", Path(str(dist.locate_file(""))))
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "direct_url", dist.read_text("direct_url.json"))
        object.__setattr__(self, "wheel", dist.read_text("WHEEL"))


def distribution_inventory(paths: tuple[Path, ...]) -> dict[str, DistributionSnapshot]:
    """Read each identity once per observation pass, rejecting all duplicates."""
    inventory = {}
    for dist in distributions(path=[str(path) for path in paths]):
        snapshot = DistributionSnapshot(dist)
        if snapshot.name in inventory:
            raise ValueError("dependency environment contains duplicate installed identities")
        inventory[snapshot.name] = snapshot
    return inventory


def _hashes(dist: DistributionSnapshot, *, limit: int = MAX_INSTALLED_BYTES, shared_files: frozenset[str] = frozenset()) -> dict[str, str]:
    """Hash installed distribution files under a total byte limit, excluding bytecode and generated wrappers outside site-packages."""
    if type(limit) is not int or not 0 < limit <= MAX_NATIVE_BYTES:
        raise ValueError("installed package byte limit must be positive and at most 512 MiB")
    base = dist.location
    names = set()
    roots = set()
    for path in dist.records:
        # Entry-point wrappers can live outside site-packages. They are generated
        # by the installer and are deliberately not publication evidence.
        if ".." in path.parts or path.suffix == ".pyc":
            continue
        names.add(str(path))
        if len(path.parts) > 1 and not path.parts[0].endswith(".dist-info"):
            roots.add(path.parts[0])

    def raise_walk_error(error: OSError) -> None:
        """A partially readable package tree cannot establish the installed inventory."""
        raise error

    for root in sorted(roots):
        directory = base / root
        if directory.is_symlink() or directory.is_junction():
            raise ValueError("published dependency proof does not support symlinked package directories")
        for parent, directories, filenames in directory.walk(on_error=raise_walk_error):
            for leaf in directories:
                if (parent / leaf).is_junction():
                    raise ValueError("published dependency proof does not support symlinked package directories")
            for leaf in filenames:
                path = parent / leaf
                if path.is_junction() or path.is_symlink() and path.is_dir():
                    raise ValueError("published dependency proof does not support symlinked package directories")
                if path.suffix == ".pyc":
                    continue
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
        digest = hashlib.sha256()
        target = base / name
        try:
            mode = target.stat().st_mode
        except FileNotFoundError as error:
            if target.is_symlink():
                raise ValueError(f"installed package requires a regular file: {name}") from error
            raise
        if not stat.S_ISREG(mode):
            raise ValueError(f"installed package requires a regular file: {name}")
        with target.open("rb") as stream:
            # read(n) can reserve n bytes even for a tiny file. Keep the buffer
            # independent of the package allowance, which can be 512 MiB.
            while data := stream.read(min(1024 * 1024, remaining + 1)):
                remaining -= len(data)
                if remaining < 0:
                    raise ValueError("installed package exceeds byte limits")
                digest.update(data)
        result[name] = digest.hexdigest()
    return result


def inspect_installation(
    name: str, *, paths: tuple[Path, ...] | None = None, limit: int = MAX_INSTALLED_BYTES, shared_files: frozenset[str] = frozenset()
) -> dict[str, object]:
    """Observe metadata and installed bytes without importing the selected package."""
    candidates = distributions() if paths is None else distributions(path=[str(path) for path in paths])
    matches = [dist for dist in candidates if _normalized(dist.metadata.get("Name", "")) == _normalized(name)]
    if len(matches) != 1:
        raise ValueError(f"{name}: expected exactly one installed distribution, found {len(matches)}")
    observed = inspect_distribution(DistributionSnapshot(matches[0]), limit=limit, shared_files=shared_files)
    # Preserve the caller's spelling at the existing observation boundary.
    observed["name"] = name
    return observed


def inspect_distribution(dist: DistributionSnapshot, *, limit: int = MAX_INSTALLED_BYTES, shared_files: frozenset[str] = frozenset()) -> dict[str, object]:
    """Read live bytes using the parsed metadata owned by this observation pass."""
    # Direct sources are identified by PEP 610, never by their version label.
    try:
        hashes = {} if dist.direct_url is not None else _hashes(dist, limit=limit, shared_files=shared_files)
    except ValueError as error:
        raise ValueError(f"{dist.name}: {error}") from error
    return {
        "name": dist.name,
        "version": dist.version,
        "direct_url": dist.direct_url,
        "wheel": dist.wheel,
        "files": hashes,
        "location": str(dist.location),
    }
