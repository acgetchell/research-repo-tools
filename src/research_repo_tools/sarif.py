"""Strict, consumer-selected SARIF runs and complete output generations."""

import copy
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from research_repo_tools.evidence import _load_json, _object, deterministic_json
from research_repo_tools.files import publish_directory

__all__ = ["SarifOutput", "SarifPolicy", "split", "transform"]


@dataclass(frozen=True, slots=True)
class SarifPolicy:
    """Exact driver names mapped to rule-ID prefixes; empty prefixes keep all."""

    drivers: Mapping[str, tuple[str, ...]]
    category_prefix: str = "analysis"

    def __post_init__(self) -> None:
        if not isinstance(self.drivers, Mapping) or not self.drivers:
            raise ValueError("sarif.drivers must select at least one exact driver name")
        selected = {}
        for name, prefixes in self.drivers.items():
            if not isinstance(name, str) or not name.strip() or any(char in name for char in "\0\r\n"):
                raise ValueError("sarif driver names must be nonempty strings without NUL/CR/LF")
            if not isinstance(prefixes, (tuple, list)) or any(not isinstance(prefix, str) or not prefix for prefix in prefixes):
                raise ValueError("sarif driver namespaces must be arrays of nonempty rule-ID prefixes")
            selected[name] = tuple(prefixes)
        if not isinstance(self.category_prefix, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.category_prefix) is None:
            raise ValueError("sarif.category-prefix must be a filename-safe identifier")
        object.__setattr__(self, "drivers", MappingProxyType(selected))


@dataclass(frozen=True, slots=True)
class SarifOutput:
    filename: str
    category: str
    payload: bytes


