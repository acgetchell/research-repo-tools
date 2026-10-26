"""Check external action references against consumer-owned selected-actions JSON."""

import json
from dataclasses import dataclass
from pathlib import Path

from research_repo_tools.workflow_uses import SHA, identity, references, workflow_paths


@dataclass(frozen=True, slots=True)
class ActionRule:
    action: str
    ref: str

    @classmethod
    def parse(cls, pattern: str) -> ActionRule:
        action, separator, ref = pattern.partition("@")
        if not separator or ref != "*" and SHA.fullmatch(ref) is None:
            raise ValueError(f"expected exact owner/repo[/path]@* or owner/repo[/path]@<40-hex-SHA> entry: {pattern!r}")
        return cls(identity(action), ref.lower())

    def allows(self, ref: str) -> bool:
        return self.ref == "*" or self.ref == ref.lower()


def load_policy(path: Path) -> frozenset[ActionRule]:
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
    if not isinstance(patterns, list) or any(not isinstance(pattern, str) for pattern in patterns):
        raise ValueError(f"{path}: patterns_allowed must be a list of exact identity/ref strings")
    try:
        return frozenset(ActionRule.parse(pattern) for pattern in patterns)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error


def check(root: Path, policy: Path, inputs: list[str]) -> int:
    allowed = load_policy(policy)
    diagnostics = []
    for path in workflow_paths(root, inputs):
        for use in references(path, path.read_bytes().decode("utf-8-sig")):
            if use.action is None or use.ref is None:
                continue
            rules = [rule for rule in allowed if rule.action == use.action]
            if not any(rule.allows(use.ref) for rule in rules):
                constraint = ", ".join(f"{rule.action}@{rule.ref}" for rule in sorted(rules, key=lambda rule: rule.ref))
                reason = f"permitted references: {constraint}" if rules else f"no approved identity {use.action!r}"
                diagnostics.append(f"{use.location}: unapproved reference {use.node.value!r}; {reason} in {policy}")
    if diagnostics:
        raise ValueError("\n".join(diagnostics))
    print(f"External Actions allowlist passed using {policy}; local actions and containers are outside this policy.")
    return 0
