"""Configured benchmark measurement and independently captured source provenance."""

import json
import re
import shutil
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from research_repo_tools.criterion import COMPARISON_SCHEMA, Sample, Statistic, Unit, _statistic, _unit, collect_sample, compare_samples, serialize_comparison
from research_repo_tools.evidence import Evidence, Provenance, _object, _string, compare_provenance, fingerprint_files
from research_repo_tools.host_metadata import capture_host, serialize_host
from research_repo_tools.process import run_command_live, run_git_bytes
from research_repo_tools.publication import _inventory, _path, _publication_name
from research_repo_tools.publication_config import _table
from research_repo_tools.release_discovery import _tag_version, normalize_tag
from research_repo_tools.release_pairs import ReleasePair
from research_repo_tools.source_exports import CapturedSource, capture_source, export_source
from research_repo_tools.worktrees import apply_snapshot, capture_snapshot, temporary_worktree

if TYPE_CHECKING:
    from research_repo_tools.common_measurement import CompleteMeasurementPlan

__all__ = ["MeasurementConfig", "capture_provenance", "load_measurement", "measure_checkout", "measure_pair", "resolve_revision"]


def _strings(value: object, context: str, *, nonempty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or (nonempty and not value):
        raise ValueError(f"{context} must be an array{' with at least one entry' if nonempty else ''}")
    return tuple(_string(item, context) for item in value)


@dataclass(frozen=True, slots=True)
class MeasurementConfig:
    command: tuple[str, ...]
    sources: tuple[str, ...]
    harness: tuple[str, ...]
    criterion_dir: str = "target/criterion"
    sample: str = "new"
    statistic: Statistic = "median"
    unit: Unit = "ns"
    timeout: int = 7200
    probes: tuple[tuple[str, tuple[str, ...]], ...] = ()
    dependencies: tuple[tuple[str, str], ...] = ()
    context: tuple[tuple[str, str], ...] = ()
    compatible: tuple[str, ...] = ()
    minimum_release: str | None = field(default=None, kw_only=True)
    workloads: tuple[str, ...] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workloads", _strings(self.workloads, "workloads", nonempty=False))
        if self.minimum_release is not None:
            object.__setattr__(self, "minimum_release", normalize_tag(_string(self.minimum_release, "minimum-release")))
        object.__setattr__(self, "probes", tuple((key, _strings(value, f"probe {key}")) for key, value in self.probes))
        object.__setattr__(self, "dependencies", tuple(tuple(item) for item in self.dependencies))
        object.__setattr__(self, "context", tuple(tuple(item) for item in self.context))
        for name in ("command", "sources", "harness", "compatible"):
            object.__setattr__(self, name, _strings(getattr(self, name), name, nonempty=name != "compatible"))
        _publication_name(self.criterion_dir)
        _publication_name(self.sample)
        if "/" in self.sample:
            raise ValueError("sample must be a single component")
        _statistic(self.statistic)
        _unit(self.unit)
        if type(self.timeout) is not int or self.timeout <= 0:
            raise ValueError("measurement timeout must be a positive integer")
        reserved = {
            "release",
            "mode",
            "os",
            "architecture",
            "cpu",
            "host",
            "command",
            "source-inventory",
            "harness-inventory",
            "harness-modes",
            "fingerprint-schema",
            "phase",
            "environment",
            "gate-command",
            "gate-status",
            "original-source-sha256",
            "original-source-inventory",
            "discovery-inventory",
            "sampling-policy",
            "measurement-declaration",
            "source-export",
            "saved-sample",
            "workload-inventory",
            "workload-sha256",
        }
        context = dict(self.context)
        if len(context) != len(self.context) or context.keys() & reserved or any(key.startswith(("tool.", "dependency.", "host.")) for key in context):
            raise ValueError("measurement context duplicates or replaces captured metadata")
        for key, value in self.context:
            _string(key, "context name")
            _string(value, f"context {key}")
        for name, _command in self.probes:
            _string(name, "probe name")
        if len(dict(self.probes)) != len(self.probes) or len(dict(self.dependencies)) != len(self.dependencies):
            raise ValueError("measurement probes/dependencies must have unique names")
        for name, path in self.dependencies:
            _string(name, "dependency name")
            _publication_name(path)
        if self.compatible:
            # Check field names before running any configured commands.
            unknown = Provenance("0" * 40)
            compare_provenance(unknown, unknown, fields=self.compatible)

    def validate_release(self, tag: str) -> None:
        """Reject a label below the configured floor before acquiring new samples."""
        if self.minimum_release is not None and _tag_version(normalize_tag(tag)) < _tag_version(self.minimum_release):
            raise ValueError(f"unsupported measurement release {tag}; requires {self.minimum_release} or newer")


def load_measurement(root: Path, configuration: str) -> MeasurementConfig | CompleteMeasurementPlan:
    """Read strict measurement TOML; commands are trusted consumer configuration."""
    document = tomllib.loads(_path(root.resolve(), configuration).read_bytes().decode("utf-8"))
    if type(document.get("schema")) is int and document["schema"] in (2, 3, 4):
        from research_repo_tools.common_measurement import parse_plan

        return parse_plan(document)
    raw = _table(
        document,
        "measurement",
        {"schema", "command", "sources", "harness"},
        {"criterion-dir", "sample", "statistic", "unit", "timeout", "probes", "dependencies", "context", "compatible", "minimum-release"},
    )
    if type(raw["schema"]) is not int or raw["schema"] != 1:
        raise ValueError("measurement schema must be integer 1")
    timeout = raw.get("timeout", 7200)
    if type(timeout) is not int:
        raise ValueError("measurement timeout must be a positive integer")
    return MeasurementConfig(
        _strings(raw["command"], "command"),
        _strings(raw["sources"], "sources"),
        _strings(raw["harness"], "harness"),
        _string(raw.get("criterion-dir", "target/criterion"), "criterion-dir"),
        _string(raw.get("sample", "new"), "sample"),
        _statistic(raw.get("statistic", "median")),
        _unit(raw.get("unit", "ns")),
        timeout,
        tuple((key, _strings(value, f"probe {key}")) for key, value in _object(raw.get("probes", {}), "probes").items()),
        tuple((key, _string(value, f"dependency {key}")) for key, value in _object(raw.get("dependencies", {}), "dependencies").items()),
        tuple((key, _string(value, f"context {key}")) for key, value in _object(raw.get("context", {}), "context").items()),
        _strings(raw.get("compatible", []), "compatible", nonempty=False),
        minimum_release=_string(raw["minimum-release"], "minimum-release") if "minimum-release" in raw else None,
    )


def resolve_revision(root: Path, reference: str) -> str:
    """Resolve HEAD or an existing local stable tag without fetching."""
    if reference != "HEAD" and re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", reference) is None:
        raise ValueError("measurement reference must be HEAD or a canonical stable tag")
    selected = reference if reference == "HEAD" else f"refs/tags/{reference}"
    revision = run_git_bytes(["--no-pager", "--no-replace-objects", "rev-parse", "--verify", f"{selected}^{{commit}}"], cwd=root).stdout.decode("ascii").strip()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision) is None:
        raise ValueError(f"invalid resolved revision for {reference}")
    return revision


