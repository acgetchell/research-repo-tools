"""Installed-release authority for mandatory Python support and development mirrors."""

import re
import tomllib
from dataclasses import dataclass
from importlib.metadata import metadata, version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from research_repo_tools.config import _table

DEVELOPMENT_PYTHON = "3.14"
PACKAGE = "research-repo-tools"


@dataclass(frozen=True, slots=True)
class PythonBaseline:
    """Runtime requirement and selected development minor are separate contracts."""

    requirement: str
    selected: str
    package_version: str

    def __post_init__(self):
        _ = self.minimum_requirement
        if not re.fullmatch(r"3\.[1-9][0-9]*", self.selected):
            raise ValueError("shared development Python must select a minor version")
        if (SpecifierSet(f"=={self.selected}.*") & SpecifierSet(self.requirement)).is_unsatisfiable():
            raise ValueError("shared development Python is incompatible with package Requires-Python")

    @property
    def minimum_requirement(self) -> str:
        """Extract the published lower bound independently of the dev selector.

        Upper bounds and exclusions belong to the shared tool's own installation
        contract; consumers inherit its language-feature floor, not those limits.
        """
        bounds = []
        for item in SpecifierSet(self.requirement):
            if item.operator in {">=", ">", "~=", "=="}:
                value = Version(item.version.removesuffix(".*"))
                bounds.append((value, item.operator == ">"))
        if not bounds:
            raise ValueError("installed shared Requires-Python must declare a minimum version")
        value, exclusive = max(bounds)
        return f"{'>' if exclusive else '>='}{value}"


def reconcile_requirement(requirement: object, authority: PythonBaseline) -> str:
    """Intersect the consumer with the floor, preserving every local restriction."""
    minimum = authority.minimum_requirement
    if requirement is None:
        return minimum
    if not isinstance(requirement, str):
        raise ValueError("project.requires-python must be a version specifier string")
    current = SpecifierSet(requirement)
    if (current & SpecifierSet(minimum)).is_unsatisfiable():
        raise ValueError(f"project.requires-python {requirement!r} conflicts with shared minimum {minimum}; review compatibility before adoption")
    opposite = ("<=" if minimum.startswith(">") and not minimum.startswith(">=") else "<") + minimum.lstrip(">=")
    if (current & SpecifierSet(opposite)).is_unsatisfiable():
        return requirement
    return ",".join(filter(None, (requirement, minimum)))


def minimum_problems(document: dict, authority: PythonBaseline) -> tuple[str, ...]:
    project = _table(document.get("project", {}), "project")
    requirement = project.get("requires-python")
    try:
        reconciled = reconcile_requirement(requirement, authority)
    except ValueError as error:
        return (str(error),)
    if reconciled != requirement:
        return (f"project.requires-python must enforce installed {PACKAGE}=={authority.package_version} minimum {authority.minimum_requirement}",)
    return ()


def check_minimum(root: Path, document: dict | None = None) -> None:
    """Reject stale application metadata regardless of development opt-ins.

    Source-only lint directories without a project or shared dependency are not
    managed Python consumers. Pin reconciliation remains an explicit adoption.
    """
    if document is None:
        manifest = root / "pyproject.toml"
        if not manifest.is_file():
            return
        document = tomllib.loads(manifest.read_text(encoding="utf-8"))
    project = document.get("project", {})
    groups = document.get("dependency-groups", {})
    if not isinstance(project, dict):
        raise ValueError("project must be a table")
    if not isinstance(groups, dict):
        raise ValueError("dependency-groups must be a table")
    managed = any(
        isinstance(raw, str) and canonicalize_name(Requirement(raw).name) == PACKAGE
        for requirements in groups.values()
        if isinstance(requirements, list)
        for raw in requirements
    )
    if not project and not managed:
        return
    authority = baseline()
    problems = list(minimum_problems(document, authority))
    selector = root / ".python-version"
    if selector.is_file():
        selected = selector.read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"3\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))?", selected):
            problems.append(".python-version must select Python as 3.MINOR[.PATCH]")
        else:
            request = f"=={selected}.*" if selected.count(".") == 1 else f"=={selected}"
            requirement = project.get("requires-python", authority.minimum_requirement)
            if isinstance(requirement, str) and (SpecifierSet(request) & SpecifierSet(requirement) & SpecifierSet(authority.requirement)).is_unsatisfiable():
                problems.append(f".python-version must satisfy application metadata and installed shared Python {authority.requirement}")
    if problems:
        raise ValueError("shared Python drift: " + "; ".join(problems) + "; run the target pinned package's toolchain adopt --dry-run, then --apply")


