"""Advisory, versioned progress for read-only dependency verification."""

import json
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TextIO

__all__ = ["DependencyProgress"]


class DependencyProgress:
    """Report phases/counts without provider URLs, exception payloads or an ETA.

    Observers receive independent event dictionaries. Observer/stream failures
    abort verification, but cannot replace an already active verification error.
    """

    def __init__(
        self,
        *,
        mode: str = "human",
        stream: TextIO | None = None,
        observer: Callable[[dict[str, object]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if mode not in {"human", "json", "none"}:
            raise ValueError("dependency progress mode must be human, json, or none")
        self.mode, self.observer, self.clock = mode, observer, clock
        self.stream = stream if stream is not None else sys.stderr
        self.started = clock()
        self.requests = 0
        self.retries = 0

    def emit(self, event: str, phase: str, *, graph: str | None = None, checked: int = 0, total: int | None = None, detail: str | None = None) -> None:
        if event == "request":
            self.requests += 1
        elif event == "retry":
            self.retries += 1
        data: dict[str, object] = {
            "schema": "research-repo-tools/dependency-progress/v1",
            "event": event,
            "phase": phase,
            "graph": graph,
            "checked": checked,
            "total": total,
            "elapsed_seconds": max(0.0, self.clock() - self.started),
            "requests": self.requests,
            "retries": self.retries,
            "detail": detail,
        }
        if self.observer is not None:
            self.observer(data.copy())
        if self.mode == "none" or (self.mode == "human" and event == "request"):
            return
        message = (
            json.dumps(data, ensure_ascii=True, sort_keys=True, allow_nan=False)
            if self.mode == "json"
            else (
                f"Dependencies {event}: {phase} {graph or ''}; {checked}/{total if total is not None else 'unknown'} checked; "
                f"elapsed {data['elapsed_seconds']:.1f}s; {self.requests} requests, {self.retries} retries" + (f"; {detail}" if detail else "")
            )
        )
        encoding = self.stream.encoding or "utf-8"
        print(message.encode(encoding, errors="backslashreplace").decode(encoding), file=self.stream, flush=True)


_CURRENT: ContextVar[DependencyProgress | None] = ContextVar("dependency_progress", default=None)


def report(event: str, phase: str, *, graph: str | None = None, checked: int = 0, total: int | None = None, detail: str | None = None) -> None:
    progress = _CURRENT.get()
    if progress is not None:
        progress.emit(event, phase, graph=graph, checked=checked, total=total, detail=detail)


@contextmanager
def reporting(progress: DependencyProgress | None, phase: str) -> Iterator[None]:
    token = _CURRENT.set(progress if progress is not None else _CURRENT.get())
    try:
        report("started", phase)
        yield
    except BaseException as error:
        # Exceptions can contain credentials or signed URLs. The owning CLI
        # supplies its separately redacted diagnostic, never this observer.
        try:
            report("failed", phase, detail="Verification did not complete; retained evidence remains subject to its original guards.")
        except Exception:
            error.add_note("Dependency failure progress could not be emitted.")
        raise
    else:
        report("completed", phase)
    finally:
        _CURRENT.reset(token)
