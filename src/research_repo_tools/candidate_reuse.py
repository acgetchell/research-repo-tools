"""Pre-tag benchmark acceptance composed with trusted final-commit evidence.

Policies are independent assertions supplied by the trusted acquisition owner.
Neither a self-authored policy nor this integrity receipt authenticates a run.
"""

import json
import tempfile
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from research_repo_tools._dependency_observation import DependencyOperation
from research_repo_tools.archives import ArchiveLimits, extract_archive
from research_repo_tools.candidate_publication import _acquisition_input_keys, _parse_publication, _PublicationReceipt
from research_repo_tools.complete_runs import CompleteSample, _array, _integer, run_identity
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
from research_repo_tools.files import _validate_distinct_paths, replace_many
from research_repo_tools.publication import PublicationPlan, _path, plan_outputs
from research_repo_tools.release_assets import ReleaseTarget, _repository, _sha, _stable_tag, require_release_checkout
from research_repo_tools.release_discovery import require_first_release_history
from research_repo_tools.release_evidence import EvidencePolicy, verify_release_evidence
from research_repo_tools.scheduled_runs import ScheduledRun, validate_scheduled_policy
from research_repo_tools.source_exports import capture_source, validate_source_record
from research_repo_tools.suite_measurement import SuiteMeasurementPlan, parse_suite_plan
from research_repo_tools.suite_runs import SuiteRun, _source, parse_suite_run, validate_suite_policy

__all__ = [
    "CandidatePolicy",
    "acquisition_assertion_digest",
    "parse_candidate_policy",
    "plan_candidate_archive",
    "retain_candidate_release",
    "serialize_candidate_policy",
    "verify_candidate_archive",
    "verify_candidate_release",
]

POLICY_SCHEMA = "research-repo-tools/candidate-policy/v1"
ACCEPTANCE_SCHEMA = "research-repo-tools/candidate-acceptance/v1"
ARCHIVE_SCHEMA = "research-repo-tools/benchmark-reuse/v2"


@dataclass(frozen=True, slots=True)
class CandidatePolicy:
    """Trusted acquisition assertions and independently selected final commit.

    Observation keys are (one-based trial, suite, phase). No default copies assertions
    from candidate evidence. The measurement's output exceptions predate acquisition.
    Use commit=None in Step 1; replace it with the independently selected final
    commit before release verification or archive operations.
    """

    repository: str
    tag: str
    commit: str | None
    acquisition_owner: str
    identity: str
    measurement: SuiteMeasurementPlan
    candidate: Provenance
    observations: tuple[tuple[int, str, str, Provenance], ...]
    report: str | None = None
    first_release: bool = False

    def __post_init__(self) -> None:
        _repository(self.repository)
        _stable_tag(self.tag)
        if self.commit is not None:
            _sha(self.commit)
        _digest(self.identity, "independent acquisition identity")
        if self.acquisition_owner not in {"local", "github-actions"} or not isinstance(self.measurement, SuiteMeasurementPlan):
            raise ValueError("candidate reuse requires an explicit owner and a suite measurement declaration")
        if type(self.first_release) is not bool:
            raise ValueError("first_release must be a boolean")
        schedule = self.measurement.schedule
        if self.first_release and (schedule is not None or self.report is None):
            raise ValueError("first-release candidate reuse requires a single acquisition and a declared report")
        if not self.first_release and schedule is None:
            raise ValueError("paired candidate reuse requires schema-6 measurement")
        if not isinstance(self.candidate, Provenance):
            raise ValueError("candidate requires independent original source provenance")
        receipt = dict(self.candidate.context).get("source-export", "")
        validate_source_record(receipt, self.candidate.revision)
        if self.report is not None:
            from research_repo_tools.source_exports import _names

            _names(None, (self.report,))
            if self.report not in {name for name, _, _ in json.loads(receipt)["files"]}:
                raise ValueError("candidate report configuration must be captured before acquisition")
        if dict(self.candidate.context).get("release") != self.tag:
            raise ValueError("candidate label differs from final version")
        observations = tuple((trial, suite, phase, source) for trial, suite, phase, source in self.observations)
        keys = [(trial, suite, phase) for trial, suite, phase, _ in observations]
        if not keys or len(set(keys)) != len(keys):
            raise ValueError("candidate assertions must be nonempty and unique")
        for trial, suite, phase, source in observations:
            if (
                type(trial) is not int
                or not 1 <= trial <= (schedule.trials if schedule is not None else 1)
                or suite not in {s.name for s in self.measurement.suites}
                or phase not in {"baseline", "current"}
                or (self.first_release and phase != "current")
                or not isinstance(source, Provenance)
            ):
                raise ValueError("invalid candidate observation assertion")
        object.__setattr__(self, "observations", tuple(sorted(observations)))


