"""Bounded declarative conversion of historical CSV and Criterion artifacts.

These readers have no legacy writers. Retire a consumer's conversion config
after verified shared-format companions exist for its retained originals.
"""

import csv
import io
import json
import tempfile
import tomllib
from pathlib import Path

from research_repo_tools.archives import ArchiveLimits, extract_archive
from research_repo_tools.criterion import COMPARISON_SCHEMA, Estimate, Sample, _statistic, _unit, collect_sample, compare_samples, serialize_comparison
from research_repo_tools.evidence import Evidence, Provenance, _load_json, _object, _string, sha256, verify_sha256
from research_repo_tools.measurement import _strings
from research_repo_tools.publication import _path
from research_repo_tools.publication_config import _table
from research_repo_tools.release_discovery import normalize_tag

__all__ = ["convert_csv", "read_legacy_baseline"]


def _field(document: object, path: str) -> object:
    if path == "$":
        return document
    value = document
    for name in _string(path, "legacy field selector").split("."):
        value = _object(value, f"legacy {path}")
        if name not in value:
            raise ValueError(f"legacy metadata is missing {path}")
        value = value[name]
    return value


def _validate(document: object, config: dict[str, object]) -> None:
    schema = _field(document, _string(config["schema-field"], "schema-field"))
    schemas = config["schemas"]
    if not isinstance(schemas, list) or not schemas or not any(type(schema) is type(expected) and schema == expected for expected in schemas):
        raise ValueError(f"unsupported legacy metadata schema: {schema!r}")
    for selector, expected in _object(config.get("assertions", {}), "legacy assertions").items():
        actual = _field(document, selector)
        if type(actual) is not type(expected) or actual != expected:
            raise ValueError(f"legacy assertion failed: {selector}")
    equal = config.get("equal", [])
    if not isinstance(equal, list):
        raise ValueError("legacy equal must be an array of selector pairs")
    for pair in equal:
        selectors = _strings(pair, "legacy equal selectors")
        if len(selectors) != 2:
            raise ValueError("legacy equal requires pairs of selectors")
        left, right = (_field(document, selector) for selector in selectors)
        if type(left) is not type(right) or left != right:
            raise ValueError(f"legacy identity selectors differ: {selectors}")


def _source(document: object, config: object, origin: dict[str, str]) -> Provenance:
    source = _table(config, "legacy source", {"revision", "release", "record"})
    revision = _string(_field(document, _string(source["revision"], "revision selector")), "legacy revision")
    release = normalize_tag(_string(_field(document, _string(source["release"], "release selector")), "legacy release"))
    record = _field(document, _string(source["record"], "record selector"))
    # The complete historical record remains explicitly opaque. In particular,
    # neither legacy digest is relabeled as research-repo-tools/files/v1.
    context = {**origin, "release": release, "legacy.record": json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False)}
    return Provenance(revision, context=tuple(context.items()))


