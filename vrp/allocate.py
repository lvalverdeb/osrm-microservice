"""Operational allocation reporting — FR-30, FR-33, FR-36, §7.8, T-44.

FR-36: "For every deployed vehicle, report utilisation on each capacity
dimension, duty time used vs available, and the marginal cost of removing it."

Allocation itself is not a separate solve. §7.8 makes deployment endogenous --
each vehicle carries its own fixed cost, charged only when used, so the search
decides the fleet while it routes. `vrp.objective` does that pricing. What is
left, and what this module is, is the part a dispatcher reads: which vehicles
went out, how full they were, how much of the day they used, and what each one
was worth.

**Everything here is recomputed, never read off the plan.** INV-9 says not to
trust a solver's own accounting, and an allocation block is exactly the sort of
output nobody checks -- it is prose, it looks authoritative, and it is where a
plausible-but-wrong utilisation figure would live for years. So loads come from
`build_timeline` rather than from the plan's `load_after`, and duty from the
recomputed timeline rather than from its arrival stamps.

**Marginal value takes its re-solver as an argument.** §7.8 defines it as "the
objective delta from re-solving with that vehicle removed (approximated by a
short warm-started re-solve, exact value not required)", and a caller holding a
warm start and an iteration budget knows better than this module how to spend
them. It also keeps a reporting module free of a solver dependency, which is
the same argument §11.2 makes about anything that judges a plan.

Placement: **Python**, per criterion 2. This reads the domain model and the
objective, and it changes whenever either does.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from itertools import pairwise

from vrp.evaluator import build_timeline, route_is_legal
from vrp.model import Problem, Solution
from vrp.objective import ObjectiveSpec, Tier, score

# Utilisation in parts per thousand, for CON-4's reason: a percentage of a
# 3-unit load in a 7-unit van is not representable in integers and this project
# does not accumulate floats.
FULL = 1000


@dataclass(frozen=True)
class VehicleAllocation:
    """What one vehicle did, and what it was worth. FR-36."""

    vehicle_id: str
    deployed: bool
    orders: int
    utilisation: dict[str, int]
    duty_used: int
    duty_available: int
    fixed_cost: int
    operating_cost: int

    @property
    def duty_utilisation(self) -> int:
        """Parts per thousand of the shift consumed."""
        if self.duty_available <= 0:
            return 0
        return self.duty_used * FULL // self.duty_available


class AllocationReport(dict):
    """Every vehicle in the fleet, keyed by id, deployed or not.

    Idle vehicles are included deliberately. FR-36 asks about deployed ones, but
    a vehicle that was never worth deploying is the answer to "can I sell it",
    and T-46's fleet-sizing sweep is built on exactly that question. Reporting
    only the busy half would make the sweep blind to its own subject.
    """

    def __iter__(self):
        return iter(self.values())


def allocate(problem: Problem, solution: Solution,
             spec: ObjectiveSpec) -> AllocationReport:
    """The allocation block. FR-36.

    Args:
        problem: the instance.
        solution: the plan to describe.
        spec: the objective, which owns each vehicle's rates.

    Returns:
        One `VehicleAllocation` per vehicle in the fleet, keyed by vehicle id.
        Iterating the report yields the entries themselves.
    """
    carried = {route.vehicle_id: [step.order_id for step in route.steps
                                  if step.order_id]
               for route in solution.routes}
    return AllocationReport(
        (vehicle.id, _entry(problem, spec, vehicle.id,
                            carried.get(vehicle.id, [])))
        for vehicle in problem.vehicles)


def _entry(problem: Problem, spec: ObjectiveSpec, vehicle_id: str,
           order_ids: list[str]) -> VehicleAllocation:
    vehicle = problem.vehicle(vehicle_id)
    available = vehicle.shift.end - vehicle.shift.start
    if not order_ids:
        # §7.8: "Empty routes are free and removable at zero cost."
        return VehicleAllocation(
            vehicle_id=vehicle_id, deployed=False, orders=0,
            utilisation=dict.fromkeys(vehicle.capacities, 0),
            duty_used=0, duty_available=available,
            fixed_cost=0, operating_cost=0)

    timeline = build_timeline(problem, vehicle_id, order_ids)
    rates = spec.rates(problem, vehicle_id)
    distance, duration = _travelled(problem, timeline)
    return VehicleAllocation(
        vehicle_id=vehicle_id, deployed=True, orders=len(order_ids),
        utilisation=_utilisation(problem, vehicle_id, timeline),
        duty_used=timeline[-1].arrival - timeline[0].departure,
        duty_available=available,
        fixed_cost=rates.fixed,
        operating_cost=(distance * rates.per_metre
                        + duration * rates.per_second
                        + len(order_ids) * rates.per_order))


def _travelled(problem: Problem, timeline) -> tuple[int, int]:
    matrix = problem.matrix
    distance = duration = 0
    for previous, current in pairwise(timeline):
        origin = problem.location(previous.location_id).matrix_index
        destination = problem.location(current.location_id).matrix_index
        distance += matrix.distance(origin, destination)
        duration += matrix.duration(origin, destination)
    return distance, duration


def _utilisation(problem: Problem, vehicle_id: str, timeline) -> dict[str, int]:
    """Peak load as parts per thousand of capacity, on every dimension.

    Peak rather than final: a van that leaves full and returns empty was full.
    Every dimension the vehicle declares, rather than only the ones the load
    touches -- a van full by volume and empty by weight is a different purchase
    decision from one full by both, and a dimension missing from the report
    reads as a dimension with no pressure on it.
    """
    capacities = problem.vehicle(vehicle_id).capacities
    peaks = {dimension: 0 for dimension in capacities}
    for step in timeline:
        for dimension, amount in (step.load_after or {}).items():
            if dimension in peaks:
                peaks[dimension] = max(peaks[dimension], amount)
    return {dimension: (peaks[dimension] * FULL // limit if limit else 0)
            for dimension, limit in capacities.items()}


def marginal_values(problem: Problem, solution: Solution, spec: ObjectiveSpec,
                    resolve: Callable[[Problem], Solution | None],
                    ) -> dict[str, int | None]:
    """Each vehicle's marginal value: the objective delta from removing it.

    Args:
        problem: the instance.
        solution: the incumbent to measure against.
        spec: the objective both plans are scored by.
        resolve: re-solves a problem with one vehicle removed. §7.8 permits a
            short warm-started approximation; returning None means the work
            cannot be done without that vehicle.

    Returns:
        One delta per vehicle, or None where the fleet cannot manage without it.

    Sign follows cost: negative means removing the vehicle *improves* the
    objective, so the vehicle is costing more than it saves. None is not a large
    number and must not be rendered as one -- "load-bearing" and "expensive" are
    opposite answers to a fleet-sizing question, and T-46's sweep would invert
    on the confusion.

    The delta is **money**, not `Score.total`. FR-36's words are "the marginal
    cost of removing it", and the scaled total is scaled precisely so the
    lexicographic ordering cannot invert -- on a four-vehicle instance its
    magnitude runs to 10^17, which orders plans correctly and prices nothing.
    A caller wanting the ordering can score the two plans itself.
    """
    incumbent = _cost_of(problem, solution, spec)
    values: dict[str, int | None] = {}
    for vehicle in problem.vehicles:
        reduced = _without(problem, vehicle.id)
        if reduced is None:
            values[vehicle.id] = None
            continue
        replanned = resolve(reduced)
        # Either instance would do, and perturbation confirmed it: the tier
        # *values* count vehicles that appear in routes and orders that do not,
        # and both are the same either way. It mattered when this returned
        # `Score.total`, whose scales are derived from the instance -- a fleet
        # one vehicle short scaled differently, and subtracting the two totals
        # subtracted numbers in different currencies, giving marginal values
        # near -7e16 on an objective whose range was about 10^6. Money removed
        # the trap rather than avoiding it; `problem` is kept because it is the
        # instance the caller asked about.
        values[vehicle.id] = (None if _abandoned(problem, replanned)
                              else _cost_of(problem, replanned, spec)
                              - incumbent)
    return values


def _cost_of(problem: Problem, solution: Solution, spec: ObjectiveSpec) -> int:
    """A plan's price: the tiers §5.2 compares in one currency."""
    scored = score(problem, solution, spec)
    return (scored.values[Tier.FLEET] + scored.values[Tier.OPERATING]
            + scored.values[Tier.UNSERVED])


