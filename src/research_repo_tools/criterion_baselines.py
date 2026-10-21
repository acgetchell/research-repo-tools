"""Read-only exact-file verification of saved Criterion samples."""

import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from research_repo_tools.complete_runs import CompletePolicy, CompleteSample, _integer, collect_complete_sample
from research_repo_tools.criterion import _positive, _statistic, _unit
from research_repo_tools.criterion_inventory import parse_inventory
from research_repo_tools.evidence import _load_json, _object, _string
from research_repo_tools.measurement import _strings
from research_repo_tools.publication import _path, _publication_name
from research_repo_tools.publication_config import _table

__all__ = ["SavedSamplePlan", "load_saved_sample", "validate_saved_sample"]

_CORE_FILES = ("benchmark.json", "estimates.json", "sample.json")


def _saved_name(value: str) -> str:
    _publication_name(value)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is None or value.casefold() in {"new", "base", "change", "report"}:
        raise ValueError("saved sample must be a safe, nonreserved single directory name")
    return value


def _raw_files(values: tuple[str, ...]) -> tuple[str, ...]:
    files = _strings(values, "raw files")
    for name in files:
        _publication_name(name)
        if "/" in name or not name.endswith(".json"):
            raise ValueError("raw files must be single JSON filenames")
    keys = {unicodedata.normalize("NFC", name).casefold() for name in files}
    if len(keys) != len(files) or not set(_CORE_FILES) <= set(files):
        raise ValueError("raw files must be unique and include benchmark.json, estimates.json and sample.json")
    return tuple(sorted(files))


@dataclass(frozen=True, slots=True)
class SavedSamplePlan:
    criterion_dir: str
    saved_sample: str
    policy: CompletePolicy
    fresh_sample: str = "new"
    raw_files: tuple[str, ...] = _CORE_FILES
    storage_paths: tuple[tuple[str, str], ...] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        _publication_name(self.criterion_dir)
        _saved_name(self.saved_sample)
        _publication_name(self.fresh_sample)
        if "/" in self.fresh_sample or self.fresh_sample.casefold() == self.saved_sample.casefold():
            raise ValueError("fresh and saved samples must be distinct single components")
        if not isinstance(self.policy, CompletePolicy):
            raise TypeError("saved sample plan requires a CompletePolicy")
        object.__setattr__(self, "raw_files", _raw_files(self.raw_files))
        paths = tuple((_string(identity, "storage ID"), _publication_name(path)) for identity, path in self.storage_paths)
        if len(dict(paths)) != len(paths) or not dict(paths).keys() <= set(self.policy.expected):
            raise ValueError("storage paths must name unique expected IDs")
        keys = {tuple(unicodedata.normalize("NFC", part).casefold() for part in path.split("/")) for _, path in paths}
        if len(keys) != len(paths):
            raise ValueError("storage paths must not contain duplicate paths (including case/Unicode aliases)")
        if any(key[:end] in keys for key in keys for end in range(1, len(key))):
            raise ValueError("storage paths must not contain overlapping paths")
        object.__setattr__(self, "storage_paths", tuple(sorted(paths)))


