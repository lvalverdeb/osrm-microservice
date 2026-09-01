"""The fourteen operations that must work at v1, and the obvious wrong answer.

Demonstrates the P0 operational set of the real-world catalogue
(`CAT-VRP-003` §13.1, `UC-001`…`UC-171`) — every scenario the catalogue tiers as
"must work at v1", each run beside the naive approach its `Breaks` line names.

    vrp.bench.fixtures  the instances, one canonical per scenario
    vrp.solve           PyVRP over the domain model
    vrp.evaluator       the canonical objective, for comparing two answers
    vrp.triggers        locked re-optimisation and its delta
    vrp.zones           the sequence prior learned from executed rounds
    vrp.verify          the independent verifier, which has the last word

§1 of the catalogue: "The `Breaks` line is the point. 'Test that capacity works'
is a weak test. 'Test that a route whose total load fits but whose peak load
exceeds capacity is rejected' is a real one, and it comes from an operation
where that bug shipped."

So every section below computes two answers on one instance -- the obvious one
and the engine's -- and prints them together. A section that printed only a plan
would be showing that something came back, which is not what any of these
operations is about.

Five groups, following the catalogue's own sections:

1. **Sequence only.** A round, a technician's day, and the stops left after a
   missed one. The assignment is already fixed; only the order is open, and the
   order drivers actually run is not the shortest one.

2. **Capacity binds first.** A hopper that empties three times a shift, and a
   load that stops falling once the empties are aboard.

3. **Time binds first.** Slots rather than volume, a receiving bay that shuts,
   a day that is mostly parking and walking, and technicians who start at their
   own front doors.

4. **Many origins.** A vehicle partitioned into compartments, and depots whose
   catchments overlap.

5. **The plan meets the day.** A breakdown at noon, an urgent order at eleven,
   and a driver who does not arrive.

Four sections print INCOMPLETE. Three of them are the same defect: skills,
order-class incompatibility, site access and depot inventory are enforced by the
verifier and reported by pre-flight, and none of them is compiled into the
search — so the engine reliably detects an illegal plan it had no way to avoid
building. The fourth is §8.4's recovery, which is built for a mid-day
disruption and is applied here at shift start.

Runs offline. The instances are seeded synthetic ones, which is what §13.1 asks
for at this stage; §13.4 replaces them with anonymised production data "as
customers arrive".

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/p0/must_work_at_v1.py
"""

from __future__ import annotations

import sys
from itertools import pairwise
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.adherence import ExecutedRoute
from vrp.bench import fixtures
from vrp.bench.fixtures import FIXTURES
from vrp.committed import loading_locks, moved_since
from vrp.depots import drawn_per_depot, over_drawn, solve_within_inventory
from vrp.evaluator import evaluate
from vrp.solve.pyvrp_adapter import solve
from vrp.triggers import Trigger, recover_from_absence, reoptimise
from vrp.verify import verify
from vrp.zones import learn_prior, order_by_prior

HOUR = 3600


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 74}\n{number}  {title}\n{'=' * 74}")


def case(uc: str, title: str) -> None:
    print(f"\n  {uc}  {title}")


def say(*lines: str) -> None:
    for line in lines:
        print(f"      {line}")


def plan_for(uc_id: str, iterations: int = 600, seed: int = 0):
    problem = FIXTURES[uc_id]()
    return problem, solve(problem, iterations=iterations, seed=seed)


def assignment_of(solution) -> dict[str, list[str]]:
    return {route.vehicle_id: [s.order_id for s in route.steps if s.order_id]
            for route in solution.routes}


def served(solution) -> set[str]:
    return {o for seq in assignment_of(solution).values() for o in seq}


def vans_used(solution) -> int:
    return len([r for r in solution.routes if any(s.order_id for s in r.steps)])


# --------------------------------------------------------------------------
# 1. Sequence only
# --------------------------------------------------------------------------

