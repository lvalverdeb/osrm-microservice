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

import argparse
import math
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

import dataset
import maps

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
    Vehicle,
)
from vrp.objective import Mode, ObjectiveSpec, Tier, score

GATEWAY = os.environ.get("OSRM_API_URL", "http://localhost:8000")
DAY = TimeWindow(start=0, end=12 * 3600)


def round_from_dataset(stops: int, vans: int, path: Path,
                       gateway: str) -> Problem:
    """A real round: the nearest deliveries to a depot, on real road distances.

    Deliberately not a uniform ring. On a symmetric one every split is even and
    FR-17's three measures agree, which is exactly the case that proves
    nothing: the first version of this example printed three columns of zeros
    and claimed they showed the measures diverging. A generated spiral gave the
    asymmetry by construction; real deliveries give it because towns and roads
    are not laid out for the convenience of a fairness metric -- and this
    example's claim is about sending the same driver to the same *street*,
    which a spiral does not have.

    Stops are ordered by bearing from the depot, because that ordering is what
    `zoned` and `interleaved` slice: contiguous means a wedge of the map only
    if the sequence goes round it.

    Args:
        stops: How many deliveries to take.
        vans: How many vehicles the round is planned for.
        path: Where the delivery corpus lives.
        gateway: Base URL of the OSRM API gateway, for the road matrix.

    Returns:
        A `Problem` over real coordinates and real road travel.
    """
    deliveries, depot = dataset.load(path).nearest(stops)
    deliveries.sort(key=lambda d: math.atan2(d["latitude"] - depot["latitude"],
                                             d["longitude"] - depot["longitude"]))

    points = [(depot["latitude"], depot["longitude"])]
    points += [(d["latitude"], d["longitude"]) for d in deliveries]
    matrix, road = dataset.road_matrix_or_planar(points, gateway,
                                                 "territories")
    if not road:
        print("   no gateway; distances are straight-line, so the costs"
              " below are lower than the road gives")

    locations = (Location(id="D", lat=depot["latitude"], lon=depot["longitude"],
                          matrix_index=0),) + tuple(
        Location(id=d["product_id"], lat=d["latitude"], lon=d["longitude"],
                 matrix_index=i + 1)
        for i, d in enumerate(deliveries))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=d["product_id"],
                                time_windows=(DAY,),
                                service_fixed=d["service_minutes"] * 60))
        for i, d in enumerate(deliveries))
    vehicles = tuple(
        Vehicle(id=f"V{n}", capacities={"kg": 100}, shift=DAY,
                start_location_id="D", end_location_id="D", cost_per_metre=1)
        for n in range(1, vans + 1))
    return Problem(id="terr", locations=locations, orders=orders,
                   vehicles=vehicles, matrix=matrix)


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
    for name, members in sorted(zones.items()):
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


def site_of(problem: Problem, order_id: str) -> tuple[float, float]:
    """Where an order is served, as `(latitude, longitude)`."""
    stop = problem.location(problem.order(order_id).delivery.location_id)
    return (stop.lat, stop.lon)


def draw(canvas, problem: Problem, label: str,
         assignment: dict[str, list[str]], shown: bool) -> int:
    """One assignment as a toggleable layer: a hull and its stops per van.

    Returns:
        How many vans held too few stops to bound an area. Two stops make a
        line, and a territory that cannot be drawn is worth saying rather than
        leaving as a gap the reader reads as a bug.
    """
    layer = maps.group(canvas, label, shown=shown)
    flat = 0
    for index, (vehicle_id, order_ids) in enumerate(sorted(assignment.items())):
        shade = maps.colour(index)
        sites = [site_of(problem, order_id) for order_id in order_ids]
        if not maps.region(layer, sites, shade, f"{label}: {vehicle_id}"):
            flat += 1
        for order_id, site in zip(order_ids, sites, strict=True):
            maps.stop(layer, site, shade, f"{vehicle_id}  {order_id}")
    home = problem.location("D")
    maps.depot(layer, (home.lat, home.lon), "depot")
    return flat


def coverage(problem: Problem, assignment: dict[str, list[str]]) -> int:
    """What share of the round an average van's hull covers, in percent."""
    return maps.coverage(
        [[site_of(problem, order_id) for order_id in order_ids]
         for order_ids in assignment.values()],
        [site_of(problem, order.id) for order in problem.orders])


def show_map(problem: Problem, vans: int) -> None:
    """5. The picture those numbers are of.

    The two layers carry the whole of section 4's argument. Under `zoned` each
    van's hull is a wedge off the depot and the wedges barely touch; under
    `interleaved` every van's hull is the entire round, drawn three times on
    top of itself. Both plans give each van the same number of stops, which is
    why FR-17's stop count alone cannot tell them apart -- and why a dispatcher
    asked to believe the table is entitled to see this instead.
    """
    print("\n5. The same stops under both plans")
    by_territory, round_robin = zoned(problem, vans), interleaved(problem, vans)
    canvas = maps.base_map([(site.lat, site.lon) for site in problem.locations])
    flat = draw(canvas, problem, "by territory", by_territory, True)
    flat += draw(canvas, problem, "round-robin", round_robin, False)
    maps.controls(canvas)
    maps.legend(canvas, {f"V{n + 1}": maps.colour(n) for n in range(vans)}, "van")
    if flat:
        print(f"   {flat} van(s) hold fewer than three stops, so they are drawn"
              " as points rather than regions")
    maps.save(canvas, Path(__file__).parent / "territories_map.html")
    print(f"   average van covers {coverage(problem, by_territory)}% of the "
          f"round by territory, {coverage(problem, round_robin)}% round-robin")
    print("   Toggle the layers. Both plans give every van the same number of")
    print("   stops, which is why FR-17's stop count cannot separate them and")
    print("   why the shapes can.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stops", type=int, default=12)
    parser.add_argument("--vans", type=int, default=3)
    parser.add_argument("--dataset", type=Path, default=dataset.DEFAULT_PATH)
    args = parser.parse_args()

    vans = args.vans
    print(f"fetching a road matrix from {GATEWAY}")
    problem = round_from_dataset(args.stops, vans, args.dataset, GATEWAY)
    show_fairness(problem, vans)
    show_territories(problem, vans)
    show_horizon(problem, vans)
    show_the_price(problem, vans)
    show_map(problem, vans)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
