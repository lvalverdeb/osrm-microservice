"""The catalogue's P0 operations, each asserting its own `Breaks` line.

`CAT-VRP-003` §13.1: "P0 scenarios become seeded synthetic fixtures first — one
per P0 scenario, at three sizes." §13.5 says what each must then do: "Every
fixture carries its `Breaks` assertion as a named test. A fixture asserting only
'returns a feasible solution' is not earning its runtime."

So none of these asserts that a plan came back. Each asserts the specific wrong
answer the entry names is *not* the answer given — usually by computing the
naive answer alongside the real one and comparing them on the same instance.
`§1`: "'Test that capacity works' is a weak test. 'Test that a route whose total
load fits but whose peak load exceeds capacity is rejected' is a real one, and
it comes from an operation where that bug shipped."

Instances come from `vrp.bench.fixtures`, so the instance asserted on here is
the one the coverage gate counts. Small is the fast tier; `medium` and `large`
are marked `slow` and run under `make corpus`.

Four assertions are strict xfails, and all four are the same defect wearing
different clothes: skills (FR-10), order-class incompatibility (FR-10), site
access (FR-11) and depot inventory (FR-31) are enforced by the verifier and
reported by pre-flight, but none of them is compiled into the search. The
adapter's `add_vehicle_type` carries capacity, depots, shifts and costs, and
nothing that makes a client ineligible for a vehicle. So the engine reliably
*detects* an illegal plan it had no way to avoid building.
"""

from __future__ import annotations

import time
from itertools import pairwise

import pytest

from vrp.adherence import ExecutedRoute
from vrp.bench.fixtures import FIXTURES
from vrp.committed import commit_locks, moved_since
from vrp.evaluator import evaluate
from vrp.model import Route, Solution
from vrp.solve.pyvrp_adapter import solve
from vrp.triggers import Trigger, reoptimise
from vrp.verify import verify
from vrp.zones import learn_prior, order_by_prior

HOUR = 3600
SLOW_SIZES = ("medium", "large")


def assignment_of(solution) -> dict[str, list[str]]:
    return {route.vehicle_id: [s.order_id for s in route.steps if s.order_id]
            for route in solution.routes}


def served(solution) -> set[str]:
    return {o for seq in assignment_of(solution).values() for o in seq}


def vans_used(solution) -> int:
    return len([r for r in solution.routes if any(s.order_id for s in r.steps)])


def driving_and_working(problem, solution) -> tuple[int, int]:
    """Seconds spent moving, and the whole span of the duty."""
    driving = working = 0
    for route in solution.routes:
        steps = [s for s in route.steps]
        if len(steps) < 2:
            continue
        working += steps[-1].departure - steps[0].departure
        driving += sum(problem.matrix.duration(
            problem.location(a.location_id).matrix_index,
            problem.location(b.location_id).matrix_index)
            for a, b in pairwise(steps))
    return driving, working


# --------------------------------------------------------------------------
# UC-075 — delivery-station route sequencing to match driver behaviour
# --------------------------------------------------------------------------

def test_uc075_the_distance_optimal_sequence_is_not_the_executed_one():
    """Breaks: minimising distance. The mathematically optimal sequence is not
    the executed sequence, and the gap is parking, access and zone structure."""
    problem = FIXTURES["UC-075"]()
    zones = {loc.id: f"Z{(index - 1) % 3}" if index else "DEPOT"
             for index, loc in enumerate(problem.locations)}
    order_ids = [o.id for o in problem.orders]

    # What drivers did: three streets, finished one at a time, and in an order
    # no distance metric knows about -- the far street first, because parking
    # there is easy before the school run.
    executed = [oid for zone in ("Z2", "Z0", "Z1")
                for oid in order_ids
                if zones[problem.order(oid).delivery.location_id] == zone]
    history = (ExecutedRoute(vehicle_id="V1", driver_id="d1", depot_id="D",
                             territory="T", sequence=tuple(executed),
                             arrivals={oid: 0 for oid in executed}),)
    prior = learn_prior(problem, history, zones)

    shortest = assignment_of(solve(problem, iterations=600, seed=0))["V1"]
    advised = order_by_prior(problem, shortest, prior, zones)

    assert set(advised) == set(shortest), "advice must not lose a stop"

    # The claim is about zone structure, so it is measured on zone structure.
    # `dissimilarity` compares arcs and scores both sequences identically here:
    # the advised tour visits the streets in the driver's order while keeping
    # the shortest tour's order within each, so almost every arc still differs.
    # That is the right measure for plan adherence and the wrong one for this.
    def street_order(sequence):
        streets = []
        for order_id in sequence:
            zone = zones[problem.order(order_id).delivery.location_id]
            if not streets or streets[-1] != zone:
                streets.append(zone)
        return streets

    assert street_order(advised) == street_order(executed), (
        "the prior exists to recover the order drivers work the streets in")
    assert street_order(shortest) != street_order(executed), (
        f"the shortest tour walks the streets {street_order(shortest)} and the "
        f"driver walks them {street_order(executed)}. If distance already "
        "reproduced the executed order, this operation would not be in the "
        "catalogue and §12.4's prior would have nothing to learn")


