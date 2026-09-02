"""Two routes meeting at a place and a time — FR-26, INV-15, T-76.

Every other constraint in this system belongs to something: a capacity to a
vehicle, a window to a stop, an obligation to an order. This one belongs to a
*pair*, and `UC-131` says why that matters: "the second-echelon departure
depends on the first echelon's arrival, which is a synchronisation constraint
across two routing problems." Both routes can be individually perfect while the
cargo bikes leave the satellite an hour before the lorry carrying their load
arrives.

So the verifier is the authority here, and `vrp.synchronise` is a loop that
tries to produce a plan it will accept -- not a constraint the search carries,
because there is no construct in the search that relates two routes' timelines.
"""

from __future__ import annotations

import pytest

from vrp.model import (
    Location,
    Order,
    Problem,
    Route,
    Solution,
    Step,
    StopSpec,
    Synchronisation,
    TimeWindow,
    TravelMatrix,
    ValidationError,
    Vehicle,
)
from vrp.solve.pyvrp_adapter import solve
from vrp.synchronise import solve_synchronised, unmet
from vrp.verify import verify

DAY = TimeWindow(start=0, end=12 * 3600)
LEG = ((0, 600, 600, 1200), (600, 0, 0, 600),
       (600, 0, 0, 600), (1200, 600, 600, 0))


def satellite(*, distinguishable: bool = True) -> tuple[Location, ...]:
    """A hub with a receiving bay and a dispatch bay, at the same coordinates.

    `distinguishable` gives them different access classes, which is what lets
    the two echelons be told apart. Without it both bays admit both vehicles
    and one van can do the whole transfer by itself -- a good route, and not
    the operation.
    """
    inbound = frozenset({"HGV"}) if distinguishable else frozenset()
    outbound = frozenset({"BIKE"}) if distinguishable else frozenset()
    return (Location(id="D", lat=9.90, lon=-84.0, matrix_index=0),
            Location(id="IN", lat=9.95, lon=-84.0, matrix_index=1,
                     access_classes=inbound),
            Location(id="OUT", lat=9.95, lon=-84.0, matrix_index=2,
                     access_classes=outbound),
            Location(id="C", lat=10.0, lon=-84.0, matrix_index=3,
                     access_classes=outbound))


def two_echelons(*, min_gap: int = 300,
                 distinguishable: bool = True) -> Problem:
    orders = (Order(id="TRUNK", kind="JOB", quantities={"kg": 50},
                    delivery=StopSpec(location_id="IN", time_windows=(DAY,),
                                      service_fixed=600)),
              Order(id="ONWARD", kind="JOB", quantities={"kg": 50},
                    pickup=StopSpec(location_id="OUT", time_windows=(DAY,),
                                    service_fixed=600)))
    fleet = (Vehicle(id="LORRY", capacities={"kg": 100}, shift=DAY,
                     start_location_id="D", end_location_id="D",
                     access_class="HGV" if distinguishable else None),
             Vehicle(id="BIKE", capacities={"kg": 100}, shift=DAY,
                     start_location_id="D", end_location_id="D",
                     access_class="BIKE" if distinguishable else None))
    return Problem(
        id="echelon", locations=satellite(distinguishable=distinguishable),
        orders=orders, vehicles=fleet,
        matrix=TravelMatrix(version="e", durations=LEG, distances=LEG),
        synchronisations=(Synchronisation(kind="TRANSFER", first="TRUNK",
                                          second="ONWARD", min_gap=min_gap),))


def engine(problem):
    return solve(problem, iterations=400, seed=0)


def hand_built(problem: Problem, timings) -> Solution:
    """A plan with the two halves where the caller says, matrix be damned.

    INV-15 is about the relation between two routes, so the fixture has to be
    able to put them anywhere; INV-3 and INV-4 police the arithmetic and are
    not what is under test.
    """
    return Solution(
        problem_id=problem.id, status="FEASIBLE",
        routes=tuple(Route(vehicle_id=vehicle_id, steps=tuple(
            Step(type=step_type, order_id=order_id, location_id=location,
                 arrival=start, start_service=start, departure=departure)
            for step_type, order_id, location, start, departure in steps))
            for vehicle_id, steps in timings.items()))


