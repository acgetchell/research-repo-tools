"""Resolve explicit Actions targets and publish narrowly scoped YAML edits."""

import base64
import json
import re
import tomllib
from pathlib import Path
from urllib.parse import quote

import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from research_repo_tools import config
from research_repo_tools.dependencies import _group_requirements
from research_repo_tools.files import replace_many
from research_repo_tools.process import run_safe_command
from research_repo_tools.tool_pins import STABLE
from research_repo_tools.workflow_uses import SHA, identity, references, workflow_paths

TAG = re.compile(r"v?(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){0,2}")


def _api(endpoint: str, root: Path) -> object:
    output = run_safe_command("gh", ["api", endpoint], cwd=root, timeout=30).stdout
    try:
        return json.loads(output)
    except (ValueError, RecursionError) as error:
        raise ValueError(f"GitHub API returned invalid JSON for {endpoint}") from error


def resolve(action: str, target: str, root: Path) -> tuple[str, str]:
    """Resolve a stable release or explicit version tag, peeling annotated tags."""
    repository = "/".join(action.split("/")[:2])
    if target == "latest":
        release = _api(f"repos/{repository}/releases/latest", root)
        if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
            raise ValueError(f"{action}: expected a published stable release")
        released_tag = release.get("tag_name")
        if not isinstance(released_tag, str) or TAG.fullmatch(released_tag) is None:
            raise ValueError(f"{action}: latest release must have a stable version tag")
        target = released_tag
    data = _api(f"repos/{repository}/git/ref/tags/{quote(target, safe='')}", root)
    if not isinstance(data, dict) or data.get("ref") != f"refs/tags/{target}":
        raise ValueError(f"{action}: tag resolution did not return the exact requested tag {target}")
    obj = data.get("object")
    seen: set[str] = set()
    for _ in range(8):
        if not isinstance(obj, dict) or not isinstance(sha := obj.get("sha"), str) or SHA.fullmatch(sha) is None:
            raise ValueError(f"{action}: tag must resolve to a full commit SHA")
        if obj.get("type") == "commit":
            return sha.lower(), target
        if obj.get("type") != "tag" or sha in seen:
            raise ValueError(f"{action}: unsupported or recursive tag object")
        seen.add(sha)
        data = _api(f"repos/{repository}/git/tags/{sha}", root)
        obj = data.get("object") if isinstance(data, dict) else None
    raise ValueError(f"{action}: annotated tag depth exceeds limit")


def _policy(path: Path) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    data = tomllib.loads(path.read_bytes().decode("utf-8"))
    if set(data) - {"actions", "compatibility"} or not isinstance(data.get("actions"), dict):
        raise ValueError(f"{path}: expected [actions] and optional [compatibility] tables")
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
    return targets, checks


def _tool_version(settings: config.Config, name: str) -> str:
    versions: set[str] = set()
    for table in (settings.toolchain.cargo, settings.toolchain.binaries):
        if name in table:
            versions.add(table[name])
    document = tomllib.loads(settings.path(settings.deps.pyproject).read_bytes().decode("utf-8"))
    groups = document.get("dependency-groups", {})
    if not isinstance(groups, dict):
        raise ValueError("dependency-groups must be a table")
    for group in groups:
        for entry in _group_requirements(groups, group):
            requirement = Requirement(entry)
            if canonicalize_name(requirement.name) == canonicalize_name(name):
                specifiers = list(requirement.specifier)
                if len(specifiers) != 1 or specifiers[0].operator != "==" or STABLE.fullmatch(specifiers[0].version) is None:
                    raise ValueError(f"wrapper compatibility requires an exact stable pin for {name}")
                versions.add(specifiers[0].version)
    for pin, tool in settings.deps.tools.items():
        if tool == name:
            text = settings.path(settings.deps.justfile).read_bytes().decode("utf-8")
            matches = re.findall(rf'(?m)^{re.escape(pin)}[ \t]*:=[ \t]*"([^"\r\n]+)"[ \t]*(?:#[^\r\n]*)?\r?$', text)
            if len(matches) != 1:
                raise ValueError(f"wrapper compatibility requires exactly one {pin} assignment")
            versions.update(matches)
    if len(versions) != 1 or any(STABLE.fullmatch(version) is None for version in versions):
        raise ValueError(f"wrapper compatibility requires one authoritative version for {name}; found {sorted(versions)}")
    return versions.pop()


