"""The frozen benchmark corpus — SDD §11.3, CON-9.

Instances are *specified*, not stored: a `Spec` plus a seed reproduces the same
problem byte for byte, so the corpus can live in a hundred lines rather than a
directory of data files, and a reviewer can see what is being measured.

Frozen means frozen. Changing a spec changes what every recorded baseline number
means, so a change here invalidates `benchmarks/BASELINE.md` and must come with
a re-record. The alternative — quietly editing an instance and comparing new
numbers to old ones — is the most effective way to fool yourself about progress.

The shapes deliberately vary along the axes that make routing hard: clustered
against scattered customers, slack against tight windows, and enough capacity
pressure to force multiple vehicles. A corpus of one shape measures one thing.

Public sets (Solomon, Gehring & Homberger, CVRPLIB, Li & Lim) arrive with
`T-06`, and with them gap-against-published-BKS. Until then this measures
regression against our own recorded numbers, which is what a CI gate needs.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

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


@dataclass(frozen=True)
class Spec:
    """Everything needed to rebuild one instance exactly."""

    name: str
    customers: int
    vehicles: int
    capacity: int
    seed: int
    clustered: bool
    tight_windows: bool


# Changing any row invalidates the recorded baseline. See the module docstring.
CORPUS: tuple[Spec, ...] = (
    Spec("c20-clustered-slack", customers=20, vehicles=4, capacity=50,
         seed=1001, clustered=True, tight_windows=False),
    Spec("c20-scattered-slack", customers=20, vehicles=4, capacity=50,
         seed=1002, clustered=False, tight_windows=False),
    Spec("c30-clustered-tight", customers=30, vehicles=5, capacity=45,
         seed=1003, clustered=True, tight_windows=True),
    Spec("c30-scattered-tight", customers=30, vehicles=5, capacity=45,
         seed=1004, clustered=False, tight_windows=True),
    # 8x40 against ~274 units of demand: about 86% utilised, so most of the
    # fleet is needed and the assignment matters. The first draft used 6
    # vehicles, which demanded 274 units from a 240-unit fleet -- impossible,
    # not merely pressured. The verifier refused it and the recorder refused to
    # write a baseline from it, which is the guard working; the corpus test
    # below now catches it earlier.
    Spec("c50-clustered-pressure", customers=50, vehicles=8, capacity=40,
         seed=1005, clustered=True, tight_windows=False),
)


def _points(spec: Spec, rng: random.Random) -> list[tuple[float, float]]:
    """Customer coordinates in kilometres from a depot at the origin."""
    if not spec.clustered:
        return [(rng.uniform(-25, 25), rng.uniform(-25, 25))
                for _ in range(spec.customers)]
    # Four clusters, which is what makes route structure matter: a solver that
    # ignores geography pays for it here and not on scattered points.
    centres = [(15, 15), (-15, 12), (-12, -16), (18, -14)]
    points = []
    for index in range(spec.customers):
        cx, cy = centres[index % len(centres)]
        points.append((cx + rng.gauss(0, 3), cy + rng.gauss(0, 3)))
    return points


def build_instance(spec: Spec) -> Problem:
    """Rebuild one corpus instance. Deterministic for a given spec."""
    rng = random.Random(spec.seed)
    coords = [(0.0, 0.0), *_points(spec, rng)]
    size = len(coords)

    locations = tuple(
        Location(id="DEPOT" if i == 0 else f"C{i}",
                 lat=9.9 + y / 100, lon=-84.0 + x / 100, matrix_index=i)
        for i, (x, y) in enumerate(coords)
    )

    # Straight-line travel at 30 km/h, rounded to whole metres and seconds.
    # Not road distance -- this corpus measures the solver, and a real matrix
    # would make the numbers depend on map data as well as on the search.
    def leg(a: int, b: int) -> tuple[int, int]:
        (ax, ay), (bx, by) = coords[a], coords[b]
        metres = round(math.hypot(ax - bx, ay - by) * 1000)
        return metres, round(metres / 30_000 * 3600)

    durations = tuple(tuple(0 if i == j else leg(i, j)[1] for j in range(size))
                      for i in range(size))
    distances = tuple(tuple(0 if i == j else leg(i, j)[0] for j in range(size))
                      for i in range(size))

    orders = []
    for index in range(1, size):
        if spec.tight_windows:
            # Two-hour windows staggered across the day, so ordering matters.
            opens = 3600 + (index % 5) * 2 * 3600
            window = TimeWindow(start=opens, end=opens + 2 * 3600)
        else:
            window = DAY
        orders.append(Order(
            id=f"O{index}", kind="JOB",
            quantities={"units": rng.randint(1, 9)},
            delivery=StopSpec(location_id=f"C{index}",
                              time_windows=(window,),
                              service_fixed=rng.choice([300, 600, 900])),
        ))

    vehicles = tuple(
        Vehicle(id=f"V{v}", capacities={"units": spec.capacity}, shift=DAY,
                start_location_id="DEPOT", end_location_id="DEPOT")
        for v in range(1, spec.vehicles + 1)
    )
    return Problem(id=spec.name, locations=locations, orders=tuple(orders),
                   vehicles=vehicles,
                   matrix=TravelMatrix(version=f"{spec.name}-v1",
                                       durations=durations, distances=distances))
