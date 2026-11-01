"""Availability-aware suite plans composed with the complete acquisition engine."""

import json
import os
import re
import tempfile
import time
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
from research_repo_tools.release_pairs import ReleasePair
from research_repo_tools.source_exports import capture_source, export_source

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

    def decision(self, release: str, source: str) -> dict[str, object]:
        version = _tag_version(release)
        available = (self.since is None or version >= _tag_version(self.since)) and (self.before is None or version < _tag_version(self.before))
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

    @property
    def schema(self) -> int:
        return 5

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
            sources=tuple(sorted({p for s in self.suites for p in s.measurement.sources})),
            harness=tuple(sorted({p for s in self.suites for p in s.measurement.harness})),
            workloads=tuple(sorted({p for s in self.suites for p in s.measurement.measurement.workloads})),
        )

    @property
    def sources(self) -> tuple[str, ...]:
        return self.measurement.sources

    @property
    def harness(self) -> tuple[str, ...]:
        return self.measurement.harness

    def document(self) -> dict[str, Any]:
        return {
            "schema": 5,
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
    raw = _table(document, "suite measurement", {"schema", "suites"})
    if type(raw["schema"]) is not int or raw["schema"] != 5 or not isinstance(raw["suites"], list):
        raise ValueError("suite measurement requires schema 5 and a suite array")
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
    return SuiteMeasurementPlan(tuple(suites))


def _source_record(source: Provenance) -> dict[str, object]:
    return {"revision": source.revision, "source_sha256": source.source_sha256, "harness_sha256": source.harness_sha256, "context": dict(source.context)}


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
        return _measure_suites(root, plan, pair, working_tree=working_tree, checkpoint=checkpoint, resume=resume, progress=reporter, verify=verify)
    except BaseException as error:
        event = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        if reporter.last_event != event:
            reporter.emit(event, force=True)
        raise


def _measure_suites(
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
    from research_repo_tools.suite_runs import SUITE_RUN_SCHEMA, parse_suite_run, sample_document, validate_unit

    if resume and checkpoint is None:
        raise ValueError("resume requires an explicit checkpoint path")
    reporter = progress or ProgressReporter()
    with reporter.stage("availability"):
        captures = {
            "baseline": capture_source(root, pair.baseline),
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
    ambient = sha256(deterministic_json(dict(os.environ)))

    def unchanged() -> None:
        if verify is not None:
            verify()
        for capture in captures.values():
            capture.verify()
        if sha256(deterministic_json(dict(os.environ))) != ambient:
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
        temporary = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="research-suites-"))).resolve()
        prepared: dict[
            str,
            tuple[
                dict[str, CompletePolicy],
                dict[str, Provenance],
                Callable[[], tuple[tuple[tuple[str, CompleteSample], ...], tuple[tuple[str, Provenance], ...]]],
            ],
        ] = {}
        counts: dict[str, int] = {}
        inventory = {(suite.name, phase): 0 for suite in plan.suites for phase in captures}
        nominal: float | None = 0.0
        ownership: dict[str, set[str]] = {"baseline": set(), "current": set()}
        for suite in plan.suites:
            reporter.suite = suite.name
            active = tuple(phase for phase in captures if decisions[suite.name][phase]["state"] == "available")
            if not active:
                continue
            directories = {}
            for phase, capture in captures.items():
                directories[phase] = stack.enter_context(export_source(capture, temporary / f"{suite.name}-{phase}"))

            def accepted_sample(phase: str, sample: CompleteSample, elapsed: float, selected: AcquisitionSuite = suite) -> None:
                policy, source = prepared[selected.name][0][phase], prepared[selected.name][1][phase]
                reporter.accept(_work_class(selected, source, policy), len(sample.cases), elapsed)

            prepared[suite.name] = _prepare_phases(
                directories["baseline"],
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
            )
            policies, declared, _acquire = prepared[suite.name]
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
        retained: dict[str, dict[str, object]] = {}
        if journal is not None:
            with reporter.stage("checkpoint"):
                retained = journal.read()
        # Validate every retained unit before accepting or measuring any work.
        for suite in plan.suites:
            if suite.name in retained:
                with reporter.stage("checkpoint", suite=suite.name):
                    validate_unit(retained[suite.name], suite, decisions[suite.name], prepared.get(suite.name, ({}, {}, None))[1])
        units = []
        for suite in plan.suites:
            reporter.suite = suite.name
            unit: dict[str, object] | None = retained.get(suite.name)
            reused = unit is not None
            started = time.time_ns()
            elapsed_started = reporter.clock()
            if unit is None:
                measurements: tuple[tuple[str, CompleteSample], ...] = ()
                sources: tuple[tuple[str, Provenance], ...] = ()
                if suite.name in prepared:
                    measurements, sources = prepared[suite.name][2]()
                unit = {
                    "name": suite.name,
                    "contract": suite.contract,
                    "availability": decisions[suite.name],
                    "session": uuid.uuid4().hex,
                    "started_ns": started,
                    "finished_ns": time.time_ns(),
                    "observations": {
                        phase: {"sample": sample_document(sample), "source": _source_record(dict(sources)[phase])} for phase, sample in measurements
                    },
                }
                validate_unit(unit, suite, decisions[suite.name], prepared.get(suite.name, ({}, {}, None))[1])
                unchanged()
                if journal:
                    with reporter.stage("checkpoint", suite=suite.name):
                        journal.accept(suite.name, unit)
            units.append(unit)
            if reused and suite.name in prepared:
                policies, declared, _acquire = prepared[suite.name]
                count = sum(len(policy.expected) for policy in policies.values())
                elapsed = reporter.clock() - elapsed_started
                for phase, policy in policies.items():
                    reporter.state, reporter.phase = "checkpoint", phase
                    reporter.accept(_work_class(suite, declared[phase], policy), len(policy.expected), elapsed * len(policy.expected) / count, retained=reused)
        unchanged()
        result = Evidence(
            deterministic_json({"schema": SUITE_RUN_SCHEMA, "declaration": plan.document(), "units": units, "reused": sorted(retained)}),
            SUITE_RUN_SCHEMA,
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
