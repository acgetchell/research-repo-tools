"""Shared representation and extraction policy for Semgrep scans and fixtures."""

from pathlib import PurePosixPath
from typing import Any


def representation_rules(rule: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate rule metadata and return unchanged source or scoped Rust rules.

    Without metadata, retain source checks plus extracted checks for Rust rules.
    A block path override belongs only to the extracted representation.
    """
    label = f"{rule['id']}: metadata.research-repo-tools"
    metadata = rule.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError(f"{rule['id']}: metadata must be a mapping")
    policy = metadata.get("research-repo-tools", {})
    if not isinstance(policy, dict) or policy.keys() - {"representations", "rust-block-paths"}:
        raise ValueError(f"{label} has unknown or invalid fields")
    rust = "rust" in rule.get("languages", [])
    selected = policy.get("representations", ["source", "rust-block"] if rust else ["source"])
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(item, str) or item not in {"source", "rust-block"} for item in selected)
        or len(selected) != len(set(selected))
    ):
        raise ValueError(f"{label}.representations requires distinct source/rust-block values")
    if "rust-block" in selected and not rust:
        raise ValueError(f"{label}: rust-block requires a Rust rule")
    paths = policy.get("rust-block-paths")
    if "rust-block-paths" in policy:
        if "rust-block" not in selected or not isinstance(paths, dict) or not paths or paths.keys() - {"include", "exclude"}:
            raise ValueError(f"{label}.rust-block-paths requires Rust-block include/exclude scopes")
        for key, patterns in paths.items():
            if not isinstance(patterns, list) or not patterns or any(not isinstance(item, str) or not item or "\0" in item for item in patterns):
                raise ValueError(f"{label}.rust-block-paths.{key} requires nonempty path patterns")
    result = {}
    if "source" in selected:
        result["source"] = rule
    if "rust-block" in selected:
        result["rust-block"] = {**rule, "languages": ["rust"], **({"paths": paths} if paths is not None else {})}
    return result


def extraction_selected(name: str, include: tuple[str, ...], exclude: tuple[str, ...]) -> bool:
    """Match root-relative, case-sensitive POSIX globs independently of Git scope."""
    path = PurePosixPath(name)
    return any(path.full_match(pattern) for pattern in include) and not any(path.full_match(pattern) for pattern in exclude)