# --------------------------------------------------------------------------
# UC-077 — single technician's fixed day
# --------------------------------------------------------------------------

def test_uc077_a_one_van_day_answers_in_a_response_budget_not_a_planning_one():
    """Breaks: treating it as a VRP with one vehicle. It is, formally — but the
    useful behaviour is sub-second response so the engineer can re-sequence
    from the van, which is a different budget."""
    problem = FIXTURES["UC-077"]()

    started = time.perf_counter()
    solution = solve(problem, iterations=200, seed=0)
    elapsed = time.perf_counter() - started

    assert served(solution) == {o.id for o in problem.orders}
    assert verify(problem, solution).ok
    assert elapsed < 1.0, (
        f"{elapsed:.2f}s to re-sequence one technician's day. NFR-02 allows two "
        "seconds for an interactive quote; an engineer standing at a van will "
        "not wait for a planning budget")


# --------------------------------------------------------------------------
# UC-087 — re-sequencing a route mid-shift after a missed stop
# --------------------------------------------------------------------------

def test_uc087_the_tour_starts_where_the_vehicle_is_not_at_the_depot():
    """Breaks: re-solving from the depot. The tour starts wherever the vehicle
    is now, which makes it an open TSP with a fixed origin and, often, no fixed
    destination."""
    problem = FIXTURES["UC-087"]()

    solution = solve(problem, iterations=400, seed=0)
    steps = solution.routes[0].steps

    assert steps[0].location_id == "C1", (
        f"the van is at C1 and the plan starts at {steps[0].location_id}: "
        "re-solving from the depot invents a leg nobody will drive")
    assert steps[-1].location_id != "D", "an open tour does not go home to finish"
    assert served(solution) == {o.id for o in problem.orders}
    assert verify(problem, solution).ok


# --------------------------------------------------------------------------
# UC-013 — municipal waste and recycling collection
# --------------------------------------------------------------------------

def test_uc013_a_round_is_three_trips_to_the_tip_not_three_plans():
    """Breaks: single-trip modelling. A round is three trips to the tip;
    chaining independent single-trip plans double-counts the driver's day."""
    multi_trip = FIXTURES["UC-013"]()
    single_trip = multi_trip.__class__(
        **{**multi_trip.__dict__,
           "id": "uc013-single",
           "vehicles": tuple(v.__class__(**{**v.__dict__, "max_reloads": 0,
                                            "reload_locations": ()})
                             for v in multi_trip.vehicles)})

    with_reloads = solve(multi_trip, iterations=600, seed=0)
    without = solve(single_trip, iterations=600, seed=0)

    reloads = [s for r in with_reloads.routes for s in r.steps if s.type == "RELOAD"]
    assert reloads, "a hopper a third the size of the round has to be emptied"
    assert with_reloads.status == "FEASIBLE"
    assert verify(multi_trip, with_reloads).ok

    # The single-trip model does not serve fewer stops. It returns the same
    # round as a plan that cannot be driven: one hopper-load carrying three.
    assert without.status == "INFEASIBLE", (
        "a hopper a third the size of the round, with nowhere to empty it, has "
        "no feasible single-trip plan")
    overloads = [v for v in verify(single_trip, without).violations
                 if v.invariant == "INV-5"]
    assert overloads, (
        "chaining independent single trips double-counts the day: the capacity "
        "violation is what that arithmetic hides")


