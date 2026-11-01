"""Common-harness plans composed with the existing measurement/worktree engine."""

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from research_repo_tools.suite_measurement import SuiteMeasurementPlan
    from research_repo_tools.suite_runs import SuiteRun

from research_repo_tools.complete_runs import (
    RUN_SCHEMA,
    RUN_SCHEMA_V2,
    RUN_SCHEMA_V3,
    CompletePolicy,
    CompleteRun,
    CompleteSample,
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
    parse_inventory,
    serialize_inventory,
)
from research_repo_tools.evidence import Evidence, Provenance, _digest, _object, _string, compare_provenance, fingerprint_files
from research_repo_tools.measurement import MeasurementConfig, _strings, capture_provenance
from research_repo_tools.performance_progress import ProgressReporter
from research_repo_tools.process import run_command_live
from research_repo_tools.publication import _inventory, _path
from research_repo_tools.publication_config import _table
from research_repo_tools.release_pairs import ReleasePair
from research_repo_tools.source_exports import CapturedSource, _files, verify_export

__all__ = [
    "CompleteMeasurementPlan",
    "CommonHarnessPlan",
    "DiscoveredSeries",
    "DiscoveryPolicy",
    "MeasurementPhase",
    "measure_prepared_pair",
    "validate_run_policy",
]


@dataclass(frozen=True, slots=True)
class DiscoveredSeries:
    """Select semantic IDs with case-sensitive shell globs; row keys are the IDs."""

    name: str
    phase: str
    include: tuple[str, ...]
    exclude: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _string(self.name, "series name")
        if self.phase not in {"baseline", "current"}:
            raise ValueError("series phase must be baseline or current")
        object.__setattr__(self, "include", _strings(self.include, "include"))
        object.__setattr__(self, "exclude", _strings(self.exclude, "exclude", nonempty=False))

    def resolve(self, expected: tuple[str, ...]) -> RunSeries:
        rows = tuple(
            (identity, identity)
            for identity in expected
            if any(fnmatchcase(identity, pattern) for pattern in self.include) and not any(fnmatchcase(identity, pattern) for pattern in self.exclude)
        )
        return RunSeries(self.name, self.phase, rows)


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
class CompleteMeasurementPlan:
    measurement: MeasurementConfig
    baseline: MeasurementPhase
    current: MeasurementPhase
    series: tuple[RunSeries | DiscoveredSeries, ...]
    schema: int = field(default=4, kw_only=True)
    harness_mode: str = field(kw_only=True)
    backend: str = field(kw_only=True)
    raw_files: tuple[str, ...] = field(default=("tukey.json",), kw_only=True)
    saved_sample: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema not in (2, 3, 4):
            raise ValueError("common measurement schema must be integer 2, 3, or 4")
        if self.harness_mode not in {"common", "independent"} or self.backend not in {"export", "worktree"}:
            raise ValueError("invalid harness mode or source backend")
        if self.schema != 4 and self.harness_mode != "common":
            raise ValueError("independent harness mode requires measurement schema 4")
        if self.schema != 4 and (self.raw_files or self.saved_sample is not None):
            raise ValueError("raw files and saved samples require measurement schema 4")
        from research_repo_tools.criterion_baselines import _raw_files, _saved_name

        _raw_files(("benchmark.json", "estimates.json", "sample.json", *self.raw_files))
        object.__setattr__(self, "raw_files", tuple(sorted(self.raw_files)))
        if self.saved_sample is not None:
            _saved_name(self.saved_sample)
            if self.saved_sample == self.measurement.sample:
                raise ValueError("saved sample must differ from fresh sample")
        if not isinstance(self.measurement, MeasurementConfig) or any(not isinstance(phase, MeasurementPhase) for phase in (self.baseline, self.current)):  # ty: ignore[redundant-condition-strict]  Runtime boundary also protects untyped callers.
            raise TypeError("common plan requires measurement settings and two phases")
        if self.schema == 4 and not self.measurement.workloads:
            raise ValueError("measurement schema 4 requires nonempty workloads")
        if "harness_sha256" not in self.measurement.compatible:
            raise ValueError("common plan must require harness_sha256 compatibility")
        if not self.measurement.probes:
            raise ValueError("common plan requires at least one toolchain version probe")
        required = {
            *(f"context.tool.{name}" for name, _ in self.measurement.probes),
            *(f"context.dependency.{name}" for name, _ in self.measurement.dependencies),
        }
        if self.schema == 4:
            required.add("context.harness-modes")
        if not required <= set(self.measurement.compatible):
            raise ValueError("common plan requires compatibility for all declared tools and dependencies")
        if any(not isinstance(series, RunSeries | DiscoveredSeries) for series in self.series):
            raise TypeError("plan series must be RunSeries or DiscoveredSeries instances")
        if not self.series or len({series.name for series in self.series}) != len(self.series):
            raise ValueError("plan series must have unique names")
        for series in self.series:
            policy = getattr(self, series.phase).policy
            if isinstance(policy, CompletePolicy) and isinstance(series, RunSeries) and any(full_id not in policy.expected for _, full_id in series.rows):
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


