"""Randomised instance generator for property testing — SDD §11.1 L2, T-05.

§11.1's L2 level is "randomised instance generators + invariants INV-1..INV-9,
zero violations over 10⁵ generated cases". This module is the generator half.

Two properties matter more than realism:

**Reproducible.** Every instance is a pure function of its seed, so a failure
found on case 84,213 of a soak can be regenerated on its own and investigated.
A generator that cannot do that turns a found bug back into an unfound one.

**Actually hard.** A generator that only emits roomy instances would run ten
thousand cases and exercise nothing while reporting a green gate. `Shape` names
the axes that make routing bind -- capacity pressure, window tightness, depot
count, driving hours -- and `test_tight_shapes_actually_bind` checks the tight
shapes leave more orders unplaced than the slack one, so a branch that silently
stopped biting is caught.

The travel matrix is straight-line at a fixed speed, not road distance. That is
deliberate: this measures the model, the evaluator and the verifier against each
other, and a real matrix would make the results depend on map data as well.
Road-matrix behaviour is `E-10`'s subject.

Placement: Python. Test infrastructure for the domain model, off the request
path entirely.
"""

from __future__ import annotations

import math
import random
from enum import Enum

from vrp.matrix import PlanarMatrix
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)

DAY = TimeWindow(start=0, end=12 * 3600)
SPEED_M_PER_S = 30_000 / 3600      # 30 km/h, rounded to whole units below


class Shape(Enum):
    """The axes along which routing gets hard. One instance takes one shape."""

    SLACK = "SLACK"                      # room everywhere; the control
    TIGHT_CAPACITY = "TIGHT_CAPACITY"    # demand close to fleet capacity
    TIGHT_WINDOWS = "TIGHT_WINDOWS"      # narrow, staggered delivery windows
    MULTI_DEPOT = "MULTI_DEPOT"          # vehicles starting in different places
    DRIVING_HOURS = "DRIVING_HOURS"      # long legs under EU-561


def _coordinates(rng: random.Random, count: int,
                 spread_km: float) -> list[tuple[float, float]]:
    """Customer positions in kilometres about the origin."""
    return [(rng.uniform(-spread_km, spread_km), rng.uniform(-spread_km, spread_km))
            for _ in range(count)]


def _matrix(coords: list[tuple[float, float]]) -> TravelMatrix:
    """Straight-line travel, whole metres and whole seconds."""
    size = len(coords)
    distances, durations = [], []
    for origin in range(size):
        distance_row, duration_row = [], []
        for destination in range(size):
            if origin == destination:
                distance_row.append(0)
                duration_row.append(0)
                continue
            (ax, ay), (bx, by) = coords[origin], coords[destination]
            metres = round(math.hypot(ax - bx, ay - by) * 1000)
            distance_row.append(metres)
            duration_row.append(round(metres / SPEED_M_PER_S))
        distances.append(tuple(distance_row))
        durations.append(tuple(duration_row))
    return TravelMatrix(version="generated-v1", durations=tuple(durations),
                        distances=tuple(distances))


