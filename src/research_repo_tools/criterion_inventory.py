"""Configured native Criterion discovery with portable, retained semantic IDs."""

import os
import re
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from research_repo_tools.complete_runs import _integer
from research_repo_tools.evidence import _load_json, _object, _string, deterministic_json
from research_repo_tools.measurement import _strings
from research_repo_tools.process import run_command
from research_repo_tools.publication import _path, _publication_name
from research_repo_tools.publication_config import _table

__all__ = [
    "INVENTORY_SCHEMA",
    "CriterionInventory",
    "DiscoveryCommand",
    "DiscoveryPlan",
    "discover_inventory",
    "load_discovery",
    "parse_benchmark_list",
    "parse_inventory",
    "serialize_inventory",
]

INVENTORY_SCHEMA = "research-repo-tools/criterion-inventory/v1"


def _id(value: object) -> str:
    return _string(value, "semantic benchmark ID")


def parse_benchmark_list(output: object) -> tuple[str, ...]:
    """Read exact ': benchmark' records, ignoring unrelated native build noise.

    IDs are semantic strings, not paths; flat IDs and sanitized storage names
    are valid. CRLF/LF transport yields the same IDs. Malformed benchmark records,
    an empty list and duplicate IDs fail instead of producing partial coverage.
    """
    if not isinstance(output, str):
        raise TypeError("Criterion list output must be text")
    ids = []
    for line in output.replace("\r\n", "\n").split("\n"):
        if line.endswith(": benchmark"):
            ids.append(_id(line.removesuffix(": benchmark")))
        elif re.search(r":\s*benchmark\b", line):
            raise ValueError(f"malformed Criterion list record: {line!r}")
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Criterion inventory must be nonempty with unique semantic benchmark IDs")
    return tuple(sorted(ids))


def _environment_value(value: object) -> str:
    if not isinstance(value, str) or "\0" in value:
        raise ValueError("environment value must be text without NUL")
    return value


def _environment(values) -> tuple[tuple[str, str], ...]:
    pairs = []
    for item in values:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError("environment requires key/value pairs")
        key, value = _string(item[0], "environment key"), _environment_value(item[1])
        if "=" in key:
            raise ValueError("invalid environment entry")
        pairs.append((key, value))
    if len({key.casefold() for key, _ in pairs}) != len(pairs):
        raise ValueError("environment keys must be unique on every platform")
    return tuple(sorted(pairs))


