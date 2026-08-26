"""PyVRP adapter — model compiler and solution mapper. SDD §7.3, T-12.

Two directions, deliberately separated:

*Compile* turns a `Problem` into a PyVRP model. Every travel cost comes from the
pinned matrix rather than from coordinates — PyVRP will compute Euclidean
distances from `x`/`y` if you let it, and silently disagreeing with the matrix
the plan is later verified against is exactly the drift INV-4 exists to catch.
Coordinates are passed for display only.

*Map* turns the result back into a `Solution`, carrying **PyVRP's own arrival
and service times** rather than times recomputed here. That is the point: the
independent verifier then checks the solver's arithmetic against the matrix,
instead of checking our evaluator against itself.

Placement: this is Python, not gateway. It is optimisation logic whose value is
the PyVRP ecosystem, it is not on the request path, and constraint semantics
change far more often than transport behaviour. See "Placement" in
docs/planning/VRP_TDD_EXAMPLES.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyvrp import Model
from pyvrp.stop import MaxIterations

from vrp.model import Problem, Route, Solution, Step

# PyVRP addresses capacity dimensions positionally, so the order must be pinned
# and used identically when compiling and when mapping back. Sorted rather than
# insertion-ordered: two Problems describing the same fleet must compile to the
# same model whichever order their dicts were built in, or a cached plan and a
# fresh one disagree about which number is the pallets.

# FR-07 lists a per-vehicle routing profile, and a profile is a matrix. A
# Problem pins exactly one, so a mixed-profile fleet would route a bicycle and
# an artic over identical travel while appearing to differ. PyVRP supports
# per-profile edge sets (`Model.add_profile`), so the gap is in the domain
# model rather than the solver -- it needs a Problem that can carry several
# matrices. Refused loudly until then.
_UNSUPPORTED_MIXED_PROFILES = (
    "a fleet mixing routing profiles needs one matrix per profile, which "
    "Problem does not yet carry; every vehicle must share one profile")


@dataclass(frozen=True)
class _Compiled:
    model: Model
    dimensions: tuple[str, ...]
    # PyVRP client index (0-based, in insertion order) -> our order id.
    order_by_client: dict[int, str]
    # PyVRP numbers shipments in their own space, from zero, overlapping the
    # client indices. Two maps, because one would silently conflate them.
    order_by_shipment: dict[int, str]
    vehicle_ids: list[str]


def shift_start_of(problem: Problem) -> int:
    return min(v.shift.start for v in problem.vehicles)


def shift_end_of(problem: Problem) -> int:
    return max(v.shift.end for v in problem.vehicles)


def _bounds(window, shift_start: int, shift_end: int) -> tuple[int, int]:
    """PyVRP bounds for one window. FR-04's hard/soft distinction lives here.

    PyVRP has no soft time windows -- `PenaltyManager` is its internal search
    mechanism, not a user-facing feature -- so a soft window is widened to the
    shift and its breach costed afterwards by the evaluator. That is a real
    limitation and worth naming: the solver will not *search* for the cheapest
    lateness, it merely stops treating a soft window as a wall.

    Passing a soft window through as a hard bound, which is what this did
    before T-23, made a stop 600 s away with a soft 100 s window come back
    INFEASIBLE -- refusing a plan that any dispatcher would call "late".
    """
    if window is None:
        return shift_start, shift_end
    if window.hardness == "SOFT":
        return shift_start, shift_end
    return window.start, window.end


def _single_profile(problem: Problem) -> str:
    """The fleet's shared routing profile, or a refusal. FR-07."""
    profiles = {vehicle.profile for vehicle in problem.vehicles}
    if len(profiles) > 1:
        raise NotImplementedError(
            f"{_UNSUPPORTED_MIXED_PROFILES} (found {sorted(profiles)})")
    return next(iter(profiles), "driving")


def _dimensions(problem: Problem) -> tuple[str, ...]:
    """Every capacity dimension in play, in a stable order. FR-02, §6.1."""
    names = {d for order in problem.orders for d in order.quantities}
    names |= {d for vehicle in problem.vehicles for d in vehicle.capacities}
    return tuple(sorted(names))


