"""Byte-preserving document sections, retained timing renderers, and publication plans."""

import difflib
import html
import ntpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from research_repo_tools.criterion import Comparison, ComparisonSet, Unit, _format_estimate, _markdown_text, _unit
from research_repo_tools.evidence import _string, sha256
from research_repo_tools.files import _validate_distinct_paths, replace_many
from research_repo_tools.process import run_git_bytes
from research_repo_tools.release_policy import ReleaseRule, relative_path

__all__ = [
    "GitCheck",
    "GlobCheck",
    "MarkerPair",
    "PublicationPlan",
    "TableLayout",
    "plan_outputs",
    "plan_publication",
    "preview_publication",
    "publish_publication",
    "render_svg",
    "render_table",
    "replace_section",
    "verify_tagged_files",
]


@dataclass(frozen=True, slots=True)
class MarkerPair:
    """Distinct single-line UTF-8 delimiters occurring exactly once and in order."""

    begin: str
    end: str

    def __post_init__(self) -> None:
        _string(self.begin, "begin marker")
        _string(self.end, "end marker")
        if self.begin in self.end or self.end in self.begin:
            raise ValueError("publication markers must be distinct and must not contain each other")


def replace_section(document: bytes, markers: MarkerPair, content: str) -> bytes:
    """Replace only the bytes between delimiters, inserting two LF on each side.

    Surrounding bytes (including BOM, CRLF, historical links, and non-UTF-8 text)
    are untouched. Content is UTF-8 with LF, without either delimiter. A second
    call with the same content is byte-identical.
    """
    if not isinstance(document, bytes) or not isinstance(content, str):
        raise TypeError("publication requires document bytes and string content")
    begin, end = markers.begin.encode("utf-8"), markers.end.encode("utf-8")
    first, last = document.find(begin), document.find(end)
    if first < 0 or last < 0 or document.find(begin, first + 1) >= 0 or document.find(end, last + 1) >= 0 or first + len(begin) > last:
        raise ValueError("document must contain exactly one ordered publication marker pair")
    if markers.begin in content or markers.end in content:
        raise ValueError("rendered content must not contain publication markers")
    body = content.replace("\r\n", "\n").replace("\r", "\n").strip("\n").encode("utf-8")
    result = document[: first + len(begin)] + b"\n\n" + body + b"\n\n" + document[last:]
    # Delimiters may also straddle the content boundary for non-HTML markers.
    if result.find(begin, result.find(begin) + 1) >= 0 or result.find(end, result.find(end) + 1) >= 0:
        raise ValueError("rendered content creates an ambiguous publication marker")
    return result


@dataclass(frozen=True, slots=True)
class TableLayout:
    """Explicit ordered (benchmark, display label) rows and recorded timing unit.

    Labels do not select rows. Unit must match the evidence; no conversion,
    workload selection, eligibility, or scientific conclusion is inferred.
    """

    rows: tuple[tuple[str, str], ...]
    baseline_label: str
    current_label: str
    unit: Unit

    def __post_init__(self) -> None:
        rows = tuple((_string(name, "benchmark"), _string(label, "row label")) for name, label in self.rows)
        if not rows or len(dict(rows)) != len(rows):
            raise ValueError("publication requires a nonempty selection of unique benchmarks")
        _string(self.baseline_label, "baseline label")
        _string(self.current_label, "current label")
        _unit(self.unit)
        object.__setattr__(self, "rows", rows)


def _selected(comparison: ComparisonSet, layout: TableLayout) -> tuple[tuple[str, Comparison], ...]:
    if layout.unit != comparison.baseline.unit:
        raise ValueError(f"publication unit {layout.unit} does not match retained unit {comparison.baseline.unit}")
    rows = {row.benchmark: row for row in comparison.comparisons}
    missing = [name for name, _ in layout.rows if name not in rows]
    if missing:
        raise ValueError(f"publication rows must have both baseline and current evidence: {missing}")
    return tuple((label, rows[name]) for name, label in layout.rows)


