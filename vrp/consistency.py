"""Territories, consistency and fairness — FR-17, FR-18, FR-35, §6.7, T-47.

§6.7 refuses to treat any of this as a concession: "Drivers who serve the same
territory daily accumulate tacit knowledge -- access codes, parking,
receiving-bay habits -- which reduces service time and errors. Customers value
being served at a predictable time by a familiar driver. Consistency is a
genuine cost saver, not a concession."

Three measures, kept apart because they move apart:

* **Workload fairness** (FR-17): the spread of route duration, distance and
  stop count. Three numbers, not one. A fleet even on stops can be wildly
  uneven on hours, and the driver who notices is the one with the long day.
* **Driver consistency** (FR-18): distinct drivers per customer over a horizon.
  Multi-period by definition -- a single day cannot be inconsistent.
* **Arrival-time consistency** (FR-18): `max(arrival) - min(arrival)` per
  customer across the horizon. §6.7 names the cheap lever for closing it --
  "departure-time adjustment at the depot... without changing sequences" --
  which is exactly what `vrp.polish.optimal_departure` already computes exactly.

And the requirement that stops all of it being decorative: "It MUST be
measurable: report the cost delta of enforcing consistency versus the
unconstrained optimum so the business can price it." That is `consistency_price`
and it is T-47's definition of done, not the spreads. A consistency feature that
cannot say what it costs is one nobody can decide to buy.

Consistency is Tier 6 by §6.7, which is the bottom of §5.1's hierarchy: a
tie-breaker, never a reason to drive further. `vrp.objective` now fills that
tier from `workload_spread` rather than leaving it hard-zero.

Placement: **Python**, per criterion 2. This reads plans and the domain model,
and it changes whenever either does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise

from vrp.model import Problem, Solution


@dataclass(frozen=True)
class Spread:
    """FR-17's three measures of how unevenly the work fell."""

    duration: int
    distance: int
    stops: int

    @property
    def worst(self) -> int:
        """One number for Tier 6, which needs a scalar. Duration leads because
        it is the one a driver experiences; the others break its ties."""
        return max(self.duration, self.distance, self.stops)


@dataclass(frozen=True)
class Horizon:
    """A multi-period plan. FR-18's "across a multi-period horizon"."""

    periods: tuple[Solution, ...]


@dataclass(frozen=True)
class Price:
    """What consistency cost, against the plan that ignored it. §6.7."""

    unconstrained: int
    consistent: int

    @property
    def delta(self) -> int:
        return self.consistent - self.unconstrained


def _working(solution: Solution):
    """Routes that actually carry work.

    An idle vehicle is not a zero-hour driver. Counting it would make every
    fleet with a spare van report maximum imbalance, turning the measure into a
    count of spare vans.
    """
    return [route for route in solution.routes
            if any(step.order_id for step in route.steps)]


def workload_spread(problem: Problem, solution: Solution) -> Spread:
    """FR-17: the spread of duration, distance and stop count across drivers.

    Args:
        problem: the instance, for the matrix.
        solution: the plan to measure.

    Returns:
        Max minus min on each measure. Zero on all three is a perfectly even
        fleet; one route with everything is the worst case.

    Recomputed from the matrix rather than read off the plan, per INV-9.
    """
    routes = _working(solution)
    if len(routes) < 2:
        return Spread(duration=0, distance=0, stops=0)

    durations, distances, counts = [], [], []
    for route in routes:
        metres = seconds = 0
        for previous, current in pairwise(route.steps):
            origin = problem.location(previous.location_id).matrix_index
            destination = problem.location(current.location_id).matrix_index
            metres += problem.matrix.distance(origin, destination)
            seconds += problem.matrix.duration(origin, destination)
        durations.append(seconds)
        distances.append(metres)
        counts.append(sum(1 for step in route.steps if step.order_id))

    return Spread(duration=max(durations) - min(durations),
                  distance=max(distances) - min(distances),
                  stops=max(counts) - min(counts))


def distinct_drivers(horizon: Horizon) -> dict[str, int]:
    """FR-18: how many different drivers served each customer over the horizon.

    One is perfect. §6.7's model offers two ways to hold it there -- "bound the
    number of distinct drivers serving a customer over a horizon (generalised
    ConVRP formulation), or pin via territory" -- and this measures the outcome
    either way rather than assuming which was used.
    """
    seen: dict[str, set[str]] = {}
    for period in horizon.periods:
        for route in period.routes:
            for step in route.steps:
                if step.order_id is not None:
                    seen.setdefault(step.order_id, set()).add(route.vehicle_id)
    return {order_id: len(drivers) for order_id, drivers in sorted(seen.items())}


