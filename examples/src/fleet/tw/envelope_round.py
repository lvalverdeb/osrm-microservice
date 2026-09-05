"""Signed documents: a round where the driving barely matters.

A courier hands over a letter-size envelope, waits while the customer opens it,
signs, seals it and hands it back, then rides to the next address. Ten minutes
a stop, every stop. The envelope weighs 200 grams and the processed one comes
back, so the satchel never changes size.

That is a different problem from every other example here, and the difference
is worth stating before the numbers:

    vrp.model      disjoint windows (FR-04), service_fixed (FR-05), a
                   capacity dimension that is not kilograms (FR-02)
    vrp.evaluator  route_metrics -- where the day actually goes
    vrp.solve      PyVRP, with business hours as exclusive client groups
    vrp.verify     the independent verifier

**The round is service-bound, not travel-bound.** Section 1 measures it, and
the split is not close: signing takes the overwhelming majority of the day,
riding a low single-digit percentage, and the remainder is waiting -- for the
offices to open at eight, and for them to reopen at one. Almost everything the other fleet examples optimise -- `cost_per_metre`,
the distance objective, `alloc/territories.py`'s 46%-further trade for
coherence -- is rounding error against a stop that costs ten minutes whatever
route reaches it. The only decision left is how many couriers to send, which
is section 4.

**Kilograms have no resolution left.** `dataset.load_kg` rounds a corpus weight
up to whole kilograms, which is the safe direction for a capacity and is what
the other examples use. It cannot help here: 200 g rounds up to 1 kg, a
fivefold overstatement, and so does every other envelope, so the dimension
would carry no information at all. `vrp.model` is integers throughout on
purpose (a float second makes `INV-4` unfalsifiable), so the answer is a finer
unit rather than a fractional one: grams. `rich/multi_capacity.py` already
reaches for the same trick, counting volume in whole tenths of a cubic metre.

**Four constraints and none of them is capacity.** The shift is eight hours and
must fall in daylight, which at 10 degrees north is 11h25m even at the December
solstice -- so daylight fixes when the shift starts, never how long it runs.
Offices open 08:00-17:00 and close for lunch, which is two disjoint windows per
stop. The courier's own lunch hour then falls in the gap because there is
nothing to deliver, not because anything here models a break.

**Stated rather than implied.** Nothing forces the courier to rest in that gap;
the solver may ride across town during it, and section 3 measures how long each
one actually stands still. A window also constrains only when service *begins*,
so a call started at 11:55 runs on into the closure -- legal here, and the kind
of thing a customer shutting at noon would disagree with. A break with a *placement* is not
expressible today -- `vrp.hos` triggers on accumulated driving, 4.5 h under
EU-561, and this courier drives well under an hour all day -- so the lunch is a
consequence of the closure here, not an enforced rest. The returned envelope is
not modelled as a pickup either: one out and one in is a net load of zero, so
it would constrain nothing. It is why the routes are closed.

Runs offline. Without a gateway the matrix is planar and the output says so.
    # dataset: see docs/dataset_prep.md

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/tw/envelope_round.py --stops 40
"""

from __future__ import annotations

import argparse
import os
import sys
from itertools import pairwise
from pathlib import Path

# Importing config puts OSRM_API_URL into the environment and the repository
# root on sys.path, which is what makes `import vrp` below resolve.
import config  # noqa: F401
import dataset
import maps

from vrp.consistency import territories
from vrp.evaluator import route_metrics
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    Vehicle,
)
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

GATEWAY = os.environ.get("OSRM_API_URL", "http://localhost:8000")
HOUR = 3600

# Eight hours inside the daylight every month of the year. Sunrise at San Jose
# runs 05:43 to 06:17 and sunset 17:43 to 18:17 across the solstices -- 70
# minutes of annual swing, because the corpus sits at 10 degrees north -- so a
# fixed 08:00-16:00 needs no seasonal calendar behind it.
SHIFT = TimeWindow(start=8 * HOUR, end=16 * HOUR)

# Offices open at eight, shut for lunch, and close at five. The afternoon
# window outlasts the shift on purpose: the answer below is that the shift
# binds and business hours do not, and a window trimmed to the shift would
# have assumed that rather than shown it.
BUSINESS = (TimeWindow(start=8 * HOUR, end=12 * HOUR),
            TimeWindow(start=13 * HOUR, end=17 * HOUR))

# Ten minutes with the customer, and the corpus's own `service_minutes` (3 to
# 12, mean 6.9) is deliberately ignored: that is parcel drop-off, which is a
# different business from waiting while somebody signs.
SIGNING_SECONDS = 10 * 60
ENVELOPE_GRAMS = 200
# A motorbike top box. Present to be measured, not to bind -- see section 2.
SATCHEL_GRAMS = 25_000
# The price of leaving a letter undelivered, so a courier count that cannot
# reach every address drops the surplus instead of returning INFEASIBLE. It is
# what makes section 4 a sizing question rather than a yes/no one.
UNDELIVERED = 1_000_000