@dataclass(frozen=True, slots=True)
class CommonHarnessPlan(CompleteMeasurementPlan):
    """Older constructor defaults; acquisition mechanics are shared."""

    schema: int = field(default=2, kw_only=True)
    harness_mode: str = field(default="common", kw_only=True)
    backend: str = field(default="worktree", kw_only=True)
    raw_files: tuple[str, ...] = field(default=(), kw_only=True)


def parse_plan(document: dict[str, Any]) -> CompleteMeasurementPlan:
    raw = _table(
        document,
        "common measurement",
        {"schema", "sources", "harness", "phases", "series"},
        {
            "criterion-dir",
            "sample",
            "sample-count",
            "statistics",
            "confidence-level",
            "unit",
            "timeout",
            "probes",
            "dependencies",
            "context",
            "compatible",
            "minimum-release",
            "workloads",
            "harness-mode",
            "backend",
            "raw-files",
            "saved-sample",
        },
    )
    phases = _object(raw["phases"], "phases", {"baseline", "current"})
    schema = _integer(raw["schema"], "common measurement schema")
    if schema not in (2, 3, 4):
        raise ValueError("common measurement schema must be integer 2, 3, or 4")
    if schema == 4 and not {"harness-mode", "backend"} <= raw.keys():
        raise ValueError("schema 4 requires explicit harness-mode and backend")
    parsed = {}
    for name, data in phases.items():
        phase = _table(
            data,
            name,
            {"command", "gate"},
            {"expected", "discovery", "environment"} | ({"sample-counts"} if schema >= 3 else set()) | ({"sample-count"} if schema == 4 else set()),
        )
        if ("expected" in phase) == ("discovery" in phase):
            raise ValueError("phase requires exactly one of expected or discovery")
        if "sample-counts" in phase and ("sample-count" in raw or "sample-count" in phase):
            raise ValueError("choose exactly one of shared sample-count or phase sample-counts")
        counts = tuple((key, _integer(value, "sample count")) for key, value in _object(phase.get("sample-counts", {}), "sample counts").items())
        settings = (
            None if "sample-counts" in phase else _integer(phase.get("sample-count", raw.get("sample-count", 100)), "sample count"),
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
    series: list[RunSeries | DiscoveredSeries] = []
    for value in raw["series"]:
        item = _table(value, "series", {"name", "phase"}, {"rows", "select"})
        if ("rows" in item) == ("select" in item):
            raise ValueError("series requires exactly one of rows or select")
        if "select" in item:
            selection = _table(item["select"], "series selection", {"include"}, {"exclude"})
            series.append(
                DiscoveredSeries(
                    _string(item["name"], "name"),
                    _string(item["phase"], "phase"),
                    _strings(selection["include"], "include"),
                    _strings(selection.get("exclude", []), "exclude", nonempty=False),
                )
            )
            continue
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
    if schema == 4:
        required.add("context.harness-modes")
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
        minimum_release=_string(raw["minimum-release"], "minimum-release") if "minimum-release" in raw else None,
        workloads=_strings(raw.get("workloads", []), "workloads", nonempty=False),
    )
    if schema != 4 and {"workloads", "harness-mode", "backend", "raw-files", "saved-sample"} & raw.keys():
        raise ValueError("explicit acquisition settings require measurement schema 4")
    plan_type = CompleteMeasurementPlan if schema == 4 else CommonHarnessPlan
    return plan_type(
        config,
        parsed["baseline"],
        parsed["current"],
        tuple(series),
        schema=schema,
        harness_mode=_string(raw.get("harness-mode", "common"), "harness-mode"),
        backend=_string(raw.get("backend", "export" if schema == 4 else "worktree"), "backend"),
        raw_files=_strings(raw.get("raw-files", ["tukey.json"] if schema == 4 else []), "raw-files", nonempty=False),
        saved_sample=_string(raw["saved-sample"], "saved-sample") if "saved-sample" in raw else None,
    )