def baseline() -> PythonBaseline:
    """Read runtime support from installed distribution metadata, never a checkout."""
    requires = metadata(PACKAGE).get("Requires-Python")
    if not requires:
        raise ValueError("installed shared package has no Requires-Python metadata")
    return PythonBaseline(requires, DEVELOPMENT_PYTHON, version(PACKAGE))


def package_groups(document: dict) -> dict[str, list[Requirement]]:
    result = {}
    groups = document.get("dependency-groups", {})
    if not isinstance(groups, dict):
        raise ValueError("dependency-groups must be a table")
    for group, requirements in groups.items():
        if not isinstance(requirements, list):
            raise ValueError(f"dependency-groups.{group} must be an array")
        selected = []
        for value in requirements:
            if not isinstance(value, str):
                continue
            item = Requirement(value)
            if canonicalize_name(item.name) == PACKAGE:
                if item.url or item.marker or len(item.specifier) != 1 or next(iter(item.specifier)).operator != "==" or "*" in str(item.specifier):
                    raise ValueError("shared Python inheritance requires unmarked exact registry package pins in dependency groups")
                selected.append(item)
        if selected:
            result[group] = selected
    if not result:
        raise ValueError("shared Python inheritance requires an exact research-repo-tools pin in a dependency group")
    tool = _table(document.get("tool", {}), "tool")
    uv = _table(tool.get("uv", {}), "tool.uv")
    sources = _table(uv.get("sources", {}), "tool.uv.sources")
    if any(canonicalize_name(name) == PACKAGE for name in sources):
        raise ValueError("shared Python inheritance requires a published registry pin, not tool.uv.sources")
    return result


def drift(root: Path, document: dict | None = None) -> tuple[str, ...]:
    """Report mirror or package drift without resolution, installation, or edits."""
    document = document if document is not None else tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    authority = baseline()
    problems = list(minimum_problems(document, authority))
    groups = package_groups(document)
    if any(str(item.specifier) != f"=={authority.package_version}" for requirements in groups.values() for item in requirements):
        problems.append(f"package pins must match installed {PACKAGE}=={authority.package_version}")
    selector = root / ".python-version"
    if not selector.is_file() or selector.read_text(encoding="utf-8").strip() != authority.selected:
        problems.append(f".python-version must mirror shared development Python {authority.selected}")
    uv = document.get("tool", {}).get("uv", {})
    problems.extend(target_problems(document, authority.selected))
    if uv.get("package") is not False:
        for group in groups:
            requirement = uv.get("dependency-groups", {}).get(group, {}).get("requires-python")
            if requirement is None or reconcile_requirement(requirement, authority) != requirement:
                problems.append(f"tool.uv.dependency-groups.{group}.requires-python must enforce {authority.minimum_requirement}")
    return tuple(problems)


def target_problems(document: dict, selected: str) -> tuple[str, ...]:
    """Retain deliberate lower targets; reject targets newer than the interpreter."""
    tool = document.get("tool", {})
    targets = (("Ruff", tool.get("ruff", {}).get("target-version")), ("ty", tool.get("ty", {}).get("environment", {}).get("python-version")))
    problems = []
    for name, target in targets:
        if target is None:
            continue
        normalized = re.sub(r"^py3", "3.", target) if isinstance(target, str) else ""
        if not re.fullmatch(r"3\.[1-9][0-9]*", normalized) or tuple(map(int, normalized.split("."))) > tuple(map(int, selected.split("."))):
            problems.append(f"{name} target {target!r} is incompatible with shared Python {selected}")
    return tuple(problems)


def check(root: Path, document: dict | None = None) -> None:
    if problems := drift(root, document):
        raise ValueError("shared Python drift: " + "; ".join(problems) + "; run the target pinned package's toolchain adopt --dry-run, then --apply")