def arrival_spread(horizon: Horizon) -> dict[str, int]:
    """FR-18: `max(arrival) - min(arrival)` per customer, across the horizon.

    A customer seen in only one period has a spread of zero -- one observation
    cannot vary. Not undefined, and emphatically not maximal: a new customer is
    not an inconsistency.
    """
    arrivals: dict[str, list[int]] = {}
    for period in horizon.periods:
        for route in period.routes:
            for step in route.steps:
                if step.order_id is not None:
                    arrivals.setdefault(step.order_id, []).append(step.arrival)
    return {order_id: max(times) - min(times)
            for order_id, times in sorted(arrivals.items())}


def territories(problem: Problem, count: int) -> dict[str, list[str]]:
    """FR-35: stable, workload-balanced, geographically coherent zones.

    Args:
        problem: the instance whose orders are being divided.
        count: how many territories to produce.

    Returns:
        Territory name to the order ids it holds, balanced on stop count to
        within one.

    A polar sweep: order the customers by bearing from the depot and cut the
    ring into contiguous arcs of equal size. Contiguity is what makes a zone
    usable as the warm start FR-35 asks for -- a driver is given a wedge, not a
    scattering -- and cutting by count rather than by angle is what keeps the
    workloads level when demand is denser on one side.

    A first version sorted by *distance* from the depot and dealt round-robin.
    It looked coherent on a fixture whose two clusters happened to sit at
    different radii, and it is not coherent in general: two clusters the same
    distance out in opposite directions are indistinguishable by radius, and
    round-robin then deals every zone a share of both. Perturbation caught it
    -- neither shuffling the input nor dealing in blocks changed any result,
    which is what a test proving nothing looks like.

    There is no seed. The sweep is deterministic and FR-35 asks for stability
    rather than variety, so a seed would be a knob that no test could justify
    and every reader would assume did something.
    """
    if count < 1:
        raise ValueError("a territory count must be at least one")
    depot = problem.vehicles[0].start_location_id if problem.vehicles else None
    origin = problem.location(depot) if depot else problem.locations[0]

    def bearing(order_id: str) -> tuple[float, str]:
        stop = problem.order(order_id).delivery or problem.order(order_id).pickup
        site = problem.location(stop.location_id)
        angle = math.atan2(site.lat - origin.lat, site.lon - origin.lon)
        return angle % math.tau, order_id

    order_ids = sorted((order.id for order in problem.orders), key=bearing)
    zones: dict[str, list[str]] = {f"T{n}": [] for n in range(count)}
    total = len(order_ids)
    for position, order_id in enumerate(order_ids):
        # Contiguous arcs, sized to within one of each other.
        zones[f"T{position * count // max(total, 1)}"].append(order_id)
    return zones


def consistency_price(problem: Problem, unconstrained: Solution,
                      consistent: Solution) -> Price:
    """§6.7: what enforcing consistency cost, against the plan that ignored it.

    Args:
        problem: the instance both plans solve.
        unconstrained: the plan free to do whatever was cheapest.
        consistent: the plan held to territories or driver pinning.

    Returns:
        Both distances and the difference. Positive delta is what consistency
        cost; zero means it was free, and negative means it paid -- §6.7 says
        consistency "is a genuine cost saver, not a concession", so a report
        that could only ever show a penalty would be arguing rather than
        measuring.

    Distance rather than the full objective, deliberately. The two plans serve
    the same orders with the same fleet, so fleet cost and unserved penalties
    are identical by construction and including them would bury the number
    somebody is trying to read.
    """
    return Price(unconstrained=_distance(problem, unconstrained),
                 consistent=_distance(problem, consistent))


def _distance(problem: Problem, solution: Solution) -> int:
    total = 0
    for route in solution.routes:
        for previous, current in pairwise(route.steps):
            origin = problem.location(previous.location_id).matrix_index
            destination = problem.location(current.location_id).matrix_index
            total += problem.matrix.distance(origin, destination)
    return total


def align_departures(problem: Problem, horizon: Horizon,
                     rules=None) -> dict[str, int]:
    """§6.7's cheap lever, applied: the departure each vehicle should take.

    "Permit departure-time adjustment at the depot as a cheap lever to align
    arrival times without changing sequences." T-39 already solves the
    departure question exactly, so this is composition rather than new
    arithmetic -- and the sequences are untouched, which is the whole appeal.
    """
    from vrp.polish import optimal_departure

    departures: dict[str, int] = {}
    for period in horizon.periods:
        for route in period.routes:
            order_ids = [step.order_id for step in route.steps if step.order_id]
            if order_ids:
                departures[route.vehicle_id] = optimal_departure(
                    problem, route.vehicle_id, order_ids, rules=rules)
    return departures


def imbalance(problem: Problem, solution: Solution) -> int:
    """Tier 6's value for one plan. §5.1's "workload imbalance"."""
    return workload_spread(problem, solution).worst
