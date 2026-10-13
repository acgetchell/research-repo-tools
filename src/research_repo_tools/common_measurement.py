"""Common-harness plans composed with the existing measurement/worktree engine."""

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from research_repo_tools.complete_runs import (
    RUN_SCHEMA,
    RUN_SCHEMA_V2,
    CompletePolicy,
    CompleteRun,
    RunSeries,
    _array,
    _integer,
    _sample_settings,
    _sampling_counts,
    collect_complete_sample,
    serialize_policy,
    serialize_run,
    validate_run_evidence,
)
from research_repo_tools.criterion import Statistic, Unit, _positive, _statistic, _unit
from research_repo_tools.criterion_inventory import (
    DiscoveryPlan,
    _environment,
    _environment_value,
    _overlay_environment,
    _parse_discovery,
    discover_inventory,
    serialize_inventory,
)
from research_repo_tools.evidence import Evidence, Provenance, _object, _string, compare_provenance, fingerprint_files
from research_repo_tools.measurement import MeasurementConfig, _strings, capture_provenance
from research_repo_tools.process import run_command_live
from research_repo_tools.publication import _inventory, _path
from research_repo_tools.publication_config import _table
from research_repo_tools.release_pairs import ReleasePair

__all__ = ["CommonHarnessPlan", "DiscoveryPolicy", "MeasurementPhase", "measure_prepared_pair"]


@dataclass(frozen=True, slots=True)
class DiscoveryPolicy:
    """Validated sample requirements whose exact IDs come from native discovery."""

    discovery: DiscoveryPlan
    sample_count: int | None = 100
    statistics: tuple[Statistic, ...] = ("mean", "median")
    confidence_level: float = 0.95
    unit: Unit = "ns"
    sample_counts: tuple[tuple[str, int], ...] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.discovery, DiscoveryPlan):
            raise TypeError("discovery policy requires a DiscoveryPlan")
        object.__setattr__(self, "sample_counts", _sampling_counts(self.sample_count, self.sample_counts))
        statistics, level = _sample_settings(self.sample_count, self.statistics, self.confidence_level, self.unit)
        object.__setattr__(self, "statistics", statistics)
        object.__setattr__(self, "confidence_level", level)


@dataclass(frozen=True, slots=True)
class MeasurementPhase:
    command: tuple[str, ...]
    gate: tuple[str, ...]
    policy: CompletePolicy | DiscoveryPolicy
    environment: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for key in ("command", "gate"):
            if isinstance(getattr(self, key), str):
                raise ValueError(f"{key} must be an argument vector")
            object.__setattr__(self, key, _strings(list(getattr(self, key)), key))
        if not isinstance(self.policy, CompletePolicy | DiscoveryPolicy):
            raise TypeError("phase requires a completeness policy")
        object.__setattr__(self, "environment", _environment(self.environment))


