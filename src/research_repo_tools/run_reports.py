"""Immutable complete-run history and validated latest selection."""

import re
import tomllib
from pathlib import Path

from research_repo_tools.complete_runs import render_run, run_identity, validate_run_evidence
from research_repo_tools.evidence import Evidence, _load_json, _object, _string, deterministic_json, parse_evidence, serialize_evidence
from research_repo_tools.publication import PublicationPlan, _path, _publication_name, plan_outputs
from research_repo_tools.publication_config import _table
from research_repo_tools.release_pairs import ReleasePair

__all__ = ["load_latest_run", "load_run_report_plan"]

_INDEX = "research-repo-tools/run-index/v1"
_LATEST = "research-repo-tools/latest-run/v1"


def _entry(evidence: Evidence) -> dict[str, str]:
    return {"id": run_identity(evidence), **{name: dict(source.context)["release"] for name, source in evidence.sources}}


def _parse_entry(value: object) -> dict[str, str]:
    raw = _object(value, "run entry", {"id", "baseline", "current"})
    entry = {name: _string(value, name) for name, value in raw.items()}
    if re.fullmatch(r"[0-9a-f]{64}", entry["id"]) is None:
        raise ValueError("invalid run identity")
    ReleasePair(entry["current"], entry["baseline"])
    return entry


def _targets(archive: str, identity: str) -> tuple[str, str, str]:
    stem = f"{archive}/runs/{identity}"
    return f"{stem}/run.json", f"{stem}/evidence.json", f"{stem}/report.md"


def _stored_runs(root: Path) -> set[str]:
    if not root.exists():
        return set()
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        raise ValueError("run history must be a regular directory")
    result = set()
    for path in root.iterdir():
        if path.is_symlink() or path.is_junction() or not path.is_dir():
            raise ValueError("run history entries must be regular directories")
        children = {child.name for child in path.iterdir()}
        # Empty directories may remain after a successful file rollback.
        if not children:
            continue
        if children != {"run.json", "evidence.json", "report.md"}:
            raise ValueError("partial retained run directory")
        result.add(path.name)
    return result


def _history(root: Path, archive: str) -> tuple[dict[str, dict[str, str]], dict[str, bytes], dict[str, str] | None]:
    _publication_name(archive)
    inputs: dict[str, bytes] = {}
    entries = {}
    index_name, latest_name = f"{archive}/index.json", f"{archive}/latest.json"
    index_path, latest_path = _path(root, index_name), _path(root, latest_name)
    run_root = root / archive / "runs"
    stored = _stored_runs(run_root)
    if index_path.exists() != latest_path.exists():
        raise ValueError("partial retained history: index and latest pointer are required together")
    if not index_path.exists():
        if stored:
            raise ValueError("partial retained history without an index")
        return entries, inputs, None
    inputs[index_name] = index_path.read_bytes()
    raw = _object(_load_json(inputs[index_name], "run index"), "run index", {"schema", "runs"})
    if raw["schema"] != _INDEX or not isinstance(raw["runs"], list) or not raw["runs"]:
        raise ValueError("invalid run index")
    for value in raw["runs"]:
        entry = _parse_entry(value)
        identity = entry["id"]
        if identity in entries:
            raise ValueError("duplicate run index entry")
        targets = _targets(archive, identity)
        for name in targets:
            inputs[name] = _path(root, name).read_bytes()
        evidence = parse_evidence(inputs[targets[0]], inputs[targets[1]])
        if _entry(evidence) != entry or inputs[targets[2]] != render_run(evidence):
            raise ValueError("retained run identity, labels or report do not match the index")
        entries[identity] = entry
    if stored != set(entries):
        raise ValueError("retained run directories do not match the complete index")
    inputs[latest_name] = latest_path.read_bytes()
    pointer = _object(_load_json(inputs[latest_name], "latest run"), "latest run", {"schema", "run"})
    if pointer["schema"] != _LATEST:
        raise ValueError("unsupported latest-run schema")
    latest = _parse_entry(pointer["run"])
    if entries.get(latest["id"]) != latest:
        raise ValueError("latest pointer does not identify an indexed complete run")
    return entries, inputs, latest


