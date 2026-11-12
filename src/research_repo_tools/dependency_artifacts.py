"""Compare native dependency inputs with the bytes of verified registry artifacts."""

import hashlib
import io
import os
import tarfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from research_repo_tools._archive_paths import ArchivePaths
from research_repo_tools._dependency_observation import retain_guard
from research_repo_tools.dependency_policy import MAX_SNAPSHOT_FILE_BYTES, MAX_SNAPSHOT_FILES, InputSnapshot
from research_repo_tools.dependency_progress import report
from research_repo_tools.dependency_sources import HttpSourceProvider, SourceProvider, VerifiedSource, table
from research_repo_tools.evidence import deterministic_json, sha256

if TYPE_CHECKING:
    from research_repo_tools.dependency_graphs import DependencyGraph

CARGO_MAX_COMPRESSED_BYTES = 32 * 1024 * 1024
CARGO_MAX_EXPANDED_BYTES = 64 * 1024 * 1024


def _legacy_uv_hooks(site: Path) -> InputSnapshot:
    """Recognize the exact uv 0.8.0 pair, never names or executable imports.

    https://github.com/astral-sh/uv/blob/0.8.0/crates/uv-virtualenv/src/_virtualenv.py
    uv writes both files byte-for-byte on every platform. Unknown versions must
    be recreated with the supported uv instead of receiving a blanket exemption.
    """
    snapshot = InputSnapshot.capture((site / "_virtualenv.py", site / "_virtualenv.pth"))
    module, hook = (content for _, content in snapshot.files)
    if module is None and hook is None:
        return snapshot
    if (
        module is None
        or hashlib.sha256(module).hexdigest() != "6cf30c56faf2a55228914dbbd17f8088ed371ebb08f5e7fa6fd931f913fcaf1d"
        or hook != b"import _virtualenv"
    ):
        raise ValueError("dependency environment contains unrecognized legacy uv startup hooks; recreate the environment")
    return snapshot


def verify_cargo_input(path: Path, verified: VerifiedSource, *, provider: SourceProvider | None = None) -> InputSnapshot:
    try:
        return _cargo_input(path, verified, provider=provider)
    except tarfile.TarError as error:
        raise ValueError("Cargo dependency archive is malformed") from error


def _cargo_input(path: Path, verified: VerifiedSource, *, provider: SourceProvider | None) -> InputSnapshot:
    """Verify used extracted crate files against a downloaded locked .crate."""
    (artifact,) = verified.artifacts
    prefix = f"{verified.source.package}-{verified.version}"
    if artifact.size > CARGO_MAX_COMPRESSED_BYTES:
        raise ValueError(f"{prefix}: Cargo archive exceeds the {CARGO_MAX_COMPRESSED_BYTES}-byte compressed limit")
    payload = (provider or HttpSourceProvider()).read(artifact.url)
    if len(payload) != artifact.size or hashlib.sha256(payload).hexdigest() != artifact.sha256:
        raise ValueError("Cargo dependency archive bytes differ from crates.io and the lock")
    expected: set[str] = set()
    observed: list[tuple[Path, bytes]] = []
    paths = ArchivePaths()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        total = 0
        for index, member in enumerate(archive):
            if index >= MAX_SNAPSHOT_FILES:
                raise ValueError(f"{prefix}: Cargo archive exceeds the {MAX_SNAPSHOT_FILES}-member limit")
            if member.size < 0:
                raise ValueError(f"{prefix}: Cargo archive has a negative member size")
            if member.size > MAX_SNAPSHOT_FILE_BYTES:
                raise ValueError(f"{prefix}: Cargo archive exceeds the {MAX_SNAPSHOT_FILE_BYTES}-byte per-member limit")
            total += member.size
            if total > CARGO_MAX_EXPANDED_BYTES:
                raise ValueError(f"{prefix}: Cargo archive exceeds the {CARGO_MAX_EXPANDED_BYTES}-byte expanded limit")
            pure = paths.add(member.name, directory=member.isdir())
            if pure.is_absolute() or ".." in pure.parts or "\\" in member.name or not pure.parts or pure.parts[0] != prefix:
                raise ValueError("Cargo archive has unsafe paths")
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
                content = stream.read()
            if not target.is_file():
                raise ValueError(f"{prefix}: missing Cargo cache input {target}; fetch this locked crate into the selected Cargo cache and retry")
            if target.read_bytes() != content:
                raise ValueError(f"{prefix}: Cargo dependency cache differs from published bytes at {target}; restore this locked crate's cache entry")
            observed.append((target, content))

    # Cargo creates these bookkeeping files after extraction. Every compilable
    # input must be present in the published archive, not merely its checksum map.
    def inventory_unchanged() -> None:
        actual: set[str] = set()
        for index, item in enumerate(path.rglob("*")):
            if item.is_symlink() or item.is_junction() or index >= 20000:
                raise ValueError(f"Cargo dependency cache has links or exceeds file limits at {path}; restore this locked crate's cache entry")
            if item.is_file():
                actual.add(item.relative_to(path).as_posix())
        if actual - expected - {".cargo-ok", ".cargo-checksum.json"}:
            raise ValueError(f"Cargo dependency cache contains unpublished files at {path}; restore this locked crate's cache entry")

    inventory_unchanged()
    environment = InputSnapshot.capture(()).environment
    snapshot = InputSnapshot(tuple(observed), environment)
    snapshot.verify()
    retain_guard(inventory_unchanged, sha256(deterministic_json({"path": str(path), "files": sorted(expected)})))
    return snapshot