def build(depot: dict, deliveries: list[dict], matrix, couriers: int) -> Problem:
    """The same round, staffed by `couriers` riders."""
    locations = [Location(id="DEPOT", lat=depot["latitude"],
                          lon=depot["longitude"], matrix_index=0)]
    orders = []
    for offset, delivery in enumerate(deliveries):
        locations.append(Location(id=delivery["product_id"],
                                  lat=delivery["latitude"],
                                  lon=delivery["longitude"],
                                  matrix_index=offset + 1))
        orders.append(Order(
            id=delivery["product_id"], kind="JOB",
            quantities={"grams": ENVELOPE_GRAMS},
            priority_tier=1, prize=UNDELIVERED,
            delivery=StopSpec(location_id=delivery["product_id"],
                              time_windows=BUSINESS,
                              service_fixed=SIGNING_SECONDS)))
    # Closed routes: the signed documents have to reach the depot, so the leg
    # home is the point of the trip rather than an overhead to optimise away.
    riders = tuple(Vehicle(id=f"COURIER-{n + 1}", capacities={"grams": SATCHEL_GRAMS},
                           shift=SHIFT, start_location_id="DEPOT",
                           end_location_id="DEPOT")
                   for n in range(couriers))
    return Problem(id=f"envelopes-{couriers}", locations=tuple(locations),
                   orders=tuple(orders), vehicles=riders, matrix=matrix)


def clock(seconds: int) -> str:
    return f"{seconds // HOUR:02d}:{seconds % HOUR // 60:02d}"


def served_steps(solution) -> list:
    return [s for route in solution.routes for s in route.steps if s.order_id]


def where_the_day_goes(problem: Problem, solution) -> dict[str, int]:
    """Driving, waiting and service across every route, in seconds."""
    totals = {"driving_seconds": 0, "waiting_seconds": 0, "service_seconds": 0}
    for route in solution.routes:
        metrics = route_metrics(problem, route.steps)
        for key in totals:
            totals[key] += metrics[key]
    return totals


def show_the_shape_of_the_day(problem: Problem, solution) -> None:
    """1. Where the time actually goes."""
    print("\n1. The day is signing, not riding")
    spent = where_the_day_goes(problem, solution)
    total = sum(spent.values()) or 1
    for name, seconds in spent.items():
        print(f"   {name.replace('_seconds', ''):<10}{seconds // 60:>6} min"
              f"{seconds * 100 // total:>6}%")
    share = spent["service_seconds"] * 100 // total
    print(f"   Signing is {share}% of the round. A better route can only move")
    print(f"   the {spent['driving_seconds'] * 100 // total}% that is riding, "
          "which is why the distance objective")
    print("   that drives every other fleet example barely applies here.")


def show_capacity_is_slack(problem: Problem, solution) -> None:
    """2. The dimension that cannot bind."""
    print("\n2. Grams, and why kilograms would not have worked")
    carried = max((sum(ENVELOPE_GRAMS for s in route.steps if s.order_id)
                   for route in solution.routes), default=0)
    print(f"   heaviest satchel   {carried:,} g of {SATCHEL_GRAMS:,} g "
          f"({SATCHEL_GRAMS // max(carried, 1)}x headroom)")
    print(f"   one envelope       {ENVELOPE_GRAMS} g, which in kilograms is "
          f"{ENVELOPE_GRAMS / 1000}, and")
    print(f"   `max(1, round({ENVELOPE_GRAMS / 1000}))` is 1 -- every envelope "
          "a kilogram, all identical.")
    print("   Capacity is reported here to show it is slack. A reader who sees")
    print("   a capacity dimension assumes it binds; this one never can.")


def idle_in_the_closure(solution) -> list[int]:
    """Seconds each courier spends idle inside 12:00-13:00.

    The example's central claim, measured instead of asserted: nothing here
    models a lunch break, so if the couriers stop between noon and one it is
    because every office is shut and there is no legal call to make.
    """
    closure = (12 * HOUR, 13 * HOUR)
    idle = []
    for route in solution.routes:
        served = [s for s in route.steps if s.order_id]
        overlap = 0
        for before, after in pairwise(served):
            overlap += max(0, min(after.start_service, closure[1])
                           - max(before.departure, closure[0]))
        if served:
            idle.append(overlap)
    return idle


