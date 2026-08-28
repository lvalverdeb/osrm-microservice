"""Shadow mode and canary rollout — §11.4, T-65.

§11.4 opens with the sentence this module serves: "Benchmarks validate the
algorithm; only production validates the model." Then the three stages:

* **Shadow mode.** "Produce plans daily without executing them; measure the gap
  between the shadow plan and the executed plan, and interrogate every large
  divergence."
* **Canary.** "One depot, one month, with explicit rollback criteria agreed in
  advance."
* **Plan adherence.** "This is the metric that tells you whether the model is
  right" -- T-61's, reused rather than reinvented.

**"Agreed in advance" is the phrase a tool can actually enforce**, and it is the
reason this module has a fingerprint in it. Criteria chosen after seeing the
results are not criteria; they are a rationalisation, and the failure mode is
completely ordinary -- the run lands 4% down, somebody observes that 5% was
always the real line, and the canary has demonstrated nothing. So criteria are
fixed at construction, hashed, and the decision carries the hash. It does not
prevent anyone moving the bar; it makes the move visible in the record, which is
the most a library can do about a human process.

**A canary is one depot.** Judging it on the whole fleet's data would make it a
rollout with extra steps, and a bounded blast radius is the entire point.

**Any criterion failing is a no-go**, not a score and not a majority. §11.4 calls
them rollback criteria, and a criterion that can be outvoted is a preference.

**The defaults fail closed.** No days is a no-go, and a run shorter than the
agreed month is a no-go. A rollout tool whose default answer is "ship it" is the
wrong way round, and the case where the data never arrived looks exactly like
the case where everything went well if nobody decided in advance which it is.

Placement: **Python**, per criterion 2. It composes T-61's adherence with a
plan-producing callable, and changes whenever either does.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from vrp.adherence import ExecutedRoute, adherence
from vrp.model import Problem, Solution

Planner = Callable[[ExecutedRoute], Solution]


@dataclass(frozen=True)
class Criterion:
    """One rollback threshold, agreed before the run. §11.4."""

    name: str
    limit: int

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a criterion needs a name")


@dataclass(frozen=True)
class Failure:
    """A criterion that was not met, with the number that missed it."""

    name: str
    limit: int
    observed: int


@dataclass(frozen=True)
class ShadowDay:
    """One day planned in shadow and compared with what happened."""

    depot_id: str
    dissimilarity: int
    cost_delta: int


@dataclass(frozen=True)
class Canary:
    """A scoped rollout with its criteria fixed in advance. §11.4."""

    depot_id: str
    criteria: tuple[Criterion, ...]
    minimum_days: int = 20

    def __post_init__(self) -> None:
        if not self.criteria:
            raise ValueError(
                "a canary needs explicit rollback criteria; §11.4 requires "
                "them agreed in advance and none is not explicit")

    @property
    def fingerprint(self) -> str:
        """A hash of the criteria, so a reader can tell whether the bar moved.

        Not tamper-proof and not meant to be -- anyone can re-run with
        different criteria. What it prevents is the quiet version: a decision
        reported against criteria nobody can check were the ones agreed.
        """
        material = f"{self.depot_id}|{self.minimum_days}|" + "|".join(
            f"{c.name}:{c.limit}" for c in sorted(self.criteria,
                                                  key=lambda c: c.name))
        return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True)
class Decision:
    """§11.4's go/no-go, written down."""

    go: bool
    depot_id: str
    days_considered: int
    fingerprint: str
    failed: tuple[Failure, ...] = ()
    summary: str = ""


def shadow(problem: Problem, planner: Planner,
           executed: Sequence[ExecutedRoute]) -> list[ShadowDay]:
    """Plan each day without executing it, and measure the gap. §11.4.

    Args:
        problem: the instance.
        planner: produces the shadow plan for a day. Injected, so the thing
            under evaluation is whatever the caller is trying to roll out.
        executed: what actually happened, from T-61's telematics.

    Returns:
        One `ShadowDay` per executed route.

    The shadow plan never reaches a vehicle: this takes what happened as input
    and returns a comparison, and there is deliberately no path out of here to
    a dispatch. §11.4's "without executing them" is a property of the shape,
    not a promise in a docstring.
    """
    days = []
    for route in executed:
        measured = adherence(problem, planner(route), (route,))[0]
        days.append(ShadowDay(depot_id=route.depot_id,
                              dissimilarity=measured.dissimilarity,
                              cost_delta=measured.cost_delta))
    return days


def divergences(days: Sequence[ShadowDay], threshold: int) -> list[ShadowDay]:
    """The days worth interrogating. §11.4's "every large divergence".

    Large, not every: a list that flagged all of them would be a list nobody
    reads, and §12.4 has already said that routine deviation is information
    rather than a fault.
    """
    return [day for day in days if day.dissimilarity > threshold]


def decide(canary: Canary, days: Sequence[ShadowDay]) -> Decision:
    """The go/no-go, written down. §11.4.

    Args:
        canary: the depot, the criteria and the agreed length.
        days: shadow days, from any depot; only this canary's are considered.

    Returns:
        A `Decision` whose `summary` is a paragraph fit for a change record.

    Fails closed. No days is a no-go and a short run is a no-go, because a month
    where the data never arrived looks exactly like a month where everything
    went well unless somebody decided in advance which it is.
    """
    mine = [day for day in days if day.depot_id == canary.depot_id]
    short = canary.fingerprint[:8]

    if not mine:
        return Decision(
            go=False, depot_id=canary.depot_id, days_considered=0,
            fingerprint=canary.fingerprint,
            summary=(f"NO-GO for depot {canary.depot_id}: no days observed. "
                     f"Criteria {short} were never tested."))

    if len(mine) < canary.minimum_days:
        return Decision(
            go=False, depot_id=canary.depot_id, days_considered=len(mine),
            fingerprint=canary.fingerprint,
            summary=(f"NO-GO for depot {canary.depot_id}: {len(mine)} days "
                     f"observed against {canary.minimum_days} agreed. "
                     f"Criteria {short} not yet tested over the agreed run."))

    observed = {
        "adherence": max(day.dissimilarity for day in mine),
        "cost_delta": max(day.cost_delta for day in mine),
    }
    failed = tuple(
        Failure(name=c.name, limit=c.limit, observed=observed.get(c.name, 0))
        for c in canary.criteria
        if observed.get(c.name, 0) > c.limit)

    verdict = "NO-GO" if failed else "GO"
    lines = [(f"{verdict} for depot {canary.depot_id} over {len(mine)} days, "
              f"criteria {short}.")]
    for criterion in sorted(canary.criteria, key=lambda c: c.name):
        seen = observed.get(criterion.name, 0)
        mark = "FAIL" if seen > criterion.limit else "ok"
        lines.append(f"  {criterion.name}: {seen} against a limit of "
                     f"{criterion.limit} -- {mark}")

    return Decision(go=not failed, depot_id=canary.depot_id,
                    days_considered=len(mine),
                    fingerprint=canary.fingerprint, failed=failed,
                    summary="\n".join(lines))
