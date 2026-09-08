"""Validated, checkout-independent TOML configuration."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIELDS = {
    "deps": {"pyproject", "justfile", "tools", "uv"},
    "semgrep": {"config", "fixtures", "namespace", "timeout", "cwd", "counts"},
    "release": {"date-policy", "final-changelog"},
    "changelog": {"formatter", "cliff-config", "owner", "repository"},
}
BOOLS = {"final-changelog"}


@dataclass(frozen=True)
class Config:
    root: Path
    sections: dict[str, dict[str, Any]]

    def section(self, name: str) -> dict[str, Any]:
        return dict(self.sections.get(name, {}))

    def path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.root / path


def load(path: Path | None = None, root: Path | None = None) -> Config:
    filename = path or (root or Path.cwd()) / "pyproject.toml"
    data: dict[str, Any] = {}
    if filename.is_file():
        parsed = tomllib.loads(filename.read_text(encoding="utf-8"))
        if filename.name == "pyproject.toml":
            tool = parsed.get("tool", {})
            if not isinstance(tool, dict):
                raise ValueError(f"{filename}: tool must be a table")
            data = tool.get("research-repo-tools", {})
        else:
            data = parsed
    elif path is not None:
        raise ValueError(f"configuration not found: {path}")
    if not isinstance(data, dict):
        raise ValueError("research-repo-tools configuration must be a table")
    unknown = set(data) - {"schema", *FIELDS}
    if unknown:
        raise ValueError(f"unknown configuration keys: {', '.join(sorted(unknown))}")
    if type(data.get("schema", 1)) is not int or data.get("schema", 1) != 1:
        raise ValueError("configuration schema must be integer 1")
    sections: dict[str, dict[str, Any]] = {}
    for name, allowed in FIELDS.items():
        section = data.get(name, {})
        if not isinstance(section, dict) or set(section) - allowed:
            raise ValueError(f"invalid or unknown fields in {name} configuration")
        for key, value in section.items():
            if key in BOOLS:
                valid = type(value) is bool
            elif key == "timeout":
                valid = type(value) is int and value > 0
            elif key == "tools":
                valid = isinstance(value, dict) and all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in value.items())
            elif key == "counts":
                valid = isinstance(value, dict) and all(
                    isinstance(path, str)
                    and path
                    and isinstance(counts, dict)
                    and counts
                    and all(isinstance(rule, str) and rule and type(count) is int and count >= 0 for rule, count in counts.items())
                    for path, counts in value.items()
                )
            else:
                valid = isinstance(value, str) and bool(value)
            if not valid:
                raise ValueError(f"invalid value for {name}.{key}: {value!r}")
        if name == "release" and section.get("date-policy", "today") not in {"today", "declared"}:
            raise ValueError("release.date-policy must be today or declared")
        sections[name] = section
    return Config((root or filename.parent).resolve(), sections)
