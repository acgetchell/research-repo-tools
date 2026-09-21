"""Advance exact Python development-tool pins with uv's resolver."""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from research_repo_tools.files import preserve_files
from research_repo_tools.process import ExecutableNotFoundError, format_exception_diagnostics, run_safe_command

RESOLVED_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^;\s]+)(?:\s*;\s*.+)?$",
)

# Dependency resolution may involve network access, but neither phase may block
# the release/update workflow indefinitely.
UV_RESOLVE_TIMEOUT_SECONDS = 300
UV_ADD_TIMEOUT_SECONDS = 300


@dataclass(frozen=True, slots=True)
class DevPin:
    """One exact direct development-tool requirement."""

    name: str
    version: str


def canonicalize_name(name: str) -> str:
    """Return the normalized distribution name used for comparisons."""
    return re.sub(r"[-_.]+", "-", name).casefold()


def _required_table(data: dict[str, object], name: str) -> dict[str, object]:
    """Return a required TOML table with a typed failure."""
    table = data.get(name)
    if not isinstance(table, dict):
        msg = f"pyproject.toml must contain a [{name}] table"
        raise TypeError(msg)
    return cast("dict[str, object]", table)


def _python_constraints(project: dict[str, object]) -> SpecifierSet:
    """Parse the same Python version specifiers accepted by toolchain setup."""
    requires_python = project.get("requires-python", ">=3.14")
    if not isinstance(requires_python, str):
        msg = "project.requires-python must be a string"
        raise TypeError(msg)
    constraints = SpecifierSet(requires_python)
    if constraints.is_unsatisfiable():
        raise ValueError(f"project.requires-python has no matching versions: {requires_python}")
    return constraints


def _dev_pins(groups: dict[str, object]) -> list[DevPin]:
    """Return exact simple pins while leaving every other dev requirement alone."""
    _group_requirements(groups, "dev")  # Validate included groups before any resolver call.
    dev = groups.get("dev")
    if not isinstance(dev, list):
        msg = "dependency-groups.dev must be an array"
        raise TypeError(msg)

    pins: list[DevPin] = []
    normalized_names: set[str] = set()
    for raw_requirement in dev:
        if isinstance(raw_requirement, dict):
            continue  # Included groups retain their pins and are resolved as constraints.
        if not isinstance(raw_requirement, str):
            msg = "dependency-groups.dev entries must be strings"
            raise TypeError(msg)
        try:
            requirement = Requirement(raw_requirement)
        except InvalidRequirement as error:
            msg = f"dependency-groups.dev contains an invalid requirement: {raw_requirement!r}"
            raise ValueError(msg) from error
        specifiers = list(requirement.specifier)
        if requirement.extras or requirement.marker is not None or requirement.url is not None or len(specifiers) != 1:
            continue
        specifier = specifiers[0]
        if specifier.operator != "==" or "*" in specifier.version:
            continue
        pin = DevPin(requirement.name, specifier.version)
        normalized = canonicalize_name(pin.name)
        if normalized in normalized_names:
            msg = f"duplicate exact development-tool requirement: {pin.name}"
            raise ValueError(msg)
        normalized_names.add(normalized)
        pins.append(pin)
    return pins


def _group_requirements(groups: dict[str, object], name: str, ancestors: tuple[str, ...] = ()) -> list[str]:
    """Expand PEP 735 includes without granting ownership of their version pins."""
    if name in ancestors:
        raise ValueError(f"dependency group cycle: {' -> '.join((*ancestors, name))}")
    entries = groups.get(name)
    if not isinstance(entries, list):
        raise TypeError(f"dependency-groups.{name} must be an array")
    result: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            Requirement(entry)
            result.append(entry)
        elif isinstance(entry, dict) and set(entry) == {"include-group"} and isinstance(entry["include-group"], str):
            result.extend(_group_requirements(groups, entry["include-group"], (*ancestors, name)))
        else:
            raise TypeError(f"dependency-groups.{name} entries must be requirements or include-group tables")
    return result


def parse_project(text: str) -> tuple[SpecifierSet, list[DevPin]]:
    """Parse Python constraints and exact direct development-tool pins."""
    data = tomllib.loads(text)
    return _python_constraints(_required_table(data, "project")), _dev_pins(_required_table(data, "dependency-groups"))


def parse_resolution(output: str, pins: list[DevPin]) -> list[DevPin]:
    """Extract one resolver-selected version for every direct development tool."""
    requested = {canonicalize_name(pin.name): pin.name for pin in pins}
    resolved: dict[str, set[str]] = {name: set() for name in requested}

    for raw_line in output.splitlines():
        match = RESOLVED_REQUIREMENT.fullmatch(raw_line.strip())
        if match is None:
            continue
        normalized = canonicalize_name(match.group("name"))
        if normalized in resolved:
            version = match.group("version")
            try:
                Version(version)
            except InvalidVersion as error:
                raise ValueError(f"invalid resolved version for {match.group('name')}: {version}") from error
            resolved[normalized].add(version)

    latest: list[DevPin] = []
    for pin in pins:
        versions = resolved[canonicalize_name(pin.name)]
        if not versions:
            msg = f"uv resolver output omitted direct development tool: {pin.name}"
            raise ValueError(msg)
        if len(versions) != 1:
            rendered = ", ".join(sorted(versions))
            msg = f"uv resolver selected multiple versions for {pin.name}: {rendered}"
            raise ValueError(msg)
        latest.append(DevPin(pin.name, next(iter(versions))))
    return latest


