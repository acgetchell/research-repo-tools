"""Availability-aware suite plans composed with the complete acquisition engine."""

import json
import os
import re
import tempfile
import time
import unicodedata
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from research_repo_tools.common_measurement import CompleteMeasurementPlan, _plan_document, _prepare_phases, parse_plan
from research_repo_tools.complete_runs import CompletePolicy, CompleteSample, serialize_policy
from research_repo_tools.evidence import Evidence, Provenance, _object, _string, deterministic_json, sha256
from research_repo_tools.measurement import MeasurementConfig, _strings
from research_repo_tools.measurement_checkpoints import open_checkpoint
from research_repo_tools.performance_progress import ProgressReporter
from research_repo_tools.publication_config import _table
from research_repo_tools.release_discovery import _tag_version, normalize_tag
from research_repo_tools.release_pairs import FirstRelease, ReleasePair
from research_repo_tools.source_exports import capture_source, export_source
from research_repo_tools.trial_schedule import TrialSchedule, parse_schedule

__all__ = ["AcquisitionSuite", "SuiteMeasurementPlan", "parse_suite_plan", "measure_suites"]


@dataclass(frozen=True, slots=True)
class AcquisitionSuite:
    name: str
    contract: str
    measurement: CompleteMeasurementPlan
    since: str | None = None
    before: str | None = None
    nominal_seconds_per_case: float | None = None
    compilation: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", self.name):
            raise ValueError("suite name must be a lowercase portable slug")
        _string(self.contract, "suite semantic contract")
        compiled = tuple((phase, _strings(command, "compilation command")) for phase, command in self.compilation)
        if compiled and (len(compiled) != 2 or set(dict(compiled)) != {"baseline", "current"}):
            raise ValueError("compilation commands must identify both phases")
        object.__setattr__(self, "compilation", compiled)
        if not isinstance(self.measurement, CompleteMeasurementPlan) or self.measurement.schema != 4 or self.measurement.backend != "export":
            raise ValueError("suite measurement requires schema 4 with backend export")
        for key in ("since", "before"):
            value = getattr(self, key)
            if value is not None and normalize_tag(value) != value:
                raise ValueError("availability bounds require canonical stable tags")
        if self.since is not None and self.before is not None and _tag_version(self.since) >= _tag_version(self.before):
            raise ValueError("availability interval must be nonempty")
        if self.nominal_seconds_per_case is not None:
            from research_repo_tools.criterion import _positive

            _positive(self.nominal_seconds_per_case, "nominal seconds per case")

    def available(self, release: str) -> bool:
        """Use the inclusive since/exclusive before interval for a selected release."""
        version = _tag_version(release)
        return (self.since is None or version >= _tag_version(self.since)) and (self.before is None or version < _tag_version(self.before))

    def decision(self, release: str, source: str) -> dict[str, object]:
        available = self.available(release)
        return {
            "state": "available" if available else "unavailable",
            "reason": "inside declared release interval" if available else "outside declared release interval",
            "since": self.since,
            "before": self.before,
            "release": release,
            "source": json.loads(source),
        }


