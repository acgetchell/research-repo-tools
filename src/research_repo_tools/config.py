"""Parse checkout-independent TOML into immutable, typed settings."""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, TypeIs, cast

from research_repo_tools.release_policy import ReleasePolicy as ReleasePolicy
from research_repo_tools.release_policy import ReleaseRule

__all__ = ["load", "parse"]

FIELDS = {
    "notebooks": {"advice", "group", "cwd", "output-dir", "timeout", "outputs", "prohibit-installs"},
    "toolchain": {"binaries", "cargo", "inherit-python"},
    "deps": {"pyproject", "justfile", "tools", "uv"},
    "semgrep": {"config", "fixtures", "namespace", "timeout", "cwd", "counts"},
    "release": {"date-policy", "final-changelog", "required-files", "exclude", "rules", "tag-policy"},
    "changelog": {"formatter", "cliff-config", "owner", "repository"},
    "zizmor": {"persona", "timeout"},
}


@dataclass(frozen=True, slots=True)
class NotebookAdvice:
    descriptive_ids: bool = True
    ruff_rules: tuple[str, ...] = ()
    strict: bool = False
    subprocess_timeout: bool = False


@dataclass(frozen=True, slots=True)
class NotebookSettings:
    group: str = "notebook"
    cwd: str = "."
    output_dir: str = "target/notebooks"
    timeout: int = 600
    outputs: Literal["clear", "preserve"] = "clear"
    advice: NotebookAdvice = field(default_factory=NotebookAdvice)
    prohibit_installs: bool = False


@dataclass(frozen=True, slots=True)
class ToolchainSettings:
    cargo: Mapping[str, str] = field(default_factory=dict)
    inherit_python: bool = False
    binaries: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cargo", MappingProxyType(dict(self.cargo)))
        object.__setattr__(self, "binaries", MappingProxyType(dict(self.binaries)))


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
class ChangelogSettings:
    formatter: str | None = None
    cliff_config: str | None = None
    owner: str | None = None
    repository: str | None = None


@dataclass(frozen=True, slots=True)
class ZizmorSettings:
    persona: Literal["regular", "pedantic", "auditor"] | None = None
    timeout: int = 300


@dataclass(frozen=True, slots=True)
class Config:
    root: Path
    toolchain: ToolchainSettings = field(default_factory=ToolchainSettings)
    deps: DependencySettings = field(default_factory=DependencySettings)
    semgrep: SemgrepSettings = field(default_factory=SemgrepSettings)
    release: ReleasePolicy = field(default_factory=ReleasePolicy)
    changelog: ChangelogSettings = field(default_factory=ChangelogSettings)
    notebooks: NotebookSettings = field(default_factory=NotebookSettings)
    zizmor: ZizmorSettings = field(default_factory=ZizmorSettings)

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


def _release_paths(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array of paths")
    return tuple(_string(item, context) for item in value)


def _release_rules(value: object) -> tuple[ReleaseRule, ...]:
    if not isinstance(value, list):
        raise ValueError("release.rules must be an array of tables")
    rules = []
    for item in value:
        rule = _table(item, "release rule")
        if rule.keys() - {"path", "pattern", "value", "source", "count", "exclude"}:
            raise ValueError("unknown release rule field")
        source = rule.get("source")
        if source not in (None, "version", "tag", "previous-tag", "release-date"):
            raise ValueError("invalid release rule source")
        count = rule.get("count", 1)
        if type(count) is not int:
            raise ValueError("release rule count must be a positive integer")
        rules.append(
            ReleaseRule(
                path=_string(rule.get("path"), "release rule path"),
                pattern=_string(rule.get("pattern"), "release rule pattern"),
                value=_optional_string(rule, "value", "release rule"),
                source=cast(Literal["version", "tag", "previous-tag", "release-date"] | None, source),
                count=count,
                exclude=_optional_string(rule, "exclude", "release rule"),
            )
        )
    return tuple(rules)


def _notebook_advice(value: object) -> NotebookAdvice:
    section = _table(value, "notebooks.advice")
    if section.keys() - {"descriptive-ids", "ruff-rules", "strict", "subprocess-timeout"}:
        raise ValueError("unknown notebooks.advice field")
    for key in ("descriptive-ids", "strict", "subprocess-timeout"):
        if key in section and type(section[key]) is not bool:
            raise ValueError(f"notebooks.advice.{key} must be a boolean")
    rules = section.get("ruff-rules", [])
    if not isinstance(rules, list) or any(not isinstance(rule, str) or re.fullmatch(r"[A-Z]+[0-9]*", rule) is None for rule in rules):
        raise ValueError("notebooks.advice.ruff-rules must be an array of Ruff rule codes or prefixes")
    if len(set(rules)) != len(rules):
        raise ValueError("notebooks.advice.ruff-rules must be distinct")
    return NotebookAdvice(
        descriptive_ids=section.get("descriptive-ids", True) is True,
        ruff_rules=tuple(rules),
        strict=section.get("strict", False) is True,
        subprocess_timeout=section.get("subprocess-timeout", False) is True,
    )


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
    if type(toolchain.get("inherit-python", False)) is not bool:
        raise ValueError("toolchain.inherit-python must be a boolean")
    deps = _section(data, "deps")
    semgrep = _section(data, "semgrep")
    release = _section(data, "release")
    changelog = _section(data, "changelog")
    notebooks = _section(data, "notebooks")
    zizmor = _section(data, "zizmor")
    persona = zizmor.get("persona")
    if "persona" in zizmor and persona not in ("regular", "pedantic", "auditor"):
        raise ValueError("zizmor.persona must be regular, pedantic, or auditor")
    zizmor_timeout = zizmor.get("timeout", 300)
    if type(zizmor_timeout) is not int or zizmor_timeout <= 0:
        raise ValueError("zizmor.timeout must be a positive integer")
    notebook_timeout = notebooks.get("timeout", 600)
    if type(notebook_timeout) is not int or notebook_timeout <= 0:
        raise ValueError("notebooks.timeout must be a positive integer")
    notebook_outputs = notebooks.get("outputs", "clear")
    if notebook_outputs not in ("clear", "preserve"):
        raise ValueError("notebooks.outputs must be clear or preserve")
    prohibit_installs = notebooks.get("prohibit-installs", False)
    if type(prohibit_installs) is not bool:
        raise ValueError("notebooks.prohibit-installs must be a boolean")
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
        zizmor=ZizmorSettings(cast(Literal["regular", "pedantic", "auditor"] | None, persona), zizmor_timeout),
        toolchain=ToolchainSettings(
            _strings(toolchain.get("cargo", {}), "toolchain.cargo"),
            inherit_python=toolchain.get("inherit-python", False) is True,
            binaries=_strings(toolchain.get("binaries", {}), "toolchain.binaries"),
        ),
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
        release=ReleasePolicy(
            date_policy,
            final,
            required_files=_release_paths(release.get("required-files", []), "release.required-files"),
            exclude=_release_paths(release.get("exclude", []), "release.exclude"),
            rules=_release_rules(release.get("rules", [])),
            tag_policy=cast(Literal["normalized-stable", "canonical-stable"], release.get("tag-policy", "normalized-stable")),
        ),
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
            advice=_notebook_advice(notebooks.get("advice", {})),
            prohibit_installs=prohibit_installs,
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
