"""Strict TOML adapter for publishing shared comparison evidence."""

import posixpath
import tomllib
from pathlib import Path
from urllib.parse import quote

from research_repo_tools.criterion import COMPARISON_SCHEMA, _markdown_text, _unit, parse_comparison
from research_repo_tools.evidence import Provenance, _object, _string, compare_provenance, parse_evidence
from research_repo_tools.files import _validate_distinct_paths
from research_repo_tools.publication import GitCheck, MarkerPair, PublicationPlan, TableLayout, _path, plan_publication, render_svg, render_table
from research_repo_tools.release_policy import ReleaseRule
from research_repo_tools.release_tags import _github_repo_url, validate_semver

__all__ = ["load_publication"]


def _table(value: object, context: str, required: set[str], optional: set[str] | frozenset[str] = frozenset()) -> dict[str, object]:
    table = _object(value, context)
    if required - table.keys() or table.keys() - required - optional:
        raise ValueError(f"{context}: required fields {sorted(required)}, optional fields {sorted(optional)}; found {sorted(table)}")
    return table


def _array(value: object, context: str) -> list[object]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{context} must be a nonempty array")
    return value


def load_publication(root: Path, configuration: str) -> PublicationPlan:
    """Load a v1 publication TOML file, validate retained evidence, and plan outputs.

    Paths are root-relative POSIX names. No measurement or network access occurs.
    Provenance pins require independent baseline/current commit and release values.
    ReleaseRule selectors bind package/report captures to those release identities.
    Optional GitHub links require a locally present current tag with exact blobs.
    Use plan_publication for historical schemas or consumer-specific rendering.
    """
    root = root.resolve(strict=True)
    config_bytes = _path(root, configuration).read_bytes()
    raw = _table(
        tomllib.loads(config_bytes.decode("utf-8")),
        "publication",
        {"schema", "document", "payload", "manifest", "begin", "end", "unit", "rows", "provenance", "references"},
        {"baseline-label", "current-label", "svg", "links", "repository"},
    )
    if type(raw["schema"]) is not int or raw["schema"] != 1:
        raise ValueError("publication schema must be integer 1")
    names = {key: _string(raw[key], key) for key in ("document", "payload", "manifest")}
    # Do not lose aliases before the shared planner checks the complete inventory.
    _validate_distinct_paths(tuple(_path(root, name) for name in (configuration, *names.values())))
    inputs = {name: _path(root, name).read_bytes() for name in (names["payload"], names["manifest"])}
    inputs[configuration] = config_bytes
    retained = parse_evidence(inputs[names["payload"]], inputs[names["manifest"]])
    if retained.payload_schema != COMPARISON_SCHEMA:
        raise ValueError(f"publication requires {COMPARISON_SCHEMA}; use a consumer adapter for {retained.payload_schema}")
    comparison = parse_comparison(retained.payload)
    pins = _table(raw["provenance"], "provenance", {"baseline", "current"})
    sources = dict(retained.sources)
    releases: dict[str, str] = {}
    checks = []
    for name in ("baseline", "current"):
        pin = _table(pins[name], f"provenance.{name}", {"revision", "release"}, {"source-sha256", "harness-sha256", "context", "verify-tag"})
        release = _string(pin["release"], f"{name} release")
        validate_semver(release)
        releases[name] = release
        context = {key: _string(value, f"{name} context {key}") for key, value in _object(pin.get("context", {}), f"{name} context").items()}
        if "release" in context:
            raise ValueError("provenance context.release must be configured through release")
        context["release"] = release
        expected = Provenance(
            _string(pin["revision"], f"{name} revision"),
            _string(pin["source-sha256"], "source-sha256") if "source-sha256" in pin else None,
            _string(pin["harness-sha256"], "harness-sha256") if "harness-sha256" in pin else None,
            tuple(context.items()),
        )
        fields = ["revision", *(f"context.{key}" for key in context)]
        fields.extend(key.replace("-", "_") for key in ("source-sha256", "harness-sha256") if key in pin)
        if name not in sources:
            raise ValueError(f"publication evidence is missing the {name} source")
        compatible = compare_provenance(sources[name], expected, fields=fields)
        if not compatible.compatible:
            raise ValueError(f"publication {name} provenance differs or is unknown: {compatible.differences}")
        verify = pin.get("verify-tag", False)
        if type(verify) is not bool:
            raise ValueError("provenance verify-tag must be a boolean")
        if verify:
            checks.append(GitCheck(release, revision=expected.revision))
    rows = []
    for item in _array(raw["rows"], "rows"):
        row = _table(item, "publication row", {"benchmark", "label"})
        rows.append((_string(row["benchmark"], "benchmark"), _string(row["label"], "label")))
    layout = TableLayout(
        tuple(rows),
        _string(raw.get("baseline-label", releases["baseline"]), "baseline-label"),
        _string(raw.get("current-label", releases["current"]), "current-label"),
        _unit(raw["unit"]),
    )
    values = {"tag": releases["current"], "previous-tag": releases["baseline"], "version": releases["current"].removeprefix("v")}
    references = []
    selected_sources: set[str] = set()
    for item in _array(raw["references"], "references"):
        rule = _table(item, "publication reference", {"path", "pattern", "source"}, {"count", "exclude"})
        source = _string(rule["source"], "reference source")
        if source not in values:
            raise ValueError("publication reference source must be previous-tag, tag, or version")
        selected_sources.add(source)
        count = rule.get("count", 1)
        if type(count) is not int:
            raise ValueError("publication reference count must be a positive integer")
        references.append(
            ReleaseRule(
                _string(rule["path"], "reference path"),
                _string(rule["pattern"], "reference pattern"),
                value=values[source],
                count=count,
                exclude=_string(rule["exclude"], "reference exclude") if "exclude" in rule else None,
            )
        )
    if selected_sources != set(values):
        raise ValueError("publication references must check the current version and both report tags (version, tag, previous-tag)")
    document = names["document"]
    figures: dict[str, bytes] = {}
    if "svg" in raw:
        figures[_string(raw["svg"], "svg")] = render_svg(comparison, layout)
    repository = _github_repo_url(_string(raw["repository"], "repository")) if "repository" in raw else None
    link_paths: list[str] = []

    def link(name: str, *, image: bool = False) -> str:
        _path(root, name)
        link_paths.append(name)
        if repository:
            repo = repository.removeprefix("https://github.com/")
            base = (
                f"https://raw.githubusercontent.com/{repo}/{quote(releases['current'], safe='')}/"
                if image
                else f"{repository}/blob/{quote(releases['current'], safe='')}/"
            )
            return base + quote(name, safe="/")
        # Relative to the document, including documents in nested directories.
        return quote(posixpath.relpath(name, posixpath.dirname(document) or "."), safe="/.")

    content = render_table(comparison, layout)
    if figures:
        content = f"![Baseline/current timing point ratios]({link(next(iter(figures)), image=True)})\n\n" + content
    if "links" in raw:
        content += "\n"
        for item in _array(raw["links"], "links"):
            entry = _table(item, "publication link", {"label", "path"})
            label, path = _string(entry["label"], "link label"), _string(entry["path"], "link path")
            content += f"- [{_markdown_text(label)}]({link(path)})\n"
    for name in link_paths:
        if name not in figures and name != document and name not in inputs:
            inputs[name] = _path(root, name).read_bytes()
    if repository:
        # Verify every retained artifact/reference and generated figure, whether
        # linked directly or used to establish a link's release identity.
        paths = tuple(dict.fromkeys([names["payload"], names["manifest"], *(rule.path for rule in references), *link_paths]))
        checks.append(GitCheck(releases["current"], paths))
    return plan_publication(
        root,
        document,
        MarkerPair(_string(raw["begin"], "begin"), _string(raw["end"], "end")),
        content,
        inputs=inputs,
        figures=figures,
        references=references,
        git_checks=checks,
    )