def python_environment_layout(graph: DependencyGraph) -> tuple[Path, tuple[Path, ...]]:
    """Fail cheap local environment prerequisites before registry traversal."""
    environment = graph.root / os.environ.get("UV_PROJECT_ENVIRONMENT", ".venv")
    if environment.is_symlink() or environment.is_junction() or not environment.resolve().is_relative_to(graph.root):
        raise ValueError("dependency environment must be contained in the observed project")
    if not environment.exists():
        return environment, ()
    configuration_path = environment / "pyvenv.cfg"
    if not configuration_path.is_file():
        raise ValueError(f"dependency environment lacks {configuration_path}; recreate this environment from its declared lock")
    configuration = configuration_path.read_text(encoding="utf-8")
    if any(line.strip().lower().replace(" ", "") == "include-system-site-packages=true" for line in configuration.splitlines()):
        raise ValueError("system-site-packages cannot establish dependency provenance")
    candidates: list[Path] = [environment / "Lib/site-packages"] if os.name == "nt" else list((environment / "lib").glob("python*/site-packages"))
    sites = tuple(site for site in candidates if site.is_dir())
    if len(sites) != 1:
        raise ValueError(
            f"dependency environment requires one native site-packages directory at {environment}; recreate this environment from its declared lock"
        )
    return environment, sites