def capture_provenance(
    root: Path, configuration: MeasurementConfig, tag: str, *, mode: str, env: Mapping[str, str] | None = None, source: CapturedSource | None = None
) -> Provenance:
    """Capture source/harness framing, host identity, exact command and probes."""
    root = root.resolve(strict=True)
    sources, harness = _inventory(root, configuration.sources), _inventory(root, configuration.harness)
    context = dict(configuration.context)
    context.update(
        {
            "release": tag,
            "mode": mode,
            "command": json.dumps(configuration.command, ensure_ascii=False),
            "source-inventory": json.dumps(sources, ensure_ascii=False),
            "harness-inventory": json.dumps(harness, ensure_ascii=False),
            "harness-modes": json.dumps([(name, _path(root, name).stat().st_mode & 0o777) for name in harness]),
            "fingerprint-schema": "research-repo-tools/files/v1",
        }
    )
    if configuration.workloads:
        workloads = _inventory(root, configuration.workloads)
        context["workload-inventory"] = json.dumps(workloads, ensure_ascii=False)
        context["workload-sha256"] = fingerprint_files(root, tuple(map(Path, workloads)))
    host = capture_host(root, probes=configuration.probes, env=env)
    context["host"] = json.dumps(json.loads(serialize_host(host)), ensure_ascii=False, sort_keys=True)
    for key in ("os", "architecture", "cpu"):
        value = getattr(host, key)
        if value is not None:
            context[key] = value
    for key in ("physical_cores", "logical_threads", "memory_bytes"):
        value = getattr(host, key)
        if value is not None:
            context[f"host.{key}"] = str(value)
    for name, result in host.tools:
        if result is None:
            raise ValueError(f"unavailable tool version probe: {name}")
        context[f"tool.{name}"] = json.dumps(result, ensure_ascii=False)
    for name, path in configuration.dependencies:
        lock = tomllib.loads(_path(root, path).read_bytes().decode("utf-8"))
        versions = [entry.get("version") for entry in lock.get("package", []) if isinstance(entry, dict) and entry.get("name") == name]
        if len(versions) != 1:
            raise ValueError(f"{path} must contain exactly one {name} dependency version")
        context[f"dependency.{name}"] = _string(versions[0], f"{name} dependency version")
    if source is not None:
        context["source-export"] = source.provenance()
    return Provenance(
        source.revision if source is not None else resolve_revision(root, "HEAD"),
        fingerprint_files(root, tuple(map(Path, sources))),
        fingerprint_files(root, tuple(map(Path, harness))),
        tuple(context.items()),
    )


