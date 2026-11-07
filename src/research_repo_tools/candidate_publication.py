"""Exact shared-publisher outputs, independently retained before release merge.

A receipt is an integrity record, not authentication. Its digest must travel
through the acquisition owner's separate trusted handoff, never the final tree.
"""

import json
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from research_repo_tools.evidence import _digest, _load_json, _object, _string, deterministic_json, parse_evidence, sha256
from research_repo_tools.publication import PublicationPlan, _path, _verify_plan
from research_repo_tools.run_reports import _targets, load_run_report_plan
from research_repo_tools.source_exports import _names
lazy from research_repo_tools.candidate_reuse import CandidatePolicy

__all__ = ["plan_candidate_publication"]

_SCHEMA = "research-repo-tools/candidate-publication/v1"


@dataclass(frozen=True, slots=True)
class _PublicationReceipt:
    """Parsed, policy-bound receipt; raw mappings never enter source binding."""

    archive: str
    outputs: tuple[tuple[str, str], ...]
    payload: bytes

    def document(self) -> dict[str, Any]:
        return _object(_load_json(self.payload, "publication receipt"), "publication receipt")


def _roles(archive: str, current: str, identity: str) -> dict[str, str]:
    paths = (*_targets(archive, identity), current, f"{archive}/index.json", f"{archive}/latest.json", f"{archive}/README.md")
    _names(None, paths)
    if PurePosixPath(current).suffix.casefold() != ".md" or current.startswith(f"{archive}/runs/"):
        raise ValueError("generated current report must be Markdown outside immutable run history")
    return dict(zip(paths, ("payload", "envelope", "report", "current", "index", "latest", "history"), strict=True))


def _parse_publication(payload: bytes, policy: CandidatePolicy) -> _PublicationReceipt:
    from research_repo_tools.candidate_reuse import acquisition_assertion_digest

    if policy.report is None:
        raise ValueError("publication receipt requires a declared candidate report")
    raw = _object(
        _load_json(payload, "publication receipt"),
        "publication receipt",
        {"schema", "acquisition_sha256", "identity", "publisher", "configuration", "archive", "current", "prior", "outputs"},
    )
    if deterministic_json(raw) != payload:
        raise ValueError("publication receipt must retain original canonical publisher bytes")
    if raw["schema"] != _SCHEMA or raw["acquisition_sha256"] != acquisition_assertion_digest(policy) or raw["identity"] != policy.identity:
        raise ValueError("publication receipt differs from independent acquisition")
    publisher = _object(raw["publisher"], "publisher", {"name", "version"})
    if publisher["name"] != "research-repo-tools/run-report/v2":
        raise ValueError("unsupported generated-evidence publisher")
    _string(publisher["version"], "publisher version")
    if not isinstance(raw["configuration"], str):
        raise ValueError("original report configuration must be UTF-8 text")
    configuration = raw["configuration"].encode("utf-8")
    original = {name: (digest, bool(mode & 0o111)) for name, digest, mode in json.loads(dict(policy.candidate.context)["source-export"])["files"]}
    if original.get(policy.report) != (sha256(configuration), False):
        raise ValueError("publisher configuration differs from original acquisition tree")
    declaration = tomllib.loads(configuration.decode("utf-8"))
    if type(declaration.get("schema")) is not int or declaration["schema"] != 2:
        raise ValueError("committed evidence requires the schema-2 complete-run publisher")
    archive, current = (_string(raw[key], key) for key in ("archive", "current"))
    if (archive, current) != (declaration.get("archive"), declaration.get("current")):
        raise ValueError("generated output paths differ from original publisher declaration")
    roles = _roles(archive, current, policy.identity)
    outputs = _object(raw["outputs"], "generated outputs", set(roles))
    _names(None, tuple(sorted(set(original) | set(outputs))))
    inputs = set()
    for _, _, phase, source in policy.observations:
        keys = ("harness-inventory", "workload-inventory")
        if phase == "current":
            keys += ("source-inventory", "original-source-inventory")
        for key in keys:
            if key in dict(source.context):
                inputs.update(json.loads(dict(source.context)[key]))
    inputs.add(policy.report)

    def canonical(name: str) -> str:
        return unicodedata.normalize("NFC", name).casefold()

    patterns = (*policy.measurement.sources, *policy.measurement.harness, *policy.measurement.measurement.workloads)
    canonical_inputs = {canonical(path) for path in inputs}
    digests = {}
    for name, value in outputs.items():
        item = _object(value, "generated output", {"role", "sha256", "size", "executable"})
        if item["role"] != roles[name] or item["executable"] is not False or type(item["size"]) is not int or item["size"] < 0:
            raise ValueError("invalid generated output role, size or mode")
        if canonical(name) in canonical_inputs or any(PurePosixPath(canonical(name)).full_match(canonical(p)) for p in patterns):
            raise ValueError(f"generated output overlaps acquisition inputs: {name}")
        if original.get(name, (None, False))[1]:
            raise ValueError("generated output was an executable acquisition input")
        digests[name] = _digest(item["sha256"], "generated output digest")
    prior = _object(raw["prior"], "original publication inputs")
    _names(None, tuple(prior))
    for name, digest in prior.items():
        if digest is not None:
            _digest(digest, "original publication input")
        if (original.get(name) or (None, False))[0] != digest:
            raise ValueError(f"publication input differs from acquisition tree: {name}")
    history = {name for name in original if name.startswith(f"{archive}/")}
    if not (history | set(outputs) | {policy.report}) <= prior.keys():
        raise ValueError("publication receipt omits original history or output states")
    return _PublicationReceipt(archive, tuple(sorted(digests.items())), payload)


