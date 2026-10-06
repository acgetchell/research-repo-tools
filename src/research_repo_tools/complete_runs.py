"""Complete Criterion runs with semantic identities and phase-owned named series."""

import base64
import binascii
import hashlib
from dataclasses import dataclass
from pathlib import Path

from research_repo_tools.criterion import Estimate, Statistic, Unit, _format_estimate, _markdown_text, _positive, _statistic, _unit, parse_estimate
from research_repo_tools.evidence import Evidence, Provenance, _digest, _load_json, _object, _string, compare_provenance, deterministic_json, serialize_evidence
from research_repo_tools.publication import _publication_name
from research_repo_tools.release_pairs import ReleasePair

__all__ = [
    "RUN_SCHEMA",
    "CompleteCase",
    "CompletePolicy",
    "CompleteRun",
    "CompleteSample",
    "RunSeries",
    "collect_complete_sample",
    "parse_run",
    "render_run",
    "run_identity",
    "serialize_run",
    "validate_run_evidence",
]

RUN_SCHEMA = "research-repo-tools/complete-run/v1"


def _array(value: object, context: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    return value


def _integer(value: object, context: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{context} must be an integer")
    return value


def _names(values, context: str) -> tuple[str, ...]:
    if isinstance(values, str):
        raise ValueError(f"{context} requires a sequence")
    result = tuple(_string(item, context) for item in values)
    if not result or len(set(result)) != len(result):
        raise ValueError(f"{context} must be nonempty and unique")
    return result


@dataclass(frozen=True, slots=True)
class CompletePolicy:
    expected: tuple[str, ...]
    sample_count: int = 100
    statistics: tuple[Statistic, ...] = ("mean", "median")
    confidence_level: float = 0.95
    unit: Unit = "ns"

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected", tuple(sorted(_names(self.expected, "expected full IDs"))))
        object.__setattr__(self, "statistics", tuple(sorted(_statistic(item) for item in _names(self.statistics, "statistics"))))
        if type(self.sample_count) is not int or self.sample_count < 2:
            raise ValueError("sample count must be an integer of at least two")
        level = _positive(self.confidence_level, "confidence level")
        if level >= 1:
            raise ValueError("confidence level must lie strictly between zero and one")
        object.__setattr__(self, "confidence_level", level)
        _unit(self.unit)


@dataclass(frozen=True, slots=True)
class CompleteCase:
    """Original Criterion files; semantic ID and portable storage path stay separate."""

    storage_path: str
    benchmark: bytes
    estimates: bytes
    sample: bytes

    def __post_init__(self) -> None:
        _publication_name(self.storage_path)
        if any(not isinstance(value, bytes) for value in (self.benchmark, self.estimates, self.sample)):
            raise TypeError("Criterion files must be immutable bytes")
        _ = self.full_id
        raw = _object(_load_json(self.sample, "Criterion raw sample"), "Criterion raw sample")
        iters, times = (_array(raw.get(key), key) for key in ("iters", "times"))
        if len(iters) < 2 or len(iters) != len(times):
            raise ValueError("raw sample requires matching iterations and times with at least two samples")
        for iterations, elapsed in zip(iters, times, strict=True):
            count = _positive(iterations, "sample iterations")
            if not count.is_integer():
                raise ValueError("sample iterations must be whole numbers")
            _positive(_positive(elapsed, "sample time") / count, "sample time per iteration")
        _object(_load_json(self.estimates, "Criterion estimates"), "Criterion estimates")

    @property
    def full_id(self) -> str:
        return _string(_object(_load_json(self.benchmark, "Criterion benchmark"), "Criterion benchmark").get("full_id"), "Criterion full_id")

    @property
    def sample_count(self) -> int:
        return len(_array(_object(_load_json(self.sample, "Criterion sample"), "Criterion sample")["iters"], "iters"))

    def estimate(self, statistic: Statistic) -> Estimate:
        return parse_estimate(self.estimates, statistic=statistic)


@dataclass(frozen=True, slots=True)
class CompleteSample:
    policy: CompletePolicy
    cases: tuple[CompleteCase, ...]

    def __post_init__(self) -> None:
        cases = tuple(self.cases)
        if not isinstance(self.policy, CompletePolicy) or any(not isinstance(case, CompleteCase) for case in cases):
            raise TypeError("complete samples require a policy and complete cases")
        ids = _names((case.full_id for case in cases), "measured full IDs")
        _names((case.storage_path for case in cases), "storage paths")
        if set(ids) != set(self.policy.expected):
            raise ValueError(
                f"benchmark inventory differs: missing={sorted(set(self.policy.expected) - set(ids))}, unexpected={sorted(set(ids) - set(self.policy.expected))}"
            )
        for case in cases:
            if case.sample_count != self.policy.sample_count:
                raise ValueError(f"{case.full_id}: expected {self.policy.sample_count} raw samples, got {case.sample_count}")
            for statistic in self.policy.statistics:
                estimate = case.estimate(statistic)
                if estimate.confidence_level != self.policy.confidence_level:
                    raise ValueError(f"{case.full_id}: {statistic} requires a complete interval at confidence {self.policy.confidence_level}")
        object.__setattr__(self, "cases", tuple(sorted(cases, key=lambda case: case.full_id)))


def collect_complete_sample(criterion_dir: Path, sample: str, policy: CompletePolicy) -> CompleteSample:
    """Reject incomplete inventories and unsafe trees; never infer IDs from paths."""
    _publication_name(sample)
    if "/" in sample:
        raise ValueError("sample must be a single component")
    if criterion_dir.is_symlink() or criterion_dir.is_junction() or not criterion_dir.is_dir():
        raise ValueError("Criterion root must be an existing regular directory")
    cases = []
    required = {"benchmark.json", "estimates.json", "sample.json"}

    def fail(error: OSError) -> None:
        raise error

    for directory, directories, files in criterion_dir.walk(on_error=fail):
        for name in (*directories, *files):
            if (directory / name).is_symlink() or (directory / name).is_junction():
                raise ValueError("Criterion tree contains a symlink or junction")
        # A group (or the root) can share the sample name. Native row files
        # identify samples; empty leaves still fail as incomplete samples.
        if directory != criterion_dir and directory.name == sample and (required.intersection(files) or not directories):
            if not required <= set(files):
                raise ValueError(f"incomplete Criterion sample: {directory}")
            cases.append(
                CompleteCase(
                    directory.parent.relative_to(criterion_dir).as_posix(),
                    *((directory / name).read_bytes() for name in ("benchmark.json", "estimates.json", "sample.json")),
                )
            )
    return CompleteSample(policy, tuple(cases))


@dataclass(frozen=True, slots=True)
class RunSeries:
    """A report view into one measured phase, including reused reference series."""

    name: str
    phase: str
    rows: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _string(self.name, "series name")
        if self.phase not in {"baseline", "current"}:
            raise ValueError("series phase must be baseline or current")
        rows = tuple((_string(label, "row label"), _string(full_id, "full ID")) for label, full_id in self.rows)
        _names((label for label, _ in rows), "series row labels")
        object.__setattr__(self, "rows", tuple(sorted(rows)))


@dataclass(frozen=True, slots=True)
class CompleteRun:
    phases: tuple[tuple[str, CompleteSample], ...]
    series: tuple[RunSeries, ...]
    compatible: tuple[str, ...] = ("harness_sha256",)

    def __post_init__(self) -> None:
        phases = tuple((name, sample) for name, sample in self.phases)
        if len(phases) != 2 or {name for name, _ in phases} != {"baseline", "current"}:
            raise ValueError("complete run requires exactly baseline and current phases")
        if any(not isinstance(sample, CompleteSample) for _, sample in phases):
            raise TypeError("run phases require complete samples")
        policies = [sample.policy for _, sample in phases]
        if any(
            (p.sample_count, p.statistics, p.confidence_level, p.unit)
            != (policies[0].sample_count, policies[0].statistics, policies[0].confidence_level, policies[0].unit)
            for p in policies
        ):
            raise ValueError("run phases require matching measurement policies")
        series = tuple(self.series)
        if any(not isinstance(item, RunSeries) for item in series):
            raise TypeError("run series must be RunSeries instances")
        _names((item.name for item in series), "series names")
        for item in series:
            available = {case.full_id for case in dict(phases)[item.phase].cases}
            if any(full_id not in available for _, full_id in item.rows):
                raise ValueError(f"series {item.name} refers to an unmeasured full ID in phase {item.phase}")
        compatible = _names(self.compatible, "compatible fields")
        if "harness_sha256" not in compatible:
            raise ValueError("complete runs require common harness compatibility")
        unknown = Provenance("0" * 40)
        compare_provenance(unknown, unknown, fields=compatible)
        object.__setattr__(self, "phases", tuple(sorted(phases)))
        object.__setattr__(self, "series", series)
        object.__setattr__(self, "compatible", tuple(sorted(compatible)))


def serialize_run(run: CompleteRun) -> bytes:
    return deterministic_json(
        {
            "schema": RUN_SCHEMA,
            "compatible": run.compatible,
            "series": [{"name": series.name, "phase": series.phase, "rows": dict(series.rows)} for series in run.series],
            "phases": {
                name: {
                    "policy": {
                        "expected": sample.policy.expected,
                        "sample_count": sample.policy.sample_count,
                        "statistics": sample.policy.statistics,
                        "confidence_level": sample.policy.confidence_level,
                        "unit": sample.policy.unit,
                    },
                    "cases": [
                        {
                            "storage_path": case.storage_path,
                            **{key: base64.b64encode(getattr(case, key)).decode("ascii") for key in ("benchmark", "estimates", "sample")},
                        }
                        for case in sample.cases
                    ],
                }
                for name, sample in run.phases
            },
        }
    )


def parse_run(payload: bytes) -> CompleteRun:
    raw = _object(_load_json(payload, "complete run"), "complete run", {"schema", "compatible", "series", "phases"})
    if raw["schema"] != RUN_SCHEMA:
        raise ValueError("unsupported complete run schema")
    phases = []
    for name, value in _object(raw["phases"], "phases").items():
        phase = _object(value, "phase", {"policy", "cases"})
        policy = _object(phase["policy"], "policy", {"expected", "sample_count", "statistics", "confidence_level", "unit"})
        parsed_policy = CompletePolicy(
            tuple(_array(policy["expected"], "expected")),
            _integer(policy["sample_count"], "sample count"),
            tuple(_statistic(item) for item in _array(policy["statistics"], "statistics")),
            _positive(policy["confidence_level"], "confidence level"),
            _unit(policy["unit"]),
        )
        cases = []
        for item in _array(phase["cases"], "cases"):
            case = _object(item, "case", {"storage_path", "benchmark", "estimates", "sample"})
            try:
                files = tuple(base64.b64decode(_string(case[key], key), validate=True) for key in ("benchmark", "estimates", "sample"))
            except binascii.Error as error:
                raise ValueError("invalid base64 Criterion file") from error
            cases.append(CompleteCase(_string(case["storage_path"], "storage path"), *files))
        phases.append((name, CompleteSample(parsed_policy, tuple(cases))))
    series = []
    for item in _array(raw["series"], "series"):
        row = _object(item, "series", {"name", "phase", "rows"})
        series.append(
            RunSeries(
                _string(row["name"], "name"),
                _string(row["phase"], "phase"),
                tuple((label, _string(full_id, "full ID")) for label, full_id in _object(row["rows"], "rows").items()),
            )
        )
    return CompleteRun(tuple(phases), tuple(series), tuple(_array(raw["compatible"], "compatible")))


def validate_run_evidence(evidence: Evidence) -> CompleteRun:
    if evidence.payload_schema != RUN_SCHEMA:
        raise ValueError("requires complete-run evidence")
    run = parse_run(evidence.payload)
    sources = dict(evidence.sources)
    if set(sources) != {"baseline", "current"}:
        raise ValueError("run provenance requires baseline and current sources")
    for phase, source in sources.items():
        context = dict(source.context)
        if source.source_sha256 is None or context.get("phase") != phase or context.get("gate-status") != "passed":
            raise ValueError("run requires source identity and a passed gate attributed to each phase")
        for key in (
            "command",
            "environment",
            "gate-command",
            "host",
            "source-inventory",
            "harness-inventory",
            "release",
            "original-source-sha256",
            "original-source-inventory",
        ):
            if key not in context:
                raise ValueError(f"run provenance missing {key}")
        if context.get("fingerprint-schema") != "research-repo-tools/files/v1":
            raise ValueError("complete runs require the shared file fingerprint framing")
        _digest(context["original-source-sha256"], "original source fingerprint")
        from research_repo_tools.host_metadata import parse_host

        parse_host(context["host"].encode("utf-8"))
        for key in ("command", "gate-command", "source-inventory", "harness-inventory", "original-source-inventory"):
            values = _array(_load_json(context[key].encode("utf-8"), key), key)
            if not values:
                raise ValueError(f"{key} must not be empty")
            for value in values:
                _string(value, key)
        environment = _object(_load_json(context["environment"].encode("utf-8"), "environment"), "environment")
        if any(not isinstance(value, str) or "\0" in value for value in environment.values()):
            raise ValueError("invalid recorded environment")
    ReleasePair(dict(sources["current"].context)["release"], dict(sources["baseline"].context)["release"])
    compatibility = compare_provenance(sources["baseline"], sources["current"], fields=run.compatible)
    if not compatibility.compatible:
        raise ValueError(f"required run provenance differs or is unknown: {compatibility.differences}")
    return run


def run_identity(evidence: Evidence) -> str:
    """Versioned length framing over the exact retained payload and envelope."""
    validate_run_evidence(evidence)
    digest = hashlib.sha256(b"research-repo-tools/run-id/v1\0")
    for data in serialize_evidence(evidence):
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def render_run(evidence: Evidence, *, title: str = "Benchmark timings") -> bytes:
    """Render configured rows only; retained cases and statistics remain complete."""
    run = validate_run_evidence(evidence)
    phases = dict(run.phases)
    policy = phases["baseline"].policy
    labels = sorted({label for series in run.series for label, _ in series.rows})
    lines = [
        f"# {_markdown_text(title)}",
        "",
        f"Run: `{run_identity(evidence)}`",
        "",
        "Intervals are marginal timing intervals. No significance or ratio interval is inferred.",
        "",
    ]
    for series in run.series:
        source = dict(evidence.sources)[series.phase]
        lines.append(
            f"- {_markdown_text(series.name)}: measured in **{series.phase}** phase ({_markdown_text(dict(source.context)['release'])}), revision `{source.revision}`."
        )
    for statistic in policy.statistics:
        lines.extend(
            [
                "",
                f"## {statistic.title()} ({policy.unit})",
                "",
                "| Case | " + " | ".join(_markdown_text(series.name) for series in run.series) + " |",
                "| --- |" + " --- |" * len(run.series),
            ]
        )
        for label in labels:
            cells = []
            for series in run.series:
                full_id = dict(series.rows).get(label)
                case = next((case for case in phases[series.phase].cases if case.full_id == full_id), None)
                cells.append("—" if case is None else _format_estimate(case.estimate(statistic), policy.unit))
            lines.append(f"| {_markdown_text(label)} | " + " | ".join(cells) + " |")
    return ("\n".join(lines) + "\n").encode("utf-8")