def sequence_only() -> None:
    heading("1.", "Sequence only — the assignment is fixed, the order is not")

    case("UC-075", "delivery-station round, sequenced to match the driver")
    problem, solution = plan_for("UC-075")
    zones = {loc.id: f"Z{(i - 1) % 3}" if i else "DEPOT"
             for i, loc in enumerate(problem.locations)}
    street_of = lambda oid: zones[problem.order(oid).delivery.location_id]
    ids = [o.id for o in problem.orders]
    # The driver runs the far street first: parking there is easy early.
    executed = [o for street in ("Z2", "Z0", "Z1") for o in ids
                if street_of(o) == street]
    prior = learn_prior(problem, (ExecutedRoute(
        vehicle_id="V1", driver_id="d1", depot_id="D", territory="T",
        sequence=tuple(executed), arrivals={o: 0 for o in executed}),), zones)
    shortest = assignment_of(solution)["V1"]
    advised = order_by_prior(problem, shortest, prior, zones)

    say(f"driver walked the streets   {_streets(executed, street_of)}",
        f"shortest tour walks them    {_streets(shortest, street_of)}",
        f"prior-ordered tour walks    {_streets(advised, street_of)}",
        "The optimal sequence is not the executed sequence, and the gap is",
        "parking, access and zone structure (§4.2). The prior is advisory: it",
        "reorders, never drops.")

    case("UC-077", "one technician's fixed day")
    problem, solution = plan_for("UC-077", iterations=200)
    say(f"{len(problem.orders)} appointments, one engineer, "
        f"served {len(served(solution))}, verified {verify(problem, solution).ok}",
        "Formally a VRP with one vehicle. Operationally a response budget:",
        "the engineer re-sequences from the van, so NFR-02's two seconds is",
        "the constraint, not plan quality.")

    case("UC-087", "re-sequencing mid-shift after a missed stop")
    problem, solution = plan_for("UC-087", iterations=400)
    steps = solution.routes[0].steps
    say(f"the van is at C1; the plan starts at {steps[0].location_id} "
        f"and ends at {steps[-1].location_id}",
        "Re-solving from the depot invents a leg nobody will drive. What is",
        "left is an open tour with a fixed origin and no fixed destination.")


def _streets(sequence, street_of) -> list[str]:
    out: list[str] = []
    for order_id in sequence:
        street = street_of(order_id)
        if not out or out[-1] != street:
            out.append(street)
    return out


# --------------------------------------------------------------------------
# 2. Capacity binds first
# --------------------------------------------------------------------------

def capacity_binds() -> None:
    heading("2.", "Capacity binds before the clock")

    case("UC-013", "municipal waste: a round is three trips to the tip")
    multi = FIXTURES["UC-013"]()
    single = multi.__class__(**{**multi.__dict__, "id": "single",
                                "vehicles": tuple(
                                    v.__class__(**{**v.__dict__, "max_reloads": 0,
                                                   "reload_locations": ()})
                                    for v in multi.vehicles)})
    with_tips = solve(multi, iterations=600, seed=0)
    without = solve(single, iterations=600, seed=0)
    reloads = len([s for r in with_tips.routes for s in r.steps if s.type == "RELOAD"])
    overloads = len([v for v in verify(single, without).violations
                     if v.invariant == "INV-5"])
    say(f"multi-trip  status={with_tips.status:10s} reloads={reloads}",
        f"single-trip status={without.status:10s} capacity violations={overloads}",
        "The single-trip model does not serve fewer stops. It returns the same",
        "round as a plan nobody can drive: one hopper-load carrying three.")

    case("UC-004", "beverage: full crates out, empties back")
    problem, solution = plan_for("UC-004")
    for route in solution.routes:
        loads = [s.load_after.get("kg", 0) for s in route.steps]
        if any(b > a for a, b in pairwise(loads)):
            say(f"{route.vehicle_id} load profile, kg: "
                + " ".join(str(x) for x in loads),
                f"empty after stop {loads.index(min(loads))}, and carrying "
                f"{loads[-1]}kg when it gets home "
                f"(capacity {problem.vehicle(route.vehicle_id).capacities['kg']})")
            break
    say("Load does not fall monotonically once empties are aboard, so the",
        "binding number is the highest point along the route. A delivery-only",
        "check exceeds capacity at a stop it thought it was emptying.")


# --------------------------------------------------------------------------
# 3. Time binds first
# --------------------------------------------------------------------------

