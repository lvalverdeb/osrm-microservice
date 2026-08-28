"""The committed-state manager — DYN-4, AC-2.2, §8.3, T-50.

US-2 is the requirement in one sentence: "when a vehicle breaks down at 11:00, I
re-optimise only the affected and nearby work while everything already executed
or committed stays fixed." AC-2.2 names the hard part: "No stop already visited
or currently en route is moved."

DYN-4 says how it is done -- a component that "converts executed and en-route
work into `FIX_ROUTE_PREFIX` / `FREEZE_UNTIL` locks (§6.6)". Both lock kinds
have existed since T-29 and INV-8 has enforced them since. What was missing was
the thing that produces them, so a re-optimisation at 11:00 was free to reorder
the morning and nothing in the system objected.

Two details carry the requirement:

**En route is committed.** A van three minutes from a stop has not visited it,
and moving it means a driver turning around in the street. AC-2.2 names both
states in one breath for exactly that reason, and a manager pinning only
completed work passes every test written against completed work.

**The freeze horizon is not the prefix.** `FIX_ROUTE_PREFIX` pins *what* each
vehicle has done. `FREEZE_UNTIL` stops the optimiser filling the morning around
it with new work. Emit one without the other and a plan can honour every prefix
while scheduling a fresh stop at 09:00.

**Execution beats the plan.** The timeline says when a stop *was scheduled*, and
a van running late has executed less than that. Where telematics is available
the caller passes an `Execution` and it wins; where it is not, the plan's own
clock is the best available estimate and is used as one.

Placement: **Python**, per criterion 2. This reads plans and emits domain locks,
and it changes whenever either does.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from vrp.model import Lock, Problem, Route, Solution


@dataclass(frozen=True)
class Execution:
    """What the fleet has actually done, when anyone knows. §12.4's telematics.

    `completed` is per vehicle, in the order the stops were served. `en_route`
    names the one stop each vehicle is currently driving towards -- committed
    by AC-2.2 despite not being visited.
    """

    completed: dict[str, tuple[str, ...]]
    en_route: dict[str, str | None] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.en_route is None:
            object.__setattr__(self, "en_route", {})


def committed_prefix(problem: Problem, route: Route, now: int,
                     include_en_route: bool = False,
                     execution: Execution | None = None) -> list[str]:
    """The orders on this route that may no longer be moved. AC-2.2.

    Args:
        problem: the instance (unused when `execution` is given; kept so the
            signature does not change when it is not).
        route: the vehicle's current route.
        now: the instant being planned at.
        include_en_route: whether the stop currently being driven to counts.
            AC-2.2 says it does; the default is False so a caller asking
            literally "what has been served" gets that.
        execution: what telematics says actually happened. Wins over the
            plan's clock, because a van running late has executed less than
            its timeline claims and pinning the difference pins work that has
            not happened.

    Returns:
        Order ids in route order, longest-first-prefix of the route.
    """
    if execution is not None:
        done = list(execution.completed.get(route.vehicle_id, ()))
        driving = execution.en_route.get(route.vehicle_id)
        if include_en_route and driving is not None:
            done.append(driving)
        return done

    served: list[str] = []
    left_last_stop = None
    for step in route.steps:
        if step.order_id is None:
            left_last_stop = step.departure
            continue
        if step.start_service <= now:
            # Under service counts as committed: a driver at the door is not
            # rerouted, and `start_service <= now < departure` is exactly that.
            served.append(step.order_id)
            left_last_stop = step.departure
        else:
            # En route means the van has *left* the previous stop, strictly.
            # Without the strictness, planning at t=0 finds the first stop of
            # every route already committed -- the day's opening plan would be
            # partly frozen before anyone had driven anywhere.
            if include_en_route and left_last_stop is not None \
                    and left_last_stop < now:
                served.append(step.order_id)
            break
    return served


def commit_locks(problem: Problem, solution: Solution, now: int) -> tuple[Lock, ...]:
    """The locks that hold the committed state in place. DYN-4.

    Args:
        problem: the instance.
        solution: the plan as it stands.
        now: the instant being planned at; the freeze horizon.

    Returns:
        One `FIX_ROUTE_PREFIX` per vehicle with committed work, plus a single
        `FREEZE_UNTIL` at `now`.

    Raises:
        ValueError: if `now` is negative.

    A vehicle with nothing committed gets no prefix lock. An empty prefix
    constrains nothing while looking like an instruction, which is §6.6's own
    objection to a lock without a subject.
    """
    if now < 0:
        raise ValueError("a freeze horizon must not be negative")

    locks = []
    for route in solution.routes:
        prefix = committed_prefix(problem, route, now, include_en_route=True)
        if prefix:
            locks.append(Lock(kind="FIX_ROUTE_PREFIX",
                              vehicle_id=route.vehicle_id,
                              order_ids=tuple(prefix)))
    locks.append(Lock(kind="FREEZE_UNTIL", instant=now))
    return tuple(locks)


def moved_since(before: Solution, after: Solution) -> dict[str, tuple[str, str | None]]:
    """Which stops changed vehicle between two plans. AC-2.3's churn.

    Returns:
        Order id to (old vehicle, new vehicle). A new vehicle of None means the
        stop is no longer planned at all.

    Resequencing within one vehicle is not a move. AC-2.3 asks for "stops moved
    between vehicles" and lists ETA shifts separately, because they are
    different facts with different consequences: one reassigns a driver, the
    other changes what a customer was told.
    """
    return moved_between(_owners(before), _owners(after))


def _owners(solution: Solution) -> dict[str, str]:
    return {step.order_id: route.vehicle_id
            for route in solution.routes for step in route.steps
            if step.order_id is not None}


def moved_between(was: Mapping[str, str],
                  now: Mapping[str, str]) -> dict[str, tuple[str, str | None]]:
    """The same comparison over bare order-to-vehicle maps.

    Extracted so the replayer can use it on the provisional assignments it
    carries between epochs, which are maps rather than plans. Duplicating four
    lines would have been easier and would have left two definitions of "moved"
    to drift apart.
    """
    return {order_id: (vehicle, now.get(order_id))
            for order_id, vehicle in was.items()
            if now.get(order_id) != vehicle}