def render_table(comparison: ComparisonSet, layout: TableLayout) -> str:
    """Render selected retained timings with recorded bounds and coverage counts."""
    selected = _selected(comparison, layout)
    lines = [
        f"Statistic: {comparison.baseline.statistic}. Unit: {layout.unit}.",
        "",
        f"| Benchmark | {_markdown_text(layout.baseline_label)} | {_markdown_text(layout.current_label)} | Baseline/current | Time reduction (%) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label, row in selected:
        lines.append(
            f"| {_markdown_text(label)} | {_format_estimate(row.baseline, layout.unit)} | {_format_estimate(row.current, layout.unit)} | "
            f"{row.speedup:.6g} | {row.percent_reduction:.6g} |"
        )
    lines.extend(
        [
            "",
            f"Coverage: {len(selected)} selected of {len(comparison.comparisons)} comparable; "
            f"{len(comparison.missing_baseline)} current-only; {len(comparison.missing_current)} baseline-only benchmarks.",
            "",
            "Ratios are point estimates, not significance tests or scientific acceptance.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_svg(comparison: ComparisonSet, layout: TableLayout) -> bytes:
    """Render deterministic accessible SVG bars without plotting dependencies.

    Bar lengths show baseline/current point ratios with a reference at one.
    Labels are XML text, never markup; repeated display labels remain separate
    rows. There are no timestamps, fonts to download, or random identifiers.
    """
    rows = _selected(comparison, layout)
    if any("\ufffe" in text or "\uffff" in text for text in (layout.current_label, layout.baseline_label, *(label for label, _ in rows))):
        raise ValueError("SVG labels must be valid XML text")
    label_width = max(200, 9 * max(len(label) for label, _ in rows) + 20)
    title_text = f"{layout.current_label} against {layout.baseline_label}"
    width, height = max(label_width + 490, 9 * len(title_text) + 32), 110 + 32 * len(rows)
    maximum = max(1.0, *(row.speedup for _, row in rows))
    reference = label_width + 360 / maximum
    title = html.escape(title_text)
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
        f'<title id="title">{title}</title>',
        '<desc id="description">Baseline time / current time, point estimates. Greater than one means less current time. No significance test.</desc>',
        '<g font-family="sans-serif" font-size="14" fill="#222222">',
        f'<text x="16" y="25">{title}</text>',
    ]
    for index, (label, row) in enumerate(rows):
        y = 48 + 32 * index
        length = row.speedup / maximum * 360
        lines.extend(
            [
                f'<text x="{label_width - 12}" y="{y + 17}" text-anchor="end">{html.escape(label)}</text>',
                f'<rect x="{label_width}" y="{y}" width="{length:.6f}" height="24" fill="#267394"/>',
                f'<text x="{label_width + length + 8:.6f}" y="{y + 17}">{row.speedup:.6g}</text>',
            ]
        )
    lines.extend(
        [
            f'<path d="M {reference:.6f} 40 V {height - 65}" stroke="#555555" stroke-dasharray="4 4"/>',
            f'<text x="16" y="{height - 20}">Baseline/current point ratio; dashed line = 1</text>',
            "</g>",
            "</svg>",
        ]
    )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _publication_name(name: str) -> str:
    relative_path(name)
    if any(ntpath.isreserved(part) or part.casefold() == ".git" or "\x7f" in part for part in name.split("/")):
        raise ValueError(f"publication path must be portable: {name!r}")
    return _string(name, "publication path")


def _path(root: Path, name: str) -> Path:
    _publication_name(name)
    path = root / name
    if any(item.is_symlink() for item in (path, *path.parents) if item.is_relative_to(root)):
        raise ValueError(f"publication path must not contain a symlink: {name}")
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"publication path must remain inside the root: {name}")
    if path.exists() and not path.is_file():
        raise ValueError(f"publication path must be a regular file: {name}")
    return path


def _inventory(root: Path, patterns: tuple[str, ...]) -> tuple[str, ...]:
    paths: set[str] = set()
    for pattern in patterns:
        # Validate traversal before allowing glob expansion; wildcard components
        # intentionally remain patterns until the selected paths are validated.
        if pattern.startswith("/") or "\\" in pattern or any(part in {"", ".", "..", ".git"} for part in pattern.split("/")):
            raise ValueError(f"source pattern must be root-relative: {pattern!r}")
        found = list(root.glob(pattern))
        if not found:
            raise ValueError(f"source pattern has no matches: {pattern}")
        for path in found:
            name = path.relative_to(root).as_posix()
            _path(root, name)
            if not path.is_file():
                raise ValueError(f"source input is missing or not a regular file: {name}")
            paths.add(name)
    _validate_distinct_paths(tuple(root / path for path in paths))
    return tuple(sorted(paths))


@dataclass(frozen=True, slots=True)
class GlobCheck:
    """Root-relative glob patterns and their complete expected file inventory.

    Every selected path must also have validated bytes in the planner's inputs.
    Re-expansion before publication detects additions as well as removals.
    """

    patterns: tuple[str, ...]
    paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.patterns, str) or isinstance(self.paths, str) or not self.patterns or not self.paths:
            raise ValueError("glob check requires nonempty pattern and path sequences")
        object.__setattr__(self, "patterns", tuple(_string(pattern, "glob pattern") for pattern in self.patterns))
        paths = tuple(_publication_name(path) for path in self.paths)
        if len(set(paths)) != len(paths):
            raise ValueError("glob check paths must be unique")
        object.__setattr__(self, "paths", tuple(sorted(paths)))


