"""Consumer-owned Actions update selectors and offline consistency identities."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from research_repo_tools.workflow_uses import identity

TAG = re.compile(r"v?(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){0,2}")


@dataclass(frozen=True, slots=True)
class ActionPolicy:
    targets: dict[str, str]
    compatibility: dict[str, tuple[str, str]]
    consistency: tuple[str, ...]


def load_policy(path: Path) -> ActionPolicy:
    try:
        data = tomllib.loads(path.read_bytes().decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise ValueError(f"{path}: invalid Actions policy: {error}") from error
    if set(data) - {"actions", "compatibility", "consistency"} or not isinstance(data.get("actions"), dict):
        raise ValueError(f"{path}: expected [actions] and optional [compatibility]/[consistency] tables")
    targets: dict[str, str] = {}
    for action, tag in data["actions"].items():
        key = identity(action)
        if key in targets or not isinstance(tag, str) or tag != "latest" and TAG.fullmatch(tag) is None:
            raise ValueError(f"{path}: duplicate identity or unsupported target for {action}; use latest or a stable version tag")
        targets[key] = tag
    compatibility = data.get("compatibility", {})
    if not isinstance(compatibility, dict):
        raise ValueError(f"{path}: compatibility must be a table")
    checks: dict[str, tuple[str, str]] = {}
    for action, value in compatibility.items():
        key = identity(action)
        if (
            key in checks
            or not isinstance(value, dict)
            or set(value) != {"path", "tool"}
            or not isinstance(value["path"], str)
            or not value["path"]
            or any(part in {"", ".", ".."} for part in value["path"].split("/"))
            or "\\" in value["path"]
            or not isinstance(value["tool"], str)
            or re.fullmatch(r"[A-Za-z0-9_-]+", value["tool"]) is None
        ):
            raise ValueError(f"{path}: compatibility.{action} requires a relative upstream path and tool name")
        checks[key] = value["path"], value["tool"]
    consistency = data.get("consistency", {})
    if not isinstance(consistency, dict) or set(consistency) - {"actions"}:
        raise ValueError(f"{path}: consistency requires only an actions list")
    selected = consistency.get("actions", [])
    if not isinstance(selected, list) or any(not isinstance(action, str) for action in selected):
        raise ValueError(f"{path}: consistency.actions must be a list of exact identities")
    try:
        identities = tuple(identity(action) for action in selected)
    except ValueError as error:
        raise ValueError(f"{path}: consistency.actions: {error}") from error
    if len(set(identities)) != len(identities):
        raise ValueError(f"{path}: consistency.actions contains duplicate identities")
    return ActionPolicy(targets, checks, identities)