def verify_python_environment(graph: DependencyGraph, *, build_directory: Path | None = None) -> None:
    """Inspect existing site-packages statically; never import consumer packages.

    Universal locks prove all target/extra variants. For distributions actually
    present in the normal environment, also require published wheel byte identity.
    An absent environment belongs to acquisition, whose native installer must
    consume the same checked lock before downstream validation.
    """
    from research_repo_tools._dependency_probe import MAX_NATIVE_BYTES, distribution_inventory, inspect_distribution
    from research_repo_tools.dependency_build_receipts import MAX_WHEEL_BYTES, verify_build_receipt
    from research_repo_tools.dependency_builds import locked_sdist
    from research_repo_tools.dependency_policy import load_policy
    from research_repo_tools.dependency_provenance import _check_installation, _published_wheel, verify_wheel_payload

    environment, sites = python_environment_layout(graph)
    environment_snapshot = InputSnapshot.capture((environment / "pyvenv.cfg",))

    def layout_unchanged() -> None:
        environment_snapshot.verify()
        if python_environment_layout(graph) != (environment, sites):
            raise ValueError("dependency environment layout changed during verification")

    retain_guard(layout_unchanged, environment_snapshot.digest)
    if not sites:
        return
    from packaging.utils import canonicalize_name

    inventory = distribution_inventory(tuple(sites))
    installed = set(inventory)
    owned = {name for name, _, _ in graph.owned}
    unexpected = set(installed) - owned - {dependency.source.package for dependency in graph.dependencies}
    if unexpected:
        raise ValueError(f"dependency environment {sites[0]} contains unlocked distributions: {', '.join(sorted(unexpected))}; sync the declared lock")
    roots = set()
    shared_files = set()
    for dist in inventory.values():
        for record in dist.records:
            if record.parts and ".." not in record.parts:
                roots.add(record.parts[0])
                shared_files.add(record.as_posix())
    legacy = _legacy_uv_hooks(sites[0])
    recognized = {path.name for path, content in legacy.files if content is not None}
    unexpected_files = {path.name for path in sites[0].iterdir()} - roots - {"__pycache__"} - recognized
    if unexpected_files:
        raise ValueError(
            f"dependency environment contains unpublished files or startup hooks at {sites[0]}: {', '.join(sorted(unexpected_files))}; repair this environment"
        )
    import tomllib

    locked = tomllib.loads((graph.root / "uv.lock").read_text(encoding="utf-8"))["package"]
    policy = load_policy(graph.root)
    builds = policy.source_builds if policy is not None else None
    # The locked Linux Semgrep wheel expands to 286,642,049 bytes (>256 MiB).
    # Keep a finite package bound without changing selected-tool verification defaults.
    native_limit = MAX_NATIVE_BYTES
    observations = []
    source_wheels = []
    scripts = environment / ("Scripts" if os.name == "nt" else "bin")
    scope = f"python:{graph.project.directory}"
    report("started", "installed bytes", graph=scope, total=len(installed - owned))
    for index, name in enumerate(sorted(set(installed) - owned)):
        report("started", "installed bytes", graph=scope, checked=index, total=len(installed - owned), detail=name)
        # Every RECORD is checked below. Shared namespace files belong to their
        # own distribution; unrecorded additions still fail their namespace scan.
        observed = inspect_distribution(inventory[name], limit=native_limit, shared_files=frozenset(shared_files))
        matches = [item for item in locked if canonicalize_name(item["name"]) == name and item["version"] == observed.get("version")]
        if len(matches) != 1 or observed.get("direct_url") is not None:
            raise ValueError("installed dependency differs from the registry lock or has development provenance")
        _check_installation(observed, name, matches[0]["version"])
        if builds is not None and name in builds.packages:
            version, source = locked_sdist(graph, name)
            receipt = verify_build_receipt(graph.root, name, version, source, directory=build_directory)
            verify_wheel_payload(name, receipt.payload, observed, scripts=scripts, limit=MAX_WHEEL_BYTES)
            source_wheels.append((name, version, source, observed))
        else:
            _published_wheel(
                name, matches[0]["version"], table(matches[0], "locked package"), (observed,), require_sdist=False, limit=native_limit, scripts=scripts
            )
        observations.append((name, observed))
        report("verified", "installed bytes", graph=scope, checked=index + 1, total=len(installed - owned), detail=name)
    # Discover identities again; a retained inventory must not hide additions,
    # removals or duplicate distributions introduced during verification.

    def unchanged() -> None:
        current = distribution_inventory(tuple(sites))
        if current.keys() != inventory.keys():
            raise ValueError("installed dependency inventory changed during verification")
        if {path.name for path in sites[0].iterdir()} - roots - {"__pycache__"} - recognized:
            raise ValueError(f"dependency environment contains unpublished files or startup hooks at {sites[0]}")
        for name, observed in observations:
            if inspect_distribution(current[name], limit=native_limit, shared_files=frozenset(shared_files)) != observed:
                raise ValueError(f"installed dependency changed during verification: {name}")
        legacy.verify()
        for name, version, source, observed in source_wheels:
            receipt = verify_build_receipt(graph.root, name, version, source, directory=build_directory)
            verify_wheel_payload(name, receipt.payload, observed, scripts=scripts, limit=MAX_WHEEL_BYTES)

    unchanged()
    retain_guard(unchanged, sha256(deterministic_json({"observations": observations, "inventory": sorted(inventory), "roots": sorted(roots)})))
    report("completed", "installed bytes", graph=scope, checked=len(observations), total=len(installed - owned))
