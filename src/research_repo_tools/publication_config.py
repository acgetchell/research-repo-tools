"""Strict TOML adapter for publishing shared comparison or complete-run evidence."""

import posixpath
import tomllib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast
from urllib.parse import quote

from research_repo_tools.criterion import COMPARISON_SCHEMA, _markdown_text, _unit, parse_comparison
from research_repo_tools.evidence import Evidence, Provenance, _object, _string, compare_provenance, fingerprint_files, parse_evidence
from research_repo_tools.files import _validate_distinct_paths
from research_repo_tools.publication import (
    GitCheck,
    GlobCheck,
    MarkerPair,
    PublicationPlan,
    RunSelectionCheck,
    TableLayout,
    _inventory,
    _path,
    plan_outputs,
    plan_publication,
    render_svg,
    render_table,
)
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
    return cast(list[object], value)


def _run_layout(raw: dict[str, object], retained: Evidence):
    from research_repo_tools.complete_runs import validate_run_evidence
    from research_repo_tools.criterion import _statistic
    from research_repo_tools.run_presentation import RunLayout, RunRow, SeriesSelection

    recorded_series = {series.name: series for series in validate_run_evidence(retained).series}
    recorded_sources = {phase: dict(source.context) for phase, source in retained.sources}
    series = []
    for value in _array(raw["series"], "series"):
        item = _table(value, "series selection", {"name", "label", "rows"}, {"label-version"})
        label = _string(item["label"], "series label")
        if "label-version" in item:
            version_key = _string(item["label-version"], "label-version")
            if version_key != "release" and not version_key.startswith("dependency."):
                raise ValueError("label-version must be release or dependency.NAME")
            selected = recorded_series.get(_string(item["name"], "series name"))
            if selected is None:
                raise ValueError("version label requires a retained series")
            version = recorded_sources[selected.phase].get(version_key)
            label += " " + _string(version, f"{selected.phase} recorded {version_key}")
        series.append(
            SeriesSelection(
                _string(item["name"], "series name"),
                label,
                tuple((key, _string(full_id, "benchmark")) for key, full_id in _object(item["rows"], "series rows").items()),
            )
        )
    rows = []
    for value in _array(raw["rows"], "rows"):
        item = _table(value, "run row", {"key", "label"}, {"coordinate"})
        # RunRow validates numbers without accepting booleans or coerced strings.
        from research_repo_tools.run_presentation import _coordinate

        rows.append(
            RunRow(_string(item["key"], "row key"), _string(item["label"], "row label"), _coordinate(item["coordinate"]) if "coordinate" in item else None)
        )
    pairs = []
    if "comparisons" in raw:
        for value in _array(raw["comparisons"], "comparisons"):
            item = _table(value, "comparison", {"numerator", "denominator"})
            pairs.append((_string(item["numerator"], "numerator"), _string(item["denominator"], "denominator")))
    missing = raw.get("missing", "error")
    if missing not in ("error", "unavailable"):
        raise ValueError("missing must be error or unavailable")
    return RunLayout(
        tuple(series), tuple(rows), _statistic(raw["statistic"]), _unit(raw["unit"]), "error" if missing == "error" else "unavailable", tuple(pairs)
    )


def _run_table_style(value: object):
    from research_repo_tools.run_presentation import RunTableStyle

    raw = _table(
        value,
        "run table style",
        set(),
        {"row-heading", "intervals", "timing-legend", "comparison-columns", "ratio-headings", "reduction-headings", "ratio-suffix"},
    )
    intervals = raw.get("intervals", "inline")
    if intervals not in ("inline", "below"):
        raise ValueError("intervals must be inline or below")
    legend = raw.get("timing-legend", False)
    if type(legend) is not bool:
        raise ValueError("timing-legend must be a boolean")

    def strings(field: str, default: tuple[str, ...]) -> tuple[str, ...]:
        if field not in raw:
            return default
        values = raw[field]
        if not isinstance(values, list):
            raise ValueError(f"{field} must be an array")
        return tuple(_string(item, field) for item in cast(list[object], values))

    columns = strings("comparison-columns", ("ratio", "reduction"))
    if any(column not in ("ratio", "reduction") for column in columns):
        raise ValueError("comparison columns must be unique ratio/reduction selections")
    return RunTableStyle(
        row_heading=_string(raw.get("row-heading", "Case"), "row heading"),
        intervals="inline" if intervals == "inline" else "below",
        timing_legend=legend,
        comparison_columns=tuple("ratio" if column == "ratio" else "reduction" for column in columns),
        ratio_headings=strings("ratio-headings", ()),
        reduction_headings=strings("reduction-headings", ()),
        ratio_suffix="" if raw.get("ratio-suffix", "") == "" else _string(raw["ratio-suffix"], "ratio suffix"),
    )