def _provenance(source: Provenance) -> dict[str, Any]:
    return {**asdict(source), "context": dict(source.context)}


def serialize_candidate_policy(policy: CandidatePolicy) -> bytes:
    schema = POLICY_SCHEMA if policy.report is None else "research-repo-tools/candidate-policy/v2"
    if policy.first_release:
        schema = "research-repo-tools/candidate-policy/v3"
    return deterministic_json(
        {
            "schema": schema,
            **({"first_release": True} if policy.first_release else {}),
            **({"report": policy.report} if policy.report is not None else {}),
            "repository": policy.repository,
            "tag": policy.tag,
            "commit": policy.commit,
            "acquisition_owner": policy.acquisition_owner,
            "identity": policy.identity,
            "measurement": policy.measurement.document(),
            "candidate": _provenance(policy.candidate),
            "observations": [
                {"trial": trial, "suite": suite, "phase": phase, "source": _provenance(source)} for trial, suite, phase, source in policy.observations
            ],
        }
    )


def parse_candidate_policy(payload: bytes) -> CandidatePolicy:
    raw = _object(
        _load_json(payload, "candidate policy"),
        "candidate policy",
    )
    fields = {"schema", "repository", "tag", "commit", "acquisition_owner", "identity", "measurement", "candidate", "observations"}
    first = raw.get("schema") == "research-repo-tools/candidate-policy/v3"
    if first:
        fields.update(("first_release", "report"))
        if raw.get("first_release") is not True:
            raise ValueError("candidate policy v3 requires explicit first-release intent")
    elif raw.get("schema") == "research-repo-tools/candidate-policy/v2":
        fields.add("report")
    elif raw.get("schema") != POLICY_SCHEMA:
        raise ValueError("unsupported candidate policy")
    _object(raw, "candidate policy", fields)
    observations = []
    for value in _array(raw["observations"], "observations"):
        item = _object(value, "observation", {"trial", "suite", "phase", "source"})
        observations.append((_integer(item["trial"], "trial"), _string(item["suite"], "suite"), _string(item["phase"], "phase"), _source(item["source"])))
    # Envelope provenance has no harness digest; it describes the original tree.
    candidate = _object(raw["candidate"], "candidate", {"revision", "source_sha256", "harness_sha256", "context"})
    source = Provenance(
        _string(candidate["revision"], "revision"),
        _digest(candidate["source_sha256"], "source digest"),
        _digest(candidate["harness_sha256"], "harness digest") if candidate["harness_sha256"] is not None else None,
        tuple((key, _string(value, key)) for key, value in _object(candidate["context"], "context").items()),
    )
    return CandidatePolicy(
        _string(raw["repository"], "repository"),
        _string(raw["tag"], "tag"),
        _string(raw["commit"], "commit") if raw["commit"] is not None else None,
        _string(raw["acquisition_owner"], "acquisition_owner"),
        _string(raw["identity"], "identity"),
        parse_suite_plan(_object(raw["measurement"], "measurement")),
        source,
        tuple(observations),
        _string(raw["report"], "report") if "report" in raw else None,
        first,
    )