def _resolution_requirements(text: str, pins: list[DevPin]) -> list[str]:
    """Return project and retained dev constraints with managed pins unpinned."""
    data = tomllib.loads(text)
    project = _required_table(data, "project")
    dependencies = project.get("dependencies", [])
    if not isinstance(dependencies, list):
        msg = "project.dependencies must be an array"
        raise TypeError(msg)

    requirements: list[str] = []
    for dependency in dependencies:
        if not isinstance(dependency, str):
            msg = "project.dependencies entries must be strings"
            raise TypeError(msg)
        requirements.append(dependency)

    groups = _required_table(data, "dependency-groups")
    dev = groups.get("dev")
    if not isinstance(dev, list):
        msg = "dependency-groups.dev must be an array"
        raise TypeError(msg)
    managed = {canonicalize_name(pin.name): pin for pin in pins}
    for raw_requirement in dev:
        if isinstance(raw_requirement, dict):
            requirements.extend(_group_requirements(groups, raw_requirement["include-group"], ("dev",)))
            continue
        if not isinstance(raw_requirement, str):
            msg = "dependency-groups.dev entries must be strings"
            raise TypeError(msg)
        try:
            requirement = Requirement(raw_requirement)
        except InvalidRequirement as error:
            msg = f"dependency-groups.dev contains an invalid requirement: {raw_requirement!r}"
            raise ValueError(msg) from error
        specifiers = list(requirement.specifier)
        pin = managed.get(canonicalize_name(requirement.name))
        if (
            pin is not None
            and not requirement.extras
            and requirement.marker is None
            and requirement.url is None
            and len(specifiers) == 1
            and specifiers[0].operator == "=="
            and "*" not in specifiers[0].version
            and specifiers[0].version == pin.version
        ):
            requirements.append(pin.name)
        else:
            requirements.append(raw_requirement)
    return requirements


def resolve_latest_pins(pins: list[DevPin], requires_python: SpecifierSet, project_root: Path, *, uv: str = "uv") -> list[DevPin]:
    """Resolve a universal set without changing the consumer's files."""
    manifest = project_root / "pyproject.toml"
    requirements = _resolution_requirements(manifest.read_text(encoding="utf-8"), pins)
    # pip compile only conveys a Python lower bound. Exporting a temporary
    # script lock preserves the full range without changing the consumer lock.
    # uv reads this metadata; the script is never executed.
    with tempfile.TemporaryDirectory(prefix="research-repo-tools-resolve-") as directory:
        source = Path(directory) / "requirements.py"
        source.write_text(
            f"# /// script\n# requires-python = {json.dumps(str(requires_python))}\n# dependencies = {json.dumps(requirements)}\n# ///\n",
            encoding="utf-8",
            newline="\n",
        )
        result = run_safe_command(
            uv,
            [
                "export",
                "--script",
                str(source),
                "--format",
                "requirements.txt",
                "--no-header",
                "--no-annotate",
                "--no-hashes",
                "--directory",
                str(project_root),
                "--project",
                str(project_root),
            ],
            cwd=project_root,
            timeout=UV_RESOLVE_TIMEOUT_SECONDS,
        )
    return parse_resolution(result.stdout, pins)


def _conventional_manifest(pyproject: Path) -> Path:
    """Resolve a conventional project manifest that uv will target exactly."""
    if pyproject.name != "pyproject.toml":
        msg = f"--pyproject must name a conventional pyproject.toml manifest, got {pyproject}"
        raise ValueError(msg)
    if pyproject.is_symlink():
        msg = f"--pyproject must not be a symbolic link: {pyproject}"
        raise ValueError(msg)
    resolved = pyproject.resolve()
    if not resolved.is_file():
        msg = f"--pyproject must be an existing file: {resolved}"
        raise ValueError(msg)
    for ancestor in resolved.parent.parents:
        candidate = ancestor / "pyproject.toml"
        if not candidate.is_file():
            continue
        data = tomllib.loads(candidate.read_text(encoding="utf-8"))
        tool = data.get("tool")
        uv = tool.get("uv") if isinstance(tool, dict) else None
        if isinstance(uv, dict) and isinstance(uv.get("workspace"), dict):
            msg = f"--pyproject must not select a uv workspace member; use the workspace-root manifest instead: {resolved}"
            raise TypeError(msg)
    return resolved


