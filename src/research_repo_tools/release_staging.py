"""Durable, independently selected asset batches for one captured draft."""

from collections.abc import Callable, Sequence
from pathlib import Path

from research_repo_tools._dependency_observation import DependencyOperation
from research_repo_tools.archives import ArchiveLimits
from research_repo_tools.dependency_progress import DependencyProgress, report, reporting
from research_repo_tools.evidence import _digest, _load_json, _object, deterministic_json, sha256
from research_repo_tools.files import _validate_distinct_paths, replace_many
from research_repo_tools.process import format_exception_diagnostics
from research_repo_tools.release_assets import (
    GitHubRelease,
    ReleaseTarget,
    _asset_name,
    _observe_release_target,
    _upload_asset,
    _verified_asset,
    serialize_release_target,
)
from research_repo_tools.release_validation import redact_release_diagnostic

__all__ = ["stage_release_assets"]
_SCHEMA = "research-repo-tools/release-asset-batch/v1"


def _regular(path: Path) -> None:
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)) or (path.exists() and not path.is_file()):
        raise ValueError(f"asset batch requires regular files without links: {path}")


def _read(path: Path, limit: int) -> bytes:
    _regular(path)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("asset batch input exceeds its byte limit")
    return data


def stage_release_assets(
    root: Path,
    target: ReleaseTarget,
    assets: Sequence[Path],
    *,
    journal: Path,
    destination: Path,
    approve: bool,
    checkout: Path | None = None,
    dependencies: tuple[str, ...] = (),
    limits: ArchiveLimits = ArchiveLimits(),
    progress: DependencyProgress | None = None,
    verify_inputs: Callable[[], None] | None = None,
) -> bytes:
    """Stage exact inert files, retaining verified per-asset progress after effects.

    Retry this same call with the original journal, target, files and destination.
    Existing matching assets are downloaded and verified without another POST.
    Recorded asset IDs may never be replaced. The final transfer is retained only
    after every original asset and a fresh dependency pass have been verified.
    Exclusive writer control is required. This operation never publishes a draft.
    """
    with reporting(progress, "asset staging"):
        return _stage(
            root,
            target,
            tuple(assets),
            journal=journal,
            destination=destination,
            approve=approve,
            checkout=checkout,
            dependencies=dependencies,
            limits=limits,
            verify_inputs=verify_inputs,
        )