def acquisition_assertion_digest(policy: CandidatePolicy) -> str:
    """Capture under acquisition-owner control in Step 1, before final-tree edits.

    Only the independently selected final commit is deferred. Computing this
    from an untrusted candidate at Step 2 defeats authentication; obtain the
    expected value through the trusted owner's separate immutable handoff.
    """
    document = _object(_load_json(serialize_candidate_policy(policy), "policy"), "policy")
    del document["commit"]
    return sha256(deterministic_json(document))


def _candidate_observations(run: ScheduledRun | SuiteRun) -> Iterator[tuple[int, str, str, CompleteSample, Provenance]]:
    if isinstance(run, SuiteRun):
        for suite, phase, sample, source in run.observations:
            yield 1, suite, phase, sample, source
    else:
        for index, trial in enumerate(run.trials, 1):
            for suite, phase, sample, source in parse_suite_run(trial).observations:
                yield index, suite, phase, sample, source


def _eligible(evidence: Evidence, policy: CandidatePolicy) -> ScheduledRun | SuiteRun:
    run = validate_suite_policy(evidence, policy.measurement) if policy.first_release else validate_scheduled_policy(evidence, policy.measurement)
    if isinstance(run, SuiteRun) and not run.first_release:
        raise ValueError("first-release candidate requires evidence without a predecessor")
    if run_identity(evidence) != policy.identity or dict(evidence.sources)["current"] != policy.candidate:
        raise ValueError("candidate differs from independent acquisition assertions")
    actual = []
    current_count = 0
    for trial, suite, phase, _, source in _candidate_observations(run):
        actual.append((trial, suite, phase, source))
        context = dict(source.context)
        if context.get("repository") != policy.repository or context.get("acquisition-owner") != policy.acquisition_owner:
            raise ValueError("candidate repository/acquisition owner differs")
        if phase == "current":
            current_count += 1
            if context["source-export"] != dict(policy.candidate.context)["source-export"]:
                raise ValueError("candidate original capture differs across suites")
    if tuple(sorted(actual)) != policy.observations or not current_count:
        raise ValueError("candidate observation inventory/provenance differs from assertions")
    return run


def _binding(policy: CandidatePolicy, final: str, publication: _PublicationReceipt | None = None) -> dict[str, Any]:
    validate_source_record(final, _final_commit(policy))
    original = json.loads(dict(policy.candidate.context)["source-export"])
    finished = json.loads(final)
    if finished["mode"] != "revision" or finished["dirty"]:
        raise ValueError("final binding requires an independently captured commit tree")
    before = {name: (digest, bool(mode & 0o111)) for name, digest, mode in original["files"]}
    after = {name: (digest, bool(mode & 0o111)) for name, digest, mode in finished["files"]}
    outputs = set(policy.measurement.outputs)
    if policy.report is not None:
        if publication is None:
            raise ValueError("committed evidence requires a parsed publication receipt")
        generated = dict(publication.outputs)
        for name, digest in publication.outputs:
            if after.get(name) != (digest, False):
                raise ValueError(f"committed generated output differs from trusted publication: {name}")
        archive = publication.archive
        # Legacy document exceptions cannot authorize edits to old history.
        historical = {name for name in before if name.startswith(f"{archive}/runs/")}
        if any(before[name] != after.get(name) for name in historical):
            raise ValueError("historical benchmark run bytes or modes changed")
        # Reserve the complete history namespace. A legacy Markdown exception
        # must not introduce an unindexed partial run beside the new run trio.
        outputs = {name for name in outputs if not name.startswith(f"{archive}/")}
        outputs.update(generated)
    elif publication is not None:
        raise ValueError("publication receipt requires a declared candidate report")
    inputs = set()
    for _, _, phase, source in policy.observations:
        context = dict(source.context)
        for key in _acquisition_input_keys(phase):
            if key in context:
                inputs.update(_string(name, key) for name in _array(_load_json(context[key].encode("utf-8"), key), key))

    def canonical(name: str) -> str:
        return unicodedata.normalize("NFC", name).casefold()

    if {canonical(name) for name in inputs} & {canonical(name) for name in outputs}:
        raise ValueError("output exception overlaps independently observed acquisition inputs")
    for name in outputs:
        if before.get(name, (None, False))[1] or after.get(name, (None, False))[1]:
            raise ValueError("executable files cannot be declared report outputs")
    changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    if set(changed) - outputs:
        raise ValueError(f"performance-relevant or undeclared final-tree differences: {sorted(set(changed) - outputs)}")
    return {
        "candidate": original,
        "final": finished,
        "outputs": sorted(outputs),
        "changed": changed,
        "mode_policy": "git-executable-bit",
        **({"publication": publication.document(), "publication_sha256": sha256(publication.payload)} if publication is not None else {}),
    }


