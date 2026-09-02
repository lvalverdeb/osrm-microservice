"""The run record — NFR-06, CON-4, T-87.

`NFR-06`: "Every run emits: objective trajectory over time, incumbent
timestamps, constraint-violation counts, matrix cache hit rate, seed, solver
version, deterministic iteration count."

Seven things. `Solution.solver` carried three of them -- seed, solver version
and the iteration count `CON-4` demands -- and the other four existed in pieces
nothing collected: `PairCache` counts its own hits, the independent verifier
returns violations, and the objective trajectory was not recorded at all.

**The risk in a requirement like this is a decorative record.** Every field is
easy to emit and hard to make true: a trajectory that is always empty, a hit
rate that is always zero, a violation count nobody increments. Such a record
passes any test that asks whether the field exists, and tells an operator
nothing on the day they need it. The tests for this module therefore run the
same thing twice with one dimension changed and assert the field moved.

**Two clocks, and only one of them replays.** `NFR-06` asks for a trajectory
"over time" and for "incumbent timestamps"; `CON-4` asks that any run be
replayable. Wall-clock nanoseconds satisfy the first and destroy the second --
two runs of the same seed agree about everything except when they happened.
Recording only iterations would lose what NFR-06 asked for. So both are kept
and `replayable()` names the half that is a function of the seed, rather than
leaving somebody to discover which fields to ignore when comparing two runs.

Placement: **Python**, per criterion 2. It assembles what the solve, the
verifier and the matrix cache already know, and changes when they do.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:                       # pragma: no cover
    from vrp.matrix import PairCache
    from vrp.verify import Report

# NFR-06's list, as attribute names on `RunRecord`. Written out so a test can
# check the record against the requirement rather than against a count, and so
# that a field renamed here without the requirement changing fails loudly.
NFR_06_FIELDS: tuple[str, ...] = (
    "trajectory",           # objective trajectory over time
    "incumbent_stamps",     # incumbent timestamps
    "violations",           # constraint-violation counts
    "cache_hit_rate_ppt",   # matrix cache hit rate
    "seed",
    "solver",               # solver version
    "iterations",           # deterministic iteration count
)

PPT = 1000


@dataclass(frozen=True)
class Incumbent:
    """One improvement, stamped by both clocks.

    Attributes:
        iteration: which iteration found it. Deterministic, and the half that
            replays.
        elapsed_ns: nanoseconds since the run began. What an operator reads,
            and what will never match between two runs of the same seed.
        objective: the canonical objective at that moment.
    """

    iteration: int
    elapsed_ns: int
    objective: int


@dataclass(frozen=True)
class RunRecord:
    """What one run emits. NFR-06's seven, from one place."""

    solver: str
    seed: int
    iterations: int
    trajectory: tuple[Incumbent, ...] = ()
    violations: dict[str, int] = field(default_factory=dict)
    cache_hits: int = 0
    cache_lookups: int = 0

    @property
    def incumbent_stamps(self) -> tuple[int, ...]:
        """When each incumbent was found, in nanoseconds since the start."""
        return tuple(point.elapsed_ns for point in self.trajectory)

    @property
    def cache_hit_rate_ppt(self) -> int:
        """MTX-10's hit rate, in parts per thousand.

        An integer because CON-4 forbids accumulating floats, and because a
        rate that reads `0.8999999999` in one log line and `0.9` in another
        invites an argument nobody should have to have.
        """
        if self.cache_lookups <= 0:
            return 0
        return self.cache_hits * PPT // self.cache_lookups

    @property
    def total_violations(self) -> int:
        return sum(self.violations.values())

    def replayable(self) -> dict[str, Any]:
        """The half of the record that is a function of the seed. CON-4.

        Excludes `elapsed_ns` and the cache counters: the first is wall-clock
        and the second depends on what a previous day left warm. Two runs of
        the same seed on the same instance must agree here exactly, and
        comparing whole records instead would fail for reasons that say nothing
        about the plan.
        """
        return {
            "solver": self.solver,
            "seed": self.seed,
            "iterations": self.iterations,
            "trajectory": [(point.iteration, point.objective)
                           for point in self.trajectory],
            "violations": dict(sorted(self.violations.items())),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "solver": self.solver,
            "seed": self.seed,
            "iterations": self.iterations,
            "trajectory": [[point.iteration, point.elapsed_ns, point.objective]
                           for point in self.trajectory],
            "violations": dict(self.violations),
            "cache_hits": self.cache_hits,
            "cache_lookups": self.cache_lookups,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RunRecord:
        return cls(
            solver=raw["solver"], seed=raw["seed"], iterations=raw["iterations"],
            trajectory=tuple(Incumbent(iteration=point[0], elapsed_ns=point[1],
                                       objective=point[2])
                             for point in raw.get("trajectory", ())),
            violations=dict(raw.get("violations", {})),
            cache_hits=raw.get("cache_hits", 0),
            cache_lookups=raw.get("cache_lookups", 0),
        )


class Recorder:
    """Collects a run's observations while it happens.

    Mutable on purpose: a run is a sequence of events and the record is what is
    left when it ends. `finish` returns the frozen `RunRecord`.
    """

    def __init__(self, solver: str, seed: int,
                 clock: Any = time.perf_counter_ns) -> None:
        """
        Args:
            solver: the engine and its version, as `Solution.solver` spells it.
            seed: the run's seed.
            clock: nanosecond source, injectable so a test can pin it.
        """
        self._solver = solver
        self._seed = seed
        self._clock = clock
        self._started = clock()
        self._trajectory: list[Incumbent] = []

    def improved(self, iteration: int, objective: int) -> None:
        """Record a new best.

        Args:
            iteration: which iteration found it.
            objective: the canonical objective, which must be better than the
                last recorded one.

        Raises:
            ValueError: if `objective` is not an improvement. A trajectory that
                accepts equal or worse entries measures how often the loop ran
                rather than how it did, and the two are indistinguishable once
                written down.
        """
        if self._trajectory and objective >= self._trajectory[-1].objective:
            raise ValueError(
                f"{objective} is not better than the incumbent "
                f"{self._trajectory[-1].objective}; a trajectory of "
                "non-improvements is a loop counter wearing another name")
        self._trajectory.append(Incumbent(iteration=iteration,
                                          elapsed_ns=self._clock() - self._started,
                                          objective=objective))

    def finish(self, iterations: int, violations: dict[str, int] | None = None,
               cache: PairCache | None = None) -> RunRecord:
        """Close the record.

        Args:
            iterations: the deterministic count *achieved*, not the budget.
                CON-4 asks for the achieved figure because that is what a
                replay has to reproduce.
            violations: counts by invariant, from `violations_of`.
            cache: the pair cache, for MTX-10's hit rate.

        Returns:
            The run record.
        """
        return RunRecord(
            solver=self._solver, seed=self._seed, iterations=iterations,
            trajectory=tuple(self._trajectory),
            violations=dict(violations or {}),
            cache_hits=0 if cache is None else cache.hits,
            cache_lookups=0 if cache is None else cache.hits + cache.misses,
        )

    @staticmethod
    def violations_of(report: Report) -> dict[str, int]:
        """Count a verifier report by invariant.

        Taken from the independent verifier rather than from the solver's own
        opinion of its plan, which is what CON-1 exists to keep separate.
        """
        counts: dict[str, int] = {}
        for violation in report.violations:
            counts[violation.invariant] = counts.get(violation.invariant, 0) + 1
        return counts