@dataclass(frozen=True, slots=True)
class CommonHarnessPlan:
    measurement: MeasurementConfig
    baseline: MeasurementPhase
    current: MeasurementPhase
    series: tuple[RunSeries, ...]
    schema: int = field(default=2, kw_only=True)

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema not in (2, 3):
            raise ValueError("common measurement schema must be integer 2 or 3")
        if not isinstance(self.measurement, MeasurementConfig) or any(not isinstance(phase, MeasurementPhase) for phase in (self.baseline, self.current)):  # ty: ignore[redundant-condition-strict]  Runtime boundary also protects untyped callers.
            raise TypeError("common plan requires measurement settings and two phases")
        if "harness_sha256" not in self.measurement.compatible:
            raise ValueError("common plan must require harness_sha256 compatibility")
        if not self.measurement.probes:
            raise ValueError("common plan requires at least one toolchain version probe")
        required = {
            *(f"context.tool.{name}" for name, _ in self.measurement.probes),
            *(f"context.dependency.{name}" for name, _ in self.measurement.dependencies),
        }
        if not required <= set(self.measurement.compatible):
            raise ValueError("common plan requires compatibility for all declared tools and dependencies")
        if any(not isinstance(series, RunSeries) for series in self.series):  # ty: ignore[redundant-condition-strict]  Runtime boundary also protects untyped callers.
            raise TypeError("plan series must be RunSeries instances")
        if not self.series or len({series.name for series in self.series}) != len(self.series):
            raise ValueError("plan series must have unique names")
        for series in self.series:
            policy = getattr(self, series.phase).policy
            if isinstance(policy, CompletePolicy) and any(full_id not in policy.expected for _, full_id in series.rows):
                raise ValueError("series refers to an unexpected benchmark")
        left, right = self.baseline.policy, self.current.policy
        if self.schema == 2 and (left.sample_count is None or right.sample_count is None or left.sample_count != right.sample_count):
            raise ValueError("schema-2 phases must share a uniform sample count")
        if (left.statistics, left.confidence_level, left.unit) != (right.statistics, right.confidence_level, right.unit):
            raise ValueError("phases must share statistics, confidence and unit")
        object.__setattr__(self, "series", tuple(self.series))

    @property
    def sources(self) -> tuple[str, ...]:
        return self.measurement.sources

    @property
    def harness(self) -> tuple[str, ...]:
        return self.measurement.harness


def parse_plan(document: dict[str, Any]) -> CommonHarnessPlan:
    raw = _table(
        document,
        "common measurement",
        {"schema", "sources", "harness", "phases", "series"},
        {"criterion-dir", "sample", "sample-count", "statistics", "confidence-level", "unit", "timeout", "probes", "dependencies", "context", "compatible"},
    )
    phases = _object(raw["phases"], "phases", {"baseline", "current"})
    schema = _integer(raw["schema"], "common measurement schema")
    if schema not in (2, 3):
        raise ValueError("common measurement schema must be integer 2 or 3")
    parsed = {}
    for name, data in phases.items():
        phase = _table(data, name, {"command", "gate"}, {"expected", "discovery", "environment"} | ({"sample-counts"} if schema == 3 else set()))
        if ("expected" in phase) == ("discovery" in phase):
            raise ValueError("phase requires exactly one of expected or discovery")
        if "sample-counts" in phase and "sample-count" in raw:
            raise ValueError("choose exactly one of shared sample-count or phase sample-counts")
        counts = tuple((key, _integer(value, "sample count")) for key, value in _object(phase.get("sample-counts", {}), "sample counts").items())
        settings = (
            None if "sample-counts" in phase else _integer(raw.get("sample-count", 100), "sample count"),
            tuple(_statistic(item) for item in _array(raw.get("statistics", ["mean", "median"]), "statistics")),
            _positive(raw.get("confidence-level", 0.95), "confidence level"),
            _unit(raw.get("unit", "ns")),
        )
        policy = (
            CompletePolicy(_strings(phase["expected"], "expected"), *settings, sample_counts=counts)
            if "expected" in phase
            else DiscoveryPolicy(_parse_discovery(phase["discovery"]), *settings, sample_counts=counts)
        )
        parsed[name] = MeasurementPhase(
            _strings(phase["command"], "command"),
            _strings(phase["gate"], "gate"),
            policy,
            tuple((key, _environment_value(value)) for key, value in _object(phase.get("environment", {}), "environment").items()),
        )
    if not isinstance(raw["series"], list):
        raise ValueError("series must be an array of tables")
    series = []
    for value in raw["series"]:
        item = _table(value, "series", {"name", "phase", "rows"}, set())
        series.append(
            RunSeries(
                _string(item["name"], "name"),
                _string(item["phase"], "phase"),
                tuple((key, _string(value, "full ID")) for key, value in _object(item["rows"], "rows").items()),
            )
        )
    probes = tuple((key, _strings(value, "probe")) for key, value in _object(raw.get("probes", {}), "probes").items())
    dependencies = tuple((key, _string(value, "dependency")) for key, value in _object(raw.get("dependencies", {}), "dependencies").items())
    required = {"harness_sha256", *(f"context.tool.{name}" for name, _ in probes), *(f"context.dependency.{name}" for name, _ in dependencies)}
    compatible = tuple(sorted(required | set(_strings(raw.get("compatible", []), "compatible", nonempty=False))))
    config = MeasurementConfig(
        parsed["current"].command,
        _strings(raw["sources"], "sources"),
        _strings(raw["harness"], "harness"),
        _string(raw.get("criterion-dir", "target/criterion"), "criterion-dir"),
        _string(raw.get("sample", "new"), "sample"),
        timeout=_integer(raw.get("timeout", 7200), "timeout"),
        probes=probes,
        dependencies=dependencies,
        context=tuple((key, _string(value, "context")) for key, value in _object(raw.get("context", {}), "context").items()),
        compatible=compatible,
    )
    return CommonHarnessPlan(config, parsed["baseline"], parsed["current"], tuple(series), schema=schema)


