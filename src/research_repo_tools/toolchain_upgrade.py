"""Explicit Cargo upgrades: resolve first, verify installations, then publish pins."""

import json
import sys
import tomllib
import urllib.request
from dataclasses import replace
from pathlib import Path

from research_repo_tools import config, files, toolchain, toolchain_config
from research_repo_tools.toml_source import replace_string
from research_repo_tools.tool_pins import SEMVER

INDEX_LIMIT = 16 * 1024 * 1024


def precedence(version: str) -> tuple[int, int, int, bool]:
    """Compare stable candidates with a canonical declared pin, ignoring builds."""
    core = version.split("+", 1)[0]
    release, _, prerelease = core.partition("-")
    major, minor, patch = map(int, release.split("."))
    return major, minor, patch, not prerelease


def latest_stable(package: str) -> str:
    """Read only the declared package's crates.io sparse-index entry."""
    if package not in toolchain_config.CARGO_TOOLS:
        raise ValueError(f"unsupported Cargo tool {package!r}")
    request = urllib.request.Request(
        f"https://index.crates.io/{package[:2]}/{package[2:4]}/{package}",
        headers={"User-Agent": "research-repo-tools (https://github.com/acgetchell/research-repo-tools)"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read(INDEX_LIMIT + 1)
    if len(payload) > INDEX_LIMIT:
        raise ValueError(f"registry entry for {package} exceeds {INDEX_LIMIT} bytes")
    candidates: list[str] = []
    for line in payload.decode("utf-8").splitlines():
        entry = json.loads(line)
        if (
            not isinstance(entry, dict)
            or entry.get("name") != package
            or not isinstance(version := entry.get("vers"), str)
            or SEMVER.fullmatch(version) is None
            or type(entry.get("yanked")) is not bool
        ):
            raise ValueError(f"malformed registry version for {package}")
        if not entry["yanked"] and "-" not in version.split("+", 1)[0]:
            candidates.append(version)
    if not candidates:
        raise ValueError(f"no non-yanked stable release found for {package}")
    return max(candidates, key=lambda version: (precedence(version), version))


def upgrade(settings: config.Config, *, source: Path | None = None, dry_run: bool = False) -> None:
    """Leave authoritative pins unchanged unless every candidate is installed."""
    plan = toolchain_config.load(settings)
    current = toolchain.Runtime(plan)
    uv = current.uv_status()
    if not uv.ok:
        raise ValueError(f"uv {plan.uv} must be installed before Cargo upgrades; found {uv.actual}; no changes made")
    # The selected filename determines the config schema even when it is a
    # symlink to a differently named target. Guarded publication preserves the link.
    source = (source or settings.root / "pyproject.toml").absolute()
    original = source.read_bytes()
    # Ensure the source being rewritten actually supplied these declarations.
    if config.load(source, settings.root).toolchain != settings.toolchain:
        raise ValueError("Cargo declarations differ from the configuration source; reload and retry")
    text = original.decode("utf-8")
    table = "tool.research-repo-tools.toolchain.cargo" if source.name == "pyproject.toml" else "toolchain.cargo"
    selected = []
    for tool in plan.cargo:
        proposed = latest_stable(tool.package)
        if SEMVER.fullmatch(proposed) is None or "-" in proposed.split("+", 1)[0]:
            raise ValueError(f"expected a canonical stable registry version for {tool.package}: {proposed}")
        if precedence(proposed) > precedence(tool.version):
            text = replace_string(text, table, tool.package, proposed)
            selected.append(replace(tool, version=proposed))
        else:
            selected.append(tool)
    changes = [(old, new) for old, new in zip(plan.cargo, selected, strict=True) if old != new]
    for old, new in changes:
        print(f"{old.package}: {old.version} -> {new.version}", flush=True)
    if not changes:
        print("Declared Cargo tools are current; no changes made.")
        return
    document = tomllib.loads(text)
    data = document["tool"]["research-repo-tools"] if source.name == "pyproject.toml" else document
    candidate = config.parse(data, root=settings.root)
    if dict(candidate.toolchain.cargo) != {tool.package: tool.version for tool in selected}:
        raise ValueError("candidate Cargo declarations do not match the selected versions")
    if dry_run:
        print("Dry run: no installations or declaration changes.")
        return
    try:
        toolchain.Runtime(replace(plan, cargo=tuple(selected))).sync()
        files.replace_if_unchanged(source, original, text.encode("utf-8"))
    except (OSError, ValueError, RuntimeError) as error:
        raise RuntimeError(
            f"Cargo upgrade failed: {error}\n"
            "Cargo declarations were not published. Previous versioned tools remain available; "
            "successful candidate installations are retained. Fix the cause and rerun toolchain upgrade."
        ) from error
    print(f"Updated Cargo declarations in {source}", file=sys.stderr)