def plan_candidate_publication(root: Path, payload: str, manifest: str, policy: CandidatePolicy, *, acquisition_sha256: str) -> tuple[PublicationPlan, bytes]:
    """Plan the declared shared report and freeze its exact outputs before writes.

    Call under trusted acquisition-owner control after acquisition, before report
    promotion. Save the returned receipt and its SHA-256 independently. Publish
    the returned plan with publish_publication; ordinary stale-input checks apply.
    Raw evidence must be the original retained pair, not a regenerated envelope.
    """
    from research_repo_tools import __version__
    from research_repo_tools.candidate_reuse import _eligible, acquisition_assertion_digest

    if acquisition_assertion_digest(policy) != _digest(acquisition_sha256, "trusted acquisition digest"):
        raise ValueError("candidate assertions differ from trusted Step 1 handoff")
    if policy.report is None:
        raise ValueError("committed evidence requires a declared candidate report")
    root = root.resolve(strict=True)
    evidence = parse_evidence(_path(root, payload).read_bytes(), _path(root, manifest).read_bytes())
    _eligible(evidence, policy)
    plan = load_run_report_plan(root, policy.report, payload=payload, manifest=manifest)
    originals = dict(plan.originals)
    retained_payload, retained_manifest = originals[payload], originals[manifest]
    assert retained_payload is not None and retained_manifest is not None
    _eligible(parse_evidence(retained_payload, retained_manifest), policy)
    configuration = originals[policy.report]
    assert configuration is not None
    declaration = tomllib.loads(configuration.decode("utf-8"))
    roles = _roles(declaration["archive"], declaration["current"], policy.identity)
    if set(dict(plan.outputs)) != set(roles):
        raise ValueError("publisher produced unsupported generated output paths")
    # Only the freshly acquired scratch pair may be absent from the captured
    # tree. Every other input and prior output must match its original bytes.
    if payload in roles or manifest in roles:
        raise ValueError("capture publication before promoting the original scratch pair")
    receipt = {
        "schema": _SCHEMA,
        "acquisition_sha256": acquisition_sha256,
        "identity": policy.identity,
        "publisher": {"name": "research-repo-tools/run-report/v2", "version": __version__},
        "configuration": configuration.decode("utf-8"),
        "archive": declaration["archive"],
        "current": declaration["current"],
        "prior": {name: sha256(data) if data is not None else None for name, data in originals.items() if name not in (payload, manifest)},
        "outputs": {name: {"role": roles[name], "sha256": sha256(data), "size": len(data), "executable": False} for name, data in plan.outputs},
    }
    parsed = _parse_publication(deterministic_json(receipt), policy)
    _verify_plan(plan)
    return plan, parsed.payload
