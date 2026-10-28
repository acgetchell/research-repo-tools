"""Read-only first-use report inventory; initialized reports retain strict checks."""

import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from research_repo_tools.evidence import _string
from research_repo_tools.files import _paths_alias, _validate_distinct_paths
from research_repo_tools.publication import _path, _publication_name
from research_repo_tools.publication_config import _table

__all__ = ["ReportCheck", "check_report"]


@dataclass(frozen=True, slots=True)
class ReportCheck:
    status: Literal["uninitialized", "current", "stale"]
    changed_paths: tuple[Path, ...] = ()


def _names(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"initialization.{name} must be an array of paths")
    return tuple(_publication_name(_string(item, name)) for item in value)


def _directory(root: Path, name: str) -> Path:
    _publication_name(name)
    path = root / name
    for item in (path, *path.parents):
        if item.is_relative_to(root) and (item.is_symlink() or item.is_junction()):
            raise ValueError(f"inventory directory must not contain links: {name}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"inventory directory must be a regular directory: {name}")
    return path


def _portable_observation(root: Path, name: str) -> None:
    """Do not mistake a differently cased/normalized existing output for absence."""
    parent = root
    for part in Path(name).parts:
        if not parent.is_dir():
            return
        key = unicodedata.normalize("NFC", part).casefold()
        if any(child.name != part and unicodedata.normalize("NFC", child.name).casefold() == key for child in parent.iterdir()):
            raise ValueError(f"inventory path has a case/Unicode alias: {name}")
        parent = parent / part


def report_configuration(root: Path, configuration: str) -> tuple[bytes, dict[str, object]]:
    """Parse the complete declaration, including first-use policy, before effects."""
    content = _path(root, configuration).read_bytes()
    document = tomllib.loads(content.decode("utf-8"))
    schema = document.get("schema")
    if type(schema) is not int or schema not in (1, 2):
        raise ValueError("report schema must be integer 1 or 2")
    optional = {"prose-file"} if schema == 1 else {"measurement", "release"}
    raw = _table(document, "report", {"schema", "current", "archive", "title"}, optional | {"initialization"})
    current, archive = (_string(raw[key], key) for key in ("current", "archive"))
    _string(raw["title"], "title")
    _path(root, current)
    directory = _directory(root, archive)
    if _paths_alias(root / current, directory):
        raise ValueError("current report must not alias its archive directory")
    files = [configuration, current]
    for key in ("prose-file", "measurement"):
        if key in raw:
            files.append(_string(raw[key], key))
    if "release" in raw and type(raw["release"]) is not bool:
        raise ValueError("report release must be a boolean")
    if raw.get("release") and "measurement" not in raw:
        raise ValueError("release promotion requires an independent measurement policy")
    if "initialization" in raw:
        policy = _table(raw["initialization"], "initialization", {"allow-empty", "directories", "guards"})
        if type(policy["allow-empty"]) is not bool:
            raise ValueError("initialization.allow-empty must be a boolean")
        directories = (archive, *_names(policy["directories"], "directories"))
        guards = _names(policy["guards"], "guards")
        for name in (*files, *directories, *guards):
            _portable_observation(root, name)
        _validate_distinct_paths(tuple(_directory(root, name) for name in directories))
        _validate_distinct_paths(tuple(_path(root, name) for name in (*files, *guards)))
        # Inputs and companion guards cannot be hidden inside output inventory.
        for name in (configuration, *files[2:], *guards):
            _validate_distinct_paths((_path(root, name), *(root / directory for directory in directories)))
    _validate_distinct_paths(tuple(_path(root, name) for name in files))
    return content, raw


def check_report(root: Path, configuration: str) -> ReportCheck:
    """Accept a declared empty inventory, otherwise verify the retained report.

    All archive entries and additional declared directories count, regardless of
    suffix. Guards are exact companion paths outside those directories. No files
    or placeholders are created, and no measurements or network calls occur.
    """
    root = root.resolve(strict=True)
    _, raw = report_configuration(root, configuration)
    current = _path(root, _string(raw["current"], "current"))
    policy = raw.get("initialization")
    if not current.exists() and isinstance(policy, dict) and policy["allow-empty"]:
        inventory = []
        for name in (_string(raw["archive"], "archive"), *_names(policy["directories"], "directories")):
            directory = _directory(root, name)
            if directory.exists():
                # Any entry, including an empty partial run directory or dangling
                # link, disproves first use. Never follow a link while scanning.
                inventory.extend(directory.iterdir())
        inventory.extend(_path(root, name) for name in _names(policy["guards"], "guards") if _path(root, name).exists())
        if inventory:
            raise ValueError("uninitialized report has orphaned inventory: " + ", ".join(sorted(path.relative_to(root).as_posix() for path in inventory)))
        if "prose-file" in raw:
            _path(root, _string(raw["prose-file"], "prose-file")).read_bytes().decode("utf-8")
        if "measurement" in raw:
            from research_repo_tools.measurement import load_measurement

            load_measurement(root, _string(raw["measurement"], "measurement"))
        return ReportCheck("uninitialized")
    from research_repo_tools.performance_reports import load_report_plan

    plan = load_report_plan(root, configuration)
    return ReportCheck("stale" if plan.changed_paths else "current", plan.changed_paths)
