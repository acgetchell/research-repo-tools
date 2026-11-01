"""Offline, independently asserted conversion of complete acquisitions.

These receipts establish integrity and policy eligibility, not authentication.
Release workflows still own trusted acquisition selection and approval.
"""

import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from research_repo_tools.archives import ArchiveLimits, extract_archive
from research_repo_tools.common_measurement import validate_run_policy
from research_repo_tools.complete_runs import RUN_SCHEMA_V4, run_identity
from research_repo_tools.criterion_suites import _archive
from research_repo_tools.evidence import (
    Evidence,
    Provenance,
    _digest,
    _load_json,
    _object,
    _string,
    deterministic_json,
    parse_evidence,
    serialize_evidence,
    sha256,
)
from research_repo_tools.publication import PublicationPlan, _path, plan_outputs
from research_repo_tools.release_assets import ReleaseTarget, parse_release_target, serialize_release_target
from research_repo_tools.suite_measurement import SuiteMeasurementPlan, parse_suite_plan
from research_repo_tools.suite_runs import SuiteRun, _source, comparison_for_run

__all__ = ["REUSE_SCHEMA", "ReusePolicy", "parse_reuse_policy", "plan_reuse_archive", "serialize_reuse_policy", "verify_reuse_archive"]

REUSE_SCHEMA = "research-repo-tools/benchmark-reuse/v1"
POLICY_SCHEMA = "research-repo-tools/benchmark-reuse-policy/v1"


@dataclass(frozen=True, slots=True)
class ReusePolicy:
    """Independent acceptance assertions, never inferred from candidate evidence.

    Observations are (suite, phase, Provenance) tuples from the trusted acquisition
    handoff. Identity binds the entire original run, including sessions and times.
    Owner is an explicit orchestration policy, not a claim of verified CI trust.
    Comparisons are pairs of (phase, full_id); empty means absolute-only export.
    """

    target: ReleaseTarget
    acquisition_owner: str
    identity: str
    measurement: SuiteMeasurementPlan
    observations: tuple[tuple[str, str, Provenance], ...]
    comparisons: tuple[tuple[tuple[str, str], tuple[str, str]], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.target, ReleaseTarget) or not isinstance(self.measurement, SuiteMeasurementPlan):
            raise TypeError("reuse requires a release target and suite measurement plan")
        if self.acquisition_owner not in {"local", "github-actions"}:
            raise ValueError("acquisition owner must be local or github-actions")
        _digest(self.identity, "independently asserted run identity")
        observations = tuple((suite, phase, source) for suite, phase, source in self.observations)
        names = {suite.name for suite in self.measurement.suites}
        if not observations or len({(suite, phase) for suite, phase, _ in observations}) != len(observations):
            raise ValueError("reuse requires unique observation assertions")
        for suite, phase, source in observations:
            if suite not in names or phase not in {"baseline", "current"} or not isinstance(source, Provenance):
                raise ValueError("invalid reuse observation assertion")
            if source.source_sha256 is None or source.harness_sha256 is None:
                raise ValueError("reuse requires known source and harness identities")
        object.__setattr__(self, "observations", tuple(sorted(observations)))
        comparisons = tuple((tuple(left), tuple(right)) for left, right in self.comparisons)
        for pair in comparisons:
            if any(len(item) != 2 or item[0] not in {"baseline", "current"} for item in pair):
                raise ValueError("reuse comparisons require phase/full-ID pairs")
            for _, full_id in pair:
                _string(full_id, "comparison full ID")
        if len(set(comparisons)) != len(comparisons):
            raise ValueError("reuse comparisons must be unique")
        object.__setattr__(self, "comparisons", comparisons)