# --------------------------------------------------------------------------
# UC-004 — beverage and brewery distribution
# --------------------------------------------------------------------------

def test_uc004_load_does_not_fall_monotonically_once_empties_are_aboard():
    """Breaks: computing load as deliveries only. Load does not decrease
    monotonically, so the vehicle can exceed capacity at a stop it was
    supposedly emptying."""
    problem = FIXTURES["UC-004"]()

    solution = solve(problem, iterations=600, seed=0)

    assert verify(problem, solution).ok, verify(problem, solution).violations
    rose_somewhere = False
    for route in solution.routes:
        loads = [s.load_after.get("kg", 0) for s in route.steps]
        rose_somewhere |= any(b > a for a, b in pairwise(loads))
    assert rose_somewhere, (
        "no route's load ever rose, so this instance never exercised the "
        "non-monotonic case the entry is about")


# --------------------------------------------------------------------------
# UC-001 — grocery home delivery
# --------------------------------------------------------------------------

def test_uc001_a_fleet_sized_from_volume_is_short_on_the_evening_peak():
    """Breaks: sizing the fleet from volume. Vans leave 60% empty because
    everyone books 17:00–19:00; a volume-derived fleet is short by a third."""
    problem = FIXTURES["UC-001"]()
    capacity = problem.vehicles[0].capacities["kg"]
    demand = sum(o.quantities.get("kg", 0) for o in problem.orders)
    from_volume = -(-demand // capacity)          # ceiling division

    solution = solve(problem, iterations=800, seed=0)

    assert served(solution) == {o.id for o in problem.orders}
    assert verify(problem, solution).ok
    assert vans_used(solution) > from_volume, (
        f"volume says {from_volume} van(s); the slots need {vans_used(solution)}. "
        "If they agreed, fleet sizing from volume would be sound and this "
        "operation would not be in the catalogue")


# --------------------------------------------------------------------------
# UC-003 — retail store delivery into receiving-bay hours
# --------------------------------------------------------------------------

def test_uc003_a_bay_that_closes_does_not_take_a_late_pallet():
    """Breaks: treating the window as soft. A store closing goods-in at 11:00
    does not accept an 11:20 arrival; the pallets come back."""
    problem = FIXTURES["UC-003"]()
    closes = problem.orders[0].delivery.time_windows[0].end

    solution = solve(problem, iterations=600, seed=0)

    assert verify(problem, solution).ok, verify(problem, solution).violations
    late = [s.order_id for r in solution.routes for s in r.steps
            if s.order_id and s.start_service > closes]
    assert not late, (
        f"{late} start service after the bay shuts at {closes // 3600:02d}:00. "
        "A hard window is not an expensive one")
    assert len(served(solution)) < len(problem.orders) or vans_used(solution) > 1, (
        "if one van served the whole round inside a five-hour bay window the "
        "instance is too slack to be about bay hours at all")


# --------------------------------------------------------------------------
# UC-009 — parcel last mile from a delivery station
# --------------------------------------------------------------------------

def test_uc009_travel_is_the_minority_of_the_day_so_distance_is_the_wrong_target():
    """Breaks: optimising distance. Travel is roughly a third of the driver's
    day (§4.2); a plan saving 8% of distance and adding parking difficulty is
    worse."""
    problem = FIXTURES["UC-009"]()

    solution = solve(problem, iterations=600, seed=0)
    driving, working = driving_and_working(problem, solution)

    assert verify(problem, solution).ok
    assert working > 0
    share = driving / working
    assert share < 0.5, (
        f"driving is {share:.0%} of the duty. §4.2 puts travel at roughly a "
        "third of a dense last-mile day; an instance where it dominates is not "
        "the operation this entry describes")


# --------------------------------------------------------------------------
# UC-019 — utility installation and repair appointments
# --------------------------------------------------------------------------

def test_uc019_home_start_technicians_are_multi_depot_even_with_one_office():
    """Breaks: modelling technicians as depot-based. Each home is a distinct
    start and end, making this multi-depot even with one office."""
    from_home = FIXTURES["UC-019"]()
    from_office = from_home.__class__(
        **{**from_home.__dict__, "id": "uc019-office",
           "vehicles": tuple(v.__class__(**{**v.__dict__,
                                            "start_location_id": "D",
                                            "end_location_id": "D"})
                             for v in from_home.vehicles)})

    home_plan = solve(from_home, iterations=600, seed=0)
    office_plan = solve(from_office, iterations=600, seed=0)

    assert served(home_plan) == {o.id for o in from_home.orders}
    starts = {r.steps[0].location_id for r in home_plan.routes if r.steps}
    assert starts and starts <= {v.start_location_id for v in from_home.vehicles}
    assert "D" not in starts, "nobody drives to the office to begin the day"

    # The depot-based model is not more expensive. It is incomplete: its cost
    # omits the two legs it silently requires, home to office and back. Add
    # them and the comparison reverses, which is what "modelling technicians as
    # depot-based" costs in practice.
    home_cost = evaluate(from_home, assignment_of(home_plan)).total
    office_cost = evaluate(from_office, assignment_of(office_plan)).total
    commute = 0
    for vehicle in from_home.vehicles:
        home = from_home.location(vehicle.start_location_id).matrix_index
        office = from_home.location("D").matrix_index
        commute += 2 * from_home.matrix.distance(home, office)
    assert office_cost + commute > home_cost, (
        f"home-start {home_cost}; depot-based {office_cost} plus {commute} of "
        "unmodelled commute. If the office plan were genuinely cheaper once "
        "the commute is counted, the entry's claim would be wrong")


@pytest.mark.xfail(strict=True, reason=(
    "FR-10's skills are enforced by the verifier (INV-10) and reported by "
    "pre-flight, and are compiled into nothing. `add_vehicle_type` carries "
    "capacity, depots, shifts and costs; there is no mechanism making a client "
    "ineligible for a vehicle, so the search assigns gas work to an "
    "electricity-only crew and the plan is rejected afterwards."))
def test_uc019_the_search_respects_which_crew_holds_which_ticket():
    """Breaks: a qualification is a constraint on the plan, not a note on it."""
    problem = FIXTURES["UC-019"]()

    solution = solve(problem, iterations=600, seed=0)

    assert verify(problem, solution).ok


# --------------------------------------------------------------------------
# UC-002 — multi-temperature convenience store replenishment
# --------------------------------------------------------------------------

def test_uc002_a_partitioned_vehicle_is_not_one_number():
    """Breaks: aggregate capacity checks. Total volume is the wrong feasibility
    test when the vehicle is physically partitioned."""
    problem = FIXTURES["UC-002"]()
    van = problem.vehicles[0]
    total_capacity = sum(van.capacities.values())

    solution = solve(problem, iterations=600, seed=0)
    assert verify(problem, solution).ok, verify(problem, solution).violations

    # A route an aggregate check would wave through: every frozen order on one
    # van. The totals fit the vehicle comfortably; the frozen compartment does
    # not.
    frozen_orders = [o.id for o in problem.orders if o.quantities["frozen"]]
    assert len(frozen_orders) >= 2, "the fixture must have frozen work to stack"
    stacked = {van.id: frozen_orders}
    aggregate = sum(sum(problem.order(o).quantities.values())
                    for o in frozen_orders)
    frozen = sum(problem.order(o).quantities["frozen"] for o in frozen_orders)

    assert aggregate <= total_capacity, "the aggregate check would pass this"
    assert frozen > van.capacities["frozen"], "and the compartment would not"

    hand_built = _plan_from(problem, stacked)
    report = verify(problem, hand_built)
    assert not report.ok and any(v.invariant == "INV-5" for v in report.violations), (
        "a compartment is a dimension; the wrong feasibility test is the sum")


def _plan_from(problem, assignment) -> Solution:
    """A timeline for a hand-built assignment, so the verifier can judge it."""
    evaluation = evaluate(problem, assignment)
    return Solution(problem_id=problem.id,
                    routes=tuple(Route(vehicle_id=vehicle_id, steps=steps)
                                 for vehicle_id, steps in evaluation.timelines.items()),
                    status="FEASIBLE")


# --------------------------------------------------------------------------
# UC-134 — regional distribution with overlapping depot catchments
# --------------------------------------------------------------------------

def test_uc134_a_stockout_at_the_nearest_depot_is_detected():
    """Breaks: nearest-depot assignment. The nearest depot may lack stock."""
    problem = FIXTURES["UC-134"]()
    empty = [loc.id for loc in problem.locations
             if loc.inventory is not None and not any(loc.inventory.values())]
    assert empty == ["D"], "the fixture's point is a depot with nothing in it"

    solution = solve(problem, iterations=600, seed=0)
    report = verify(problem, solution)

    assert served(solution) == {o.id for o in problem.orders}
    assert any(v.invariant == "INV-13" for v in report.violations), (
        "the search drew from an empty depot and INV-13 is what noticed. If "
        "this stops failing, the search has learned about inventory and "
        "test_uc134_the_search_chooses_a_depot_that_can_supply should be "
        "promoted along with UC-134's catalogue status")


@pytest.mark.xfail(strict=True, reason=(
    "FR-31's depot inventory is enforced by the verifier (INV-13) and reported "
    "by pre-flight as DEPOT_STOCKOUT, and reaches the search nowhere. DEC-1 "
    "says inventory MUST be enforced globally; the decomposition orchestrator "
    "says so in its docstring and enforces only the dock schedule."))
def test_uc134_the_search_chooses_a_depot_that_can_supply():
    """Breaks: fixing assignment before routing forecloses the cheapest plans —
    and drawing from an empty depot is not a plan at all."""
    problem = FIXTURES["UC-134"]()

    solution = solve(problem, iterations=600, seed=0)

    assert verify(problem, solution).ok


# --------------------------------------------------------------------------
# UC-032 — mid-day vehicle breakdown recovery
# --------------------------------------------------------------------------

def test_uc032_a_breakdown_moves_the_affected_work_not_the_whole_day():
    """Breaks: full re-solve. Re-planning the world moves stops drivers have
    already passed and reshuffles routes that were fine."""
    problem = FIXTURES["UC-032"]()
    plan = solve(problem, iterations=600, seed=0)
    broken = plan.routes[0].vehicle_id
    now = 12 * HOUR

    response = reoptimise(problem, plan, Trigger("BREAKDOWN", at=now, vehicle_id=broken),
                          now=now, neighbours=1)
    from_scratch = solve(problem, iterations=600, seed=1)

    committed = {lock.order_id for lock in commit_locks(problem, plan, now)
                 if lock.order_id}
    assert not (set(response.delta.moved) & committed), (
        "a stop already committed by 12:00 was moved; §8.3 forbids it outright")
    assert response.delta.churn < len(moved_since(plan, from_scratch)), (
        f"locked re-optimisation moved {response.delta.churn} stops and a full "
        f"re-solve moved {len(moved_since(plan, from_scratch))}. If they were "
        "equal there would be nothing to lock")


# --------------------------------------------------------------------------
# UC-033 — urgent order injection into a live plan
# --------------------------------------------------------------------------

def test_uc033_an_insertion_is_quoted_on_knock_on_lateness_not_on_distance():
    """Breaks: quoting insertion cost from distance. The true cost is knock-on
    lateness across the whole downstream tail."""
    problem = FIXTURES["UC-033"]()
    plan = solve(problem, iterations=600, seed=0)
    urgent = problem.orders[len(problem.orders) // 2].id

    # Midway through the day, so there is a committed morning for the insertion
    # to knock into. An injection before anyone has left is a re-plan.
    now = 11 * HOUR
    response = reoptimise(problem, plan,
                          Trigger("PRIORITY_ORDER", at=now, order_id=urgent),
                          now=now, neighbours=1)
    delta = response.delta

    assert urgent in served(response.plan), "the urgent order is the point"
    assert delta.cost_before and delta.cost_after, "a quote needs both sides"
    assert delta.cost_change > 0, (
        "inserting into a plan whose windows are 90 minutes wide is not free; "
        "a quote of zero is a quote of marginal distance")
    assert delta.churn <= 2, (
        f"the insertion moved {delta.churn} stops. AC-2.3 asks what an "
        "insertion costs, not what re-planning the day would cost")


# --------------------------------------------------------------------------
# UC-171 — driver absence discovered at shift start
# --------------------------------------------------------------------------

def test_uc171_an_absence_strips_and_redistributes_rather_than_replanning():
    """Breaks: re-solving from scratch. Vehicles are loaded; the practical
    question is which stops to strip and redistribute."""
    problem = FIXTURES["UC-171"]()
    plan = solve(problem, iterations=600, seed=0)
    absent = plan.routes[0].vehicle_id

    # Shift start, so nothing is committed and nothing can be locked. The
    # entry's claim is about churn, not about locks: the question is which
    # stops to strip, not how to re-plan the day.
    response = reoptimise(problem, plan,
                          Trigger("BREAKDOWN", at=7 * HOUR, vehicle_id=absent),
                          now=7 * HOUR, neighbours=1)

    assert not [s for r in response.plan.routes if r.vehicle_id == absent
                for s in r.steps if s.order_id], "the absent driver carries nothing"

    # What survives an absence is a choice, and FR-13's tiers are what make it
    # one rather than an arbitrary loss.
    high_tier = {o.id for o in problem.orders if o.priority_tier == 0}
    assert high_tier <= served(response.plan), (
        f"tier-0 work {sorted(high_tier - served(response.plan))} was dropped "
        "while lower-tier work was kept")


@pytest.mark.xfail(strict=True, reason=(
    "§8.4's default recovery is a cheapest-insertion pass, which is right for "
    "a mid-day disruption where most of the plan is committed and wrong for an "
    "absence at shift start where none of it is. Measured on this instance: "
    "re-planning the reduced fleet from scratch serves 12 of 12 and moves 6 "
    "stops; the targeted response drops half the round to 6 of 12, moves all "
    "12, and takes the objective from 63,736 to 644,730. Opening more "
    "neighbours does not change it. UC-171 is PARTIALLY_MODELLED for this."))
def test_uc171_stripping_and_redistributing_beats_replanning_the_day():
    """Breaks: re-solving from scratch. The practical question is which stops
    to strip and redistribute, not how to re-plan the day."""
    problem = FIXTURES["UC-171"]()
    plan = solve(problem, iterations=600, seed=0)
    absent = plan.routes[0].vehicle_id

    response = reoptimise(problem, plan,
                          Trigger("BREAKDOWN", at=7 * HOUR, vehicle_id=absent),
                          now=7 * HOUR, neighbours=2)
    reduced = problem.__class__(**{**problem.__dict__, "id": "uc171-reduced",
                                   "vehicles": tuple(v for v in problem.vehicles
                                                     if v.id != absent)})
    from_scratch = solve(reduced, iterations=600, seed=2)

    assert len(served(response.plan)) >= len(served(from_scratch))
    assert response.delta.churn < len(moved_since(plan, from_scratch))


# --------------------------------------------------------------------------
# The corpus at size — §13.1's other two sizes, out of the fast tier
# --------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.parametrize("size", SLOW_SIZES)
@pytest.mark.parametrize("uc_id", sorted(
    uc for uc in FIXTURES if uc in {
        "UC-001", "UC-002", "UC-003", "UC-004", "UC-009", "UC-013", "UC-019",
        "UC-032", "UC-033", "UC-075", "UC-077", "UC-087", "UC-134", "UC-171"}))
def test_the_p0_corpus_builds_and_solves_at_every_size(uc_id, size):
    """§13.1 asks for three sizes. Small carries the `Breaks` assertions above;
    these two exist so the same operation can be measured where search has to
    work, and so a fixture that only holds together at twelve stops is caught."""
    problem = FIXTURES[uc_id](size=size)

    solution = solve(problem, iterations=200, seed=0)
    report = verify(problem, solution)
    unenforced = {"INV-10", "INV-13"}          # see this module's docstring
    surprises = [v.invariant for v in report.violations
                 if v.invariant not in unenforced]
    assert not surprises, f"{uc_id} at {size}: {surprises}"
