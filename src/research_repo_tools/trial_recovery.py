"""Strict replay of scheduled paired-suite attempts and collection segments."""

import base64
import binascii
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research_repo_tools.common_measurement import _validate_phase_identity, _validate_phase_sample
from research_repo_tools.complete_runs import _integer, _parse_complete_sample
from research_repo_tools.evidence import _object, _string, deterministic_json
from research_repo_tools.suite_measurement import SuiteMeasurementPlan, _source_record
from research_repo_tools.suite_runs import _source

LIFECYCLE_SCHEMA = "research-repo-tools/trial-lifecycle/v1"
_FAILED_OUTPUT_BYTES = 16 * 1024 * 1024
_FAILED_OUTPUT_FILES = 4096


def capture_failed_output(directory: Path) -> dict[str, Any]:
    """Preserve bounded opaque failure bytes; never parse them as valid samples."""
    from research_repo_tools.publication import _path
    from research_repo_tools.source_exports import _names

    files: dict[str, str] = {}
    total = 0
    try:
        if not directory.exists() and not directory.is_symlink():
            return {"status": "absent", "files": files, "reason": "no output directory observed"}
        _path(directory.parent, directory.name, directory=True)

        def fail(error: OSError) -> None:
            raise error

        for root, directories, names in directory.walk(on_error=fail):
            for name in (*directories, *names):
                path = root / name
                _path(directory, path.relative_to(directory).as_posix(), directory=name in directories)
            for name in sorted(names):
                path = root / name
                relative = path.relative_to(directory).as_posix()
                _names(None, (*files, relative))
                if len(files) >= _FAILED_OUTPUT_FILES:
                    raise ValueError("failed-output file bound exceeded")
                with path.open("rb") as source:
                    data = source.read(_FAILED_OUTPUT_BYTES - total + 1)
                if total + len(data) > _FAILED_OUTPUT_BYTES:
                    raise ValueError("failed-output byte bound exceeded")
                total += len(data)
                files[relative] = base64.b64encode(data).decode("ascii")
        return {"status": "retained", "files": files, "reason": "opaque output observed after phase failure; excluded from accepted samples"}
    except (OSError, ValueError) as error:
        return {"status": "unavailable", "files": files, "reason": f"{type(error).__name__}: failed-output capture incomplete"}


def _validate_failed_output(value: object) -> None:
    from research_repo_tools.source_exports import _names

    raw = _object(value, "failed phase output", {"status", "files", "reason"})
    files = _object(raw["files"], "failed output files")
    _names(None, tuple(files))
    _string(raw["reason"], "failed-output reason")
    status = _string(raw["status"], "failed-output status")
    if status not in {"retained", "absent", "unavailable"} or len(files) > _FAILED_OUTPUT_FILES or (status == "absent" and files):
        raise ValueError("invalid failed-output capture status/inventory")
    total = 0
    for data in files.values():
        if not isinstance(data, str):
            raise ValueError("failed output bytes must be base64 strings")
        try:
            total += len(base64.b64decode(data, validate=True))
        except binascii.Error as error:
            raise ValueError("invalid failed-output encoding") from error
        if total > _FAILED_OUTPUT_BYTES:
            raise ValueError("failed-output byte bound exceeded")


@dataclass
class TrialRecovery:
    units: list[dict[str, Any]] = field(default_factory=list)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    prepared: dict[str, dict[str, Any]] | None = None
    sessions: set[str] = field(default_factory=set)
    opened: bool = False
    started: int = 0
    finished: int = 0
    segment: str = ""
    abandoned: int = 0
    segments: int = 0
    reused: list[str] = field(default_factory=list)