@dataclass(frozen=True, slots=True)
class SuiteMeasurementPlan:
    """Schema 5: each suite owns a whole paired acquisition/checkpoint unit."""

    suites: tuple[AcquisitionSuite, ...]
    schedule: TrialSchedule | None = None
    outputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        suites = tuple(self.suites)
        if not suites or any(not isinstance(item, AcquisitionSuite) for item in suites):  # ty: ignore[redundant-condition-strict]  Validate untyped callers.
            raise ValueError("suite plan requires acquisition suites")
        if len({item.name for item in suites}) != len(suites):
            raise ValueError("suite names must be unique")
        modes = {item.measurement.harness_mode for item in suites}
        if len(modes) != 1:
            raise ValueError("suites must share a harness mode")
        policies = [item.measurement.baseline.policy for item in suites]
        if any((p.statistics, p.confidence_level, p.unit) != (policies[0].statistics, policies[0].confidence_level, policies[0].unit) for p in policies):
            raise ValueError("suites must share statistics, confidence, and units")
        object.__setattr__(self, "suites", suites)
        from research_repo_tools.source_exports import _names

        outputs = tuple(self.outputs)
        _names(None, outputs)
        if outputs and self.schedule is None:
            raise ValueError("candidate output declarations require a schema-6 schedule")
        if self.schedule is not None and not isinstance(self.schedule, TrialSchedule):
            raise ValueError("invalid trial schedule")
        from pathlib import PurePosixPath

        for name in outputs:
            if PurePosixPath(name).suffix.casefold() not in {".md", ".rst", ".csv", ".svg"}:
                raise ValueError("output exceptions require Markdown, reStructuredText, CSV, or SVG report/document files")
            if any(
                PurePosixPath(unicodedata.normalize("NFC", name).casefold()).full_match(unicodedata.normalize("NFC", pattern).casefold())
                for suite in suites
                for pattern in (*suite.measurement.phase_sources("current"), *suite.measurement.harness, *suite.measurement.measurement.workloads)
            ):
                raise ValueError("declared output overlaps measured inputs")
        object.__setattr__(self, "outputs", tuple(sorted(outputs)))

    @property
    def schema(self) -> int:
        return 6 if self.schedule is not None else 5

    @property
    def backend(self) -> str:
        return "export"

    @property
    def harness_mode(self) -> str:
        return self.suites[0].measurement.harness_mode

    @property
    def measurement(self) -> MeasurementConfig:
        """Controller settings; suite-specific identities remain authoritative."""
        first = self.suites[0].measurement.measurement
        return replace(
            first,
            sources=tuple(sorted({p for s in self.suites for p in s.measurement.phase_sources("current")})),
            harness=tuple(sorted({p for s in self.suites for p in s.measurement.harness})),
            workloads=tuple(sorted({p for s in self.suites for p in s.measurement.measurement.workloads})),
        )

    @property
    def sources(self) -> tuple[str, ...]:
        return self.measurement.sources

    @property
    def harness(self) -> tuple[str, ...]:
        return self.measurement.harness

    def validate_release(self, release: str) -> None:
        """Require each available suite's floor without inspecting or acquiring sources."""
        for suite in self.suites:
            if suite.available(release):
                suite.measurement.measurement.validate_release(release)

    def document(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            **({"schedule": self.schedule.document(), "outputs": list(self.outputs)} if self.schedule else {}),
            "suites": [
                {
                    "name": item.name,
                    "contract": item.contract,
                    "measurement": _plan_document(item.measurement),
                    **({"since": item.since} if item.since is not None else {}),
                    **({"before": item.before} if item.before is not None else {}),
                    **({"nominal-seconds-per-case": item.nominal_seconds_per_case} if item.nominal_seconds_per_case is not None else {}),
                    **({"compilation": dict(item.compilation)} if item.compilation else {}),
                }
                for item in self.suites
            ],
        }


def parse_suite_plan(document: dict[str, Any]) -> SuiteMeasurementPlan:
    raw = _table(document, "suite measurement", {"schema", "suites"}, {"schedule", "outputs"})
    if type(raw["schema"]) is not int or raw["schema"] not in {5, 6} or not isinstance(raw["suites"], list):
        raise ValueError("suite measurement requires schema 5 or 6 and a suite array")
    if (raw["schema"] == 6) != ("schedule" in raw) or (raw["schema"] == 5 and "outputs" in raw):
        raise ValueError("schedule/output declarations require schema 6")
    suites = []
    for value in raw["suites"]:
        item = _table(value, "suite", {"name", "contract", "measurement"}, {"since", "before", "nominal-seconds-per-case", "compilation"})
        from research_repo_tools.criterion import _positive

        suites.append(
            AcquisitionSuite(
                _string(item["name"], "suite name"),
                _string(item["contract"], "semantic contract"),
                parse_plan(_object(item["measurement"], "suite measurement")),
                _string(item["since"], "since") if "since" in item else None,
                _string(item["before"], "before") if "before" in item else None,
                _positive(item["nominal-seconds-per-case"], "nominal seconds per case") if "nominal-seconds-per-case" in item else None,
                tuple((phase, _strings(command, "compilation command")) for phase, command in _object(item.get("compilation", {}), "compilation").items()),
            )
        )
    return SuiteMeasurementPlan(
        tuple(suites), parse_schedule(raw["schedule"]) if "schedule" in raw else None, _strings(raw.get("outputs", []), "outputs", nonempty=False)
    )


def _source_record(source: Provenance) -> dict[str, object]:
    return {"revision": source.revision, "source_sha256": source.source_sha256, "harness_sha256": source.harness_sha256, "context": dict(source.context)}


