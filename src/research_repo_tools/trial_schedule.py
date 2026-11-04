"""Predeclared acquisition order, independent of release chronology."""

import hashlib
from dataclasses import dataclass

from research_repo_tools.complete_runs import _integer
from research_repo_tools.evidence import _object, _string

__all__ = ["TrialSchedule", "parse_schedule"]


@dataclass(frozen=True, slots=True)
class TrialSchedule:
    """Balanced pairs; randomization uses versioned SHA-256 ranks, not ambient RNG."""

    trials: int = 2
    order: str = "alternating"
    seed: int | None = None

    def __post_init__(self) -> None:
        if type(self.trials) is not int or not 1 <= self.trials <= 1000:
            raise ValueError("trial count must be an integer from 1 through 1000")
        if self.order not in {"alternating", "randomized", "sequential"}:
            raise ValueError("trial order must be alternating, randomized, or sequential")
        if self.order != "sequential" and (self.trials < 2 or self.trials % 2):
            raise ValueError("counterbalancing requires an even number of trials")
        if self.order == "randomized":
            if type(self.seed) is not int or not 0 <= self.seed < 2**64:
                raise ValueError("randomized order requires an unsigned 64-bit seed")
        elif self.seed is not None:
            raise ValueError("only randomized order accepts a seed")

    @property
    def phases(self) -> tuple[tuple[str, str], ...]:
        reverse = {i for i in range(self.trials) if i % 2} if self.order == "alternating" else set()
        if self.order == "randomized":
            ranked = sorted(range(self.trials), key=lambda i: (hashlib.sha256(f"rrt-schedule-v1:{self.seed}:{i}".encode("ascii")).digest(), i))
            reverse = set(ranked[: self.trials // 2])
        return tuple(("current", "baseline") if i in reverse else ("baseline", "current") for i in range(self.trials))

    def document(self) -> dict[str, object]:
        return {"trials": self.trials, "order": self.order, "seed": self.seed, "phases": [list(pair) for pair in self.phases]}


def parse_schedule(value: object) -> TrialSchedule:
    raw = _object(value, "schedule")
    if {"trials", "order"} - raw.keys() or raw.keys() - {"trials", "order", "seed", "phases"}:
        raise ValueError("invalid schedule fields")
    schedule = TrialSchedule(
        _integer(raw["trials"], "trials"), _string(raw["order"], "order"), _integer(raw["seed"], "seed") if raw.get("seed") is not None else None
    )
    if "phases" in raw and raw["phases"] != schedule.document()["phases"]:
        raise ValueError("schedule phases differ from declared algorithm/seed")
    return schedule