def generate_instance(seed: int, shape: Shape | None = None) -> Problem:
    """Build one instance. A pure function of `seed` and `shape`.

    With no shape, the seed picks one, so a plain sweep over seeds covers every
    axis rather than repeating the easiest.
    """
    rng = random.Random(seed)
    shape = shape or list(Shape)[seed % len(Shape)]

    customers = rng.randint(4, 14)
    depots = 3 if shape is Shape.MULTI_DEPOT else 1
    # Long legs are what make driving hours bite; everything else stays local.
    spread = 260.0 if shape is Shape.DRIVING_HOURS else 25.0

    depot_coords = [(0.0, 0.0)]
    depot_coords += [(rng.uniform(-spread, spread), rng.uniform(-spread, spread))
                     for _ in range(depots - 1)]
    coords = depot_coords + _coordinates(rng, customers, spread)

    locations = tuple(
        Location(id=f"D{index}" if index < depots else f"C{index}",
                 lat=9.9 + y / 100, lon=-84.0 + x / 100, matrix_index=index)
        for index, (x, y) in enumerate(coords)
    )
    matrix = _matrix(coords)

    shift = DAY if shape is not Shape.DRIVING_HOURS else TimeWindow(0, 24 * 3600)
    orders = []
    for index in range(depots, len(coords)):
        if shape is Shape.TIGHT_WINDOWS:
            # Two-hour windows staggered across the day: ordering matters, and
            # a route that visits in the wrong sequence cannot be repaired by
            # driving faster.
            opens = 3600 + (index % 4) * 2 * 3600
            windows = (TimeWindow(start=opens, end=opens + 2 * 3600),)
        else:
            windows = (shift,)
        orders.append(Order(
            id=f"O{index}", kind="JOB",
            quantities={"units": rng.randint(1, 9)},
            delivery=StopSpec(location_id=locations[index].id,
                              time_windows=windows,
                              service_fixed=rng.choice([300, 600, 900])),
        ))

    fleet = rng.randint(2, 4)
    demand = sum(order.quantities["units"] for order in orders)
    if shape is Shape.TIGHT_CAPACITY:
        # Just enough in total, so the assignment has to be right rather than
        # merely possible. Integer division rounds down, hence the +1: without
        # it the fleet is a unit or two short and every instance is infeasible,
        # which tests nothing but the unassigned path.
        capacity = demand // fleet + 1
    else:
        capacity = demand              # any one vehicle could take the lot

    vehicles = tuple(
        Vehicle(id=f"V{n}", capacities={"units": capacity}, shift=shift,
                start_location_id=locations[n % depots].id,
                end_location_id=locations[n % depots].id,
                hos_rules="EU-561" if shape is Shape.DRIVING_HOURS else None)
        for n in range(fleet)
    )
    return Problem(id=f"gen-{shape.value.lower()}-{seed}", locations=locations,
                   orders=tuple(orders), vehicles=vehicles, matrix=matrix)


def plan_greedily(problem: Problem) -> dict[str, list[str]]:
    """A plausible assignment, built nearest-neighbour under capacity.

    Not a solver and not pretending to be one. Its job is to produce a *legal*
    plan cheaply enough to run 10⁵ times, so the verifier has something to
    judge; plan quality is irrelevant here and is `E-16`'s subject.

    Orders that do not fit are left out rather than forced in. INV-1 counts
    `routes ∪ unassigned`, so an order the constructor cannot place is still
    accounted for -- and on the tight shapes that path is most of the point.

    Every candidate is checked against time windows and, where the vehicle
    declares a rule set, driving hours *before* it is accepted. The first
    version checked only capacity, which produced plans the verifier rightly
    rejected on INV-3 and INV-7 -- a constructor bug that would have read as a
    generator finding. A property harness whose plans are illegal by
    construction measures nothing.
    """
    matrix = problem.matrix
    index_of = {location.id: location.matrix_index for location in problem.locations}
    remaining = {order.id: order for order in problem.orders}
    assignment: dict[str, list[str]] = {}

    for vehicle in problem.vehicles:
        capacity = vehicle.capacities.get("units", 0)
        position = index_of[vehicle.start_location_id]
        carried, sequence = 0, []
        while remaining:
            affordable = [order for order in remaining.values()
                          if carried + order.quantities["units"] <= capacity]
            placed = False
            # Nearest first, but fall through to the next candidate when the
            # nearest cannot be served legally -- otherwise one awkward stop
            # ends the route while easy ones remain.
            for candidate in sorted(affordable, key=lambda order: matrix.duration(
                    position, index_of[(order.delivery or order.pickup).location_id])):
                if not _is_legal(problem, vehicle, [*sequence, candidate.id]):
                    continue
                sequence.append(candidate.id)
                carried += candidate.quantities["units"]
                position = index_of[
                    (candidate.delivery or candidate.pickup).location_id]
                del remaining[candidate.id]
                placed = True
                break
            if not placed:
                break
        assignment[vehicle.id] = sequence
    return assignment


def _is_legal(problem: Problem, vehicle: Vehicle, sequence: list[str]) -> bool:
    """Would this sequence serve every stop inside its window and the law?

    Delegates to `vrp.evaluator.route_is_legal`, which is where this predicate
    now lives: §7.6's cross-boundary repair needs the same question answered,
    and two copies of "is this route legal" is how two parts of a planner start
    to disagree about what legal means.
    """
    from vrp.evaluator import route_is_legal

    return route_is_legal(problem, vehicle.id, sequence)


