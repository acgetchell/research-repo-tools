"""Strict native Semgrep scans over the shared portable file inventory."""

import copy
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any
from urllib.parse import quote, unquote

import yaml

from research_repo_tools import config, security, semgrep
from research_repo_tools.evidence import _load_json, _object, deterministic_json
from research_repo_tools.files import _path_keys, publish_directory
from research_repo_tools.process import resolve_executable
from research_repo_tools.sarif import _metadata_references, _objects, _reindex_reference, _rule_id, _rule_relationships, _run
from research_repo_tools.scanner_output import FindingOutput
from research_repo_tools.security import security_inventory
from research_repo_tools.selection import argument_batches
from research_repo_tools.semgrep_docs import RustBlock, documentation_blocks
from research_repo_tools.semgrep_findings import parse_results

__all__ = ["check_documentation_fixtures", "scan"]


def check_documentation_fixtures(settings: config.Config) -> int:
    """Check native/prose and Rust-block assertions through one fixture checker."""
    if settings.semgrep.counts:
        raise ValueError("documentation fixtures use source annotations; keep count-based fixtures in a separate fixture gate")
    return semgrep.check(settings, rust_docs=True)


@dataclass(frozen=True, slots=True)
class ScanInput:
    physical: Path
    source: Path
    logical: str
    block: RustBlock | None = None

    @property
    def label(self) -> str:
        if self.block is None:
            return self.logical
        return f"{self.logical} (Rust block {self.block.index + 1}, lines {self.block.start_line}-{self.block.end_line})"

    def coverage(self) -> dict[str, object]:
        result: dict[str, object] = {"path": self.logical, "representation": "source" if self.block is None else "rust-block"}
        if self.block is not None:
            result.update(block=self.block.index + 1, start_line=self.block.start_line, end_line=self.block.end_line)
        return result


def _scan_groups(root: Path, names: tuple[str, ...], temporary: Path, configuration: Path, rust_docs: bool):
    """Give each block its source-relative name, without rewriting path filters."""
    originals = [ScanInput(root / name, root / name, name) for name in names]
    groups = [(root, configuration, originals, False)]
    layers: dict[int, list[ScanInput]] = {}
    if rust_docs:
        for original in originals:
            if original.source.suffix not in {".md", ".rs"}:
                continue
            for block in documentation_blocks(original.source):
                scan_root = temporary / "blocks" / str(block.index)
                physical = scan_root / original.logical
                physical.parent.mkdir(parents=True, exist_ok=True)
                physical.write_text(block.code, encoding="utf-8", newline="\n")
                layers.setdefault(block.index, []).append(ScanInput(physical, original.source, original.logical, block))
    if layers:
        selected = [{**rule, "languages": ["rust"]} for rule in semgrep.rules(configuration).values() if "rust" in rule.get("languages", [])]
        if not selected:
            raise ValueError("Rust documentation blocks require Rust rules; include their logical source paths in the rule scopes")
        rust_configuration = temporary / "rust-rules.yaml"
        rust_configuration.write_text(yaml.safe_dump({"rules": selected}, sort_keys=False), encoding="utf-8", newline="\n")
        groups.extend((temporary / "blocks" / str(index), rust_configuration, inputs, True) for index, inputs in sorted(layers.items()))
    return groups


