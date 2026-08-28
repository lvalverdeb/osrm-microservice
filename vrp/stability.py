"""Churn: measuring it, pricing it, and choosing how much to pay — §8.3, T-57.

§8.3: "Re-optimisation MUST be **stability-aware**: report and optionally
penalise churn (stops moved between vehicles, ETA shifts communicated to
customers). A 0.5% cost gain that reshuffles half the plan at 14:00 is a net
loss... implement churn as a Tier-6 objective term."

Three instructions, and T-56 carried out only the first. It reports churn; this
prices it, puts it in Tier 6 where §8.3 says it belongs, and produces the curve
that lets an operation decide how much stability is worth to them.

**Two kinds, counted separately.** A stop moving to another van is a driver's
problem: an unplanned route, an address they do not know, a van that may not
have the right equipment. A stop keeping its van and shifting an hour is a
customer's problem: somebody was told a time and it is now wrong. §8.3 names
both, and they are not equally expensive in any operation I can think of, so
summing them at source would take a real decision away from the caller.

**Why a curve rather than a constant.** The right penalty depends on what churn
actually costs a business -- a courier network re-planning every ten minutes and
a grocery delivery with booked slots are not the same problem. A hard-coded
weight would be this codebase deciding on their behalf, which is precisely what
T-57's "for operations to choose a point" rules out.

Placement: **Python**, per criterion 2. It compares plans and composes the
trigger engine; it changes whenever either does.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from vrp.committed import moved_since
from vrp.model import Problem, Solution


@dataclass(frozen=True)
class Churn:
    """§8.3's two disruptions, kept apart."""

    moved: int
    eta_shift: int


@dataclass(frozen=True)
class Point:
    """One choice on the trade-off curve."""

    weight: int
    churn: int
    eta_shift: int
    cost: int


def churn(previous: Solution, candidate: Solution) -> Churn:
    """How much the plan moved. §8.3.

    `moved` counts stops that changed vehicle, including ones that stopped
    being served at all -- being dropped is the largest disruption there is.
    `eta_shift` is the total absolute change in start-of-service across the
    stops that *kept* their vehicle, because a stop that moved is already
    counted once and charging it twice would make reassignment look worse than
    it is for reasons nobody could trace.
    """
    changed = moved_since(previous, candidate)
    was = _arrivals(previous)
    now = _arrivals(candidate)
    drift = sum(abs(now[order_id] - when) for order_id, when in was.items()
                if order_id in now and order_id not in changed)
    return Churn(moved=len(changed), eta_shift=drift)


def _arrivals(plan: Solution) -> dict[str, int]:
    return {step.order_id: step.start_service
            for route in plan.routes for step in route.steps
            if step.order_id is not None}


def churn_cost(previous: Solution, candidate: Solution, per_move: int,
               per_second: int) -> int:
    """What this much churn costs, at the caller's prices. §8.3's "optionally"."""
    measured = churn(previous, candidate)
    return measured.moved * per_move + measured.eta_shift * per_second


def tradeoff(problem: Problem, previous: Solution, trigger, now: int,
             weights: Sequence[int], neighbours: int = 1) -> list[Point]:
    """The churn/cost curve. T-57's definition of done.

    Args:
        problem: the instance.
        previous: the plan in force.
        trigger: the disruption to re-optimise around.
        now: the instant; committed work stays put regardless of weight.
        weights: churn penalties to try, in any order.
        neighbours: how many nearby routes to open, as for `reoptimise`.

    Returns:
        One point per weight, ordered by weight so the result reads as a curve
        rather than a set of unrelated runs.

    Raises:
        ValueError: if no weights are given. A curve through no points is not a
            choice, and returning an empty list would look like one.
    """
    from vrp.triggers import reoptimise

    if not weights:
        raise ValueError("a trade-off curve needs at least one weight")

    points = []
    for weight in sorted(set(weights)):
        response = reoptimise(problem, previous, trigger, now,
                              neighbours=neighbours, churn_weight=weight)
        measured = churn(previous, response.plan)
        points.append(Point(weight=weight, churn=measured.moved,
                            eta_shift=measured.eta_shift,
                            cost=response.delta.cost_after))
    return points
