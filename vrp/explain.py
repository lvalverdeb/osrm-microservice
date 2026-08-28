"""The explanation service — CON-5, FR-36, §9.4, T-60.

CON-5 is unusually direct about why this exists: "Every route plan MUST be able
to answer, per order: why was I assigned to this vehicle, in this position, at
this time? Every rejection MUST answer: which constraint made me infeasible, and
what would have to change? Dispatchers reject plans they cannot explain, and
unexplainable plans are silently overridden -- which destroys the benefit."

The bar is therefore not "an explanation exists". "Time window problem" is an
explanation and it is useless. §9.4's own example sets the standard -- "Earliest
arrival 14:12 from nearest eligible vehicle V-11; window closes 13:30" --
because it names the vehicle to look at and the two numbers to compare.

`would_fit_if` is the harder half. A reason code says what went wrong; this says
what to do, as a concrete edit to the instance: widen *this* window to *this*
instant, raise *this* capacity to *this* figure. A dispatcher handed a diagnosis
and no prescription still has all the work in front of them.

**Nothing here re-derives a reason.** T-14's `preflight` owns the reason codes
and owns the rule that they come from an explicit pass rather than a guess. This
turns each code into an actionable edit and stops where pre-flight stops: an
order the solver dropped for a reason pre-flight cannot see gets no confident
suggestion, because E-14's argument -- that a wrong reason delivered with
confidence costs an afternoon -- applies just as much to a wrong fix.

Placement: **Python**, per criterion 2. It reads the constraint model and the
diagnosis; it changes whenever either does.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import pairwise

from vrp.diagnose import preflight
from vrp.model import Order, Problem, Solution


@dataclass(frozen=True)
class Change:
    """One concrete edit that would make an order fit. §9.4's `would_fit_if`."""

    change: str
    to: int | str


@dataclass(frozen=True)
class Rationale:
    """CON-5's first question, answered."""

    order_id: str
    vehicle_id: str
    position: int
    arrival: int
    marginal_cost: int
    because: tuple[str, ...]


@dataclass(frozen=True)
class Rejection:
    """CON-5's second question, answered."""

    order_id: str
    reason_code: str
    explanation: str
    would_fit_if: tuple[Change, ...]


@dataclass(frozen=True)
class Explanation:
    """Every order, one way or the other."""

    assigned: dict[str, Rationale]
    rejected: dict[str, Rejection]


def explain_assignment(problem: Problem, solution: Solution,
                       order_id: str) -> Rationale | None:
    """Why this order is on this vehicle, in this position, at this time.

    Returns None when the order is not in the plan -- there is no rationale for
    an assignment that did not happen, and inventing one would be the exact
    failure CON-5 is about.
    """
    for route in solution.routes:
        carried = [step.order_id for step in route.steps if step.order_id]
        if order_id not in carried:
            continue
        position = carried.index(order_id) + 1
        step = next(s for s in route.steps if s.order_id == order_id)
        detour = _detour(problem, route.vehicle_id, carried, order_id)
        return Rationale(
            order_id=order_id, vehicle_id=route.vehicle_id, position=position,
            arrival=step.arrival, marginal_cost=detour,
            because=(
                (f"{route.vehicle_id} was the vehicle carrying it, stop "
                 f"{position} of {len(carried)}"),
                (f"arrival {step.arrival}s, service starts "
                 f"{step.start_service}s"),
                f"serving it here costs {detour} more than skipping it",
            ))
    return None


def _detour(problem: Problem, vehicle_id: str, carried: list[str],
            order_id: str) -> int:
    """FR-36's marginal cost, per order: what the route would save by not going.

    The route as planned against the same route with this stop removed, which
    is a number a dispatcher can check by looking at a map. Not a re-solve --
    that is the vehicle-level question and T-44 owns it.
    """
    without = [other for other in carried if other != order_id]
    return _length(problem, vehicle_id, carried) - _length(problem, vehicle_id,
                                                           without)


def _length(problem: Problem, vehicle_id: str, order_ids: list[str]) -> int:
    vehicle = problem.vehicle(vehicle_id)
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    nodes = [index[vehicle.start_location_id]]
    for order_id in order_ids:
        order = problem.order(order_id)
        stop = order.delivery or order.pickup
        nodes.append(index[stop.location_id])
    nodes.append(index[vehicle.end_location_id or vehicle.start_location_id])
    return sum(problem.matrix.distance(a, b) for a, b in pairwise(nodes))