def build_plan(problem: Problem) -> tuple[dict[str, list[str]], dict[str, tuple]]:
    """Assign orders and build each vehicle's timeline with the right builder.

    Vehicles under an hours-of-service rule set get their timeline from the HOS
    scheduler, so it carries the breaks the law requires; everything else goes
    through the canonical evaluator. Using one builder for both was the first
    version, and it produced plans that had been *validated* against driving
    hours and then *built* without the breaks -- so the verifier rejected them
    on INV-7, correctly, for a defect in the harness rather than the model.
    """
    from vrp.evaluator import build_timeline
    from vrp.hos.rules import rules_for
    from vrp.hos.schedule import schedule_route

    assignment = plan_greedily(problem)
    timelines: dict[str, tuple] = {}
    for vehicle_id, order_ids in assignment.items():
        if not order_ids:
            continue
        vehicle = problem.vehicle(vehicle_id)
        if vehicle.hos_rules:
            timelines[vehicle_id] = schedule_route(
                problem, vehicle_id, order_ids, rules_for(vehicle.hos_rules)).steps
        else:
            timelines[vehicle_id] = build_timeline(problem, vehicle_id, order_ids)
    return assignment, timelines


def generate_large_instance(seed: int, stops: int,
                            depots: int = 1) -> Problem:
    """A instance of arbitrary size, with a matrix that computes its own cells.

    `generate_instance` deliberately makes small instances -- four to fourteen
    customers, chosen so a case fits in a test and in a reader's head. §7.6
    starts where that ends: "above roughly 2,000-3,000 stops, monolithic search
    degrades", and NFR-01 wants 10,000 inside an hour.

    The difference is not only the count. A stored matrix for 10,000 stops is
    ~3.8 GB and about twelve minutes to build, so this returns a `PlanarMatrix`
    that computes cells on demand instead. The arithmetic is the same as
    `_matrix`'s, and a test pins the two together.

    Args:
        seed: the run seed. Same seed, same instance (CON-4).
        stops: how many customers.
        depots: how many depots the fleet starts from.

    Returns:
        A `Problem` whose fleet is sized so the instance is feasible with room
        to spare. Making it tight is `Shape.TIGHT_CAPACITY`'s job on small
        instances; here the point is scale, and an infeasible 10,000-stop
        instance would measure the unassigned path rather than decomposition.
    """
    rng = random.Random(seed)
    spread = max(10.0, stops ** 0.5)

    depot_coords = [(0.0, 0.0)]
    depot_coords += [(rng.uniform(-spread, spread), rng.uniform(-spread, spread))
                     for _ in range(depots - 1)]
    coords = depot_coords + _coordinates(rng, stops, spread)

    locations = tuple(
        Location(id=f"D{index}" if index < depots else f"C{index}",
                 lat=9.9 + y / 100, lon=-84.0 + x / 100, matrix_index=index)
        for index, (x, y) in enumerate(coords)
    )
    matrix = PlanarMatrix(version=f"generated-large-{seed}",
                          coordinates=tuple(coords))

    orders = tuple(
        Order(id=f"O{index}", kind="JOB",
              quantities={"units": rng.randint(1, 9)},
              delivery=StopSpec(location_id=locations[index].id,
                                time_windows=(DAY,),
                                service_fixed=rng.choice([60, 120, 180])))
        for index in range(depots, len(coords))
    )

    # Roughly twenty stops a vehicle, and capacity for thirty of the heaviest.
    # Deliberately generous: see the docstring.
    fleet = max(2, stops // 20)
    vehicles = tuple(
        Vehicle(id=f"V{n}", capacities={"units": 9 * 30}, shift=DAY,
                start_location_id=locations[n % depots].id,
                end_location_id=locations[n % depots].id)
        for n in range(fleet)
    )
    return Problem(id=f"large-{seed}-{stops}", locations=locations,
                   orders=orders, vehicles=vehicles, matrix=matrix)