def _declaration(plan: CompleteMeasurementPlan) -> str:
    """Bind acquisition settings absent from older provenance to the measured run."""
    config = plan.measurement
    if plan.schema == 4:
        return json.dumps(_plan_document(plan), sort_keys=True, ensure_ascii=False)
    return json.dumps(
        {
            "schema": "research-repo-tools/measurement-declaration/v1",
            "sources": config.sources,
            "harness": config.harness,
            "probes": config.probes,
            "dependencies": config.dependencies,
            "criterion-dir": config.criterion_dir,
            "sample": config.sample,
            "discovery": {
                phase: asdict(selected.policy.discovery)
                for phase in ("baseline", "current")
                if isinstance((selected := getattr(plan, phase)).policy, DiscoveryPolicy)
            },
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _series(plan: CompleteMeasurementPlan, policies: dict[str, CompletePolicy]) -> tuple[RunSeries, ...]:
    return tuple(item.resolve(policies[item.phase].expected) if isinstance(item, DiscoveredSeries) else item for item in plan.series if item.phase in policies)


def validate_run_policy(
    evidence: Evidence, plan: MeasurementConfig | CompleteMeasurementPlan | SuiteMeasurementPlan, *, release: bool = False
) -> CompleteRun | SuiteRun:
    """Validate acquisition against independent declarations; release adds eligibility."""
    from research_repo_tools.suite_measurement import SuiteMeasurementPlan
    from research_repo_tools.suite_runs import validate_suite_policy

    if isinstance(plan, SuiteMeasurementPlan):
        return validate_suite_policy(evidence, plan, release=release)
    if not isinstance(plan, CompleteMeasurementPlan):
        raise ValueError("run eligibility requires a complete measurement policy")
    run = validate_run_evidence(evidence)
    if not isinstance(run, CompleteRun):
        raise ValueError("suite evidence requires a suite measurement policy")
    return _validate_policy(evidence, plan, run, release=release)


def _validate_policy(evidence: Evidence, plan: CompleteMeasurementPlan, run: CompleteRun, *, release: bool = False) -> CompleteRun:
    if run.harness_mode != plan.harness_mode or (run.schema == RUN_SCHEMA_V3) != (plan.schema == 4):
        raise ValueError("retained acquisition mode/schema differs from measurement policy")
    if release:
        from research_repo_tools.complete_runs import ratio_incompatibilities

        reasons = ratio_incompatibilities(evidence)
        if reasons:
            raise ValueError(f"release comparison is ineligible: {reasons}")
    if set(run.compatible) != set(plan.measurement.compatible):
        raise ValueError("retained compatibility fields differ from measurement policy")
    sources = dict(evidence.sources)
    pair = ReleasePair(dict(sources["current"].context)["release"], dict(sources["baseline"].context)["release"])
    if pair.current == pair.baseline and sources["current"].source_sha256 == sources["baseline"].source_sha256:
        raise ValueError("same-release comparisons require distinct source identities")
    if plan.schema == 4 and pair.current == pair.baseline and dict(sources["current"].context).get("mode") != "working-tree":
        raise ValueError("same-release acquisitions require a working-tree current source")
    if release and pair.current == pair.baseline:
        raise ValueError("same-release local comparisons cannot be promoted as release comparisons")
    policies = {}
    for phase, sample in run.phases:
        policies[phase] = _validate_phase_sample(plan, phase, sample, sources[phase])
    if run.series != _series(plan, policies):
        raise ValueError("retained series selections or phases differ from measurement policy")
    return run


def _validate_phase_sample(plan: CompleteMeasurementPlan, phase: str, sample: CompleteSample, source: Provenance) -> CompletePolicy:
    """Validate one available side without inventing an unavailable peer."""
    selected = getattr(plan, phase)
    policy = selected.policy
    context = dict(source.context)
    if plan.schema == 4:
        _validate_phase_identity(source, plan, phase)
        if any(tuple(name for name, _ in case.raw_files) != plan.raw_files for case in sample.cases):
            raise ValueError("retained raw files differ from acquisition declaration")
        if context.get("saved-sample") != (plan.saved_sample or "none"):
            raise ValueError("retained saved verification differs from acquisition declaration")
    if context.get("measurement-declaration") != _declaration(plan):
        raise ValueError(f"{phase} acquisition declaration differs from measurement policy or is missing")
    expected = policy.expected if isinstance(policy, CompletePolicy) else sample.policy.expected
    if isinstance(policy, DiscoveryPolicy):
        inventory = parse_inventory(context.get("discovery-inventory", "").encode("utf-8"))
        if set(dict(inventory.suites)) != {suite.name for suite in policy.discovery.suites} or inventory.excluded != policy.discovery.excluded:
            raise ValueError(f"{phase} discovery suites/exclusions differ from measurement policy")
        if not set(policy.discovery.required) <= set(inventory.expected):
            raise ValueError(f"{phase} retained discovery omits required IDs")
        expected = inventory.expected
    expected_policy = CompletePolicy(expected, policy.sample_count, policy.statistics, policy.confidence_level, policy.unit, sample_counts=policy.sample_counts)
    if sample.policy != expected_policy:
        raise ValueError(f"{phase} sampling policy differs from measurement policy")
    for series in plan.series:
        if series.phase != phase:
            continue
        resolved = series.resolve(sample.policy.expected) if isinstance(series, DiscoveredSeries) else series
        if any(full_id not in sample.policy.expected for _, full_id in resolved.rows):
            raise ValueError(f"{phase} series refers to an unmeasured benchmark")
    for key, value in (("command", list(selected.command)), ("gate-command", list(selected.gate)), ("environment", dict(selected.environment))):
        if json.loads(context[key]) != value:
            raise ValueError(f"{phase} {key} differs from measurement policy")
    if any(context.get(key) != value for key, value in plan.measurement.context):
        raise ValueError(f"{phase} context differs from measurement policy")
    return expected_policy


def _prepare_phases(
    baseline: Path,
    current: Path,
    plan: CompleteMeasurementPlan,
    pair: ReleasePair,
    *,
    working_tree: bool = False,
    captured_sources: dict[str, CapturedSource] | None = None,
    verify: Callable[[], None] | None = None,
    active: tuple[str, ...] = ("baseline", "current"),
    progress: ProgressReporter | None = None,
    compilation: dict[str, tuple[str, ...]] | None = None,
    on_sample: Callable[[str, CompleteSample, float], None] | None = None,
) -> tuple[dict[str, CompletePolicy], dict[str, Provenance], Callable[[], tuple[tuple[tuple[str, CompleteSample], ...], tuple[tuple[str, Provenance], ...]]]]:
    """Prepare a complete unit; return the same validated sampler used by v1-v3."""
    reporter = progress or ProgressReporter(mode="none")
    for phase in active:
        plan.measurement.validate_release(getattr(pair, phase))
    roots = {"baseline": baseline.resolve(strict=True), "current": current.resolve(strict=True)}
    if roots["baseline"] == roots["current"] or any(
        left in right.parents for left, right in ((roots["baseline"], roots["current"]), (roots["current"], roots["baseline"]))
    ):
        raise ValueError("measurement checkouts must be distinct and nonoverlapping")
    if plan.schema == 4 and (plan.backend == "export") != (captured_sources is not None):
        raise ValueError("prepared sources must match the declared export/worktree backend")
    if captured_sources is not None:
        if set(captured_sources) != set(roots):
            raise ValueError("captured sources must identify both phases")
        for phase, root in roots.items():
            receipt = captured_sources[phase]
            verify_export(receipt, root)
            receipt.verify()
    current = roots["current"]
    roots = {phase: root for phase, root in roots.items() if phase in active}
    harness_root = current if plan.harness_mode == "common" else next(iter(roots.values()))
    inventory = _inventory(harness_root, plan.harness)
    captured = {name: (_path(harness_root, name).read_bytes(), _path(harness_root, name).stat().st_mode & 0o777) for name in inventory}
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
    for root in roots.values() if plan.harness_mode == "common" else ():
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
    effective = {}
    if plan.schema == 4:
        for phase, root in roots.items():
            names = set(_inventory(root, (*plan.sources, *plan.harness, *plan.measurement.workloads)))
            if captured_sources is not None:
                names.update(name for name, _, _ in captured_sources[phase].files if (root / name).exists())
            effective[phase] = _files(root, tuple(sorted(names)))
    before: dict[str, Provenance] = {}
    declared: dict[str, Provenance] = {}
    configurations = {}
    environments = {}
    for phase, root in roots.items():
        selected = plan.baseline if phase == "baseline" else plan.current
        config = replace(plan.measurement, command=selected.command)
        configurations[phase] = config
        env = _overlay_environment(os.environ, selected.environment)
        environments[phase] = env
        before[phase] = capture_provenance(
            root,
            config,
            getattr(pair, phase),
            mode="working-tree" if phase == "current" and working_tree else "tag",
            env=env,
            source=captured_sources[phase] if captured_sources else None,
        )
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
                    "measurement-declaration": _declaration(plan),
                }.items()
            ),
        )
    early_fields = tuple(field for field in plan.measurement.compatible if field not in {"context.discovery-inventory", "context.sampling-policy"})
    compatibility = compare_provenance(declared[active[0]], declared[active[-1]], fields=early_fields)
    if plan.harness_mode == "common" and not compatibility.compatible:
        raise ValueError(f"required benchmark provenance differs or is unknown before timing: {compatibility.differences}")

    def unchanged(phase: str) -> None:
        if verify is not None:
            verify()
        if captured_sources is not None:
            captured_sources[phase].verify()
        if effective and _files(roots[phase], tuple(name for name, _, _ in effective[phase])) != effective[phase]:
            raise ValueError(f"{phase} captured inputs changed")
        source = before[phase]
        if (
            capture_provenance(
                roots[phase],
                configurations[phase],
                getattr(pair, phase),
                mode=dict(source.context)["mode"],
                env=environments[phase],
                source=captured_sources[phase] if captured_sources else None,
            )
            != source
        ):
            raise ValueError(f"{phase} measurement inputs or host/tool identity changed")

    policies: dict[str, CompletePolicy] = {}
    for phase, root in roots.items():
        if compilation and phase in compilation:
            command = compilation[phase]
            with reporter.stage("compilation", suite=reporter.suite, phase=phase):
                run_command_live(command[0], command[1:], cwd=root, env=environments[phase], timeout=plan.measurement.timeout)
            unchanged(phase)
            if (root / plan.measurement.criterion_dir).exists():
                raise ValueError("compilation produced timing output before measurement")
        policy = (plan.baseline if phase == "baseline" else plan.current).policy
        if isinstance(policy, DiscoveryPolicy):
            with reporter.stage("discovery", suite=reporter.suite, phase=phase):
                inventory = discover_inventory(root, policy.discovery, env=environments[phase])
            policies[phase] = CompletePolicy(
                inventory.expected, policy.sample_count, policy.statistics, policy.confidence_level, policy.unit, sample_counts=policy.sample_counts
            )
            rows = {full_id for series in plan.series if isinstance(series, RunSeries) and series.phase == phase for _, full_id in series.rows}
            if not rows <= set(inventory.expected):
                raise ValueError(f"{phase} discovered inventory omits series IDs: {sorted(rows - set(inventory.expected))}")
            source = declared[phase]
            declared[phase] = replace(
                source, context=(*source.context, ("discovery-inventory", json.dumps(json.loads(serialize_inventory(inventory)), sort_keys=True)))
            )
        else:
            policies[phase] = policy
        if plan.schema >= 3:
            source = declared[phase]
            declared[phase] = replace(source, context=(*source.context, ("sampling-policy", serialize_policy(policies[phase]).decode("utf-8"))))
        for selected in roots:
            unchanged(selected)
            if (roots[selected] / plan.measurement.criterion_dir).exists():
                raise ValueError("discovery produced timing output before measurement")

    compatibility = compare_provenance(declared[active[0]], declared[active[-1]], fields=plan.measurement.compatible)
    if plan.harness_mode == "common" and not compatibility.compatible:
        raise ValueError(f"required benchmark provenance differs or is unknown before timing: {compatibility.differences}")
    _series(plan, policies)
    if len(active) == 2 and pair.current == pair.baseline and before["baseline"].source_sha256 == before["current"].source_sha256:
        raise ValueError("same-release comparisons require distinct source identities")

    for phase, root in roots.items():
        gate = getattr(plan, phase).gate
        with reporter.stage("gate", suite=reporter.suite, phase=phase):
            result = run_command_live(gate[0], gate[1:], cwd=root, env=environments[phase], timeout=plan.measurement.timeout, check=False)
        if result.returncode:
            raise ValueError(f"{phase} preflight gate failed")
        for checked in roots:
            unchanged(checked)
        if any((directory / plan.measurement.criterion_dir).exists() for directory in roots.values()):
            raise ValueError("preflight gate produced timing output before measurement")

    def acquire() -> tuple[tuple[tuple[str, CompleteSample], ...], tuple[tuple[str, Provenance], ...]]:
        samples: list[tuple[str, CompleteSample]] = []
        sources: list[tuple[str, Provenance]] = []
        for phase, root in roots.items():
            selected = getattr(plan, phase)
            unchanged(phase)
            if (root / plan.measurement.criterion_dir).exists():
                raise ValueError(f"{phase} timing output appeared before its measurement")
            started = time.monotonic()
            with reporter.stage("acquisition", suite=reporter.suite, phase=phase):
                result = run_command_live(selected.command[0], selected.command[1:], cwd=root, env=environments[phase], timeout=plan.measurement.timeout)
            if result.returncode:
                raise ValueError(f"{phase} measurement failed")
            criterion = root / plan.measurement.criterion_dir
            if plan.saved_sample is not None:
                from research_repo_tools.criterion_baselines import validate_saved_sample

                validate_saved_sample(
                    criterion,
                    plan.saved_sample,
                    policies[phase],
                    fresh_sample=plan.measurement.sample,
                    raw_files=("benchmark.json", "estimates.json", "sample.json", *plan.raw_files),
                )
            samples.append((phase, collect_complete_sample(criterion, plan.measurement.sample, policies[phase], raw_files=plan.raw_files)))
            unchanged(phase)
            source = declared[phase]
            context = {**dict(source.context), "gate-status": "passed", **({"saved-sample": plan.saved_sample or "none"} if plan.schema == 4 else {})}
            sources.append((phase, Provenance(source.revision, source.source_sha256, source.harness_sha256, tuple(context.items()))))
            if on_sample is not None:
                on_sample(phase, samples[-1][1], time.monotonic() - started)
        for phase, root in roots.items():
            unchanged(phase)
            criterion = root / plan.measurement.criterion_dir
            retained_sample = dict(samples)[phase]
            if collect_complete_sample(criterion, plan.measurement.sample, policies[phase], raw_files=plan.raw_files) != retained_sample:
                raise ValueError(f"{phase} raw observations changed before retention")
            if plan.saved_sample is not None:
                from research_repo_tools.criterion_baselines import validate_saved_sample

                validate_saved_sample(
                    criterion,
                    plan.saved_sample,
                    policies[phase],
                    fresh_sample=plan.measurement.sample,
                    raw_files=("benchmark.json", "estimates.json", "sample.json", *plan.raw_files),
                )
        return tuple(samples), tuple(sources)

    return policies, declared, acquire


