"""Carry project-only uv policy into disposable, read-only pin resolution."""

import json
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

from research_repo_tools.config import _table

# Other uv resolver/build/authentication settings are read from the selected
# project's configuration by --directory/--project. These project metadata
# fields must also be present on the PEP 723 target itself.
PROJECT_POLICY = frozenset(
    {
        "build-constraint-dependencies",
        "constraint-dependencies",
        "dependency-metadata",
        "environments",
        "exclude-dependencies",
        "index",
        "override-dependencies",
        "required-environments",
        "sources",
    }
)


def _literal(value: object) -> str:
    """Encode the bounded TOML values used by uv metadata, never executable text."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return "{" + ", ".join(f"{json.dumps(key)} = {_literal(item)}" for key, item in value.items()) + "}"
    raise ValueError("unsupported uv resolution metadata value")


def policy(text: str, root: Path) -> dict[str, object]:
    """Validate source forms before writes; retain named-index identity and auth."""
    data = tomllib.loads(text)
    uv = _table(_table(data.get("tool", {}), "tool").get("uv", {}), "tool.uv")
    for name in ("workspace", "conflicts", "dev-dependencies"):
        if uv.get(name):
            raise ValueError(f"Python pin updates do not support tool.uv.{name}; update this project explicitly with uv")
    project = _table(data.get("project", {}), "project")
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list):
        raise ValueError("project.dynamic must be an array")
    if "dependencies" in dynamic:
        raise ValueError("Python pin updates require static project dependencies")
    sources = _table(uv.get("sources", {}), "tool.uv.sources")
    for name, entries in sources.items():
        for entry in entries if isinstance(entries, list) else [entries]:
            if isinstance(entry, dict) and isinstance(entry.get("path"), str) and not set(entry) - {"path", "marker"}:
                path = Path(entry["path"])
                if path.is_absolute() and path.is_file() and path.name.endswith((".whl", ".tar.gz", ".zip")):
                    continue
            if not isinstance(entry, dict) or not isinstance(entry.get("index"), str) or set(entry) - {"index", "marker"}:
                raise ValueError(
                    f"unsupported tool.uv.sources.{name}: pin updates support named indexes and absolute wheel/sdist files with optional markers; "
                    "relative paths, directories, workspace/Git/URL/group/extra sources require explicit uv maintenance"
                )
    result = {key: value for key, value in uv.items() if key in PROJECT_POLICY}
    if "index" in result:
        indexes = result["index"]
        if not isinstance(indexes, list):
            raise ValueError("tool.uv.index must be an array of tables")
        for entry in indexes:
            if not isinstance(entry, dict) or not isinstance(entry.get("url"), str):
                raise ValueError("each tool.uv.index must declare a URL")
            url = entry["url"]
            # A local simple index is relative to the consumer, not to the
            # temporary script. URLs (including embedded auth) remain intact.
            if not urlsplit(url).scheme or Path(url).is_absolute():
                entry["url"] = (root / url).resolve().as_uri()
    for name in ("constraint-dependencies", "override-dependencies", "build-constraint-dependencies"):
        entries = result.get(name, [])
        if not isinstance(entries, list):
            raise ValueError(f"tool.uv.{name} must be an array")
        for entry in entries:
            if not isinstance(entry, str):
                raise ValueError(f"unsupported tool.uv.{name}: pin updates require requirement strings")
            require_registry_requirement(entry)
    return result


def artifact_source_names(text: str) -> set[str]:
    """Fixed local distributions retain their pins; they have no upgrade index."""
    data = tomllib.loads(text)
    uv = _table(_table(data.get("tool", {}), "tool").get("uv", {}), "tool.uv")
    sources = _table(uv.get("sources", {}), "tool.uv.sources")
    return {
        canonicalize_name(name)
        for name, entries in sources.items()
        for entry in (entries if isinstance(entries, list) else [entries])
        if isinstance(entry, dict) and isinstance(entry.get("path"), str) and Path(entry["path"]).is_absolute()
    }


def group_constraints(text: str) -> dict[str, str]:
    """Retain per-group Python support when flattening the dev include closure."""
    data = tomllib.loads(text)
    uv = _table(_table(data.get("tool", {}), "tool").get("uv", {}), "tool.uv")
    groups = _table(uv.get("dependency-groups", {}), "tool.uv.dependency-groups")
    result = {}
    for name, value in groups.items():
        entry = _table(value, f"tool.uv.dependency-groups.{name}")
        requirement = entry.get("requires-python")
        if set(entry) != {"requires-python"} or not isinstance(requirement, str):
            raise ValueError(f"unsupported tool.uv.dependency-groups.{name}: expected requires-python")
        specifiers = SpecifierSet(requirement)
        if specifiers.is_unsatisfiable():
            raise ValueError(f"tool.uv.dependency-groups.{name}.requires-python has no matching versions")
        result[name] = str(specifiers)
    return result


def constrain_group(raw: str, python: str) -> str:
    if not python:
        return raw
    requirement = Requirement(raw)
    marker = requirement.marker
    requirement.marker = None
    conditions = [f"python_full_version {specifier.operator} {json.dumps(specifier.version)}" for specifier in sorted(SpecifierSet(python), key=str)]
    if marker is not None:
        conditions.insert(0, f"({marker})")
    return str(Requirement(f"{requirement}; " + " and ".join(conditions)))


def require_registry_requirement(raw: str) -> None:
    if Requirement(raw).url is not None:
        raise ValueError("Python pin updates do not support direct URL/path/Git requirements; use a named package index")


def script_metadata(text: str, root: Path, requires_python: str, requirements: list[str]) -> str:
    for raw in requirements:
        require_registry_requirement(raw)
    metadata: dict[str, object] = {"requires-python": requires_python, "dependencies": requirements}
    options = policy(text, root)
    if options:
        metadata["tool"] = {"uv": options}
    document = "\n".join(f"{json.dumps(key)} = {_literal(value)}" for key, value in metadata.items())
    tomllib.loads(document)
    return "# /// script\n" + "\n".join("# " + line for line in document.splitlines()) + "\n# ///\n"
