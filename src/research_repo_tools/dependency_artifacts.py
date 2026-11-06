"""Compare native dependency inputs with the bytes of verified registry artifacts."""

import hashlib
import io
import os
import tarfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from research_repo_tools._archive_paths import ArchivePaths
from research_repo_tools.dependency_policy import InputSnapshot
from research_repo_tools.dependency_sources import HttpSourceProvider, SourceProvider, VerifiedSource, table
from research_repo_tools.registry import MAX_BYTES

if TYPE_CHECKING:
    from research_repo_tools.dependency_graphs import DependencyGraph


def verify_cargo_input(path: Path, verified: VerifiedSource, *, provider: SourceProvider | None = None) -> InputSnapshot:
    try:
        return _cargo_input(path, verified, provider=provider)
    except tarfile.TarError as error:
        raise ValueError("Cargo dependency archive is malformed") from error


def _cargo_input(path: Path, verified: VerifiedSource, *, provider: SourceProvider | None) -> InputSnapshot:
    """Verify used extracted crate files against a downloaded locked .crate."""
    (artifact,) = verified.artifacts
    if artifact.size > MAX_BYTES:
        raise ValueError("Cargo dependency archive exceeds the byte limit")
    payload = (provider or HttpSourceProvider()).read(artifact.url)
    if len(payload) != artifact.size or hashlib.sha256(payload).hexdigest() != artifact.sha256:
        raise ValueError("Cargo dependency archive bytes differ from crates.io and the lock")
    prefix = f"{verified.source.package}-{verified.version}"
    expected: set[str] = set()
    observed: list[tuple[Path, bytes]] = []
    paths = ArchivePaths()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        total = 0
        for index, member in enumerate(archive):
            if member.size < 0:
                raise ValueError("Cargo archive has a negative member size")
            total += member.size
            pure = paths.add(member.name, directory=member.isdir())
            if (
                index >= 10000
                or total > MAX_BYTES
                or pure.is_absolute()
                or ".." in pure.parts
                or "\\" in member.name
                or not pure.parts
                or pure.parts[0] != prefix
            ):
                raise ValueError("Cargo archive has unsafe paths or exceeds content limits")
            if member.isdir():
                continue
            relative = PurePosixPath(*pure.parts[1:]).as_posix()
            if not member.isfile() or relative in expected:
                raise ValueError("Cargo archive has links or duplicate members")
            expected.add(relative)
            target = path / relative
            if not target.resolve().is_relative_to(path.resolve()) or target.is_symlink():
                raise ValueError("Cargo dependency cache contains unsafe links")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("Cargo archive member cannot be read")
            with stream:
                content = stream.read(MAX_BYTES + 1)
            if target.read_bytes() != content:
                raise ValueError(f"Cargo dependency cache differs from published bytes: {relative}")
            observed.append((target, content))
    # Cargo creates these bookkeeping files after extraction. Every compilable
    # input must be present in the published archive, not merely its checksum map.
    actual: set[str] = set()
    for index, item in enumerate(path.rglob("*")):
        if item.is_symlink() or index >= 20000:
            raise ValueError("Cargo dependency cache has links or exceeds file limits")
        if item.is_file():
            actual.add(item.relative_to(path).as_posix())
    if actual - expected - {".cargo-ok", ".cargo-checksum.json"}:
        raise ValueError("Cargo dependency cache contains unpublished files")
    environment = InputSnapshot.capture(()).environment
    snapshot = InputSnapshot(tuple(observed), environment)
    snapshot.verify()
    return snapshot


def verify_python_environment(graph: DependencyGraph) -> None:
    """Inspect existing site-packages statically; never import consumer packages.

    Universal locks prove all target/extra variants. For distributions actually
    present in the normal environment, also require published wheel byte identity.
    An absent environment belongs to acquisition, whose native installer must
    consume the same checked lock before downstream validation.
    """
    from importlib.metadata import distributions

    from research_repo_tools._dependency_probe import inspect_installation
    from research_repo_tools.dependency_provenance import _check_installation, _published_wheel

    environment = graph.root / os.environ.get("UV_PROJECT_ENVIRONMENT", ".venv")
    if not environment.exists():
        return
    if environment.is_symlink() or not environment.resolve().is_relative_to(graph.root):
        raise ValueError("dependency environment must be contained in the observed project")
    configuration = (environment / "pyvenv.cfg").read_text(encoding="utf-8")
    if any(line.strip().lower().replace(" ", "") == "include-system-site-packages=true" for line in configuration.splitlines()):
        raise ValueError("system-site-packages cannot establish dependency provenance")
    candidates = [environment / "Lib/site-packages"] if os.name == "nt" else list((environment / "lib").glob("python*/site-packages"))
    sites = [site for site in candidates if site.is_dir()]
    if len(sites) != 1:
        raise ValueError("dependency environment requires one native site-packages directory")
    from packaging.utils import canonicalize_name

    inventory = tuple(distributions(path=[str(sites[0])]))
    installed = [canonicalize_name(dist.metadata["Name"]) for dist in inventory]
    if len(set(installed)) != len(installed):
        raise ValueError("dependency environment contains duplicate installed identities")
    owned = {name for name, _, _ in graph.owned}
    if set(installed) - owned - {dependency.source.package for dependency in graph.dependencies}:
        raise ValueError("dependency environment contains unlocked distributions")
    roots = set()
    for dist in inventory:
        records = dist.files
        if records is None:
            raise ValueError("installed distribution requires a RECORD inventory")
        for record in records:
            if record.parts and ".." not in record.parts:
                roots.add(record.parts[0])
    if {path.name for path in sites[0].iterdir()} - roots - {"__pycache__"}:
        raise ValueError("dependency environment contains unpublished files or startup hooks")
    import tomllib

    locked = tomllib.loads((graph.root / "uv.lock").read_text(encoding="utf-8"))["package"]
    observations = []
    for name in sorted(set(installed) - owned):
        observed = inspect_installation(name, paths=tuple(sites))
        matches = [item for item in locked if canonicalize_name(item["name"]) == name and item["version"] == observed.get("version")]
        if len(matches) != 1 or observed.get("direct_url") is not None:
            raise ValueError("installed dependency differs from the registry lock or has development provenance")
        _check_installation(observed, name, matches[0]["version"])
        _published_wheel(name, matches[0]["version"], table(matches[0], "locked package"), (observed,), require_sdist=False)
        observations.append((name, observed))
    for name, observed in observations:
        if inspect_installation(name, paths=tuple(sites)) != observed:
            raise ValueError("installed dependency changed during verification")
