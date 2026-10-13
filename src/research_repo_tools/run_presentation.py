"""Configured views of complete evidence, with original phase attribution."""

import csv
import html
import io
import math
from dataclasses import dataclass
from typing import Literal

from research_repo_tools.complete_runs import CompleteRun, run_identity, validate_run_evidence
from research_repo_tools.criterion import Comparison, ComparisonSet, Estimate, Sample, Statistic, Unit, _format_estimate, _markdown_text, _statistic, _unit
from research_repo_tools.evidence import Evidence, _string
from research_repo_tools.publication import TableLayout, _render_ratio_svg

__all__ = ["CoordinatePlot", "RunLayout", "RunRow", "SeriesSelection", "render_coordinate_svg", "render_run_ratios", "render_run_table", "serialize_run_csv"]


def _unique(values: tuple[str, ...], context: str) -> None:
    if not values or len(set(values)) != len(values):
        raise ValueError(f"{context} must be nonempty and unique")


def _coordinate(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("coordinate must be a finite number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError("coordinate must be a finite number") from error
    if not math.isfinite(result):
        raise ValueError("coordinate must be a finite number")
    return result


@dataclass(frozen=True, slots=True)
class RunRow:
    """Ordered presentation row; optional numeric workload coordinate."""

    key: str
    label: str
    coordinate: float | None = None

    def __post_init__(self) -> None:
        _string(self.key, "row key")
        _string(self.label, "row label")
        if self.coordinate is not None:
            object.__setattr__(self, "coordinate", _coordinate(self.coordinate))


@dataclass(frozen=True, slots=True)
class SeriesSelection:
    """Named retained series, display label and (row key, full ID) mappings.

    Mappings may rename rows, but must select IDs belonging to the named view.
    The original phase is taken only from evidence.
    """

    name: str
    label: str
    rows: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _string(self.name, "series name")
        _string(self.label, "series label")
        rows = tuple((_string(key, "row key"), _string(full_id, "benchmark")) for key, full_id in self.rows)
        _unique(tuple(key for key, _ in rows), "series row keys")
        _unique(tuple(full_id for _, full_id in rows), "series benchmark mappings")
        object.__setattr__(self, "rows", rows)


@dataclass(frozen=True, slots=True)
class RunLayout:
    """Explicit series/row order, recorded statistic/unit and missing-cell policy.

    ``missing='unavailable'`` renders missing mappings explicitly. A mapped ID
    that is unmeasured or outside its named series always fails. Comparisons are
    ordered (numerator series, denominator series) pairs of point timings.
    """

    series: tuple[SeriesSelection, ...]
    rows: tuple[RunRow, ...]
    statistic: Statistic
    unit: Unit
    missing: Literal["error", "unavailable"] = "error"
    comparisons: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        series, rows = tuple(self.series), tuple(self.rows)
        if any(not isinstance(item, SeriesSelection) for item in series) or any(not isinstance(row, RunRow) for row in rows):  # ty: ignore[redundant-condition-strict]  Protect untyped callers.
            raise TypeError("run layout requires SeriesSelection and RunRow instances")
        _unique(tuple(item.name for item in series), "selected series")
        _unique(tuple(item.label for item in series), "series labels")
        _unique(tuple(row.key for row in rows), "row keys")
        _statistic(self.statistic)
        _unit(self.unit)
        if self.missing not in {"error", "unavailable"}:
            raise ValueError("missing must be error or unavailable")
        keys = {row.key for row in rows}
        for item in series:
            mapped = set(dict(item.rows))
            if mapped - keys or (self.missing == "error" and mapped != keys):
                raise ValueError("series mappings must select declared rows; missing rows require unavailable policy")
        pairs = tuple((_string(a, "numerator"), _string(b, "denominator")) for a, b in self.comparisons)
        names = {item.name for item in series}
        if len(set(pairs)) != len(pairs) or any(a == b or {a, b} - names for a, b in pairs):
            raise ValueError("comparisons require unique pairs of distinct selected series")
        object.__setattr__(self, "series", series)
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "comparisons", pairs)


def _select(evidence: Evidence, layout: RunLayout) -> tuple[CompleteRun, dict[str, dict[str, Estimate]]]:
    run = validate_run_evidence(evidence)
    phases, views = dict(run.phases), {item.name: item for item in run.series}
    policy = phases["baseline"].policy
    if layout.unit != policy.unit or layout.statistic not in policy.statistics:
        raise ValueError("selected statistic/unit must match the recorded measurement policy")
    selected = {}
    for item in layout.series:
        if item.name not in views:
            raise ValueError(f"unknown retained series: {item.name}")
        view = views[item.name]
        allowed = set(dict(view.rows).values())
        cases = {case.full_id: case for case in phases[view.phase].cases}
        if any(full_id not in allowed for _, full_id in item.rows):
            raise ValueError(f"mapping is outside the retained series: {item.name}")
        selected[item.name] = {key: cases[full_id].estimate(layout.statistic) for key, full_id in item.rows}
    return run, selected


def _attribution(evidence: Evidence, run: CompleteRun, name: str) -> str:
    phase = next(item.phase for item in run.series if item.name == name)
    source = dict(evidence.sources)[phase]
    return f"{phase} phase, {dict(source.context)['release']}, revision {source.revision}"


def render_run_table(evidence: Evidence, layout: RunLayout) -> str:
    """Render configured timings, omissions and optional point comparisons."""
    run, selected = _select(evidence, layout)
    labels = {item.name: item.label for item in layout.series}
    lines = [f"Run: `{run_identity(evidence)}`. Statistic: {layout.statistic}. Unit: {layout.unit}.", ""]
    lines.extend(f"- {_markdown_text(item.label)}: {_markdown_text(_attribution(evidence, run, item.name))}." for item in layout.series)
    headers = ["Case", *(_markdown_text(item.label) for item in layout.series)]
    for a, b in layout.comparisons:
        headers.extend(
            [f"{_markdown_text(labels[a])} / {_markdown_text(labels[b])}", f"{_markdown_text(labels[a])} → {_markdown_text(labels[b])} time reduction (%)"]
        )
    lines.extend(["", "| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"])
    for row in layout.rows:
        cells = [_markdown_text(row.label)]
        for item in layout.series:
            estimate = selected[item.name].get(row.key)
            cells.append(_format_estimate(estimate, layout.unit) if estimate is not None else "unavailable")
        for a, b in layout.comparisons:
            first, second = selected[a].get(row.key), selected[b].get(row.key)
            if first is None or second is None:
                cells.extend(["unavailable", "unavailable"])
            else:
                pair = Comparison(row.key, first, second)
                cells.extend([f"{pair.speedup:.6g}", f"{pair.percent_reduction:.6g}"])
        lines.append("| " + " | ".join(cells) + " |")
    lines.extend(
        [
            "",
            "Intervals are recorded marginal timing intervals; ratios are point estimates, with no ratio intervals or significance claims.",
            "Time reduction is 100 × (numerator − denominator) / numerator; positive means less denominator time.",
            "",
            "Selection coverage:",
        ]
    )
    for item in layout.series:
        view = next(view for view in run.series if view.name == item.name)
        count = len(selected[item.name])
        total = len(dict(run.phases)[view.phase].cases)
        lines.append(
            f"- {_markdown_text(item.label)}: {count} selected; {len(layout.rows) - count} unavailable rows; {total - count} phase cases omitted. Complete evidence is retained independently."
        )
    return "\n".join(lines) + "\n"


def serialize_run_csv(evidence: Evidence, layout: RunLayout) -> bytes:
    """UTF-8/LF long-form CSV; unavailable cells retain series/phase/run identity."""
    run, selected = _select(evidence, layout)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "run_id",
            "series",
            "label",
            "phase",
            "revision",
            "release",
            "source_sha256",
            "harness_sha256",
            "row",
            "row_label",
            "coordinate",
            "benchmark",
            "statistic",
            "unit",
            "status",
            "point",
            "lower",
            "upper",
            "confidence_level",
        ]
    )
    identity = run_identity(evidence)
    for item in layout.series:
        view = next(view for view in run.series if view.name == item.name)
        source = dict(evidence.sources)[view.phase]
        for row in layout.rows:
            estimate = selected[item.name].get(row.key)
            writer.writerow(
                [
                    identity,
                    item.name,
                    item.label,
                    view.phase,
                    source.revision,
                    dict(source.context)["release"],
                    source.source_sha256,
                    source.harness_sha256,
                    row.key,
                    row.label,
                    row.coordinate,
                    dict(item.rows).get(row.key),
                    layout.statistic,
                    layout.unit,
                    "available" if estimate is not None else "unavailable",
                    *(getattr(estimate, field) if estimate is not None else None for field in ("point", "lower", "upper", "confidence_level")),
                ]
            )
    return output.getvalue().encode("utf-8")