def _contents(evidence: Evidence, policy: CandidatePolicy, binding: dict[str, Any]) -> dict[str, bytes]:
    run = _eligible(evidence, policy)
    publication = _parse_publication(deterministic_json(binding["publication"]), policy) if "publication" in binding else None
    if binding != _binding(policy, json.dumps(binding.get("final")), publication):
        raise ValueError("candidate binding differs")
    payload, manifest = serialize_evidence(evidence)
    files = {"evidence/run.json": payload, "evidence/evidence.json": manifest}
    for trial, suite, phase, sample, _ in _candidate_observations(run):
        if phase != "current":
            continue
        declaration = next(s for s in policy.measurement.suites if s.name == suite)
        for case in sample.cases:
            for name, data in (("benchmark.json", case.benchmark), ("estimates.json", case.estimates), ("sample.json", case.sample), *case.raw_files):
                path = f"criterion/trial-{trial}/{suite}/{case.storage_path}/{declaration.measurement.measurement.sample}/{name}"
                if path in files:
                    raise ValueError("candidate archive storage collision")
                files[path] = data
    files["reuse.json"] = deterministic_json(
        {
            "schema": ARCHIVE_SCHEMA,
            "policy_sha256": sha256(serialize_candidate_policy(policy)),
            "binding": binding,
            "files": {name: sha256(data) for name, data in files.items()},
        }
    )
    return files


def _final_commit(policy: CandidatePolicy) -> str:
    if policy.commit is None:
        raise ValueError("candidate release requires an independently selected final commit")
    return policy.commit


def _acceptance(payload: bytes, policy: CandidatePolicy) -> dict[str, Any]:
    _final_commit(policy)
    raw = _object(
        _load_json(payload, "candidate acceptance"), "candidate acceptance", {"schema", "policy_sha256", "identity", "binding", "native", "archive_sha256"}
    )
    if raw["schema"] != ACCEPTANCE_SCHEMA or raw["policy_sha256"] != sha256(serialize_candidate_policy(policy)) or raw["identity"] != policy.identity:
        raise ValueError("candidate acceptance policy/identity changed")
    _digest(raw["archive_sha256"], "accepted archive")
    return raw


def verify_candidate_release(
    root: Path,
    evidence: Evidence,
    policy: CandidatePolicy,
    native_policy: EvidencePolicy,
    read: Callable[[str], Any],
    *,
    acquisition_sha256: str,
    publication_receipt: bytes | None = None,
    publication_sha256: str | None = None,
    expected: bytes | None = None,
    reproduced_archive: Path | None = None,
    reproduced_tools: Mapping[str, str] | None = None,
    limits: ArchiveLimits = ArchiveLimits(),
    dependencies: tuple[str, ...] = (),
) -> bytes:
    """Verify without retention; obtain fresh dependency proof at entry and return."""
    return _verify_candidate_release(
        root,
        evidence,
        policy,
        native_policy,
        read,
        acquisition_sha256=acquisition_sha256,
        publication_receipt=publication_receipt,
        publication_sha256=publication_sha256,
        expected=expected,
        reproduced_archive=reproduced_archive,
        reproduced_tools=reproduced_tools,
        limits=limits,
        dependencies=dependencies,
    )