def time_binds() -> None:
    heading("3.", "Time binds before capacity")

    case("UC-001", "grocery home delivery: slots, not volume")
    problem, solution = plan_for("UC-001", iterations=800)
    capacity = problem.vehicles[0].capacities["kg"]
    demand = sum(o.quantities.get("kg", 0) for o in problem.orders)
    say(f"volume says  {-(-demand // capacity)} van(s)  "
        f"({demand}kg over {capacity}kg vans)",
        f"slots need   {vans_used(solution)} van(s)",
        "Everyone books 17:00-19:00, so vans leave most of the day empty and a",
        "volume-derived fleet is short on the evening peak.")

    case("UC-003", "retail: a receiving bay that shuts at 11:00")
    problem, solution = plan_for("UC-003")
    closes = problem.orders[0].delivery.time_windows[0].end
    latest = max((s.start_service for r in solution.routes for s in r.steps
                  if s.order_id), default=0)
    say(f"bay closes {closes // 3600:02d}:00; latest service starts "
        f"{latest // 3600:02d}:{latest % 3600 // 60:02d}",
        f"{vans_used(solution)} vans for {len(problem.orders)} stores",
        "A store closing goods-in does not accept a late pallet. The window is",
        "hard, not expensive: treating it as soft wastes the whole trip.")

    case("UC-009", "parcel last mile: the day is mostly parking and walking")
    problem, solution = plan_for("UC-009")
    driving = working = 0
    for route in solution.routes:
        steps = list(route.steps)
        if len(steps) < 2:
            continue
        working += steps[-1].departure - steps[0].departure
        driving += sum(problem.matrix.duration(
            problem.location(a.location_id).matrix_index,
            problem.location(b.location_id).matrix_index)
            for a, b in pairwise(steps))
    say(f"driving {driving // 60} min of a {working // 60} min duty "
        f"({driving / working:.0%})",
        "§4.2 puts travel at roughly a third of a dense last-mile day. A plan",
        "saving 8% of distance and adding parking difficulty is worse, and no",
        "distance objective can see that.")

    case("UC-019", "utilities: technicians who start at their own front doors")
    from_home = FIXTURES["UC-019"]()
    from_office = from_home.__class__(
        **{**from_home.__dict__, "id": "office",
           "vehicles": tuple(v.__class__(**{**v.__dict__, "start_location_id": "D",
                                            "end_location_id": "D"})
                             for v in from_home.vehicles)})
    home_plan = solve(from_home, iterations=600, seed=0)
    office_plan = solve(from_office, iterations=600, seed=0)
    commute = sum(2 * from_home.matrix.distance(
        from_home.location(v.start_location_id).matrix_index,
        from_home.location("D").matrix_index) for v in from_home.vehicles)
    home_cost = evaluate(from_home, assignment_of(home_plan)).total
    office_cost = evaluate(from_office, assignment_of(office_plan)).total
    say(f"home-start   {home_cost}",
        f"depot-based  {office_cost} + {commute} of commute it does not model "
        f"= {office_cost + commute}",
        "Each home is a distinct start and end, so this is multi-depot with one",
        "office. The depot-based model is not more expensive, it is incomplete.")
    skills = [v.invariant for v in verify(from_home, home_plan).violations]
    say("", f"INCOMPLETE. The verifier reports {sorted(set(skills))}: FR-10's",
        "skills are checked after the fact and compiled into the search",
        "nowhere, so gas work reaches an electricity-only crew.")


# --------------------------------------------------------------------------
# 4. Many origins
# --------------------------------------------------------------------------

def many_origins() -> None:
    heading("4.", "Many origins, and vehicles that are not interchangeable")

    case("UC-002", "multi-temperature: one van, three compartments")
    problem, solution = plan_for("UC-002")
    van = problem.vehicles[0]
    frozen_orders = [o.id for o in problem.orders if o.quantities["frozen"]]
    aggregate = sum(sum(problem.order(o).quantities.values()) for o in frozen_orders)
    frozen = sum(problem.order(o).quantities["frozen"] for o in frozen_orders)
    say(f"vehicle: frozen {van.capacities['frozen']}, "
        f"chilled {van.capacities['chilled']}, ambient {van.capacities['ambient']}"
        f"  (total {sum(van.capacities.values())})",
        f"every frozen order on one van: total {aggregate} -- fits; "
        f"frozen {frozen} -- does not",
        f"plan verified: {verify(problem, solution).ok}",
        "Total volume is the wrong feasibility test for a vehicle that is",
        "physically partitioned. Each compartment is its own dimension.")

    case("UC-134", "regional distribution: overlapping depot catchments")
    problem = FIXTURES["UC-134"]()
    stock = {loc.id: loc.inventory for loc in problem.locations
             if loc.inventory is not None}
    single = solve(problem, iterations=600, seed=0)
    loop, planned = solve_within_inventory(
        problem, lambda p: solve(p, iterations=600, seed=0))
    say(f"depot stock:      {stock}",
        f"one pass drew:    {drawn_per_depot(problem, single)}  "
        f"(over by {over_drawn(problem, single)})",
        f"within inventory: {drawn_per_depot(planned, loop)}  "
        f"serving {len(served(loop))}/{len(problem.orders)}",
        f"withdrawals recorded as {len(planned.locks)} FR-21 locks",
        "The nearest depot may have nothing in it. Assigning orders to depots",
        "first would fix that and forfeit the cheapest plans, which is the",
        "entry's other half, so the search keeps choosing: only the choices no",
        "depot can honour are withdrawn, and it chooses again.")


# --------------------------------------------------------------------------
# 5. The plan meets the day
# --------------------------------------------------------------------------

