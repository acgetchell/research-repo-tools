"""Strict native Semgrep scans over the shared portable file inventory."""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path, PurePath
from urllib.parse import quote, unquote

from research_repo_tools import config, security, semgrep
from research_repo_tools.evidence import _load_json, _object, deterministic_json
from research_repo_tools.files import publish_directory
from research_repo_tools.process import resolve_executable
from research_repo_tools.sarif import _metadata_references, _objects, _reindex_reference, _rule_id, _rule_relationships, _run
from research_repo_tools.scanner_output import FindingOutput
from research_repo_tools.security import security_inventory
from research_repo_tools.selection import argument_batches
from research_repo_tools.semgrep_docs import rust_blocks
from research_repo_tools.semgrep_findings import parse_results

__all__ = ["check_documentation_fixtures", "scan"]


def check_documentation_fixtures(settings: config.Config) -> int:
    """Adapt Markdown fences, delegating assertions to the shared fixture checker."""
    if settings.semgrep.fixtures is None:
        raise ValueError("semgrep.fixtures must be explicit")
    source = settings.path(settings.semgrep.fixtures)
    if not source.is_dir():
        raise ValueError("documentation fixtures require a directory")
    if settings.semgrep.counts:
        raise ValueError("documentation fixtures use source annotations; keep count-based fixtures in a separate fixture gate")
    with tempfile.TemporaryDirectory(prefix="research-semgrep-fixtures-") as directory:
        root = Path(directory)
        for path in semgrep.fixtures(source):
            if path.is_symlink():
                raise ValueError("fixture symlinks are unsupported")
            destination = root / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".md":
                blocks = rust_blocks(path)
                if not blocks:
                    raise ValueError(f"Markdown fixture contains no Rust fences: {path}")
                for index, block in enumerate(blocks):
                    destination.with_name(f"{path.name}.block-{index}.rs").write_text(block, encoding="utf-8", newline="\n")
            else:
                shutil.copyfile(path, destination)
        return semgrep.check(replace(settings, semgrep=replace(settings.semgrep, fixtures=str(root))))


def _locations(value, mapping):
    if isinstance(value, dict):
        return {key: _locations(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_locations(item, mapping) for item in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def _source_mapping(snippet: PurePath, source: PurePath) -> dict[str, str]:
    """Preserve native JSON paths and forward-slash/encoded SARIF URIs."""
    return {
        str(snippet): str(source),
        snippet.as_posix(): source.as_posix(),
        quote(snippet.as_posix(), safe="/"): quote(source.as_posix(), safe="/"),
    }


def _offset_artifacts(value, offset, length):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "artifactLocation" and isinstance(item, dict) and "index" in item:
                if type(item["index"]) is not int or not 0 <= item["index"] < length:
                    raise ValueError("invalid SARIF artifact index")
                item["index"] += offset
            elif key != "properties":
                _offset_artifacts(item, offset, length)
    elif isinstance(value, list):
        for item in value:
            _offset_artifacts(item, offset, length)


def _aggregate_sarif(documents: list[dict], category: str) -> dict:
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
            for artifact in artifacts:
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
                if "invocationIndex" in result:
                    old = result["invocationIndex"]
                    if type(old) is not int or not 0 <= old < len(invocations):
                        raise ValueError("invalid SARIF invocation index")
                    result["invocationIndex"] = old + len(all_invocations)
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
        automationDetails={"id": category},
    )
    merged["runs"] = [combined]
    return merged


def _batch_reports(json_path: Path, sarif_path: Path, targets: set[Path], root: Path, inline_suppressions: bool) -> tuple[dict, dict, bool]:
    values = []
    for path in (json_path, sarif_path):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("missing or oversized native report")
        values.append(_object(_load_json(path.read_bytes(), "Semgrep report"), "Semgrep report"))
    data, sarif = values
    parsed = parse_results(json.dumps(data))
    paths = _object(data.get("paths"), "Semgrep paths")
    scanned = paths.get("scanned")
    if not isinstance(data.get("errors"), list) or not isinstance(scanned, list) or any(not isinstance(path, str) for path in scanned):
        raise ValueError("Semgrep report is missing errors or scanned-path inventory")
    if {(root / path).resolve() for path in scanned} != targets:
        raise ValueError("Semgrep skipped a required input or scanned an unselected input")
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
    for _native_run, _driver, rules, results in [_run(run) for run in runs]:
        for result in results:
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
            path = (root / unquote(uri)).resolve()
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
    if any((settings.root / name).resolve().is_relative_to(settings.path(output).resolve()) for name in names):
        raise ValueError("Semgrep output directory must not contain selected inputs")
    binary = resolve_executable("semgrep", cwd=settings.root)
    with publish_directory(settings.path(output)) as candidate, tempfile.TemporaryDirectory(prefix="research-semgrep-scan-") as directory:
        temporary = Path(directory)
        targets, mapping = [], {}
        for name in names:
            path = settings.root / name
            if path.suffix != ".md" or not rust_docs:
                targets.append(path)
            if rust_docs and path.suffix in {".md", ".rs"}:
                for index, block in enumerate(rust_blocks(path)):
                    snippet = temporary / "inputs" / name / f"block-{index}.rs"
                    snippet.parent.mkdir(parents=True, exist_ok=True)
                    snippet.write_text(block, encoding="utf-8", newline="\n")
                    targets.append(snippet)
                    mapping.update(_source_mapping(snippet, path))
        if not targets:
            raise ValueError("selected documentation contains no Rust snippets")
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
        # Absolute snippet paths also work when Windows temp and repository
        # directories live on different drives. Native paths remain whole args.
        arguments = [str(path) for path in targets]
        batches = argument_batches(command, arguments, batch_size=policy.batch_size)
        reports, code = [], 0
        for batch in batches:
            json_path.unlink(missing_ok=True)
            sarif_path.unlink(missing_ok=True)
            try:
                result = security.run_command_bytes(batch[0], batch[1:], cwd=settings.root, env=env, input=b"", timeout=policy.timeout, check=False)
            except subprocess.TimeoutExpired:
                print("Semgrep: scan timed out; no report published", file=sys.stderr)
                return 124
            status = result.returncode if result.returncode >= 0 else 128 - result.returncode
            try:
                selected = {(settings.root / path).resolve() for path in batch[len(command) :]}
                data, sarif, found = _batch_reports(json_path, sarif_path, selected, settings.root, policy.inline_suppressions)
            except OSError, ValueError, TypeError, KeyError:
                print(f"Semgrep: missing, malformed, or incomplete paired reports (exit {status}); no report published", file=sys.stderr)
                return code or status or 1
            code = code or status or int(found)
            reports.append((_locations(data, mapping), _locations(sarif, mapping), found))
        payloads = {}
        if policy.report_layout == "aggregate":
            data = {
                "results": [result for data, _, _ in reports for result in data["results"]],
                "errors": [],
                "paths": {"scanned": sorted({path for data, _, _ in reports for path in data["paths"]["scanned"]})},
                "batches": [data for data, _, _ in reports],
            }
            try:
                sarif = _aggregate_sarif([sarif for _, sarif, _ in reports], policy.report_category)
            except ValueError:
                print("Semgrep: incompatible batch SARIF metadata; no report published", file=sys.stderr)
                return code or 1
            payloads = {"semgrep.json": data, "semgrep.sarif": sarif}
        else:
            for index, (data, sarif, _) in enumerate(reports):
                for run_index, run in enumerate(sarif["runs"]):
                    run["automationDetails"] = {**run.get("automationDetails", {}), "id": f"{policy.report_category}-{index}-{run_index}"}
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
