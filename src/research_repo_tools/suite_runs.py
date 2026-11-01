"""Complete suite evidence with explicit absence and observation-level ratios."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_repo_tools.common_measurement import DiscoveredSeries, _validate_phase_sample
from research_repo_tools.complete_runs import (
    CompleteCase,
    CompleteSample,
    _integer,
    _parse_complete_sample,
    _sample_document,
    _validate_sample_source,
    parse_policy,
)
from research_repo_tools.criterion import Comparison, Statistic, Unit, _format_estimate, _markdown_text
from research_repo_tools.evidence import Evidence, Provenance, _load_json, _object, _string, compare_provenance, deterministic_json, fingerprint_files
from research_repo_tools.host_metadata import parse_host
from research_repo_tools.measurement import _strings
from research_repo_tools.release_pairs import ReleasePair
from research_repo_tools.source_exports import validate_source_record
from research_repo_tools.suite_measurement import AcquisitionSuite, SuiteMeasurementPlan, _comparison_fields, parse_suite_plan

__all__ = ["SUITE_RUN_SCHEMA", "SuiteRun", "comparison_incompatibilities", "parse_suite_run"]

SUITE_RUN_SCHEMA = "research-repo-tools/complete-run/v4"


def sample_document(sample: CompleteSample) -> dict[str, object]:
    return _object(_load_json(deterministic_json(_sample_document(sample)), "complete sample"), "complete sample")


def _source(value: object) -> Provenance:
    raw = _object(value, "unit provenance", {"revision", "source_sha256", "harness_sha256", "context"})
    return Provenance(
        _string(raw["revision"], "revision"),
        _string(raw["source_sha256"], "source digest"),
        _string(raw["harness_sha256"], "harness digest"),
        tuple((key, _string(value, key)) for key, value in _object(raw["context"], "source context").items()),
    )


def validate_unit(
    value: object,
    suite: AcquisitionSuite,
    decisions: dict[str, Any],
    prepared: dict[str, Provenance] | None = None,
) -> tuple[tuple[str, CompleteSample, Provenance], ...]:
    """Validate a whole paired unit; unavailable sides have no sample object."""
    unit = _object(value, "acquisition unit", {"name", "contract", "availability", "session", "started_ns", "finished_ns", "observations"})
    if unit["name"] != suite.name or unit["contract"] != suite.contract or unit["availability"] != decisions:
        raise ValueError("unit declaration or availability differs")
    if not isinstance(unit["session"], str) or re.fullmatch(r"[0-9a-f]{32}", unit["session"]) is None:
        raise ValueError("unit requires a collection session identity")
    started, finished = (_integer(unit[key], key) for key in ("started_ns", "finished_ns"))
    if started < 0 or finished < started:
        raise ValueError("unit collection interval is invalid")
    observations = _object(unit["observations"], "unit observations")
    active = {phase for phase, decision in decisions.items() if decision["state"] == "available"}
    if set(observations) != active:
        raise ValueError("unit must contain exactly its available sides")
    parsed = []
    for phase, value in observations.items():
        observation = _object(value, "unit observation", {"sample", "source"})
        sample = _parse_complete_sample(observation["sample"])
        source = _source(observation["source"])
        context = dict(source.context)
        _validate_sample_source(source, phase)
        if context.get("release") != decisions[phase]["release"] or json.loads(context.get("source-export", "null")) != decisions[phase]["source"]:
            raise ValueError("unit source differs from independently resolved availability")
        validate_source_record(context["source-export"], source.revision)
        _validate_phase_sample(suite.measurement, phase, sample, source)
        if parse_policy(context.get("sampling-policy", "").encode("utf-8")) != sample.policy:
            raise ValueError("unit retained sampling policy differs from its complete sample")
        if prepared is not None:
            expected = prepared[phase]
            actual_context = {key: value for key, value in context.items() if key not in {"gate-status", "saved-sample"}}
            if (source.revision, source.source_sha256, source.harness_sha256, actual_context) != (
                expected.revision,
                expected.source_sha256,
                expected.harness_sha256,
                dict(expected.context),
            ):
                raise ValueError(f"{suite.name}/{phase} checkpoint observations differ from current independently prepared inputs")
        parsed.append((phase, sample, source))
    if len(parsed) == 2 and suite.measurement.harness_mode == "common":
        compatible = compare_provenance(parsed[0][2], parsed[1][2], fields=suite.measurement.measurement.compatible)
        if not compatible.compatible:
            raise ValueError(f"common suite provenance differs: {compatible.differences}")
    return tuple(parsed)


@dataclass(frozen=True, slots=True)
class PhasePolicyView:
    """Derived inventory view, including an absent phase; never a CompletePolicy."""

    expected: tuple[str, ...]
    statistics: tuple[Statistic, ...]
    confidence_level: float
    unit: Unit
    sample_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class PhaseView:
    policy: PhasePolicyView
    cases: tuple[CompleteCase, ...]


@dataclass(frozen=True, slots=True)
class SeriesView:
    name: str
    phase: str
    rows: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class SuiteRun:
    """Authoritative units plus derived presentation views; no synthetic samples."""

    plan: SuiteMeasurementPlan
    units: tuple[dict[str, Any], ...]
    observations: tuple[tuple[str, str, CompleteSample, Provenance], ...]

    @property
    def schema(self) -> str:
        return SUITE_RUN_SCHEMA

    @property
    def harness_mode(self) -> str:
        return self.plan.harness_mode

    @property
    def phases(self) -> tuple[tuple[str, PhaseView], ...]:
        settings = self.plan.suites[0].measurement.baseline.policy
        result = []
        for phase in ("baseline", "current"):
            cases = tuple(sorted((case for _, side, sample, _ in self.observations if side == phase for case in sample.cases), key=lambda case: case.full_id))
            result.append(
                (
                    phase,
                    PhaseView(
                        PhasePolicyView(
                            tuple(case.full_id for case in cases),
                            settings.statistics,
                            settings.confidence_level,
                            settings.unit,
                            tuple((case.full_id, case.sample_count) for case in cases),
                        ),
                        cases,
                    ),
                )
            )
        return tuple(result)

    @property
    def series(self) -> tuple[SeriesView, ...]:
        samples = {(suite, phase): sample for suite, phase, sample, _ in self.observations}
        result = []
        for suite in self.plan.suites:
            for series in suite.measurement.series:
                sample = samples.get((suite.name, series.phase))
                rows = () if sample is None else series.resolve(sample.policy.expected).rows if isinstance(series, DiscoveredSeries) else series.rows
                result.append(SeriesView(f"{suite.name}/{series.name}", series.phase, rows))
        return tuple(result)

    def observation(self, phase: str, full_id: str) -> tuple[str, CompleteSample, Provenance]:
        found = [(suite, sample, source) for suite, side, sample, source in self.observations if side == phase and full_id in sample.policy.expected]
        if len(found) != 1:
            raise ValueError(f"observation is unavailable or ambiguous: {phase}/{full_id}")
        return found[0]


def parse_suite_run(evidence: Evidence) -> SuiteRun:
    if evidence.payload_schema != SUITE_RUN_SCHEMA:
        raise ValueError("requires complete-run v4 evidence")
    raw = _object(_load_json(evidence.payload, "suite run"), "suite run", {"schema", "declaration", "units", "reused"})
    if raw["schema"] != SUITE_RUN_SCHEMA or not isinstance(raw["units"], list):
        raise ValueError("invalid suite run schema/units")
    plan = parse_suite_plan(_object(raw["declaration"], "suite declaration"))
    sources = dict(evidence.sources)
    if set(sources) != {"baseline", "current"}:
        raise ValueError("suite run requires two independent source assertions")
    releases = {phase: dict(source.context).get("release", "") for phase, source in sources.items()}
    pair = ReleasePair(releases["current"], releases["baseline"])
    receipts = {}
    for phase, source in sources.items():
        context = dict(source.context)
        receipt = context.get("source-export", "")
        validate_source_record(receipt, source.revision)
        record = json.loads(receipt)
        if context.get("mode") != ("working-tree" if record["mode"] == "working-tree" else "tag"):
            raise ValueError("suite envelope source mode differs from its receipt")
        if record["sha256"] != source.source_sha256:
            raise ValueError("suite envelope source identity differs")
        receipts[phase] = receipt
    if pair.current == pair.baseline and (
        sources["current"].source_sha256 == sources["baseline"].source_sha256 or json.loads(receipts["current"])["mode"] != "working-tree"
    ):
        raise ValueError("same-release suite runs require a distinct working-tree source")
    if json.loads(receipts["baseline"])["mode"] != "revision":
        raise ValueError("baseline requires a revision source")
    units = tuple(_object(item, "unit") for item in raw["units"])
    reused = raw["reused"]
    if (
        not isinstance(reused, list)
        or any(not isinstance(name, str) for name in reused)
        or len(set(reused)) != len(reused)
        or not set(reused) <= {suite.name for suite in plan.suites}
    ):
        raise ValueError("retained unit attribution must name unique declared units")
    if [item.get("name") for item in units] != [item.name for item in plan.suites]:
        raise ValueError("suite unit inventory/order differs from declaration")
    observations = []
    owners: set[tuple[str, str]] = set()
    for suite, unit in zip(plan.suites, units, strict=True):
        decisions = {phase: suite.decision(releases[phase], receipts[phase]) for phase in sources}
        for phase, sample, source in validate_unit(unit, suite, decisions):
            for case in sample.cases:
                owner = (phase, case.full_id)
                if owner in owners:
                    raise ValueError("duplicate benchmark ownership across units")
                owners.add(owner)
            observations.append((suite.name, phase, sample, source))
    return SuiteRun(plan, units, tuple(observations))


def comparison_incompatibilities(evidence: Evidence, numerator: tuple[str, str], denominator: tuple[str, str]) -> tuple[str, ...]:
    """Explain eligibility for (phase, native full ID) observations, including peers."""
    return comparison_for_run(parse_suite_run(evidence), numerator, denominator)


def comparison_for_run(run: SuiteRun, numerator: tuple[str, str], denominator: tuple[str, str]) -> tuple[str, ...]:
    a, left, first = run.observation(*numerator)
    b, right, second = run.observation(*denominator)
    reasons = []
    if a != b:
        return ("observations belong to different collection units; cross-unit comparisons are absolute-only",)
    suite = next(item for item in run.plan.suites if item.name == a)
    if numerator[0] != denominator[0] and numerator[1] != denominator[1]:
        reasons.append("workload IDs differ within the semantic contract")
    if numerator[0] != denominator[0]:
        compilation = dict(suite.compilation)
        if compilation.get(numerator[0]) != compilation.get(denominator[0]):
            reasons.append("compilation commands differ; flag equivalence is not established")
    # Defaults do not demand whole-inventory equality. Explicit consumer
    # compatibility constraints remain authoritative, including stricter ones.
    result = compare_provenance(first, second, fields=_comparison_fields(suite))
    reasons.extend(f"{item.field}: differs or is unknown" for item in result.differences)
    if left.policy.count_for(numerator[1]) != right.policy.count_for(denominator[1]):
        reasons.append("sample counts differ")
    for source in (first, second):
        host = parse_host(dict(source.context)["host"].encode("utf-8"))
        if any(getattr(host, key) is None for key in ("os", "architecture", "cpu", "physical_cores", "logical_threads", "memory_bytes")):
            reasons.append("required host observations are unknown")
    return tuple(dict.fromkeys(reasons))


def validate_suite_policy(evidence: Evidence, plan: SuiteMeasurementPlan, *, release: bool = False) -> SuiteRun:
    run = parse_suite_run(evidence)
    if deterministic_json(run.plan.document()) != deterministic_json(plan.document()):
        raise ValueError("suite acquisition declaration changed")
    if release:
        sources = dict(evidence.sources)
        pair = ReleasePair(dict(sources["current"].context)["release"], dict(sources["baseline"].context)["release"])
        if pair.current == pair.baseline:
            raise ValueError("same-release acquisitions cannot be promoted as release comparisons")
        shared = set(dict(run.phases)["baseline"].policy.expected) & set(dict(run.phases)["current"].policy.expected)
        eligible = 0
        for full_id in shared:
            a, _, _ = run.observation("baseline", full_id)
            b, _, _ = run.observation("current", full_id)
            if a != b:
                continue
            reasons = comparison_for_run(run, ("baseline", full_id), ("current", full_id))
            if reasons:
                raise ValueError(f"release workload {full_id} is ineligible: {reasons}")
            eligible += 1
        if not eligible:
            raise ValueError("release comparison has no eligible shared workloads; retain as absolute evidence")
    return run


def render_suite_run(evidence: Evidence, *, title: str, links: str, identity: str) -> bytes:
    run = parse_suite_run(evidence)
    lines = [
        "<!-- research-repo-tools/run-report/v2 -->",
        f"# {_markdown_text(title)}",
        "",
        f"Run: `{identity}`",
        "",
        links,
        "",
        "Ratios are point estimates within one accepted paired suite. Marginal timing intervals do not establish significance.",
    ]
    observations = {(name, phase): (sample, source) for name, phase, sample, source in run.observations}
    reused = _strings(_object(_load_json(evidence.payload, "suite run"), "suite run")["reused"], "reused units", nonempty=False)
    for suite, unit in zip(run.plan.suites, run.units, strict=True):
        lines.extend(
            [
                "",
                f"## {_markdown_text(suite.name)}",
                "",
                f"Semantic contract: {_markdown_text(suite.contract)}. Collection session: `{unit['session']}`.",
                f"Collection interval (Unix ns): {unit['started_ns']}–{unit['finished_ns']}.",
                "Acquisition: " + ("retained from validated checkpoint." if suite.name in reused else "newly collected."),
            ]
        )
        for phase in ("baseline", "current"):
            decision = unit["availability"][phase]
            lines.append(f"- {phase}: {decision['state']} — {decision['reason']}; release {decision['release']}; revision `{decision['source']['revision']}`.")
            if (suite.name, phase) in observations:
                _, source = observations[(suite.name, phase)]
                lines.append(f"  Source SHA-256: `{source.source_sha256}`; harness SHA-256: `{source.harness_sha256}`; scientific gate: passed.")
        policy = suite.measurement.baseline.policy
        cases = {
            phase: {case.full_id: case for case in observations[(suite.name, phase)][0].cases} if (suite.name, phase) in observations else {}
            for phase in ("baseline", "current")
        }
        for statistic in policy.statistics:
            lines.extend(
                ["", f"### {statistic.title()} ({policy.unit})", "", "| Workload | Baseline | Current | Baseline / current |", "| --- | --- | --- | --- |"]
            )
            for full_id in sorted(set(cases["baseline"]) | set(cases["current"])):
                cells = [_markdown_text(full_id)]
                for phase in ("baseline", "current"):
                    case = cases[phase].get(full_id)
                    cells.append(_format_estimate(case.estimate(statistic), policy.unit) if case else "unavailable")
                reasons = (
                    comparison_for_run(run, ("baseline", full_id), ("current", full_id))
                    if all(full_id in cases[phase] for phase in cases)
                    else ("one-sided workload",)
                )
                cells.append(
                    "unavailable — " + _markdown_text("; ".join(reasons))
                    if reasons
                    else f"{Comparison(full_id, cases['baseline'][full_id].estimate(statistic), cases['current'][full_id].estimate(statistic)).speedup:.6g}"
                )
                lines.append("| " + " | ".join(cells) + " |")
    return ("\n".join(lines) + "\n").encode("utf-8")


def publication_inputs(root: Path, evidence: Evidence):
    """Capture current declared scientific inputs for ordinary publication checks."""
    from research_repo_tools.publication import GlobCheck, _inventory, _path

    run = parse_suite_run(evidence)
    inputs: dict[str, bytes] = {}
    checks = []
    suites = {suite.name: suite for suite in run.plan.suites}
    for name, phase, _sample, source in run.observations:
        suite = suites[name]
        context = dict(source.context)
        selections = []
        if phase == "current":
            selections.extend(
                [
                    (suite.measurement.sources, "source-inventory", source.source_sha256),
                    (suite.measurement.measurement.workloads, "workload-inventory", context["workload-sha256"]),
                ]
            )
        if phase == "current" or suite.measurement.harness_mode == "common":
            selections.append((suite.measurement.harness, "harness-inventory", source.harness_sha256))
        for patterns, key, expected in selections:
            inventory = _inventory(root, patterns)
            if list(inventory) != json.loads(context[key]) or fingerprint_files(root, tuple(map(Path, inventory))) != expected:
                raise ValueError(f"current {name} {key} differs from measured inputs")
            checks.append(GlobCheck(patterns, inventory))
            inputs.update({path: _path(root, path).read_bytes() for path in inventory})
            if fingerprint_files(root, tuple(map(Path, inventory))) != expected:
                raise ValueError("suite publication inputs changed during planning")
    return inputs, checks