def measure_checkout(root: Path, configuration: MeasurementConfig, tag: str, *, mode: str, source: CapturedSource | None = None) -> tuple[Sample, Provenance]:
    """Run the configured command live and reject source changes during measurement.

    Use a fresh isolated checkout. The selected sample must not already exist,
    preventing stale Criterion rows from being mixed with this measurement.
    This operation executes trusted consumer code and needs no write credentials.
    """
    configuration.validate_release(tag)
    root = root.resolve(strict=True)
    criterion = root / configuration.criterion_dir
    for ancestor in (criterion, *criterion.parents):
        if ancestor.is_relative_to(root) and (ancestor.is_symlink() or ancestor.is_junction()):
            raise ValueError("Criterion output path contains a symlink or junction")
    if criterion.exists() and collect_sample(criterion, configuration.sample, statistic=configuration.statistic, unit=configuration.unit).estimates:
        raise ValueError("measurement sample already exists; use a fresh isolated checkout")
    before = capture_provenance(root, configuration, tag, mode=mode, source=source)
    print(f"Measuring {tag}; live benchmark output follows", flush=True)
    run_command_live(configuration.command[0], configuration.command[1:], cwd=root, timeout=configuration.timeout)
    sample = collect_sample(criterion, configuration.sample, statistic=configuration.statistic, unit=configuration.unit)
    if not sample.estimates:
        raise ValueError(f"measurement produced no estimates for sample {configuration.sample}")
    after = capture_provenance(root, configuration, tag, mode=mode, source=source)
    if before != after:
        raise ValueError("measurement inputs or host/tool identity changed while the benchmark ran")
    return sample, before


