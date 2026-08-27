"""Set-partitioning polish over the route pool — ALG-6, T-38.

ALG-6: "Collect all distinct routes generated across the whole search into a
pool, then solve a set-partitioning MILP over the pool (each order covered
exactly once, vehicle-type counts respected). With a few thousand columns this
solves in seconds and reliably recovers 0.5-2% over the best single trajectory.
Requires that pooled routes be individually verified feasible."

The premise is that a search discards good work. Run A finds an excellent route
through the north and a mediocre one through the south; run B does the reverse.
Neither trajectory is the best plan available, and the best plan is already
sitting in the union of what the two of them built -- nobody has assembled it.
Set partitioning assembles it.

**Exactly once, not at least once.** Relaxing the covering constraint to `>=`
turns this into set covering: easier to solve, cheaper-looking, and it produces
plans that deliver the same parcel twice. The difference is one character in the
model, so it has its own test.

**"Across the whole search" is approximated here.** PyVRP does not expose the
intermediate solutions it passes through, so the pool is fed by repeated short
runs at different seeds rather than by instrumenting one long run. That is a
weaker pool than ALG-6 describes -- a genuine trajectory pool would hold every
route the search ever touched -- and it is the part to revisit if the recovered
percentage ever needs to be larger. `build_pool` says which it is doing.

**Feasibility of columns.** ALG-6 requires pooled routes be individually
feasible, so `RoutePool.add` refuses any route its vehicle could not drive.
That check is `route_is_legal`, the constructor-side predicate: it shares the
timeline builder with the plan, which is fine for deciding what to *attempt*.
The finished recombination is then judged by `vrp.verify`, which shares nothing
with any of this. Admission is a construction decision; the plan is not.

Placement: **Python**. A MILP over a column pool, off the request path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise

from vrp.evaluator import route_is_legal
from vrp.hos.schedule import schedule_route
from vrp.model import Problem, Route, Solution

# CP-SAT is deterministic only when told to be. One worker and a fixed seed:
# a parallel portfolio search returns whichever equally-optimal solution its
# threads happened to race to, which would break CON-4 across machines.
_WORKERS = 1


@dataclass(frozen=True)
class PooledRoute:
    """One column: a sequence of orders, the vehicle that can drive it, its cost.

    `vehicle_id` is carried because "vehicle-type counts respected" needs the
    column to know what it needs -- a route legal on a 50-unit van is not legal
    on a 20-unit one, and a pool that forgets which is which respects nothing.
    """

    order_ids: tuple[str, ...]
    vehicle_id: str
    cost: int

    @property
    def key(self) -> tuple[str, tuple[str, ...]]:
        return self.vehicle_id, self.order_ids


def route_distance(problem: Problem, vehicle_id: str,
                   order_ids: list[str]) -> int:
    """What this route costs, read from the matrix.

    Not from whatever produced it. INV-9's argument applies to a pool with
    particular force: its columns come from several engines, each of which
    counted something slightly different.
    """
    vehicle = problem.vehicle(vehicle_id)
    index = {location.id: location.matrix_index for location in problem.locations}
    nodes = [index[vehicle.start_location_id]]
    nodes += [index[(problem.order(o).delivery or problem.order(o).pickup
                     ).location_id] for o in order_ids]
    nodes.append(index[vehicle.end_location_id])
    return sum(problem.matrix.distance(a, b)
               for a, b in pairwise(nodes))


@dataclass
class RoutePool:
    """The distinct routes a search produced, and what each one costs."""

    entries: dict[tuple[str, tuple[str, ...]], PooledRoute] = field(
        default_factory=dict)
    # The total cost of each contributing run, so the polish can be compared
    # against the best single trajectory that fed it -- which is the comparison
    # ALG-6 actually claims to win.
    trajectories: list[int] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries.values())

    def add(self, problem: Problem, vehicle_id: str,
            order_ids: list[str]) -> bool:
        """Admit one route, if its vehicle could actually drive it.

        Returns whether it was admitted. A rejected route is not an error: a
        pool is fed by several engines and the point of the check is that one
        of them proposing an impossible route cannot contaminate the model.
        """
        if not order_ids:
            return False
        if not route_is_legal(problem, vehicle_id, list(order_ids)):
            return False

        entry = PooledRoute(order_ids=tuple(order_ids), vehicle_id=vehicle_id,
                            cost=route_distance(problem, vehicle_id, order_ids))
        self.entries.setdefault(entry.key, entry)
        return True


def build_pool(problem: Problem, runs: int = 5, iterations: int = 200,
               seed: int = 0) -> RoutePool:
    """Collect routes from repeated short searches. ALG-6's pool.

    Args:
        problem: the instance.
        runs: how many independent searches contribute. Diversity is the whole
            point -- one run's pool can only reproduce that run.
        iterations: budget per run. Deterministic, not wall-clock (CON-4).
        seed: base seed; run `k` uses `seed + k`, so the set of runs is
            reproducible and the runs differ from each other.

    Returns:
        A pool of distinct feasible routes, plus each run's total cost in
        `trajectories`.

    This approximates ALG-6's "all distinct routes generated across the whole
    search": repeated short runs stand in for instrumenting one long one,
    because PyVRP does not expose the solutions it passes through. The pool is
    therefore smaller than the technique assumes.
    """
    from vrp.solve import pyvrp_adapter

    pool = RoutePool()
    for run in range(runs):
        solution = pyvrp_adapter.solve(problem, iterations=iterations,
                                       seed=seed + run)
        total = 0
        for route in solution.routes:
            orders = [step.order_id for step in route.steps if step.order_id]
            if not orders:
                continue
            pool.add(problem, route.vehicle_id, orders)
            total += route_distance(problem, route.vehicle_id, orders)
        if not solution.unassigned:
            pool.trajectories.append(total)

    return pool


def partition_cost(chosen: list[PooledRoute] | None) -> int:
    """What a selection costs. Summed over columns, which for a partition is
    exact: every order is in exactly one of them."""
    return sum(route.cost for route in chosen) if chosen else 0


def select_routes(problem: Problem, pool: RoutePool,
                  seed: int = 0) -> list[PooledRoute] | None:
    """Solve the set-partitioning model over the pool. ALG-6.

    Args:
        problem: the instance, for its order list and fleet size.
        pool: the columns.
        seed: CP-SAT's seed. With one worker this makes the answer reproducible.

    Returns:
        The cheapest set of columns covering every order exactly once and using
        no more vehicles than exist, or None when the pool admits no partition
        at all. None rather than a best-effort partial cover: a partial cover is
        not a cheap plan, it is undelivered freight.
    """
    from ortools.sat.python import cp_model

    columns = list(pool)
    if not columns:
        return None

    model = cp_model.CpModel()
    take = [model.NewBoolVar(f"r{i}") for i in range(len(columns))]

    # Exactly once. `== 1`, not `>= 1`: at-least-once is set *covering*, and it
    # buys a cheaper objective by delivering some orders twice.
    covering: dict[str, list] = {order.id: [] for order in problem.orders}
    for index, column in enumerate(columns):
        for order_id in column.order_ids:
            covering[order_id].append(take[index])
    for order_id, members in covering.items():
        if not members:
            return None                      # no column serves this order
        model.Add(sum(members) == 1)

    # Vehicle-type counts respected. Columns are keyed by the vehicle that can
    # drive them, so a per-vehicle cap of one is exactly "each vehicle drives at
    # most one route", and the fleet cap follows from there.
    by_vehicle: dict[str, list] = {}
    for index, column in enumerate(columns):
        by_vehicle.setdefault(column.vehicle_id, []).append(take[index])
    for members in by_vehicle.values():
        model.Add(sum(members) <= 1)
    model.Add(sum(take) <= len(problem.vehicles))

    model.Minimize(sum(column.cost * take[index]
                       for index, column in enumerate(columns)))

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = _WORKERS
    solver.parameters.random_seed = seed
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    return [column for index, column in enumerate(columns)
            if solver.Value(take[index])]


def polish(problem: Problem, runs: int = 5, iterations: int = 200,
           seed: int = 0) -> Solution:
    """Search, pool, partition, and build the plan. ALG-6 end to end.

    Returns:
        A Solution whose routes are exactly the selected columns, in the
        sequence they were pooled as. The polish *selects* routes; it does not
        re-sequence them, because the cost the model minimised is the cost of
        those sequences and no others.
    """
    pool = build_pool(problem, runs=runs, iterations=iterations, seed=seed)
    chosen = select_routes(problem, pool, seed=seed)
    if chosen is None:
        raise ValueError(
            f"no partition of {problem.id} exists over a pool of {len(pool)} "
            "routes; the pool is the problem, not the model")

    routes = tuple(
        Route(vehicle_id=column.vehicle_id,
              steps=schedule_route(problem, column.vehicle_id,
                                   list(column.order_ids), rules=None).steps)
        for column in sorted(chosen, key=lambda c: c.vehicle_id))

    return Solution(
        problem_id=problem.id, routes=routes, unassigned=(),
        objective_breakdown={"total": partition_cost(chosen)},
        status="FEASIBLE",
        solver={"solver": "setpartition", "seed": seed,
                "columns": len(pool), "runs": runs,
                "matrix_version": problem.matrix.version})
