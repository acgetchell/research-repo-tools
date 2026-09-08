"""Reconcile explicitly mapped Just pins with installed Cargo tools and uv."""

import re
from collections.abc import Mapping
from pathlib import Path

from research_repo_tools.files import replace
from research_repo_tools.process import run_safe_command

STABLE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
SEMVER = re.compile(rf"{STABLE.pattern}(?:-{_IDENTIFIER}(?:\.{_IDENTIFIER})*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?")
TOOL_VERSION = re.compile(rf"(?<![0-9A-Za-z_.+-])v?(?P<version>{SEMVER.pattern})(?![0-9A-Za-z_.+-])")
PACKAGE_HEADER = re.compile(r"(?P<package>[A-Za-z0-9_-]+) v(?P<version>[^\s:]+)(?: \([^\r\n]+\))?:")


def _version(version: str, tool: str, *, stable: bool = False) -> str:
    pattern = STABLE if stable else SEMVER
    if pattern.fullmatch(version) is None:
        expected = "stable X.Y.Z" if stable else "canonical SemVer"
        raise ValueError(f"invalid installed version for {tool}: {version}; expected {expected}")
    return version


def parse_installed_packages(output: str) -> dict[str, str]:
    packages: dict[str, str] = {}
    for line in output.splitlines():
        if not line or line[0].isspace():
            continue
        match = PACKAGE_HEADER.fullmatch(line)
        if match is None:
            raise ValueError(f"malformed Cargo package header: {line}")
        package, version = match["package"], match["version"]
        if package in packages:
            raise ValueError(f"duplicate installed Cargo package: {package}")
        packages[package] = _version(version, package)
    return packages


def parse_tool_version(output: str, tool: str) -> str:
    versions = [match["version"] for match in TOOL_VERSION.finditer(output)]
    if len(versions) != 1:
        raise ValueError(f"expected exactly one {tool} version, found {len(versions)}")
    return _version(versions[0], tool, stable=True)


def check_uv(*, executable: str = "uv", output: str | None = None) -> str:
    """Validate supplied output or the selected executable without reading Cargo."""
    if output is None:
        output = run_safe_command(executable, ["--version"], timeout=30).stdout
    return parse_tool_version(output, "uv")


def update_text(text: str, installed: Mapping[str, str], mapping: Mapping[str, str]) -> tuple[str, dict[str, tuple[str, str]]]:
    changes: dict[str, tuple[str, str]] = {}
    for pin, tool in mapping.items():
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", pin) is None or re.fullmatch(r"[A-Za-z0-9_-]+", tool) is None:
            raise ValueError(f"invalid Just variable or package mapping: {pin} -> {tool}")
        version = installed.get(tool)
        if version is None:
            raise ValueError(f"managed tool is not installed: {tool}")
        _version(version, tool, stable=tool == "uv")
        assignment = re.compile(rf'^(?P<prefix>{re.escape(pin)}[ \t]*:=[ \t]*")(?P<version>[^"\r\n]+)(?P<suffix>"[ \t]*(?:#[^\r\n]*)?\r?$)', re.MULTILINE)
        matches = list(assignment.finditer(text))
        if len(matches) != 1:
            raise ValueError(f"expected exactly one {pin} assignment, found {len(matches)}")
        old = matches[0].group("version")
        if old != version:
            text = assignment.sub(lambda match: match.group("prefix") + version + match.group("suffix"), text, count=1)
            changes[pin] = (old, version)
    return text, changes


def reconcile(path: Path, installed: Mapping[str, str], mapping: Mapping[str, str], *, dry_run: bool = False) -> dict[str, tuple[str, str]]:
    """Validate every managed pin before atomically replacing the original bytes."""
    payload, changes = update_text(path.read_bytes().decode("utf-8"), installed, mapping)
    if changes and not dry_run:
        replace(path, payload.encode("utf-8"))
    return changes


def update(path: Path, mapping: Mapping[str, str], *, uv: str = "uv", dry_run: bool = False) -> dict[str, tuple[str, str]]:
    if not mapping:
        raise ValueError("deps.tools must explicitly map Just variable names to Cargo package names or uv")
    installed: dict[str, str] = {}
    if "uv" in mapping.values():
        installed["uv"] = check_uv(executable=uv)
    if any(tool != "uv" for tool in mapping.values()):
        packages = parse_installed_packages(run_safe_command("cargo", ["install", "--list"], timeout=30).stdout)
        installed.update({package: version for package, version in packages.items() if package != "uv"})
    return reconcile(path, installed, mapping, dry_run=dry_run)