def _environment_digest() -> str:
    """Ignore only documented shell/terminal/SSH session bookkeeping, never prefixes."""
    ignored = {
        "_",
        "SHLVL",
        "TERM_SESSION_ID",
        "ITERM_SESSION_ID",
        "SSH_CONNECTION",
        "SSH_CLIENT",
        "SSH_TTY",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "TMUX",
        "TMUX_PANE",
        "STY",
        "WINDOW",
        "WINDOWID",
    }
    return sha256(deterministic_json({key: value for key, value in os.environ.items() if key not in ignored}))


def _retain_failed_phase(phase: str, started: int, directory: Path, error: BaseException, emit: Callable[[dict[str, Any]], None]) -> None:
    from research_repo_tools.trial_recovery import capture_failed_output

    emit(
        {
            "kind": "phase-failed",
            "phase": phase,
            "started_ns": started,
            "at_ns": time.time_ns(),
            "reason": type(error).__name__,
            "output": capture_failed_output(directory),
        }
    )


def measure_suites(
    root: Path,
    plan: SuiteMeasurementPlan,
    pair: ReleasePair,
    *,
    working_tree: bool = False,
    checkpoint: Path | None = None,
    resume: bool = False,
    progress: ProgressReporter | None = None,
    verify: Callable[[], None] | None = None,
) -> Evidence:
    """Resolve availability before native commands and resume whole complete units."""
    reporter = progress or ProgressReporter()
    try:
        if plan.schedule is not None:
            from research_repo_tools.scheduled_runs import measure_schedule

            return measure_schedule(root, plan, pair, working_tree=working_tree, checkpoint=checkpoint, resume=resume, progress=reporter, verify=verify)
        return _measure_suites(root, plan, pair, working_tree=working_tree, checkpoint=checkpoint, resume=resume, progress=reporter, verify=verify)
    except BaseException as error:
        reporter._failed(error)
        raise