@dataclass(frozen=True, slots=True)
class CoordinatePlot:
    """Axis labels/scales for one deterministic dependency-free coordinate plot."""

    title: str
    x_label: str
    y_label: str
    x_scale: Literal["linear", "log"] = "linear"
    y_scale: Literal["linear", "log"] = "linear"

    def __post_init__(self) -> None:
        for value in (self.title, self.x_label, self.y_label):
            _string(value, "plot label")
        if self.x_scale not in {"linear", "log"} or self.y_scale not in {"linear", "log"}:
            raise ValueError("axis scale must be linear or log")


def _xml(value: str) -> str:
    if any(not (char in "\t\n\r" or 0x20 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF) for char in value):
        raise ValueError("SVG labels must be valid XML text")
    return html.escape(value)


def _axis(values: list[float], scale: str):
    if scale == "log" and any(value <= 0 for value in values):
        raise ValueError("log axes require positive coordinates and timing bounds")
    transformed = [math.log10(value) if scale == "log" else value for value in values]
    low, high = min(transformed), max(transformed)
    # Normalize before subtraction to keep geometry finite even for extreme inputs.
    divisor = max(abs(low), abs(high), 1.0)
    lo, hi = low / divisor, high / divisor

    def position(value: float) -> float:
        transformed_value = math.log10(value) if scale == "log" else value
        if low == high:
            return 0.5
        if lo == hi:
            return (transformed_value - low) / (high - low)
        return (transformed_value / divisor - lo) / (hi - lo)

    return position


