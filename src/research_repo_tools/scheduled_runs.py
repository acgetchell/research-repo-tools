"""Versioned declared trial slots with observed execution and recovery receipts.

The outer evidence binds a declaration and original v4 bytes. It never pools
Criterion samples or fabricates a receipt for a historical acquisition.
"""

import base64
import binascii
import time
import uuid
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_repo_tools.complete_runs import RUN_SCHEMA_V5, _array, _integer, run_identity
from research_repo_tools.criterion import _markdown_text
from research_repo_tools.evidence import Evidence, Provenance, _load_json, _object, deterministic_json, parse_evidence, serialize_evidence
from research_repo_tools.measurement_checkpoints import open_checkpoint
from research_repo_tools.performance_progress import ProgressReporter
from research_repo_tools.release_pairs import ReleasePair
from research_repo_tools.source_exports import capture_source
from research_repo_tools.suite_measurement import SuiteMeasurementPlan, _environment_digest, _measure_suites, _source_record, parse_suite_plan
from research_repo_tools.suite_runs import SUITE_RUN_SCHEMA, parse_suite_run, validate_suite_policy
from research_repo_tools.trial_recovery import LIFECYCLE_SCHEMA, _event_timestamp, replay_trial

__all__ = ["SCHEDULED_RUN_SCHEMA", "ScheduledRun", "parse_scheduled_run", "select_trial"]

SCHEDULED_RUN_SCHEMA = RUN_SCHEMA_V5


@dataclass(frozen=True, slots=True)
class ScheduledRun:
    """Separate trials, with no implicit pooled phase or series view.

    Use ``select_trial`` and ``parse_suite_run`` to inspect a trial's views.
    """

    plan: SuiteMeasurementPlan
    trials: tuple[Evidence, ...]

    @property
    def harness_mode(self) -> str:
        return self.plan.harness_mode

    @property
    def schema(self) -> str:
        return SCHEDULED_RUN_SCHEMA


