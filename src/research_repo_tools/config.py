"""Parse checkout-independent TOML into immutable, typed settings."""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, TypeIs

FIELDS = {
    "notebooks": {"group", "cwd", "output-dir", "timeout", "outputs"},
    "toolchain": {"cargo"},
    "deps": {"pyproject", "justfile", "tools", "uv"},
    "semgrep": {"config", "fixtures", "namespace", "timeout", "cwd", "counts"},
    "release": {"date-policy", "final-changelog"},
    "changelog": {"formatter", "cliff-config", "owner", "repository"},
}


@dataclass(frozen=True, slots=True)
class NotebookSettings:
    group: str = "notebook"
    cwd: str = "."
    output_dir: str = "target/notebooks"
    timeout: int = 600
    outputs: Literal["clear", "preserve"] = "clear"


@dataclass(frozen=True, slots=True)
class ToolchainSettings:
    cargo: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cargo", MappingProxyType(dict(self.cargo)))


@dataclass(frozen=True, slots=True)
class DependencySettings:
    pyproject: str = "pyproject.toml"
    justfile: str = "justfile"
    tools: Mapping[str, str] = field(default_factory=dict)
    uv: str = "uv"

    def __post_init__(self) -> None:
        object.__setattr__(self, "tools", MappingProxyType(dict(self.tools)))