# --------------------------------------------------------------------------
# The verifier, which is the authority
# --------------------------------------------------------------------------

def test_a_transfer_the_second_echelon_left_before_is_rejected():
    """`UC-131`'s failure exactly: both routes are individually fine and the
    load never made it onto the bike."""
    problem = two_echelons(min_gap=300)
    plan = hand_built(problem, {
        "LORRY": [("DELIVERY", "TRUNK", "IN", 3_000, 3_600)],
        "BIKE": [("PICKUP", "ONWARD", "OUT", 1_000, 1_600)]})

    report = verify(problem, plan)

    assert [v.invariant for v in report.violations] == ["INV-15"]
    assert "of a required 300s" in report.violations[0].detail


def test_a_transfer_that_waits_the_handover_out_is_accepted():
    problem = two_echelons(min_gap=300)
    plan = hand_built(problem, {
        "LORRY": [("DELIVERY", "TRUNK", "IN", 1_000, 1_600)],
        "BIKE": [("PICKUP", "ONWARD", "OUT", 1_900, 2_500)]})

    assert verify(problem, plan).ok


def test_the_clock_starts_when_the_first_echelon_finishes_not_when_it_arrives():
    """The handover cannot begin while the lorry is still unloading.

    `UC-131`'s "the second-echelon departure depends on the first echelon's
    arrival" is loose language for a precise thing: what the bike waits for is
    the load being *off*, not the lorry being *there*. A gap measured from the
    first stop's start rather than its departure hides the whole unloading
    time, and every test above passes either way -- which is how the first
    version of this file passed with exactly that perturbed in.
    """
    problem = two_echelons(min_gap=300)
    # The lorry unloads for ten minutes from 1000. The bike starts at 1700:
    # seven hundred seconds after the lorry arrived, one hundred after it
    # finished, and the handover needs three hundred.
    plan = hand_built(problem, {
        "LORRY": [("DELIVERY", "TRUNK", "IN", 1_000, 1_600)],
        "BIKE": [("PICKUP", "ONWARD", "OUT", 1_700, 2_300)]})

    coupling = [v for v in verify(problem, plan).violations
                if v.invariant == "INV-15"]

    assert len(coupling) == 1, (
        "1700 is comfortably after the lorry arrived and not after it left; a "
        "gap measured from arrival calls this fine")
    assert "by 100s of a required 300s" in coupling[0].detail


def test_one_vehicle_cannot_satisfy_a_coupling_by_carrying_both_halves():
    """A coupling is between two routes. Two stops on one route are a
    sequence, and calling that a transfer would let a plan meet the constraint
    by ignoring it."""
    problem = two_echelons()
    plan = hand_built(problem, {
        "LORRY": [("DELIVERY", "TRUNK", "IN", 1_000, 1_600),
                  ("PICKUP", "ONWARD", "OUT", 2_000, 2_600)]})

    report = verify(problem, plan)

    # The hand-built plan breaks other things too -- an HGV standing at a
    # bike-only bay is INV-10's business, and the times are invented so INV-4
    # objects. What matters is that the coupling itself is reported, by name.
    coupling = [v for v in report.violations if v.invariant == "INV-15"]
    assert len(coupling) == 1, [str(v) for v in report.violations]
    assert "cannot be met by one" in coupling[0].detail


