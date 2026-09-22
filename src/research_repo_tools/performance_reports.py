"""Retained comparison reports, current selection, archival and promotion."""

import posixpath
import re
import tomllib
from pathlib import Path
from urllib.parse import quote

from research_repo_tools.criterion import COMPARISON_SCHEMA, _markdown_text, parse_comparison, render_comparison, serialize_comparison_csv
from research_repo_tools.evidence import Evidence, parse_evidence, serialize_evidence
from research_repo_tools.files import _paths_alias, _validate_distinct_paths
from research_repo_tools.publication import PublicationPlan, _path, _publication_name, plan_outputs
from research_repo_tools.publication_config import _table
from research_repo_tools.release_pairs import ReleasePair

__all__ = ["evidence_pair", "load_report_plan", "parse_report_pair", "render_report", "retained_paths"]

_HEADER = re.compile(rb"\A<!-- research-repo-tools/performance-report/v1 current=(v[0-9]+\.[0-9]+\.[0-9]+) baseline=(v[0-9]+\.[0-9]+\.[0-9]+) -->\n")


def evidence_pair(evidence: Evidence) -> ReleasePair:
    if evidence.payload_schema != COMPARISON_SCHEMA:
        raise ValueError("report requires shared comparison evidence")
    sources = dict(evidence.sources)
    if set(sources) != {"baseline", "current"}:
        raise ValueError("comparison evidence requires exactly baseline/current sources")
    try:
        return ReleasePair(dict(sources["current"].context)["release"], dict(sources["baseline"].context)["release"])
    except KeyError as error:
        raise ValueError("comparison evidence requires both release labels") from error


def parse_report_pair(report: bytes) -> ReleasePair:
    match = _HEADER.match(report)
    if match is None:
        raise ValueError("current report requires a shared report identity; preserve legacy reports at their original paths")
    return ReleasePair(match[1].decode("ascii"), match[2].decode("ascii"))


def retained_paths(archive: str, pair: ReleasePair) -> tuple[str, str, str]:
    _publication_name(archive)
    stem = f"{archive}/{pair.stem}"
    return stem + ".comparison.json", stem + ".evidence.json", stem + ".csv"


def _link(document: str, target: str) -> str:
    return quote(posixpath.relpath(target, posixpath.dirname(document) or "."), safe="/.")


def render_report(evidence: Evidence, *, title: str = "Benchmark timings", prose: str = "", links: tuple[tuple[str, str], ...] = ()) -> bytes:
    """Render retained numeric meaning and source context without measuring/I/O."""
    pair = evidence_pair(evidence)
    comparison = parse_comparison(evidence.payload)
    report = render_comparison(comparison)
    report = f"# {_markdown_text(title)}\n" + report.split("\n", 1)[1]
    lines = [
        f"<!-- research-repo-tools/performance-report/v1 current={pair.current} baseline={pair.baseline} -->",
        report.rstrip(),
        "",
        f"Current: **{pair.current}**. Baseline: **{pair.baseline}**.",
        "",
        "Recorded intervals are marginal timing intervals, not paired ratio intervals. Missing provenance stays unknown.",
    ]
    if prose:
        lines.extend(["", prose.rstrip()])
    for name, source in evidence.sources:
        lines.extend(
            [
                "",
                f"## {_markdown_text(name.title())} provenance",
                "",
                f"- Revision: `{source.revision}`",
                f"- Source fingerprint: `{source.source_sha256 or 'unknown'}`",
                f"- Harness fingerprint: `{source.harness_sha256 or 'unknown'}`",
            ]
        )
        lines.extend(f"- {_markdown_text(key)}: {_markdown_text(value)}" for key, value in source.context)
    if links:
        lines.append("")
        lines.extend(f"- [{_markdown_text(label)}]({url})" for label, url in links)
    return ("\n".join(lines) + "\n").encode("utf-8")


