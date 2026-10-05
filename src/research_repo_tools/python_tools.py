"""Opt-in tool versions owned solely by the installed python-tools extra."""

import tomllib
from importlib.metadata import metadata, version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from research_repo_tools.config import _table
from research_repo_tools.python_baseline import PACKAGE, package_groups
from research_repo_tools.toml_source import replace_array_strings

EXTRA = "python-tools"
TOOLS = frozenset({"pytest", "ruff", "ty"})


def versions() -> dict[str, str]:
    """Read exact, unconditional extra pins from distribution metadata."""
    result = {}
    for raw in metadata(PACKAGE).get_all("Requires-Dist", []):
        item = Requirement(raw)
        name = canonicalize_name(item.name)
        if name not in TOOLS or item.marker is None or not item.marker.evaluate({"extra": EXTRA}):
            continue
        specs = list(item.specifier)
        if item.url or item.extras or len(specs) != 1 or specs[0].operator != "==" or "*" in specs[0].version or name in result:
            raise ValueError(f"installed {PACKAGE}[{EXTRA}] must provide one exact pin for {name}")
        result[name] = str(Version(specs[0].version))
    if result.keys() != TOOLS:
        raise ValueError(f"installed {PACKAGE}[{EXTRA}] must pin pytest, ruff, and ty")
    return dict(sorted(result.items()))


def _groups(document: dict) -> dict[str, list[str]]:
    from research_repo_tools.dependencies import _group_requirements

    groups = _table(document.get("dependency-groups", {}), "dependency-groups")
    direct = {}
    for group, entries in groups.items():
        if not isinstance(entries, list):
            raise ValueError(f"dependency-groups.{group} must be an array")
        _group_requirements(groups, group)
        direct[group] = [raw for raw in entries if isinstance(raw, str)]
    dev = _group_requirements(groups, "dev")
    if not any(canonicalize_name(Requirement(raw).name) == PACKAGE for raw in dev):
        raise ValueError("shared Python tools require dev to include the exact shared-package dependency")
    return direct


def _requirements(value: object, context: str) -> tuple[Requirement, ...]:
    if not isinstance(value, list) or any(not isinstance(raw, str) for raw in value):
        raise ValueError(f"{context} must be an array of requirement strings")
    return tuple(Requirement(raw) for raw in value)


def _external_constraints(document: dict) -> None:
    """Public dependencies and uv overrides cannot silently lose their authority."""
    project = _table(document.get("project", {}), "project")
    uv = _table(_table(document.get("tool", {}), "tool").get("uv", {}), "tool.uv")
    constraints = list(_requirements(project.get("dependencies", []), "project.dependencies"))
    for name, entries in _table(project.get("optional-dependencies", {}), "project.optional-dependencies").items():
        constraints.extend(_requirements(entries, f"project.optional-dependencies.{name}"))
    for name in ("constraint-dependencies", "override-dependencies", "build-constraint-dependencies"):
        constraints.extend(_requirements(uv.get(name, []), f"tool.uv.{name}"))
    for item in constraints:
        if canonicalize_name(item.name) in TOOLS:
            raise ValueError(
                f"shared Python tools conflict with a public dependency or uv constraint: {item}; retain consumer ownership or remove it deliberately"
            )
    for name in _table(uv.get("sources", {}), "tool.uv.sources"):
        if canonicalize_name(name) in TOOLS:
            raise ValueError(f"shared Python tools require registry packages, not tool.uv.sources.{name}")


def adopt_manifest(text: str) -> str:
    """Retire direct tool pins in groups, preserving native policy and comments.

    Old plain exact pins are superseded mirrors. Compatible ranges can become
    unversioned requirements; incompatible ranges, markers, extras, URLs, public
    requirements, and overrides require an explicit consumer decision first.
    """
    authority = versions()
    document = tomllib.loads(text)
    _external_constraints(document)
    groups = _groups(document)
    package_groups(document)
    for group, entries in groups.items():
        replacements = {}
        for raw in entries:
            item = Requirement(raw)
            name = canonicalize_name(item.name)
            if name == PACKAGE:
                extras = ",".join(sorted(item.extras | {EXTRA}))
                replacements[raw] = f"{item.name}[{extras}]=={version(PACKAGE)}"
            elif name in TOOLS:
                specs = list(item.specifier)
                stale_pin = len(specs) == 1 and specs[0].operator == "==" and "*" not in specs[0].version
                if item.url or item.marker or item.extras or not stale_pin and authority[name] not in item.specifier:
                    raise ValueError(f"{group}: {raw} conflicts with shared {name}=={authority[name]}; reconcile the constraint before adoption")
                replacements[raw] = item.name
        if replacements:
            text = replace_array_strings(text, "dependency-groups", group, replacements)
    return text