def load_latest_run(root: Path, archive: str) -> Evidence:
    """Verify the complete index, run bytes and pointer without recapturing a host."""
    _, inputs, latest = _history(root.resolve(strict=True), archive)
    if latest is None:
        raise ValueError("no retained latest run")
    payload, manifest, _ = _targets(archive, latest["id"])
    return parse_evidence(inputs[payload], inputs[manifest])


def load_run_report_plan(root: Path, configuration: str, *, payload: str | None = None, manifest: str | None = None) -> PublicationPlan:
    """Plan schema-2 promotion or offline rerender as one recoverable transaction.

    Explicit input paths never fall back to retained history, even when both are
    missing. Omit both paths to use the validated latest pointer after scratch cleanup.
    """
    root = root.resolve(strict=True)
    config_bytes = _path(root, configuration).read_bytes()
    raw = _table(tomllib.loads(config_bytes.decode("utf-8")), "complete report", {"schema", "current", "archive", "title"}, set())
    if type(raw["schema"]) is not int or raw["schema"] != 2:
        raise ValueError("complete report schema must be integer 2")
    current, archive, title = (_string(raw[key], key) for key in ("current", "archive", "title"))
    _publication_name(archive)
    _path(root, current)
    if (payload is None) != (manifest is None):
        raise ValueError("promotion requires both payload and manifest")
    mutable = (current, f"{archive}/index.json", f"{archive}/latest.json", f"{archive}/README.md")
    from research_repo_tools.files import _validate_distinct_paths

    _validate_distinct_paths(
        tuple(_path(root, name) for name in (*mutable, configuration, *([payload, manifest] if payload is not None and manifest is not None else [])))
    )
    entries, history, latest = _history(root, archive)
    inputs = {configuration: config_bytes, **history}
    if payload is None:
        if latest is None:
            raise ValueError("no retained latest run to rerender")
        payload, manifest, _ = _targets(archive, latest["id"])
    assert manifest is not None
    inputs[payload], inputs[manifest] = _path(root, payload).read_bytes(), _path(root, manifest).read_bytes()
    evidence = parse_evidence(inputs[payload], inputs[manifest])
    validate_run_evidence(evidence)
    entry = _entry(evidence)
    targets = _targets(archive, entry["id"])
    if current in {*targets, f"{archive}/index.json", f"{archive}/latest.json", f"{archive}/README.md"} or current.startswith(f"{archive}/runs/"):
        raise ValueError("current report must be outside immutable history and index paths")
    entries[entry["id"]] = entry
    data, envelope = serialize_evidence(evidence)
    outputs = dict(zip(targets, (data, envelope, render_run(evidence)), strict=True))
    outputs[current] = render_run(evidence, title=title)
    outputs[f"{archive}/index.json"] = deterministic_json({"schema": _INDEX, "runs": [entries[key] for key in sorted(entries)]})
    outputs[f"{archive}/latest.json"] = deterministic_json({"schema": _LATEST, "run": entry})
    outputs[f"{archive}/README.md"] = (
        "# Complete performance runs\n\n"
        + "\n".join(f"- [{item['current']} vs {item['baseline']} — {key}](runs/{key}/report.md)" for key, item in sorted(entries.items()))
        + "\n"
    ).encode("utf-8")
    # Mutable pointer/index observations are checked against the planner's
    # snapshot; immutable evidence remains a validated input and output.
    observed = {}
    for name in (current, f"{archive}/index.json", f"{archive}/latest.json", f"{archive}/README.md"):
        path = _path(root, name)
        observed[name] = history.get(name, path.read_bytes() if path.exists() else None)
        inputs.pop(name, None)
    plan = plan_outputs(root, outputs, inputs=inputs, immutable=targets)
    if any(dict(plan.originals)[name] != prior for name, prior in observed.items()):
        raise ValueError("retained selection changed during promotion planning")
    return plan
