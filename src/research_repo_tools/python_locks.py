"""Reject decreases in retained uv lock resolutions before synchronizing them."""

import tomllib
from pathlib import Path

from packaging.markers import Marker
from packaging.utils import canonicalize_name
from packaging.version import Version


def versions(payload: bytes | None) -> dict[str, dict[tuple[str, ...], set[Version]]]:
    """Read versions by name and universal resolution branch, without host filtering."""
    if payload is None:
        return {}
    data = tomllib.loads(payload.decode("utf-8"))
    packages = data.get("package")
    if data.get("version") != 1 or not isinstance(packages, list):
        raise ValueError("downgrade protection requires a valid version 1 uv.lock with package entries")
    result: dict[str, dict[tuple[str, ...], set[Version]]] = {}
    for package in packages:
        if not isinstance(package, dict) or not isinstance(package.get("name"), str) or not isinstance(package.get("version"), str):
            raise ValueError("uv.lock package must declare a name and version")
        source = package.get("source")
        if not isinstance(source, dict):
            raise ValueError("uv.lock package must declare its source")
        if set(source) & {"virtual", "editable"}:
            continue  # The local project's own release version is not an update target.
        markers = package.get("resolution-markers", [])
        if not isinstance(markers, list) or any(not isinstance(marker, str) for marker in markers):
            raise ValueError("uv.lock resolution-markers must be strings")
        branch = tuple(sorted(str(Marker(marker)) for marker in markers))
        result.setdefault(canonicalize_name(package["name"]), {}).setdefault(branch, set()).add(Version(package["version"]))
    return result


def require_no_downgrades(original: bytes | None, lock: Path) -> None:
    """Compare all retained packages, including inactive platform forks and transitives.

    Changed marker partitions are accepted only when every new version is at
    least every old version. Otherwise equivalence is ambiguous and must be
    reviewed explicitly, rather than silently bypassing the guard.
    """
    before = versions(original)
    after = versions(lock.read_bytes())
    for name in sorted(before.keys() & after.keys()):
        old, new = before[name], after[name]
        if old == new:
            continue
        pairs = [(old[branch], new[branch]) for branch in old] if old.keys() == new.keys() else [(set.union(*old.values()), set.union(*new.values()))]
        for old_versions, new_versions in pairs:
            if min(new_versions) < max(old_versions):
                raise ValueError(
                    f"refusing dependency downgrade or ambiguous branch change in {lock}: {name} "
                    f"{', '.join(map(str, sorted(old_versions)))} -> {', '.join(map(str, sorted(new_versions)))}; "
                    "review intentional downgrades or marker changes separately with uv"
                )