def would_fit_if(problem: Problem, order_id: str) -> tuple[Change, ...]:
    """What would have to change for this order to be servable. CON-5.

    Args:
        problem: the instance.
        order_id: the order in question.

    Returns:
        Concrete edits, or `()` when the order already fits -- or when the
        reason needs a solve. §6.5's `UNIMPLEMENTED` codes are exactly the
        cases pre-flight cannot see, and offering a fix for one would be
        guessing with a straight face.

    Raises:
        KeyError: if the order is not in the instance.
    """
    order = problem.order(order_id)
    finding = preflight(problem).get(order_id)
    if finding is None:
        return ()
    return _remedy(problem, order, finding.code)


def _remedy(problem: Problem, order: Order, code: str) -> tuple[Change, ...]:
    """One reason code, turned into an edit."""
    stop = order.delivery or order.pickup
    match code:
        case "TIME_WINDOW_UNREACHABLE":
            arrival = _earliest_arrival(problem, order)
            return () if arrival is None else (
                Change("window_end", arrival),)
        case "CAPACITY_EXCEEDED":
            return tuple(Change(f"vehicle_capacity_{dimension}", amount)
                         for dimension, amount in sorted(order.quantities.items()))
        case "NO_ELIGIBLE_VEHICLE":
            if order.required_skills:
                return tuple(Change("vehicle_skill", skill)
                             for skill in sorted(order.required_skills))
            site = problem.location(stop.location_id)
            if site.access_classes:
                return tuple(Change("vehicle_access_class", access)
                             for access in sorted(site.access_classes))
            if site.max_vehicle_kg is not None:
                return (Change("vehicle_gross_weight_kg", site.max_vehicle_kg),)
            return ()
        case "DEPOT_STOCKOUT":
            return tuple(Change(f"depot_inventory_{dimension}", amount)
                         for dimension, amount in sorted(order.quantities.items()))
        case "RELEASE_AFTER_WINDOW":
            hard = [w for w in stop.time_windows if w.hardness == "HARD"]
            return (Change("release_time", min(w.start for w in hard)),) if hard \
                else ()
        case "DUTY_LIMIT":
            return (Change("vehicle_shift_end", _round_trip(problem, order)),)
        case _:
            # FLEET_EXHAUSTED, DROPPED_BY_PRIZE, INCOMPATIBLE_ONLY and
            # LOCK_CONFLICT either need a solve or need a human decision about
            # intent. Saying nothing is the honest answer.
            return ()


def _earliest_arrival(problem: Problem, order: Order) -> int | None:
    """When the quickest eligible vehicle could get there, straight out."""
    stop = order.delivery or order.pickup
    destination = problem.location(stop.location_id).matrix_index
    arrivals = []
    for vehicle in problem.vehicles:
        start = problem.location(vehicle.start_location_id).matrix_index
        if not problem.matrix.is_reachable(start, destination):
            continue
        arrivals.append(max(vehicle.shift.start, order.release_time)
                        + problem.matrix.duration(start, destination))
    return min(arrivals) if arrivals else None


def _round_trip(problem: Problem, order: Order) -> int:
    stop = order.delivery or order.pickup
    destination = problem.location(stop.location_id).matrix_index
    trips = []
    for vehicle in problem.vehicles:
        start = problem.location(vehicle.start_location_id).matrix_index
        trips.append(vehicle.shift.start
                     + 2 * problem.matrix.duration(start, destination)
                     + stop.service_fixed)
    return min(trips) if trips else 0


def explain(problem: Problem, solution: Solution) -> Explanation:
    """Every order in the plan, and every one that is not. CON-5.

    An order in neither list is one nobody can ask about, so the two together
    cover the instance by construction.
    """
    assigned, rejected = {}, {}
    for order in problem.orders:
        rationale = explain_assignment(problem, solution, order.id)
        if rationale is not None:
            assigned[order.id] = rationale
            continue
        finding = preflight(problem).get(order.id)
        code = finding.code if finding else "FLEET_EXHAUSTED"
        detail = finding.detail if finding else (
            "feasible on its own, but no capacity remained once the rest of "
            "the day was planned; §6.5 needs a solve to say more")
        rejected[order.id] = Rejection(
            order_id=order.id, reason_code=code,
            explanation=f"{code}: {detail}",
            would_fit_if=_remedy(problem, order, code))
    return Explanation(assigned=assigned, rejected=rejected)


def without(problem: Problem, order_id: str) -> Problem:
    """The instance without one order, for asking what it was costing."""
    return replace(problem, orders=tuple(order for order in problem.orders
                                         if order.id != order_id))