def _compatible(action: str, sha: str, path: str, version: str, root: Path) -> None:
    repository = "/".join(action.split("/")[:2])
    data = _api(f"repos/{repository}/contents/{quote(path, safe='/')}?ref={sha}", root)
    if not isinstance(data, dict) or data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
        raise ValueError(f"{action}: expected a base64 support inventory at {path}")
    try:
        content = base64.b64decode(data["content"].replace("\n", ""), validate=True).decode("utf-8")
    except (ValueError, UnicodeError) as error:
        raise ValueError(f"{action}: invalid support inventory") from error
    supported: set[str] = set()
    for line in content.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key = line.split()[0]
        if key != "latest" and STABLE.fullmatch(key) is None or key in supported:
            raise ValueError(f"{action}: support inventory requires unique stable versions in the first column (or latest)")
        supported.add(key)
    if version not in supported:
        raise ValueError(f"{action}@{sha}: upstream {path} does not support tool version {version}; update the wrapper or use the shared scanner directly")


def update(settings: config.Config, policy: Path, inputs: list[str], *, dry_run: bool = False, check: bool = False) -> int:
    targets, checks = _policy(policy)
    paths = workflow_paths(settings.root, inputs)
    originals = {path: path.read_bytes() for path in paths}
    texts = {path: payload.decode("utf-8") for path, payload in originals.items()}
    inventories = {path: references(path, text) for path, text in texts.items()}
    present = {use.action for uses in inventories.values() for use in uses if use.action is not None}
    if missing := (targets.keys() | checks.keys()) - present:
        raise ValueError(f"selected Actions are absent from workflow scope: {', '.join(sorted(missing))}")
    if "zizmorcore/zizmor-action" in present and "zizmorcore/zizmor-action" not in checks:
        raise ValueError("retained zizmor-action requires a compatibility inventory; prefer the shared direct scanner (see #43)")
    tool_versions = {action: _tool_version(settings, tool) for action, (_, tool) in sorted(checks.items())}
    resolved = {action: resolve(action, target, settings.root) for action, target in sorted(targets.items())}
    replacements: dict[Path, bytes] = {}
    report: list[str] = []
    verified: set[tuple[str, str]] = set()
    for path, uses in inventories.items():
        text = texts[path]
        edits: dict[tuple[int, int], str] = {}
        has_aliases = any(isinstance(event, yaml.events.AliasEvent) for event in yaml.parse(text)) if any(use.action in targets for use in uses) else False
        for use in uses:
            if use.action is None or use.action not in targets and use.action not in checks:
                report.append(f"Skipped {use.location}: outside selected external Actions scope")
                continue
            if use.ref is None or SHA.fullmatch(use.ref) is None:
                raise ValueError(f"{use.location}: selected references must already use full commit SHAs")
            sha, tag = resolved.get(use.action, (use.ref, ""))
            if use.action in checks and (use.action, sha) not in verified:
                support, _ = checks[use.action]
                _compatible(use.action, sha, support, tool_versions[use.action], settings.root)
                verified.add((use.action, sha))
            if use.action not in targets:
                report.append(f"Unchanged {use.location}: retained wrapper supports the declared tool")
                continue
            start, end = use.start, use.end
            scalar = text[start:end]
            # An aliased scalar may also be used outside uses. Reject all aliases
            # for update inputs, but keep them supported for read-only allowlists.
            if has_aliases or "\n" in scalar or "\r" in scalar:
                raise ValueError(f"{use.location}: updater requires single-line, unaliased uses scalars")
            if scalar not in {use.node.value, f"'{use.node.value}'", f'"{use.node.value}"'}:
                raise ValueError(f"{use.location}: updater requires an ordinary scalar without anchors, tags, or escapes")
            line_end = text.find("\n", end)
            line_end = len(text) if line_end < 0 else line_end
            suffix = text[end:line_end]
            comment = re.match(r"([ \t]*#[ \t]*)(v?\d+(?:\.\d+){0,2})(?=[ \t\r]|$)", suffix)
            if comment is None:
                raise ValueError(f"{use.location}: selected uses requires a version comment such as '# v1.2.3'")
            old_comment = comment[2]
            ref_start = scalar.rfind("@") + 1
            updated = scalar[:ref_start] + sha + scalar[ref_start + len(use.ref) :]
            edits[start, end] = updated
            edits[end + comment.start(2), end + comment.end(2)] = tag
            changed = sha != use.ref or tag != old_comment
            report.append(f"{'Update' if changed else 'Unchanged'} {use.location}: {use.action} {use.ref} (# {old_comment}) -> {sha} (# {tag})")
        candidate = text
        for (start, end), replacement in sorted(edits.items(), reverse=True):
            candidate = candidate[:start] + replacement + candidate[end:]
        references(path, candidate)
        if candidate != text:
            replacements[path] = candidate.encode("utf-8")
    if replacements and not dry_run and not check:
        if any(path.read_bytes() != original for path, original in originals.items()):
            raise ValueError("workflow changed during Actions resolution; reload and retry")
        replace_many(replacements, expected=originals)
    for line in report:
        print(line)
    preview = dry_run or check
    print(f"{'Preview' if preview else 'Applied'}: {len(replacements)} workflow files {'would change' if preview else 'changed'}; no Git operations.")
    return 1 if check and replacements else 0
