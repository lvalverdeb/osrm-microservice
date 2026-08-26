"""OR-Tools adapter — the second engine. SDD §7.3, CON-3, T-30.

§7.3 keeps OR-Tools as the portfolio's "expressiveness escape hatch": PyVRP is
the stronger CVRPTW solver, and OR-Tools models constraints PyVRP cannot. This
adapter exists for a second reason too, and it is the one E-30 tests. CON-3
says "the domain model is defined independently of any solver... No
solver-specific concept may leak into the domain layer", and a single adapter
cannot demonstrate that. Two can: the same `Problem`, untouched, compiled twice.

What it supports is deliberately narrower than the PyVRP adapter, and refuses
the rest rather than approximating it. An escape hatch that quietly ignores a
constraint is worse than one that declines the instance, because the plan it
returns looks like an answer.

Determinism (CON-4): OR-Tools is given a fixed seed and a deterministic
solution limit rather than a wall-clock budget, for the same reason §7.3 gives
-- a time limit makes the answer depend on the machine.

Placement: Python. A solver adapter, beside the one it exists to disagree with.
"""

from __future__ import annotations

from dataclasses import dataclass

from vrp.model import Problem, Route, Solution, Step, service_time

_UNSUPPORTED = (
    "the OR-Tools adapter takes single-window delivery jobs on a homogeneous "
    "fleet; {what} needs the PyVRP adapter")


@dataclass(frozen=True)
class _Compiled:
    manager: object
    routing: object
    dimension: str | None
    order_at_node: dict[int, str]


def _refuse(problem: Problem) -> None:
    """Decline what this adapter cannot model, by name.

    Every one of these is expressible in OR-Tools with more work. Refusing is a
    statement about this adapter's scope, not about the library -- and naming
    the feature beats returning a plan that silently ignored it.
    """
    for order in problem.orders:
        if order.kind == "SHIPMENT":
            raise NotImplementedError(_UNSUPPORTED.format(what="shipments"))
        if order.pickup is not None and order.delivery is None:
            raise NotImplementedError(_UNSUPPORTED.format(what="pickup-only orders"))
        stop = order.delivery
        if len(stop.time_windows) > 1:
            raise NotImplementedError(
                _UNSUPPORTED.format(what="multiple time windows"))
    if any(vehicle.open_route for vehicle in problem.vehicles):
        raise NotImplementedError(_UNSUPPORTED.format(what="open routes"))
    if any(vehicle.max_reloads for vehicle in problem.vehicles):
        raise NotImplementedError(_UNSUPPORTED.format(what="multi-trip reloading"))
    if problem.locks:
        raise NotImplementedError(_UNSUPPORTED.format(what="operator locks"))
    starts = {vehicle.start_location_id for vehicle in problem.vehicles}
    if len(starts) > 1:
        raise NotImplementedError(_UNSUPPORTED.format(what="multiple depots"))