def serialize_reuse_policy(policy: ReusePolicy) -> bytes:
    """Serialize independent assertions for a trusted, separately retained handoff."""
    return deterministic_json(
        {
            "schema": POLICY_SCHEMA,
            "target": _load_json(serialize_release_target(policy.target), "release target"),
            "acquisition_owner": policy.acquisition_owner,
            "identity": policy.identity,
            "measurement": policy.measurement.document(),
            "observations": [
                {"suite": suite, "phase": phase, "source": {**asdict(source), "context": dict(source.context)}} for suite, phase, source in policy.observations
            ],
            "comparisons": policy.comparisons,
        }
    )


def parse_reuse_policy(payload: bytes) -> ReusePolicy:
    """Parse assertions without authenticating their origin or granting CI trust."""
    from research_repo_tools.complete_runs import _array

    raw = _object(
        _load_json(payload, "reuse policy"),
        "reuse policy",
        {"schema", "target", "acquisition_owner", "identity", "measurement", "observations", "comparisons"},
    )
    if raw["schema"] != POLICY_SCHEMA:
        raise ValueError("unsupported reuse policy schema")
    observations = []
    for value in _array(raw["observations"], "observation assertions"):
        item = _object(value, "observation assertion", {"suite", "phase", "source"})
        observations.append((_string(item["suite"], "suite"), _string(item["phase"], "phase"), _source(item["source"])))
    comparisons = []
    for value in _array(raw["comparisons"], "comparisons"):
        pair = _array(value, "comparison")
        if len(pair) != 2:
            raise ValueError("comparison requires two observations")
        sides = []
        for value in pair:
            side = _array(value, "comparison observation")
            if len(side) != 2:
                raise ValueError("comparison requires phase/full-ID pairs")
            sides.append((_string(side[0], "phase"), _string(side[1], "full ID")))
        comparisons.append((sides[0], sides[1]))
    return ReusePolicy(
        parse_release_target(deterministic_json(raw["target"])),
        _string(raw["acquisition_owner"], "acquisition owner"),
        _digest(raw["identity"], "run identity"),
        parse_suite_plan(_object(raw["measurement"], "measurement")),
        tuple(observations),
        tuple(comparisons),
    )


def _eligible(evidence: Evidence, policy: ReusePolicy) -> SuiteRun:
    if evidence.payload_schema != RUN_SCHEMA_V4:
        raise ValueError("benchmark reuse requires complete-run v4 with original collection sessions")
    run = validate_run_policy(evidence, policy.measurement)
    assert isinstance(run, SuiteRun)
    if run_identity(evidence) != policy.identity:
        raise ValueError("acquisition differs from independently asserted run identity")
    expected = {(suite, phase): source for suite, phase, source in policy.observations}
    if {(suite, phase) for suite, phase, _, _ in run.observations} != set(expected):
        raise ValueError("reuse observation assertion inventory differs")
    current = dict(evidence.sources)["current"]
    if current.revision != policy.target.commit or dict(current.context).get("release") != policy.target.tag:
        raise ValueError("measured current source differs from exact release target")
    current_count = 0
    for suite, phase, _, source in run.observations:
        if source != expected[suite, phase]:
            raise ValueError(f"{suite}/{phase} provenance differs from independent assertions")
        context = dict(source.context)
        if context.get("repository") != policy.target.repository or context.get("acquisition-owner") != policy.acquisition_owner:
            raise ValueError("recorded repository/acquisition owner is missing or differs")
        if phase == "current":
            current_count += 1
            receipt = _object(_load_json(context["source-export"].encode("utf-8"), "source export"), "source export")
            if (
                source.revision != policy.target.commit
                or context["release"] != policy.target.tag
                or context.get("mode") != "tag"
                or receipt["mode"] != "revision"
                or receipt["dirty"] is not False
            ):
                raise ValueError("reuse requires exact tagged revision exports; working-tree equivalence is unsupported")
    if not current_count:
        raise ValueError("reuse requires current observations")
    for left, right in policy.comparisons:
        reasons = comparison_for_run(run, left, right)
        if reasons:
            raise ValueError(f"requested reuse comparison is ineligible: {reasons}")
    return run


