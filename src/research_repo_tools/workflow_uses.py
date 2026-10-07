"""Read workflow references from YAML structure, retaining scalar source spans."""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

IDENTITY = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*")
SHA = re.compile(r"[a-fA-F0-9]{40}")


def identity(value: str) -> str:
    if IDENTITY.fullmatch(value) is None or any(part in {".", ".."} for part in value.split("/")):
        raise ValueError(f"expected exact owner/repo[/path] identity: {value!r}")
    owner, repo, *path = value.split("/")
    return "/".join([owner.lower(), repo.lower(), *path])


@dataclass(frozen=True, slots=True)
class Use:
    path: Path
    node: ScalarNode
    context: str
    action: str | None
    ref: str | None
    line: int
    column: int
    start: int
    end: int

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}:{self.column} ({self.context})"


def _mapping(node: Node, path: Path) -> dict[str, Node]:
    if not isinstance(node, MappingNode):
        raise ValueError(f"{path}:{node.start_mark.line + 1}: expected a YAML mapping")
    result: dict[str, Node] = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.value == "<<" or key.value in result:
            raise ValueError(f"{path}:{key.start_mark.line + 1}: duplicate, merge, or complex YAML mapping key")
        result[key.value] = value
    return result


def _validate(node: Node, path: Path, active: set[int], visited: set[int]) -> None:
    if id(node) in active:
        raise ValueError(f"{path}:{node.start_mark.line + 1}: recursive YAML aliases are unsupported")
    if id(node) in visited:
        return
    active.add(id(node))
    if isinstance(node, MappingNode):
        children = list(_mapping(node, path).values())
    elif isinstance(node, SequenceNode):
        children = node.value
    else:
        children = []
    for child in children:
        _validate(child, path, active, visited)
    active.remove(id(node))
    visited.add(id(node))


def references(path: Path, text: str) -> tuple[Use, ...]:
    """Reject ambiguous YAML; inspect job calls and every named/unnamed step.

    Aliases retain the anchor's source location. This is reference inspection,
    not an actionlint replacement. Local actions and containers are out of scope.
    """
    try:
        document = yaml.compose(text, Loader=yaml.SafeLoader)
        if document is not None:
            _validate(document, path, set(), set())
    except RecursionError as error:
        raise ValueError(f"{path}: workflow YAML nesting exceeds supported depth") from error
    except yaml.YAMLError as error:
        raise ValueError(f"{path}: invalid workflow YAML: {error}") from error
    if document is None:
        raise ValueError(f"{path}: empty workflow")
    top = _mapping(document, path)
    if "jobs" not in top:
        raise ValueError(f"{path}: workflow requires jobs")
    uses: list[Use] = []

    def collect(node: Node, context: str) -> None:
        if not isinstance(node, ScalarNode) or node.tag != "tag:yaml.org,2002:str":
            raise ValueError(f"{path}:{node.start_mark.line + 1}: uses must be a string")
        if node.start_mark is None or node.end_mark is None:
            raise ValueError(f"{path}: uses scalar has no YAML source location")
        value = node.value.strip()
        if value.startswith(("./", "docker://")):
            action = ref = None
        else:
            raw_action, separator, ref = value.partition("@")
            if not separator or not ref or re.search(r"\s|@|\$", ref):
                raise ValueError(f"{path}:{node.start_mark.line + 1}: unsupported uses reference: {value!r}")
            action = identity(raw_action)
        uses.append(Use(path, node, context, action, ref, node.start_mark.line + 1, node.start_mark.column + 1, node.start_mark.index, node.end_mark.index))

    for name, job in _mapping(top["jobs"], path).items():
        fields = _mapping(job, path)
        if "uses" in fields:
            collect(fields["uses"], f"jobs.{name}.uses")
        if "steps" in fields:
            steps = fields["steps"]
            if not isinstance(steps, SequenceNode):
                raise ValueError(f"{path}:{steps.start_mark.line + 1}: steps must be a sequence")
            for index, step in enumerate(steps.value):
                fields = _mapping(step, path)
                if "uses" in fields:
                    collect(fields["uses"], f"jobs.{name}.steps[{index}].uses")
    return tuple(uses)


def workflow_paths(root: Path, inputs: list[str]) -> tuple[Path, ...]:
    """Expand explicit directories to YAML files, without following symlinks."""
    paths: set[Path] = set()
    for value in inputs:
        path = Path(value)
        path = path if path.is_absolute() else root / path
        if path.is_symlink():
            raise ValueError(f"workflow input must not be a symlink: {path}")
        path = path.resolve()
        if path.is_dir():
            selected = [item for item in path.rglob("*") if item.suffix in {".yml", ".yaml"}]
            if any(item.is_symlink() for item in selected):
                raise ValueError(f"workflow directory contains symlinked YAML: {path}")
            selected = [item for item in selected if item.is_file()]
        elif path.is_file():
            selected = [path]
        else:
            raise ValueError(f"workflow input does not exist: {path}")
        paths.update(selected)
    if not paths:
        raise ValueError("no workflow YAML files selected")
    return tuple(sorted(paths))