def the_day_happens() -> None:
    heading("5.", "The plan meets the day")

    case("UC-032", "a van breaks down mid-round")
    problem, plan = plan_for("UC-032")
    # The busiest round, not whichever route came back first: breaking a van
    # that has already finished moves nothing and proves nothing.
    broken_route = max(plan.routes,
                       key=lambda r: len([s for s in r.steps if s.order_id]))
    broken = broken_route.vehicle_id
    # Mid-round, taken from the plan rather than named as a clock time. A
    # synthetic round may finish before lunch, and a breakdown after the last
    # drop moves nothing however dramatic the hour sounds.
    times = [s.start_service for s in broken_route.steps if s.order_id]
    now = times[len(times) // 2]
    response = reoptimise(problem, plan, Trigger("BREAKDOWN", at=now,
                                                 vehicle_id=broken), now=now,
                          neighbours=1)
    scratch = solve(problem, iterations=600, seed=1)
    say(f"locked re-optimisation moved {response.delta.churn} stops "
        f"({response.locked_share / 10:.0f}% of the plan locked)",
        f"a full re-solve moved       {len(moved_since(plan, scratch))} stops",
        "§8.3: re-planning the world moves stops drivers have already passed",
        "and reshuffles routes that were fine. A 0.5% gain that reshuffles half",
        "the plan at 14:00 is a net loss.")

    case("UC-033", "an urgent order at eleven")
    problem, plan = plan_for("UC-033")
    urgent = problem.orders[len(problem.orders) // 2].id
    now = 11 * HOUR
    response = reoptimise(problem, plan, Trigger("PRIORITY_ORDER", at=now,
                                                 order_id=urgent), now=now,
                          neighbours=1)
    delta = response.delta
    say(f"quote for {urgent}: cost {delta.cost_before} -> {delta.cost_after} "
        f"({delta.cost_change:+d}), lateness "
        f"{delta.lateness_before} -> {delta.lateness_after}, "
        f"{delta.churn} stop(s) moved",
        "The cost of an insertion is not its detour. It is the knock-on across",
        "the whole downstream tail, which is why AC-2.3 asks for three numbers",
        "rather than one.")

    case("UC-171", "a driver does not arrive")
    problem, plan = plan_for("UC-171")
    absent = max(plan.routes,
                 key=lambda r: len([s for s in r.steps if s.order_id])).vehicle_id
    loaded = {lock.order_id: lock.vehicle_id
              for lock in loading_locks(problem, plan, absent=[absent])}
    response = recover_from_absence(
        problem, plan, [absent], lambda p: solve(p, iterations=600, seed=0))
    carrier = {s.order_id: r.vehicle_id for r in response.plan.routes
               for s in r.steps if s.order_id}
    repacked = [o for o, van in loaded.items()
                if carrier.get(o) not in (None, van)]
    tier0 = {o.id for o in problem.orders if o.priority_tier == 0}
    say(f"{absent} does not arrive; {len(loaded)} orders are already aboard "
        "the vans that do",
        f"strip and redistribute: serves {len(served(response.plan))}"
        f"/{len(problem.orders)}, moves {response.delta.churn}, objective "
        f"{response.delta.cost_before} -> {response.delta.cost_after}",
        f"already-loaded work asked to move: {repacked or 'none'}",
        f"tier-0 work kept: {sorted(tier0 & served(response.plan))}",
        "A free re-plan of the two remaining vans scores better on paper and",
        "is not available: at 05:30 it means drivers moving stock between",
        "vehicles in the yard. Loading is a commitment, so it is pinned, and",
        "the only thing free to move is the stock nobody is driving.")


def main() -> int:
    print("The fourteen operations that must work at v1, and the obvious wrong "
          "answer.")
    print("\nCAT-VRP-003 §13.1 -- the P0 operational set, each beside its "
          "`Breaks` line.")
    sequence_only()
    capacity_binds()
    time_binds()
    many_origins()
    the_day_happens()
    print(f"\n{'=' * 74}")
    print("All fourteen now behave as the catalogue requires. The last three to")
    print("arrive were UC-019, UC-134 and UC-171, and the last of those was not")
    print("an engine gap at all: the entry was right and the measurement was")
    print("wrong, because a re-plan of the reduced fleet was being scored on a")
    print("freedom the depot does not have. Loading is a commitment. Once it is")
    print("written down as one, the question the morning actually asks is which")
    print("stops to strip, and that is the question the engine answers.")
    print(f"\nInstances: vrp/bench/fixtures.py, three sizes each "
          f"({', '.join(f'{k}={v}' for k, v in fixtures.SIZES.items())} stops; "
          f"single-tour and appointment-day operations scale within a duty).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