def _measure_suites(
    root: Path,
    plan: SuiteMeasurementPlan,
    pair: ReleasePair | FirstRelease,
    *,
    working_tree: bool = False,
    checkpoint: Path | None = None,
    resume: bool = False,
    progress: ProgressReporter | None = None,
    verify: Callable[[], None] | None = None,
    phase_order: tuple[str, ...] = ("baseline", "current"),
    on_phase: Callable[[str, str, int, int], None] | None = None,
    asserted_units: tuple[dict[str, Any], ...] = (),
    reuse_assertions: bool = False,
    retained_units: tuple[dict[str, Any], ...] = (),
    on_prepared: Callable[[dict[str, dict[str, Provenance]]], None] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> Evidence:
    """Resolve availability before native commands and resume whole complete units."""
    from research_repo_tools.suite_runs import FIRST_RELEASE_RUN_SCHEMA, SUITE_RUN_SCHEMA, parse_suite_run, sample_document, validate_unit

    if resume and checkpoint is None:
        raise ValueError("resume requires an explicit checkpoint path")
    first = isinstance(pair, FirstRelease)
    if first and (plan.schedule is not None or not working_tree):
        raise ValueError("first-release acquisition requires one working-tree acquisition without a trial schedule")
    if first:
        phase_order = ("current",)
    reporter = progress or ProgressReporter()
    with reporter.stage("availability"):
        captures = {
            **({"baseline": capture_source(root, pair.baseline)} if isinstance(pair, ReleasePair) else {}),
            "current": capture_source(root, "HEAD" if working_tree else pair.current, working_tree=working_tree),
        }
        # A prospective label is an independent assertion, but must also match
        # the ORIGINAL captured package metadata, never the overlaid harness.
        if working_tree:
            from research_repo_tools.release_metadata import read_package_info

            with tempfile.TemporaryDirectory(prefix="research-availability-") as temporary:
                with export_source(captures["current"], Path(temporary).resolve() / "source") as original:
                    if "v" + read_package_info(original).version != pair.current:
                        raise ValueError("working-tree availability label differs from original package version")
        decisions = {
            suite.name: {phase: suite.decision(getattr(pair, phase), capture.provenance()) for phase, capture in captures.items()} for suite in plan.suites
        }
    envelope = tuple(
        (
            phase,
            Provenance(
                capture.revision,
                capture.identity,
                context=(
                    ("release", getattr(pair, phase)),
                    ("source-export", capture.provenance()),
                    ("mode", "working-tree" if capture.working_tree else "tag"),
                ),
            ),
        )
        for phase, capture in captures.items()
    )
    ambient = _environment_digest()

    def unchanged() -> None:
        if verify is not None:
            verify()
        for capture in captures.values():
            capture.verify()
        if _environment_digest() != ambient:
            raise ValueError("acquisition environment changed")

    with ExitStack() as stack:
        identity: dict[str, object] = {
            "declaration": plan.document(),
            "sources": {phase: capture.provenance() for phase, capture in captures.items()},
            "releases": {phase: getattr(pair, phase) for phase in captures},
            "availability": decisions,
            "environment-sha256": ambient,
        }
        journal = None
        if checkpoint is not None:
            with reporter.stage("checkpoint"):
                journal = stack.enter_context(open_checkpoint(checkpoint, identity, tuple(s.name for s in plan.suites), resume=resume))
        exports_root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="research-suites-"))).resolve()
        prepared: dict[str, tuple[dict[str, CompletePolicy], dict[str, Provenance]]] = {}
        counts: dict[str, int] = {}
        inventory = {(suite.name, phase): 0 for suite in plan.suites for phase in captures}
        nominal: float | None = 0.0
        ownership: dict[str, set[str]] = {"baseline": set(), "current": set()}

        def prepare(
            suite: AcquisitionSuite,
            resources: ExitStack,
        ) -> tuple[
            dict[str, CompletePolicy],
            dict[str, Provenance],
            Callable[[], tuple[tuple[tuple[str, CompleteSample], ...], tuple[tuple[str, Provenance], ...]]],
        ]:
            active = tuple(phase for phase in captures if decisions[suite.name][phase]["state"] == "available")
            directories = {phase: exports_root / f"{suite.name}-{phase}" for phase in captures}
            for phase, capture in captures.items():
                if phase in active or (phase == "current" and suite.measurement.harness_mode == "common"):
                    directories[phase] = resources.enter_context(export_source(capture, directories[phase]))

            def accepted_sample(phase: str, sample: CompleteSample, elapsed: float) -> None:
                policy, source = prepared[suite.name][0][phase], prepared[suite.name][1][phase]
                reporter.accept(_work_class(suite, source, policy), len(sample.cases), elapsed)

            return _prepare_phases(
                directories.get("baseline"),
                directories["current"],
                suite.measurement,
                pair,
                captured_sources=captures,
                working_tree=working_tree,
                verify=unchanged,
                active=active,
                progress=reporter,
                compilation=dict(suite.compilation),
                on_sample=accepted_sample,
                phase_order=phase_order,
                on_phase=(lambda phase, start, end: on_phase(suite.name, phase, start, end)) if on_phase is not None else None,
                on_phase_start=(lambda phase, start: on_event({"kind": "phase-start", "phase": phase, "at_ns": start})) if on_event else None,
                on_phase_sample=(
                    lambda phase, sample, source, start, end: on_event(
                        {
                            "kind": "phase-complete",
                            "phase": phase,
                            "started_ns": start,
                            "finished_ns": end,
                            "sample": sample_document(sample),
                            "source": _source_record(source),
                        }
                    )
                )
                if on_event
                else None,
                on_phase_failure=(lambda phase, start, directory, error: _retain_failed_phase(phase, start, directory, error, on_event)) if on_event else None,
            )

        # Discover and gate every suite before timing, retaining only metadata.
        # Each export (including its build outputs) is removed before the next suite.
        for suite in plan.suites:
            reporter.suite = suite.name
            if not any(decision["state"] == "available" for decision in decisions[suite.name].values()):
                continue
            with ExitStack() as resources:
                policies, declared, _acquire = prepare(suite, resources)
            prepared[suite.name] = policies, declared
            for phase, policy in policies.items():
                if ownership[phase] & set(policy.expected):
                    raise ValueError(f"duplicate benchmark ownership in {phase}: {sorted(ownership[phase] & set(policy.expected))}")
                ownership[phase].update(policy.expected)
                key = _work_class(suite, declared[phase], policy)
                counts[key] = counts.get(key, 0) + len(policy.expected)
                inventory[(suite.name, phase)] = len(policy.expected)
                if suite.nominal_seconds_per_case is None:
                    nominal = None
                elif nominal is not None:
                    nominal += len(policy.expected) * suite.nominal_seconds_per_case
        reporter.budget(counts, nominal, inventory=inventory)
        if asserted_units:
            if [unit.get("name") for unit in asserted_units] != [suite.name for suite in plan.suites]:
                raise ValueError("asserted unit inventory differs")
            for suite, unit in zip(plan.suites, asserted_units, strict=True):
                validate_unit(unit, suite, decisions[suite.name], prepared.get(suite.name, ({}, {}))[1])
        retained: dict[str, dict[str, object]] = {}
        if journal is not None:
            with reporter.stage("checkpoint"):
                retained = journal.read()
        if retained_units:
            if journal is not None or reuse_assertions:
                raise ValueError("retained suite units require a single external checkpoint owner")
            retained = {unit["name"]: unit for unit in retained_units}
        if reuse_assertions:
            if journal is not None or not asserted_units:
                raise ValueError("retained assertions require a complete independent unit set without a checkpoint")
            retained = {unit["name"]: unit for unit in asserted_units}
        # Validate every retained unit before accepting or measuring any work.
        for suite in plan.suites:
            if suite.name in retained:
                with reporter.stage("checkpoint", suite=suite.name):
                    validate_unit(retained[suite.name], suite, decisions[suite.name], prepared.get(suite.name, ({}, {}))[1])
        if on_prepared is not None:
            on_prepared({suite.name: prepared.get(suite.name, ({}, {}))[1] for suite in plan.suites})
        units = []
        for suite in plan.suites:
            reporter.suite = suite.name
            unit: dict[str, object] | None = retained.get(suite.name)
            reused = unit is not None
            started = time.time_ns()
            elapsed_started = reporter.clock()
            if unit is None:
                session = uuid.uuid4().hex
                if on_event is not None:
                    on_event({"kind": "suite-start", "suite": suite.name, "session": session, "at_ns": started})
                measurements: tuple[tuple[str, CompleteSample], ...] = ()
                sources: tuple[tuple[str, Provenance], ...] = ()
                if suite.name in prepared:
                    # Rebuild only missing units, one suite at a time. Bind the
                    # new sampler to the inventory/provenance checked above.
                    with ExitStack() as resources:
                        policies, declared, acquire = prepare(suite, resources)
                        if (policies, declared) != prepared[suite.name]:
                            raise ValueError(f"{suite.name} preparation changed before measurement")
                        measurements, sources = acquire()
                unit = {
                    "name": suite.name,
                    "contract": suite.contract,
                    "availability": decisions[suite.name],
                    "session": session,
                    "started_ns": started,
                    "finished_ns": time.time_ns(),
                    "observations": {
                        phase: {"sample": sample_document(sample), "source": _source_record(dict(sources)[phase])} for phase, sample in measurements
                    },
                }
                validate_unit(unit, suite, decisions[suite.name], prepared.get(suite.name, ({}, {}))[1])
                unchanged()
                if on_event is not None:
                    on_event({"kind": "suite-complete", "unit": unit})
                if journal:
                    with reporter.stage("checkpoint", suite=suite.name):
                        journal.accept(suite.name, unit)
            units.append(unit)
            if reused and suite.name in prepared:
                policies, declared = prepared[suite.name]
                count = sum(len(policy.expected) for policy in policies.values())
                elapsed = reporter.clock() - elapsed_started
                for phase, policy in policies.items():
                    reporter.state, reporter.phase = "checkpoint", phase
                    reporter.accept(_work_class(suite, declared[phase], policy), len(policy.expected), elapsed * len(policy.expected) / count, retained=reused)
        unchanged()
        schema = FIRST_RELEASE_RUN_SCHEMA if first else SUITE_RUN_SCHEMA
        result = Evidence(
            deterministic_json({"schema": schema, "declaration": plan.document(), "units": units, "reused": sorted(retained)}),
            schema,
            envelope,
        )
        parse_suite_run(result)
        reporter.state = "retention"
        reporter.emit("acquisition-complete", force=True)
        return result


def _work_class(suite: AcquisitionSuite, source: Provenance, policy: CompletePolicy) -> str:
    context = dict(source.context)
    fields = _comparison_fields(suite)
    compatible = {field: context.get(field.removeprefix("context.")) if field.startswith("context.") else getattr(source, field) for field in fields}
    return sha256(
        deterministic_json(
            {
                "contract": suite.contract,
                "compatible": compatible,
                "compilation": dict(suite.compilation).get(context["phase"]),
                "unknown-observation": (suite.name, context["phase"]) if any(value is None for value in compatible.values()) else None,
                "sampling": serialize_policy(policy).decode(),
            }
        )
    )


def _comparison_fields(suite: AcquisitionSuite) -> tuple[str, ...]:
    """The same observed identities bound ratio eligibility and timing estimates."""
    return tuple(sorted({*suite.measurement.measurement.compatible, "context.host", "context.environment", "context.workload-sha256", "context.command"}))