def compile_problem(problem: Problem) -> _Compiled:
    """Build the PyVRP model. Travel comes from the matrix, never the geometry."""
    dimensions = _dimensions(problem)
    _single_profile(problem)
    model = Model()

    # One PyVRP location per domain location, in matrix-index order so the edge
    # loop below can address them by index without a second mapping.
    ordered = sorted(problem.locations, key=lambda location: location.matrix_index)
    handles = [model.add_location(x=round(location.lon * 10_000),
                                  y=round(location.lat * 10_000),
                                  name=location.id)
               for location in ordered]

    depot_ids = {vehicle.start_location_id for vehicle in problem.vehicles}
    depot_ids |= {vehicle.ends_at for vehicle in problem.vehicles
                  if not vehicle.open_route}
    depots = {
        location.id: model.add_depot(location=handles[location.matrix_index],
                                     tw_early=min(v.shift.start for v in problem.vehicles),
                                     tw_late=max(v.shift.end for v in problem.vehicles),
                                     name=location.id)
        for location in ordered if location.id in depot_ids
    }

    # FR-08's "end-anywhere". PyVRP requires an end depot and accepts
    # `end_depot=None` by silently closing the route -- measured, a 2 km
    # one-way problem reports 4 km either way. A sink reachable from every
    # location at zero cost is the construction that actually works.
    open_sink = None
    if any(vehicle.open_route for vehicle in problem.vehicles):
        sink_handle = model.add_location(x=0, y=0, name="__open_route_sink__")
        open_sink = model.add_depot(
            location=sink_handle,
            tw_early=min(v.shift.start for v in problem.vehicles),
            tw_late=max(v.shift.end for v in problem.vehicles),
            name="__open_route_sink__")

    order_by_client: dict[int, str] = {}
    order_by_shipment: dict[int, str] = {}
    for index, order in enumerate(problem.orders):
        if order.kind == "SHIPMENT":
            # FR-01: goods move from one place to another, so PyVRP models it
            # as a pair with precedence and same-vehicle built in rather than
            # as two clients we would then have to constrain ourselves.
            if len(order.pickup.time_windows) > 1 or len(order.delivery.time_windows) > 1:
                raise NotImplementedError(
                    "a shipment end with several windows needs client groups, "
                    "which add_shipment does not take")
            collect = problem.location(order.pickup.location_id)
            drop = problem.location(order.delivery.location_id)
            model.add_shipment(
                pickup_location=handles[collect.matrix_index],
                delivery_location=handles[drop.matrix_index],
                pickup_tw_early=order.pickup.time_windows[0].start
                if order.pickup.time_windows else shift_start_of(problem),
                pickup_tw_late=order.pickup.time_windows[0].end
                if order.pickup.time_windows else shift_end_of(problem),
                pickup_service_duration=order.pickup.service_fixed
                + collect.dwell_overhead,
                delivery_tw_early=order.delivery.time_windows[0].start
                if order.delivery.time_windows else shift_start_of(problem),
                delivery_tw_late=order.delivery.time_windows[0].end
                if order.delivery.time_windows else shift_end_of(problem),
                delivery_service_duration=order.delivery.service_fixed
                + drop.dwell_overhead,
                amount=[order.quantities.get(name, 0) for name in dimensions],
                prize=order.prize,
                required=order.prize == 0,
                name=order.id,
            )
            order_by_shipment[len(order_by_shipment)] = order.id
            continue

        stop = order.delivery or order.pickup
        location = problem.location(stop.location_id)
        # §6.1's signed load: a quantity is applied at pickup and released at
        # delivery, so which list it goes in is what makes the load profile
        # rise or fall. A pickup-only order compiled as a delivery -- which is
        # what happened before E-20 -- inverts the profile silently.
        amounts = [order.quantities.get(name, 0) for name in dimensions]
        delivered = amounts if order.delivery is not None else [0] * len(dimensions)
        collected = amounts if order.delivery is None else [0] * len(dimensions)
        shift_end = max(v.shift.end for v in problem.vehicles)
        shift_start = min(v.shift.start for v in problem.vehicles)
        windows = stop.time_windows or (None,)

        # FR-04: several disjoint windows become several clients at the same
        # place in one mutually-exclusive group, so exactly one is visited.
        # PyVRP requires group members to be optional and the *group* to carry
        # the requirement -- a required client inside a group is rejected.
        group = (model.add_client_group(required=True)
                 if len(windows) > 1 else None)

        for window in windows:
            early, late = _bounds(window, shift_start, shift_end)
            client = model.add_client(
                location=handles[location.matrix_index],
                delivery=delivered,
                pickup=collected,
                service_duration=stop.service_fixed + location.dwell_overhead,
                tw_early=early,
                tw_late=late,
                release_time=order.release_time,
                prize=order.prize,
                # Optional orders are T-27. Until then everything is required,
                # and a solver that cannot place an order must say so rather
                # than drop it. Group members are the exception PyVRP demands.
                required=False if group is not None else order.prize == 0,
                group=group,
                name=order.id,
            )
            order_by_client[len(order_by_client)] = order.id
            del client

    vehicle_ids: list[str] = []
    for vehicle in problem.vehicles:
        # PyVRP spells the duration limit `shift_duration`, and both limits
        # must be omitted rather than passed as None when unset.
        limits = {}
        if vehicle.max_duration is not None:
            limits["shift_duration"] = vehicle.max_duration
        if vehicle.max_distance is not None:
            limits["max_distance"] = vehicle.max_distance
        # FR-07: costs come from the vehicle. PyVRP names them differently and
        # takes them natively, so this is wiring rather than modelling.
        costs = {}
        if vehicle.fixed_cost:
            costs["fixed_cost"] = vehicle.fixed_cost
        if vehicle.cost_per_metre:
            costs["unit_distance_cost"] = vehicle.cost_per_metre
        if vehicle.cost_per_second:
            costs["unit_duration_cost"] = vehicle.cost_per_second
        if vehicle.overtime_cost_per_second:
            costs["unit_overtime_cost"] = vehicle.overtime_cost_per_second

        model.add_vehicle_type(
            num_available=1,
            capacity=[vehicle.capacities.get(name, 0) for name in dimensions],
            start_depot=depots[vehicle.start_location_id],
            end_depot=open_sink if vehicle.open_route else depots[vehicle.ends_at],
            tw_early=vehicle.shift.start,
            tw_late=vehicle.shift.end,
            name=vehicle.id,
            **costs,
            **limits,
        )
        vehicle_ids.append(vehicle.id)

    matrix = problem.matrix
    for origin in ordered:
        for destination in ordered:
            if origin.matrix_index == destination.matrix_index:
                continue
            if not matrix.is_reachable(origin.matrix_index,
                                       destination.matrix_index):
                # MTX-5: an unreachable pair is a hard-infeasible arc, so the
                # edge is simply absent. Adding it at any finite cost is what
                # lets a solver route through a road that does not exist.
                continue
            model.add_edge(
                handles[origin.matrix_index], handles[destination.matrix_index],
                distance=matrix.distance(origin.matrix_index, destination.matrix_index),
                duration=matrix.duration(origin.matrix_index, destination.matrix_index),
            )
    if open_sink is not None:
        for origin in ordered:
            model.add_edge(handles[origin.matrix_index], sink_handle,
                           distance=0, duration=0)

    return _Compiled(model=model, dimensions=dimensions,
                     order_by_client=order_by_client,
                     order_by_shipment=order_by_shipment,
                     vehicle_ids=vehicle_ids)


