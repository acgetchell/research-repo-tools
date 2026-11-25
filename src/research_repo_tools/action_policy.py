"""Consumer-owned Actions update selectors and offline consistency identities."""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from research_repo_tools.workflow_uses import SHA, identity

TAG = re.compile(r"v?(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){0,2}")


@dataclass(frozen=True, slots=True)
class ActionPolicy:
    """Upstream Action targets, wrapper compatibility checks, and identities whose pins must agree."""

    targets: dict[str, str]
    compatibility: dict[str, tuple[str, str]]
    consistency: tuple[str, ...]
    reviewed_commits: dict[str, str] = field(default_factory=dict)


def load_policy(path: Path) -> ActionPolicy:
    """Read accepted Action targets, compatibility constraints, and the list of pins to check."""
    try:
        data = tomllib.loads(path.read_bytes().decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise ValueError(f"{path}: invalid Actions policy: {error}") from error
    if set(data) - {"actions", "compatibility", "consistency"} or not isinstance(data.get("actions"), dict):
        raise ValueError(f"{path}: expected [actions] and optional [compatibility]/[consistency] tables")
    targets: dict[str, str] = {}
    for action, tag in data["actions"].items():
        try:
            key = identity(action)
        except ValueError as error:
            raise ValueError(f"{path}: actions.{action}: {error}") from error
        if key in targets or not isinstance(tag, str) or tag != "latest" and TAG.fullmatch(tag) is None:
            raise ValueError(f"{path}: duplicate identity or unsupported target for {action}; use latest or a stable version tag")
        targets[key] = tag
    compatibility = data.get("compatibility", {})
    if not isinstance(compatibility, dict):
        raise ValueError(f"{path}: compatibility must be a table")
    checks: dict[str, tuple[str, str]] = {}
    for action, value in compatibility.items():
        try:
            key = identity(action)
        except ValueError as error:
            raise ValueError(f"{path}: compatibility.{action}: {error}") from error
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
    if not isinstance(consistency, dict) or set(consistency) - {"actions", "releases"}:
        raise ValueError(f"{path}: consistency accepts actions and reviewed releases")
    selected = consistency.get("actions", [])
    if not isinstance(selected, list) or any(not isinstance(action, str) for action in selected):
        raise ValueError(f"{path}: consistency.actions must be a list of exact identities")
    try:
        identities = tuple(identity(action) for action in selected)
    except ValueError as error:
        raise ValueError(f"{path}: consistency.actions: {error}") from error
    if len(set(identities)) != len(identities):
        raise ValueError(f"{path}: consistency.actions contains duplicate identities")
    return ActionPolicy(targets, checks, identities, _reviewed_commits(path, consistency.get("releases", []), targets, identities))


def _reviewed_commits(path: Path, releases: object, targets: dict[str, str], identities: tuple[str, ...]) -> dict[str, str]:
    """Bind each selected subpath to its repository's independently reviewed release."""
    if not isinstance(releases, list):
        raise ValueError(f"{path}: consistency.releases requires an array of tables")
    reviewed: dict[str, str] = {}
    for release in releases:
        if not isinstance(release, dict) or set(release) != {"actions", "tag", "commit"}:
            raise ValueError(f"{path}: reviewed release requires actions, tag and commit")
        actions, tag, commit = release["actions"], release["tag"], release["commit"]
        if not isinstance(tag, str) or not TAG.fullmatch(tag) or not isinstance(commit, str) or not SHA.fullmatch(commit):
            raise ValueError(f"{path}: reviewed release requires an explicit stable tag and full commit SHA")
        if not isinstance(actions, list) or not actions or not all(isinstance(action, str) for action in actions):
            raise ValueError(f"{path}: reviewed release requires a nonempty action list")
        members = [identity(action) for action in actions]
        if len({"/".join(member.split("/")[:2]) for member in members}) != 1:
            raise ValueError(f"{path}: each reviewed release belongs to one repository")
        for member in members:
            if member in reviewed or member not in identities or targets.get(member) != tag:
                raise ValueError(f"{path}: {member} must select its reviewed tag exactly once in the consistency policy")
            reviewed[member] = commit.lower()
    return reviewed
