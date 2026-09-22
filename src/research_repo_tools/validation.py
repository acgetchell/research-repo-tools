"""Configured command checks and reusable executable/package preflights."""

import re
import sys
import tomllib
from pathlib import Path

from research_repo_tools.evidence import _load_json, _object, _string
from research_repo_tools.measurement import _strings
from research_repo_tools.process import resolve_executable, run_command
from research_repo_tools.publication import _path
from research_repo_tools.publication_config import _table

__all__ = ["check_cargo_metadata", "require_executables", "run_checks"]


def require_executables(root: Path, names: tuple[str, ...]) -> tuple[Path, ...]:
    """Resolve every prerequisite without installing or launching any of them."""
    return tuple(resolve_executable(name, cwd=root) for name in names)


def run_checks(root: Path, configuration: str, names: tuple[str, ...] = ()) -> None:
    """Run named checks from trusted schema-1 TOML and require literal markers.

    The consumer owns commands, execution order and scientific output markers.
    Parse all selected commands before any execution. Capture each command's
    stdout/stderr with a deadline; echo them and fail on a nonzero status or
    absent marker. Explicit binary paths use platform executable discovery.
    """
    raw = _table(tomllib.loads(_path(root.resolve(), configuration).read_bytes().decode("utf-8")), "validation", {"schema", "checks"}, {"prerequisites"})
    if type(raw["schema"]) is not int or raw["schema"] != 1:
        raise ValueError("validation schema must be integer 1")
    if not isinstance(raw["checks"], list) or not raw["checks"]:
        raise ValueError("validation checks must be a nonempty array")
    checks = []
    for entry in raw["checks"]:
        check = _table(entry, "validation check", {"name", "command"}, {"expect", "timeout"})
        name = _string(check["name"], "check name")
        command = _strings(check["command"], "check command")
        expected = _strings(check.get("expect", []), "expected markers", nonempty=False)
        timeout = check.get("timeout", 300)
        if type(timeout) is not int or timeout <= 0:
            raise ValueError("check timeout must be a positive integer")
        checks.append((name, command, expected, timeout))
    available = {check[0] for check in checks}
    if len(available) != len(checks) or len(set(names)) != len(names) or set(names) - available:
        raise ValueError("validation check names must be unique and select known entries")
    require_executables(root, _strings(raw.get("prerequisites", []), "prerequisites", nonempty=False))
    for name, command, expected, timeout in checks:
        if names and name not in names:
            continue
        result = run_command(command[0], command[1:], cwd=root, timeout=timeout)
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        for marker in expected:
            if marker not in result.stdout:
                raise ValueError(f"check {name} is missing expected output marker {marker!r}")


def check_cargo_metadata(root: Path, *, package: str | None = None) -> None:
    """Check keyword/category/description constraints before native packaging."""
    data = _object(
        _load_json(run_command("cargo", ["metadata", "--locked", "--no-deps", "--format-version=1"], cwd=root).stdout.encode(), "Cargo metadata"),
        "Cargo metadata",
    )
    packages = data.get("packages")
    if not isinstance(packages, list):
        raise ValueError("Cargo metadata packages must be an array")
    entries = [_object(item, "Cargo package") for item in packages]
    if package is not None:
        entries = [item for item in entries if item.get("name") == package]
    if len(entries) != 1:
        raise ValueError("select exactly one Cargo package for metadata validation")
    selected = entries[0]
    keywords = _strings(selected.get("keywords"), "Cargo keywords", nonempty=False)
    if len(keywords) > 5 or any(len(value) > 20 or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None for value in keywords):
        raise ValueError("Cargo keywords require at most five ASCII alphanumeric/underscore/hyphen strings of at most 20 characters")
    categories = _strings(selected.get("categories"), "Cargo categories", nonempty=False)
    if len(categories) > 5:
        raise ValueError("Cargo categories require at most five entries")
    description = _string(selected.get("description"), "Cargo description")
    if len(description) > 1000:
        raise ValueError("Cargo description exceeds 1000 characters")
