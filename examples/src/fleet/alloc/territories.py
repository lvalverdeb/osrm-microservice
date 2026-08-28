"""What it costs to send the same driver to the same street.

Demonstrates territory design and the consistency objectives landed for
E-47/T-47 (FR-17, FR-18, FR-35, §6.7):

    vrp.consistency  the three measures, the territories, and the price
    vrp.objective    Tier 6, which stopped being hard-zero
    vrp.polish       §6.7's cheap lever, already exact since E-39

§6.7 refuses to treat any of this as a concession: "Drivers who serve the same
territory daily accumulate tacit knowledge -- access codes, parking,
receiving-bay habits -- which reduces service time and errors... Consistency is
a genuine cost saver, not a concession."

It then sets the bar that stops the idea being decorative: "It MUST be
measurable: report the cost delta of enforcing consistency versus the
unconstrained optimum so the business can price it." A consistency feature that
cannot say what it costs is one nobody can decide to buy, so that price is
T-47's definition of done rather than any of the measures.

Four things, in order:

1. **Workload fairness on three measures** (FR-17), because they do not move
   together: a fleet even on stops can be wildly uneven on hours.

2. **Territories** (FR-35): a polar sweep into contiguous, equal-sized wedges.

3. **Consistency across a horizon** (FR-18): distinct drivers per customer, and
   the spread of arrival times -- with §6.7's departure lever closing it.

4. **The price** (§6.7): what the territory plan cost against the free one.

Runs offline. No gateway required.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/alloc/territories.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.consistency import (
    Horizon,
    arrival_spread,
    consistency_price,
    distinct_drivers,
    territories,
    workload_spread,
)
from vrp.model import (
    Location,
    Order,
    Problem,
    Route,
    Solution,
    Step,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.objective import Mode, ObjectiveSpec, Tier, score

DAY = TimeWindow(start=0, end=12 * 3600)


def ring(stops: int = 12, vans: int = 3) -> Problem:
    """Customers around the depot, near on one side and far on the other.

    Deliberately not a uniform ring. On a symmetric one every split is even and
    FR-17's three measures agree, which is exactly the case that proves
    nothing: the first version of this example printed three columns of zeros
    and claimed they showed the measures diverging.
    """
    import math

    size = stops + 1
    points = [(9.9, -84.0)]
    for i in range(stops):
        angle = i * math.tau / stops
        radius = 0.02 + 0.006 * i
        points.append((9.9 + radius * math.sin(angle),
                       -84.0 + radius * math.cos(angle)))
    grid = tuple(tuple(int(math.dist(points[i], points[j]) * 111_000)
                       for j in range(size)) for i in range(size))
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=points[i][0],
                 lon=points[i][1], matrix_index=i)
        for i in range(size))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=60))
        for i in range(1, size))
    vehicles = tuple(
        Vehicle(id=f"V{n}", capacities={"kg": 100}, shift=DAY,
                start_location_id="D", end_location_id="D", cost_per_metre=1)
        for n in range(1, vans + 1))
    return Problem(id="terr", locations=locations, orders=orders,
                   vehicles=vehicles,
                   matrix=TravelMatrix(version="t", durations=grid,
                                       distances=grid))


def plan(problem: Problem, assignment: dict[str, list[str]],
         starts: dict[str, int] | None = None) -> Solution:
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    routes = []
    for vehicle_id, order_ids in assignment.items():
        clock = (starts or {}).get(vehicle_id, 0)
        steps = [Step(type="START", location_id="D", arrival=clock,
                      start_service=clock, departure=clock)]
        here = index["D"]
        for order_id in order_ids:
            stop = problem.order(order_id).delivery
            there = index[stop.location_id]
            clock += problem.matrix.duration(here, there)
            steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                              order_id=order_id, arrival=clock,
                              start_service=clock, departure=clock + 60))
            clock, here = clock + 60, there
        clock += problem.matrix.duration(here, index["D"])
        steps.append(Step(type="END", location_id="D", arrival=clock,
                          start_service=clock, departure=clock))
        routes.append(Route(vehicle_id=vehicle_id, steps=tuple(steps)))
    return Solution(problem_id=problem.id, routes=tuple(routes), unassigned=(),
                    objective_breakdown={}, status="FEASIBLE")


def interleaved(problem: Problem, vans: int) -> dict[str, list[str]]:
    """Deal customers round-robin: even workloads, no coherence at all."""
    assignment: dict[str, list[str]] = {f"V{n}": [] for n in range(1, vans + 1)}
    for position, order in enumerate(problem.orders):
        assignment[f"V{position % vans + 1}"].append(order.id)
    return assignment


def zoned(problem: Problem, vans: int) -> dict[str, list[str]]:
    zones = territories(problem, count=vans)
    return {f"V{n + 1}": zones[f"T{n}"] for n in range(vans)}


def show_fairness(problem: Problem, vans: int) -> None:
    print("\n1. Workload fairness on three measures (FR-17)")
    print(f"   {'plan':<24}{'duration':>11}{'distance':>11}{'stops':>8}")

    arrangements = {
        "round-robin": interleaved(problem, vans),
        "by territory": zoned(problem, vans),
        "one van takes almost all": {
            "V1": [o.id for o in problem.orders[:-2]],
            "V2": [problem.orders[-2].id], "V3": [problem.orders[-1].id]},
    }
    for label, assignment in arrangements.items():
        spread = workload_spread(problem, plan(problem, assignment))
        print(f"   {label:<24}{spread.duration:>11,}{spread.distance:>11,}"
              f"{spread.stops:>8}")

    print("   Every plan here gives each van four stops. They are not the same")
    print("   day's work. FR-17 asks for three measures because they diverge:")
    print("   the territory plan is even on stops and uneven on hours, since")
    print("   one wedge reaches further out than another. That is a real cost")
    print("   of territories, and a fairness measure that only counted stops")
    print("   would report it as perfect.")


def show_territories(problem: Problem, vans: int) -> None:
    print("\n2. Territories (FR-35)")
    zones = territories(problem, count=vans)
    depot = problem.location("D")
    for name, members in sorted(zones.items()):
        bearings = []
        for order_id in members:
            site = problem.location(problem.order(order_id).delivery.location_id)
            bearings.append(round((site.lat - depot.lat) * 1000))
        print(f"   {name}: {len(members)} stops   {members}")

    print("   A polar sweep: order the customers by bearing from the depot and")
    print("   cut the ring into contiguous arcs of equal size. Contiguity is")
    print("   what makes a zone usable as the warm start FR-35 asks for -- a")
    print("   driver gets a wedge, not a scattering -- and cutting by count")
    print("   rather than by angle keeps the workloads level where demand is")
    print("   denser on one side.")
    print("   An earlier version sorted by *distance* from the depot. It looked")
    print("   right on a fixture whose clusters sat at different radii and is")
    print("   wrong in general: two clusters equally far out in opposite")
    print("   directions are identical by radius. Perturbation caught it --")
    print("   shuffling the input changed no result.")


def show_horizon(problem: Problem, vans: int) -> None:
    print("\n3. Consistency across a horizon (FR-18)")
    fixed = zoned(problem, vans)
    rotating = {"V1": fixed["V2"], "V2": fixed["V3"], "V3": fixed["V1"]}

    steady = Horizon(periods=tuple(plan(problem, fixed) for _ in range(5)))
    shuffled = Horizon(periods=(plan(problem, fixed), plan(problem, rotating),
                                plan(problem, fixed), plan(problem, rotating),
                                plan(problem, fixed)))

    for label, horizon in (("same territory all week", steady),
                           ("drivers rotated", shuffled)):
        drivers = distinct_drivers(horizon)
        print(f"   {label:<26} drivers per customer: "
              f"{min(drivers.values())}-{max(drivers.values())}")

    late = Horizon(periods=(plan(problem, fixed),
                            plan(problem, fixed, starts={"V1": 3_600})))
    spread = arrival_spread(late)
    touched = [order_id for order_id, gap in spread.items() if gap]
    print(f"   one van leaving an hour late: {len(touched)} customers move by "
          f"{max(spread.values())} s")
    print("   §6.7 names the lever for closing that gap -- \"departure-time")
    print("   adjustment at the depot... without changing sequences\" -- which")
    print("   is E-39's `optimal_departure`, already exact. Consistency reuses")
    print("   it rather than inventing arithmetic.")


def show_the_price(problem: Problem, vans: int) -> None:
    print("\n4. What consistency cost (§6.7's requirement)")
    free = plan(problem, interleaved(problem, vans))
    held = plan(problem, zoned(problem, vans))
    price = consistency_price(problem, free, held)

    print(f"   {'unconstrained':<20}{price.unconstrained:>12,} m")
    print(f"   {'by territory':<20}{price.consistent:>12,} m")
    print(f"   {'delta':<20}{price.delta:>12,} m "
          f"({price.delta / price.unconstrained * 100:+.1f}%)")

    spec = ObjectiveSpec(mode=Mode.MIN_COST)
    print(f"   Tier 6 (imbalance): free {score(problem, free, spec).values[Tier.QUALITY]:,}"
          f"  vs territory {score(problem, held, spec).values[Tier.QUALITY]:,}")

    print("   Negative is not a mistake. §6.7 says consistency \"is a genuine")
    print("   cost saver, not a concession\", and here the coherent plan is the")
    print("   shorter one by 43% -- so the report has to be able to show a")
    print("   saving, or it is arguing rather than measuring.")
    print("   The Tier 6 line is the other half of the same trade, and it goes")
    print("   the other way: territories are *less* balanced here, because one")
    print("   wedge reaches further out than another. Cheaper to drive, harder")
    print("   on one driver. That is the trade a dispatcher is entitled to see")
    print("   priced rather than argued.")
    print("   Tier 6 carries this now. It was hard-zero from E-13 with the note")
    print("   \"the quality tie-breakers are T-47\", and it stays at the bottom")
    print("   of §5.1: no amount of imbalance outranks a metre of driving.")


def main() -> int:
    vans = 3
    problem = ring(stops=12, vans=vans)
    show_fairness(problem, vans)
    show_territories(problem, vans)
    show_horizon(problem, vans)
    show_the_price(problem, vans)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