def render_coordinate_svg(evidence: Evidence, layout: RunLayout, plot: CoordinatePlot) -> bytes:
    """Point timings with recorded marginal intervals; gaps never imply a rerun.

    Coordinates are supplied by consumers. Lines follow numeric x order and
    break at unavailable rows. No resampling, conversion or inference occurs.
    """
    run, selected = _select(evidence, layout)
    coordinates = [row.coordinate for row in layout.rows]
    if any(value is None for value in coordinates):
        raise ValueError("coordinate plots require a coordinate for every row")
    xs = [_coordinate(value) for value in coordinates]
    if len(set(xs)) != len(xs):
        raise ValueError("coordinate plot rows must have unique coordinates")
    ordered = sorted(layout.rows, key=lambda row: _coordinate(row.coordinate))
    estimates = [estimate for cells in selected.values() for estimate in cells.values()]
    ys = [value for estimate in estimates for value in (estimate.point, estimate.lower, estimate.upper) if value is not None]
    x, y = _axis(xs, plot.x_scale), _axis(ys, plot.y_scale)
    width, height = 960, 530 + 42 * len(layout.series)
    identity = run_identity(evidence)
    description = f"Run {identity}. {layout.statistic} timing ({layout.unit}). Recorded marginal timing intervals; no ratio interval or significance claim. "
    description += " ".join(f"{item.label}: {_attribution(evidence, run, item.name)}." for item in layout.series)
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description" data-run-id="{identity}">',
        f'<title id="title">{_xml(plot.title)}</title>',
        f'<desc id="description">{_xml(description)}</desc>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        '<g font-family="sans-serif" font-size="13" fill="#222222">',
        f'<text x="80" y="28">{_xml(plot.title)}</text>',
        '<path d="M 80 60 V 420 H 900" stroke="#222222" fill="none"/>',
        f'<text x="490" y="475" text-anchor="middle">{_xml(plot.x_label)} ({plot.x_scale})</text>',
        f'<text transform="translate(18 240) rotate(-90)" text-anchor="middle">{_xml(plot.y_label)} ({layout.unit}, {plot.y_scale})</text>',
    ]
    for value in sorted(set(xs)):
        px = 80 + 820 * x(value)
        lines.append(f'<text x="{px:.6f}" y="444" text-anchor="middle">{value:.6g}</text>')
    for value in sorted({min(ys), max(ys)}):
        py = 420 - 360 * y(value)
        lines.extend([f'<path d="M 80 {py:.6f} H 900" stroke="#dddddd"/>', f'<text x="72" y="{py:.6f}" text-anchor="end">{value:.6g}</text>'])
    colors = ("#267394", "#9c4420", "#527a28", "#7955a1", "#a52d62")
    for index, item in enumerate(layout.series):
        color = colors[index % len(colors)]
        lines.append(f'<g stroke="{color}" fill="{color}" stroke-dasharray="{index + 2} 2" data-series="{_xml(item.name)}">')
        previous = None
        for row in ordered:
            estimate = selected[item.name].get(row.key)
            if estimate is None:
                previous = None
                continue
            px, py = 80 + 820 * x(_coordinate(row.coordinate)), 420 - 360 * y(estimate.point)
            if previous is not None:
                lines.append(f'<path d="M {previous[0]:.6f} {previous[1]:.6f} L {px:.6f} {py:.6f}" fill="none"/>')
            previous = (px, py)
            if estimate.lower is not None and estimate.upper is not None:
                lower, upper = 420 - 360 * y(estimate.lower), 420 - 360 * y(estimate.upper)
                lines.append(
                    f'<path d="M {px:.6f} {lower:.6f} V {upper:.6f} M {px - 4:.6f} {lower:.6f} H {px + 4:.6f} M {px - 4:.6f} {upper:.6f} H {px + 4:.6f}" stroke-dasharray="none"/>'
                )
            tooltip = f"{item.label}, {row.label}, x={row.coordinate}: {_format_estimate(estimate, layout.unit)}; {_attribution(evidence, run, item.name)}"
            lines.append(
                f'<circle cx="{px:.6f}" cy="{py:.6f}" r="{4 + index % 4}" fill="none" stroke-width="1.5" stroke-dasharray="none"><title>{_xml(tooltip)}</title></circle>'
            )
        legend_y = 505 + 42 * index
        lines.extend(
            [
                f'<path d="M 55 {legend_y - 5} H 72" fill="none"/>',
                f'<text x="80" y="{legend_y}" stroke="none">{_xml(item.label)} — {_xml(_attribution(evidence, run, item.name))}</text>',
                f'<text x="80" y="{legend_y + 17}" stroke="none">{len(selected[item.name])} selected; {len(layout.rows) - len(selected[item.name])} unavailable rows</text>',
                "</g>",
            ]
        )
    lines.extend(
        [
            f'<text x="80" y="{height - 28}">Statistic: {layout.statistic}; recorded marginal timing intervals; no significance test.</text>',
            f'<text x="80" y="{height - 11}" font-size="11">Run: {identity}</text>',
            "</g>",
            "</svg>",
        ]
    )
    return ("\n".join(lines) + "\n").encode("utf-8")