def measure_prepared_pair(
    baseline: Path,
    current: Path,
    plan: CompleteMeasurementPlan,
    pair: ReleasePair,
    *,
    working_tree: bool = False,
    captured_sources: dict[str, CapturedSource] | None = None,
    verify: Callable[[], None] | None = None,
    progress: ProgressReporter | None = None,
) -> Evidence:
    """Measure two disposable, caller-owned checkouts using captured current files.

    Commands are trusted consumer code. Both gates pass independently before any
    timing. Retained raw files survive checkout cleanup. No Git state is mutated by
    this function; measure_pair owns optional worktree lifecycle.
    """
    reporter = progress or ProgressReporter(mode="none")

    def accepted_sample(phase: str, sample: CompleteSample, elapsed: float) -> None:
        reporter.accept(phase, len(sample.cases), elapsed)

    policies, _declared, acquire = _prepare_phases(
        baseline, current, plan, pair, working_tree=working_tree, captured_sources=captured_sources, verify=verify, progress=reporter, on_sample=accepted_sample
    )
    # Historical plans retain separate work classes: they do not declare the
    # suite-level contract needed to extrapolate timing between phases.
    reporter.budget({phase: len(policy.expected) for phase, policy in policies.items()})
    samples, sources = acquire()
    series = _series(plan, policies)

    run = CompleteRun(
        tuple(samples),
        series,
        plan.measurement.compatible,
        schema=RUN_SCHEMA_V3 if plan.schema == 4 else RUN_SCHEMA_V2 if plan.schema == 3 else RUN_SCHEMA,
        harness_mode=plan.harness_mode,
    )
    retained = Evidence(serialize_run(run), run.schema, tuple(sources))
    validate_run_policy(retained, plan)
    return retained