def measure_prepared_pair(baseline: Path, current: Path, plan: CommonHarnessPlan, pair: ReleasePair, *, working_tree: bool = False) -> Evidence:
    """Measure two disposable, caller-owned checkouts using captured current files.

    Commands are trusted consumer code. Both gates pass independently before any
    timing. Retained raw files survive checkout cleanup. No Git state is mutated by
    this function; measure_pair owns optional worktree lifecycle.
    """
    roots = {"baseline": baseline.resolve(strict=True), "current": current.resolve(strict=True)}
    if roots["baseline"] == roots["current"] or any(
        left in right.parents for left, right in ((roots["baseline"], roots["current"]), (roots["current"], roots["baseline"]))
    ):
        raise ValueError("measurement checkouts must be distinct and nonoverlapping")
    current = roots["current"]
    inventory = _inventory(current, plan.harness)
    captured = {name: (_path(current, name).read_bytes(), _path(current, name).stat().st_mode & 0o777) for name in inventory}
    if any(path not in captured for _, path in plan.measurement.dependencies):
        raise ValueError("dependency resolution files must belong to the captured harness")
    originals = {}
    for phase, root in roots.items():
        names = _inventory(root, plan.sources)
        originals[phase] = (fingerprint_files(root, tuple(map(Path, names))), names)
        criterion = root / plan.measurement.criterion_dir
        if any(path.is_symlink() or path.is_junction() for path in (criterion, *criterion.parents) if path.is_relative_to(root)):
            raise ValueError("Criterion output contains a symlink or junction")
        # A complete run starts with no output tree, including partial scratch.
        if criterion.exists():
            raise ValueError("complete measurement requires an absent Criterion output tree")
    for root in roots.values():
        # Remove obsolete harness files matched by the same declared patterns.
        for pattern in plan.harness:
            for path in root.glob(pattern):
                name = path.relative_to(root).as_posix()
                _path(root, name)
                if name not in captured:
                    path.unlink()
        for name, (data, mode) in captured.items():
            path = _path(root, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(mode)
    before = {}
    declared = {}
    configurations = {}
    environments = {}
    for phase, root in roots.items():
        selected = getattr(plan, phase)
        config = replace(plan.measurement, command=selected.command)
        configurations[phase] = config
        env = _overlay_environment(os.environ, selected.environment)
        environments[phase] = env
        before[phase] = capture_provenance(root, config, getattr(pair, phase), mode="working-tree" if phase == "current" and working_tree else "tag", env=env)
        declared[phase] = replace(
            before[phase],
            context=tuple(
                {
                    **dict(before[phase].context),
                    "phase": phase,
                    "environment": json.dumps(dict(selected.environment), sort_keys=True),
                    "gate-command": json.dumps(selected.gate),
                    "original-source-sha256": originals[phase][0],
                    "original-source-inventory": json.dumps(originals[phase][1]),
                }.items()
            ),
        )
    early_fields = tuple(field for field in plan.measurement.compatible if field not in {"context.discovery-inventory", "context.sampling-policy"})
    compatibility = compare_provenance(declared["baseline"], declared["current"], fields=early_fields)
    if not compatibility.compatible:
        raise ValueError(f"required benchmark provenance differs or is unknown before timing: {compatibility.differences}")

    def unchanged(phase: str) -> None:
        source = before[phase]
        if capture_provenance(roots[phase], configurations[phase], getattr(pair, phase), mode=dict(source.context)["mode"], env=environments[phase]) != source:
            raise ValueError(f"{phase} measurement inputs or host/tool identity changed")

    policies = {}
    for phase, root in roots.items():
        policy = getattr(plan, phase).policy
        if isinstance(policy, DiscoveryPolicy):
            inventory = discover_inventory(root, policy.discovery, env=environments[phase])
            policies[phase] = CompletePolicy(
                inventory.expected, policy.sample_count, policy.statistics, policy.confidence_level, policy.unit, sample_counts=policy.sample_counts
            )
            rows = {full_id for series in plan.series if series.phase == phase for _, full_id in series.rows}
            if not rows <= set(inventory.expected):
                raise ValueError(f"{phase} discovered inventory omits series IDs: {sorted(rows - set(inventory.expected))}")
            source = declared[phase]
            declared[phase] = replace(
                source, context=(*source.context, ("discovery-inventory", json.dumps(json.loads(serialize_inventory(inventory)), sort_keys=True)))
            )
        else:
            policies[phase] = policy
        if plan.schema == 3:
            source = declared[phase]
            declared[phase] = replace(source, context=(*source.context, ("sampling-policy", serialize_policy(policies[phase]).decode("utf-8"))))
        for selected in roots:
            unchanged(selected)
            if (roots[selected] / plan.measurement.criterion_dir).exists():
                raise ValueError("discovery produced timing output before measurement")

    compatibility = compare_provenance(declared["baseline"], declared["current"], fields=plan.measurement.compatible)
    if not compatibility.compatible:
        raise ValueError(f"required benchmark provenance differs or is unknown before timing: {compatibility.differences}")

    for phase, root in roots.items():
        gate = getattr(plan, phase).gate
        result = run_command_live(gate[0], gate[1:], cwd=root, env=environments[phase], timeout=plan.measurement.timeout, check=False)
        if result.returncode:
            raise ValueError(f"{phase} preflight gate failed")
        unchanged(phase)
        if (root / plan.measurement.criterion_dir).exists():
            raise ValueError("preflight gate produced timing output before measurement")
    samples, sources = [], []
    for phase, root in roots.items():
        selected = getattr(plan, phase)
        unchanged(phase)
        if (root / plan.measurement.criterion_dir).exists():
            raise ValueError(f"{phase} timing output appeared before its measurement")
        result = run_command_live(selected.command[0], selected.command[1:], cwd=root, env=environments[phase], timeout=plan.measurement.timeout)
        if result.returncode:
            raise ValueError(f"{phase} measurement failed")
        samples.append((phase, collect_complete_sample(root / plan.measurement.criterion_dir, plan.measurement.sample, policies[phase])))
        unchanged(phase)
        source = declared[phase]
        context = {**dict(source.context), "gate-status": "passed"}
        sources.append((phase, Provenance(source.revision, source.source_sha256, source.harness_sha256, tuple(context.items()))))
    for phase in roots:
        unchanged(phase)
    run = CompleteRun(tuple(samples), plan.series, plan.measurement.compatible, schema=RUN_SCHEMA_V2 if plan.schema == 3 else RUN_SCHEMA)
    retained = Evidence(serialize_run(run), run.schema, tuple(sources))
    validate_run_evidence(retained)
    return retained
