"""Common-harness plans composed with the existing measurement/worktree engine."""

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from research_repo_tools.complete_runs import (
    RUN_SCHEMA,
    CompletePolicy,
    CompleteRun,
    RunSeries,
    _array,
    _integer,
    collect_complete_sample,
    serialize_run,
    validate_run_evidence,
)
from research_repo_tools.criterion import _positive, _statistic, _unit
from research_repo_tools.evidence import Evidence, Provenance, _object, _string, compare_provenance, fingerprint_files
from research_repo_tools.measurement import MeasurementConfig, _strings, capture_provenance
from research_repo_tools.process import run_command_live
from research_repo_tools.publication import _inventory, _path
from research_repo_tools.publication_config import _table
from research_repo_tools.release_pairs import ReleasePair

__all__ = ["CommonHarnessPlan", "MeasurementPhase", "measure_prepared_pair"]


@dataclass(frozen=True, slots=True)
class MeasurementPhase:
    command: tuple[str, ...]
    gate: tuple[str, ...]
    policy: CompletePolicy
    environment: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for key in ("command", "gate"):
            if isinstance(getattr(self, key), str):
                raise ValueError(f"{key} must be an argument vector")
            object.__setattr__(self, key, _strings(list(getattr(self, key)), key))
        if not isinstance(self.policy, CompletePolicy):
            raise TypeError("phase requires a completeness policy")
        environment = tuple(sorted(tuple(item) for item in self.environment))
        if len({key.casefold() for key, _ in environment}) != len(environment):
            raise ValueError("environment keys must be unique on every platform")
        for key, value in environment:
            _string(key, "environment key")
            if "=" in key or not isinstance(value, str) or "\0" in value:
                raise ValueError("invalid environment entry")
        object.__setattr__(self, "environment", environment)


@dataclass(frozen=True, slots=True)
class CommonHarnessPlan:
    measurement: MeasurementConfig
    baseline: MeasurementPhase
    current: MeasurementPhase
    series: tuple[RunSeries, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.measurement, MeasurementConfig) or any(not isinstance(phase, MeasurementPhase) for phase in (self.baseline, self.current)):
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
        if any(not isinstance(series, RunSeries) for series in self.series):
            raise TypeError("plan series must be RunSeries instances")
        if not self.series or len({series.name for series in self.series}) != len(self.series):
            raise ValueError("plan series must have unique names")
        for series in self.series:
            if any(full_id not in getattr(self, series.phase).policy.expected for _, full_id in series.rows):
                raise ValueError("series refers to an unexpected benchmark")
        left, right = self.baseline.policy, self.current.policy
        if replace(left, expected=right.expected) != right:
            raise ValueError("phases must share sample count, statistics, confidence and unit")
        object.__setattr__(self, "series", tuple(self.series))

    @property
    def sources(self) -> tuple[str, ...]:
        return self.measurement.sources

    @property
    def harness(self) -> tuple[str, ...]:
        return self.measurement.harness


def parse_plan(document: dict) -> CommonHarnessPlan:
    raw = _table(
        document,
        "common measurement",
        {"schema", "sources", "harness", "phases", "series"},
        {"criterion-dir", "sample", "sample-count", "statistics", "confidence-level", "unit", "timeout", "probes", "dependencies", "context", "compatible"},
    )
    phases = _object(raw["phases"], "phases", {"baseline", "current"})
    parsed = {}
    for name, data in phases.items():
        phase = _table(data, name, {"command", "gate", "expected"}, {"environment"})
        policy = CompletePolicy(
            _strings(phase["expected"], "expected"),
            _integer(raw.get("sample-count", 100), "sample count"),
            tuple(_statistic(item) for item in _array(raw.get("statistics", ["mean", "median"]), "statistics")),
            _positive(raw.get("confidence-level", 0.95), "confidence level"),
            _unit(raw.get("unit", "ns")),
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
    return CommonHarnessPlan(config, parsed["baseline"], parsed["current"], tuple(series))


def _environment_value(value: object) -> str:
    if not isinstance(value, str) or "\0" in value:
        raise ValueError("environment value must be text without NUL")
    return value


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
        # Fold keys when overlaying so Windows and POSIX reject ambiguous plans.
        keys = {key.casefold() for key, _ in selected.environment}
        env = {key: value for key, value in os.environ.items() if key.casefold() not in keys}
        env.update(selected.environment)
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
    compatibility = compare_provenance(declared["baseline"], declared["current"], fields=plan.measurement.compatible)
    if not compatibility.compatible:
        raise ValueError(f"required benchmark provenance differs or is unknown before timing: {compatibility.differences}")

    def unchanged(phase: str) -> None:
        source = before[phase]
        if capture_provenance(roots[phase], configurations[phase], getattr(pair, phase), mode=dict(source.context)["mode"], env=environments[phase]) != source:
            raise ValueError(f"{phase} measurement inputs or host/tool identity changed")

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
        samples.append((phase, collect_complete_sample(root / plan.measurement.criterion_dir, plan.measurement.sample, selected.policy)))
        unchanged(phase)
        source = declared[phase]
        context = {**dict(source.context), "gate-status": "passed"}
        sources.append((phase, Provenance(source.revision, source.source_sha256, source.harness_sha256, tuple(context.items()))))
    for phase in roots:
        unchanged(phase)
    retained = Evidence(serialize_run(CompleteRun(tuple(samples), plan.series, plan.measurement.compatible)), RUN_SCHEMA, tuple(sources))
    validate_run_evidence(retained)
    return retained
