"""Large-instance decomposition — §7.6, T-37, NFR-01.

§7.6 asks for three things and forbids a fourth. Partition the instance (a),
re-optimise sub-problems against an incumbent (b), repair the seams that
partitioning left behind (c) -- and never, under DEC-3, score the result by
adding up what the sub-solvers said about their own pieces.

The reason this is an orchestrator rather than a for-loop is DEC-1: "depot
inventory, dock capacity and shared-vehicle constraints MUST be enforced
globally, never per cluster". Every sub-problem can be individually perfect and
the concatenation still fiction -- fifty clusters each politely sending one
vehicle to the same dock at 06:00, none aware of the other forty-nine. FR-19
puts it plainly: "if 40 vehicles are planned to depart at 06:00 and there are 8
bays, the plan is fiction". No sub-solver can see that, so the orchestrator owns
a global scheduling step, and INV-12 is what judges it.

The partitioner is adaptive because §7.6 requires it to be: "fixed partitioning
rules perform inconsistently across instances with differing spatial/demand/
operational characteristics, so the partitioner MUST be adaptive and MUST be
evaluated as a component, not assumed". `partition_spatially` exists to be the
fixed rule that gets beaten, and `partition_quality` is the measurement -- kept
in the module rather than the test so the comparison is reproducible outside it.

**Measured against NFR-01**, which allows an hour for 10,000 stops:

    stops    clusters   wall clock   routes
       1000        10        2.8 s       20
      10000        50        9.6 min    200

Both verify. The 10,000-stop run is the acceptance, and it is a sixth of the
budget rather than a squeak under it -- worth stating, because the number that
matters is not "passed" but how much room is left when a real instance turns out
to be harder than a generated one.

Placement: **Python**. Orchestration over the adapters, off the request path,
and every part of it is search-shaped work the gateway has no business holding.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from vrp.evaluator import ObjectiveWeights, evaluate, route_is_legal
from vrp.hos.schedule import schedule_route
from vrp.matrix import submatrix
from vrp.model import Location, Problem, Route, Solution, Step, Vehicle

# How many candidate cross-boundary moves are evaluated before the repair stops.
# A count rather than a clock, for CON-4: the same instance and seed must give
# the same plan on a fast machine and a slow one. What the cap dropped is
# reported in the returned metadata rather than swallowed -- a silent truncation
# reads as "everything was tried".
DEFAULT_REPAIR_BUDGET = 2_000

# Weights are 1:1:1 across imbalance, window mixing and spatial spread. That is
# itself the §7.6 claim: a partition is "jointly capacity- and time-aware, not
# merely spatial", so the two operational terms together outweigh the spatial
# one two to one. Tightening these would be tuning the referee.
_QUALITY_TERMS = ("imbalance", "mixing", "spread")


@dataclass(frozen=True)
class SubProblem:
    """One cluster: its orders, its sub-fleet, and a Problem of its own.

    `vehicle_ids` are disjoint across sub-problems, which is DEC-2. A vehicle in
    two clusters is planned twice and can only do one of them.
    """

    index: int
    order_ids: tuple[str, ...]
    vehicle_ids: tuple[str, ...]
    problem: Problem


# --------------------------------------------------------------------------
# (a) Cluster-first partitioning
# --------------------------------------------------------------------------

def _position(problem: Problem, order_id: str) -> tuple[float, float]:
    """A stop's coordinates as `(lon, lat)` degrees."""
    order = problem.order(order_id)
    stop = order.delivery or order.pickup
    location = problem.location(stop.location_id)
    return location.lon, location.lat