@dataclass(frozen=True, slots=True)
class GitCheck:
    """Required local tag, exact blob paths, and optional measured commit identity.

    Paths are root-relative POSIX names. An empty inventory requires a revision.
    No fetch, Git filters, or missing/future-tag fallback occurs.
    """

    tag: str
    paths: tuple[str, ...] = ()
    revision: str | None = None
    allow_missing: bool = False

    def __post_init__(self) -> None:
        _string(self.tag, "publication tag")
        if type(self.allow_missing) is not bool:
            raise ValueError("allow_missing must be a boolean")
        if self.allow_missing and re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", self.tag) is None:
            raise ValueError("future publication tags must use canonical stable vX.Y.Z syntax")
        paths = tuple(_publication_name(path) for path in self.paths)
        if len(set(paths)) != len(paths) or (not paths and self.revision is None):
            raise ValueError("Git check requires unique paths or a source revision")
        if self.revision is not None and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", self.revision) is None:
            raise ValueError("Git check revision must be a full lowercase commit ID")
        object.__setattr__(self, "paths", paths)


def verify_tagged_files(root: Path, tag: str, files: Mapping[str, bytes], *, revision: str | None = None) -> str:
    """Require exact stored blob bytes and, optionally, the tag's commit identity.

    Return the resolved commit. Read-only Git calls bypass text conversion and
    clean/smudge filters and replacement objects. The root must be the repository root. Missing tags,
    non-blob objects, or byte differences fail, including LF versus CRLF.
    """
    check = GitCheck(tag, tuple(files), revision)
    for data in files.values():
        if not isinstance(data, bytes):
            raise TypeError("tagged publication contents must be bytes")

    def git(args: list[str], *, check: bool = True):
        return run_git_bytes(["--no-pager", "--no-replace-objects", *args], cwd=root, check=check)

    prefix = git(["rev-parse", "--show-prefix"]).stdout.strip()
    if prefix:
        raise ValueError("tag verification requires the Git repository root")
    reference = f"refs/tags/{check.tag}"
    valid = git(["check-ref-format", reference], check=False)
    if valid.returncode:
        raise ValueError(f"invalid publication tag: {tag}")
    resolved = git(["rev-parse", "--verify", f"{reference}^{{commit}}"], check=False)
    if resolved.returncode:
        raise ValueError(f"publication requires the existing local tag {tag}")
    commit = resolved.stdout.decode("ascii").strip()
    if revision is not None and revision != commit:
        raise ValueError(f"tag {tag} does not identify the recorded source revision {revision}")
    for path, expected in files.items():
        entry = git(["ls-tree", "-z", commit, "--", f":(literal){path}"])
        if not entry.stdout.startswith((b"100644 blob ", b"100755 blob ")):
            raise ValueError(f"tag {tag} does not contain a regular publication artifact {path}")
        blob = git(["cat-file", "blob", f"{commit}:{path}"], check=False)
        if blob.returncode or blob.stdout != expected:
            raise ValueError(f"tag {tag} does not contain the exact publication artifact {path}")
    return commit


