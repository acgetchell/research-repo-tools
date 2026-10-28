"""Verified transport of independent Criterion suites, without host recapture."""

import io
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from research_repo_tools.archives import ArchiveLimits, extract_archive
from research_repo_tools.complete_runs import CompleteSample, _integer, serialize_policy
from research_repo_tools.criterion_baselines import SavedSamplePlan, _load_saved_sample, _scope_plan, validate_saved_sample
from research_repo_tools.criterion_inventory import CriterionInventory, _directory, serialize_inventory
from research_repo_tools.evidence import Provenance, _digest, _load_json, _object, _string, deterministic_json, sha256, verify_sha256
from research_repo_tools.files import replace_many
from research_repo_tools.host_metadata import HostMetadata, parse_host, serialize_host
from research_repo_tools.publication import _path

__all__ = ["SUITE_SCHEMA", "SuiteRun", "assemble_saved_suites", "pack_saved_suite"]

SUITE_SCHEMA = "research-repo-tools/criterion-suite/v1"


@dataclass(frozen=True, slots=True)
class SuiteRun:
    """Original runner observations and caller-asserted measurement interval.

    Session must distinguish collection attempts; provenance identifies measured
    source and may retain command/features/tool/lock identity in its context.
    Times are integer UTC Unix seconds. Neither construction nor replay probes
    the host or infers freshness from file modification times.
    """

    provenance: Provenance
    session: str
    host: HostMetadata
    started_at: int
    finished_at: int

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, Provenance) or not isinstance(self.host, HostMetadata):
            raise TypeError("suite runs require Provenance and HostMetadata")
        _string(self.session, "collection session")
        if any(type(value) is not int or value < 0 for value in (self.started_at, self.finished_at)) or self.finished_at < self.started_at:
            raise ValueError("measurement times must be nonnegative integer seconds in start/end order")