def _decode(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError("trial evidence bytes must be base64 strings")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("invalid trial evidence encoding") from error


def parse_scheduled_run(evidence: Evidence) -> ScheduledRun:
    return _parse_scheduled_run(evidence)


def _parse_scheduled_run(evidence: Evidence, *, partial: bool = False) -> ScheduledRun:
    raw = _object(_load_json(evidence.payload, "scheduled run"), "scheduled run", {"schema", "declaration", "trials"})
    if evidence.payload_schema != SCHEDULED_RUN_SCHEMA or raw["schema"] != SCHEDULED_RUN_SCHEMA:
        raise ValueError("requires complete-run v5 evidence")
    plan = parse_suite_plan(_object(raw["declaration"], "declaration"))
    if plan.schedule is None:
        raise ValueError("scheduled evidence requires schema-6 declaration")
    records = _array(raw["trials"], "trials")
    if not records or len(records) > plan.schedule.trials or (not partial and len(records) != plan.schedule.trials):
        raise ValueError("trial inventory differs from schedule")
    base = SuiteMeasurementPlan(plan.suites)
    trials = []
    sessions = set()
    identities = set()
    previous_end = 0
    observed_sources = None
    for index, value in enumerate(records):
        record = _object(value, "trial")
        _object(
            record,
            "trial",
            {"trial", "session", "started_ns", "finished_ns", "order", "receipts", "payload", "manifest", "identity"}
            | ({"lifecycle"} if "lifecycle" in record else set()),
        )
        if type(record["trial"]) is not int or record["trial"] != index + 1 or record["order"] != list(plan.schedule.phases[index]):
            raise ValueError("trial identity/order differs from schedule")
        import re

        if not isinstance(record["session"], str) or re.fullmatch(r"[0-9a-f]{32}", record["session"]) is None or record["session"] in sessions:
            raise ValueError("trial sessions must be unique collection identities")
        sessions.add(record["session"])
        started, ended = (_integer(record[key], key) for key in ("started_ns", "finished_ns"))
        if started < previous_end or ended < started:
            raise ValueError("trial intervals overlap or are reordered")
        original = parse_evidence(_decode(record["payload"]), _decode(record["manifest"]))
        run = validate_suite_policy(original, base)
        if original.sources != evidence.sources:
            raise ValueError("trial original source differs from envelope")
        identity = run_identity(original)
        if record["identity"] != identity or identity in identities:
            raise ValueError("duplicate or changed trial payload identity")
        identities.add(identity)
        sources = tuple(sorted((name, phase, source) for name, phase, _, source in run.observations))
        if observed_sources is not None and sources != observed_sources:
            raise ValueError("trial source/host/tool/environment observations changed")
        observed_sources = sources
        recovery = None
        if "lifecycle" in record:
            lifecycle = _object(record["lifecycle"], "trial lifecycle", {"schema", "events"})
            if lifecycle["schema"] != LIFECYCLE_SCHEMA:
                raise ValueError("unknown trial lifecycle schema")
            events = _array(lifecycle["events"], "trial lifecycle events")
            recovery = replay_trial(events, plan, index + 1, complete=True)
            if (
                (started, ended, record["session"]) != (recovery.started, recovery.finished, events[0]["session"])
                or deterministic_json(list(run.units)) != deterministic_json(recovery.units)
                or record["receipts"] != recovery.receipts
                or _object(_load_json(original.payload, "trial"), "trial")["reused"] != sorted(recovery.reused)
            ):
                raise ValueError("trial differs from committed recovery lifecycle")
            other_sessions = recovery.sessions - {record["session"]}
            if sessions & other_sessions:
                raise ValueError("recovery collection sessions are reused across trials")
            sessions.update(other_sessions)
        expected_order = []
        units = {unit["name"]: unit for unit in run.units}
        for suite in base.suites:
            unit = units[suite.name]
            if not started <= unit["started_ns"] <= unit["finished_ns"] <= ended or (recovery is None and unit["session"] in sessions):
                raise ValueError("trial unit session/interval differs")
            sessions.add(unit["session"])
            expected_order.extend((suite.name, phase) for phase in plan.schedule.phases[index] if phase in unit["observations"])
        receipts = _array(record["receipts"], "phase receipts")
        if len(receipts) != len(expected_order):
            raise ValueError("phase receipt inventory differs")
        phase_end = started
        for item, (suite, phase) in zip(receipts, expected_order, strict=True):
            receipt = _object(item, "phase receipt", {"suite", "phase", "started_ns", "finished_ns"})
            first, last = (_integer(receipt[key], key) for key in ("started_ns", "finished_ns"))
            unit = units[suite]
            if (receipt["suite"], receipt["phase"]) != (suite, phase) or not unit["started_ns"] <= first <= last <= unit["finished_ns"] or first < phase_end:
                raise ValueError("phase execution order/interval differs from schedule")
            phase_end = last
        previous_end = ended
        trials.append(original)
    return ScheduledRun(plan, tuple(trials))


def select_trial(evidence: Evidence, trial: int) -> Evidence:
    """Validate the whole acquisition before selecting one explicitly labeled trial."""
    run = parse_scheduled_run(evidence)
    if type(trial) is not int or not 1 <= trial <= len(run.trials):
        raise ValueError("trial selection is outside the declared schedule")
    return run.trials[trial - 1]


def validate_scheduled_policy(evidence: Evidence, plan: SuiteMeasurementPlan, *, release: bool = False) -> ScheduledRun:
    run = parse_scheduled_run(evidence)
    if deterministic_json(run.plan.document()) != deterministic_json(plan.document()):
        raise ValueError("scheduled acquisition declaration changed")
    for trial in run.trials:
        validate_suite_policy(trial, SuiteMeasurementPlan(plan.suites), release=release)
    return run


def measure_schedule(
    root: Path,
    plan: SuiteMeasurementPlan,
    pair: ReleasePair,
    *,
    working_tree: bool,
    checkpoint: Path | None,
    resume: bool,
    progress: ProgressReporter,
    verify: Callable[[], None] | None,
) -> Evidence:
    assert plan.schedule is not None
    if resume and checkpoint is None:
        raise ValueError("resume requires an explicit checkpoint")
    captures = {
        "baseline": capture_source(root, pair.baseline),
        "current": capture_source(root, "HEAD" if working_tree else pair.current, working_tree=working_tree),
    }
    ambient = _environment_digest()
    identity: dict[str, object] = {
        "declaration": plan.document(),
        "sources": {phase: source.provenance() for phase, source in captures.items()},
        "environment": ambient,
    }

    def unchanged() -> None:
        if verify is not None:
            verify()
        for source in captures.values():
            source.verify()
        if _environment_digest() != ambient:
            raise ValueError("scheduled acquisition environment changed")

    names = tuple(f"trial-{index + 1}" for index in range(plan.schedule.trials))
    keys = tuple(key for name in names for key in (f"started-{name}", name))
    context = open_checkpoint(checkpoint, identity, keys, resume=resume, events=True) if checkpoint else nullcontext(None)
    with context as journal:
        with progress.stage("checkpoint"):
            retained = journal.read() if journal else {}
            events = journal.read_events() if journal else []
        lifecycle_enabled = journal is None or journal.events_enabled
        event_trials = [event.get("trial") for event in events]
        if any(type(trial) is not int or not 1 <= trial <= len(names) for trial in event_trials):
            raise ValueError("checkpoint recovery trial events are reordered or unknown")
        if event_trials != sorted(_integer(trial, "recovery trial") for trial in event_trials):
            raise ValueError("checkpoint recovery trial events are reordered or unknown")
        prefix = []
        for name in names:
            if name in retained:
                if len(prefix) != names.index(name) or f"started-{name}" not in retained:
                    raise ValueError("checkpoint trials must form a contiguous started/completed prefix")
                prefix.append(retained[name])
            elif f"started-{name}" in retained:
                if names.index(name) != len(prefix) or retained[f"started-{name}"] != {"trial": len(prefix) + 1, "lifecycle": LIFECYCLE_SCHEMA}:
                    raise ValueError("interrupted trial may contain unretained timings; preserve it and start a new acquisition")
        for index, name in enumerate(names):
            trial_events = [event for event in events if event["trial"] == index + 1]
            if trial_events and f"started-{name}" not in retained:
                raise ValueError("recovery events lack a started trial marker")
            if index < len(prefix):
                expected_events = _object(prefix[index].get("lifecycle", {}), "trial lifecycle").get("events", [])
                if trial_events != expected_events:
                    raise ValueError("completed trial differs from checkpoint recovery journal")
            elif trial_events:
                if index != len(prefix):
                    raise ValueError("recovery trial events must form a contiguous prefix")
                replay_trial(trial_events, plan, index + 1)
        asserted_units = ()
        if prefix:
            first = parse_evidence(_decode(prefix[0].get("payload")), _decode(prefix[0].get("manifest")))
            _parse_scheduled_run(
                Evidence(
                    deterministic_json({"schema": SCHEDULED_RUN_SCHEMA, "declaration": plan.document(), "trials": prefix}), SCHEDULED_RUN_SCHEMA, first.sources
                ),
                partial=True,
            )
            # Resume is acquisition, not a read-only report operation. Re-run
            # preparation against the retained units before accepting any work.
            asserted_units = parse_suite_run(first).units
            _measure_suites(
                root,
                SuiteMeasurementPlan(plan.suites),
                pair,
                working_tree=working_tree,
                progress=progress,
                verify=unchanged,
                asserted_units=asserted_units,
                reuse_assertions=True,
            )
        trials = []
        envelope = None
        result = None
        for index, order in enumerate(plan.schedule.phases):
            unchanged()
            record: dict[str, object]
            if index < len(prefix):
                record = prefix[index]
                original = parse_evidence(_decode(record.get("payload")), _decode(record.get("manifest")))
            else:
                marker = f"started-{names[index]}"
                if journal and marker not in retained:
                    journal.accept(marker, {"trial": index + 1, **({"lifecycle": LIFECYCLE_SCHEMA} if lifecycle_enabled else {})})
                trial_events = [event for event in events if event["trial"] == index + 1]
                recovery = replay_trial(trial_events, plan, index + 1)
                receipts = list(recovery.receipts)

                def event(item: dict[str, Any]) -> None:
                    value = {"trial": index + 1, **item}
                    # Reject a backward clock step before it can poison the journal.
                    _event_timestamp(value, _event_timestamp(trial_events[-1]) if trial_events else 0)
                    if journal:
                        journal.append_event(value)
                    trial_events.append(value)

                def prepared(observations: dict[str, dict[str, Provenance]]) -> None:
                    observed = {name: {phase: _source_record(source) for phase, source in phases.items()} for name, phases in observations.items()}
                    if recovery.prepared is not None and deterministic_json(observed) != deterministic_json(recovery.prepared):
                        raise ValueError("checkpoint observations differ from interrupted trial preparation")
                    if recovery.finished:
                        return
                    if recovery.opened:
                        event({"kind": "abandoned", "at_ns": time.time_ns(), "reason": "recovery observed an unclosed collection segment; finish time unknown"})
                    if len(recovery.units) == len(plan.suites):
                        event(
                            {
                                "kind": "recovered-end",
                                "at_ns": time.time_ns(),
                                "reason": "recovery finalized durable pairs; original collection finish time unknown",
                            }
                        )
                        return
                    event({"kind": "segment", "session": uuid.uuid4().hex, "at_ns": time.time_ns(), "prepared": observed})

                def phase_receipt(suite: str, phase: str, first: int, last: int) -> None:
                    receipts.append({"suite": suite, "phase": phase, "started_ns": first, "finished_ns": last})

                start = time.time_ns()
                try:
                    original = _measure_suites(
                        root,
                        SuiteMeasurementPlan(plan.suites),
                        pair,
                        working_tree=working_tree,
                        progress=progress,
                        verify=unchanged,
                        phase_order=order,
                        on_phase=phase_receipt,
                        asserted_units=asserted_units,
                        retained_units=tuple(recovery.units),
                        on_prepared=prepared if lifecycle_enabled else None,
                        on_event=event if lifecycle_enabled and not recovery.finished else None,
                    )
                    if lifecycle_enabled and not replay_trial(trial_events, plan, index + 1).finished:
                        event({"kind": "segment-end", "at_ns": time.time_ns()})
                except BaseException as error:
                    if lifecycle_enabled:
                        try:
                            # A failed append can leave a partial journal. Never
                            # append beyond it or reinterpret it as missing work.
                            if journal:
                                trial_events[:] = [item for item in journal.read_events() if item.get("trial") == index + 1]
                            state = replay_trial(trial_events, plan, index + 1)
                            if state.opened:
                                event({"kind": "abandoned", "at_ns": time.time_ns(), "reason": type(error).__name__})
                        except BaseException as retention_error:
                            raise BaseExceptionGroup("acquisition and recovery retention failed", [error, retention_error]) from None
                    raise
                finish = time.time_ns()
                if lifecycle_enabled:
                    recovery = replay_trial(trial_events, plan, index + 1, complete=True)
                    start, finish = recovery.started, recovery.finished
                    receipts = recovery.receipts
                    # Reconstruct from durable pairs, including when assembly
                    # failed after the last commit. Do not invent new unit bytes.
                    original = Evidence(
                        deterministic_json(
                            {
                                "schema": SUITE_RUN_SCHEMA,
                                "declaration": SuiteMeasurementPlan(plan.suites).document(),
                                "units": recovery.units,
                                "reused": sorted(recovery.reused),
                            }
                        ),
                        SUITE_RUN_SCHEMA,
                        original.sources,
                    )
                payload, manifest = serialize_evidence(original)
                record = {
                    "trial": index + 1,
                    "session": trial_events[0]["session"] if lifecycle_enabled else uuid.uuid4().hex,
                    "started_ns": start,
                    "finished_ns": finish,
                    "order": list(order),
                    "receipts": receipts,
                    "payload": base64.b64encode(payload).decode("ascii"),
                    "manifest": base64.b64encode(manifest).decode("ascii"),
                    "identity": run_identity(original),
                    **({"lifecycle": {"schema": LIFECYCLE_SCHEMA, "events": trial_events}} if lifecycle_enabled else {}),
                }
                asserted_units = parse_suite_run(original).units
            if envelope is not None and original.sources != envelope:
                raise ValueError("trial original sources changed")
            envelope = original.sources
            for phase, source in envelope:
                if dict(source.context)["source-export"] != captures[phase].provenance():
                    raise ValueError("retained trial source differs from independently captured input")
            trials.append(record)
            result = Evidence(
                deterministic_json({"schema": SCHEDULED_RUN_SCHEMA, "declaration": plan.document(), "trials": trials}), SCHEDULED_RUN_SCHEMA, envelope
            )
            _parse_scheduled_run(result, partial=index < len(names) - 1)
            unchanged()
            if journal and index >= len(prefix):
                with progress.stage("checkpoint"):
                    journal.accept(names[index], record)
        assert result is not None
        return result


def render_scheduled_run(evidence: Evidence, *, title: str, links: str, identity: str) -> bytes:
    from research_repo_tools.suite_runs import render_suite_run

    run = parse_scheduled_run(evidence)
    assert run.plan.schedule is not None
    lines = [
        "<!-- research-repo-tools/run-report/v2 -->",
        f"# {_markdown_text(title)}",
        "",
        f"Run: `{identity}`",
        "",
        links,
        "",
        f"Acquisition schedule: {run.plan.schedule.order}; trials: {len(run.trials)}; seed: {run.plan.schedule.seed}.",
        "Each trial is retained separately. Within-phase samples are not independent trials; no pooled significance claim is made.",
    ]
    records = _array(_object(_load_json(evidence.payload, "schedule"), "schedule")["trials"], "trials")
    for index, trial in enumerate(run.trials):
        lines.extend(["", f"## Trial {index + 1}: {' → '.join(run.plan.schedule.phases[index])}", ""])
        if "lifecycle" in records[index]:
            recovery = replay_trial(records[index]["lifecycle"]["events"], run.plan, index + 1, complete=True)
            lines.extend(
                [
                    f"Collection: {recovery.segments} segment(s); {recovery.abandoned} abandoned attempt(s). Validated abandoned phases are retained and excluded from accepted pairs; failed-output capture status is recorded.",
                    "These are completed campaign slots; recovery does not establish uninterrupted collection, independent trials, or absence of optional-stopping bias.",
                    "",
                ]
            )
            for event in records[index]["lifecycle"]["events"]:
                if event["kind"] == "segment":
                    lines.append(f"- Collection segment `{event['session']}` started: {event['at_ns']} (Unix ns).")
                elif event["kind"] == "abandoned":
                    lines.append(f"- Abandonment observed: {event['at_ns']} (Unix ns); {_markdown_text(event['reason'])}.")
                elif event["kind"] == "phase-failed":
                    lines.append(f"- Failed {event['phase']} output: {event['output']['status']}; {len(event['output']['files'])} opaque file(s) retained.")
                elif event["kind"] == "recovered-end":
                    lines.append(f"- Recovery finalization observed: {event['at_ns']} (Unix ns); original collection finish unknown.")
        for receipt in records[index]["receipts"]:
            lines.append(f"- {receipt['suite']}/{receipt['phase']}: {receipt['started_ns']}–{receipt['finished_ns']} (Unix ns).")
        trial_report = render_suite_run(trial, title=f"Trial {index + 1}", links=links, identity=run_identity(trial)).decode("utf-8").splitlines()
        # The scheduled report owns the marker, title, identity and evidence links.
        lines.extend(["", *trial_report[7:]])
    return ("\n".join(lines) + "\n").encode("utf-8")