def convert_csv(payload: bytes, manifest: bytes, configuration: bytes) -> Evidence:
    """Verify the original CSV hash before interpreting any measurements.

    A schema-1 TOML layout declares exact headers/constants, timing columns,
    metadata selectors, and identity assertions. Values and marginal bounds are
    parsed through the shared model. Both original hashes and the complete old
    metadata are retained in context; originals are never rewritten.
    """
    config = _table(
        tomllib.loads(configuration.decode("utf-8")),
        "legacy CSV conversion",
        {"schema", "schema-field", "schemas", "hash-field", "header", "benchmark", "baseline", "current", "statistic", "unit", "sources"},
        {"constants", "assertions", "equal", "coverage"},
    )
    if type(config["schema"]) is not int or config["schema"] != 1:
        raise ValueError("legacy conversion schema must be integer 1")
    document = _load_json(manifest, "legacy manifest")
    verify_sha256(payload, _string(_field(document, _string(config["hash-field"], "hash-field")), "legacy payload digest"))
    _validate(document, config)
    header = _strings(config["header"], "CSV header")
    if len(set(header)) != len(header):
        raise ValueError("legacy CSV header must have unique columns")
    benchmark = _string(config["benchmark"], "benchmark column")
    columns = {}
    for side in ("baseline", "current"):
        columns[side] = _table(config[side], f"legacy {side} columns", {"point", "lower", "upper"}, {"confidence-level"})
        for name in columns[side].values():
            if name not in header:
                raise ValueError(f"legacy timing column is not in the header: {name}")
    constants = _object(config.get("constants", {}), "CSV constants")
    coverage = _table(config["coverage"], "coverage", {"column", "common", "added", "missing"}) if "coverage" in config else None
    if coverage is not None and coverage["column"] not in header:
        raise ValueError("legacy coverage column must belong to the header")
    if benchmark not in header or constants.keys() - set(header):
        raise ValueError("legacy benchmark/constant columns must belong to the header")
    try:
        rows = list(csv.reader(io.StringIO(payload.decode("utf-8"), newline=""), strict=True))
    except csv.Error as error:
        raise ValueError(f"malformed legacy CSV: {error}") from error
    if not rows or tuple(rows[0]) != header or len(rows) < 2:
        raise ValueError("legacy CSV requires the exact configured header and at least one row")
    samples: dict[str, list[tuple[str, Estimate]]] = {"baseline": [], "current": []}
    names: set[str] = set()
    for values in rows[1:]:
        if len(values) != len(header):
            raise ValueError("legacy CSV row has an incorrect field count")
        row = dict(zip(header, values, strict=True))
        if any(row[key] != value for key, value in constants.items()):
            raise ValueError("legacy CSV schema, suite or scope constants differ")
        name = _string(row[benchmark], "benchmark")
        if name in names:
            raise ValueError(f"duplicate legacy benchmark: {name}")
        names.add(name)
        present = False
        for side, mapping in columns.items():
            fields = {key: row[_string(column, "timing column")] for key, column in mapping.items()}
            if not any(fields.values()):
                continue
            if not fields["point"]:
                raise ValueError(f"legacy {side} bounds require a point estimate")
            numbers = [float(fields[part]) if fields.get(part) else None for part in ("point", "lower", "upper", "confidence-level")]
            assert numbers[0] is not None
            samples[side].append((name, Estimate(numbers[0], numbers[1], numbers[2], numbers[3])))
            present = True
        if not present:
            raise ValueError("legacy benchmark has neither a baseline nor a current estimate")
        if coverage is not None:
            has_baseline = bool(row[_string(columns["baseline"]["point"], "baseline point")])
            has_current = bool(row[_string(columns["current"]["point"], "current point")])
            expected = coverage["common" if has_baseline and has_current else "added" if has_current else "missing"]
            if row[_string(coverage["column"], "coverage column")] != expected:
                raise ValueError("legacy coverage disagrees with the measured samples")
    statistic, unit = _statistic(config["statistic"]), _unit(config["unit"])
    comparison = compare_samples(Sample(tuple(samples["baseline"]), statistic, unit), Sample(tuple(samples["current"]), statistic, unit))
    origin = {
        "legacy.payload-sha256": sha256(payload),
        "legacy.manifest-sha256": sha256(manifest),
        "legacy.configuration-sha256": sha256(configuration),
        "legacy.schema": str(_field(document, _string(config["schema-field"], "schema-field"))),
        "legacy.manifest": json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False),
    }
    sources = _table(config["sources"], "legacy sources", {"baseline", "current"})
    return Evidence(
        serialize_comparison(comparison), COMPARISON_SCHEMA, tuple((side, _source(document, sources[side], origin)) for side in ("baseline", "current"))
    )


def read_legacy_baseline(archive: Path, configuration: bytes, expected_tag: str, *, limits: ArchiveLimits = ArchiveLimits()) -> tuple[Sample, Provenance]:
    """Read a historical Criterion archive through a declared metadata layout.

    No old archive is rewritten and no source fingerprint is upgraded. The
    authenticated provider is the trust boundary where historical assets lack
    independent digests; the archive's observed hash is retained as its origin.
    """
    config = _table(
        tomllib.loads(configuration.decode("utf-8")),
        "legacy baseline",
        {"schema", "schema-field", "schemas", "metadata", "criterion", "sample", "statistic", "unit", "source"},
        {"assertions", "equal"},
    )
    if type(config["schema"]) is not int or config["schema"] != 1:
        raise ValueError("legacy baseline schema must be integer 1")
    with tempfile.TemporaryDirectory(prefix="research-legacy-baseline-") as temporary:
        root = Path(temporary).resolve() / "baseline"
        extract_archive(archive, root, limits=limits)
        document = _load_json(_path(root, _string(config["metadata"], "metadata")).read_bytes(), "legacy baseline metadata")
        _validate(document, config)
        source = _source(
            document, config["source"], {"legacy.archive-sha256": sha256(archive.read_bytes()), "legacy.configuration-sha256": sha256(configuration)}
        )
        if dict(source.context)["release"] != normalize_tag(expected_tag):
            raise ValueError("legacy baseline metadata does not identify the requested release")
        criterion = _string(config["criterion"], "criterion")
        from research_repo_tools.publication import _publication_name

        _publication_name(criterion)
        sample_name = _string(config["sample"], "sample").replace("{tag}", normalize_tag(expected_tag))
        sample = collect_sample(root / criterion, sample_name, statistic=_statistic(config["statistic"]), unit=_unit(config["unit"]))
        if not sample.estimates:
            raise ValueError("legacy baseline contains no selected estimates")
        return sample, source
