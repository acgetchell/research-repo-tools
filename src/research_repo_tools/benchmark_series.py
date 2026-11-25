"""Release-series declarations and selection from authenticated published tag trees."""

import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_repo_tools.evidence import _digest, _object, _string, deterministic_json, sha256
from research_repo_tools.release_discovery import _stable_history, _tag_version, normalize_tag
from research_repo_tools.release_pairs import ReleasePair
lazy from research_repo_tools.suite_measurement import SuiteMeasurementPlan


@dataclass(frozen=True, slots=True)
class BenchmarkSeries:
    """Consumer-owned scientific identity, inaugural tag and exact workload IDs by suite."""

    name: str
    first: str
    workloads: tuple[tuple[str, tuple[str, ...]], ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]*", self.name) or normalize_tag(self.first) != self.first:
            raise ValueError("benchmark series requires a stable lowercase ID and canonical first release")
        from research_repo_tools.measurement import _strings

        workloads = tuple((_string(name, "series suite name"), tuple(sorted(_strings(group, name)))) for name, group in self.workloads)
        suites = [name for name, _ in workloads]
        cases = [case for _, group in workloads for case in group]
        if not suites or len(set(suites)) != len(suites) or len(set(cases)) != len(cases):
            raise ValueError("benchmark series requires nonempty unique suites and workload IDs")
        object.__setattr__(self, "workloads", tuple(sorted(workloads)))

    @property
    def declaration(self) -> str:
        """Return the stable path that identifies this series across release trees."""
        return f"benchmark-series/{self.name}.toml"

    def document(self) -> dict[str, Any]:
        """Serialize the stable identity and exact workload inventory in canonical order."""
        return {"id": self.name, "first-release": self.first, "workloads": {name: list(group) for name, group in self.workloads}}

    @classmethod
    def parse(cls, value: object) -> BenchmarkSeries:
        """Read the series declaration used in measurement configuration and retained evidence."""
        from research_repo_tools.measurement import _strings

        raw = _object(value, "benchmark-series", {"id", "first-release", "workloads"})
        workloads = _object(raw["workloads"], "series workload inventory")
        return cls(
            _string(raw["id"], "series ID"),
            _string(raw["first-release"], "first release"),
            tuple((name, _strings(group, name)) for name, group in workloads.items()),
        )


