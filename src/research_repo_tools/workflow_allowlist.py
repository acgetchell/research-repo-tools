"""Check external action references against consumer-owned selected-actions JSON."""

import json
from pathlib import Path

from research_repo_tools.workflow_uses import identity, references, workflow_paths


def load_policy(path: Path) -> frozenset[str]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{path}: duplicate policy field {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=unique)
    except (ValueError, RecursionError) as error:
        raise ValueError(f"{path}: invalid selected-actions JSON: {error}") from error
    if not isinstance(value, dict) or set(value) != {"github_owned_allowed", "verified_allowed", "patterns_allowed"}:
        raise ValueError(f"{path}: expected github_owned_allowed, verified_allowed, and patterns_allowed only")
    if value["github_owned_allowed"] is not False or value["verified_allowed"] is not False:
        raise ValueError(f"{path}: github_owned_allowed and verified_allowed must both be false")
    patterns = value["patterns_allowed"]
    if not isinstance(patterns, list) or any(not isinstance(pattern, str) or not pattern.endswith("@*") for pattern in patterns):
        raise ValueError(f"{path}: patterns_allowed must contain exact owner/repo[/path]@* entries")
    return frozenset(identity(pattern[:-2]) for pattern in patterns)


def check(root: Path, policy: Path, inputs: list[str]) -> int:
    allowed = load_policy(policy)
    diagnostics = []
    for path in workflow_paths(root, inputs):
        for use in references(path, path.read_bytes().decode("utf-8-sig")):
            if use.action is not None and use.action not in allowed:
                diagnostics.append(f"{use.location}: unapproved reference {use.node.value!r}; add {use.action}@* to {policy} after review")
    if diagnostics:
        raise ValueError("\n".join(diagnostics))
    print(f"External Actions allowlist passed using {policy}; local actions and containers are outside this policy.")
    return 0
