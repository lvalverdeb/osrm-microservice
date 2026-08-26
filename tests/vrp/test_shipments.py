"""Shipments: precedence and same-vehicle — FR-01, INV-2, T-12's deferred half.

FR-01: "Represent transport requests as either single-stop **jobs** or paired
**shipments** (pickup → delivery) with precedence and same-vehicle
enforcement."

Jobs landed with E-12; shipments were refused with `NotImplementedError`, and
the message pointed at `T-13` — which is the lexicographic objective and has
nothing to do with them. FR-01 belongs to `T-12`, the adapter task.

INV-2 has been implemented in the verifier since E-03 and has never had a
subject: no shipment could reach it, so it passed by never being asked. That is
the same silent shape that hid the INV-5 capacity gap until E-04's generator
found it, and it is why this file checks the verifier *catches* a bad pairing
as well as accepting a good one.

PyVRP indexes shipments separately from clients — an activity is `is_client()`
with `idx` into clients, or `is_shipment()` with `idx` into shipments, and the
two spaces overlap at 0. A mapper reading `idx` without checking which space it
is in will map shipment 0 to client 0 and be confidently wrong.
"""

from __future__ import annotations

from vrp.model import (
    Location,
    Order,
    Problem,
    Route,
    Solution,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

DAY = TimeWindow(start=0, end=12 * 3600)


def instance(orders: tuple[Order, ...], stops: int, vehicles: int = 1,
             capacity: int = 100) -> Problem:
    size = stops + 1
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 1000, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    distances = tuple(tuple(abs(i - j) * 1000 for j in range(size))
                      for i in range(size))
    durations = tuple(tuple(abs(i - j) * 60 for j in range(size))
                      for i in range(size))
    fleet = tuple(
        Vehicle(id=f"V{n}", capacities={"kg": capacity}, shift=DAY,
                start_location_id="D", end_location_id="D")
        for n in range(1, vehicles + 1))
    return Problem(id="ship", locations=locations, orders=orders,
                   vehicles=fleet,
                   matrix=TravelMatrix(version="ship-v1", durations=durations,
                                       distances=distances))


def shipment(order_id: str, collect_at: str, drop_at: str, kg: int) -> Order:
    return Order(
        id=order_id, kind="SHIPMENT", quantities={"kg": kg},
        pickup=StopSpec(location_id=collect_at, time_windows=(DAY,),
                        service_fixed=60),
        delivery=StopSpec(location_id=drop_at, time_windows=(DAY,),
                          service_fixed=60))


def job(order_id: str, stop: str, kg: int) -> Order:
    return Order(id=order_id, kind="JOB", quantities={"kg": kg},
                 delivery=StopSpec(location_id=stop, time_windows=(DAY,),
                                   service_fixed=60))


def steps_of(solution) -> list:
    return [s for route in solution.routes for s in route.steps if s.order_id]


def test_a_shipment_is_collected_before_it_is_delivered():
    """FR-01's precedence, end to end through the solver."""
    problem = instance((shipment("S1", "C1", "C2", kg=10),), stops=2)
    solution = solve(problem, iterations=300, seed=0)

    assert solution.status == "FEASIBLE"
    assert not solution.unassigned
    served = steps_of(solution)
    assert [s.type for s in served] == ["PICKUP", "DELIVERY"]
    assert [s.location_id for s in served] == ["C1", "C2"]
    assert served[0].departure <= served[1].arrival


def test_both_ends_of_a_shipment_ride_the_same_vehicle():
    """FR-01's same-vehicle rule. Two vehicles and one shipment: whichever is
    chosen must carry both ends, because the goods cannot teleport."""
    problem = instance((shipment("S1", "C1", "C2", kg=10),), stops=2, vehicles=2)
    solution = solve(problem, iterations=300, seed=0)

    carrying = [route.vehicle_id for route in solution.routes
                if any(s.order_id == "S1" for s in route.steps)]
    assert len(carrying) == 1, f"S1 split across {carrying}"
    assert verify(problem, solution).ok


def test_the_load_rises_at_the_pickup_and_falls_at_the_delivery():
    """A shipment is §6.1's signed load in its clearest form: goods board the
    vehicle at one stop and leave it at another."""
    problem = instance((shipment("S1", "C1", "C2", kg=10),), stops=2)
    solution = solve(problem, iterations=300, seed=0)

    steps = solution.routes[0].steps
    assert steps[0].load_after["kg"] == 0, "leaves the depot empty"
    served = {s.type: s.load_after["kg"] for s in steps if s.order_id}
    assert served["PICKUP"] == 10
    assert served["DELIVERY"] == 0


def test_shipments_and_jobs_coexist():
    """The index trap. PyVRP numbers shipments and clients from zero in
    separate spaces, so a mapper reading `idx` without checking which space it
    is in maps shipment 0 onto job 0 -- and reports a plan that names the wrong
    orders while looking entirely well formed.
    """
    orders = (job("J1", "C3", kg=5), shipment("S1", "C1", "C2", kg=10))
    problem = instance(orders, stops=3)
    solution = solve(problem, iterations=400, seed=0)

    assert verify(problem, solution).ok
    by_id = {}
    for step in steps_of(solution):
        by_id.setdefault(step.order_id, []).append(step.type)
    assert by_id["J1"] == ["DELIVERY"], by_id
    assert by_id["S1"] == ["PICKUP", "DELIVERY"], by_id


def test_the_verifier_catches_a_delivery_placed_before_its_pickup():
    """INV-2 finally has a subject.

    It has been implemented since E-03 and never evaluated, because no shipment
    could reach the verifier. An invariant that is never asked passes, which is
    indistinguishable from an invariant that holds.
    """
    problem = instance((shipment("S1", "C1", "C2", kg=10),), stops=2)
    reversed_plan = Solution(
        problem_id=problem.id,
        routes=(Route(vehicle_id="V1", steps=(
            _step("START", "D", 0),
            _step("DELIVERY", "C2", 120, order_id="S1"),
            _step("PICKUP", "C1", 240, order_id="S1"),
            _step("END", "D", 360),
        )),),
        unassigned=(), objective_breakdown={}, status="FEASIBLE")

    report = verify(problem, reversed_plan)
    assert not report.ok
    assert any(v.invariant == "INV-2" for v in report.violations), \
        [str(v) for v in report.violations]


def test_the_verifier_catches_a_shipment_split_across_vehicles():
    """The other half of INV-2: both ends on one route."""
    problem = instance((shipment("S1", "C1", "C2", kg=10),), stops=2, vehicles=2)
    split = Solution(
        problem_id=problem.id,
        routes=(
            Route(vehicle_id="V1", steps=(_step("START", "D", 0),
                                          _step("PICKUP", "C1", 60, order_id="S1"),
                                          _step("END", "D", 120))),
            Route(vehicle_id="V2", steps=(_step("START", "D", 0),
                                          _step("DELIVERY", "C2", 120, order_id="S1"),
                                          _step("END", "D", 240))),
        ),
        unassigned=(), objective_breakdown={}, status="FEASIBLE")

    report = verify(problem, split)
    assert any(v.invariant == "INV-2" for v in report.violations), \
        [str(v) for v in report.violations]


def _step(kind: str, location: str, at: int, order_id: str | None = None):
    from vrp.model import Step
    return Step(type=kind, location_id=location, arrival=at, start_service=at,
                departure=at + (60 if order_id else 0), order_id=order_id,
                load_after={"kg": 0})