def retain_candidate_release(
    root: Path,
    evidence: Evidence,
    policy: CandidatePolicy,
    native_policy: EvidencePolicy,
    read: Callable[[str], Any],
    *,
    destination: Path,
    acquisition_sha256: str,
    publication_receipt: bytes | None = None,
    publication_sha256: str | None = None,
    expected: bytes | None = None,
    policy_output: Path | None = None,
    native_output: Path | None = None,
    reproduced_archive: Path | None = None,
    reproduced_tools: Mapping[str, str] | None = None,
    verify_inputs: Callable[[], None] | None = None,
    verify_acceptance: Callable[[bytes], None] | None = None,
    dependencies: tuple[str, ...] = (),
    limits: ArchiveLimits = ArchiveLimits(),
) -> bytes:
    """Verify and freeze acceptance, optional final policy and native selection.

    Two fresh dependency passes bracket native verification and guarded durable
    retention. An in-memory final policy need not be published beforehand. All
    destinations are immutable: matching retries succeed, conflicting bytes fail.
    The caller owns exclusive writer control and independent input authentication.
    ``verify_inputs`` adds a guard; it cannot replace the built-in checks.
    """
    return _retain_candidate_release(
        root,
        evidence,
        policy,
        native_policy,
        read,
        destination=destination,
        acquisition_sha256=acquisition_sha256,
        publication_receipt=publication_receipt,
        publication_sha256=publication_sha256,
        expected=expected,
        policy_output=policy_output,
        native_output=native_output,
        reproduced_archive=reproduced_archive,
        reproduced_tools=reproduced_tools,
        verify_inputs=verify_inputs,
        verify_acceptance=verify_acceptance,
        dependencies=dependencies,
        limits=limits,
    )


def _retain_candidate_release(
    root: Path,
    evidence: Evidence,
    policy: CandidatePolicy,
    native_policy: EvidencePolicy,
    read: Callable[[str], Any],
    *,
    destination: Path,
    acquisition_sha256: str,
    publication_receipt: bytes | None = None,
    publication_sha256: str | None = None,
    expected: bytes | None = None,
    policy_output: Path | None = None,
    native_output: Path | None = None,
    reproduced_archive: Path | None = None,
    reproduced_tools: Mapping[str, str] | None = None,
    verify_inputs: Callable[[], None] | None = None,
    verify_acceptance: Callable[[bytes], None] | None = None,
    dependencies: tuple[str, ...] = (),
    limits: ArchiveLimits = ArchiveLimits(),
    entry: DependencyOperation | None = None,
) -> bytes:
    paths = (destination, *(() if policy_output is None else (policy_output,)), *(() if native_output is None else (native_output,)))
    _validate_distinct_paths(paths)

    def original(path: Path) -> bytes | None:
        if any(item.is_symlink() or item.is_junction() for item in (path, *path.parents)) or (path.exists() and not path.is_file()):
            raise ValueError("candidate retention requires regular destinations without links")
        return path.read_bytes() if path.exists() else None

    originals = {path: original(path) for path in paths}

    def retain(accepted: bytes, verify: Callable[[], None]) -> None:
        if verify_acceptance is not None:
            verify_acceptance(accepted)
        outputs = {destination: accepted}
        if policy_output is not None:
            outputs[policy_output] = serialize_candidate_policy(policy)
        if native_output is not None:
            outputs[native_output] = deterministic_json(_object(_load_json(accepted, "candidate acceptance"), "acceptance")["native"])
        if any(before is not None and before != outputs[path] for path, before in originals.items()):
            raise ValueError("candidate retention already contains different evidence; preserve the original selection")

        def guard() -> None:
            if any(original(path) != before for path, before in originals.items()):
                raise ValueError("candidate retention changed during verification")
            if verify_inputs is not None:
                verify_inputs()
            verify()
            if verify_acceptance is not None:
                verify_acceptance(accepted)
            if verify_inputs is not None:
                verify_inputs()
            if any(original(path) != before for path, before in originals.items()):
                raise ValueError("candidate retention changed during verification")

        replace_many(outputs, verify=guard)

    if verify_inputs is not None:
        verify_inputs()
    return _verify_candidate_release(
        root,
        evidence,
        policy,
        native_policy,
        read,
        acquisition_sha256=acquisition_sha256,
        publication_receipt=publication_receipt,
        publication_sha256=publication_sha256,
        expected=expected,
        dependencies=dependencies,
        reproduced_archive=reproduced_archive,
        reproduced_tools=reproduced_tools,
        limits=limits,
        retain=retain,
        entry=entry,
    )