def _objects(value: object, context: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    return [_object(item, context) for item in value]


def _name(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a nonempty string")
    return value


def _index(value: object, length: int, context: str) -> int:
    if type(value) is not int or not 0 <= value < length:
        raise ValueError(f"{context} is not a valid rule index")
    return value


def _rule_id(result: dict[str, object], rules: list[dict[str, object]]) -> str:
    """Resolve legacy and reportingDescriptorReference forms consistently."""
    identities = []
    for reference, id_key, index_key in ((result, "ruleId", "ruleIndex"), (_object(result.get("rule", {}), "result.rule"), "id", "index")):
        if "toolComponent" in reference:
            # Extension tables need their own indexing contract; never silently
            # rewrite an extension index as an index into the driver table.
            raise ValueError("SARIF extension rule references are unsupported")
        if id_key in reference:
            identities.append(_name(reference[id_key], f"result.{id_key}"))
        if index_key in reference:
            identities.append(_name(rules[_index(reference[index_key], len(rules), f"result.{index_key}")]["id"], "rule.id"))
    if not identities or len(set(identities)) != 1:
        raise ValueError("SARIF result has missing or inconsistent rule identity")
    return identities[0]


def _run(value: object) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    run = _object(value, "SARIF run")
    tool = _object(run.get("tool"), "SARIF tool")
    driver = _object(tool.get("driver"), "SARIF driver")
    _name(driver.get("name"), "SARIF driver.name")
    rules = _objects(driver.get("rules", []), "SARIF rules")
    ids = [_name(rule.get("id"), "SARIF rule.id") for rule in rules]
    if len(set(ids)) != len(ids):
        raise ValueError("SARIF rules contain duplicate IDs")
    results = _objects(run.get("results", []), "SARIF results")
    for result in results:
        message = _object(result.get("message"), "SARIF result.message")
        if not any(isinstance(message.get(key), str) and message[key] for key in ("text", "markdown", "id")):
            raise ValueError("SARIF result.message must contain text, markdown or id")
        _rule_id(result, rules)
    if "automationDetails" in run:
        _object(run["automationDetails"], "SARIF automationDetails")
    for invocation in _objects(run.get("invocations", []), "SARIF invocations"):
        if type(invocation.get("executionSuccessful")) is not bool or not invocation["executionSuccessful"]:
            raise ValueError("SARIF invocation must report executionSuccessful=true")
    identity_indices = {index: index for index in range(len(rules))}
    _metadata_references(copy.deepcopy(run), identity_indices)
    _rule_relationships(copy.deepcopy(rules), identity_indices)
    return run, driver, rules, results


def _reindex_reference(reference: dict[str, object], indices: dict[int, int], key: str) -> None:
    if key in reference:
        old = reference[key]
        if type(old) is not int or old not in indices:
            raise ValueError("SARIF metadata references a removed or invalid rule index")
        reference[key] = indices[old]


def _metadata_references(value: object, indices: dict[int, int]) -> None:
    """Reindex standard notification rule references without touching properties."""
    if isinstance(value, list):
        for item in value:
            _metadata_references(item, indices)
    elif isinstance(value, dict):
        if "associatedRule" in value:
            reference = _object(value["associatedRule"], "associatedRule")
            if "toolComponent" in reference:
                raise ValueError("SARIF extension rule references are unsupported")
            _reindex_reference(reference, indices, "index")
            value["associatedRule"] = reference
        for key, item in value.items():
            if key not in {"properties", "associatedRule", "results", "tool"}:
                _metadata_references(item, indices)


def _rule_relationships(rules: list[dict[str, object]], indices: dict[int, int]) -> None:
    for rule in rules:
        if "relationships" not in rule:
            continue
        relationships = _objects(rule["relationships"], "rule.relationships")
        for relationship in relationships:
            target = _object(relationship.get("target"), "relationship.target")
            if "toolComponent" in target:
                raise ValueError("SARIF extension rule references are unsupported")
            _reindex_reference(target, indices, "index")
            relationship["target"] = target
        rule["relationships"] = relationships


def transform(document: object, policy: SarifPolicy) -> tuple[SarifOutput, ...]:
    """Validate all input, select drivers/namespaces and serialize split runs.

    Version 2.1.0, finite JSON, driver rule tables and consistent result rule
    references are required. Rule IDs without a descriptor table are supported;
    extension rule references are rejected. Preserve unrelated root/run metadata
    and automation fields. Retain namespace-selected rules even without findings;
    skip runs with neither rules nor results. Categories include a driver-name
    digest and its input occurrence, so slug collisions and empty runs cannot
    reuse another run's category. No caller data or filesystem state is mutated.
    """
    # Also validates direct Python inputs (including cycles and non-finite data).
    raw = _object(_load_json(deterministic_json(document), "SARIF"), "SARIF root")
    if raw.get("version") != "2.1.0":
        raise ValueError("SARIF version must be 2.1.0")
    runs = [_run(run) for run in _objects(raw.get("runs"), "SARIF runs")]
    seen: dict[str, int] = {}
    outputs = []
    for run, driver, rules, results in runs:
        name = _name(driver["name"], "driver.name")
        seen[name] = seen.get(name, 0) + 1
        if name not in policy.drivers:
            continue
        prefixes = policy.drivers[name]

        def selected(identity: str) -> bool:
            return not prefixes or identity.startswith(prefixes)

        retained = [index for index, rule in enumerate(rules) if selected(_name(rule["id"], "rule.id"))]
        indices = {old: new for new, old in enumerate(retained)}
        findings = [copy.deepcopy(result) for result in results if selected(_rule_id(result, rules))]
        for result in findings:
            _reindex_reference(result, indices, "ruleIndex")
            if "rule" in result:
                reference = _object(result["rule"], "result.rule")
                _reindex_reference(reference, indices, "index")
                result["rule"] = reference
        if not retained and not findings:
            continue
        tool = _object(run["tool"], "tool")
        retained_rules = [rules[index] for index in retained]
        _rule_relationships(retained_rules, indices)
        driver["rules"] = retained_rules
        tool["driver"] = driver
        run["tool"], run["results"] = tool, findings
        _metadata_references(run, indices)
        slug = re.sub(r"[^a-z0-9_.-]+", "-", name.lower()).strip("-.") or "driver"
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
        category = f"{policy.category_prefix}-{slug[:80]}-{digest}-{seen[name]}"
        automation = _object(run.get("automationDetails", {}), "automationDetails")
        automation["id"] = category
        run["automationDetails"] = automation
        rendered = {key: value for key, value in raw.items() if key != "runs"}
        rendered["runs"] = [run]
        outputs.append(SarifOutput(f"{category}.sarif", category, deterministic_json(rendered)))
    return tuple(outputs)


def split(source: Path, destination: Path, policy: SarifPolicy) -> tuple[SarifOutput, ...]:
    """Parse and render before publishing a complete owned directory generation."""
    outputs = transform(_load_json(source.read_bytes(), "SARIF"), policy)
    with publish_directory(destination) as candidate:
        for output in outputs:
            (candidate / output.filename).write_bytes(output.payload)
    return outputs