def _locations(value, mapping):
    if isinstance(value, dict):
        return {key: _locations(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_locations(item, mapping) for item in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def _file_uri(path: PurePath) -> str:
    if isinstance(path, PureWindowsPath):
        # PureWindowsPath also models cross-drive native reports on POSIX hosts.
        prefix = "file:" if path.drive.startswith("\\\\") else "file:///"
        return prefix + quote(path.as_posix(), safe="/:")
    return Path(path).as_uri()


def _source_mapping(snippet: PurePath, source: PurePath) -> dict[str, str]:
    """Preserve native JSON paths and forward-slash/encoded SARIF URIs."""
    mapping = {
        str(snippet): str(source),
        snippet.as_posix(): source.as_posix(),
        quote(snippet.as_posix(), safe="/"): quote(source.as_posix(), safe="/"),
    }
    if snippet.is_absolute():
        mapping[_file_uri(snippet)] = _file_uri(source)
    return mapping


def _report_path(value: str, root: Path, *, uri: bool = False) -> Path:
    if uri and value.startswith("file:"):
        return Path.from_uri(value).resolve()
    return (root / (unquote(value) if uri else value)).resolve()


def _coverage_description(paths: set[Path], inputs: dict[Path, ScanInput]) -> str:
    labels = sorted(inputs[path].label if path in inputs else str(path) for path in paths)
    shown = ", ".join(repr(label[:240]) for label in labels[:8])
    return shown + (f", ... ({len(labels)} total)" if len(labels) > 8 else "")


def _project_positions(data, sarif, inputs, root):
    """Restore source columns/byte offsets before comparing overlapping spans."""
    source_lines = {}
    for result in data["results"]:
        item = inputs[_report_path(result["path"], root)]
        if item.block is None:
            continue
        if item.source not in source_lines:
            source_lines[item.source] = item.source.read_bytes().splitlines(keepends=True)
        lines = source_lines[item.source]
        positions = [result["start"], result["end"]]
        for value in _object(result.get("extra", {}).get("metavars", {}), "Semgrep metavariables").values():
            metavariable = _object(value, "Semgrep metavariable")
            positions.extend(metavariable[field] for field in ("start", "end") if field in metavariable)
        for value in positions:
            if not isinstance(value, dict):
                raise ValueError("Semgrep position must be an object")
            position = value
            line = position.get("line")
            if type(line) is not int or line < 1:
                raise ValueError("Semgrep position requires a positive line")
            position["line"] = line + item.block.start_line - 1
            if "col" in position:
                column = position["col"]
                if type(column) is not int or column < 1:
                    raise ValueError("Semgrep position requires a positive column")
                position["col"] = item.block.source_column(line + item.block.start_line - 1, column)
                if "offset" in position:
                    position["offset"] = sum(map(len, lines[: line + item.block.start_line - 2])) + position["col"] - 1
    for run in sarif["runs"]:
        for result in run["results"]:
            physical = result["locations"][0]["physicalLocation"]
            item = inputs[_report_path(physical["artifactLocation"]["uri"], root, uri=True)]
            if item.block is None:
                continue
            region = physical["region"]
            region["startLine"] += item.block.start_line - 1
            if "endLine" in region:
                region["endLine"] += item.block.start_line - 1
            for field in ("start", "end"):
                if field + "Column" in region:
                    region[field + "Column"] = item.block.source_column(region.get(field + "Line", region["startLine"]), region[field + "Column"])


def _deduplicate_overlaps(data, sarif, source_counts, root, *, original):
    """Keep multiplicity within each representation; remove exact cross-input repeats."""

    def keep(key, counts):
        if original:
            counts[key] += 1
        elif counts[key]:
            counts[key] -= 1
            return False
        return True

    retained = []
    for result in data["results"]:
        start, end = result["start"], result["end"]
        key = (
            result["check_id"],
            result["path"],
            start["line"],
            start.get("col"),
            end["line"],
            end.get("col"),
            result.get("extra", {}).get("is_ignored", False),
        )
        if "col" not in start or "col" not in end or keep(key, source_counts[0]):
            retained.append(result)
    data["results"] = retained
    for run in sarif["runs"]:
        retained = []
        for result in run["results"]:
            physical = result["locations"][0]["physicalLocation"]
            region = physical["region"]
            key = (
                _rule_id(result, run["tool"]["driver"].get("rules", [])),
                _report_path(physical["artifactLocation"]["uri"], root, uri=True),
                region["startLine"],
                region.get("startColumn"),
                region.get("endLine", region["startLine"]),
                region.get("endColumn"),
                any(item.get("status", "accepted") == "accepted" for item in result.get("suppressions", [])),
            )
            if "startColumn" not in region or "endColumn" not in region or keep(key, source_counts[1]):
                retained.append(result)
        run["results"] = retained


def _offset_artifact_location(value, offset, length):
    location = _object(value, "SARIF artifact location")
    if "index" in location:
        if type(location["index"]) is not int or not 0 <= location["index"] < length:
            raise ValueError("invalid SARIF artifact index")
        location["index"] += offset
    return location


def _offset_artifacts(value, offset, length):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"artifactLocation", "analysisTarget"}:
                value[key] = _offset_artifact_location(item, offset, length)
            elif key != "properties":
                _offset_artifacts(item, offset, length)
    elif isinstance(value, list):
        for item in value:
            _offset_artifacts(item, offset, length)


def _invocation_index(result: dict[str, object], length: int) -> int:
    provenance = _object(result.get("provenance", {}), "result.provenance")
    index = provenance.get("invocationIndex", -1)
    if type(index) is not int or not -1 <= index < length:
        raise ValueError("invalid SARIF invocation index")
    return index


def _aggregate_sarif(documents: list[dict[str, Any]], category: str) -> dict[str, Any]:
    """Merge native Semgrep runs, retaining tables and rejecting conflicts."""
    merged = {key: value for key, value in documents[0].items() if key != "runs"}
    combined: dict[str, object] | None = None
    base_tool: dict[str, object] = {}
    base_driver: dict[str, object] = {}
    all_rules: list[dict[str, object]] = []
    all_results: list[dict[str, object]] = []
    all_artifacts: list[dict[str, object]] = []
    all_invocations: list[dict[str, object]] = []
    ignored = {"tool", "results", "artifacts", "invocations", "automationDetails"}
    for document in documents:
        if {key: value for key, value in document.items() if key != "runs"} != merged:
            raise ValueError("inconsistent SARIF document metadata across batches")
        for run, driver, rules, results in [_run(item) for item in _objects(document.get("runs"), "SARIF runs")]:
            tool = _object(run["tool"], "tool")
            if combined is None:
                combined = {key: value for key, value in run.items() if key not in ignored}
                base_tool = {key: value for key, value in tool.items() if key != "driver"}
                base_driver = {key: value for key, value in driver.items() if key != "rules"}
            if {key: value for key, value in driver.items() if key != "rules"} != base_driver:
                raise ValueError("inconsistent SARIF driver across batches")
            if {key: value for key, value in run.items() if key not in ignored} != combined:
                raise ValueError("inconsistent SARIF run metadata across batches")
            if {key: value for key, value in tool.items() if key != "driver"} != base_tool:
                raise ValueError("inconsistent SARIF tool metadata across batches")
            indices = {}
            known = {rule["id"]: index for index, rule in enumerate(all_rules)}
            for index, rule in enumerate(rules):
                if rule["id"] not in known:
                    known[rule["id"]] = len(known)
                indices[index] = known[rule["id"]]
            for index, rule in enumerate(rules):
                normalized = copy.deepcopy(rule)
                _rule_relationships([normalized], indices)
                destination = indices[index]
                if destination < len(all_rules):
                    if all_rules[destination] != normalized:
                        raise ValueError("conflicting SARIF descriptors for the same rule")
                else:
                    all_rules.append(normalized)
            artifacts = _objects(run.get("artifacts", []), "artifacts")
            invocations = _objects(run.get("invocations", []), "invocations")
            _offset_artifacts(results, len(all_artifacts), len(artifacts))
            _offset_artifacts(artifacts, len(all_artifacts), len(artifacts))
            _offset_artifacts(invocations, len(all_artifacts), len(artifacts))
            _metadata_references(invocations, indices)
            for invocation in invocations:
                for field in ("executableLocation", "workingDirectory", "stdin", "stdout", "stderr", "stdoutStderr"):
                    if field in invocation:
                        invocation[field] = _offset_artifact_location(invocation[field], len(all_artifacts), len(artifacts))
                if "responseFiles" in invocation:
                    invocation["responseFiles"] = [
                        _offset_artifact_location(item, len(all_artifacts), len(artifacts))
                        for item in _objects(invocation["responseFiles"], "invocation.responseFiles")
                    ]
            for artifact in artifacts:
                if "location" in artifact:
                    artifact["location"] = _offset_artifact_location(artifact["location"], len(all_artifacts), len(artifacts))
                if "parentIndex" in artifact:
                    parent = artifact["parentIndex"]
                    if type(parent) is not int or not 0 <= parent < len(artifacts):
                        raise ValueError("invalid SARIF parent artifact index")
                    artifact["parentIndex"] = parent + len(all_artifacts)
            for result in results:
                _reindex_reference(result, indices, "ruleIndex")
                if "rule" in result:
                    reference = _object(result["rule"], "result.rule")
                    _reindex_reference(reference, indices, "index")
                    result["rule"] = reference
                old = _invocation_index(result, len(invocations))
                # SARIF uses -1 for an unknown invocation; only offset known ones.
                if old >= 0:
                    provenance = _object(result["provenance"], "result.provenance")
                    provenance["invocationIndex"] = old + len(all_invocations)
                    result["provenance"] = provenance
            all_results.extend(results)
            all_artifacts.extend(artifacts)
            all_invocations.extend(invocations)
    if combined is None:
        raise ValueError("Semgrep did not produce a SARIF run")
    combined.update(
        tool={**base_tool, "driver": {**base_driver, "rules": all_rules}},
        results=all_results,
        artifacts=all_artifacts,
        invocations=all_invocations,
        automationDetails={"id": f"{category}/"},
    )
    merged["runs"] = [combined]
    return merged


def _batch_reports(
    json_path: Path, sarif_path: Path, targets: set[Path], root: Path, inline_suppressions: bool, inputs: dict[Path, ScanInput] | None = None
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    values = []
    for path in (json_path, sarif_path):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("missing or oversized native report")
        values.append(_object(_load_json(path.read_bytes(), "Semgrep report"), "Semgrep report"))
    data, sarif = values
    return _validate_pair(data, sarif, targets, root, inline_suppressions, inputs)


def _validate_pair(data, sarif, targets, root, inline_suppressions, inputs=None):
    if isinstance(data.get("errors"), list) and data["errors"]:
        kinds = [str(error.get("type", "unknown"))[:100] if isinstance(error, dict) else "malformed error" for error in data["errors"][:8]]
        raise ValueError(f"native Semgrep errors ({len(data['errors'])}): {', '.join(kinds)}; inputs: {_coverage_description(targets, inputs or {})}")
    parsed = parse_results(json.dumps(data))
    paths = _object(data.get("paths"), "Semgrep paths")
    scanned = paths.get("scanned")
    if not isinstance(data.get("errors"), list) or not isinstance(scanned, list) or any(not isinstance(path, str) for path in scanned):
        raise ValueError("Semgrep report is missing errors or scanned-path inventory")
    observed = {_report_path(path, root) for path in scanned}
    if observed != targets:
        missing = _coverage_description(targets - observed, inputs or {}) or "none"
        unexpected = _coverage_description(observed - targets, inputs or {}) or "none"
        raise ValueError(f"Semgrep skipped a required input or scanned an unselected input; missing: {missing}; unexpected: {unexpected}")
    actual = Counter()
    for finding, raw in zip(parsed.results, _objects(data["results"], "Semgrep results"), strict=True):
        path = (root / finding.path).resolve()
        if path not in targets:
            raise ValueError("Semgrep finding identifies an unselected input")
        ignored = _object(raw.get("extra", {}), "Semgrep extra").get("is_ignored", False)
        if type(ignored) is not bool or (ignored and not inline_suppressions):
            raise ValueError("unexpected Semgrep suppression")
        if not ignored:
            actual[(finding.check_id, path, finding.start_line)] += 1
    if sarif.get("version") != "2.1.0":
        raise ValueError("invalid SARIF version")
    reported = Counter()
    runs = _objects(sarif.get("runs"), "SARIF runs")
    if not runs:
        raise ValueError("missing SARIF runs")
    for native_run, _driver, rules, results in [_run(run) for run in runs]:
        invocations = _objects(native_run.get("invocations", []), "invocations")
        for result in results:
            _invocation_index(result, len(invocations))
            suppressions = _objects(result.get("suppressions", []), "SARIF suppressions")
            suppressed = any(item.get("status", "accepted") == "accepted" for item in suppressions)
            if suppressed and not inline_suppressions:
                raise ValueError("unexpected SARIF suppression")
            locations = _objects(result.get("locations"), "SARIF locations")
            if len(locations) != 1:
                raise ValueError("Semgrep SARIF result must identify one source location")
            physical = _object(locations[0].get("physicalLocation"), "physicalLocation")
            artifact = _object(physical.get("artifactLocation"), "artifactLocation")
            uri = artifact.get("uri")
            line = _object(physical.get("region"), "region").get("startLine")
            if not isinstance(uri, str) or type(line) is not int or line <= 0:
                raise ValueError("invalid Semgrep SARIF location")
            path = _report_path(uri, root, uri=True)
            if path not in targets:
                raise ValueError("SARIF finding identifies an unselected input")
            if not suppressed:
                reported[(_rule_id(result, rules), path, line)] += 1
    if actual != reported:
        raise ValueError("Semgrep JSON and SARIF disagree on active findings")
    return data, sarif, bool(actual)


def scan(
    settings: config.Config,
    *,
    include: tuple[str, ...],
    exclude: tuple[str, ...] = (),
    output: str = "target/security/semgrep",
    rust_docs: bool = False,
    batch_size: int | None = None,
    inline_suppressions: bool | None = None,
    jobs: int | None = None,
    report_category: str | None = None,
    report_layout: str | None = None,
    target_timeout: int | None = None,
) -> int:
    """Scan bounded explicit batches once each, producing paired native reports.

    Overrides use the same validated contract as semgrep configuration. Verify
    exact input coverage and agreement of active JSON/SARIF findings before
    publication. Invalid scans publish an empty generation. Valid findings and
    native nonzero statuses remain blocking. Aggregate layout emits one SARIF
    run/category; numbered layout emits one report pair per bounded batch.
    """
    overrides = {
        key: value
        for key, value in locals().items()
        if key in {"batch_size", "inline_suppressions", "jobs", "report_category", "report_layout", "target_timeout"} and value is not None
    }
    policy = replace(settings.semgrep, **overrides)
    if not include or policy.config is None:
        raise ValueError("Semgrep scan requires explicit include patterns and semgrep.config")
    names = security_inventory(settings.root, include=include, exclude=exclude)
    if not names:
        raise ValueError("no Semgrep inputs selected")
    destination = settings.path(output)
    destination_keys = _path_keys(destination)
    for name in names:
        source = (settings.root / name).resolve()
        if any(not destination_keys.isdisjoint(_path_keys(parent)) for parent in (source, *source.parents)):
            raise ValueError("Semgrep output directory must not contain selected inputs")
    binary = resolve_executable("semgrep", cwd=settings.root)
    with publish_directory(destination) as candidate, tempfile.TemporaryDirectory(prefix="research-semgrep-scan-") as directory:
        temporary = Path(directory)
        groups = _scan_groups(settings.root, names, temporary, settings.path(policy.config), rust_docs)
        env = {
            **os.environ,
            "SEMGREP_SEND_METRICS": "off",
            "OTEL_SDK_DISABLED": "true",
            "SEMGREP_SETTINGS_FILE": str(temporary / "settings.yml"),
            "SEMGREP_VERSION_CACHE_PATH": str(temporary / "version-cache"),
            "SEMGREP_LOG_FILE": str(temporary / "semgrep.log"),
        }
        json_path, sarif_path = temporary / "report.json", temporary / "report.sarif"
        command = [
            str(binary),
            "scan",
            "--config",
            str(settings.path(policy.config)),
            "--metrics",
            "off",
            "--disable-version-check",
            "--strict",
            "--enable-nosem" if policy.inline_suppressions else "--disable-nosem",
            "--no-rewrite-rule-ids",
            "--no-git-ignore",
            "--max-target-bytes",
            "0",
            "--timeout",
            str(policy.target_timeout),
            "--jobs",
            str(policy.jobs),
            "--error",
            "--json",
            "--output",
            str(json_path),
            "--sarif-output",
            str(sarif_path),
        ]
        plans = []
        for scan_root, configuration, inputs, is_rust in groups:
            invocation = command.copy()
            invocation[invocation.index("--config") + 1] = str(configuration)
            # Native rule path filters see the same source-relative names in
            # every block layer. Force only extracted content through Rust's
            # analyzer even when its logical filename ends in .md.
            position = invocation.index("--sarif-output")
            invocation[position:position] = ["--project-root", str(scan_root), *(["--scan-unknown-extensions"] if is_rust else [])]
            inventory = {item.physical.resolve(): item for item in inputs}
            for batch in argument_batches(invocation, [str(item.physical) for item in inputs], batch_size=policy.batch_size):
                selected = {Path(path).resolve(): inventory[Path(path).resolve()] for path in batch[len(invocation) :]}
                plans.append((batch, scan_root, selected))
        reports, code = [], 0
        source_counts = (Counter(), Counter())
        for batch, scan_root, selected in plans:
            json_path.unlink(missing_ok=True)
            sarif_path.unlink(missing_ok=True)
            try:
                result = security.run_command_bytes(batch[0], batch[1:], cwd=scan_root, env=env, input=b"", timeout=policy.timeout, check=False)
            except subprocess.TimeoutExpired:
                print("Semgrep: scan timed out; no report published", file=sys.stderr)
                return 124
            status = result.returncode if result.returncode >= 0 else 128 - result.returncode
            try:
                data, sarif, found = _batch_reports(json_path, sarif_path, set(selected), scan_root, policy.inline_suppressions, selected)
                _project_positions(data, sarif, selected, scan_root)
            except (OSError, ValueError, TypeError, KeyError) as error:
                reason = str(error)[:1600]
                print(f"Semgrep: missing, malformed, or incomplete paired reports (exit {status}): {reason}; no report published", file=sys.stderr)
                return code or status or 1
            code = code or status or int(found)
            mapping = {}
            json_mapping = {}
            for item in selected.values():
                mapping.update(_source_mapping(item.physical, item.source))
                mapping.update(_source_mapping(item.physical.relative_to(scan_root), item.source))
                for physical in (item.physical, item.physical.relative_to(scan_root)):
                    json_mapping.update({str(physical): str(item.source), physical.as_posix(): str(item.source)})
            data = _locations(data, json_mapping)
            sarif = _locations(sarif, mapping)
            try:
                _deduplicate_overlaps(data, sarif, source_counts, settings.root, original=all(item.block is None for item in selected.values()))
                _validate_pair(data, sarif, {item.source.resolve() for item in selected.values()}, settings.root, policy.inline_suppressions)
            except (OSError, ValueError, TypeError, KeyError) as error:
                print(f"Semgrep: inconsistent source-mapped reports: {str(error)[:1600]}; no report published", file=sys.stderr)
                return code or 1
            data["input_coverage"] = [item.coverage() for item in selected.values()]
            reports.append((data, sarif, found))
        payloads = {}
        if policy.report_layout == "aggregate":
            data = {
                "results": [result for data, _, _ in reports for result in data["results"]],
                "errors": [],
                "paths": {"scanned": sorted({path for data, _, _ in reports for path in data["paths"]["scanned"]})},
                "input_coverage": [item for data, _, _ in reports for item in data["input_coverage"]],
                "batches": [data for data, _, _ in reports],
            }
            try:
                sarif = _aggregate_sarif([sarif for _, sarif, _ in reports], policy.report_category)
            except ValueError as error:
                print(f"Semgrep: incompatible batch SARIF metadata: {str(error)[:800]}; no report published", file=sys.stderr)
                return code or 1
            payloads = {"semgrep.json": data, "semgrep.sarif": sarif}
        else:
            for index, (data, sarif, _) in enumerate(reports):
                for run_index, run in enumerate(sarif["runs"]):
                    run["automationDetails"] = {**run.get("automationDetails", {}), "id": f"{policy.report_category}-{index}-{run_index}/"}
                payloads.update({f"{index}.json": data, f"{index}.sarif": sarif})
        for name, value in payloads.items():
            (candidate / name).write_bytes(deterministic_json(value))
    # Only describe files after the complete directory has committed.
    present = FindingOutput("Semgrep", "source", settings.root)
    for name, value in payloads.items():
        if name.endswith(".json"):
            value = {
                **value,
                "results": [item for item in _objects(value["results"], "results") if not _object(item.get("extra", {}), "extra").get("is_ignored", False)],
            }
        present(value, name.rsplit(".", 1)[1], settings.path(output) / name, code, any(found for _, _, found in reports))
    return code
