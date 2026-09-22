"""Configured benchmark measurement and independently captured source provenance."""

import json
import platform
import re
import shutil
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from research_repo_tools.criterion import COMPARISON_SCHEMA, Sample, Statistic, Unit, _statistic, _unit, collect_sample, compare_samples, serialize_comparison
from research_repo_tools.evidence import Evidence, Provenance, _object, _string, compare_provenance, fingerprint_files
from research_repo_tools.process import cpu_description, run_command, run_command_live, run_git_bytes
from research_repo_tools.publication import _inventory, _path, _publication_name
from research_repo_tools.publication_config import _table
from research_repo_tools.release_pairs import ReleasePair
from research_repo_tools.worktrees import apply_snapshot, capture_snapshot, temporary_worktree

__all__ = ["MeasurementConfig", "capture_provenance", "load_measurement", "measure_checkout", "measure_pair", "resolve_revision"]


def _strings(value: object, context: str, *, nonempty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{context} must be an array{' with at least one entry' if nonempty else ''}")
    return tuple(_string(item, context) for item in value)


@dataclass(frozen=True, slots=True)
class MeasurementConfig:
    command: tuple[str, ...]
    sources: tuple[str, ...]
    harness: tuple[str, ...]
    criterion_dir: str = "target/criterion"
    sample: str = "new"
    statistic: Statistic = "median"
    unit: Unit = "ns"
    timeout: int = 7200
    probes: tuple[tuple[str, tuple[str, ...]], ...] = ()
    dependencies: tuple[tuple[str, str], ...] = ()
    context: tuple[tuple[str, str], ...] = ()
    compatible: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "probes", tuple((key, tuple(value)) for key, value in self.probes))
        object.__setattr__(self, "dependencies", tuple(tuple(item) for item in self.dependencies))
        object.__setattr__(self, "context", tuple(tuple(item) for item in self.context))
        for name in ("command", "sources", "harness", "compatible"):
            object.__setattr__(self, name, _strings(list(getattr(self, name)), name, nonempty=name != "compatible"))
        _publication_name(self.criterion_dir)
        _publication_name(self.sample)
        if "/" in self.sample:
            raise ValueError("sample must be a single component")
        _statistic(self.statistic)
        _unit(self.unit)
        if type(self.timeout) is not int or self.timeout <= 0:
            raise ValueError("measurement timeout must be a positive integer")
        reserved = {"release", "mode", "os", "architecture", "cpu", "command", "source-inventory", "harness-inventory", "fingerprint-schema"}
        context = dict(self.context)
        if len(context) != len(self.context) or context.keys() & reserved or any(key.startswith(("tool.", "dependency.")) for key in context):
            raise ValueError("measurement context duplicates or replaces captured metadata")
        for key, value in self.context:
            _string(key, "context name")
            _string(value, f"context {key}")
        for name, command in self.probes:
            _string(name, "probe name")
            _strings(list(command), f"probe {name}")
        if len(dict(self.probes)) != len(self.probes) or len(dict(self.dependencies)) != len(self.dependencies):
            raise ValueError("measurement probes/dependencies must have unique names")
        for name, path in self.dependencies:
            _string(name, "dependency name")
            _publication_name(path)
        if self.compatible:
            # Check field names before running any configured commands.
            unknown = Provenance("0" * 40)
            compare_provenance(unknown, unknown, fields=self.compatible)


def load_measurement(root: Path, configuration: str) -> MeasurementConfig:
    """Read strict schema-1 TOML; commands are trusted consumer configuration."""
    raw = _table(
        tomllib.loads(_path(root.resolve(), configuration).read_bytes().decode("utf-8")),
        "measurement",
        {"schema", "command", "sources", "harness"},
        {"criterion-dir", "sample", "statistic", "unit", "timeout", "probes", "dependencies", "context", "compatible"},
    )
    if type(raw["schema"]) is not int or raw["schema"] != 1:
        raise ValueError("measurement schema must be integer 1")
    timeout = raw.get("timeout", 7200)
    if type(timeout) is not int:
        raise ValueError("measurement timeout must be a positive integer")
    return MeasurementConfig(
        _strings(raw["command"], "command"),
        _strings(raw["sources"], "sources"),
        _strings(raw["harness"], "harness"),
        _string(raw.get("criterion-dir", "target/criterion"), "criterion-dir"),
        _string(raw.get("sample", "new"), "sample"),
        _statistic(raw.get("statistic", "median")),
        _unit(raw.get("unit", "ns")),
        timeout,
        tuple((key, _strings(value, f"probe {key}")) for key, value in _object(raw.get("probes", {}), "probes").items()),
        tuple((key, _string(value, f"dependency {key}")) for key, value in _object(raw.get("dependencies", {}), "dependencies").items()),
        tuple((key, _string(value, f"context {key}")) for key, value in _object(raw.get("context", {}), "context").items()),
        _strings(raw.get("compatible", []), "compatible", nonempty=False),
    )


def resolve_revision(root: Path, reference: str) -> str:
    """Resolve HEAD or an existing local stable tag without fetching."""
    if reference != "HEAD" and re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", reference) is None:
        raise ValueError("measurement reference must be HEAD or a canonical stable tag")
    selected = reference if reference == "HEAD" else f"refs/tags/{reference}"
    revision = run_git_bytes(["--no-pager", "--no-replace-objects", "rev-parse", "--verify", f"{selected}^{{commit}}"], cwd=root).stdout.decode("ascii").strip()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision) is None:
        raise ValueError(f"invalid resolved revision for {reference}")
    return revision


