"""Acquisition and retained outputs shared by direct and prepared measurement."""

from collections.abc import Callable
from pathlib import Path

from research_repo_tools.evidence import publish_evidence
from research_repo_tools.files import _validate_distinct_paths
from research_repo_tools.measurement import MeasurementConfig, load_measurement, measure_pair
from research_repo_tools.performance_reports import render_report
from research_repo_tools.publication import _inventory, _path
from research_repo_tools.release_pairs import ReleasePair

__all__ = ["measure_and_retain"]


def measure_and_retain(
    root: Path,
    configuration_path: str,
    pair: ReleasePair,
    *,
    payload: str,
    manifest: str,
    report: str | None = None,
    working_tree: bool = False,
    allow_git_mutations: bool = False,
    backend: str | None = None,
    verify: Callable[[], None] | None = None,
) -> None:
    """Acquire with controller/ref freshness checks and atomically retain outputs."""
    root = root.resolve(strict=True)
    configuration_bytes = _path(root, configuration_path).read_bytes()
    configuration = load_measurement(root, configuration_path)
    selected_backend = backend or (configuration.backend if not isinstance(configuration, MeasurementConfig) else "worktree")
    if selected_backend == "worktree" and not allow_git_mutations:
        raise ValueError("measure requires --allow-git-mutations for the worktree backend")
    if not isinstance(configuration, MeasurementConfig) and selected_backend != configuration.backend:
        raise ValueError("backend must match the acquisition declaration")
    from research_repo_tools.source_exports import _files, capture_source

    settings_config = configuration if isinstance(configuration, MeasurementConfig) else configuration.measurement
    settings_config.validate_release(pair.baseline)
    settings_config.validate_release(pair.current)
    controller_patterns = (*settings_config.sources, *settings_config.harness, *settings_config.workloads)
    controller = _files(root, _inventory(root, controller_patterns)) if not isinstance(configuration, MeasurementConfig) and configuration.schema == 4 else None

    from research_repo_tools.host_metadata import capture_host

    controller_host = capture_host(root, probes=settings_config.probes) if controller is not None else None
    captured_references = (
        {
            "baseline": capture_source(root, pair.baseline),
            # Acquisition verifies full working-tree receipts before returning.
            # Publication rechecks refs and declared inputs, not staged outputs.
            "current": capture_source(root, "HEAD" if working_tree else pair.current),
        }
        if controller is not None
        else {}
    )

    def verify_controller() -> None:
        if verify is not None:
            verify()
        if _path(root, configuration_path).read_bytes() != configuration_bytes:
            raise ValueError("measurement declaration changed")
        if controller is not None:
            if _files(root, _inventory(root, controller_patterns)) != controller:
                raise ValueError("declared measurement inputs changed")
            if capture_host(root, probes=settings_config.probes) != controller_host:
                raise ValueError("controller host/tool identity changed")
            for captured in captured_references.values():
                captured.verify()

    verify_controller()
    protected = tuple(dict.fromkeys((configuration_path, *_inventory(root, controller_patterns))))
    targets = (payload, manifest, *([report] if report else []))
    _validate_distinct_paths(tuple(_path(root, name) for name in (*targets, *protected)))
    retained = measure_pair(
        root, configuration, pair, working_tree=working_tree, allow_git_mutations=allow_git_mutations, backend=backend, verify=verify_controller
    )
    reports = {}
    if report:
        from research_repo_tools.complete_runs import RUN_SCHEMAS, render_run

        if retained.payload_schema in RUN_SCHEMAS:
            reports[_path(root, report)] = render_run(retained, report_path=report, payload_path=payload, manifest_path=manifest)
        else:
            reports[_path(root, report)] = render_report(retained)
    publish_evidence(retained, _path(root, payload), _path(root, manifest), reports=reports, verify=verify_controller)