def load_report_plan(root: Path, configuration: str, *, payload: str | None = None, manifest: str | None = None) -> PublicationPlan:
    """Prepare promotion or offline current-report rerender using schema-1 TOML.

    Configure current, archive, title and optional prose-file. Supply both new
    evidence paths to promote; omit both to select the current report's retained
    evidence. Historical pairs are immutable, and every output moves together.
    """
    root = root.resolve(strict=True)
    config_bytes = _path(root, configuration).read_bytes()
    raw = _table(tomllib.loads(config_bytes.decode("utf-8")), "report", {"schema", "current", "archive", "title"}, {"prose-file"})
    if type(raw["schema"]) is not int or raw["schema"] != 1:
        raise ValueError("report schema must be integer 1")
    from research_repo_tools.evidence import _string

    current, archive, title = (_string(raw[name], name) for name in ("current", "archive", "title"))
    _publication_name(archive)
    current_path = _path(root, current)
    inputs = {configuration: config_bytes}
    prior = current_path.read_bytes() if current_path.exists() else None
    prior_pair = parse_report_pair(prior) if prior is not None else None
    if (payload is None) != (manifest is None):
        raise ValueError("promotion requires both payload and manifest")
    if payload is None:
        if prior_pair is None:
            raise ValueError("there is no current report to rerender")
        payload, manifest, _ = retained_paths(archive, prior_pair)
    assert manifest is not None
    inputs[payload], inputs[manifest] = _path(root, payload).read_bytes(), _path(root, manifest).read_bytes()
    evidence = parse_evidence(inputs[payload], inputs[manifest])
    pair = evidence_pair(evidence)
    if pair.current == pair.baseline:
        raise ValueError("same-release local comparisons cannot be promoted")
    prose = ""
    if "prose-file" in raw:
        name = _string(raw["prose-file"], "prose-file")
        inputs[name] = _path(root, name).read_bytes()
        prose = inputs[name].decode("utf-8")
    targets = retained_paths(archive, pair)
    _validate_distinct_paths(tuple(_path(root, name) for name in (current, *targets, f"{archive}/README.md")))
    payload_bytes, manifest_bytes = serialize_evidence(evidence)
    labels = ("Comparison evidence", "Provenance", "CSV export")
    outputs = {
        name: data for name, data in zip(targets, (payload_bytes, manifest_bytes, serialize_comparison_csv(parse_comparison(evidence.payload))), strict=True)
    }
    outputs[current] = render_report(
        evidence, title=title, prose=prose, links=tuple((label, _link(current, target)) for label, target in zip(labels, targets, strict=True))
    )
    immutable = []
    archive_path = root / archive
    archived = []
    if archive_path.exists():
        if archive_path.is_symlink() or not archive_path.is_dir():
            raise ValueError("report archive must be a regular directory")
        for path in sorted(archive_path.glob("*.md")):
            if path.name != "README.md" and not _paths_alias(path, current_path):
                name = path.relative_to(root).as_posix()
                inputs[name] = _path(root, name).read_bytes()
                archived.append(name)
    historical_report = f"{archive}/{pair.stem}.md"
    if historical_report in archived:
        immutable.extend(targets)
    if prior is not None and prior_pair != pair:
        assert prior_pair is not None
        previous_targets = retained_paths(archive, prior_pair)
        for name in previous_targets:
            inputs[name] = _path(root, name).read_bytes()
        previous = parse_evidence(inputs[previous_targets[0]], inputs[previous_targets[1]])
        if evidence_pair(previous) != prior_pair:
            raise ValueError("current report and retained evidence identify different release pairs")
        archived_name = f"{archive}/{prior_pair.stem}.md"
        archived_report = prior
        for name in previous_targets:
            old, new = _link(current, name), _link(archived_name, name)
            archived_report = archived_report.replace(f"]({old})".encode(), f"]({new})".encode())
        outputs[archived_name] = archived_report
        immutable.append(archived_name)
        archived.append(archived_name)
    index = (
        "# Archived performance reports\n\n"
        + "\n".join(f"- [{_markdown_text(Path(name).stem)}]({_link(f'{archive}/README.md', name)})" for name in sorted(set(archived)))
        + "\n"
    )
    outputs[f"{archive}/README.md"] = index.encode("utf-8")
    # The current report is both the selection input and a mutable output. Its
    # observed bytes must match the planner snapshot even if rendering is slow.
    plan = plan_outputs(root, outputs, inputs=inputs, immutable=immutable)
    if dict(plan.originals)[current] != prior:
        raise ValueError("current report changed during promotion planning")
    return plan
