"""Installed-package authority for opt-in managed Python and target inference."""

import re
import tomllib
from dataclasses import dataclass
from importlib.metadata import metadata, version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

DEVELOPMENT_PYTHON = "3.14"
PACKAGE = "research-repo-tools"


@dataclass(frozen=True, slots=True)
class PythonBaseline:
    """Runtime requirement and selected development minor are separate contracts."""

    requirement: str
    selected: str
    package_version: str

    def __post_init__(self):
        if not re.fullmatch(r"3\.[1-9][0-9]*", self.selected):
            raise ValueError("shared development Python must select a minor version")
        if (SpecifierSet(f"=={self.selected}.*") & SpecifierSet(self.requirement)).is_unsatisfiable():
            raise ValueError("shared development Python is incompatible with package Requires-Python")


def baseline() -> PythonBaseline:
    """Read runtime support from installed distribution metadata, never a checkout."""
    requires = metadata(PACKAGE).get("Requires-Python")
    if not requires:
        raise ValueError("installed shared package has no Requires-Python metadata")
    return PythonBaseline(requires, DEVELOPMENT_PYTHON, version(PACKAGE))


def package_groups(document: dict) -> dict[str, list[Requirement]]:
    result = {}
    for group, requirements in document.get("dependency-groups", {}).items():
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
    sources = document.get("tool", {}).get("uv", {}).get("sources", {})
    if any(canonicalize_name(name) == PACKAGE for name in sources):
        raise ValueError("shared Python inheritance requires a published registry pin, not tool.uv.sources")
    return result


def drift(root: Path, document: dict | None = None) -> tuple[str, ...]:
    """Report mirror or package drift without resolution, installation, or edits."""
    document = document if document is not None else tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    authority = baseline()
    problems = []
    groups = package_groups(document)
    if any(str(item.specifier) != f"=={authority.package_version}" for requirements in groups.values() for item in requirements):
        problems.append(f"package pins must match installed {PACKAGE}=={authority.package_version}")
    selector = root / ".python-version"
    if not selector.is_file() or selector.read_text(encoding="utf-8").strip() != authority.selected:
        problems.append(f".python-version must mirror shared development Python {authority.selected}")
    project = document.get("project", {})
    uv = document.get("tool", {}).get("uv", {})
    problems.extend(target_problems(document, authority.selected))
    if uv.get("package") is False:
        if project.get("requires-python") != authority.requirement:
            problems.append(f"dependency-only project.requires-python must mirror {authority.requirement}")
    else:
        # Public runtime compatibility is retained. uv narrows only tooling groups.
        for group in groups:
            if uv.get("dependency-groups", {}).get(group, {}).get("requires-python") != authority.requirement:
                problems.append(f"tool.uv.dependency-groups.{group}.requires-python must mirror {authority.requirement}")
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