def replay_trial(events: list[Any], plan: SuiteMeasurementPlan, trial: int, *, complete: bool = False) -> TrialRecovery:
    """Derive a fixed completed prefix, never choose retained results by value."""
    import re

    assert plan.schedule is not None
    order = plan.schedule.phases[trial - 1]
    state = TrialRecovery()
    pending = None
    phases: list[dict[str, Any]] = []
    phase_start = None
    previous = 0
    ended = False
    failed = False

    def session(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None or value in state.sessions:
            raise ValueError("recovery sessions must be unique collection identities")
        state.sessions.add(value)
        return value

    for value in events:
        event = _object(value, "trial lifecycle event")
        kind = event.get("kind")
        fields = {
            "segment": {"session", "at_ns", "prepared"},
            "suite-start": {"suite", "session", "at_ns"},
            "phase-start": {"phase", "at_ns"},
            "phase-complete": {"phase", "started_ns", "finished_ns", "sample", "source"},
            "phase-failed": {"phase", "started_ns", "at_ns", "reason", "output"},
            "suite-complete": {"unit"},
            "abandoned": {"at_ns", "reason"},
            "segment-end": {"at_ns"},
            "recovered-end": {"at_ns", "reason"},
        }
        if not isinstance(kind, str) or kind not in fields:
            raise ValueError("unknown trial lifecycle event")
        _object(event, "trial lifecycle event", {"trial", "kind", *fields[kind]})
        if type(event["trial"]) is not int or event["trial"] != trial or ended:
            raise ValueError("recovery trial inventory/order differs")
        at = _integer(
            event["unit"].get("finished_ns") if kind == "suite-complete" and isinstance(event["unit"], dict) else event.get("finished_ns", event.get("at_ns")),
            "recovery timestamp",
        )
        if at < previous:
            raise ValueError("recovery event intervals overlap or are reordered")
        previous = at
        if kind == "segment":
            if state.opened or pending is not None or len(state.units) == len(plan.suites):
                raise ValueError("invalid recovery segment transition")
            state.segment = session(event["session"])
            raw_prepared = _object(event["prepared"], "prepared suite assertions", {s.name for s in plan.suites})
            prepared = {}
            for suite in plan.suites:
                observations = _object(raw_prepared[suite.name], "prepared phases")
                prepared[suite.name] = observations
                if not set(observations) <= {"baseline", "current"}:
                    raise ValueError("invalid recovery preparation phases")
                for phase, source in observations.items():
                    _validate_phase_identity(_source(source), suite.measurement, phase)
            if state.prepared is not None and deterministic_json(prepared) != deterministic_json(state.prepared):
                raise ValueError("recovery preparation assertions changed")
            state.prepared = prepared
            state.reused = [unit["name"] for unit in state.units]
            state.opened = True
            state.segments += 1
            if state.segments == 1:
                state.started = at
        elif kind == "recovered-end":
            if state.opened or pending is not None or len(state.units) != len(plan.suites) or not state.abandoned:
                raise ValueError("recovery finalization requires all committed pairs and a disclosed abandoned segment")
            _string(event["reason"], "recovery finalization reason")
            state.finished = at
            ended = True
        elif not state.opened:
            raise ValueError("recovery event outside a collection segment")
        elif kind == "suite-start":
            if pending is not None or len(state.units) >= len(plan.suites) or event["suite"] != plan.suites[len(state.units)].name:
                raise ValueError("recovery suites must form a contiguous completed prefix")
            session(event["session"])
            pending = event
            phases = []
            phase_start = None
            failed = False
        elif kind == "phase-start":
            if pending is None or phase_start is not None or failed:
                raise ValueError("invalid recovery phase start")
            assert state.prepared is not None
            expected = [phase for phase in order if phase in state.prepared[_string(pending["suite"], "suite")]]
            if len(phases) >= len(expected) or event["phase"] != expected[len(phases)]:
                raise ValueError("recovery phase order differs from schedule")
            phase_start = event
        elif kind == "phase-complete":
            if pending is None or phase_start is None or failed or event["phase"] != phase_start["phase"] or event["started_ns"] != phase_start["at_ns"]:
                raise ValueError("recovery phase completion lacks its original start")
            suite = plan.suites[len(state.units)]
            source = _source(event["source"])
            sample = _parse_complete_sample(event["sample"])
            phase = _string(event["phase"], "phase")
            _validate_phase_sample(suite.measurement, phase, sample, source)
            observed = {
                **_source_record(source),
                "context": {key: val for key, val in dict(source.context).items() if key not in {"gate-status", "saved-sample"}},
            }
            assert state.prepared is not None
            if deterministic_json(observed) != deterministic_json(state.prepared[suite.name][phase]):
                raise ValueError("recovery phase differs from independently prepared inputs")
            phases.append(event)
            phase_start = None
        elif kind == "phase-failed":
            if pending is None or phase_start is None or event["phase"] != phase_start["phase"] or event["started_ns"] != phase_start["at_ns"] or failed:
                raise ValueError("failed phase lacks its original unresolved start")
            _string(event["reason"], "phase failure reason")
            _validate_failed_output(event["output"])
            failed = True
        elif kind == "suite-complete":
            unit = _object(event["unit"], "committed recovery suite")
            if (
                pending is None
                or phase_start is not None
                or failed
                or (unit.get("name"), unit.get("session"), unit.get("started_ns")) != (pending["suite"], pending["session"], pending["at_ns"])
            ):
                raise ValueError("committed recovery suite differs from its attempt")
            assert state.prepared is not None
            expected = [phase for phase in order if phase in state.prepared[_string(pending["suite"], "suite")]]
            if [phase["phase"] for phase in phases] != expected:
                raise ValueError("committed recovery suite lacks complete paired phases")
            observations = {phase["phase"]: {"sample": phase["sample"], "source": phase["source"]} for phase in phases}
            if deterministic_json(unit.get("observations")) != deterministic_json(observations):
                raise ValueError("committed recovery raw observations changed")
            state.units.append(unit)
            state.receipts.extend({"suite": pending["suite"], **{key: phase[key] for key in ("phase", "started_ns", "finished_ns")}} for phase in phases)
            pending = None
            phases = []
        elif kind == "abandoned":
            _string(event["reason"], "abandonment reason")
            state.abandoned += 1
            state.opened = False
            pending = None
            phases = []
            phase_start = None
        elif kind == "segment-end":
            if pending is not None or len(state.units) != len(plan.suites):
                raise ValueError("recovery segment ended before all paired suites committed")
            state.opened = False
            state.finished = at
            ended = True
    if complete and (not ended or state.opened or len(state.units) != len(plan.suites)):
        raise ValueError("incomplete trial recovery lifecycle")
    return state
