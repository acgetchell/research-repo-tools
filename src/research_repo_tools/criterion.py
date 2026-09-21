"""Validated Criterion timing estimates and deterministic comparison data."""

import html
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from research_repo_tools.evidence import _load_json, _object, _string, deterministic_json

__all__ = [
    "COMPARISON_SCHEMA",
    "Comparison",
    "ComparisonSet",
    "Estimate",
    "Sample",
    "Statistic",
    "Unit",
    "collect_sample",
    "compare_samples",
    "parse_comparison",
    "parse_estimate",
    "read_estimate",
    "render_comparison",
    "serialize_comparison",
]

type Statistic = Literal["mean", "median"]
type Unit = Literal["ns", "us", "ms", "s"]
COMPARISON_SCHEMA = "research-repo-tools/criterion-comparison/v1"


def _positive(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{context} must be a finite positive number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{context} is outside the finite floating-point range") from error
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{context} must be a finite positive number")
    return result


def _statistic(value: object) -> Statistic:
    if value == "mean":
        return "mean"
    if value == "median":
        return "median"
    raise ValueError(f"unsupported Criterion statistic: {value!r}")


def _unit(value: object) -> Unit:
    match value:
        case "ns":
            return "ns"
        case "us":
            return "us"
        case "ms":
            return "ms"
        case "s":
            return "s"
        case _:
            raise ValueError(f"unsupported Criterion timing unit: {value!r}")


@dataclass(frozen=True, slots=True)
class Estimate:
    """A positive point and optional complete ordered confidence interval.

    Units belong to Sample. Bootstrap intervals need not contain the point.
    No significance or confidence interval for a ratio is inferred here.
    """

    point: float
    lower: float | None = None
    upper: float | None = None
    confidence_level: float | None = None

    def __post_init__(self) -> None:
        for name in ("point", "lower", "upper"):
            value = getattr(self, name)
            if name == "point" or value is not None:
                object.__setattr__(self, name, _positive(value, name))
        if (self.lower is None) != (self.upper is None):
            raise ValueError("confidence interval requires both bounds")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("confidence interval lower bound exceeds upper bound")
        if self.confidence_level is not None:
            level = _positive(self.confidence_level, "confidence_level")
            if level >= 1 or self.lower is None:
                raise ValueError("confidence_level requires an interval and must lie strictly between zero and one")
            object.__setattr__(self, "confidence_level", level)


@dataclass(frozen=True, slots=True)
class Sample:
    """Unique, sorted benchmark estimates with explicit statistic and timing unit."""

    estimates: tuple[tuple[str, Estimate], ...]
    statistic: Statistic = "median"
    unit: Unit = "ns"

    def __post_init__(self) -> None:
        _statistic(self.statistic)
        _unit(self.unit)
        estimates = tuple((name, estimate) for name, estimate in self.estimates)
        for name, estimate in estimates:
            _string(name, "benchmark name")
            if not isinstance(estimate, Estimate):
                raise TypeError(f"benchmark {name!r} requires an Estimate")
        if len(dict(estimates)) != len(estimates):
            raise ValueError("benchmark names must be unique")
        object.__setattr__(self, "estimates", tuple(sorted(estimates)))


@dataclass(frozen=True, slots=True)
class Comparison:
    """Paired timings; ratios are descriptive and do not establish significance."""

    benchmark: str
    baseline: Estimate
    current: Estimate

    def __post_init__(self) -> None:
        _string(self.benchmark, "benchmark name")
        if not isinstance(self.baseline, Estimate) or not isinstance(self.current, Estimate):
            raise TypeError("comparison requires Estimate instances")
        _positive(self.speedup, "baseline/current ratio")
        _positive(self.current.point / self.baseline.point, "current/baseline ratio")
        if not math.isfinite(self.percent_reduction):
            raise ValueError("percent reduction exceeds the finite floating-point range")

    @property
    def speedup(self) -> float:
        """Baseline/current timing; values greater than one mean less current time."""
        return self.baseline.point / self.current.point

    @property
    def percent_reduction(self) -> float:
        """100 * (baseline - current) / baseline; positive means less current time."""
        return ((self.baseline.point - self.current.point) / self.baseline.point) * 100.0


@dataclass(frozen=True, slots=True)
class ComparisonSet:
    """Two samples and their derived coverage; absent benchmarks are preserved."""

    baseline: Sample
    current: Sample

    def __post_init__(self) -> None:
        if not isinstance(self.baseline, Sample) or not isinstance(self.current, Sample):
            raise TypeError("comparison set requires Sample instances")
        if (self.baseline.statistic, self.baseline.unit) != (self.current.statistic, self.current.unit):
            raise ValueError("comparison samples must use the same statistic and unit")
        # Validate derived arithmetic before a consumer can publish the samples.
        _ = self.comparisons

    @property
    def comparisons(self) -> tuple[Comparison, ...]:
        baseline, current = dict(self.baseline.estimates), dict(self.current.estimates)
        return tuple(Comparison(name, baseline[name], current[name]) for name in sorted(baseline.keys() & current.keys()))

    @property
    def missing_baseline(self) -> tuple[str, ...]:
        return tuple(sorted(dict(self.current.estimates).keys() - dict(self.baseline.estimates).keys()))

    @property
    def missing_current(self) -> tuple[str, ...]:
        return tuple(sorted(dict(self.baseline.estimates).keys() - dict(self.current.estimates).keys()))


def compare_samples(baseline: Sample, current: Sample) -> ComparisonSet:
    """Pair all common names and preserve added/missing rows, including empty sets."""
    return ComparisonSet(baseline, current)


def parse_estimate(payload: bytes, *, statistic: Statistic = "median") -> Estimate:
    """Parse Criterion estimates.json bytes. Ignore other Criterion statistics.

    Missing/null intervals are allowed; present intervals must be complete.
    Unknown Criterion fields are allowed for read compatibility. Units cannot
    be inferred from this file; supply the measurement unit when making a Sample.
    """
    statistic = _statistic(statistic)
    document = _object(_load_json(payload, "Criterion estimates"), "Criterion estimates")
    data = _object(document.get(statistic), f"Criterion {statistic}")
    point = _positive(data.get("point_estimate"), f"{statistic}.point_estimate")
    if data.get("confidence_interval") is None:
        return Estimate(point)
    interval = _object(data["confidence_interval"], f"{statistic}.confidence_interval")
    level = interval.get("confidence_level")
    return Estimate(
        point,
        _positive(interval.get("lower_bound"), "lower_bound"),
        _positive(interval.get("upper_bound"), "upper_bound"),
        None if level is None else _positive(level, "confidence_level"),
    )


def read_estimate(path: Path, *, statistic: Statistic = "median") -> Estimate:
    """Parse one file, adding its path to validation errors."""
    try:
        return parse_estimate(path.read_bytes(), statistic=statistic)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error


def collect_sample(criterion_dir: Path, sample: str, *, statistic: Statistic = "median", unit: Unit = "ns") -> Sample:
    """Read benchmark/SAMPLE/estimates.json below an existing Criterion directory.

    SAMPLE is one directory component. Benchmark IDs are relative POSIX paths.
    Reject symlinked entries instead of following data outside the sample tree.
    An existing directory with no matching estimates is an explicit empty sample.
    """
    _string(sample, "sample name")
    if sample in {".", ".."} or any(char in sample for char in "/\\:"):
        raise ValueError("sample must be a single directory component")
    if not criterion_dir.is_dir():
        raise FileNotFoundError(f"Criterion directory does not exist: {criterion_dir}")
    estimates = []

    def walk_error(error: OSError) -> None:
        raise error

    for directory, directories, files in criterion_dir.walk(on_error=walk_error):
        for name in (*directories, *files):
            if (directory / name).is_symlink():
                raise ValueError(f"Criterion tree contains a symlink: {directory / name}")
        if directory.name == sample and "estimates.json" in files:
            benchmark = directory.parent.relative_to(criterion_dir).as_posix()
            if benchmark == ".":
                raise ValueError(f"Criterion estimate has no benchmark directory: {directory}")
            estimates.append((benchmark, read_estimate(directory / "estimates.json", statistic=statistic)))
    return Sample(tuple(estimates), statistic, unit)


def serialize_comparison(comparison: ComparisonSet) -> bytes:
    """Serialize both full samples deterministically; ratios/coverage are derived."""

    def sample_document(sample: Sample) -> list[dict[str, object]]:
        return [
            {"benchmark": name, "point": estimate.point, "lower": estimate.lower, "upper": estimate.upper, "confidence_level": estimate.confidence_level}
            for name, estimate in sample.estimates
        ]

    return deterministic_json(
        {
            "schema": COMPARISON_SCHEMA,
            "statistic": comparison.baseline.statistic,
            "unit": comparison.baseline.unit,
            "baseline": sample_document(comparison.baseline),
            "current": sample_document(comparison.current),
        }
    )


def parse_comparison(payload: bytes) -> ComparisonSet:
    """Read the versioned comparison format and reject malformed or duplicate rows."""
    document = _object(_load_json(payload, "Criterion comparison"), "Criterion comparison", {"schema", "statistic", "unit", "baseline", "current"})
    if document["schema"] != COMPARISON_SCHEMA:
        raise ValueError(f"unsupported comparison schema: {document['schema']!r}")
    statistic, unit = _statistic(document["statistic"]), _unit(document["unit"])

    def sample(raw: object) -> Sample:
        if not isinstance(raw, list):
            raise ValueError("comparison sample must be an array")
        rows = []
        for value in raw:
            row = _object(value, "comparison row", {"benchmark", "point", "lower", "upper", "confidence_level"})
            rows.append(
                (
                    _string(row["benchmark"], "benchmark"),
                    Estimate(
                        _positive(row["point"], "point"),
                        None if row["lower"] is None else _positive(row["lower"], "lower"),
                        None if row["upper"] is None else _positive(row["upper"], "upper"),
                        None if row["confidence_level"] is None else _positive(row["confidence_level"], "confidence_level"),
                    ),
                )
            )
        return Sample(tuple(rows), statistic, unit)

    return compare_samples(sample(document["baseline"]), sample(document["current"]))


def render_comparison(comparison: ComparisonSet) -> str:
    """Render retained timing data without measuring or consulting live source.

    Keep domain-specific labels, acceptance thresholds, significance, and
    scientific narrative in the consumer's renderer.
    """

    def escape(value: str) -> str:
        return html.escape(value).replace("|", "&#124;").replace("`", "&#96;")

    def timing(estimate: Estimate | None) -> str:
        if estimate is None:
            return "—"
        point = f"{estimate.point:g} {comparison.baseline.unit}"
        if estimate.lower is not None and estimate.upper is not None:
            point += f" [{estimate.lower:g}, {estimate.upper:g}]"
            if estimate.confidence_level is not None:
                point += f" ({estimate.confidence_level:g} confidence)"
        return point

    lines = [
        "# Criterion timing comparison",
        "",
        f"Statistic: {comparison.baseline.statistic}. Unit: {comparison.baseline.unit}.",
        "",
        "Ratios describe timings; they do not establish statistical significance or scientific acceptance.",
        "",
        "| Benchmark | Coverage | Baseline | Current | Baseline/current | Time reduction (%) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    baseline, current = dict(comparison.baseline.estimates), dict(comparison.current.estimates)
    pairs = {row.benchmark: row for row in comparison.comparisons}
    for name in sorted(baseline.keys() | current.keys()):
        row = pairs.get(name)
        coverage = "common" if row is not None else "added" if name in current else "missing"
        ratio, reduction = (f"{row.speedup:.6g}", f"{row.percent_reduction:.6g}") if row is not None else ("—", "—")
        lines.append(f"| {escape(name)} | {coverage} | {timing(baseline.get(name))} | {timing(current.get(name))} | {ratio} | {reduction} |")
    if not baseline and not current:
        lines.extend(["", "No benchmark estimates."])
    return "\n".join(lines) + "\n"
