"""Independent preparation, measurement retention, and document publication."""

import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from research_repo_tools.evidence import Provenance, _load_json, _object, _string, deterministic_json, sha256
from research_repo_tools.files import _validate_distinct_paths, replace_many
from research_repo_tools.measurement import MeasurementConfig, load_measurement, resolve_revision
from research_repo_tools.performance_progress import ProgressReporter
from research_repo_tools.process import run_git_bytes
from research_repo_tools.publication import PublicationPlan, _path, _publication_name
from research_repo_tools.publication_config import _table, load_publication
from research_repo_tools.release_assets import _repository
from research_repo_tools.release_discovery import _tag_version
from research_repo_tools.release_metadata import read_package_info
from research_repo_tools.release_pairs import PairMode, ReleaseOrder, ReleasePair, resolve_pair
from research_repo_tools.releases import published_releases

__all__ = [
    "PerformanceRelease",
    "Preparation",
    "load_performance_release",
    "load_preparation",
    "load_readme_plan",
    "measure_release",
    "parse_preparation",
    "prepare_performance",
    "select_measurement_pair",
    "serialize_preparation",
]

PREPARATION_SCHEMA = "research-repo-tools/performance-preparation/v1"


@dataclass(frozen=True, slots=True)
class PerformanceRelease:
    repository: str
    measurement: str
    report: str
    publication: str
    preparation: str
    payload: str
    manifest: str
    mode: Literal["infer-release", "explicit", "current-vs-latest"] = "infer-release"
    order: ReleaseOrder = "published"
    current: str | None = None
    baseline: str | None = None

    def __post_init__(self) -> None:
        _repository(self.repository)
        for name in ("measurement", "report", "publication", "preparation", "payload", "manifest"):
            _publication_name(getattr(self, name))
        if self.mode not in ("infer-release", "explicit", "current-vs-latest") or self.order not in ("published", "version"):
            raise ValueError("invalid performance release mode/order")
        if self.mode == "explicit":
            resolve_pair("explicit", package_tag="v0.0.0", current=self.current, baseline=self.baseline)
        elif self.current is not None or self.baseline is not None:
            raise ValueError("current/baseline require explicit mode")

    def verify_paths(self, root: Path) -> None:
        """Reject aliases even when settings were constructed through Python."""
        _validate_distinct_paths(
            tuple(_path(root, getattr(self, field)) for field in ("measurement", "report", "publication", "preparation", "payload", "manifest"))
        )


def load_performance_release(root: Path, configuration: str) -> PerformanceRelease:
    """Load one schema-1 declaration for the three independently retriable stages."""
    root = root.resolve(strict=True)
    raw = _table(
        tomllib.loads(_path(root, configuration).read_bytes().decode("utf-8")),
        "performance release",
        {"schema", "repository", "measurement", "report", "publication", "preparation", "payload", "manifest"},
        {"mode", "order", "current", "baseline"},
    )
    schema = raw.pop("schema")
    if type(schema) is not int or schema != 1:
        raise ValueError("performance release schema must be integer 1")
    # Check the value before constructing the typed caller-facing declaration.
    values = {name: _string(value, name) for name, value in raw.items()}
    result = PerformanceRelease(
        repository=values["repository"],
        measurement=values["measurement"],
        report=values["report"],
        publication=values["publication"],
        preparation=values["preparation"],
        payload=values["payload"],
        manifest=values["manifest"],
        mode=cast(Literal["infer-release", "explicit", "current-vs-latest"], values.get("mode", "infer-release")),
        order=cast(ReleaseOrder, values.get("order", "published")),
        current=values.get("current"),
        baseline=values.get("baseline"),
    )
    _validate_distinct_paths(
        tuple(
            _path(root, name)
            for name in (configuration, result.measurement, result.report, result.publication, result.preparation, result.payload, result.manifest)
        )
    )
    return result


def select_measurement_pair(
    root: Path,
    configuration: str,
    *,
    repository: str | None = None,
    mode: PairMode = "infer-release",
    order: ReleaseOrder = "published",
    current: str | None = None,
    baseline: str | None = None,
) -> tuple[ReleasePair, bool]:
    """Select from package metadata and publication order, without measuring/fetching."""
    root = root.resolve(strict=True)
    if (current is None) != (baseline is None):
        raise ValueError("current and baseline tags must be provided together")
    releases = () if mode == "explicit" else published_releases(root, repository=repository)
    pair = resolve_pair(mode, package_tag="v" + read_package_info(root).version, releases=releases, order=order, current=current, baseline=baseline)
    measurement = load_measurement(root, configuration)
    policy = measurement if isinstance(measurement, MeasurementConfig) else measurement.measurement
    from research_repo_tools.suite_measurement import SuiteMeasurementPlan

    if not isinstance(measurement, SuiteMeasurementPlan):
        policy.validate_release(pair.current)
        policy.validate_release(pair.baseline)
    working_tree = mode == "current-vs-latest" or (mode == "infer-release" and not any(item.tag == pair.current for item in releases))
    return pair, working_tree