def _stage(
    root: Path,
    target: ReleaseTarget,
    assets: tuple[Path, ...],
    *,
    journal: Path,
    destination: Path,
    approve: bool,
    checkout: Path | None,
    dependencies: tuple[str, ...],
    limits: ArchiveLimits,
    verify_inputs: Callable[[], None] | None,
) -> bytes:
    if approve is not True or not isinstance(target, ReleaseTarget):
        raise ValueError("asset staging requires explicit approval and an independently captured ReleaseTarget")
    if not 1 <= len(assets) <= 64:
        raise ValueError("asset batch requires 1..64 independently selected files")
    _validate_distinct_paths((*assets, journal, destination))
    if checkout is not None and any(p.resolve().is_relative_to(checkout.resolve()) for p in (journal, destination)):
        raise ValueError("retain the staging journal and transfer outside the observed source checkout")
    names = tuple(_asset_name(p.name) for p in assets)
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("asset batch names must be distinct, including portable case aliases")
    payloads = {}
    for path in assets:
        payloads[path.name] = _read(path, limits.archive_bytes)
        if not payloads[path.name] or sum(map(len, payloads.values())) > limits.content_bytes:
            raise ValueError("asset batch has empty bytes or exceeds its aggregate byte limit")
    selection = {name: {"size": len(data), "sha256": sha256(data)} for name, data in payloads.items()}
    captured = _load_json(serialize_release_target(target), "release target")
    _regular(journal)
    _regular(destination)
    before = _read(journal, 1024 * 1024) if journal.exists() else None
    output_before = _read(destination, 1024 * 1024) if destination.exists() else None
    completed: dict[str, dict[str, object]] = {}
    original_dependencies: str | None = None
    if before is not None:
        record = _object(
            _load_json(before, "asset batch journal"), "asset batch journal", {"schema", "target", "inputs", "assets", "state", "dependencies_sha256"}
        )
        if (
            record["schema"] != _SCHEMA
            or deterministic_json(record["target"]) != deterministic_json(captured)
            or deterministic_json(record["inputs"]) != deterministic_json(selection)
            or record["state"] not in ("staging", "complete")
            or deterministic_json(record) != before
        ):
            raise ValueError("staging journal conflicts with the original target or input bytes")
        original_dependencies = _digest(record["dependencies_sha256"], "dependency observation digest")
        for name, raw in _object(record["assets"], "verified assets").items():
            entry = _object(raw, "verified asset", {"id", "size", "sha256"})
            if (
                name not in selection
                or type(entry["id"]) is not int
                or entry["id"] <= 0
                or type(entry["size"]) is not int
                or entry["size"] != selection[name]["size"]
            ):
                raise ValueError("staging journal has an invalid asset identity")
            if _digest(entry["sha256"], "asset digest") != selection[name]["sha256"]:
                raise ValueError("staging journal has a conflicting asset digest")
            completed[name] = entry
        if record["state"] == "complete" and set(completed) != set(selection):
            raise ValueError("complete staging journal has an incomplete asset inventory")
    if output_before is not None and (before != output_before or set(completed) != set(selection)):
        raise ValueError("existing transfer differs from the retained complete journal")

    def compare_assets(selected: tuple[Path, ...]) -> None:
        for path in selected:
            if _read(path, limits.archive_bytes) != payloads[path.name]:
                raise ValueError("asset input changed; recover the original selected bytes")

    def unchanged(selected: tuple[Path, ...] = ()) -> None:
        if verify_inputs is not None:
            verify_inputs()
        compare_assets(selected)
        _regular(journal)
        if (_read(journal, 1024 * 1024) if journal.exists() else None) != before:
            raise ValueError("staging journal changed concurrently; inspect original receipts")
        _regular(destination)
        if (_read(destination, 1024 * 1024) if destination.exists() else None) != output_before:
            raise ValueError("staging transfer changed concurrently")

    unchanged()
    operation = DependencyOperation(checkout if checkout is not None else root, dependencies)
    if original_dependencies is not None and original_dependencies != operation.digest:
        raise ValueError("staging dependencies, local inputs or installed runtime changed; reconcile the original operation")

    def observe() -> GitHubRelease:
        unchanged()
        operation.verify()
        release = _observe_release_target(root, target, checkout=checkout)
        for name, entry in completed.items():
            current = _verified_asset(release, name, payloads[name])
            if current[1] != entry["id"]:
                raise ValueError("previously verified asset ID was replaced; reconcile without overwriting")
        operation.check_inputs()
        unchanged()
        return release

    def receipt(state: str) -> bytes:
        return deterministic_json(
            {"schema": _SCHEMA, "target": captured, "inputs": selection, "assets": completed, "state": state, "dependencies_sha256": operation.digest}
        )

    def retain(state: str, *, final: bool = False, selected: tuple[Path, ...] = ()) -> bytes:
        nonlocal before
        if output_before is not None and not final:
            observe()
            unchanged(selected)
            return output_before
        data = receipt(state)
        updates = {journal: data}
        if final:
            if output_before is not None and output_before != data:
                raise ValueError("existing transfer contains different evidence")
            updates[destination] = data

        def guard() -> None:
            if final:
                operation.refresh()
            observe()
            unchanged(assets if final else selected)

        replace_many(updates, verify=guard)
        before = data
        return data

    observe()
    if before is None:
        retain("staging", selected=assets)
    else:
        unchanged(assets)
    try:
        for index, path in enumerate(assets):
            report("started", "asset", checked=index, total=len(assets), detail=path.name)

            def observe_asset(path: Path = path) -> GitHubRelease:
                release = observe()
                # Check the active input after potentially slow observations,
                # immediately before the uploader can use its captured bytes.
                unchanged((path,))
                return release

            name, identifier, size, digest = _upload_asset(root, target, path.name, payloads[path.name], limits=limits, publish=False, observe=observe_asset)
            completed[name] = {"id": identifier, "size": size, "sha256": digest}
            retain("staging", selected=(path,))
            report("verified", "asset", checked=index + 1, total=len(assets), detail=name)
        return retain("complete", final=True)
    except Exception as error:
        raise ValueError(
            f"asset staging interrupted: {redact_release_diagnostic(format_exception_diagnostics(error))}; "
            "preserve the journal and original files, inspect the reported provider state, "
            "then repeat release assets stage with the same --target, --asset, --journal and --output arguments; "
            "matching assets are verified without upload, conflicts require reconciliation"
        ) from error