def _plan_document(plan: CompleteMeasurementPlan) -> dict[str, Any]:
    config = plan.measurement
    document: dict[str, Any] = {
        "schema": 4,
        "harness-mode": plan.harness_mode,
        "backend": plan.backend,
        "sources": config.sources,
        "harness": config.harness,
        "workloads": config.workloads,
        "raw-files": plan.raw_files,
        "criterion-dir": config.criterion_dir,
        "sample": config.sample,
        "timeout": config.timeout,
        "probes": dict(config.probes),
        "dependencies": dict(config.dependencies),
        "context": dict(config.context),
        "compatible": tuple(sorted(config.compatible)),
        "statistics": plan.baseline.policy.statistics,
        "confidence-level": plan.baseline.policy.confidence_level,
        "unit": plan.baseline.policy.unit,
        "phases": {},
        "series": [],
    }
    if config.minimum_release is not None:
        document["minimum-release"] = config.minimum_release
    if plan.saved_sample is not None:
        document["saved-sample"] = plan.saved_sample
    for phase in ("baseline", "current"):
        selected = getattr(plan, phase)
        policy = selected.policy
        entry: dict[str, Any] = {"command": selected.command, "gate": selected.gate, "environment": dict(selected.environment)}
        counts = policy.sample_counts
        if counts:
            entry["sample-counts"] = dict(counts)
        else:
            entry["sample-count"] = policy.sample_count
        if isinstance(policy, CompletePolicy):
            entry["expected"] = policy.expected
        else:
            entry["discovery"] = {
                "required": policy.discovery.required,
                "excluded": policy.discovery.excluded,
                "suites": [
                    {"name": suite.name, "command": suite.command, "cwd": suite.cwd, "environment": dict(suite.environment), "timeout": suite.timeout}
                    for suite in policy.discovery.suites
                ],
            }
        document["phases"][phase] = entry
    for series in plan.series:
        entry = {"name": series.name, "phase": series.phase}
        if isinstance(series, RunSeries):
            entry["rows"] = dict(series.rows)
        else:
            entry["select"] = {"include": series.include, "exclude": series.exclude}
        document["series"].append(entry)
    return document