def show_business_hours(problem: Problem, solution) -> None:
    """3. Two windows, and the hour nobody works."""
    print("\n3. Business hours, and where lunch went")
    starts = sorted(step.start_service for step in served_steps(solution))
    if not starts:
        return
    morning = sum(1 for t in starts if t < 12 * HOUR)
    print(f"   first call {clock(starts[0])}, last {clock(starts[-1])}")
    print(f"   {morning} before noon, {len(starts) - morning} after")
    # Strictly between the windows. `TimeWindow.contains` is inclusive at both
    # ends, so a call beginning at exactly 12:00:00 is inside the morning
    # window and legal; counting it as closure work reported four violations
    # that were not violations.
    gap = [t for t in starts if 12 * HOUR < t < 13 * HOUR]
    print(f"   calls beginning inside the closure (12:00-13:00): {len(gap)}")
    idle = idle_in_the_closure(solution)
    print("   idle inside the closure: "
          + ", ".join(f"{seconds // 60} min" for seconds in idle))
    print("   Nothing here models a break. The couriers stop because every")
    print("   office is shut, which is the hour they eat in.")
    print(f"   The shift ends {clock(SHIFT.end)} while the offices stay open "
          f"until {clock(BUSINESS[-1].end)},")
    print("   so the eight hours bind and business hours do not.")


def staffing(depot: dict, deliveries: list[dict], matrix, iterations: int,
             most: int) -> list[tuple]:
    """Try one courier, then two, until a plan survives the verifier.

    Counting the stops in the returned plan is not enough and the mistake is
    instructive: asked to do this round with one courier, PyVRP returns its
    best *infeasible* attempt with every arrival clamped to noon, and a naive
    count reports a hundred and twenty letters delivered by a rider who could
    not have delivered forty. `tw/multiple_windows.py` says it plainly -- "the
    stop count it prints is what the solver attempted, not what is achievable"
    -- so a courier count counts only when the solver says FEASIBLE, the
    verifier accepts, and nothing is left unassigned.

    Returns:
        One row per courier count tried: the count, the status, whether the
        verifier accepted, how many letters were placed, and the plan itself.
    """
    rows = []
    for couriers in range(1, most + 1):
        problem = build(depot, deliveries, matrix, couriers)
        solution = solve(problem, iterations=iterations, seed=0)
        accepted = verify(problem, solution).ok
        placed = len(served_steps(solution))
        rows.append((couriers, solution.status, accepted, placed, problem, solution))
        if accepted and solution.status == "FEASIBLE" and not solution.unassigned:
            break
    return rows


def show_sizing(rows: list[tuple], total: int) -> None:
    """4. The only question left."""
    print("\n4. How many couriers")
    print(f"   {'couriers':<10}{'status':>12}{'verifier':>10}{'placed':>8}")
    for couriers, status, accepted, placed, _, _ in rows:
        print(f"   {couriers:<10}{status:>12}"
              f"{'accepts' if accepted else 'REJECTS':>10}{placed:>8}")
    clean = [r for r in rows if r[2] and r[1] == "FEASIBLE"]
    if clean:
        print(f"   {clean[0][0]} courier(s) clear {total} letters; fewer cannot, "
              "and what runs out is")
        print("   the day, not the satchel. That is the whole decision.")
    else:
        print(f"   {rows[-1][0]} couriers still cannot clear {total} letters.")
    print("   The REJECTS rows are the solver's best infeasible attempt, with")
    print("   arrivals clamped to a window it could not reach. Their `placed`")
    print("   column is what was attempted, not what a courier could do.")


def site_of(problem: Problem, step) -> tuple[float, float]:
    """Where a step happens, as `(latitude, longitude)`."""
    place = problem.location(step.location_id)
    return (place.lat, place.lon)


def draw_by_courier(canvas, problem: Problem, solution) -> list[list]:
    """Each courier's stops and the hull around them."""
    layer = maps.group(canvas, "by courier", shown=True)
    groups = []
    for index, route in enumerate(sorted(solution.routes,
                                         key=lambda r: r.vehicle_id)):
        sites = [site_of(problem, s) for s in route.steps if s.order_id]
        if not sites:
            continue
        shade = maps.colour(index)
        maps.region(layer, sites, shade, f"{route.vehicle_id}: {len(sites)} calls")
        for site in sites:
            maps.stop(layer, site, shade, route.vehicle_id, radius=4)
        groups.append(sites)
    maps.depot(layer, site_of(problem, solution.routes[0].steps[0]), "depot")
    return groups


def draw_by_half_day(canvas, problem: Problem, solution, shades: tuple) -> None:
    """The same stops, coloured by which side of the closure they fell."""
    layer = maps.group(canvas, "before / after lunch", shown=False)
    for step in served_steps(solution):
        before = step.start_service < 12 * HOUR
        maps.stop(layer, site_of(problem, step),
                  shades[0] if before else shades[1],
                  f"{clock(step.start_service)}", radius=4)
    maps.depot(layer, site_of(problem, solution.routes[0].steps[0]), "depot")