def render_run_ratios(evidence: Evidence, layout: RunLayout) -> bytes:
    """One explicitly configured numerator/denominator point-ratio figure."""
    run, selected = _select(evidence, layout)
    if len(layout.comparisons) != 1:
        raise ValueError("ratio SVG requires exactly one comparison")
    a, b = layout.comparisons[0]
    keys = [row for row in layout.rows if row.key in selected[a] and row.key in selected[b]]
    if not keys:
        raise ValueError("ratio SVG requires at least one available paired row")
    samples = [Sample(tuple((row.key, selected[name][row.key]) for row in keys), layout.statistic, layout.unit) for name in (a, b)]
    labels = {item.name: item.label for item in layout.series}
    table = TableLayout(
        tuple((row.key, row.label) for row in keys),
        f"{labels[a]} ({_attribution(evidence, run, a)})",
        f"{labels[b]} ({_attribution(evidence, run, b)})",
        layout.unit,
    )
    identity = run_identity(evidence)
    description = f"Run {identity}. Numerator time / denominator time, point estimates. Greater than one means less denominator time. No ratio interval or significance test. {len(keys)} paired rows; {len(layout.rows) - len(keys)} unavailable rows omitted."
    # The comparison is presentation only; retain the originating run binding.
    return _render_ratio_svg(
        ComparisonSet(*samples), table, description=description, caption="Numerator/denominator point ratio; dashed line = 1", identity=identity
    )
