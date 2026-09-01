"""Fifteen instances that break routing engines, and what this one answers.

Demonstrates the adversarial set of the real-world catalogue's §11
(`CAT-VRP-003`, `UC-060`…`UC-074`) — the instances that are "not customer
scenarios. These break implementations, and each has caused a production
incident somewhere."

    vrp.diagnose    pre-flight reason codes, before any search
    vrp.model       validation that refuses a malformed instance outright
    vrp.hos         the legal clock, carried in from a tachograph
    vrp.locks       the minimal conflicting lock set
    vrp.decompose   partitioning that does not assume a flat earth
    vrp.verify      the independent verifier, which has the last word

Every scenario in this catalogue asks for a good plan. These fifteen ask a
narrower and more useful question: when the input is broken, degenerate or
merely nasty, does the engine say something a dispatcher can act on?

Six themes, in order:

1. **Refused before the search.** An order no vehicle can lift, a stop no
   vehicle can reach, and a fleet that does not exist. Each has a named answer
   available in microseconds, and a quarter of an hour of search would produce
   a worse one.

2. **Broken data against tight data.** A window of zero width is an
   appointment. A window that closes before it opens is a corrupt record. An
   engine that answers "infeasible" to both has hidden a data-quality problem
   behind a plausible routing result.

3. **The clock.** Duty that runs past midnight, a driver who arrives with hours
   already spent, and a fleet sized by how much its work overlaps rather than
   by how much it weighs.

4. **What the totals hide.** The canonical capacity bug — a load that fits on
   paper and not in the van — and a pair of orders each legal alone.

5. **Degeneracy and scale.** Two hundred drops at one door, the smallest
   instance that is still a problem, and the size at which the engine changes
   its own algorithm.

6. **The world is not a plane.** A cluster straddling the antimeridian, and a
   matrix that stops arriving halfway through being built.

Two answers below are honest about being incomplete, and say so on the page
rather than in a footnote. That is the point of running this: the output is a
statement of what the engine does today, not of what it should do.

Runs offline. No gateway required — these instances are hand-built and tiny by
design, so that they can run on every commit.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/adversarial/pathological_instances.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.decompose import partition
from vrp.diagnose import preflight
from vrp.hos import EU_561, DriverState
from vrp.hos.schedule import schedule_route
from vrp.locks import minimal_conflict
from vrp.model import (
    UNREACHABLE,
    Location,
    Lock,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    ValidationError,
    Vehicle,
)
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

DAY = TimeWindow(start=0, end=12 * 3600)
HOUR = 3600


# --------------------------------------------------------------------------
# Builders. Deliberately minimal: the instance should fit on the screen beside
# the answer it produces.
# --------------------------------------------------------------------------

def grid(size: int, *, leg: int = 600, cut: set = frozenset()) -> TravelMatrix:
    rows = []
    for i in range(size):
        rows.append(tuple(
            UNREACHABLE if (i, j) in cut or (j, i) in cut
            else (0 if i == j else abs(i - j) * leg)
            for j in range(size)))
    return TravelMatrix(version="adv", durations=tuple(rows), distances=tuple(rows))


def sites(count: int) -> tuple[Location, ...]:
    return tuple(Location(id="D" if i == 0 else f"C{i}",
                          lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                 for i in range(count))


def drop(order_id: str, stop: str, *, windows=(DAY,), service=60, **qty) -> Order:
    return Order(id=order_id, kind="JOB", quantities=qty or {"kg": 1},
                 delivery=StopSpec(location_id=stop, time_windows=windows,
                                   service_fixed=service))


def van(vehicle_id="V1", **kwargs) -> Vehicle:
    base = {"capacities": {"kg": 100}, "shift": DAY,
            "start_location_id": "D", "end_location_id": "D"}
    return Vehicle(id=vehicle_id, **{**base, **kwargs})


def instance(orders, vehicles, *, matrix=None, locks=()) -> Problem:
    locations = sites(len(orders) + 1)
    return Problem(id="adv", locations=locations, orders=tuple(orders),
                   vehicles=tuple(vehicles), locks=tuple(locks),
                   matrix=matrix or grid(len(locations)))


def served(solution) -> set[str]:
    return {step.order_id for route in solution.routes
            for step in route.steps if step.order_id}


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def case(uc: str, title: str) -> None:
    print(f"\n  {uc}  {title}")


# --------------------------------------------------------------------------
# 1. Refused before the search
# --------------------------------------------------------------------------

def refused_before_the_search() -> None:
    heading("1.", "Refused before the search")
    print("\n   A pre-flight pass answers what no amount of searching would "
          "answer better.\n   §6.5: each reason \"MUST be produced by an "
          "explicit diagnostic pass, not\n   inferred\" -- a solver that failed "
          "knows only that it failed.")

    case("UC-060", "an order no vehicle can lift")
    problem = instance((drop("PALLET", "C1", kg=10_000), drop("PARCEL", "C2", kg=1)),
                       (van(capacities={"kg": 100}),))
    findings = preflight(problem)
    print(f"      PALLET  -> {findings['PALLET'].code}")
    print(f"      PARCEL  -> {'no finding' if 'PARCEL' not in findings else '?'}"
          "   (a routable order is not swept up with it)")

    case("UC-061", "a stop on an island, or behind a bollard")
    problem = instance((drop("ISLAND", "C1"),), (van(),),
                       matrix=grid(2, cut={(0, 1)}))
    print(f"      unreachable arc sentinel = {UNREACHABLE}, which is outside "
          "any real cost")
    print(f"      ISLAND  -> {preflight(problem)['ISLAND'].code}")
    print("      A large finite distance would be a number the optimiser trades")
    print("      against, so the stop gets planned and a driver finds the gate "
          "locked.")

    case("UC-071", "a fleet that does not exist")
    problem = instance((drop("O1", "C1"), drop("O2", "C2")), ())
    solution = solve(problem, iterations=100, seed=0)
    codes = {row["reason_code"] for row in solution.unassigned}
    print(f"      status={solution.status}  routes={len(solution.routes)}  "
          f"unassigned={len(solution.unassigned)} {sorted(codes)}")
    print("      A bank-holiday roster is a state of the world, not a malformed")
    print("      request. Until this example was written it raised ValueError.")


# --------------------------------------------------------------------------
# 2. Broken data against tight data
# --------------------------------------------------------------------------

def broken_against_tight() -> None:
    heading("2.", "Broken data against merely tight data")

    case("UC-062", "zero-width and inverted windows are not the same thing")
    noon = TimeWindow(start=6 * HOUR, end=6 * HOUR)
    problem = instance((drop("APPT", "C1", windows=(noon,)),), (van(),))
    solution = solve(problem, iterations=200, seed=0)
    print(f"      zero-width [06:00, 06:00] -> served={sorted(served(solution))}, "
          f"verified={verify(problem, solution).ok}")
    try:
        TimeWindow(start=7 * HOUR, end=5 * HOUR)
    except ValidationError as exc:
        print(f"      inverted  [07:00, 05:00] -> refused at construction: {exc}")
    print("      One is an appointment to hit exactly. The other is a record to")
    print("      go and fix. Answering \"infeasible\" to both loses that.")

    case("UC-068", "locks that contradict each other")
    locks = (Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1", vehicle_id="V1"),
             Lock(kind="FORBID_ORDER_ON_VEHICLE", order_id="O1", vehicle_id="V1"))
    problem = instance((drop("O1", "C1"),), (van("V1"), van("V2")), locks=locks)
    conflict = minimal_conflict(problem)
    print("      two vehicles, so neither lock conflicts alone; the pair does")
    for lock in conflict:
        print(f"      in the minimal set: {lock.kind} {lock.order_id}->{lock.vehicle_id}")
    print("      A dispatcher who pinned an order and finds it moved has no")
    print("      reason to trust the next plan either (CON-7).")


# --------------------------------------------------------------------------
# 3. The clock
# --------------------------------------------------------------------------

def the_clock() -> None:
    heading("3.", "The clock")

    case("UC-063", "a duty that runs past midnight")
    shift = TimeWindow(start=20 * HOUR, end=30 * HOUR)
    evening = TimeWindow(start=20 * HOUR, end=22 * HOUR)
    after = TimeWindow(start=25 * HOUR, end=26 * HOUR)
    problem = Problem(id="night", locations=sites(3),
                      orders=(drop("EVENING", "C1", windows=(evening,)),
                              drop("SMALLHOURS", "C2", windows=(after,))),
                      vehicles=(van(shift=shift),), matrix=grid(3))
    solution = solve(problem, iterations=300, seed=0)
    # `arrival` is when the van pulls up; `start_service` is when the window
    # lets it work. The gap is the wait, and it is the second number that has
    # to be allowed past 86,400.
    stops = [(s.order_id, s.arrival, s.start_service) for route in solution.routes
             for s in route.steps if s.order_id]
    for name, at, from_when in sorted(stops, key=lambda row: row[2]):
        print(f"      {name:11s} arrives {_hhmm(at)}, served {_hhmm(from_when)} "
              f"({from_when}s elapsed)")
    print("      Nothing here is a wall-clock time. Every instant is whole")
    print("      seconds from the horizon, so midnight is not a boundary and a")
    print("      repeated or missing DST hour cannot be represented at all.")

    case("UC-064", "a driver who is already four hours into the limit")
    long_day = TimeWindow(start=0, end=14 * HOUR)
    far = TravelMatrix(version="far", durations=((0, 4 * HOUR), (4 * HOUR, 0)),
                       distances=((0, 100_000), (100_000, 0)))
    problem = Problem(id="hos", locations=sites(2),
                      orders=(drop("O1", "C1", windows=(long_day,)),),
                      vehicles=(van(shift=long_day, hos_rules="EU-561"),),
                      matrix=far)
    for label, carried in (("rested", None),
                           ("4h already driven",
                            DriverState(drive_used=4 * HOUR, duty_used=4 * HOUR,
                                        since_last_break=4 * HOUR))):
        scheduled = schedule_route(problem, "V1", ["O1"], EU_561,
                                   initial_state=carried)
        breaks = [s for s in scheduled.steps if s.type == "BREAK"]
        when = breaks[0].start_service if breaks else None
        print(f"      {label:18s} -> first break at "
              f"{'none' if when is None else f'{when // 3600:02d}:{when % 3600 // 60:02d}'}"
              f"   ({breaks[0].rule_ref if breaks else '-'})")
    print("      Planning from a full clock builds every duty against nine hours")
    print("      the driver does not have, and the plan is illegal before the")
    print("      van leaves the yard.")

    case("UC-065", "everything due in the same hour")
    hour = TimeWindow(start=8 * HOUR, end=9 * HOUR)
    orders = tuple(drop(f"O{i}", f"C{i}", windows=(hour,)) for i in (1, 2, 3))
    spread = grid(4, leg=HOUR)
    for label, fleet in (("one van", (van("V1"),)),
                         ("three vans", tuple(van(f"V{i}") for i in (1, 2, 3)))):
        problem = Problem(id=label, locations=sites(4), orders=orders,
                          vehicles=fleet, matrix=spread)
        solution = solve(problem, iterations=400, seed=0)
        print(f"      {label:11s} -> status={solution.status:10s} "
              f"served={len(served(solution))}/3")
    print("      Fleet size follows window overlap, not volume. What must never")
    print("      happen is a feasible plan serving all three with one van --")
    print("      that would mean the window was quietly widened.")


# --------------------------------------------------------------------------
# 4. What the totals hide
# --------------------------------------------------------------------------

def what_the_totals_hide() -> None:
    heading("4.", "What the totals hide")

    case("UC-066", "a load that fits on paper and not in the van")
    morning = TimeWindow(start=8 * HOUR, end=9 * HOUR)
    afternoon = TimeWindow(start=14 * HOUR, end=15 * HOUR)
    shift = TimeWindow(start=7 * HOUR, end=17 * HOUR)
    orders = (Order(id="COLLECT", kind="JOB", quantities={"kg": 80},
                    pickup=StopSpec(location_id="C2", time_windows=(morning,),
                                    service_fixed=60)),
              drop("DELIVER", "C1", windows=(afternoon,), kg=60))
    print("      capacity 100kg;  deliveries total 60kg;  pickups total 80kg")
    print("      both totals fit, and the windows force the pickup first, so a")
    print("      shared route carries 60 out and collects 80 on top: 140kg.")
    for label, fleet in (("one van", (van(shift=shift),)),
                         ("two vans", (van("V1", shift=shift),
                                       van("V2", shift=shift)))):
        problem = instance(orders, fleet)
        solution = solve(problem, iterations=400, seed=0)
        print(f"      {label:9s} -> status={solution.status:10s} "
              f"served={len(served(solution))}/2")
    print("      §6.1: \"computing feasibility from route totals is wrong and is")
    print("      a classic production bug\". The peak is the binding number.")

    case("UC-067", "two orders, each legal alone")
    food = Order(id="FOOD", kind="JOB", quantities={"kg": 1},
                 delivery=StopSpec(location_id="C1", time_windows=(DAY,),
                                   service_fixed=60),
                 order_class="FOODSTUFF")
    hazard = Order(id="HAZ", kind="JOB", quantities={"kg": 1},
                   delivery=StopSpec(location_id="C2", time_windows=(DAY,),
                                     service_fixed=60),
                   order_class="HAZARDOUS",
                   incompatible_with=frozenset({"FOODSTUFF"}))
    problem = instance((food, hazard), (van("V1"), van("V2")))
    print(f"      pre-flight: {'clean' if not preflight(problem) else 'flagged'} "
          "-- each order is servable on its own")
    solution = solve(problem, iterations=400, seed=0)
    together = [r.vehicle_id for r in solution.routes
                if {"FOOD", "HAZ"} <= {s.order_id for s in r.steps if s.order_id}]
    report = verify(problem, solution)
    print(f"      solver:     loaded them together on {together or 'no vehicle'}")
    print(f"      verifier:   {[v.invariant for v in report.violations] or 'clean'}")
    print("      INCOMPLETE. T-22 built order-class incompatibility as a check,")
    print("      not as a constraint: `incompatible_with` reaches neither the")
    print("      PyVRP adapter nor the local search, so the plan is rejected")
    print("      after it is built rather than never built. UC-067 is recorded")
    print("      PARTIALLY_MODELLED for exactly this.")


# --------------------------------------------------------------------------
# 5. Degeneracy and scale
# --------------------------------------------------------------------------

def degeneracy_and_scale() -> None:
    heading("5.", "Degeneracy and scale")

    case("UC-069", "two hundred drops at one door")
    block = (Location(id="D", lat=9.9, lon=-84.0, matrix_index=0),
             Location(id="BLOCK", lat=9.91, lon=-84.0, matrix_index=1))
    orders = tuple(drop(f"O{i}", "BLOCK", service=30, kg=1) for i in range(200))
    problem = Problem(id="block", locations=block, orders=orders,
                      vehicles=(van(capacities={"kg": 1_000}),), matrix=grid(2))
    solution = solve(problem, iterations=200, seed=0)
    print(f"      served {len(served(solution))}/200, "
          f"verified={verify(problem, solution).ok}")
    print("      Every candidate ties at zero distance, so a neighbourhood")
    print("      ranked by distance degenerates to an arbitrary subset. Service")
    print("      time is the only thing left that varies.")

    case("UC-070", "the smallest thing that is still a routing problem")
    problem = instance((drop("O1", "C1"),), (van(),))
    solution = solve(problem, iterations=1, seed=0)
    print(f"      one iteration -> "
          f"{[s.type for s in solution.routes[0].steps]}")
    print("      The fastest smoke test there is. A trivial instance that spends")
    print("      a search budget is reporting overhead every real instance pays.")

    case("UC-074", "the size at which the engine changes algorithm")
    print("      Below the threshold the instance is solved whole; above it, it")
    print("      is partitioned, solved in pieces and repaired. The threshold is")
    print("      the one size where both are defined, so it is the only place")
    print("      they can be compared on the same instance -- see")
    print("      tests/vrp/test_pathological.py for the objective comparison.")


# --------------------------------------------------------------------------
# 6. The world is not a plane
# --------------------------------------------------------------------------

def not_a_plane() -> None:
    heading("6.", "The world is not a plane")

    case("UC-073", "a cluster straddling the antimeridian")
    for label, centre in (("longitude 0", 0.0), ("longitude 180", 180.0)):
        print(f"      {label:14s} -> {_ring_partition(centre)}")
    print("      The same ring of stops, translated across the dateline. Nothing")
    print("      about the geography changed, so the partition must not either.")
    print("      A centroid of raw longitudes lands on the far side of the")
    print("      planet and cuts the instance by which side of 180 a stop is on.")

    case("UC-072", "a matrix that stops arriving halfway through")
    print("      A mid-build failure propagates: no arc is ever filled in with a")
    print("      straight line, so no plan is costed against a road network that")
    print("      does not exist.")
    print("      INCOMPLETE. NFR-04 and MTX-11 ask for more than safety -- fall")
    print("      back to the cached matrix and mark the plan DEGRADED. Neither")
    print("      the fallback nor the label is built, so UC-072 is recorded")
    print("      PARTIALLY_MODELLED.")


def _hhmm(seconds: int) -> str:
    """Hours past the horizon's origin, which may exceed 24."""
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def _ring_partition(centre_lon: float) -> list[list[int]]:
    import math

    count, lat, radius = 8, 10.0, 0.1
    points = [(lat + radius * math.sin(2 * math.pi * k / count),
               ((centre_lon + radius * math.cos(2 * math.pi * k / count) + 180)
                % 360) - 180)
              for k in range(count)]
    locations = ((Location(id="D", lat=lat,
                           lon=((centre_lon + 180) % 360) - 180, matrix_index=0),)
                 + tuple(Location(id=f"C{k + 1}", lat=plat, lon=plon,
                                  matrix_index=k + 1)
                         for k, (plat, plon) in enumerate(points)))
    orders = tuple(drop(f"O{k + 1}", f"C{k + 1}", kg=1) for k in range(count))
    problem = Problem(id="ring", locations=locations, orders=orders,
                      vehicles=tuple(van(f"V{i}", capacities={"kg": 10})
                                     for i in (1, 2)),
                      matrix=grid(count + 1, leg=100))
    return sorted(sorted(int(o[1:]) for o in cluster.order_ids)
                  for cluster in partition(problem, target_size=4, seed=0))


def main() -> int:
    print(__doc__.split("Usage:")[0].strip().split("\n")[0])
    print("\nCAT-VRP-003 §11 -- fifteen instances that break implementations.")
    refused_before_the_search()
    broken_against_tight()
    the_clock()
    what_the_totals_hide()
    degeneracy_and_scale()
    not_a_plane()
    print(f"\n{'=' * 72}")
    print("Thirteen of the fifteen behave as the catalogue requires. UC-067 and")
    print("UC-072 do not, are marked PARTIALLY_MODELLED there, and are pinned")
    print("with strict xfail in tests/vrp/test_pathological.py so that closing")
    print("either gap fails the suite until the catalogue is updated too.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