def load_saved_sample(root: Path, configuration: str, *, saved_sample: str | None = None, criterion_dir: str | None = None) -> SavedSamplePlan:
    """Read strict saved-sample TOML and an explicit expected or retained inventory."""
    raw = _table(
        tomllib.loads(_path(root.resolve(), configuration).read_bytes().decode("utf-8")),
        "saved sample",
        {"schema"},
        {
            "saved-sample",
            "criterion-dir",
            "fresh-sample",
            "raw-files",
            "expected",
            "inventory",
            "required",
            "storage-paths",
            "sample-count",
            "sample-counts",
            "statistics",
            "confidence-level",
            "unit",
        },
    )
    if type(raw["schema"]) is not int or raw["schema"] not in (1, 2):
        raise ValueError("saved sample schema must be integer 1 or 2")
    if "sample-counts" in raw and (raw["schema"] == 1 or "sample-count" in raw):
        raise ValueError("sample-counts requires schema 2 and excludes sample-count")
    if ("expected" in raw) == ("inventory" in raw):
        raise ValueError("saved sample requires exactly one of expected or inventory")
    expected = (
        parse_inventory(_path(root.resolve(), _string(raw["inventory"], "inventory path")).read_bytes()).expected
        if "inventory" in raw
        else _strings(raw["expected"], "expected")
    )
    required = _strings(raw.get("required", []), "required", nonempty=False)
    if len(set(required)) != len(required) or not set(required) <= set(expected):
        raise ValueError("saved inventory omits required IDs or required IDs are duplicated")
    policy = CompletePolicy(
        expected,
        None if "sample-counts" in raw else _integer(raw.get("sample-count", 100), "sample count"),
        tuple(_statistic(value) for value in _strings(raw.get("statistics", ["mean", "median"]), "statistics")),
        _positive(raw.get("confidence-level", 0.95), "confidence level"),
        _unit(raw.get("unit", "ns")),
        sample_counts=tuple((key, _integer(value, "sample count")) for key, value in _object(raw.get("sample-counts", {}), "sample counts").items()),
    )
    return SavedSamplePlan(
        _string(criterion_dir if criterion_dir is not None else raw.get("criterion-dir", "target/criterion"), "criterion-dir"),
        _string(saved_sample if saved_sample is not None else raw.get("saved-sample"), "saved-sample"),
        policy,
        _string(raw.get("fresh-sample", "new"), "fresh-sample"),
        _strings(raw.get("raw-files", list(_CORE_FILES)), "raw-files"),
        storage_paths=tuple((key, _string(value, "storage path")) for key, value in _object(raw.get("storage-paths", {}), "storage paths").items()),
    )


def validate_saved_sample(
    criterion_dir: Path,
    saved_sample: str,
    policy: CompletePolicy,
    *,
    fresh_sample: str = "new",
    raw_files: tuple[str, ...] = _CORE_FILES,
    storage_paths: tuple[tuple[str, str], ...] = (),
) -> CompleteSample:
    """Require complete fresh/saved inventories and identical configured raw bytes.

    Both samples use collect_complete_sample and the same exact policy. Extra
    required JSON files (e.g. tukey.json) must parse and match byte for byte. Storage
    paths must also match, independently of semantic IDs. No files are written,
    deleted or repaired, including on rejection. Call before packaging/publication;
    a successful native benchmark exit is insufficient proof of a saved copy.
    """
    plan = SavedSamplePlan("criterion", saved_sample, policy, fresh_sample, raw_files, storage_paths=storage_paths)
    if criterion_dir.is_symlink() or criterion_dir.is_junction():
        raise ValueError("Criterion output path contains a symlink or junction")
    criterion_dir = criterion_dir.resolve(strict=True)
    fresh = collect_complete_sample(criterion_dir, plan.fresh_sample, policy)
    saved = collect_complete_sample(criterion_dir, plan.saved_sample, policy)
    for source, destination in zip(fresh.cases, saved.cases, strict=True):
        expected_path = dict(plan.storage_paths).get(source.full_id)
        if expected_path is not None and (source.storage_path != expected_path or destination.storage_path != expected_path):
            raise ValueError(f"required storage path differs for {source.full_id}: expected {expected_path}")
        if source.storage_path != destination.storage_path:
            raise ValueError(f"saved storage path differs for {source.full_id}")
        for name in plan.raw_files:
            paths = tuple(criterion_dir / source.storage_path / sample / name for sample in (plan.fresh_sample, plan.saved_sample))
            if name in _CORE_FILES:
                # Compare the immutable bytes already validated by the collector.
                data = [getattr(case, name.removesuffix(".json")) for case in (source, destination)]
            else:
                data = []
                for path in paths:
                    if not path.is_file():
                        raise ValueError(f"missing required Criterion raw file: {path}")
                    payload = path.read_bytes()
                    _load_json(payload, str(path))
                    data.append(payload)
            if data[0] != data[1]:
                raise ValueError(f"saved baseline differs from fresh measurement: {paths[1]}")
    return saved