def capture_provenance(root: Path, configuration: MeasurementConfig, tag: str, *, mode: str) -> Provenance:
    """Capture source/harness framing, host identity, exact command and probes."""
    root = root.resolve(strict=True)
    sources, harness = _inventory(root, configuration.sources), _inventory(root, configuration.harness)
    context = dict(configuration.context)
    context.update(
        {
            "release": tag,
            "mode": mode,
            "os": platform.platform(),
            "architecture": platform.machine(),
            "command": json.dumps(configuration.command, ensure_ascii=False),
            "source-inventory": json.dumps(sources, ensure_ascii=False),
            "harness-inventory": json.dumps(harness, ensure_ascii=False),
            "fingerprint-schema": "research-repo-tools/files/v1",
        }
    )
    cpu = cpu_description()
    if cpu != "unavailable":
        context["cpu"] = " ".join(cpu.split())
    for name, command in configuration.probes:
        result = run_command(command[0], command[1:], cwd=root, timeout=30).stdout.strip()
        if not result:
            raise ValueError(f"empty tool version probe: {name}")
        context[f"tool.{name}"] = json.dumps(result, ensure_ascii=False)
    for name, path in configuration.dependencies:
        lock = tomllib.loads(_path(root, path).read_bytes().decode("utf-8"))
        versions = [entry.get("version") for entry in lock.get("package", []) if isinstance(entry, dict) and entry.get("name") == name]
        if len(versions) != 1:
            raise ValueError(f"{path} must contain exactly one {name} dependency version")
        context[f"dependency.{name}"] = _string(versions[0], f"{name} dependency version")
    return Provenance(
        resolve_revision(root, "HEAD"),
        fingerprint_files(root, tuple(map(Path, sources))),
        fingerprint_files(root, tuple(map(Path, harness))),
        tuple(context.items()),
    )


def measure_checkout(root: Path, configuration: MeasurementConfig, tag: str, *, mode: str) -> tuple[Sample, Provenance]:
    """Run the configured command live and reject source changes during measurement.

    Use a fresh isolated checkout. The selected sample must not already exist,
    preventing stale Criterion rows from being mixed with this measurement.
    This operation executes trusted consumer code and needs no write credentials.
    """
    root = root.resolve(strict=True)
    criterion = root / configuration.criterion_dir
    for ancestor in (criterion, *criterion.parents):
        if ancestor.is_relative_to(root) and ancestor.is_symlink():
            raise ValueError("Criterion output path contains a symlink")
    if criterion.exists() and collect_sample(criterion, configuration.sample, statistic=configuration.statistic, unit=configuration.unit).estimates:
        raise ValueError("measurement sample already exists; use a fresh isolated checkout")
    before = capture_provenance(root, configuration, tag, mode=mode)
    print(f"Measuring {tag}; live benchmark output follows", flush=True)
    run_command_live(configuration.command[0], configuration.command[1:], cwd=root, timeout=configuration.timeout)
    sample = collect_sample(criterion, configuration.sample, statistic=configuration.statistic, unit=configuration.unit)
    if not sample.estimates:
        raise ValueError(f"measurement produced no estimates for sample {configuration.sample}")
    after = capture_provenance(root, configuration, tag, mode=mode)
    if before != after:
        raise ValueError("measurement inputs or host/tool identity changed while the benchmark ran")
    return sample, before


def measure_pair(root: Path, configuration: MeasurementConfig, pair: ReleasePair, *, working_tree: bool = False, allow_git_mutations: bool = False) -> Evidence:
    """Measure two isolated sources and return shared retained evidence.

    Working-tree measurement uses a captured HEAD patch and nonignored new
    files. Tagged comparisons never fetch absent tags. Failure leaves no
    published evidence; cleanup failures retain recovery checkouts.
    """
    if not allow_git_mutations:
        raise ValueError("measure requires --allow-git-mutations for isolated worktree lifecycle")
    root = root.resolve(strict=True)
    baseline_revision = resolve_revision(root, pair.baseline)
    snapshot = capture_snapshot(root) if working_tree else None
    current_revision = snapshot.revision if snapshot else resolve_revision(root, pair.current)
    parent = Path(tempfile.mkdtemp(prefix="research-measurement-")).resolve()
    try:
        with temporary_worktree(root, parent / "baseline", baseline_revision, allow_git_mutations=True) as baseline:
            baseline_sample, baseline_source = measure_checkout(baseline, configuration, pair.baseline, mode="tag")
        with temporary_worktree(root, parent / "current", current_revision, allow_git_mutations=True) as current:
            if snapshot:
                apply_snapshot(current, snapshot)
            current_sample, current_source = measure_checkout(current, configuration, pair.current, mode="working-tree" if snapshot else "tag")
        if snapshot and snapshot != capture_snapshot(root):
            raise ValueError("source working tree changed while measurement ran; no evidence published")
        if configuration.compatible:
            compatibility = compare_provenance(baseline_source, current_source, fields=configuration.compatible)
            if not compatibility.compatible:
                raise ValueError(f"required benchmark provenance differs or is unknown: {compatibility.differences}")
        comparison = compare_samples(baseline_sample, current_sample)
        if not comparison.comparisons:
            raise ValueError("measurements have no common benchmark rows")
        return Evidence(serialize_comparison(comparison), COMPARISON_SCHEMA, (("baseline", baseline_source), ("current", current_source)))
    finally:
        # Never erase recovery checkouts when Git removal failed.
        if not any(parent.iterdir()):
            shutil.rmtree(parent)
