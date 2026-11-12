"""Freeze local candidate assertions before any generated report is promoted."""

from collections.abc import Callable
from pathlib import Path

from research_repo_tools.candidate_hosted import AcquisitionProducer, serialize_acquisition_producer
from research_repo_tools.candidate_publication import _resume_candidate_publication, plan_candidate_publication
from research_repo_tools.candidate_reuse import CandidatePolicy, _candidate_observations, acquisition_assertion_digest, serialize_candidate_policy
from research_repo_tools.common_measurement import validate_run_policy
from research_repo_tools.complete_runs import run_identity
from research_repo_tools.evidence import parse_evidence, sha256
from research_repo_tools.files import _directory_target, _validate_distinct_paths, replace_many
from research_repo_tools.measurement import load_measurement
from research_repo_tools.performance_release import PerformanceRelease, load_preparation, serialize_preparation
from research_repo_tools.publication import PublicationPlan, _path, _verify_plan
from research_repo_tools.release_discovery import require_first_release_history
from research_repo_tools.release_metadata import read_package_info
from research_repo_tools.release_pairs import FirstRelease
from research_repo_tools.scheduled_runs import ScheduledRun
from research_repo_tools.suite_runs import SuiteRun

__all__ = ["capture_candidate_handoff", "capture_first_release_handoff"]


def capture_candidate_handoff(
    root: Path, settings: PerformanceRelease, owner: Path, *, producer: AcquisitionProducer | None = None
) -> tuple[PublicationPlan, str, str]:
    """Retain the trusted acquisition owner's original evidence, policy and receipt.

    Call before promotion from the checkout that performed the acquisition,
    under exclusive control of the original acquisition owner. The owner
    directory must be outside the checkout. Retries before promotion can only
    retain identical records. The returned publication plan has not run.
    Hosted acquisition must supply its original producer; a receiver still
    needs independent provider assertions to authenticate the artifact.
    """
    root = root.resolve(strict=True)
    preparation = load_preparation(root, settings)
    preparation_bytes = serialize_preparation(preparation)
    if producer is not None and (producer.repository, producer.commit) != (settings.repository, preparation.current_revision):
        raise ValueError("hosted producer differs from the prepared repository or current source")

    def unchanged() -> None:
        if serialize_preparation(load_preparation(root, settings)) != preparation_bytes:
            raise ValueError("preparation changed during handoff capture")

    payload, manifest = (_path(root, name).read_bytes() for name in (settings.payload, settings.manifest))
    evidence = parse_evidence(payload, manifest)
    run = validate_run_policy(evidence, load_measurement(root, settings.measurement), release=True)
    if not isinstance(run, ScheduledRun):
        raise ValueError("candidate handoff requires a declared trial schedule")
    sources = dict(evidence.sources)
    for phase, (tag, revision) in preparation.identities.items():
        context = dict(sources[phase].context)
        mode = "working-tree" if phase == "current" and preparation.working_tree else "tag"
        if (context["release"], sources[phase].revision, context["mode"]) != (tag, revision, mode):
            raise ValueError(f"retained {phase} provenance differs from preparation")
    return _freeze_handoff(
        root,
        owner,
        settings.payload,
        settings.manifest,
        payload,
        manifest,
        CandidatePolicy(
            settings.repository,
            preparation.pair.current,
            None,
            "github-actions" if producer is not None else "local",
            run_identity(evidence),
            run.plan,
            sources["current"],
            tuple((trial, suite, phase, source) for trial, suite, phase, _, source in _candidate_observations(run)),
            report=settings.report,
        ),
        verify=unchanged,
        preparation=preparation_bytes,
        producer=producer,
    )


def capture_first_release_handoff(
    root: Path,
    owner: Path,
    selection: FirstRelease,
    *,
    repository: str,
    measurement: str,
    report: str,
    payload: str,
    manifest: str,
    read_release: Callable[[str], object],
) -> tuple[PublicationPlan, str, str]:
    """Freeze a single complete acquisition while the selected release has no predecessor."""
    root = root.resolve(strict=True)
    _validate_distinct_paths(tuple(_path(root, name) for name in (measurement, report, payload, manifest)))
    require_first_release_history(read_release)
    declaration = _path(root, measurement).read_bytes()
    payload_bytes, manifest_bytes = (_path(root, name).read_bytes() for name in (payload, manifest))
    evidence = parse_evidence(payload_bytes, manifest_bytes)
    run = validate_run_policy(evidence, load_measurement(root, measurement), release=True)
    if not isinstance(run, SuiteRun) or not run.first_release:
        raise ValueError("first-release handoff requires a single acquisition without a predecessor")

    def unchanged() -> None:
        if _path(root, measurement).read_bytes() != declaration or "v" + read_package_info(root).version != selection.current:
            raise ValueError("measurement declaration or first-release package changed")

    policy = CandidatePolicy(
        repository,
        selection.current,
        None,
        "local",
        run_identity(evidence),
        run.plan,
        dict(evidence.sources)["current"],
        tuple((trial, suite, phase, source) for trial, suite, phase, _, source in _candidate_observations(run)),
        report=report,
        first_release=True,
    )
    return _freeze_handoff(root, owner, payload, manifest, payload_bytes, manifest_bytes, policy, verify=unchanged)


def _freeze_handoff(
    root: Path,
    owner: Path,
    payload_name: str,
    manifest_name: str,
    payload: bytes,
    manifest: bytes,
    policy: CandidatePolicy,
    *,
    verify: Callable[[], None],
    preparation: bytes | None = None,
    producer: AcquisitionProducer | None = None,
) -> tuple[PublicationPlan, str, str]:
    destination = owner.absolute()
    owner = _directory_target(destination)
    _validate_distinct_paths((root, owner))
    acquisition = acquisition_assertion_digest(policy)
    receipt_path = _path(owner, "publication.json")
    if receipt_path.exists():
        receipt = receipt_path.read_bytes()
        plan = _resume_candidate_publication(root, payload_name, manifest_name, policy, receipt)
    else:
        plan, receipt = plan_candidate_publication(root, payload_name, manifest_name, policy, acquisition_sha256=acquisition)
    originals = dict(plan.originals)
    if (originals[payload_name], originals[manifest_name]) != (payload, manifest):
        raise ValueError("retained evidence changed during handoff capture")
    publication = sha256(receipt)
    records = {
        "acquisition.sha256": (acquisition + "\n").encode("ascii"),
        "evidence.json": manifest,
        "policy.json": serialize_candidate_policy(policy),
        **({"preparation.json": preparation} if preparation is not None else {}),
        **({"producer.json": serialize_acquisition_producer(producer)} if producer is not None else {}),
        "publication.json": receipt,
        "publication.sha256": (publication + "\n").encode("ascii"),
        "run.json": payload,
    }

    def unchanged() -> None:
        if _directory_target(destination) != owner:
            raise ValueError("candidate owner directory changed during handoff capture")
        _validate_distinct_paths((root, owner))
        verify()
        _verify_plan(plan)
        for name, data in records.items():
            path = _path(owner, name)
            if path.exists() and path.read_bytes() != data:
                raise ValueError(f"candidate owner record already contains different bytes: {name}")

    unchanged()
    replace_many({_path(owner, name): data for name, data in records.items()}, verify=unchanged)
    return plan, acquisition, publication
