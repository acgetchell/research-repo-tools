"""Advisory acquisition progress; only the acquisition validator accepts work."""

import json
import math
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Event, Thread
from typing import TextIO

__all__ = ["ProgressReporter"]


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
        cadence: float = 10.0,
        stream: TextIO | None = None,
        observer: Callable[[dict[str, object]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if mode not in {"human", "json", "none"}:
            raise ValueError("progress mode must be human, json, or none")
        if isinstance(cadence, bool) or not math.isfinite(cadence) or cadence < 1:
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

    def budget(self, counts: dict[str, int], nominal_seconds: float | None = None, *, inventory: dict[tuple[str, str], int] | None = None) -> None:
        """Set discovered counts, keyed by independently compatible work classes."""
        if any(type(count) is not int or count < 0 for count in counts.values()):
            raise ValueError("progress counts must be nonnegative integers")
        if nominal_seconds is not None and (not math.isfinite(nominal_seconds) or nominal_seconds < 0):
            raise ValueError("nominal timing budget must be finite and nonnegative")
        if inventory is not None and (
            any(type(count) is not int or count < 0 for count in inventory.values()) or sum(inventory.values()) != sum(counts.values())
        ):
            raise ValueError("phase inventory must partition the discovered budget")
        self._inventory = dict(inventory or {})
        self._accepted = dict.fromkeys(self._inventory, 0)
        self._remaining = dict(counts)
        self.completed = 0
        self._rates.clear()
        self.total = sum(counts.values())
        self.nominal = nominal_seconds
        self.emit("budget", force=True)

    def accept(self, work_class: str, count: int, elapsed: float, *, retained: bool = False) -> None:
        """Advance only after validation; retained work supplies no new timing rate."""
        if type(count) is not int or count < 0 or work_class not in self._remaining or count > self._remaining[work_class]:
            raise ValueError("accepted progress exceeds its discovered inventory")
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("accepted elapsed time must be finite and nonnegative")
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
            self._rates[work_class] = elapsed / count
        self.emit("retained" if retained else "validated", force=True)

    def emit(self, event: str, *, force: bool = False) -> None:
        """Publish one snapshot; ordinary heartbeats obey the configured cadence."""
        now = self.clock()
        if not force and now - self._last < self.cadence:
            return
        self._last = now
        self.last_event = event
        unseen = sorted(key for key, count in self._remaining.items() if count and key not in self._rates)
        eta = None if self.total is None or unseen else sum(count * self._rates.get(key, 0) for key, count in self._remaining.items())
        compatible_eta = sum(count * self._rates[key] for key, count in self._remaining.items() if key in self._rates) if self._rates else None
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
            "eta_reason": "unseen work classes" if unseen else "inventory unknown" if self.total is None else "observed compatible work; not an upper bound",
            "unseen_work_classes": unseen,
            "inventory": inventory,
        }
        if self.observer is not None:
            self.observer(data)
        if self.mode == "none":
            return
        if self.mode == "json":
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
        encoding = self.stream.encoding or "utf-8"
        print(message.encode(encoding, errors="backslashreplace").decode(encoding), file=self.stream, flush=True)

    @contextmanager
    def stage(self, state: str, *, suite: str | None = None, phase: str | None = None) -> Iterator[None]:
        """Announce states and bounded heartbeats, reporting failures/interruption."""
        self.state, self.suite, self.phase = state, suite, phase
        self.emit("started", force=True)
        stopped = Event()

        def heartbeat() -> None:
            while not stopped.wait(self.cadence):
                self.emit("heartbeat")

        worker = Thread(target=heartbeat, name="performance-progress", daemon=True)
        if self.mode != "none" or self.observer is not None:
            worker.start()
        try:
            yield
        except BaseException as error:
            stopped.set()
            if worker.ident is not None:
                worker.join()
            self.emit("interrupted" if isinstance(error, KeyboardInterrupt) else "failed", force=True)
            raise
        else:
            stopped.set()
            if worker.ident is not None:
                worker.join()
            self.emit("finished", force=True)