@dataclass(frozen=True, slots=True)
class SemgrepSettings:
    config: str | None = None
    fixtures: str | None = None
    namespace: str = ""
    timeout: int = 300
    cwd: str = "."
    counts: Mapping[Path, Mapping[str, int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "counts", MappingProxyType({path: MappingProxyType(dict(counts)) for path, counts in self.counts.items()}))


@dataclass(frozen=True, slots=True)
class ReleasePolicy:
    date_policy: Literal["today", "declared"] = "today"
    final_changelog: bool = False


@dataclass(frozen=True, slots=True)
class ChangelogSettings:
    formatter: str | None = None
    cliff_config: str | None = None
    owner: str | None = None
    repository: str | None = None


@dataclass(frozen=True, slots=True)
class Config:
    root: Path
    toolchain: ToolchainSettings = field(default_factory=ToolchainSettings)
    deps: DependencySettings = field(default_factory=DependencySettings)
    semgrep: SemgrepSettings = field(default_factory=SemgrepSettings)
    release: ReleasePolicy = field(default_factory=ReleasePolicy)
    changelog: ChangelogSettings = field(default_factory=ChangelogSettings)
    notebooks: NotebookSettings = field(default_factory=NotebookSettings)

    def path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    def executable(self, value: str) -> str:
        """Resolve explicit executable paths at the consumer root; keep PATH names."""
        return str(self.path(value)) if "/" in value or "\\" in value else value


def _is_table(value: object) -> TypeIs[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _table(value: object, context: str) -> dict[str, object]:
    if not _is_table(value):
        raise ValueError(f"{context} must be a table")
    return value


def _section(data: dict[str, object], name: str) -> dict[str, object]:
    value = _table(data.get(name, {}), name)
    if value.keys() - FIELDS[name]:
        raise ValueError(f"invalid or unknown fields in {name} configuration")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid value for {context}: {value!r}; expected a nonempty string")
    return value


def _optional_string(section: dict[str, object], key: str, context: str) -> str | None:
    return _string(section[key], f"{context}.{key}") if key in section else None


def _strings(value: object, context: str) -> dict[str, str]:
    return {_string(key, context): _string(item, f"{context}.{key}") for key, item in _table(value, context).items()}


def _counts(value: object, root: Path) -> dict[Path, Mapping[str, int]]:
    result: dict[Path, Mapping[str, int]] = {}
    for raw_path, raw_counts in _table(value, "semgrep.counts").items():
        path = (root / _string(raw_path, "semgrep.counts path")).resolve()
        if path in result:
            raise ValueError(f"duplicate semgrep.counts fixture path: {raw_path!r} resolves to {path}")
        counts = _table(raw_counts, f"semgrep.counts.{raw_path}")
        if not counts:
            raise ValueError(f"semgrep.counts.{raw_path} must contain rule counts")
        parsed: dict[str, int] = {}
        for rule, count in counts.items():
            rule = _string(rule, "semgrep.counts rule")
            if type(count) is not int or count < 0:
                raise ValueError(f"semgrep.counts.{raw_path}.{rule} must be a nonnegative integer")
            parsed[rule] = count
        result[path] = parsed
    return result


def parse(value: object, *, root: Path) -> Config:
    """Reject invalid fields and ambiguous paths before publishing trusted settings."""
    data = _table(value, "research-repo-tools configuration")
    unknown = data.keys() - {"schema", *FIELDS}
    if unknown:
        raise ValueError(f"unknown configuration keys: {', '.join(sorted(unknown))}")
    if type(data.get("schema", 1)) is not int or data.get("schema", 1) != 1:
        raise ValueError("configuration schema must be integer 1")
    root = root.resolve()
    toolchain = _section(data, "toolchain")
    deps = _section(data, "deps")
    semgrep = _section(data, "semgrep")
    release = _section(data, "release")
    changelog = _section(data, "changelog")
    notebooks = _section(data, "notebooks")
    notebook_timeout = notebooks.get("timeout", 600)
    if type(notebook_timeout) is not int or notebook_timeout <= 0:
        raise ValueError("notebooks.timeout must be a positive integer")
    notebook_outputs = notebooks.get("outputs", "clear")
    if notebook_outputs not in ("clear", "preserve"):
        raise ValueError("notebooks.outputs must be clear or preserve")
    timeout = semgrep.get("timeout", 300)
    if type(timeout) is not int or timeout <= 0:
        raise ValueError("semgrep.timeout must be a positive integer")
    raw_policy = release.get("date-policy", "today")
    if raw_policy == "today":
        date_policy: Literal["today", "declared"] = "today"
    elif raw_policy == "declared":
        date_policy = "declared"
    else:
        raise ValueError("release.date-policy must be today or declared")
    final = release.get("final-changelog", False)
    if type(final) is not bool:
        raise ValueError("release.final-changelog must be a boolean")
    return Config(
        root=root,
        toolchain=ToolchainSettings(_strings(toolchain.get("cargo", {}), "toolchain.cargo")),
        deps=DependencySettings(
            pyproject=_string(deps.get("pyproject", "pyproject.toml"), "deps.pyproject"),
            justfile=_string(deps.get("justfile", "justfile"), "deps.justfile"),
            tools=_strings(deps.get("tools", {}), "deps.tools"),
            uv=_string(deps.get("uv", "uv"), "deps.uv"),
        ),
        semgrep=SemgrepSettings(
            config=_optional_string(semgrep, "config", "semgrep"),
            fixtures=_optional_string(semgrep, "fixtures", "semgrep"),
            namespace=_optional_string(semgrep, "namespace", "semgrep") or "",
            timeout=timeout,
            cwd=_string(semgrep.get("cwd", "."), "semgrep.cwd"),
            counts=_counts(semgrep.get("counts", {}), root),
        ),
        release=ReleasePolicy(date_policy, final),
        changelog=ChangelogSettings(
            formatter=_optional_string(changelog, "formatter", "changelog"),
            cliff_config=_optional_string(changelog, "cliff-config", "changelog"),
            owner=_optional_string(changelog, "owner", "changelog"),
            repository=_optional_string(changelog, "repository", "changelog"),
        ),
        notebooks=NotebookSettings(
            group=_string(notebooks.get("group", "notebook"), "notebooks.group"),
            cwd=_string(notebooks.get("cwd", "."), "notebooks.cwd"),
            output_dir=_string(notebooks.get("output-dir", "target/notebooks"), "notebooks.output-dir"),
            timeout=notebook_timeout,
            outputs="preserve" if notebook_outputs == "preserve" else "clear",
        ),
    )


def load(path: Path | None = None, root: Path | None = None) -> Config:
    filename = path or (root or Path.cwd()) / "pyproject.toml"
    data: object = {}
    if filename.is_file():
        document = tomllib.loads(filename.read_text(encoding="utf-8"))
        data = _table(document.get("tool", {}), f"{filename}: tool").get("research-repo-tools", {}) if filename.name == "pyproject.toml" else document
    elif path is not None:
        raise ValueError(f"configuration not found: {path}")
    return parse(data, root=root or filename.parent)