def map_solution(problem: Problem, compiled: _Compiled, best,
                 feasible: bool = True) -> Solution:
    """Turn a PyVRP solution back into ours, keeping the solver's own timings.

    `feasible` is PyVRP's own verdict and must be passed through. An early
    version of this mapper hardcoded `FEASIBLE`, and a one-vehicle instance
    with four times too little capacity came back labelled feasible with
    nothing unassigned. The independent verifier caught it -- `INV-5 load
    units=48 exceeds capacity 12` -- which is precisely the job it exists to
    do, but the adapter should not be the one lying.
    """
    dimensions = compiled.dimensions
    index_to_location = {location.matrix_index: location
                         for location in problem.locations}
    routes: list[Route] = []
    served: set[str] = set()

    for route in best.routes():
        vehicle_id = compiled.vehicle_ids[route.vehicle_type()]
        # Load is reconstructed rather than read back: PyVRP reports a route
        # total, and INV-5 is about the load carried at each step.
        # The vehicle leaves the depot carrying everything it will drop, and
        # nothing it will collect. Reconstructed per dimension rather than read
        # back: PyVRP reports a route total, and INV-5 is about the load at
        # each step -- which for a route that both drops and collects is a
        # different number (§6.1's peak, not the total).
        # Only job deliveries are loaded at the depot. A shipment's goods sit
        # somewhere else until the vehicle collects them -- and they are
        # excluded here by `is_client()` alone, because a shipment activity is
        # never a client. An explicit `kind == "JOB"` test alongside it was
        # unfalsifiable: perturbation could not make it fail, which is the
        # signature of a guard that reads as protection and provides none.
        on_board = {
            name: sum(
                problem.order(compiled.order_by_client[activity.idx])
                .quantities.get(name, 0)
                for activity in route
                if activity.is_client()
                and problem.order(
                    compiled.order_by_client[activity.idx]).delivery is not None)
            for name in dimensions
        }

        steps: list[Step] = []
        for activity in route:
            if activity.is_depot():
                kind = "START" if not steps else "END"
                if kind == "END" and problem.vehicle(vehicle_id).open_route:
                    # The sink is a modelling device, not a place. An open
                    # route ends where it last stopped, which is the step
                    # already recorded -- so the END carries that location and
                    # the zero-cost arc to the sink never appears in the plan.
                    steps.append(Step(type="END", location_id=steps[-1].location_id,
                                      arrival=steps[-1].departure,
                                      start_service=steps[-1].departure,
                                      departure=steps[-1].departure,
                                      load_after=dict(steps[-1].load_after)))
                    continue
                location = index_to_location[
                    problem.location(
                        _depot_location_id(problem, route, activity)).matrix_index]
                steps.append(Step(type=kind, location_id=location.id,
                                  arrival=activity.start_time,
                                  start_service=activity.start_time,
                                  departure=activity.end_time,
                                  load_after=dict(on_board)))
                continue

            # Which index space this activity belongs to decides which order
            # it names. PyVRP numbers clients and shipments separately from
            # zero, so reading `idx` without checking would map shipment 0 onto
            # client 0 and report a well-formed plan naming the wrong stops.
            if activity.is_shipment():
                order_id = compiled.order_by_shipment[activity.idx]
                order = problem.order(order_id)
                collecting = activity.is_pickup()
                stop = order.pickup if collecting else order.delivery
                kind = "PICKUP" if collecting else "DELIVERY"
            else:
                order_id = compiled.order_by_client[activity.idx]
                order = problem.order(order_id)
                stop = order.delivery or order.pickup
                collecting = order.delivery is None
                kind = "DELIVERY" if order.delivery is not None else "PICKUP"

            served.add(order_id)
            for name in dimensions:
                quantity = order.quantities.get(name, 0)
                on_board[name] += quantity if collecting else -quantity
            steps.append(Step(
                type=kind,
                location_id=stop.location_id, order_id=order_id,
                # `start_time` is when service begins; arrival is that minus any
                # wait. PyVRP reports the wait separately, so this reconstructs
                # the arrival it implies rather than inventing one.
                arrival=activity.start_time - activity.wait_duration,
                start_service=activity.start_time,
                departure=activity.end_time,
                load_after=dict(on_board),
            ))
        routes.append(Route(vehicle_id=vehicle_id, steps=tuple(steps)))

    unassigned = tuple(
        {"order_id": order.id, "reason_code": "NOT_PLACED",
         "explanation": "the solver could not place this order within the "
                        "fleet's capacity and time constraints"}
        for order in problem.orders if order.id not in served
    )
    return Solution(problem_id=problem.id, routes=tuple(routes),
                    unassigned=unassigned,
                    objective_breakdown={},
                    status="FEASIBLE" if feasible else "INFEASIBLE")


def _depot_location_id(problem: Problem, route, activity) -> str:
    """Which depot a depot-activity refers to.

    A route may start and end at different depots, so the first depot activity
    is the start and any later one is the end.
    """
    vehicle = problem.vehicle(
        problem.vehicles[route.vehicle_type()].id)
    first = next(iter(route))
    return vehicle.start_location_id if activity is first else vehicle.ends_at


def solve(problem: Problem, iterations: int = 500, seed: int = 0) -> Solution:
    """Compile, solve, and map back. Deterministic for a given seed (CON-4)."""
    compiled = compile_problem(problem)
    result = compiled.model.solve(stop=MaxIterations(iterations), seed=seed,
                                  display=False)
    return map_solution(problem, compiled, result.best,
                        feasible=result.is_feasible())