def load_publication(
    root: Path,
    configuration: str,
    *,
    identities: Mapping[str, tuple[str, str]] | None = None,
    artifacts_only: bool = False,
) -> PublicationPlan:
    """Load a v1 comparison or v2 complete-run publication and plan all outputs.

    Paths are root-relative POSIX names. No measurement or network access occurs.
    Provenance pins require independent baseline/current commit and release values.
    ReleaseRule selectors bind package/report captures to those release identities.
    Optional GitHub links require a locally present current tag with exact blobs.
    Use plan_publication for historical schemas or consumer-specific rendering.
    """
    root = root.resolve(strict=True)
    identities = dict[str, tuple[str, str]]() if identities is None else dict(identities)
    if identities.keys() - {"baseline", "current"}:
        raise ValueError("publication identities must name baseline/current phases")
    for identity in identities.values():
        if not isinstance(identity, tuple) or len(identity) != 2:
            raise ValueError("publication identity requires a (release, revision) tuple")
    config_bytes = _path(root, configuration).read_bytes()
    parsed = tomllib.loads(config_bytes.decode("utf-8"))
    schema = parsed.get("schema")
    if type(schema) is not int or schema not in (1, 2):
        raise ValueError("publication schema must be integer 1 or 2")
    required = {"schema", "document", "begin", "end", "unit", "rows", "provenance", "references"}
    optional = {"svg", "links", "repository", "tag-policy", "prose-file", "current-sources", "current-harness", "formatter"}
    if schema == 1:
        required |= {"payload", "manifest"}
        optional |= {"baseline-label", "current-label"}
    else:
        required |= {"statistic", "series"}
        optional |= {"payload", "manifest", "archive", "missing", "comparisons", "plot", "csv", "table", "measurement"}
    if artifacts_only:
        if schema != 2:
            raise ValueError("artifacts-only requires complete-run publication schema 2")
        required -= {"document", "begin", "end", "references"}
        optional |= {"document", "begin", "end", "references"}
    raw = _table(
        parsed,
        "publication",
        required,
        optional,
    )
    names = {} if artifacts_only else {"document": _string(raw["document"], "document")}
    inputs = {configuration: config_bytes}
    run_checks = []
    if schema == 2 and "archive" in raw:
        from research_repo_tools.run_reports import _latest_selection

        if "payload" in raw or "manifest" in raw:
            raise ValueError("select either an archive or both payload and manifest; never fall back from scratch")
        retained, names["payload"], names["manifest"], history = _latest_selection(root, _string(raw["archive"], "archive"))
        if configuration in history:
            raise ValueError("publication configuration must not alias retained history")
        inputs.update(history)
        from research_repo_tools.complete_runs import run_identity

        run_checks.append(RunSelectionCheck(_string(raw["archive"], "archive"), run_identity(retained)))
    else:
        if not {"payload", "manifest"} <= raw.keys():
            raise ValueError("publication requires both payload and manifest, or a complete-run archive")
        names.update({key: _string(raw[key], key) for key in ("payload", "manifest")})
        _validate_distinct_paths(tuple(_path(root, name) for name in (configuration, *names.values())))
        inputs.update({name: _path(root, name).read_bytes() for name in (names["payload"], names["manifest"])})
        retained = parse_evidence(inputs[names["payload"]], inputs[names["manifest"]])
    # Do not lose aliases before the shared planner checks the complete inventory.
    _validate_distinct_paths(tuple(_path(root, name) for name in (configuration, *names.values())))
    if schema == 1 and retained.payload_schema != COMPARISON_SCHEMA:
        raise ValueError(f"publication schema 1 requires {COMPARISON_SCHEMA}; use schema 2 for complete-run evidence ({retained.payload_schema})")
    if schema == 2:
        from research_repo_tools.complete_runs import validate_run_evidence

        validate_run_evidence(retained)
        if "measurement" in raw:
            from research_repo_tools.common_measurement import validate_run_policy
            from research_repo_tools.measurement import load_measurement

            policy_name = _string(raw["measurement"], "measurement")
            _validate_distinct_paths(tuple(_path(root, name) for name in (policy_name, configuration, *names.values())))
            inputs[policy_name] = _path(root, policy_name).read_bytes()
            measurement = load_measurement(root, policy_name)
            validate_run_policy(retained, measurement, release=not artifacts_only)
            if not artifacts_only:
                if "archive" not in raw:
                    raise ValueError("policy-checked document publication requires retained immutable archive evidence")
                raw.setdefault("current-sources", list(measurement.sources))
                raw.setdefault("current-harness", list(measurement.harness))
        if not artifacts_only and dict(dict(retained.sources)["current"].context)["release"] == dict(dict(retained.sources)["baseline"].context)["release"]:
            raise ValueError("same-release local comparisons cannot be published as release documentation")
    pins = _table(raw["provenance"], "provenance", {"baseline", "current"})
    sources = dict(retained.sources)
    tag_policy = raw.get("tag-policy", "existing")
    if tag_policy not in {"existing", "prepare"}:
        raise ValueError("tag-policy must be existing or prepare")
    releases: dict[str, str] = {}
    checks = []
    for name in ("baseline", "current"):
        pin = _table(pins[name], f"provenance.{name}", set(), {"revision", "release", "source-sha256", "harness-sha256", "context", "verify-tag"})
        if name in identities:
            for key, supplied in zip(("release", "revision"), identities[name], strict=True):
                if key in pin and pin[key] != supplied:
                    raise ValueError(f"{name} {key} conflicts with configured identity")
                pin[key] = supplied
        if not {"revision", "release"} <= pin.keys():
            raise ValueError(f"{name} requires independent release and revision assertions in configuration or CLI")
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
    values = {"tag": releases["current"], "previous-tag": releases["baseline"], "version": releases["current"].removeprefix("v")}
    references = []
    selected_sources: set[str] = set()
    for item in _array(raw["references"], "references") if not artifacts_only else []:
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
    if not artifacts_only and selected_sources != set(values):
        raise ValueError("publication references must check the current version and both report tags (version, tag, previous-tag)")
    document = names.get("document", "README.md")
    if tag_policy == "prepare":
        from research_repo_tools.release_discovery import _tag_version

        if "repository" not in raw:
            raise ValueError("prepare policy requires a repository for existing-tag verification")
        if dict(sources["current"].context).get("mode") != "working-tree" or _tag_version(releases["current"]) <= _tag_version(releases["baseline"]):
            raise ValueError("prepare policy requires prospective working-tree evidence newer than its baseline")
        if not {"current-sources", "current-harness"} <= raw.keys():
            raise ValueError("prepare policy requires current-sources and current-harness inventories")
    inventories = []
    for field, attribute in (("current-sources", "source_sha256"), ("current-harness", "harness_sha256")):
        if field in raw:
            patterns = tuple(_string(item, field) for item in _array(raw[field], field))
            inventory = _inventory(root, patterns)
            import json

            recorded_key = "source-inventory" if field == "current-sources" else "harness-inventory"
            if schema == 2 and sorted(json.loads(dict(sources["current"].context)[recorded_key])) != list(inventory):
                raise ValueError(f"{field} membership differs from the measured source")
            inventories.append(GlobCheck(patterns, inventory))
            if dict(sources["current"].context).get("fingerprint-schema") != "research-repo-tools/files/v1":
                raise ValueError("current input checks require the shared fingerprint framing")
            expected = getattr(sources["current"], attribute)
            if expected is None or fingerprint_files(root, tuple(map(Path, inventory))) != expected:
                raise ValueError(f"{field} differ from the measured source")
            for name in inventory:
                inputs[name] = _path(root, name).read_bytes()
            if fingerprint_files(root, tuple(map(Path, inventory))) != expected or any(_path(root, name).read_bytes() != inputs[name] for name in inventory):
                raise ValueError(f"{field} changed while planning publication")
    figures: dict[str, bytes] = {}
    images: list[tuple[str, str]] = []
    if schema == 1:
        comparison = parse_comparison(retained.payload)
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
        content = render_table(comparison, layout)
        if "svg" in raw:
            path = _string(raw["svg"], "svg")
            figures[path] = render_svg(comparison, layout)
            images.append((path, "Baseline/current timing point ratios"))
    else:
        from research_repo_tools.run_presentation import CoordinatePlot, render_coordinate_svg, render_run_ratios, render_run_table, serialize_run_csv

        run_layout = _run_layout(raw, retained)
        content = render_run_table(retained, run_layout, style=_run_table_style(raw.get("table", {})))

        def artifact(path: str, data: bytes) -> None:
            if path in figures:
                raise ValueError("duplicate publication artifact path")
            figures[path] = data

        if "svg" in raw:
            path = _string(raw["svg"], "svg")
            artifact(path, render_run_ratios(retained, run_layout))
            images.append((path, "Selected timing point ratios"))
        if "plot" in raw:
            plot = _table(raw["plot"], "coordinate plot", {"path", "title", "x-label", "y-label"}, {"x-scale", "y-scale"})
            x_scale, y_scale = plot.get("x-scale", "linear"), plot.get("y-scale", "linear")
            if x_scale not in ("linear", "log") or y_scale not in ("linear", "log"):
                raise ValueError("axis scale must be linear or log")
            settings = CoordinatePlot(
                _string(plot["title"], "title"),
                _string(plot["x-label"], "x-label"),
                _string(plot["y-label"], "y-label"),
                "linear" if x_scale == "linear" else "log",
                "linear" if y_scale == "linear" else "log",
            )
            path = _string(plot["path"], "plot path")
            artifact(path, render_coordinate_svg(retained, run_layout, settings))
            images.append((path, settings.title))
        if "csv" in raw:
            path = _string(raw["csv"], "csv")
            artifact(path, serialize_run_csv(retained, run_layout))
    if artifacts_only:
        if not figures:
            raise ValueError("artifacts-only requires at least one plot, svg or csv output")
        if "document" in raw:
            _validate_distinct_paths(tuple(_path(root, name) for name in (_string(raw["document"], "document"), *figures)))
        for selection in run_checks:
            _validate_distinct_paths((root / selection.archive, *(_path(root, name) for name in figures)))
        plan = plan_outputs(root, figures, inputs=inputs)
        plan = replace(plan, git_checks=tuple(checks), glob_checks=tuple(inventories), run_checks=tuple(run_checks))
        from research_repo_tools.publication import _verify_plan

        _verify_plan(plan)
        return plan
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

    if schema == 2:
        content += f"\n- [Complete run evidence]({link(names['payload'])})\n- [Evidence envelope]({link(names['manifest'])})\n"
        if "csv" in raw:
            content += f"- [Selected timings CSV]({link(_string(raw['csv'], 'csv'))})\n"
    if "prose-file" in raw:
        prose_path = _string(raw["prose-file"], "prose-file")
        inputs[prose_path] = _path(root, prose_path).read_bytes()
        content += "\n" + inputs[prose_path].decode("utf-8").rstrip() + "\n"
    if images:
        content = "".join(f"![{_markdown_text(label)}]({link(path, image=True)})\n\n" for path, label in images) + content
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
        paths = tuple(dict.fromkeys([*(name for name in inputs if name != configuration), *(rule.path for rule in references), *link_paths]))
        checks.append(GitCheck(releases["current"], paths, allow_missing=tag_policy == "prepare"))
    formatter = _string(raw["formatter"], "formatter") if "formatter" in raw else None
    if formatter is not None:
        _validate_distinct_paths(tuple(_path(root, name) for name in (formatter, document, *figures)))
        inputs[formatter] = _path(root, formatter).read_bytes()
    return plan_publication(
        root,
        document,
        MarkerPair(_string(raw["begin"], "begin"), _string(raw["end"], "end")),
        content,
        inputs=inputs,
        figures=figures,
        references=references,
        git_checks=checks,
        glob_checks=inventories,
        run_checks=run_checks,
        formatter=formatter,
    )