def check_declarations(document: dict) -> None:
    """Check installed release, opt-in extra, and lack of competing version pins."""
    versions()
    _external_constraints(document)
    groups = _groups(document)
    for entries in package_groups(document).values():
        for item in entries:
            if EXTRA not in item.extras or str(item.specifier) != f"=={version(PACKAGE)}":
                raise ValueError(f"package groups must request {PACKAGE}[{EXTRA}]=={version(PACKAGE)} (retaining other extras)")
    for group, entries in groups.items():
        for raw in entries:
            item = Requirement(raw)
            if canonicalize_name(item.name) in TOOLS and (item.specifier or item.url or item.extras or item.marker):
                raise ValueError(f"{group}: {raw} competes with the installed python-tools extra; use an unversioned requirement")


def _locked_versions(document: dict) -> dict[str, set[str | None]]:
    entries = document.get("package", [])
    if not isinstance(entries, list):
        raise ValueError("uv.lock package must be an array of tables")
    result: dict[str, set[str | None]] = {}
    for index, raw in enumerate(entries):
        entry = _table(raw, f"uv.lock package[{index}]")
        name, pin = entry.get("name"), entry.get("version")
        if not isinstance(name, str) or not name or pin is not None and not isinstance(pin, str):
            raise ValueError(f"uv.lock package[{index}] requires a name string and an optional version string")
        result.setdefault(canonicalize_name(name, validate=True), set()).add(pin)
    return result


def check(root: Path, *, executables: bool = True) -> None:
    """Inspect declarations, the locked dev graph, and selected executables; never sync."""
    from research_repo_tools.process import run_command

    try:
        check_declarations(tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")))
        authority = versions()
        lock = _locked_versions(tomllib.loads((root / "uv.lock").read_text(encoding="utf-8")))
        for name, expected in {PACKAGE: version(PACKAGE), **authority}.items():
            locked = lock.get(name, set())
            if locked != {expected}:
                raise ValueError(f"uv.lock must resolve {name}=={expected}; found {sorted(map(str, locked))}")
        # Let uv select the reachable dev graph, including activated extras and
        # conditional edges. Frozen/offline export cannot resolve or sync, and
        # stdout plus a temporary cache leave the consumer untouched.
        exported = run_command(
            "uv",
            [
                "export",
                "--frozen",
                "--offline",
                "--no-cache",
                "--no-python-downloads",
                "--only-group",
                "dev",
                "--format",
                "requirements.txt",
                "--no-hashes",
                "--no-annotate",
                "--no-header",
                "--no-emit-local",
                "--project",
                str(root),
                "--directory",
                str(root),
            ],
            cwd=root,
            timeout=30,
        ).stdout
        reachable = {
            canonicalize_name(item.name) for item in _requirements(exported.splitlines(), "uv dev export") if item.marker is None or item.marker.evaluate()
        }
        if missing := TOOLS - reachable:
            raise ValueError(f"{', '.join(sorted(missing))} not reachable from the locked dev profile")
        if executables:
            for name, expected in authority.items():
                output = run_command(name, ["--version"], cwd=root, timeout=30).stdout.strip().split()
                if len(output) < 2 or output[0].casefold() != name or output[1] != expected:
                    raise ValueError(f"PATH must select {name}=={expected}; found {' '.join(output)}")
    except (ValueError, TypeError, OSError) as error:
        raise ValueError(f"shared Python tool drift: {error}; run the exact target package's toolchain adopt --dry-run, then --apply") from error
