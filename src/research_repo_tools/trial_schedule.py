"""Predeclared acquisition order, independent of release chronology."""

import hashlib
from dataclasses import dataclass
from typing import assert_never, final

from research_repo_tools.complete_runs import _integer
from research_repo_tools.evidence import _object, _string

__all__ = ["AlternatingOrder", "RandomizedOrder", "SequentialOrder", "TrialOrder", "TrialSchedule", "parse_schedule"]


@final
@dataclass(frozen=True, slots=True)
class AlternatingOrder:
    """Alternate baseline-first and current-first pairs, starting baseline-first."""


@final
@dataclass(frozen=True, slots=True)
class SequentialOrder:
    """Run every trial baseline-first; a single trial is permitted."""


@final
@dataclass(frozen=True, slots=True)
class RandomizedOrder:
    """Counterbalance using a declared unsigned 64-bit seed and SHA-256 ranks."""

    seed: int

    def __post_init__(self) -> None:
        if type(self.seed) is not int or not 0 <= self.seed < 2**64:
            raise ValueError("randomized order requires an unsigned 64-bit seed")


type TrialOrder = AlternatingOrder | SequentialOrder | RandomizedOrder


def _ordering(order: object, seed: object) -> TrialOrder:
    match order:
        case "randomized":
            if type(seed) is not int:
                raise ValueError("randomized order requires an unsigned 64-bit seed")
            return RandomizedOrder(seed)
        case "alternating" | "sequential":
            if seed is not None:
                raise ValueError("only randomized order accepts a seed")
            return AlternatingOrder() if order == "alternating" else SequentialOrder()
        case _:
            raise ValueError("trial order must be alternating, randomized, or sequential")


@dataclass(frozen=True, slots=True)
class TrialSchedule:
    """Balanced pairs; randomization uses versioned SHA-256 ranks, not ambient RNG."""

    trials: int = 2
    order: TrialOrder = AlternatingOrder()

    def __post_init__(self) -> None:
        if type(self.trials) is not int or not 1 <= self.trials <= 1000:
            raise ValueError("trial count must be an integer from 1 through 1000")
        if not isinstance(self.order, AlternatingOrder | SequentialOrder | RandomizedOrder):
            raise TypeError("trial order requires AlternatingOrder, SequentialOrder, or RandomizedOrder")
        if not isinstance(self.order, SequentialOrder) and (self.trials < 2 or self.trials % 2):
            raise ValueError("counterbalancing requires an even number of trials")

    @property
    def phases(self) -> tuple[tuple[str, str], ...]:
        ordering = self.order
        match ordering:
            case AlternatingOrder():
                reverse = {i for i in range(self.trials) if i % 2}
            case SequentialOrder():
                reverse = set()
            case RandomizedOrder(seed):
                ranked = sorted(range(self.trials), key=lambda i: (hashlib.sha256(f"rrt-schedule-v1:{seed}:{i}".encode("ascii")).digest(), i))
                reverse = set(ranked[: self.trials // 2])
            case _:
                assert_never(ordering)
        return tuple(("current", "baseline") if i in reverse else ("baseline", "current") for i in range(self.trials))

    def document(self) -> dict[str, object]:
        ordering = self.order
        match ordering:
            case AlternatingOrder():
                order, seed = "alternating", None
            case SequentialOrder():
                order, seed = "sequential", None
            case RandomizedOrder(seed):
                order = "randomized"
            case _:
                assert_never(ordering)
        return {"trials": self.trials, "order": order, "seed": seed, "phases": [list(pair) for pair in self.phases]}


def parse_schedule(value: object) -> TrialSchedule:
    raw = _object(value, "schedule")
    if {"trials", "order"} - raw.keys() or raw.keys() - {"trials", "order", "seed", "phases"}:
        raise ValueError("invalid schedule fields")
    schedule = TrialSchedule(_integer(raw["trials"], "trials"), _ordering(_string(raw["order"], "order"), raw.get("seed")))
    if "phases" in raw and raw["phases"] != schedule.document()["phases"]:
        raise ValueError("schedule phases differ from declared algorithm/seed")
    return schedule