def _abandoned(problem: Problem, solution: Solution | None) -> bool:
    """Whether a re-solve failed, left must-serve work behind, or came back
    illegal.

    §4.1 makes priority tier 0 must-serve, so a plan that drops one has not
    answered "what does this vehicle cost" -- it has changed the question.
    Pricing the difference would offer the fleet a saving it is not allowed to
    take, and it would look like the cheapest option on the sheet.

    The third case is the one a caller cannot be trusted to catch. When every
    order is required, an engine short of capacity does not return an
    incomplete plan; it returns a complete, *overloaded* one. Checking
    `unassigned` sees nothing wrong, and a genuinely load-bearing vehicle
    reports a tidy saving -- measured on the E-44 fixture, a fleet that could
    not carry the work at all priced every vehicle as surplus.

    Legality is recomputed from the orders by `route_is_legal` rather than read
    off the plan. INV-9's reason: a hand-built or engine-built plan need not
    carry `load_after` at all, and a capacity check that trusts it passes an
    overloaded route by finding nothing to look at.
    """
    if solution is None:
        return True
    served = {step.order_id for route in solution.routes
              for step in route.steps if step.order_id}
    if any(order.priority_tier == 0 and order.id not in served
           for order in problem.orders):
        return True
    return any(not route_is_legal(problem, route.vehicle_id,
                                  [step.order_id for step in route.steps
                                   if step.order_id])
               for route in solution.routes
               if any(step.order_id for step in route.steps))


def _without(problem: Problem, vehicle_id: str) -> Problem | None:
    """The same instance, one vehicle short. None when it was the last one."""
    remaining = tuple(v for v in problem.vehicles if v.id != vehicle_id)
    if not remaining:
        return None
    return replace(problem, vehicles=remaining,
                   locks=tuple(lock for lock in problem.locks
                               if lock.vehicle_id != vehicle_id))