def _run_document(run: SuiteRun) -> dict[str, object]:
    return {
        "provenance": {**asdict(run.provenance), "context": dict(run.provenance.context)},
        "session": run.session,
        "host": _load_json(serialize_host(run.host), "host"),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def _parse_run(value: object) -> SuiteRun:
    raw = _object(value, "suite run", {"provenance", "session", "host", "started_at", "finished_at"})
    provenance = _object(raw["provenance"], "suite provenance", {"revision", "source_sha256", "harness_sha256", "context"})
    return SuiteRun(
        Provenance(
            _string(provenance["revision"], "revision"),
            None if provenance["source_sha256"] is None else _digest(provenance["source_sha256"], "source digest"),
            None if provenance["harness_sha256"] is None else _digest(provenance["harness_sha256"], "harness digest"),
            tuple((key, _string(value, "context value")) for key, value in _object(provenance["context"], "provenance context").items()),
        ),
        _string(raw["session"], "session"),
        parse_host(deterministic_json(raw["host"])),
        _integer(raw["started_at"], "start time"),
        _integer(raw["finished_at"], "end time"),
    )


def _contract(plan: SavedSamplePlan, inventory: CriterionInventory) -> dict[str, object]:
    # Output directory is transport-specific; all evidence requirements are not.
    return {
        "inventory": _load_json(serialize_inventory(inventory), "inventory"),
        "policy": _load_json(serialize_policy(plan.policy), "policy"),
        "saved_sample": plan.saved_sample,
        "fresh_sample": plan.fresh_sample,
        "raw_files": list(plan.raw_files),
        "storage_paths": dict(plan.storage_paths),
    }


def _load(root: Path, configuration: str, saved_sample: str | None, criterion_dir: str | None = None) -> tuple[SavedSamplePlan, CriterionInventory]:
    plan, inventory = _load_saved_sample(root, configuration, saved_sample=saved_sample, criterion_dir=criterion_dir)
    if inventory is None:
        raise ValueError("suite transport requires a retained inventory")
    return plan, inventory


def _validate(directory: Path, plan: SavedSamplePlan) -> CompleteSample:
    return validate_saved_sample(
        directory, plan.saved_sample, plan.policy, fresh_sample=plan.fresh_sample, raw_files=plan.raw_files, storage_paths=plan.storage_paths
    )


def _raw_paths(sample: CompleteSample, plan: SavedSamplePlan) -> tuple[str, ...]:
    return tuple(f"{case.storage_path}/{name}/{raw}" for case in sample.cases for name in (plan.fresh_sample, plan.saved_sample) for raw in plan.raw_files)


def _archive(files: dict[str, bytes], limits: ArchiveLimits) -> bytes:
    if len(files) > limits.members or sum(map(len, files.values())) > limits.content_bytes:
        raise ValueError("suite content exceeds archive limits")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, payload in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.mode = 0o600
            archive.addfile(member, io.BytesIO(payload))
    payload = output.getvalue()
    if len(payload) > limits.archive_bytes:
        raise ValueError("suite archive exceeds archive byte limit")
    return payload


def _checked_archive(files: dict[str, bytes], plan: SavedSamplePlan, limits: ArchiveLimits) -> tuple[bytes, CompleteSample]:
    payload = _archive(files, limits)
    # Use the same portable path/alias/overlap and bounds checks for production
    # and consumption. Validate captured bytes, not a later read of the source.
    with tempfile.TemporaryDirectory(prefix="criterion-package-") as temporary:
        archive = Path(temporary) / "suite.tar.gz"
        archive.write_bytes(payload)
        tree = Path(temporary) / "tree"
        extract_archive(archive, tree, limits=limits)
        result = _validate(tree, plan)
    return payload, result


def _output(root: Path, output: str) -> Path:
    destination = _path(root, output)
    if destination.exists():
        raise ValueError("suite output already exists; select an absent path to preserve evidence")
    return destination


def pack_saved_suite(
    root: Path,
    configuration: str,
    suite: str,
    output: str,
    *,
    run: SuiteRun,
    saved_sample: str | None = None,
    criterion_dir: str | None = None,
    limits: ArchiveLimits = ArchiveLimits(),
) -> CompleteSample:
    """Validate one suite and atomically publish an absent tar.gz archive.

    Retain only configured original fresh/saved raw files, their SHA-256 digests,
    the full independent contract and the original runner's SuiteRun. Unrelated
    Criterion reports are omitted. Caller must exclude concurrent input/output
    writers and supply measurement-time provenance; this does not run benchmarks.
    Root-relative configuration/input/output paths cannot traverse links.
    """
    root = root.resolve()
    destination = _output(root, output)
    if not isinstance(run, SuiteRun):
        raise TypeError("packing requires a SuiteRun")
    plan, inventory = _load(root, configuration, saved_sample, criterion_dir)
    selected = _scope_plan(plan, inventory, suite)
    directory = _directory(root, plan.criterion_dir)
    sample = _validate(directory, selected)
    files = {name: (directory / name).read_bytes() for name in _raw_paths(sample, selected)}
    manifest = {
        "schema": SUITE_SCHEMA,
        "suite": suite,
        "contract": _contract(plan, inventory),
        "run": _run_document(run),
        "files": {name: sha256(payload) for name, payload in files.items()},
    }
    files["suite.json"] = deterministic_json(manifest)
    payload, result = _checked_archive(files, selected, limits)
    _output(root, output)
    replace_many({destination: payload})
    return result


def _read_suite(tree: Path, plan: SavedSamplePlan, inventory: CriterionInventory, revision: str, session: str) -> tuple[str, bytes, dict[str, bytes]]:
    manifest = (tree / "suite.json").read_bytes()
    raw = _object(_load_json(manifest, "suite manifest"), "suite manifest", {"schema", "suite", "contract", "run", "files"})
    if raw["schema"] != SUITE_SCHEMA or deterministic_json(raw["contract"]) != deterministic_json(_contract(plan, inventory)):
        raise ValueError("suite schema or full inventory/sampling/storage contract differs")
    suite = _string(raw["suite"], "suite")
    selected = _scope_plan(plan, inventory, suite)
    run = _parse_run(raw["run"])
    if run.provenance.revision != revision or run.session != session:
        raise ValueError("suite source revision or collection session differs")
    result = _validate(tree, selected)
    paths = set(_raw_paths(result, selected))
    digests = _object(raw["files"], "raw file digests", paths)
    actual = {path.relative_to(tree).as_posix() for path in tree.rglob("*") if path.is_file()}
    if actual != paths | {"suite.json"}:
        raise ValueError("suite archive contains unexpected or missing files")
    files = {name: (tree / name).read_bytes() for name in paths}
    for name, payload in files.items():
        verify_sha256(payload, _digest(digests[name], "raw file digest"))
    return suite, manifest, files


def assemble_saved_suites(
    root: Path,
    configuration: str,
    archives: tuple[str, ...],
    output: str,
    *,
    revision: str,
    session: str,
    saved_sample: str | None = None,
    limits: ArchiveLimits = ArchiveLimits(),
) -> CompleteSample:
    """Publish a complete archive from exactly one archive per included suite.

    Assert the full consumer contract and independent revision/session, check all
    raw digests and fresh/saved bytes, and reject portable collisions. Preserve
    original manifests under release-suites/<index>.json (sorted suite order)
    and the full criterion-inventory.json. Never probe or substitute this host.
    Different runner hosts are allowed; no cross-host comparison is authorized.
    Limits apply to each input and the combined output. Existing output is never
    replaced, and failure publishes nothing. Exclusive ownership is required.
    """
    root = root.resolve()
    destination = _output(root, output)
    Provenance(revision)
    _string(session, "collection session")
    plan, inventory = _load(root, configuration, saved_sample)
    included = set(inventory.expected)
    suites = tuple(name for name, ids in inventory.suites if included.intersection(ids))
    if len(archives) != len(suites):
        raise ValueError("one archive is required for each included suite")
    inputs = tuple(_path(root, name) for name in archives)
    files: dict[str, bytes] = {"criterion-inventory.json": serialize_inventory(inventory)}
    seen: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="criterion-assembly-") as temporary:
        for index, archive in enumerate(inputs):
            tree = Path(temporary) / str(index)
            extract_archive(archive, tree, limits=limits)
            suite, manifest, raw_files = _read_suite(tree, plan, inventory, revision, session)
            if suite in seen:
                raise ValueError(f"duplicate suite: {suite}")
            seen.add(suite)
            raw_files[f"release-suites/{suites.index(suite)}.json"] = manifest
            if files.keys() & raw_files.keys():
                raise ValueError("suite archive paths overlap")
            files.update(raw_files)
            if len(files) > limits.members or sum(map(len, files.values())) > limits.content_bytes:
                raise ValueError("combined suite content exceeds archive limits")
        if seen != set(suites):
            raise ValueError("suite archive inventory differs")
        payload, result = _checked_archive(files, plan, limits)
    _output(root, output)
    replace_many({destination: payload})
    return result
