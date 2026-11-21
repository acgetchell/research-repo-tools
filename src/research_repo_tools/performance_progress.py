"""Advisory acquisition progress; only the acquisition validator accepts work."""

import json
import math
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from threading import Event, Thread
from typing import TextIO

__all__ = ["ProgressReporter"]


def _seconds(value: object, field: str) -> float:
    """Parse a nonnegative finite duration before accepting progress."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= sys.float_info.max:
        raise ValueError(f"{field} must be finite and nonnegative")
    return float(value)


def _work_name(value: object) -> bool:
    """Return whether a budget key names a work class, suite, or phase."""
    return isinstance(value, str) and bool(value)


def _phase_key(value: object) -> bool:
    """Require the suite and phase names used to group progress events."""
    return isinstance(value, tuple) and len(value) == 2 and all(_work_name(item) for item in value)


class ProgressReporter:
    """Emit versioned status events without reading native output or raw samples.

    Completion is supplied after complete-phase validation. Heartbeat work is
    bounded by the suite inventory and never scans benchmark directories. The observer runs
    on the acquisition thread except for heartbeat events while a command runs.
    """

    def __init__(
        self,
        *,
        mode: str = "human",
        cadence: float = 300.0,
        stream: TextIO | None = None,
        observer: Callable[[dict[str, object]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if mode not in ("human", "json", "none"):
            raise ValueError("progress mode must be human, json, or none")
        cadence = _seconds(cadence, "progress cadence")
        if cadence < 1:
            raise ValueError("progress cadence must be finite and at least one second")
        self.mode = mode
        self.cadence = cadence
        self.stream = stream if stream is not None else sys.stderr
        self.observer = observer
        self.clock = clock
        self.started = clock()
        self.completed = 0
        self.total: int | None = None
        self.nominal: float | None = None
        self.state = "preparation"
        self.phase: str | None = None
        self.suite: str | None = None
        self._last = -math.inf
        self.last_event: str | None = None
        self._rates: dict[str, float] = {}
        self._remaining: dict[str, int] = {}
        self._inventory: dict[tuple[str, str], int] = {}
        self._accepted: dict[tuple[str, str], int] = {}

    def budget(self, counts: Mapping[str, int], nominal_seconds: float | None = None, *, inventory: Mapping[tuple[str, str], int] | None = None) -> None:
        """Snapshot discovered work counts and an optional partition by suite and phase."""
        remaining = dict(counts)
        phase_counts = {} if inventory is None else dict(inventory)
        if not all(_work_name(name) for name in remaining):
            raise ValueError("progress work classes must be nonempty strings")
        if any(type(count) is not int or count < 0 for count in remaining.values()):
            raise ValueError("progress counts must be nonnegative integers")
        total = sum(remaining.values())
        if nominal_seconds is not None:
            nominal_seconds = _seconds(nominal_seconds, "nominal timing budget")
        if not all(_phase_key(scope) for scope in phase_counts):
            raise ValueError("phase inventory keys must be (suite, phase) pairs of nonempty strings")
        if inventory is not None and (any(type(count) is not int or count < 0 for count in phase_counts.values()) or sum(phase_counts.values()) != total):
            raise ValueError("phase inventory must partition the discovered budget")
        self._inventory = phase_counts
        self._accepted = dict.fromkeys(self._inventory, 0)
        self._remaining = remaining
        self.completed = 0
        self._rates.clear()
        self.total = total
        self.nominal = nominal_seconds
        self.emit("budget", force=True)

    def accept(self, work_class: str, count: int, elapsed: float, *, retained: bool = False) -> None:
        """Advance only after validation; retained work supplies no new timing rate."""
        if type(count) is not int or count < 0 or not _work_name(work_class) or work_class not in self._remaining or count > self._remaining[work_class]:
            raise ValueError("accepted progress exceeds its discovered inventory")
        elapsed = _seconds(elapsed, "accepted elapsed time")
        if type(retained) is not bool:
            raise ValueError("retained progress must be a boolean")
        if self._inventory:
            if self.suite is None or self.phase is None:
                raise ValueError("accepted progress requires its suite and phase")
            scope = (self.suite, self.phase)
            if scope not in self._inventory or self._accepted[scope] + count > self._inventory[scope]:
                raise ValueError("accepted progress exceeds its phase inventory")
            self._accepted[scope] += count
        self._remaining[work_class] -= count
        self.completed += count
        if count and not retained:
            numerator, denominator = elapsed.as_integer_ratio()
            self._rates[work_class] = numerator / (denominator * count)
        self.emit("retained" if retained else "validated", force=True)

    def _compatible_eta(self) -> float | None:
        """Return the representable estimate for classes with measured rates."""
        if not self._rates:
            return None
        try:
            estimate = sum(self._remaining[key] * rate for key, rate in self._rates.items())
        except OverflowError:
            return None
        return estimate if math.isfinite(estimate) else None

    def emit(self, event: str, *, force: bool = False) -> None:
        """Publish one snapshot; ordinary heartbeats obey the configured cadence."""
        now = self.clock()
        if not force and now - self._last < self.cadence:
            return
        self._last = now
        self.last_event = event
        unseen = sorted(key for key, count in self._remaining.items() if count and key not in self._rates)
        compatible_eta = self._compatible_eta()
        eta = None if self.total is None or unseen else 0.0 if self.total == self.completed else compatible_eta
        if unseen:
            reason = "unseen work classes"
        elif self.total is None:
            reason = "inventory unknown"
        elif eta is None:
            reason = "estimate exceeds finite range"
        else:
            reason = "observed compatible work; not an upper bound"
        inventory = [
            {"suite": suite, "phase": phase, "total": count, "completed": self._accepted[(suite, phase)], "remaining": count - self._accepted[(suite, phase)]}
            for (suite, phase), count in sorted(self._inventory.items())
        ]
        data: dict[str, object] = {
            "schema": "research-repo-tools/acquisition-progress/v1",
            "event": event,
            "state": self.state,
            "suite": self.suite,
            "phase": self.phase,
            "completed": self.completed,
            "remaining": None if self.total is None else self.total - self.completed,
            "total": self.total,
            "elapsed_seconds": max(0.0, now - self.started),
            "nominal_seconds": self.nominal,
            "eta_seconds": eta,
            "compatible_eta_seconds": compatible_eta,
            "unknown_cases": sum(self._remaining[key] for key in unseen),
            "eta_reason": reason,
            "unseen_work_classes": unseen,
            "inventory": inventory,
        }
        if self.mode == "none":
            message = None
        elif self.mode == "json":
            message = json.dumps(data, ensure_ascii=True, allow_nan=False, sort_keys=True)
        else:
            remaining = "unknown" if data["remaining"] is None else str(data["remaining"])
            estimate = "unknown" if eta is None else f"~{eta:.0f}s (estimate)"
            nominal = "unknown" if self.nominal is None else f"{self.nominal:.0f}s (nominal, not a limit)"
            message = (
                f"Performance {event}: {self.state} {self.suite or ''} {self.phase or ''}; "
                f"{self.completed} validated, {remaining} remaining; elapsed {now - self.started:.1f}s; ETA {estimate}; budget {nominal}"
            )
            if unseen and compatible_eta is not None:
                message += f"; observed-compatible remainder ~{compatible_eta:.0f}s, plus {data['unknown_cases']} unestimated cases"
            selected = [scope for scope in self._inventory if scope[0] == self.suite]
            if selected:
                message += f"; suite {sum(self._accepted[scope] for scope in selected)}/{sum(self._inventory[scope] for scope in selected)}"
                for scope in selected:
                    if scope[1] == self.phase:
                        message += f"; phase {self._accepted[scope]}/{self._inventory[scope]}"
        if self.observer is not None:
            self.observer(data)
        if message is not None:
            encoding = self.stream.encoding or "utf-8"
            print(message.encode(encoding, errors="backslashreplace").decode(encoding), file=self.stream, flush=True)

    @contextmanager
    def stage(self, state: str, *, suite: str | None = None, phase: str | None = None) -> Iterator[None]:
        """Announce states and bounded heartbeats, reporting failures/interruption."""
        self.state, self.suite, self.phase = state, suite, phase
        self.emit("started", force=True)
        stopped = Event()
        reporting_errors: list[BaseException] = []

        def heartbeat() -> None:
            try:
                while not stopped.wait(self.cadence):
                    self.emit("heartbeat")
            except BaseException as error:
                reporting_errors.append(error)
                stopped.set()

        worker = Thread(target=heartbeat, name="performance-progress", daemon=True)
        if self.mode != "none" or self.observer is not None:
            worker.start()
        body_error = None
        try:
            yield
        except BaseException as error:
            body_error = error
        finally:
            stopped.set()
            if worker.ident is not None:
                worker.join()
        failures = ([body_error] if body_error is not None else []) + reporting_errors
        if failures:
            failure = failures[0] if len(failures) == 1 else BaseExceptionGroup("acquisition and heartbeat reporting failed", failures)
            self._failed(failure)
            raise failure
        self.emit("finished", force=True)

    def _failed(self, error: BaseException) -> None:
        """Report once without replacing an acquisition failure or interruption."""
        if self.last_event in {"failed", "interrupted"}:
            return
        try:
            interrupted = isinstance(error, KeyboardInterrupt) or isinstance(error, BaseExceptionGroup) and error.subgroup(KeyboardInterrupt) is not None
            self.emit("interrupted" if interrupted else "failed", force=True)
        except BaseException as reporting_error:
            raise BaseExceptionGroup("acquisition and progress reporting failed", [error, reporting_error]) from None