def validate_acquisition(evidence: Evidence, run: CompleteRun) -> None:
    """Validate v3 self-description; independent release policy is still required."""
    sources = dict(evidence.sources)
    declarations = [dict(sources[phase].context).get("measurement-declaration") for phase in ("baseline", "current")]
    if declarations[0] is None or declarations[0] != declarations[1]:
        raise ValueError("complete acquisition requires matching phase declarations")
    plan = parse_plan(_object(json.loads(declarations[0]), "acquisition declaration"))
    if plan.schema != 4:
        raise ValueError("v3 evidence requires a schema-4 acquisition declaration")
    if plan.backend == "export":
        from research_repo_tools.source_exports import validate_source_record

        for source in sources.values():
            context = dict(source.context)
            if "source-export" not in context:
                raise ValueError("export acquisition requires captured source provenance")
            validate_source_record(context["source-export"], source.revision)
    _validate_policy(evidence, plan, run)


def _validate_phase_identity(source: Provenance, plan: CompleteMeasurementPlan, phase: str) -> None:
    """Required per-phase identity is acquisition proof, not cross-phase equality."""
    from research_repo_tools.host_metadata import parse_host
    from research_repo_tools.source_exports import _names

    context = dict(source.context)
    if source.harness_sha256 is None:
        raise ValueError(f"{phase} acquisition is missing its harness identity")
    if context.get("mode") not in {"tag", "working-tree"} or (phase == "baseline" and context.get("mode") != "tag"):
        raise ValueError("invalid acquisition source mode/phase")
    if plan.backend == "worktree" and "source-export" in context:
        raise ValueError("worktree declaration cannot qualify exported source evidence")
    host = parse_host(context["host"].encode("utf-8"))
    declared_tools = {name for name, _ in plan.measurement.probes}
    recorded_tools = {name.removeprefix("tool.") for name in context if name.startswith("tool.")}
    if recorded_tools != declared_tools or set(dict(host.tools)) != declared_tools:
        raise ValueError(f"{phase} acquisition tool inventory differs from declaration")
    for name, observed in host.tools:
        if observed is None or json.loads(context["tool." + name]) != observed:
            raise ValueError(f"{phase} acquisition tool observation is missing or inconsistent")
    declared_dependencies = {name for name, _ in plan.measurement.dependencies}
    if {name.removeprefix("dependency.") for name in context if name.startswith("dependency.")} != declared_dependencies:
        raise ValueError(f"{phase} acquisition dependency inventory differs from declaration")
    for name in declared_dependencies:
        _string(context["dependency." + name], "dependency observation")
    inventories = {}
    for key in ("source-inventory", "harness-inventory", "original-source-inventory"):
        names = _strings(json.loads(context[key]), key)
        _names(None, names)
        inventories[key] = names
    modes = _array(json.loads(context.get("harness-modes", "null")), "harness modes")
    if any(not isinstance(item, list) or len(item) != 2 or type(item[1]) is not int or not 0 <= item[1] <= 0o777 for item in modes):
        raise ValueError("invalid captured harness modes")
    if tuple(item[0] for item in modes) != inventories["harness-inventory"]:
        raise ValueError("captured harness mode inventory differs")
    if plan.measurement.workloads:
        _digest(context.get("workload-sha256"), "workload identity")
        _names(None, _strings(json.loads(context.get("workload-inventory", "null")), "workload inventory"))
    elif {"workload-sha256", "workload-inventory"} & context.keys():
        raise ValueError("workload identity requires declared workload files")
    if plan.harness_mode == "independent" and (
        source.source_sha256 != context["original-source-sha256"] or inventories["source-inventory"] != inventories["original-source-inventory"]
    ):
        raise ValueError("independent acquisition must retain its original source identity")
    if plan.backend == "export":
        receipt = _object(json.loads(context.get("source-export", "null")), "source export")
        if receipt.get("mode") != ("working-tree" if context["mode"] == "working-tree" else "revision"):
            raise ValueError("export source mode differs from acquisition")
        names = {item[0] for item in _array(receipt["files"], "source files")}
        required = set(inventories["original-source-inventory"])
        if plan.harness_mode == "independent":
            required.update(inventories["harness-inventory"])
        if not required <= names:
            raise ValueError("acquisition inputs are absent from the captured source")