@dataclass(frozen=True, slots=True)
class Preparation:
    repository: str
    measurement: str
    measurement_sha256: str
    package_version: str
    pair: ReleasePair
    current_revision: str
    baseline_revision: str
    working_tree: bool
    mode: PairMode = "infer-release"
    order: ReleaseOrder = "published"

    def __post_init__(self) -> None:
        _repository(self.repository)
        _publication_name(self.measurement)
        if not isinstance(self.measurement_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", self.measurement_sha256) is None:
            raise ValueError("preparation requires the measurement declaration SHA-256")
        if not isinstance(self.package_version, str) or re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", self.package_version) is None:
            raise ValueError("preparation requires a stable package version")
        if not isinstance(self.pair, ReleasePair) or type(self.working_tree) is not bool:
            raise ValueError("preparation requires a release pair and boolean working-tree policy")
        Provenance(self.current_revision)
        Provenance(self.baseline_revision)
        if self.mode not in ("infer-release", "explicit", "current-vs-latest") or self.order not in ("published", "version"):
            raise ValueError("invalid preparation selection mode/order")
        if (self.mode == "explicit" and self.working_tree) or (self.mode == "current-vs-latest" and not self.working_tree):
            raise ValueError("preparation source mode disagrees with selection")
        if self.mode != "explicit" and self.pair.current != "v" + self.package_version:
            raise ValueError("preparation current label must match the selected package")
        if self.mode != "current-vs-latest" and self.pair.current == self.pair.baseline:
            raise ValueError("release preparation requires distinct releases")
        if self.mode == "infer-release" and self.working_tree and _tag_version(self.pair.current) <= _tag_version(self.pair.baseline):
            raise ValueError("prospective preparation must be newer than its baseline")

    @property
    def identities(self) -> dict[str, tuple[str, str]]:
        return {"current": (self.pair.current, self.current_revision), "baseline": (self.pair.baseline, self.baseline_revision)}

    def verify(self, root: Path) -> None:
        """Recheck local source assertions and declaration, without provider queries."""
        root = root.resolve(strict=True)
        if read_package_info(root).version != self.package_version or sha256(_path(root, self.measurement).read_bytes()) != self.measurement_sha256:
            raise ValueError("stale preparation: package or measurement declaration changed; prepare again")
        for reference, revision in (("HEAD" if self.working_tree else self.pair.current, self.current_revision), (self.pair.baseline, self.baseline_revision)):
            if resolve_revision(root, reference) != revision:
                raise ValueError(f"stale preparation: {reference} moved; prepare again")
        if self.working_tree and self.mode == "infer-release":
            present = run_git_bytes(["--no-pager", "show-ref", "--verify", "--quiet", f"refs/tags/{self.pair.current}"], cwd=root, check=False)
            if present.returncode not in (0, 1):
                present.check_returncode()
            if present.returncode == 0 and resolve_revision(root, self.pair.current) != self.current_revision:
                raise ValueError("stale preparation: prospective tag differs from captured HEAD")


def serialize_preparation(record: Preparation) -> bytes:
    return deterministic_json(
        {
            "schema": PREPARATION_SCHEMA,
            "repository": record.repository,
            "measurement": record.measurement,
            "measurement_sha256": record.measurement_sha256,
            "package_version": record.package_version,
            "working_tree": record.working_tree,
            "mode": record.mode,
            "order": record.order,
            **{phase: {"release": release, "revision": revision} for phase, (release, revision) in record.identities.items()},
        }
    )


def parse_preparation(payload: bytes) -> Preparation:
    raw = _object(
        _load_json(payload, "performance preparation"),
        "preparation",
        {"schema", "repository", "measurement", "measurement_sha256", "package_version", "working_tree", "current", "baseline", "mode", "order"},
    )
    if raw["schema"] != PREPARATION_SCHEMA:
        raise ValueError("unsupported performance preparation schema; prepare a new record")
    phases = {phase: _object(raw[phase], phase, {"release", "revision"}) for phase in ("current", "baseline")}
    return Preparation(
        _string(raw["repository"], "repository"),
        _string(raw["measurement"], "measurement"),
        _string(raw["measurement_sha256"], "measurement_sha256"),
        _string(raw["package_version"], "package_version"),
        ReleasePair(_string(phases["current"]["release"], "release"), _string(phases["baseline"]["release"], "release")),
        _string(phases["current"]["revision"], "revision"),
        _string(phases["baseline"]["revision"], "revision"),
        cast(bool, raw["working_tree"]),
        cast(PairMode, _string(raw["mode"], "mode")),
        cast(ReleaseOrder, _string(raw["order"], "order")),
    )


def _scratch(root: Path, name: str) -> Path:
    path = _path(root, name)
    tracked = run_git_bytes(["--no-pager", "ls-files", "-z", "--", name], cwd=root).stdout
    ignored = run_git_bytes(["--no-pager", "check-ignore", "--quiet", "--", name], cwd=root, check=False)
    if tracked or ignored.returncode != 0:
        raise ValueError(f"preparation must use untracked ignored scratch storage: {name}")
    return path


def load_preparation(root: Path, settings: PerformanceRelease) -> Preparation:
    """Load all four independent assertions, rejecting missing/partial/stale records."""
    root = root.resolve(strict=True)
    settings.verify_paths(root)
    record = parse_preparation(_scratch(root, settings.preparation).read_bytes())
    if (record.repository, record.measurement) != (settings.repository, settings.measurement):
        raise ValueError("preparation belongs to a different repository or measurement declaration")
    if (record.mode, record.order) != (settings.mode, settings.order):
        raise ValueError("preparation selection policy changed; prepare again")
    if settings.mode == "explicit" and record.pair != ReleasePair(settings.current or "", settings.baseline or ""):
        raise ValueError("preparation differs from the explicit release pair")
    record.verify(root)
    return record


def prepare_performance(root: Path, settings: PerformanceRelease, *, fetch_tags: bool = False, allow_git_mutations: bool = False) -> Preparation:
    """Atomically retain independently resolved assertions; never measure or publish.

    Tag fetching is opt-in and additionally requires explicit Git-mutation
    authorization. A failed fetch can affect refs but never replaces the record.
    """
    root = root.resolve(strict=True)
    if fetch_tags and not allow_git_mutations:
        raise ValueError("fetching tags requires --allow-git-mutations; omit --fetch-tags to use reviewed local tags")
    destination = _scratch(root, settings.preparation)
    settings.verify_paths(root)
    if destination.exists():
        parse_preparation(destination.read_bytes())
    if fetch_tags:
        run_git_bytes(["fetch", "--tags", "--no-write-fetch-head", "--", f"https://github.com/{settings.repository}.git"], cwd=root, timeout=600)
    declaration = _path(root, settings.measurement).read_bytes()
    pair, working_tree = select_measurement_pair(
        root,
        settings.measurement,
        repository=settings.repository,
        mode=settings.mode,
        order=settings.order,
        current=settings.current,
        baseline=settings.baseline,
    )
    record = Preparation(
        settings.repository,
        settings.measurement,
        sha256(declaration),
        read_package_info(root).version,
        pair,
        resolve_revision(root, "HEAD" if working_tree else pair.current),
        resolve_revision(root, pair.baseline),
        working_tree,
        settings.mode,
        settings.order,
    )
    record.verify(root)
    replace_many({destination: serialize_preparation(record)}, verify=lambda: record.verify(root))
    return record


def load_readme_plan(root: Path, settings: PerformanceRelease, *, identities: dict[str, tuple[str, str]] | None = None) -> PublicationPlan:
    """Plan only README/figure publication from retained evidence; never acquire it."""
    root = root.resolve(strict=True)
    if identities is not None:
        if set(identities) != {"current", "baseline"}:
            raise ValueError("README publication requires all four explicit release/revision assertions")
        return load_publication(root, settings.publication, identities=identities)
    record = load_preparation(root, settings)
    original = _path(root, settings.preparation).read_bytes()
    if parse_preparation(original) != record:
        raise ValueError("preparation changed while planning README publication")
    plan = load_publication(root, settings.publication, identities=record.identities)
    _validate_distinct_paths(tuple(_path(root, name) for name in (settings.preparation, settings.measurement, *(name for name, _ in plan.outputs))))
    originals = dict(plan.originals)
    for name, value in ((settings.preparation, original), (settings.measurement, _path(root, settings.measurement).read_bytes())):
        if name in originals and originals[name] != value:
            raise ValueError("preparation inputs changed while planning publication")
        originals[name] = value
    record.verify(root)
    return replace(
        plan,
        originals=tuple(originals.items()),
        preparation_checks=(*plan.preparation_checks, settings.preparation),
    )


def measure_release(
    root: Path,
    settings: PerformanceRelease,
    *,
    allow_git_mutations: bool = False,
    checkpoint: str | None = None,
    resume: bool = False,
    progress: ProgressReporter | None = None,
) -> tuple[Path, ...]:
    """Measure a prepared pair, retain the raw evidence, then promote its report.

    A report failure preserves successful raw acquisition. Retry promotion with
    the retained paths; README publication is an independent later operation.
    """
    from research_repo_tools.performance_acquisition import measure_and_retain
    from research_repo_tools.performance_reports import load_report_plan
    from research_repo_tools.publication import publish_publication

    root = root.resolve(strict=True)
    record = load_preparation(root, settings)
    reporter = progress or ProgressReporter()
    measure_and_retain(
        root,
        settings.measurement,
        record.pair,
        payload=settings.payload,
        manifest=settings.manifest,
        working_tree=record.working_tree,
        allow_git_mutations=allow_git_mutations,
        verify=lambda: record.verify(root),
        checkpoint=checkpoint,
        resume=resume,
        progress=reporter,
    )
    with reporter.stage("publication"):
        return publish_publication(load_report_plan(root, settings.report, payload=settings.payload, manifest=settings.manifest))