def _verify_candidate_release(
    root: Path,
    evidence: Evidence,
    policy: CandidatePolicy,
    native_policy: EvidencePolicy,
    read: Callable[[str], Any],
    *,
    acquisition_sha256: str,
    publication_receipt: bytes | None = None,
    publication_sha256: str | None = None,
    expected: bytes | None = None,
    reproduced_archive: Path | None = None,
    reproduced_tools: Mapping[str, str] | None = None,
    limits: ArchiveLimits = ArchiveLimits(),
    dependencies: tuple[str, ...] = (),
    retain: Callable[[bytes, Callable[[], None]], None] | None = None,
    entry: DependencyOperation | None = None,
) -> bytes:
    """Step 2 and approval revalidation; never measure, gate, or resolve a new schedule.

    Pass the frozen returned bytes as expected at every later gate. Existing
    native evidence verifies signer, event, branch, jobs, immutable artifact and
    run attempt. The consumer must protect these bytes and the original policy.
    Optional Cargo reproduction uses the same paired archive/tool contract as
    verify_release_evidence. No files are written to root, and neither index nor
    working-tree changes are permitted at entry or return.
    Dependencies supplements the checkout's manifest policy at both boundaries.
    """
    from research_repo_tools.dependency_provenance import check_release_dependencies

    if entry is None:
        check_release_dependencies(root, packages=dependencies)
    else:
        if entry.root != root or entry.packages != dependencies:
            raise ValueError("candidate dependency entry differs from the observed source")
        entry.verify()
    _digest(acquisition_sha256, "trusted Step 1 assertion digest")
    if acquisition_assertion_digest(policy) != acquisition_sha256:
        raise ValueError("candidate assertions differ from trusted Step 1 handoff")
    commit = _final_commit(policy)
    _eligible(evidence, policy)
    if native_policy.validation.repository != policy.repository:
        raise ValueError("native evidence repository differs")
    if policy.first_release:
        require_first_release_history(read)
    pinned = _acceptance(expected, policy) if expected is not None else None
    publication = None
    if policy.report is not None:
        if publication_receipt is None or publication_sha256 is None:
            raise ValueError("committed evidence requires a publication receipt and independently trusted publication SHA-256")
        if not isinstance(publication_receipt, bytes):
            raise TypeError("publication receipt must be immutable bytes")
        _digest(publication_sha256, "trusted publication digest")
        if sha256(publication_receipt) != publication_sha256:
            raise ValueError("publication receipt differs from trusted Step 1 handoff")
        publication = _parse_publication(publication_receipt, policy)
    elif publication_receipt is not None or publication_sha256 is not None:
        raise ValueError("publication receipt requires a declared candidate report")
    require_release_checkout(root, commit)
    final = capture_source(root, commit)
    working = capture_source(root, "HEAD", working_tree=True)
    if working.revision != commit or working.dirty:
        raise ValueError("final acceptance requires the selected clean HEAD")
    binding = _binding(policy, final.provenance(), publication)
    native = verify_release_evidence(
        native_policy,
        policy.tag.removeprefix("v"),
        commit,
        read,
        expected=deterministic_json(pinned["native"]) if pinned else None,
        reproduced_archive=reproduced_archive,
        reproduced_tools=reproduced_tools,
    )
    archive = _archive(_contents(evidence, policy, binding), limits)
    accepted = deterministic_json(
        {
            "schema": ACCEPTANCE_SCHEMA,
            "policy_sha256": sha256(serialize_candidate_policy(policy)),
            "identity": policy.identity,
            "binding": binding,
            "native": _load_json(native, "native selection"),
            "archive_sha256": sha256(archive),
        }
    )
    if expected is not None and accepted != expected:
        raise ValueError("approved source, native selection, acquisition, or archive changed")

    def guard() -> None:
        check_release_dependencies(root, packages=dependencies)
        final.verify()
        working.verify()
        require_release_checkout(root, commit)
        if policy.first_release:
            require_first_release_history(read)

    if retain is None:
        guard()
    else:
        retain(accepted, guard)
    return accepted