def test_a_convoy_is_measured_on_how_far_apart_the_two_are():
    """`UC-147`: "vehicles must travel in convoy... forcing several routes to
    share a path and a schedule"."""
    problem = two_echelons()
    convoy = Synchronisation(kind="CONVOY", first="TRUNK", second="ONWARD",
                             max_gap=120)
    problem = Problem(
        id=problem.id, locations=problem.locations, orders=problem.orders,
        vehicles=problem.vehicles, matrix=problem.matrix,
        synchronisations=(convoy,))

    together = hand_built(problem, {
        "LORRY": [("DELIVERY", "TRUNK", "IN", 1_000, 1_600)],
        "BIKE": [("PICKUP", "ONWARD", "OUT", 1_060, 1_660)]})
    apart = hand_built(problem, {
        "LORRY": [("DELIVERY", "TRUNK", "IN", 1_000, 1_600)],
        "BIKE": [("PICKUP", "ONWARD", "OUT", 5_000, 5_600)]})

    assert verify(problem, together).ok
    assert [v.invariant for v in verify(problem, apart).violations] == ["INV-15"]


def test_an_instance_with_no_coupling_says_so_rather_than_passing():
    problem = two_echelons()
    plain = Problem(id=problem.id, locations=problem.locations,
                    orders=problem.orders, vehicles=problem.vehicles,
                    matrix=problem.matrix)
    plan = hand_built(plain, {
        "LORRY": [("DELIVERY", "TRUNK", "IN", 1_000, 1_600)],
        "BIKE": [("PICKUP", "ONWARD", "OUT", 2_000, 2_600)]})

    assert "INV-15" in verify(plain, plan).not_applicable


# --------------------------------------------------------------------------
# The loop, which tries to produce a plan the verifier accepts
# --------------------------------------------------------------------------

def test_a_single_pass_leaves_the_echelons_unsynchronised():
    """Why the loop exists. Nothing in the search relates two routes' times,
    so the bike is free to leave whenever suits it."""
    problem = two_echelons()

    assert unmet(problem, engine(problem)), (
        "if a plain solve already met the coupling this instance is too slack "
        "to be about synchronisation"
    )


def test_the_loop_holds_the_second_echelon_until_the_first_has_arrived():
    problem = two_echelons(min_gap=300)

    solution, planned = solve_synchronised(problem, engine)

    assert not unmet(planned, solution)
    assert verify(planned, solution).ok, verify(planned, solution).violations
    timing = {step.order_id: (route.vehicle_id, step.start_service,
                              step.departure)
              for route in solution.routes for step in route.steps
              if step.order_id}
    assert timing["ONWARD"][1] >= timing["TRUNK"][2] + 300, (
        "the bike may not begin loading until the lorry has finished unloading "
        "and the handover has had its time")


def test_two_bays_that_admit_the_same_vehicle_are_refused_by_name():
    """The half the loop cannot do. Keeping the two ends on different vehicles
    is an order-to-order constraint, the same shape as the class
    incompatibility the adapters refuse, and a time window says nothing about
    it: one van collecting where it just delivered is simply a good route."""
    problem = two_echelons(distinguishable=False)

    with pytest.raises(NotImplementedError, match="carries both"):
        solve_synchronised(problem, engine)


# --------------------------------------------------------------------------
# What the model refuses to express
# --------------------------------------------------------------------------

def test_a_convoy_without_a_bound_is_not_a_constraint():
    with pytest.raises(ValidationError, match="needs a max_gap"):
        Synchronisation(kind="CONVOY", first="A", second="B")
    assert Synchronisation(kind="CONVOY", first="A", second="B",
                           max_gap=0).max_gap == 0, "together may mean exactly"


def test_a_coupling_needs_two_orders_and_two_known_ones():
    with pytest.raises(ValidationError, match="one order is a sequence"):
        Synchronisation(kind="TRANSFER", first="A", second="A")

    problem = two_echelons()
    with pytest.raises(ValidationError, match="unknown order"):
        Problem(id="x", locations=problem.locations, orders=problem.orders,
                vehicles=problem.vehicles, matrix=problem.matrix,
                synchronisations=(Synchronisation(kind="TRANSFER", first="TRUNK",
                                                  second="GHOST"),))