def _masked_manifest(text: str, managed_names: frozenset[str]) -> dict[str, object]:
    """Return parsed manifest data with only managed pin versions masked."""
    data = tomllib.loads(text)
    groups = _required_table(data, "dependency-groups")
    dev = groups.get("dev")
    if not isinstance(dev, list):
        msg = "dependency-groups.dev must be an array"
        raise TypeError(msg)
    masked: list[object] = []
    for entry in dev:
        if not isinstance(entry, str):
            masked.append(entry)
            continue
        try:
            requirement = Requirement(entry)
        except InvalidRequirement:
            masked.append(entry)
            continue
        specifiers = list(requirement.specifier)
        normalized = canonicalize_name(requirement.name)
        if (
            normalized in managed_names
            and not requirement.extras
            and requirement.marker is None
            and requirement.url is None
            and len(specifiers) == 1
            and specifiers[0].operator == "=="
            and "*" not in specifiers[0].version
        ):
            masked.append(f"{normalized}==<managed-version>")
        else:
            masked.append(entry)
    groups["dev"] = masked
    return cast("dict[str, object]", data)


def _require_applied_pins(pyproject: Path, expected: list[DevPin], original: bytes) -> None:
    """Require uv to have changed only the exact requested manifest pins."""
    updated_text = pyproject.read_text(encoding="utf-8")
    _requires_python, actual = parse_project(updated_text)
    expected_versions = {canonicalize_name(pin.name): pin.version for pin in expected}
    actual_versions = {canonicalize_name(pin.name): pin.version for pin in actual}
    if actual_versions != expected_versions:
        msg = f"uv did not apply the resolved development-tool pins to {pyproject}"
        raise ValueError(msg)
    managed_names = frozenset(expected_versions)
    original_text = original.decode("utf-8")
    if _masked_manifest(updated_text, managed_names) != _masked_manifest(original_text, managed_names):
        msg = f"uv changed non-target manifest content in {pyproject}"
        raise ValueError(msg)


def update_dev_pins(pyproject: Path, *, uv: str = "uv") -> dict[str, tuple[str, str]]:
    """Resolve and apply all changed exact direct pins in one uv transaction."""
    manifest = _conventional_manifest(pyproject)
    uv_lock = manifest.parent / "uv.lock"
    if uv_lock.is_symlink():
        msg = f"uv.lock must not be a symbolic link: {uv_lock}"
        raise ValueError(msg)
    requires_python, current = parse_project(manifest.read_text(encoding="utf-8"))
    if not current:
        return {}
    latest = resolve_latest_pins(current, requires_python, manifest.parent, uv=uv)
    changes = {old.name: (old.version, new.version) for old, new in zip(current, latest, strict=True) if old.version != new.version}
    if not changes:
        return changes

    with preserve_files((manifest, uv_lock)) as snapshots:
        run_safe_command(
            uv,
            [
                "add",
                "--dev",
                "--no-sync",
                *(f"{pin.name}=={pin.version}" for pin in latest),
                "--directory",
                str(manifest.parent),
                "--project",
                str(manifest.parent),
            ],
            cwd=manifest.parent,
            timeout=UV_ADD_TIMEOUT_SECONDS,
        )
        _require_applied_pins(manifest, latest, snapshots[manifest] or b"")
    return changes


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="project manifest containing dependency-groups.dev",
    )
    parser.add_argument("--uv-executable", default="uv", help="uv executable used for resolution and updates")
    return parser.parse_args(argv)


def _subprocess_detail(error: subprocess.CalledProcessError) -> str:
    """Return captured tool diagnostics when available."""
    stderr = error.stderr.strip() if isinstance(error.stderr, str) else ""
    stdout = error.stdout.strip() if isinstance(error.stdout, str) else ""
    return cast("str", stderr or stdout or str(error))


def main(argv: list[str] | None = None) -> int:
    """Advance exact development-tool pins without touching other requirements."""
    args = parse_args(argv)
    try:
        _requires_python, pins = parse_project(args.pyproject.read_text(encoding="utf-8"))
        if not pins:
            print("No exact direct Python development-tool pins to update.")
            return 0
        changes = update_dev_pins(args.pyproject, uv=args.uv_executable)
    except ExceptionGroup as error:
        expected, unexpected = error.split((ExecutableNotFoundError, OSError, RuntimeError, subprocess.SubprocessError, TypeError, ValueError))
        if expected is not None:
            print(f"failed to update Python development-tool pins: {format_exception_diagnostics(expected)}", file=sys.stderr)
        if unexpected is not None:
            raise unexpected from None
        return 1
    except subprocess.CalledProcessError as error:
        print(f"failed to update Python development-tool pins: {_subprocess_detail(error)}", file=sys.stderr)
        return 1
    except (ExecutableNotFoundError, OSError, RuntimeError, subprocess.TimeoutExpired, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"failed to update Python development-tool pins: {error}", file=sys.stderr)
        return 1

    if not changes:
        print("Python development-tool pins are already current.")
        return 0
    for name, (old_version, new_version) in changes.items():
        print(f"Updated {name}: {old_version} -> {new_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