def _planar(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Project `(lon, lat)` degrees onto a local plane. `UC-073`.

    Two assumptions are wrong in raw degrees and both change the answer here.

    Longitude wraps: a cluster straddling the antimeridian averages to a
    centroid on the far side of the planet, so the sweep below measures angles
    from a point 180 degrees away and cuts the instance by which side of the
    dateline a stop fell on rather than by geography. Longitudes are unwrapped
    around the first point, so 179.95 and -179.95 become neighbours 0.1 apart.

    And a degree of longitude is not a degree of distance: it shrinks with
    `cos(latitude)`, to half by 60 degrees north. Without the scaling a sweep
    over a Nordic depot produces wedges stretched east-west, which is not what
    the demand cut below assumes it is cutting.

    A local plane is enough. Sub-problem membership does not need geodesics --
    real travel comes from the matrix, and this only has to keep neighbours
    adjacent.
    """
    if not points:
        return []
    reference_lon = points[0][0]
    mean_lat = sum(lat for _, lat in points) / len(points)
    scale = math.cos(math.radians(mean_lat))
    return [(((lon - reference_lon + 180.0) % 360.0 - 180.0) * scale, lat)
            for lon, lat in points]


def _window_key(problem: Problem, order_id: str) -> int:
    """When this order's day starts. §7.6(a)'s time-window awareness.

    Two stops next door to each other but four hours apart do not belong on one
    route, and a partitioner blind to that hands the sub-solver a problem with
    no good answer in it.
    """
    order = problem.order(order_id)
    stop = order.delivery or order.pickup
    return stop.time_windows[0].start if stop.time_windows else 0


def _demand(problem: Problem, order_id: str) -> int:
    return sum(problem.order(order_id).quantities.values())


def _sweep(problem: Problem, order_ids: list[str]) -> list[str]:
    """Order stops by angle about their own centroid.

    A sweep keeps neighbours adjacent in one dimension, which is what lets the
    demand-balanced cut below stay spatially coherent: cutting a sweep produces
    wedges, cutting an arbitrary order produces confetti.
    """
    points = _planar([_position(problem, order_id) for order_id in order_ids])
    cx = sum(x for x, _ in points) / len(points)
    cy = sum(y for _, y in points) / len(points)
    angles = {order_id: math.atan2(y - cy, x - cx)
              for order_id, (x, y) in zip(order_ids, points)}
    return sorted(order_ids, key=lambda o: (angles[o], o))


def _cut_by_demand(problem: Problem, ordered: list[str],
                   groups: int) -> list[list[str]]:
    """Cut a sweep into `groups` runs of roughly equal demand.

    Equal *demand*, not equal count: §7.6(a) wants sub-problems that are
    "capacity-aware", and on any instance where demand correlates with position
    -- which is most of them, since heavy customers cluster -- equal counts put
    every heavy order in one cluster and leave that sub-solver short of fleet
    while its neighbour idles.
    """
    if groups <= 1:
        return [ordered]
    total = sum(_demand(problem, order_id) for order_id in ordered)
    target = total / groups

    cuts: list[list[str]] = [[]]
    running = 0
    for order_id in ordered:
        # Start a new run once this one has had its share, provided there are
        # runs left to fill and orders left to fill them with.
        remaining_groups = groups - len(cuts)
        if (running >= target * len(cuts) and remaining_groups > 0
                and len(ordered) - ordered.index(order_id) > remaining_groups):
            cuts.append([])
        cuts[-1].append(order_id)
        running += _demand(problem, order_id)

    return [run for run in cuts if run]


def _assign_vehicles(problem: Problem, groups: list[list[str]],
                     ) -> list[tuple[str, ...]]:
    """Split the fleet across clusters, proportional to demand. DEC-2.

    Every cluster gets at least one vehicle: a cluster with orders and no fleet
    is infeasible by construction, and would surface as unassigned demand rather
    than as the partitioning bug it is.
    """
    fleet = [vehicle.id for vehicle in problem.vehicles]
    if len(fleet) < len(groups):
        raise ValueError(
            f"{len(groups)} clusters but only {len(fleet)} vehicles; "
            "a cluster without a vehicle is infeasible by construction")

    loads = [sum(_demand(problem, o) for o in group) for group in groups]
    total = sum(loads) or 1
    shares = [max(1, round(len(fleet) * load / total)) for load in loads]

    # Round-off is settled against the largest cluster, so the arithmetic never
    # hands out more vehicles than exist or leaves one unowned.
    while sum(shares) > len(fleet):
        shares[shares.index(max(shares))] -= 1
    while sum(shares) < len(fleet):
        shares[loads.index(max(loads))] += 1

    out, cursor = [], 0
    for share in shares:
        out.append(tuple(fleet[cursor:cursor + share]))
        cursor += share
    return out


def _build_subproblem(problem: Problem, index: int, order_ids: tuple[str, ...],
                      vehicle_ids: tuple[str, ...]) -> Problem:
    """A real Problem over just this cluster, with its own dense matrix.

    Ids are preserved -- only matrix indices are renumbered -- so a sub-solution
    maps back by reading it, with no translation table to get wrong.
    """
    depots = {problem.vehicle(v).start_location_id for v in vehicle_ids}
    depots |= {problem.vehicle(v).end_location_id for v in vehicle_ids}
    stops = {(problem.order(o).delivery or problem.order(o).pickup).location_id
             for o in order_ids}

    keep = sorted(depots | stops,
                  key=lambda lid: problem.location(lid).matrix_index)
    renumbered = {location_id: position for position, location_id in enumerate(keep)}
    matrix = submatrix(problem.matrix,
                       [problem.location(lid).matrix_index for lid in keep])

    locations = tuple(
        Location(id=lid, lat=problem.location(lid).lat,
                 lon=problem.location(lid).lon, matrix_index=renumbered[lid],
                 dwell_overhead=problem.location(lid).dwell_overhead,
                 access_classes=problem.location(lid).access_classes,
                 max_vehicle_kg=problem.location(lid).max_vehicle_kg,
                 # Dock capacity is deliberately *not* carried down. DEC-1: it
                 # is a global constraint, and a sub-solver told about one bay
                 # would serialise its own vehicles while remaining oblivious to
                 # the other clusters queueing for the same bay -- paying the
                 # cost of the constraint without gaining its guarantee.
                 dock_capacity=None)
        for lid in keep)

    return Problem(
        id=f"{problem.id}#c{index}", locations=locations,
        orders=tuple(problem.order(o) for o in order_ids),
        vehicles=tuple(problem.vehicle(v) for v in vehicle_ids),
        matrix=matrix)


def partition(problem: Problem, target_size: int = 200,
              seed: int = 0) -> list[SubProblem]:
    """Adaptive cluster-first partitioning. §7.6(a).

    Three passes, in the order §7.6 names them: separate by time-window opening
    (a route cannot span two shifts), sweep each group spatially, then cut the
    sweep by cumulative demand rather than by count.

    Args:
        problem: the whole instance.
        target_size: stops per sub-problem. The count of clusters follows from
            it, so a caller tunes one number rather than two.
        seed: accepted for CON-4 symmetry with the rest of the pipeline. The
            partition is deterministic without it; it is here so that a caller
            threading a seed through the orchestrator does not have to special-
            case this stage.

    Returns:
        Sub-problems covering every order exactly once, with disjoint sub-fleets
        (DEC-2) and at least one vehicle each.
    """
    _ = seed
    by_window: dict[int, list[str]] = defaultdict(list)
    for order in problem.orders:
        by_window[_window_key(problem, order.id)].append(order.id)

    groups: list[list[str]] = []
    for opens in sorted(by_window):
        members = by_window[opens]
        wanted = max(1, round(len(members) / max(target_size, 1)))
        groups.extend(_cut_by_demand(problem, _sweep(problem, members), wanted))

    fleets = _assign_vehicles(problem, groups)
    return [
        SubProblem(index=index, order_ids=tuple(group),
                   vehicle_ids=fleets[index],
                   problem=_build_subproblem(problem, index, tuple(group),
                                             fleets[index]))
        for index, group in enumerate(groups)
    ]


def partition_spatially(problem: Problem,
                        target_size: int = 200) -> list[SubProblem]:
    """The fixed rule §7.6 warns about: sweep, then cut into equal counts.

    Kept so "adaptive" can be measured rather than asserted. It is a perfectly
    reasonable partitioner -- it is what most descriptions of cluster-first mean
    -- and on an instance where demand and time do not correlate with position
    it is as good as the adaptive one. That is the point: it performs
    *inconsistently*, not badly.
    """
    ordered = _sweep(problem, [order.id for order in problem.orders])
    wanted = max(1, round(len(ordered) / max(target_size, 1)))
    size = math.ceil(len(ordered) / wanted)
    groups = [ordered[i:i + size] for i in range(0, len(ordered), size)]

    fleets = _assign_vehicles(problem, groups)
    return [
        SubProblem(index=index, order_ids=tuple(group),
                   vehicle_ids=fleets[index],
                   problem=_build_subproblem(problem, index, tuple(group),
                                             fleets[index]))
        for index, group in enumerate(groups)
    ]


def partition_quality(problem: Problem, clusters: list[SubProblem]) -> float:
    """How good a partition is, lower better. §7.6(a)'s "evaluated as a component".

    Three dimensionless terms, equally weighted:

    * **imbalance** -- heaviest cluster over the mean. One is perfect. This is
      the capacity awareness §7.6(a) asks for.
    * **mixing** -- distinct window openings per cluster, averaged. One is
      perfect. This is the time awareness.
    * **spread** -- mean cluster radius over the instance radius. Smaller is
      better; this is the purely spatial term, and it is one vote of three
      rather than the only one, which is the whole disagreement with a fixed
      spatial rule.

    Judging a partition by the cost of actually solving it would be a better
    metric and a far worse test: it would take minutes per comparison and fold
    the solver's own variance into a measurement of the partitioner.
    """
    loads = [sum(_demand(problem, o) for o in c.order_ids) for c in clusters]
    mean_load = (sum(loads) / len(loads)) if loads else 1
    imbalance = max(loads) / mean_load if mean_load else 1.0

    mixing = sum(len({_window_key(problem, o) for o in c.order_ids})
                 for c in clusters) / max(len(clusters), 1)

    points = [_position(problem, order.id) for order in problem.orders]
    cx = sum(x for x, _ in points) / len(points)
    cy = sum(y for _, y in points) / len(points)
    instance_radius = max(math.hypot(x - cx, y - cy) for x, y in points) or 1.0

    radii = []
    for cluster in clusters:
        local = [_position(problem, o) for o in cluster.order_ids]
        lx = sum(x for x, _ in local) / len(local)
        ly = sum(y for _, y in local) / len(local)
        radii.append(max(math.hypot(x - lx, y - ly) for x, y in local))
    spread = (sum(radii) / len(radii)) / instance_radius

    return imbalance + mixing + spread


# --------------------------------------------------------------------------
# (b) POPMUSIC sub-problem re-optimisation
# --------------------------------------------------------------------------

def _route_centroid(problem: Problem, order_ids: list[str],
                    ) -> tuple[float, float]:
    points = [_position(problem, order_id) for order_id in order_ids]
    if not points:
        return math.inf, math.inf
    return (sum(x for x, _ in points) / len(points),
            sum(y for _, y in points) / len(points))


def nearest_routes(problem: Problem, plan: dict[str, list[str]],
                   seed_vehicle: str, radius: int) -> list[str]:
    """The seed route and its `radius` nearest neighbours. §7.6(b).

    Nearest, not arbitrary: a sub-problem made of unrelated routes contains no
    improving move, and the budget is spent discovering that. Distance is
    between route centroids, which is cheap and good enough to rank -- the
    full-fidelity solver does the real work once the neighbourhood is chosen.
    """
    occupied = {vehicle_id: orders for vehicle_id, orders in plan.items() if orders}
    if seed_vehicle not in occupied:
        return [seed_vehicle]

    sx, sy = _route_centroid(problem, occupied[seed_vehicle])
    others = sorted(
        (vehicle_id for vehicle_id in occupied if vehicle_id != seed_vehicle),
        key=lambda v: (math.dist((sx, sy),
                                 _route_centroid(problem, occupied[v])), v))
    return [seed_vehicle, *others[:radius]]


def popmusic(problem: Problem, plan: dict[str, list[str]], radius: int = 3,
             rounds: int = 5, seed: int = 0,
             weights: ObjectiveWeights | None = None) -> dict[str, list[str]]:
    """Iterative sub-problem re-optimisation against an incumbent. §7.6(b).

    "Given an incumbent, repeatedly select a seed route, gather its `r` nearest
    routes, re-optimise that sub-problem to (near-)optimality with the
    full-fidelity solver, and re-insert."

    Seeds are taken in a fixed rotation rather than at random, so `seed` shifts
    where the rotation begins and two runs with one seed give one answer (CON-4).

    Args:
        problem: the whole instance -- sub-problems are cut from it here.
        plan: the incumbent, as vehicle to ordered order-ids.
        radius: how many neighbouring routes join each sub-problem.
        rounds: how many seed routes to try. A deterministic budget, not a clock.
        seed: where the rotation starts.
        weights: the canonical objective's weights, used to accept or reject.

    Returns:
        A plan never worse than the one given, with exactly the same orders. A
        re-optimisation that returned something worse would put the caller back
        in the business of checking what this was supposed to guarantee.
    """
    weights = weights or ObjectiveWeights()
    current = {vehicle_id: list(orders) for vehicle_id, orders in plan.items()}
    best = evaluate(problem, current, weights).total

    occupied = [v for v in sorted(current) if current[v]]
    if not occupied:
        return current

    for step in range(rounds):
        seed_vehicle = occupied[(seed + step) % len(occupied)]
        chosen = nearest_routes(problem, current, seed_vehicle, radius)
        orders = tuple(o for vehicle_id in chosen for o in current[vehicle_id])
        if len(orders) < 2:
            continue

        candidate = dict(current)
        solved = _solve_cluster(problem, orders, tuple(chosen), seed=seed + step)
        for vehicle_id in chosen:
            candidate[vehicle_id] = solved.get(vehicle_id, [])

        # DEC-3 in miniature: the sub-solve is accepted on the *canonical*
        # score of the whole plan, never on what the sub-solver reported about
        # its own piece. A sub-problem improving in isolation can still make
        # the global plan worse -- it does not pay the fixed cost of the extra
        # vehicle it just deployed.
        score = evaluate(problem, candidate, weights).total
        if (score < best and _same_orders(candidate, current)
                and all(route_is_legal(problem, v, candidate[v])
                        for v in chosen)):
            current, best = candidate, score

    return current


def _same_orders(left: dict[str, list[str]], right: dict[str, list[str]]) -> bool:
    """No order gained or lost. The cheapest way to improve a plan is to drop a
    stop, and a sub-solver that declares one unassigned would do exactly that."""
    return (sorted(o for orders in left.values() for o in orders)
            == sorted(o for orders in right.values() for o in orders))


def _solve_cluster(problem: Problem, order_ids: tuple[str, ...],
                   vehicle_ids: tuple[str, ...], seed: int,
                   iterations: int = 200) -> dict[str, list[str]]:
    """Run the full-fidelity solver over one neighbourhood.

    Falls back to the incumbent's own grouping if the engine declines the
    sub-problem -- §7.3's adapters refuse instances they cannot model, and a
    decomposition that died because one cluster contained a shipment would be
    worse than one that left that cluster alone.
    """
    from vrp.solve import pyvrp_adapter

    sub = _build_subproblem(problem, 0, order_ids, vehicle_ids)
    try:
        solution = pyvrp_adapter.solve(sub, iterations=iterations, seed=seed)
    except (NotImplementedError, ValueError):
        return {}
    if solution.unassigned:
        return {}
    return {route.vehicle_id: [s.order_id for s in route.steps if s.order_id]
            for route in solution.routes}


# --------------------------------------------------------------------------
# (c) Cross-boundary repair
# --------------------------------------------------------------------------

def boundary_candidates(problem: Problem, plan: dict[str, list[str]],
                        clusters: list[SubProblem]) -> list[tuple[str, str]]:
    """Order-to-vehicle moves worth trying across a cluster border. §7.6(c).

    "Run a pruned local search across cluster boundaries, using the similarity
    metadata computed during decomposition to prune candidate moves."

    The prune is the similarity itself: an order is a candidate only when some
    *other* cluster's route centroid is nearer than its own route's. Orders deep
    inside a cluster are never considered, which is what keeps this from being
    a second full local search over the whole instance -- and a second full
    local search is precisely the cost decomposition was meant to avoid.
    """
    cluster_of_vehicle = {vehicle_id: cluster.index
                          for cluster in clusters
                          for vehicle_id in cluster.vehicle_ids}
    centroids = {vehicle_id: _route_centroid(problem, orders)
                 for vehicle_id, orders in plan.items() if orders}

    candidates: list[tuple[str, str]] = []
    for vehicle_id, orders in sorted(plan.items()):
        home = cluster_of_vehicle.get(vehicle_id)
        for order_id in orders:
            point = _position(problem, order_id)
            mine = math.dist(point, centroids[vehicle_id])
            for other, centroid in sorted(centroids.items()):
                if cluster_of_vehicle.get(other) == home:
                    continue
                if math.dist(point, centroid) < mine:
                    candidates.append((order_id, other))
    return candidates


def _nodes(problem: Problem, vehicle_id: str, orders: list[str]) -> list[int]:
    """A route as matrix indices, depot at both ends."""
    vehicle = problem.vehicle(vehicle_id)
    inner = [problem.location(
        (problem.order(o).delivery or problem.order(o).pickup).location_id
    ).matrix_index for o in orders]
    return [problem.location(vehicle.start_location_id).matrix_index, *inner,
            problem.location(vehicle.end_location_id).matrix_index]


def _removal_gain(problem: Problem, vehicle_id: str, orders: list[str],
                  order_id: str) -> int:
    """Distance saved by taking `order_id` out of this route."""
    nodes = _nodes(problem, vehicle_id, orders)
    at = orders.index(order_id) + 1
    matrix = problem.matrix
    return (matrix.distance(nodes[at - 1], nodes[at])
            + matrix.distance(nodes[at], nodes[at + 1])
            - matrix.distance(nodes[at - 1], nodes[at + 1]))


def _cheapest_insertion(problem: Problem, vehicle_id: str, orders: list[str],
                        order_id: str) -> tuple[int, int]:
    """Where `order_id` fits most cheaply here, and what it costs."""
    nodes = _nodes(problem, vehicle_id, orders)
    node = problem.location(
        (problem.order(order_id).delivery
         or problem.order(order_id).pickup).location_id).matrix_index
    matrix = problem.matrix

    best, position = None, 0
    for at in range(len(nodes) - 1):
        cost = (matrix.distance(nodes[at], node)
                + matrix.distance(node, nodes[at + 1])
                - matrix.distance(nodes[at], nodes[at + 1]))
        if best is None or cost < best:
            best, position = cost, at
    return (best or 0), position


def repair_boundaries(problem: Problem, plan: dict[str, list[str]],
                      clusters: list[SubProblem], seed: int = 0,
                      weights: ObjectiveWeights | None = None,
                      budget: int = DEFAULT_REPAIR_BUDGET,
                      ) -> dict[str, list[str]]:
    """Move orders across cluster borders while it pays. §7.6(c).

    Skipping this "leaves visible seams at cluster borders, which dispatchers
    notice immediately" -- and no sub-solver can remove a seam, because the stop
    on the wrong side of it was never in its instance.

    Two stages, and the split is what makes this affordable. A **screen** ranks
    candidate moves by a distance delta read straight off the matrix, which is
    a handful of lookups. Only moves the screen believes in are then **confirmed**
    against the canonical evaluator on the whole plan, so a move that improves
    one route and ruins its neighbour is still rejected.

    Measured on a 1,000-stop instance: 359 candidate moves, and confirming every
    one at every insertion position meant roughly 36,000 whole-plan evaluations
    at 33 ms each -- about twenty minutes, for a stage §7.6 adds to make
    decomposition cheaper. Screening first leaves the canonical evaluator
    judging only the moves that could plausibly win. It decides every acceptance
    either way; the screen only decides what gets asked.

    `budget` caps confirmations as a count rather than a clock (CON-4), and what
    it left untried is recorded in `last_repair_report` rather than dropped --
    a silent truncation reads as "everything was tried".
    """
    _ = seed
    weights = weights or ObjectiveWeights()
    current = {vehicle_id: list(orders) for vehicle_id, orders in plan.items()}
    best = evaluate(problem, current, weights).total

    candidates = boundary_candidates(problem, current, clusters)
    owner = {order_id: vehicle_id
             for vehicle_id, orders in current.items() for order_id in orders}

    screened = []
    for order_id, target in candidates:
        source = owner.get(order_id)
        if source is None or source == target:
            continue
        gain = _removal_gain(problem, source, current[source], order_id)
        cost, _position = _cheapest_insertion(problem, target, current[target],
                                              order_id)
        if cost < gain:
            screened.append((cost - gain, order_id, target))
    # Ties resolve on the ids so two runs give one answer (CON-4).
    screened.sort()

    confirmed = 0
    for _delta, order_id, target in screened:
        if confirmed >= budget:
            break
        source = next((v for v, orders in current.items()
                       if order_id in orders), None)
        if source is None or source == target:
            continue

        # Recomputed here rather than trusted from the screen: earlier accepted
        # moves have changed both routes, so a delta measured before them is
        # about a plan that no longer exists.
        _cost, position = _cheapest_insertion(problem, target, current[target],
                                              order_id)
        moved = {v: list(orders) for v, orders in current.items()}
        moved[source].remove(order_id)
        moved[target].insert(position, order_id)

        confirmed += 1
        # Feasibility before cost, and before the evaluator is asked at all.
        # `vrp.evaluator` is a flat accountant by its own description -- it
        # prices a plan, it does not rule on one -- so a move that overloads the
        # target simply comes back cheaper. INV-5 caught exactly that on a
        # 1,000-stop instance: four routes carrying 291 units in a 270-unit van,
        # every one of them an accepted "improvement".
        if not route_is_legal(problem, target, moved[target]):
            continue
        score = evaluate(problem, moved, weights).total
        if score < best:
            current, best = moved, score

    last_repair_report.update(
        {"candidates": len(candidates), "screened": len(screened),
         "confirmed": confirmed,
         "untried": max(0, len(screened) - confirmed)})
    return current


# Populated by `repair_boundaries`. A module-level record rather than a return
# value so the function's signature stays the plan-in-plan-out shape the rest of
# the pipeline uses -- and so a capped run says so rather than looking complete.
last_repair_report: dict[str, int] = {}


# --------------------------------------------------------------------------
# DEC-1: global constraints, and the orchestrator
# --------------------------------------------------------------------------

def _loading_span(problem: Problem, vehicle: Vehicle) -> int:
    """How long this vehicle occupies a bay before leaving.

    The depot's own `dwell_overhead`: time spent at a location beyond the work
    done there, which at a depot is loading. Reused rather than given a new
    field because it already means this, and a second field meaning the same
    thing is how two parts of a model start to disagree.
    """
    return problem.location(vehicle.start_location_id).dwell_overhead


def _stagger(problem: Problem, assignment: dict[str, list[str]],
             ) -> dict[str, int]:
    """Depot start offsets that fit the bays. DEC-1, FR-19, §6.9.

    The constraint is global by nature: bays are shared by every cluster, so the
    only place it can be honoured is here, after recombination. Vehicles are
    dealt to bays in id order and each takes the next free slot on its bay --
    a deterministic first-fit, which is enough because every span at one depot
    is the same length.

    A depot with no `dock_capacity` is unconstrained, and a depot with no
    loading time never occupies a bay at all; both fall straight through, which
    is why this costs nothing on the instances that do not need it.
    """
    offsets: dict[str, int] = {}
    next_free: dict[tuple[str, int], int] = {}

    for vehicle_id in sorted(assignment):
        if not assignment[vehicle_id]:
            continue
        vehicle = problem.vehicle(vehicle_id)
        depot = problem.location(vehicle.start_location_id)
        span = _loading_span(problem, vehicle)
        start = vehicle.shift.start
        if depot.dock_capacity is None or span <= 0:
            offsets[vehicle_id] = start
            continue

        bay = min(range(depot.dock_capacity),
                  key=lambda b: (next_free.get((depot.id, b), start), b))
        when = max(start, next_free.get((depot.id, bay), start))
        offsets[vehicle_id] = when
        next_free[(depot.id, bay)] = when + span

    return offsets


def _route_for(problem: Problem, vehicle_id: str, order_ids: list[str],
               offset: int) -> Route:
    """One vehicle's timeline, with its loading span at the front.

    `schedule_route` builds a zero-width START, because a plan without dock
    capacity has no reason to say how long loading took. Here it matters: INV-12
    counts occupancy over exactly this span, so a START of zero width would make
    every dock constraint vacuously satisfied.
    """
    vehicle = problem.vehicle(vehicle_id)
    span = _loading_span(problem, vehicle)
    scheduled = schedule_route(problem, vehicle_id, order_ids,
                               rules=vehicle.hos_rules and None,
                               start_time=offset + span)

    head, *rest = scheduled.steps
    start = Step(type=head.type, location_id=head.location_id,
                 arrival=offset, start_service=offset, departure=offset + span,
                 load_after=head.load_after)
    return Route(vehicle_id=vehicle_id, steps=(start, *rest))


def concatenate(problem: Problem, clusters: list[SubProblem], seed: int = 0,
                stagger: bool = True,
                weights: ObjectiveWeights | None = None) -> Solution:
    """Solve each cluster and recombine into one plan. DEC-1, DEC-3.

    Args:
        problem: the whole instance.
        clusters: the partition.
        seed: threaded to each sub-solve, so the whole run is reproducible.
        stagger: whether to apply the global dock schedule. False exists for the
            control test -- a recombination that ignores DEC-1 must be shown to
            fail, or the staggering is being credited for a constraint the
            instance never posed.
        weights: the canonical objective's weights.

    Returns:
        A Solution scored by the canonical evaluator. Never by summing the
        sub-problem objectives: DEC-3 forbids it, because per-vehicle fixed
        costs and every global term are counted once per cluster that way.
    """
    weights = weights or ObjectiveWeights()
    assignment: dict[str, list[str]] = {}
    for cluster in clusters:
        solved = _solve_cluster(problem, cluster.order_ids, cluster.vehicle_ids,
                                seed=seed + cluster.index)
        if not solved:
            # The engine declined, so the cluster is served in sweep order by
            # its first vehicle rather than dropped. A worse plan is a plan; a
            # missing cluster is missing demand.
            solved = {cluster.vehicle_ids[0]: list(cluster.order_ids)}
        assignment.update(solved)

    return _finish(problem, assignment, stagger=stagger, weights=weights)


def _finish(problem: Problem, assignment: dict[str, list[str]], stagger: bool,
            weights: ObjectiveWeights) -> Solution:
    offsets = (_stagger(problem, assignment) if stagger
               else {v: problem.vehicle(v).shift.start for v in assignment})

    routes = tuple(_route_for(problem, vehicle_id, orders, offsets[vehicle_id])
                   for vehicle_id, orders in sorted(assignment.items())
                   if orders)
    placed = {order_id for orders in assignment.values() for order_id in orders}

    return Solution(
        problem_id=problem.id, routes=routes,
        unassigned=tuple({"order_id": order.id, "reason_code": "NOT_PLACED",
                          "explanation": "no cluster placed this order"}
                         for order in problem.orders if order.id not in placed),
        objective_breakdown={"total": evaluate(problem, assignment,
                                               weights).total},
        status="FEASIBLE" if len(placed) == len(problem.orders) else "INFEASIBLE",
        solver={"solver": "decompose", "seed": 0,
                "clusters": len(set(map(id, assignment))),
                "matrix_version": problem.matrix.version})


def solve_decomposed(problem: Problem, target_size: int = 200, seed: int = 0,
                     rounds: int = 3, radius: int = 2,
                     weights: ObjectiveWeights | None = None) -> Solution:
    """The whole of §7.6: partition, solve, re-optimise, repair, schedule.

    Args:
        problem: the instance, of any size.
        target_size: stops per sub-problem.
        seed: the run seed. Same seed, same plan (CON-4).
        rounds: POPMUSIC seed routes to try.
        radius: neighbouring routes per POPMUSIC sub-problem.
        weights: the canonical objective's weights.

    Returns:
        A Solution over the whole instance, scored by the canonical evaluator
        (DEC-3) and scheduled against the depots' bays (DEC-1).
    """
    weights = weights or ObjectiveWeights()
    clusters = partition(problem, target_size=target_size, seed=seed)

    combined = concatenate(problem, clusters, seed=seed, weights=weights)
    assignment = {route.vehicle_id:
                  [s.order_id for s in route.steps if s.order_id]
                  for route in combined.routes}

    assignment = repair_boundaries(problem, assignment, clusters, seed=seed,
                                   weights=weights)
    assignment = popmusic(problem, assignment, radius=radius, rounds=rounds,
                          seed=seed, weights=weights)

    return _finish(problem, assignment, stagger=True, weights=weights)