@dataclass(frozen=True, slots=True)
class PublicationPlan:
    """Validated candidate bytes from plan_publication; retain exclusive writer control.

    ``outputs`` and ``originals`` are immutable root-relative path/byte tuples.
    Originals include every input, reference, and output (None means absent).
    Construct through the planner; this record is not an authentication boundary.
    """

    root: Path
    outputs: tuple[tuple[str, bytes], ...]
    originals: tuple[tuple[str, bytes | None], ...]
    git_checks: tuple[GitCheck, ...]
    glob_checks: tuple[GlobCheck, ...] = ()

    @property
    def changed_paths(self) -> tuple[Path, ...]:
        original = dict(self.originals)
        return tuple(self.root / name for name, data in self.outputs if original[name] != data)


def plan_outputs(root: Path, outputs: Mapping[str, bytes], *, inputs: Mapping[str, bytes], immutable: Sequence[str] = ()) -> PublicationPlan:
    """Snapshot a fully rendered multi-output publication without writing files.

    Inputs may overlap outputs only when their exact bytes remain unchanged.
    Immutable outputs may be absent or identical. The returned plan uses the
    same stale-input, rollback and recovery behavior as document publication.
    """
    root = root.resolve(strict=True)
    if not outputs or not inputs:
        raise ValueError("output publication requires candidates and validated inputs")
    if any(not isinstance(data, bytes) for data in (*outputs.values(), *inputs.values())):
        raise TypeError("publication inputs and outputs must be bytes")
    if set(immutable) - outputs.keys():
        raise ValueError("immutable files must be publication outputs")
    names = tuple(dict.fromkeys([*inputs, *outputs]))
    _validate_distinct_paths(tuple(_path(root, name) for name in names))
    originals = {}
    for name in names:
        path = _path(root, name)
        originals[name] = path.read_bytes() if path.exists() else None
    for name, expected in inputs.items():
        if originals[name] != expected or (name in outputs and outputs[name] != expected):
            raise ValueError(f"validated input changed or would be overwritten: {name}")
    for name in immutable:
        if originals[name] is not None and originals[name] != outputs[name]:
            raise ValueError(f"immutable output differs: {name}")
    plan = PublicationPlan(root, tuple(outputs.items()), tuple(originals.items()), ())
    _verify_plan(plan)
    return plan