def measure_pair(
    root: Path,
    configuration: MeasurementConfig | CompleteMeasurementPlan,
    pair: ReleasePair,
    *,
    working_tree: bool = False,
    allow_git_mutations: bool = False,
    backend: str | None = None,
    verify: Callable[[], None] | None = None,
) -> Evidence:
    """Measure two isolated sources and return shared retained evidence.

    Working-tree measurement uses a captured HEAD patch and nonignored new
    files. Tagged comparisons never fetch absent tags. Failure leaves no
    published evidence; cleanup failures retain recovery checkouts.
    """
    selected_backend = backend or (configuration.backend if not isinstance(configuration, MeasurementConfig) else "worktree")
    if selected_backend not in {"export", "worktree"}:
        raise ValueError("source backend must be export or worktree")
    if not isinstance(configuration, MeasurementConfig) and selected_backend != configuration.backend:
        raise ValueError("backend must match the acquisition declaration")
    if selected_backend == "worktree" and not allow_git_mutations:
        raise ValueError("measure requires --allow-git-mutations for isolated worktree lifecycle")
    settings = configuration if isinstance(configuration, MeasurementConfig) else configuration.measurement
    settings.validate_release(pair.baseline)
    settings.validate_release(pair.current)
    root = root.resolve(strict=True)
    baseline_revision = resolve_revision(root, pair.baseline)
    snapshot = capture_snapshot(root) if working_tree and selected_backend == "worktree" else None
    current_revision = snapshot.revision if snapshot else resolve_revision(root, "HEAD" if working_tree else pair.current)
    parent = Path(tempfile.mkdtemp(prefix="research-measurement-")).resolve()
    try:
        with ExitStack() as stack:
            captures = None
            if selected_backend == "export":
                captures = {
                    "baseline": capture_source(root, pair.baseline),
                    "current": capture_source(root, "HEAD" if working_tree else pair.current, working_tree=working_tree),
                }
                baseline = stack.enter_context(export_source(captures["baseline"], parent / "baseline"))
                current = stack.enter_context(export_source(captures["current"], parent / "current"))
            else:
                baseline = stack.enter_context(temporary_worktree(root, parent / "baseline", baseline_revision, allow_git_mutations=True))
                current = stack.enter_context(temporary_worktree(root, parent / "current", current_revision, allow_git_mutations=True))
                if snapshot:
                    apply_snapshot(current, snapshot)
            if isinstance(configuration, MeasurementConfig):
                baseline_sample, baseline_source = measure_checkout(
                    baseline, configuration, pair.baseline, mode="tag", source=captures["baseline"] if captures else None
                )
                current_sample, current_source = measure_checkout(
                    current, configuration, pair.current, mode="working-tree" if working_tree else "tag", source=captures["current"] if captures else None
                )
                if pair.current == pair.baseline and baseline_source.source_sha256 == current_source.source_sha256:
                    raise ValueError("same-release comparisons require distinct source identities")
                if configuration.compatible:
                    compatibility = compare_provenance(baseline_source, current_source, fields=configuration.compatible)
                    if not compatibility.compatible:
                        raise ValueError(f"required benchmark provenance differs or is unknown: {compatibility.differences}")
                comparison = compare_samples(baseline_sample, current_sample)
                if not comparison.comparisons:
                    raise ValueError("measurements have no common benchmark rows")
                retained = Evidence(serialize_comparison(comparison), COMPARISON_SCHEMA, (("baseline", baseline_source), ("current", current_source)))
            else:
                from research_repo_tools.common_measurement import measure_prepared_pair

                retained = measure_prepared_pair(baseline, current, configuration, pair, working_tree=working_tree, captured_sources=captures, verify=verify)
            if captures:
                for capture in captures.values():
                    capture.verify()
        if snapshot and snapshot != capture_snapshot(root):
            raise ValueError("source working tree changed while measurement ran; no evidence published")
        if verify is not None:
            verify()
        return retained
    finally:
        # Never erase recovery checkouts when Git removal failed.
        if not any(parent.iterdir()):
            shutil.rmtree(parent)