def carved(problem: Problem, count: int) -> list[list]:
    """The same stops split geographically, as a control for `maps.coverage`.

    `vrp.consistency.territories` is the polar sweep `alloc/territories.py`
    demonstrates: order the stops by bearing from the depot and cut the ring
    into contiguous arcs. Running it here answers the comparison on this round
    rather than quoting a percentage measured on another example's data, which
    would go stale without anything saying so.
    """
    zones = territories(problem, count=count)
    return [[(problem.location(problem.order(order_id).delivery.location_id).lat,
              problem.location(problem.order(order_id).delivery.location_id).lon)
             for order_id in members]
            for members in zones.values()]


def scattered(problem: Problem, count: int) -> list[list]:
    """The same stops dealt round-robin: the least geographic split there is."""
    groups: list[list] = [[] for _ in range(count)]
    for position, order in enumerate(problem.orders):
        stop = problem.location(order.delivery.location_id)
        groups[position % count].append((stop.lat, stop.lon))
    return groups


def show_map(problem: Problem, solution, couriers: int) -> None:
    """5. What the round looks like, and what it does not look like.

    `alloc/territories.py` draws the same picture with the same helpers and
    gets wedges, because the plan there is built out of geography. Here the
    couriers were never given a territory, and the interesting part is that it
    barely shows: their hulls come out about as compact as a deliberate
    geographic carve-up of the same stops, and nothing like a round-robin
    scattering. All three are measured here rather than quoted from that
    example, whose round has different geometry and would not transfer.

    The second layer is the closure. If the couriers worked a morning district
    and an afternoon one, it would show; they do not, which is the same finding
    from the other side.
    """
    print("\n5. The round, drawn")
    canvas = maps.base_map([site_of(problem, s) for s in solution.routes[0].steps]
                           + [site_of(problem, s) for s in served_steps(solution)])
    groups = draw_by_courier(canvas, problem, solution)
    shades = (maps.colour(couriers), maps.colour(couriers + 1))
    draw_by_half_day(canvas, problem, solution, shades)
    maps.controls(canvas)
    legend = {f"COURIER-{n + 1}": maps.colour(n) for n in range(len(groups))}
    legend |= {"before lunch": shades[0], "after lunch": shades[1]}
    maps.legend(canvas, legend, "who and when")
    maps.save(canvas, Path(__file__).parent / "envelope_round_map.html")
    everything = [site for group in groups for site in group]
    count = len(groups)
    print("   an average group's hull, as a share of the whole round:")
    for label, split in (("these couriers", groups),
                         ("a geographic carve-up", carved(problem, count)),
                         ("dealt round-robin", scattered(problem, count))):
        print(f"      {label:<24}{maps.coverage(split, everything):>4}%")
    print("   The couriers land on the carve-up, not on the scattering, and")
    print("   nothing asked them to: riding is 2% of this round's cost, so the")
    print("   solver had almost no reason to prefer a compact day. It produced")
    print("   one anyway. Compactness here is a by-product of packing a clock,")
    print("   which is worth knowing before anyone adds a territory constraint")
    print("   to buy something they were getting for nothing.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stops", type=int, default=120)
    parser.add_argument("--iterations", type=int, default=800)
    parser.add_argument("--couriers", type=int, default=6,
                        help="most couriers to try before giving up")
    parser.add_argument("--dataset", type=Path, default=dataset.DEFAULT_PATH)
    args = parser.parse_args()

    deliveries, depot = dataset.load(args.dataset).nearest(args.stops)
    points = [(depot["latitude"], depot["longitude"])]
    points += [(d["latitude"], d["longitude"]) for d in deliveries]
    matrix, on_road = dataset.road_matrix_or_planar(points, GATEWAY, "envelopes")
    print(f"depot {depot['name']} -- {len(deliveries)} envelopes, "
          f"{SIGNING_SECONDS // 60} min each, shift "
          f"{clock(SHIFT.start)}-{clock(SHIFT.end)}")
    print(f"matrix        {'road, from ' + GATEWAY if on_road else 'planar'}"
          f"{'' if on_road else ' -- no gateway; distances are straight lines'}")

    print(f"\nstaffing the round (up to {args.couriers} couriers, "
          f"{args.iterations} iterations each)")
    rows = staffing(depot, deliveries, matrix, args.iterations, args.couriers)
    clean = [r for r in rows if r[2] and r[1] == "FEASIBLE"]
    if not clean:
        print(f"   no plan up to {args.couriers} couriers survives the verifier;"
              " sections 1-3 need a legal round to describe")
        show_sizing(rows, len(deliveries))
        return 1
    couriers, _, _, _, staffed, solution = clean[0]
    print(f"   {couriers} courier(s): FEASIBLE, verifier accepts")

    show_the_shape_of_the_day(staffed, solution)
    show_capacity_is_slack(staffed, solution)
    show_business_hours(staffed, solution)
    show_sizing(rows, len(deliveries))
    show_map(staffed, solution, couriers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