def plan_publication(
    root: Path,
    document: str,
    markers: MarkerPair,
    content: str,
    *,
    inputs: Mapping[str, bytes],
    figures: Mapping[str, bytes] | None = None,
    references: Sequence[ReleaseRule] = (),
    git_checks: Sequence[GitCheck] = (),
    glob_checks: Sequence[GlobCheck] = (),
) -> PublicationPlan:
    """Validate all candidates without writes, directories, measurement, or network.

    Consumer adapters validate evidence schemas and scientific eligibility first,
    then supply the exact validated input bytes. Inputs must remain unchanged and
    must not alias outputs. Fixed-value ReleaseRules assert current package/report
    identities and publication links; rules against outputs inspect candidates.
    GitChecks verify configured input/reference/candidate blobs before publication.
    GlobChecks require complete selected inventories whose bytes are in inputs.
    """
    root = root.resolve(strict=True)
    if not inputs:
        raise ValueError("publication requires retained evidence inputs")
    for check in glob_checks:
        if set(check.paths) - inputs.keys():
            raise ValueError("glob checks require every selected path in validated inputs")
    writes = [(document, None), *(figures or {}).items()]
    # Check sequences before making a dictionary: never collapse duplicate paths.
    names = [name for name, _ in writes] + list(inputs)
    _validate_distinct_paths(tuple(_path(root, name) for name in names))
    originals: dict[str, bytes | None] = {}
    for name in names:
        path = _path(root, name)
        originals[name] = path.read_bytes() if path.exists() else None
    for name, expected in inputs.items():
        if not isinstance(expected, bytes):
            raise TypeError("validated publication inputs must be bytes")
        if originals[name] != expected:
            raise ValueError(f"retained publication input changed or is missing: {name}")
    original = originals[document]
    if original is None:
        raise FileNotFoundError(f"publication document is missing: {document}")
    outputs = {document: replace_section(original, markers, content), **(figures or {})}
    if any(not isinstance(data, bytes) for data in outputs.values()):
        raise TypeError("publication outputs must be bytes")
    for rule in references:
        if rule.value is None:
            raise ValueError("publication references require fixed-value ReleaseRules")
        if rule.path not in originals:
            originals[rule.path] = _path(root, rule.path).read_bytes()
        data = outputs.get(rule.path, originals[rule.path])
        if data is None:
            raise ValueError(f"publication reference is missing: {rule.path}")
        if any(match.group("value") != rule.value for match in rule.matches(data.decode("utf-8"))):
            raise ValueError(f"{rule.path}: publication reference does not match {rule.value!r}")
    _validate_distinct_paths(tuple(_path(root, name) for name in originals))
    plan = PublicationPlan(root, tuple(outputs.items()), tuple(originals.items()), tuple(git_checks), tuple(glob_checks))
    _verify_plan(plan)
    return plan


def _verify_plan(plan: PublicationPlan) -> None:
    _validate_distinct_paths(tuple(_path(plan.root, name) for name, _ in plan.originals))
    for check in plan.glob_checks:
        if _inventory(plan.root, check.patterns) != check.paths:
            raise ValueError("publication source inventory changed after planning")
    for name, expected in plan.originals:
        path = _path(plan.root, name)
        actual = path.read_bytes() if path.exists() else None
        if actual != expected:
            raise ValueError(f"publication input or output changed after planning: {name}")
    available = {name: data for name, data in plan.originals if data is not None} | dict(plan.outputs)
    for check in plan.git_checks:
        missing = set(check.paths) - available.keys()
        if missing:
            raise ValueError(f"Git publication checks select unknown files: {sorted(missing)}")
        if check.allow_missing:
            state = run_git_bytes(["--no-pager", "show-ref", "--verify", "--quiet", f"refs/tags/{check.tag}"], cwd=plan.root, check=False)
            if state.returncode == 1:
                continue
            state.check_returncode()
        verify_tagged_files(plan.root, check.tag, {name: available[name] for name in check.paths}, revision=check.revision)


def publish_publication(plan: PublicationPlan) -> tuple[Path, ...]:
    """Recheck snapshots/tags and publish changed outputs in one shared transaction.

    Shares replace_many's rollback/recovery contract. Requires exclusive writer
    control; snapshot checks do not provide locking or crash atomicity.
    """
    _verify_plan(plan)
    original = dict(plan.originals)
    changed = {plan.root / name: data for name, data in plan.outputs if data != original[name]}
    replace_many(changed)
    return tuple(changed)


def preview_publication(plan: PublicationPlan) -> str:
    """Return diffs of proposed UTF-8 outputs, or digest summaries for binary bytes."""
    original = dict(plan.originals)
    chunks = []
    for name, data in plan.outputs:
        before = original[name]
        if before == data:
            continue
        try:
            old, new = (before or b"").decode("utf-8"), data.decode("utf-8")
        except UnicodeError:
            chunks.append(f"{name}: SHA-256 {sha256(before) if before is not None else '(absent)'} -> {sha256(data)}\n")
        else:
            for line in difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile=f"a/{name}", tofile=f"b/{name}"):
                chunks.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(chunks)