def verify_candidate_archive(
    archive: Path, policy: CandidatePolicy, acceptance: bytes, *, target: ReleaseTarget | None = None, limits: ArchiveLimits = ArchiveLimits()
) -> Evidence:
    """Verify a private upload copy against the independently frozen Step 2 record."""
    accepted = _acceptance(acceptance, policy)
    if target is not None and (target.repository, target.tag, target.commit) != (policy.repository, policy.tag, policy.commit):
        raise ValueError("draft target differs from accepted final release")
    with archive.open("rb") as stream:
        data = stream.read(limits.archive_bytes + 1)
    if len(data) > limits.archive_bytes or sha256(data) != accepted["archive_sha256"]:
        raise ValueError("approved benchmark artifact bytes changed")
    with tempfile.TemporaryDirectory(prefix="candidate-reuse-") as temporary:
        private = Path(temporary) / "archive.tar.gz"
        private.write_bytes(data)
        root = Path(temporary) / "contents"
        extract_archive(private, root, limits=limits)
        evidence = parse_evidence((root / "evidence/run.json").read_bytes(), (root / "evidence/evidence.json").read_bytes())
        actual = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        if actual != _contents(evidence, policy, accepted["binding"]):
            raise ValueError("derived candidate archive differs from original evidence/binding")
    return evidence


def plan_candidate_archive(
    root: Path, payload: str, manifest: str, policy: str, acceptance: str, output: str, *, limits: ArchiveLimits = ArchiveLimits()
) -> PublicationPlan:
    """Retryable offline derivation of the exact archive frozen by Step 2."""
    root = root.resolve(strict=True)
    names = (payload, manifest, policy, acceptance, output)
    _validate_distinct_paths(tuple(_path(root, name) for name in names))
    inputs = {}
    for name in names[:-1]:
        with _path(root, name).open("rb") as stream:
            data = stream.read(limits.content_bytes + 1)
        if len(data) > limits.content_bytes:
            raise ValueError("candidate input exceeds content limit")
        inputs[name] = data
    assertions = parse_candidate_policy(inputs[policy])
    accepted = _acceptance(inputs[acceptance], assertions)
    evidence = parse_evidence(inputs[payload], inputs[manifest])
    data = _archive(_contents(evidence, assertions, accepted["binding"]), limits)
    if sha256(data) != accepted["archive_sha256"]:
        raise ValueError("derived archive differs from frozen acceptance")
    with tempfile.TemporaryDirectory(prefix="candidate-plan-") as temporary:
        archive = Path(temporary) / "archive.tar.gz"
        archive.write_bytes(data)
        verify_candidate_archive(archive, assertions, inputs[acceptance], limits=limits)
    return plan_outputs(root, {output: data}, inputs=inputs, immutable=(output,))
