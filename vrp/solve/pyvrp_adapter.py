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

# PyVRP models one capacity dimension per index; this adapter handles the
# single-dimension case. Multi-dimensional capacity is T-20/E-20 and needs the
# dimension order pinned so a solution maps back unambiguously.
_UNSUPPORTED_MULTI_DIMENSION = (
    "multi-dimensional capacity is T-20; this adapter takes one dimension")

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
    dimension: str | None
    # PyVRP client index (0-based, in insertion order) -> our order id.
    order_by_client: dict[int, str]
    vehicle_ids: list[str]


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


def _single_dimension(problem: Problem) -> str | None:
    dimensions = {d for order in problem.orders for d in order.quantities}
    dimensions |= {d for vehicle in problem.vehicles for d in vehicle.capacities}
    if len(dimensions) > 1:
        raise NotImplementedError(_UNSUPPORTED_MULTI_DIMENSION)
    return next(iter(dimensions), None)


def compile_problem(problem: Problem) -> _Compiled:
    """Build the PyVRP model. Travel comes from the matrix, never the geometry."""
    dimension = _single_dimension(problem)
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
    for index, order in enumerate(problem.orders):
        stop = order.delivery or order.pickup
        if order.kind == "SHIPMENT":
            raise NotImplementedError("shipments are T-13")
        location = problem.location(stop.location_id)
        quantity = order.quantities.get(dimension, 0) if dimension else 0
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
                delivery=[quantity],
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
            capacity=[vehicle.capacities.get(dimension, 0)] if dimension else [],
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

    return _Compiled(model=model, dimension=dimension,
                     order_by_client=order_by_client, vehicle_ids=vehicle_ids)


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
    dimension = compiled.dimension
    index_to_location = {location.matrix_index: location
                         for location in problem.locations}
    routes: list[Route] = []
    served: set[str] = set()

    for route in best.routes():
        vehicle_id = compiled.vehicle_ids[route.vehicle_type()]
        # Load is reconstructed rather than read back: PyVRP reports a route
        # total, and INV-5 is about the load carried at each step.
        on_board = sum(
            problem.order(compiled.order_by_client[activity.idx]).quantities.get(dimension, 0)
            for activity in route if activity.is_client()
        ) if dimension else 0

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
                                  load_after={dimension: on_board} if dimension else {}))
                continue

            order_id = compiled.order_by_client[activity.idx]
            order = problem.order(order_id)
            served.add(order_id)
            quantity = order.quantities.get(dimension, 0) if dimension else 0
            on_board -= quantity
            stop = order.delivery or order.pickup
            steps.append(Step(
                type="DELIVERY" if order.delivery is not None else "PICKUP",
                location_id=stop.location_id, order_id=order_id,
                # `start_time` is when service begins; arrival is that minus any
                # wait. PyVRP reports the wait separately, so this reconstructs
                # the arrival it implies rather than inventing one.
                arrival=activity.start_time - activity.wait_duration,
                start_service=activity.start_time,
                departure=activity.end_time,
                load_after={dimension: on_board} if dimension else {},
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