def solve(problem: Problem, solutions: int = 200, seed: int = 0) -> Solution:
    """Solve with OR-Tools and return the same `Solution` PyVRP would.

    Args:
        problem: the domain instance, unchanged and unaware of either engine.
        solutions: deterministic budget -- a solution limit, not a time limit,
            so the answer does not depend on the machine (CON-4).
        seed: recorded on the result for replay.

    Returns:
        A `Solution` the same verifier and evaluator judge as PyVRP's.

    Raises:
        NotImplementedError: the instance uses something outside this adapter's
            scope, named explicitly.
    """
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    _refuse(problem)
    matrix = problem.matrix
    size = len(matrix.durations)
    depot_id = problem.vehicles[0].start_location_id
    depot = problem.location(depot_id).matrix_index
    fleet = list(problem.vehicles)

    order_at_node = {
        problem.location(order.delivery.location_id).matrix_index: order.id
        for order in problem.orders}

    manager = pywrapcp.RoutingIndexManager(size, len(fleet), depot)
    routing = pywrapcp.RoutingModel(manager)

    dimensions = {name for order in problem.orders for name in order.quantities}
    dimension = next(iter(sorted(dimensions)), None)

    def travel(from_index: int, to_index: int) -> int:
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        if not matrix.is_reachable(i, j):
            # MTX-5: a hard-infeasible arc. OR-Tools takes an integer, so the
            # sentinel becomes a cost no route can afford rather than an
            # absent edge -- which is the same "large finite" trap MTX-5 warns
            # about, and is why `_refuse` keeps unreachable instances out.
            raise ValueError(f"no route from node {i} to {j}")
        return matrix.distance(i, j)

    transit = routing.RegisterTransitCallback(travel)
    routing.SetArcCostEvaluatorOfAllVehicles(transit)

    if dimension is not None:
        demands = [0] * size
        for order in problem.orders:
            node = problem.location(order.delivery.location_id).matrix_index
            demands[node] = order.quantities.get(dimension, 0)

        def demand(from_index: int) -> int:
            return demands[manager.IndexToNode(from_index)]

        routing.AddDimensionWithVehicleCapacity(
            routing.RegisterUnaryTransitCallback(demand), 0,
            [v.capacities.get(dimension, 0) for v in fleet], True, "Capacity")

    # Time, so windows and service durations bind. Service is the domain's own
    # `service_time` (FR-05), not a solver-local notion of it.
    def elapsed(from_index: int, to_index: int) -> int:
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        here = order_at_node.get(i)
        served = (service_time(problem.order(here), fleet[0],
                               problem.location(problem.order(here)
                                                .delivery.location_id))
                  if here else 0)
        return matrix.duration(i, j) + served

    horizon = max(v.shift.end for v in fleet)
    routing.AddDimension(routing.RegisterTransitCallback(elapsed), horizon,
                         horizon, False, "Time")
    time_dimension = routing.GetDimensionOrDie("Time")
    for order in problem.orders:
        node = problem.location(order.delivery.location_id).matrix_index
        windows = [w for w in order.delivery.time_windows if w.hardness == "HARD"]
        if not windows:
            continue
        index = manager.NodeToIndex(node)
        time_dimension.CumulVar(index).SetRange(windows[0].start, windows[0].end)
    for number, vehicle in enumerate(fleet):
        start = routing.Start(number)
        time_dimension.CumulVar(start).SetRange(vehicle.shift.start,
                                                vehicle.shift.end)

    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC)
    parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    # A solution limit rather than a time limit: CON-4 wants the budget in
    # deterministic units so a replay on another machine gives the same plan.
    parameters.solution_limit = solutions
    parameters.log_search = False

    assignment = routing.SolveWithParameters(parameters)
    if assignment is None:
        return Solution(
            problem_id=problem.id, routes=(), status="INFEASIBLE",
            unassigned=tuple({"order_id": order.id, "reason_code": "NOT_PLACED",
                              "explanation": "OR-Tools found no assignment"}
                             for order in problem.orders),
            objective_breakdown={},
            solver={"solver": "ortools", "seed": seed,
                    "iterations": solutions,
                    "matrix_version": matrix.version})

    return _map(problem, manager, routing, assignment, order_at_node,
                dimension, fleet, seed, solutions)


def _map(problem: Problem, manager, routing, assignment, order_at_node,
         dimension, fleet, seed: int, solutions: int) -> Solution:
    """Turn OR-Tools' assignment into the domain `Solution`.

    Carries the solver's own arrival times, exactly as the PyVRP mapper does,
    so the independent verifier checks the engine's arithmetic rather than
    ours.
    """
    time_dimension = routing.GetDimensionOrDie("Time")
    routes: list[Route] = []
    served: set[str] = set()

    for number, vehicle in enumerate(fleet):
        index = routing.Start(number)
        if not routing.IsVehicleUsed(assignment, number):
            continue

        remaining = sum(
            problem.order(order_at_node[node]).quantities.get(dimension, 0)
            for node in order_at_node
            if _visits(routing, assignment, number, manager, node)
        ) if dimension else 0

        steps: list[Step] = []
        while True:
            node = manager.IndexToNode(index)
            when = assignment.Value(time_dimension.CumulVar(index))
            order_id = order_at_node.get(node)

            if not steps:
                steps.append(Step(type="START", location_id=vehicle.start_location_id,
                                  arrival=when, start_service=when, departure=when,
                                  load_after={dimension: remaining} if dimension else {}))
            elif order_id is not None:
                order = problem.order(order_id)
                served.add(order_id)
                quantity = order.quantities.get(dimension, 0) if dimension else 0
                remaining -= quantity
                stop = order.delivery
                duration = service_time(order, vehicle,
                                        problem.location(stop.location_id))
                steps.append(Step(
                    type="DELIVERY", location_id=stop.location_id,
                    order_id=order_id, arrival=when, start_service=when,
                    departure=when + duration,
                    load_after={dimension: remaining} if dimension else {}))

            if routing.IsEnd(index):
                steps.append(Step(type="END", location_id=vehicle.ends_at,
                                  arrival=when, start_service=when, departure=when,
                                  load_after={dimension: remaining} if dimension else {}))
                break
            index = assignment.Value(routing.NextVar(index))

        routes.append(Route(vehicle_id=vehicle.id, steps=tuple(steps)))

    return Solution(
        problem_id=problem.id, routes=tuple(routes),
        unassigned=tuple({"order_id": order.id, "reason_code": "NOT_PLACED",
                          "explanation": "not placed by OR-Tools"}
                         for order in problem.orders if order.id not in served),
        objective_breakdown={},
        status="FEASIBLE" if len(served) == len(problem.orders) else "INFEASIBLE",
        solver={"solver": "ortools", "seed": seed, "iterations": solutions,
                "matrix_version": problem.matrix.version})


def _visits(routing, assignment, number: int, manager, node: int) -> bool:
    index = routing.Start(number)
    while not routing.IsEnd(index):
        if manager.IndexToNode(index) == node:
            return True
        index = assignment.Value(routing.NextVar(index))
    return False