def _contents(evidence: Evidence, policy: ReusePolicy) -> dict[str, bytes]:
    run = _eligible(evidence, policy)
    payload, manifest = serialize_evidence(evidence)
    files = {"evidence/run.json": payload, "evidence/evidence.json": manifest}
    projection = []
    for suite, phase, sample, _ in run.observations:
        if phase != "current":
            continue
        declaration = next(item for item in policy.measurement.suites if item.name == suite)
        sample_name = declaration.measurement.measurement.sample
        for case in sample.cases:
            paths = []
            for name, content in (("benchmark.json", case.benchmark), ("estimates.json", case.estimates), ("sample.json", case.sample), *case.raw_files):
                path = f"criterion/{case.storage_path}/{sample_name}/{name}"
                if path in files:
                    raise ValueError("derived Criterion paths collide across observations")
                files[path] = content
                paths.append(path)
            projection.append({"suite": suite, "phase": phase, "full_id": case.full_id, "files": paths})
    files["reuse.json"] = deterministic_json(
        {
            "schema": REUSE_SCHEMA,
            "run_identity": policy.identity,
            "policy_sha256": sha256(serialize_reuse_policy(policy)),
            "target": _load_json(serialize_release_target(policy.target), "release target"),
            "acquisition_owner": policy.acquisition_owner,
            "projection": projection,
            "files": {name: sha256(content) for name, content in files.items()},
        }
    )
    return files


def verify_reuse_archive(archive: Path, policy: ReusePolicy, *, limits: ArchiveLimits = ArchiveLimits()) -> Evidence:
    """Validate every member against original evidence and independent assertions.

    This performs bounded temporary extraction, never acquisition, host capture,
    network access or publication. It provides no artifact authentication.
    """
    with tempfile.TemporaryDirectory(prefix="benchmark-reuse-") as temporary:
        root = Path(temporary) / "contents"
        extract_archive(archive, root, limits=limits)
        evidence = parse_evidence((root / "evidence/run.json").read_bytes(), (root / "evidence/evidence.json").read_bytes())
        expected = _contents(evidence, policy)
        actual = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        if actual != expected:
            raise ValueError("derived archive members or bytes differ from verified acquisition")
    return evidence


def plan_reuse_archive(
    root: Path,
    payload: str,
    manifest: str,
    policy: str,
    output: str,
    *,
    limits: ArchiveLimits = ArchiveLimits(),
) -> PublicationPlan:
    """Plan an immutable derived archive, snapshotting all three input files.

    Paths are portable and root-relative. Use publish_publication to revalidate
    inputs/outputs and commit the plan with shared rollback/recovery. An existing
    identical archive is a no-op; conflicting output is never overwritten.
    The policy must be independently established, not copied from these inputs.
    """
    from research_repo_tools.files import _validate_distinct_paths

    root = root.resolve(strict=True)
    names = (payload, manifest, policy, output)
    _validate_distinct_paths(tuple(_path(root, name) for name in names))
    inputs = {}
    for name in names[:3]:
        path = _path(root, name)
        if path.stat().st_size > limits.content_bytes:
            raise ValueError("reuse input exceeds content byte limit")
        with path.open("rb") as stream:
            content = stream.read(limits.content_bytes + 1)
        if len(content) > limits.content_bytes:
            raise ValueError("reuse input exceeds content byte limit")
        inputs[name] = content
    evidence = parse_evidence(inputs[payload], inputs[manifest])
    assertions = parse_reuse_policy(inputs[policy])
    content = _archive(_contents(evidence, assertions), limits)
    # The shared reader checks portable aliases, traversal, overlap and limits
    # on the captured bytes, including directory aliases on case-sensitive hosts.
    with tempfile.TemporaryDirectory(prefix="benchmark-reuse-plan-") as temporary:
        archive = Path(temporary) / "archive.tar.gz"
        archive.write_bytes(content)
        verify_reuse_archive(archive, assertions, limits=limits)
    return plan_outputs(root, {output: content}, inputs=inputs, immutable=(output,))