def _overlay_environment(base: Mapping[str, str], overlay: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """Replace declared keys case-insensitively and give native PATH one spelling."""
    keys = {key.casefold() for key, _ in overlay}
    environment = {key: value for key, value in base.items() if key.casefold() not in keys}
    paths = [key for key in environment if key.casefold() == "path"]
    if len(paths) > 1:
        raise ValueError("inherited PATH keys must be unambiguous")
    if paths:
        environment["PATH"] = environment.pop(paths[0])
    environment.update(("PATH" if key.casefold() == "path" else key, value) for key, value in overlay)
    return environment


def _directory(root: Path, name: str) -> Path:
    if name != ".":
        _publication_name(name)
    path = root if name == "." else root / name
    if any(item.is_symlink() or item.is_junction() for item in (path, *path.parents) if item.is_relative_to(root)):
        raise ValueError(f"directory must not contain a symlink or junction: {name}")
    if not path.resolve().is_relative_to(root) or not path.is_dir():
        raise ValueError(f"directory must exist inside the root: {name}")
    return path


@dataclass(frozen=True, slots=True)
class DiscoveryCommand:
    name: str
    command: tuple[str, ...]
    cwd: str = "."
    environment: tuple[tuple[str, str], ...] = ()
    timeout: int = 7200

    def __post_init__(self) -> None:
        _string(self.name, "suite name")
        object.__setattr__(self, "command", _strings(self.command, "discovery command"))
        if self.cwd != ".":
            _publication_name(self.cwd)
        object.__setattr__(self, "environment", _environment(self.environment))
        if type(self.timeout) is not int or self.timeout <= 0:
            raise ValueError("discovery timeout must be a positive integer")


@dataclass(frozen=True, slots=True)
class CriterionInventory:
    suites: tuple[tuple[str, tuple[str, ...]], ...]
    excluded: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        suites = tuple((_string(name, "suite name"), tuple(sorted(_id(value) for value in _strings(ids, "suite IDs")))) for name, ids in self.suites)
        if not suites or len(dict(suites)) != len(suites):
            raise ValueError("inventory requires unique nonempty suites")
        ids = [value for _, values in suites for value in values]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate semantic benchmark IDs within or across suites")
        excluded = tuple(sorted(_id(value) for value in _strings(self.excluded, "excluded", nonempty=False)))
        if len(set(excluded)) != len(excluded) or not set(excluded) <= set(ids):
            raise ValueError("excluded IDs must be unique members of the discovered inventory")
        if not set(ids) - set(excluded):
            raise ValueError("selected Criterion inventory must not be empty")
        object.__setattr__(self, "suites", tuple(sorted(suites)))
        object.__setattr__(self, "excluded", excluded)

    @property
    def expected(self) -> tuple[str, ...]:
        return tuple(sorted({value for _, values in self.suites for value in values} - set(self.excluded)))


@dataclass(frozen=True, slots=True)
class DiscoveryPlan:
    suites: tuple[DiscoveryCommand, ...]
    required: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        suites = tuple(self.suites)
        if not suites or any(not isinstance(suite, DiscoveryCommand) for suite in suites):  # ty: ignore[redundant-condition-strict]  Runtime boundary also protects untyped callers.
            raise TypeError("discovery requires nonempty DiscoveryCommand suites")
        if len({suite.name for suite in suites}) != len(suites):
            raise ValueError("discovery suite names must be unique")
        for key in ("required", "excluded"):
            values = tuple(sorted(_id(value) for value in _strings(getattr(self, key), key, nonempty=False)))
            if len(set(values)) != len(values):
                raise ValueError(f"{key} IDs must be unique")
            object.__setattr__(self, key, values)
        if set(self.required) & set(self.excluded):
            raise ValueError("required IDs must not be excluded")
        object.__setattr__(self, "suites", tuple(sorted(suites, key=lambda suite: suite.name)))


def _parse_discovery(value: object) -> DiscoveryPlan:
    raw = _table(value, "discovery", {"suites"}, {"required", "excluded"})
    if not isinstance(raw["suites"], list):
        raise ValueError("discovery suites must be an array of tables")
    suites = []
    for value in raw["suites"]:
        item = _table(value, "discovery suite", {"name", "command"}, {"cwd", "environment", "timeout"})
        suites.append(
            DiscoveryCommand(
                _string(item["name"], "suite name"),
                _strings(item["command"], "discovery command"),
                _string(item.get("cwd", "."), "discovery cwd"),
                tuple((key, _environment_value(value)) for key, value in _object(item.get("environment", {}), "environment").items()),
                _integer(item.get("timeout", 7200), "discovery timeout"),
            )
        )
    return DiscoveryPlan(
        tuple(suites), _strings(raw.get("required", []), "required", nonempty=False), _strings(raw.get("excluded", []), "excluded", nonempty=False)
    )


def load_discovery(root: Path, configuration: str) -> DiscoveryPlan:
    """Load strict schema-1 discovery TOML without executing commands."""
    raw = _table(
        tomllib.loads(_path(root.resolve(), configuration).read_bytes().decode("utf-8")), "inventory discovery", {"schema", "suites"}, {"required", "excluded"}
    )
    if type(raw["schema"]) is not int or raw["schema"] != 1:
        raise ValueError("inventory discovery schema must be integer 1")
    return _parse_discovery({key: value for key, value in raw.items() if key != "schema"})


def discover_inventory(root: Path, plan: DiscoveryPlan, *, env: Mapping[str, str] | None = None) -> CriterionInventory:
    """Run bounded trusted commands with a separate private CRITERION_HOME per suite.

    Suite environments overlay the caller's environment (inherited by default).
    CRITERION_HOME is always replaced, case-insensitively, to keep discovery output
    separate from retained measurements. Commands may compile; no Git is mutated
    by this API. The timeout covers each direct child, not its descendants.
    """
    if not isinstance(plan, DiscoveryPlan):
        raise TypeError("discovery requires a DiscoveryPlan")
    root = root.resolve(strict=True)
    directories = {suite.name: _directory(root, suite.cwd) for suite in plan.suites}
    inventories = []
    for suite in plan.suites:
        with tempfile.TemporaryDirectory(prefix="criterion-discovery-") as directory:
            overlay = (*((key, value) for key, value in suite.environment if key.casefold() != "criterion_home"), ("CRITERION_HOME", directory))
            environment = _overlay_environment(os.environ if env is None else env, overlay)
            result = run_command(suite.command[0], suite.command[1:], cwd=directories[suite.name], env=environment, timeout=suite.timeout)
            result.check_returncode()
            inventories.append((suite.name, parse_benchmark_list(result.stdout)))
    inventory = CriterionInventory(tuple(inventories), plan.excluded)
    missing = set(plan.required) - set(inventory.expected)
    if missing:
        raise ValueError(f"discovered inventory omits required IDs: {sorted(missing)}")
    return inventory


def serialize_inventory(inventory: CriterionInventory) -> bytes:
    return deterministic_json({"schema": INVENTORY_SCHEMA, "suites": dict(inventory.suites), "excluded": inventory.excluded})


def parse_inventory(payload: bytes) -> CriterionInventory:
    raw = _object(_load_json(payload, "Criterion inventory"), "Criterion inventory", {"schema", "suites", "excluded"})
    if raw["schema"] != INVENTORY_SCHEMA:
        raise ValueError("unsupported Criterion inventory schema")
    return CriterionInventory(
        tuple((name, _strings(ids, "suite IDs")) for name, ids in _object(raw["suites"], "suites").items()),
        _strings(raw["excluded"], "excluded", nonempty=False),
    )