@dataclass(frozen=True, slots=True)
class SeriesHistory:
    """Frozen selection evidence including every stable release and its tagged declaration."""

    declaration: str
    # Records are in descending numeric version order: release, provider ID, commit, matching contract.
    releases: tuple[tuple[str, int, str, str | None], ...]

    def __post_init__(self) -> None:
        from research_repo_tools.publication import _publication_name
        from research_repo_tools.release_assets import _sha

        _publication_name(self.declaration)
        tags = []
        ids = set()
        records = []
        for tag, identifier, commit, contract in self.releases:
            if normalize_tag(_string(tag, "series history tag")) != tag or type(identifier) is not int or identifier <= 0 or identifier in ids:
                raise ValueError("invalid or duplicate series history release")
            ids.add(identifier)
            tags.append(_tag_version(tag))
            _sha(commit)
            if contract is not None:
                _digest(contract, "series workload contract")
            records.append((tag, identifier, commit, contract))
        if tags != sorted(set(tags), reverse=True):
            raise ValueError("duplicate or unordered series history")
        object.__setattr__(self, "releases", tuple(records))

    def document(self) -> dict[str, Any]:
        """Return the release records in descending numeric version order."""
        return {"declaration": self.declaration, "releases": [list(item) for item in self.releases]}

    def context_value(self) -> str:
        """Encode a selection for a single-line provenance context value."""
        return json.dumps(self.document(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def parse(cls, value: object) -> SeriesHistory:
        """Decode the release IDs, commits, and workload digests recorded at selection."""
        raw = _object(value, "series history", {"declaration", "releases"})
        rows = raw["releases"]
        if not isinstance(rows, list) or any(not isinstance(row, list) or len(row) != 4 for row in rows):
            raise ValueError("invalid series history records")
        return cls(_string(raw["declaration"], "measurement declaration"), tuple((row[0], row[1], row[2], row[3]) for row in rows))

    @property
    def predecessor(self) -> tuple[str, str] | None:
        """Return the newest compatible published tag and its exact commit."""
        return next(((tag, commit) for tag, _, commit, contract in self.releases if contract is not None), None)

    def selection(self, plan: SuiteMeasurementPlan, current: str) -> SeriesBaseline | SeriesPair:
        """Resolve the prospective acquisition from this history and the declared workload contract."""
        series = plan.benchmark_series
        if series is None or self.declaration != series.declaration:
            raise ValueError("series history differs from its stable declaration path")
        plan.validate_release(current)
        current_version, first_version = _tag_version(current), _tag_version(series.first)
        expected = series_contract(plan)
        for tag, _, _, contract in self.releases:
            version = _tag_version(tag)
            if version >= current_version:
                raise ValueError("series candidate must be newer than published stable history")
            if contract is not None and (contract != expected or version < first_version):
                raise ValueError(f"series identity or workload contract changed at {tag}")
        if previous := self.predecessor:
            if not any(tag == series.first and contract == expected for tag, _, _, contract in self.releases):
                raise ValueError("series inaugural release missing from compatible history")
            plan.validate_release(previous[0])
            return SeriesPair(current, previous[0], self)
        if current != series.first:
            raise ValueError("no compatible predecessor for an established series; refusing an implicit reset")
        return SeriesBaseline(current, self)


@dataclass(frozen=True, slots=True)
class SeriesBaseline:
    """One prospective release of a series with verified absence of a predecessor."""

    current: str
    history: SeriesHistory

    @property
    def baseline(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class SeriesPair(ReleasePair):
    """A release pair bound to the series declaration in published tag history."""

    history: SeriesHistory


def series_contract(plan: SuiteMeasurementPlan) -> str:
    """Hash the series inventory and semantic suite contracts shared across releases."""
    if plan.benchmark_series is None:
        raise ValueError("measurement requires an explicit benchmark series")
    return sha256(deterministic_json({"series": plan.benchmark_series.document(), "contracts": {suite.name: suite.contract for suite in plan.suites}}))


def _tagged_contract(root: Path, commit: str, declaration: str, name: str) -> str | None:
    from research_repo_tools.source_exports import _git
    from research_repo_tools.suite_measurement import parse_suite_plan

    # A successful exact tree listing distinguishes an absent declaration from a missing Git object.
    listing = _git(root, "--literal-pathspecs", "ls-tree", "-z", commit, "--", declaration)
    if not listing:
        return None
    meta, _, path = listing.removesuffix(b"\0").partition(b"\t")
    if path != declaration.encode() or not re.fullmatch(rb"100(?:644|755) blob [0-9a-f]{40,64}", meta):
        raise ValueError("invalid historical benchmark declaration tree entry")
    raw = tomllib.loads(_git(root, "cat-file", "blob", meta.split()[-1].decode("ascii")).decode("utf-8"))
    plan = parse_suite_plan(raw)
    if plan.benchmark_series is None or plan.benchmark_series.name != name:
        raise ValueError("historical series identity differs from the declaration path")
    return series_contract(plan)


def _read_history(root: Path, declaration: str, plan: SuiteMeasurementPlan, read: Callable[[str], object]) -> SeriesHistory:
    from research_repo_tools.release_assets import _resolve_tag_commit

    series = plan.benchmark_series
    if series is None:
        raise ValueError("measurement requires an explicit benchmark series")
    if declaration != series.declaration:
        raise ValueError(f"series identity requires the stable declaration path {series.declaration}")
    rows = []
    for release, identifier in _stable_history(read, canonical=True):
        commit = _resolve_tag_commit(release.tag, read)
        contract = _tagged_contract(root, commit, declaration, series.name)
        rows.append((release.tag, identifier, commit, contract))
    return SeriesHistory(declaration, tuple(rows))


def select_series(root: Path, declaration: str, plan: SuiteMeasurementPlan, current: str, read: Callable[[str], object]) -> SeriesBaseline | SeriesPair:
    """Resolve the latest declared compatible release, or prove this is the inaugural tag."""
    return _read_history(root, declaration, plan, read).selection(plan, current)


def verify_series_history(
    root: Path, frozen: SeriesHistory, plan: SuiteMeasurementPlan, repository: str, tag: str, commit: str, read: Callable[[str], object]
) -> int | None:
    """Require the acquisition history plus, after publication, the exact immutable candidate. Return its provider ID when present."""
    from research_repo_tools.release_assets import _require_response_identity

    frozen.selection(plan, tag)
    observed = _read_history(root, frozen.declaration, plan, read)
    if observed == frozen:
        return None
    if not observed.releases or observed.releases[1:] != frozen.releases:
        raise ValueError("published benchmark series history changed since preparation")
    current, identifier, revision, contract = observed.releases[0]
    if (current, revision, contract) != (tag, commit, series_contract(plan)):
        raise ValueError("published benchmark series history changed: tag, commit or workload contract differs from the candidate")
    release = _object(read(f"releases/tags/{tag}"), "published series release")
    _require_response_identity(release, repository, tag, identifier)
    if release.get("draft") is not False or release.get("prerelease") is not False or release.get("immutable") is not True:
        raise ValueError("published benchmark series candidate must be an immutable stable release")
    return identifier
