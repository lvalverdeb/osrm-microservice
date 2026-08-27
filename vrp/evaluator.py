"""Canonical evaluator — SDD §5, T-03.

Recomputes a route's timeline and a solution's objective from first principles:
the order sequence, the pinned matrix, and nothing else. No incremental state,
no caching, no cleverness — this is the ground truth a solver's move evaluator
is checked against, so it is written to be obviously right rather than fast.

The SDD calls drift between an incremental evaluator and this one the source of
most silent optimisation bugs (INV-9), which only holds if this side is
independently derivable. Every number below comes from the matrix and the
service times, never from a solver's own accounting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise

from vrp.model import Order, Problem, Step, service_time


@dataclass(frozen=True)
class ObjectiveWeights:
    """Cost per unit. Integers, so totals stay exact. §5.3.

    This is a flat weighted sum, which is the shape §5.1 names "the most common
    modelling error in production routing". It stays flat on purpose: this
    module's job is to *account* for a plan's cost, and a single comparable
    number is what an accountant produces. Deciding which of two plans is better
    belongs to `vrp.objective`, whose tiers cannot silently invert.

    `unassigned_penalty` is the part to watch. A flat constant only outranks
    driving while it is larger than the detour dropping an order would save, and
    1,000,000 is metres -- 1,000 km. Measured headroom: the worst single-order
    round trip anywhere in the benchmark corpus is 66 km, about 15x under it,
    and Costa Rica end to end by road is roughly 500 km round trip, about 2x
    under it. So it holds at this project's scale, and 2x is not much margin.
    Anything wider-ranging should order plans through `vrp.objective.compare`,
    which derives its scaling from the instance and so cannot run out of
    headroom in the first place.
    """

    per_metre: int = 1
    per_second: int = 0
    per_vehicle: int = 0
    unassigned_penalty: int = 1_000_000


@dataclass
class Evaluation:
    total: int = 0
    breakdown: dict[str, int] = field(default_factory=dict)
    timelines: dict[str, tuple[Step, ...]] = field(default_factory=dict)


def _stop_of(order: Order) -> tuple[str, int, tuple]:
    """Where an order is served, how long it takes, and its windows.

    Only the single-stop (JOB) case is modelled here. A SHIPMENT occupies two
    positions in one sequence, which this builder's one-stop-per-order shape
    cannot express; refusing it loudly is better than quietly evaluating half
    of it. The solver and the verifier both handle shipments (FR-01, INV-2) --
    it is this convenience builder that does not, and the plans it builds are
    fixtures rather than solver output.

    The earlier note here pointed at "E-13", which is the lexicographic
    objective and has nothing to do with shipments. FR-01 belongs to `T-12`.
    """
    if order.kind == "SHIPMENT":
        raise NotImplementedError(
            "SHIPMENT evaluation needs paired pickup/delivery positions (E-13)")
    stop = order.delivery or order.pickup
    assert stop is not None  # guaranteed by Order validation
    # FR-05's other three components need the vehicle and the location,
    # which this helper does not see. `build_timeline` composes them via
    # `service_time`; this returns the fixed part for callers that only
    # want to know where and when.
    return stop.location_id, stop.service_fixed, stop.time_windows


def _service_start(arrival: int, windows: tuple) -> int:
    """When service may begin: on arrival, or when a window opens.

    Windows are validated sorted and disjoint, so the first one that has not
    already closed is the one that applies. A vehicle arriving after every
    window starts immediately and the lateness is left for the verifier to
    report — the evaluator's job is to say what happened, not to hide it.
    """
    if not windows:
        return arrival
    for window in windows:
        if arrival <= window.end:
            return max(arrival, window.start)
    return arrival


def build_timeline(problem: Problem, vehicle_id: str, order_ids: list[str],
                   start_time: int | None = None) -> tuple[Step, ...]:
    """Expand a vehicle's order sequence into a full timeline.

    Returns START, one step per order, then END. Load is computed backwards for
    delivery-only work — the vehicle leaves the depot carrying everything it
    will drop — which is what makes `load_after` meaningful at the first step.
    """
    vehicle = problem.vehicle(vehicle_id)
    orders = [problem.order(order_id) for order_id in order_ids]
    matrix = problem.matrix

    dimensions = {dimension for order in orders for dimension in order.quantities}
    # Delivery-only routes start fully laden; pickups add along the way.
    on_board = {
        dimension: sum(order.quantities.get(dimension, 0)
                       for order in orders if order.delivery is not None)
        for dimension in dimensions
    }

    clock = vehicle.shift.start if start_time is None else start_time
    start_location = problem.location(vehicle.start_location_id)
    steps: list[Step] = [Step(type="START", location_id=start_location.id,
                              arrival=clock, start_service=clock, departure=clock,
                              load_after=dict(on_board))]

    position = start_location.matrix_index
    for order in orders:
        location_id, _fixed, windows = _stop_of(order)
        location = problem.location(location_id)
        # FR-05: fixed + per-unit + vehicle factor + dwell. Reading
        # `service_fixed` alone here -- which is what this did before E-24 --
        # made the other three components decorative: a richer model that
        # produced identical plans.
        service = service_time(order, vehicle, location)
        arrival = clock + matrix.duration(position, location.matrix_index)
        begin = _service_start(arrival, windows)
        # `service_time` already includes the dwell overhead (FR-05), so
        # adding it again here charged it twice -- 255 s where the model
        # says 195. Caught by test_the_timeline_uses_the_composed_service_time.
        depart = begin + service

        if order.delivery is not None:
            for dimension, amount in order.quantities.items():
                on_board[dimension] = on_board.get(dimension, 0) - amount
        else:
            for dimension, amount in order.quantities.items():
                on_board[dimension] = on_board.get(dimension, 0) + amount

        steps.append(Step(
            type="DELIVERY" if order.delivery is not None else "PICKUP",
            location_id=location.id, order_id=order.id,
            arrival=arrival, start_service=begin, departure=depart,
            load_after=dict(on_board),
        ))
        clock, position = depart, location.matrix_index

    end_location = problem.location(vehicle.ends_at)
    arrival = clock + matrix.duration(position, end_location.matrix_index)
    steps.append(Step(type="END", location_id=end_location.id, arrival=arrival,
                      start_service=arrival, departure=arrival,
                      load_after=dict(on_board)))
    return tuple(steps)


def route_metrics(problem: Problem, timeline: tuple[Step, ...]) -> dict[str, int]:
    """Distance, driving, waiting and service for one expanded timeline."""
    matrix = problem.matrix
    metrics = {"distance": 0, "driving_seconds": 0,
               "waiting_seconds": 0, "service_seconds": 0,
               "earliness_penalty": 0, "lateness_penalty": 0}
    for previous, current in pairwise(timeline):
        origin = problem.location(previous.location_id).matrix_index
        destination = problem.location(current.location_id).matrix_index
        metrics["distance"] += matrix.distance(origin, destination)
        metrics["driving_seconds"] += matrix.duration(origin, destination)
        metrics["waiting_seconds"] += current.waiting
        metrics["service_seconds"] += current.departure - current.start_service
        early, late = soft_penalties(problem, current)
        metrics["earliness_penalty"] += early
        metrics["lateness_penalty"] += late
    return metrics


def soft_penalties(problem: Problem, step: Step) -> tuple[int, int]:
    """Earliness and lateness cost for one step's soft windows. §6.2, FR-04.

    Only SOFT windows are costed. A breached HARD window is a violation the
    verifier reports (INV-3), not a price the evaluator quietly absorbs --
    costing it here as well would let an illegal plan look merely expensive,
    and would double-count it against a verifier that already rejected it.

    Earliness is measured from arrival rather than from service start, because
    arriving early and waiting is exactly what §6.2 says must be costed: "
    uncosted waiting produces plans that look cheap and consume the whole
    driver day".
    """
    if step.order_id is None:
        return 0, 0
    order = problem.order(step.order_id)
    stop = order.delivery or order.pickup
    early = late = 0
    for window in stop.time_windows:
        if window.hardness != "SOFT":
            continue
        if step.arrival < window.start:
            early += (window.start - step.arrival) * window.earliness_cost_per_sec
        if step.start_service > window.end:
            late += (step.start_service - window.end) * window.lateness_cost_per_sec
    return early, late


def _fleet_prices_itself(problem: Problem) -> bool:
    """Whether any vehicle states a cost of its own. FR-07, T-44.

    The fallback is fleet-wide, matching `ObjectiveSpec.rates`, and for the same
    two reasons: a fleet that prices nothing keeps the flat weights it has
    always been accounted with -- the frozen corpus among them, and a benchmark
    whose numbers move because the accounting changed is not a benchmark -- and
    a per-vehicle fallback would make an unstated zero indistinguishable from a
    default price, which is precisely the own-versus-hire distinction FR-33
    needs to be able to draw.
    """
    return any(v.fixed_cost or v.cost_per_metre or v.cost_per_second
               or v.cost_per_order for v in problem.vehicles)


def _vehicle_cost(problem: Problem, vehicle_id: str,
                  weights: ObjectiveWeights, priced: bool) -> int:
    """What deploying this vehicle costs, before it drives anywhere."""
    if not priced:
        return weights.per_vehicle
    return problem.vehicle(vehicle_id).fixed_cost


def _operating(problem: Problem, vehicle_id: str, order_ids: list[str],
               metrics: dict[str, int], weights: ObjectiveWeights,
               priced: bool) -> int:
    """One route's distance, time and per-job cost, at its own vehicle's rates."""
    seconds = (metrics["driving_seconds"] + metrics["waiting_seconds"]
               + metrics["service_seconds"])
    if not priced:
        return metrics["distance"] * weights.per_metre + seconds * weights.per_second
    vehicle = problem.vehicle(vehicle_id)
    return (metrics["distance"] * vehicle.cost_per_metre
            + seconds * vehicle.cost_per_second
            + len(order_ids) * vehicle.cost_per_order)


def evaluate(problem: Problem, assignment: dict[str, list[str]],
             weights: ObjectiveWeights | None = None,
             start_times: dict[str, int] | None = None) -> Evaluation:
    """Score a complete assignment of orders to vehicles.

    `assignment` maps vehicle id to its order sequence. Any order absent from
    every sequence is unassigned and charged its prize, or the flat penalty
    when it carries none — otherwise dropping a prizeless order would be free
    and the objective would prefer serving nothing.
    """
    weights = weights or ObjectiveWeights()
    totals = {"distance": 0, "driving_seconds": 0,
              "waiting_seconds": 0, "service_seconds": 0,
              "earliness_penalty": 0, "lateness_penalty": 0}
    timelines: dict[str, tuple[Step, ...]] = {}
    deployed = 0

    priced = _fleet_prices_itself(problem)
    operating = 0
    for vehicle_id, order_ids in assignment.items():
        if not order_ids:
            continue
        deployed += _vehicle_cost(problem, vehicle_id, weights, priced)
        start = (start_times or {}).get(vehicle_id)
        timeline = build_timeline(problem, vehicle_id, order_ids, start_time=start)
        timelines[vehicle_id] = timeline
        metrics = route_metrics(problem, timeline)
        for key, value in metrics.items():
            totals[key] += value
        operating += _operating(problem, vehicle_id, order_ids, metrics,
                                weights, priced)

    served = {order_id for ids in assignment.values() for order_id in ids}
    unassigned = [order for order in problem.orders if order.id not in served]
    penalty = sum(order.prize or weights.unassigned_penalty for order in unassigned)

    breakdown = dict(totals)
    breakdown["vehicles"] = deployed
    breakdown["unassigned_penalty"] = penalty

    total = (operating
             + breakdown["vehicles"]
             + totals["earliness_penalty"] + totals["lateness_penalty"]
             + penalty)
    return Evaluation(total=total, breakdown=breakdown, timelines=timelines)


def route_is_legal(problem: Problem, vehicle_id: str,
                   sequence: list[str]) -> bool:
    """Could this vehicle actually drive this sequence? Capacity, windows, law.

    The companion to `build_timeline`: one builds the day, this says whether it
    is a day anyone could have. It is what a *constructor* asks before
    attempting a move -- the generator before proposing a route, the
    decomposer before moving an order across a cluster border.

    It is emphatically not the verifier. `vrp.verify` shares no code with
    anything that builds a plan, which is the whole of CON-1; this shares the
    timeline builder with the plan it is judging, and so can only be trusted to
    say what the builder would produce. That is exactly enough for deciding
    what to attempt, and not nearly enough for deciding what is true.

    Capacity is included because omitting it was a real defect: this predicate
    began life inside the generator checking only windows and hours, and the
    first caller outside it -- §7.6's cross-boundary repair -- promptly moved
    orders onto vehicles that could not carry them. INV-5 caught it on a
    1,000-stop instance.

    Args:
        problem: the instance.
        vehicle_id: the vehicle that would drive it.
        sequence: the order ids, in the order they would be served.

    Returns:
        Whether every stop falls in a hard window, the duty ends inside the
        shift, no load exceeds capacity, and any declared hours-of-service rule
        set is satisfied.
    """
    vehicle = problem.vehicle(vehicle_id)
    steps = build_timeline(problem, vehicle_id, sequence)

    for step in steps:
        for dimension, amount in (step.load_after or {}).items():
            if amount > vehicle.capacities.get(dimension, 0):
                return False
        if step.order_id is None:
            continue
        stop = (problem.order(step.order_id).delivery
                or problem.order(step.order_id).pickup)
        hard = [w for w in stop.time_windows if w.hardness == "HARD"]
        if hard and not any(window.contains(step.start_service) for window in hard):
            return False

    if steps and steps[-1].arrival > vehicle.shift.end:
        return False

    if vehicle.hos_rules:
        from vrp.hos.rules import rules_for
        from vrp.hos.schedule import schedule_route
        return schedule_route(problem, vehicle_id, sequence,
                              rules_for(vehicle.hos_rules)).legal
    return True
