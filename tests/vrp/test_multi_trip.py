"""Multi-trip, reloading and dock capacity — FR-09, FR-19, §6.8, §6.9, T-28.

§6.8: "A vehicle that empties before its shift ends should return, reload, and
go again... Combined with driving-hours rules this becomes a multi-trip VRPTW
with an embedded driver scheduling problem; it MUST NOT be approximated by
chaining independent single-trip plans, which double-counts driver
availability."

That prohibition is the interesting requirement. Two single-trip plans for one
van look perfectly reasonable side by side and are jointly impossible: each
assumes a full duty, so the driver works sixteen hours. The test that matters
is not "can a van do two trips" but "does the second trip cost the driver
anything" — `test_a_reload_consumes_the_driver_s_day` is that one.

§6.9: "Depot loading bays are finite. If 40 vehicles are planned to depart at
06:00 and there are 8 bays, the plan is fiction." Dock capacity is FR-19 and a
SHOULD rather than a MUST, so it is checked but not enforced in the search.
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
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.verify import verify

DAY = TimeWindow(start=0, end=14 * 3600)


def instance(orders: tuple[Order, ...], vehicles: tuple[Vehicle, ...],
             stops: int, leg: int = 600,
             depot: Location | None = None) -> Problem:
    size = stops + 1
    depot = depot or Location(id="D", lat=9.9, lon=-84.0, matrix_index=0)
    locations = (depot, *(
        Location(id=f"C{i}", lat=9.9 + i / 1000, lon=-84.0, matrix_index=i)
        for i in range(1, size)))
    grid = tuple(tuple(abs(i - j) * leg for j in range(size)) for i in range(size))
    return Problem(id="mt", locations=locations, orders=orders,
                   vehicles=vehicles,
                   matrix=TravelMatrix(version="mt", durations=grid,
                                       distances=grid))


def an_order(order_id: str, stop: str, kg: int) -> Order:
    return Order(id=order_id, kind="JOB", quantities={"kg": kg},
                 delivery=StopSpec(location_id=stop, time_windows=(DAY,),
                                   service_fixed=60))


def a_van(**kwargs) -> Vehicle:
    defaults = {"capacities": {"kg": 100}, "shift": DAY,
                "start_location_id": "D", "end_location_id": "D"}
    return Vehicle(id=kwargs.pop("id", "V1"), **{**defaults, **kwargs})


def failures(report, invariant: str) -> list[str]:
    return [v.detail for v in report.violations if v.invariant == invariant]


# --------------------------------------------------------------------------
# FR-09: the reload itself
# --------------------------------------------------------------------------

def test_a_vehicle_may_carry_more_than_its_capacity_across_two_trips():
    """Three 60 kg drops and a 100 kg van: impossible in one trip, ordinary in
    two. Without reloading the third order is simply unservable."""
    from vrp.solve.pyvrp_adapter import solve

    orders = tuple(an_order(f"O{i}", f"C{i}", kg=60) for i in (1, 2, 3))
    problem = instance(orders, (a_van(reload_locations=frozenset({"D"}),
                                      max_reloads=2, reload_duration=900),),
                       stops=3)
    solution = solve(problem, iterations=800, seed=0)

    assert not solution.unassigned, "the van should reload rather than decline"
    assert verify(problem, solution).ok


def test_without_a_reload_the_same_instance_cannot_be_served():
    """The control: reloading is doing the work, not slack somewhere else."""
    from vrp.solve.pyvrp_adapter import solve

    orders = tuple(an_order(f"O{i}", f"C{i}", kg=60) for i in (1, 2, 3))
    problem = instance(orders, (a_van(),), stops=3)
    solution = solve(problem, iterations=800, seed=0)

    assert solution.unassigned or solution.status == "INFEASIBLE"


def test_a_reload_resets_the_load():
    """§6.8: reload "resets load to zero (or to a newly loaded state)". A
    verifier that carried the load across a reload would reject every legal
    multi-trip plan; one that ignored the step would accept overloads."""
    problem = instance((an_order("O1", "C1", kg=60), an_order("O2", "C2", kg=60)),
                       (a_van(reload_locations=frozenset({"D"}), max_reloads=1,
                              reload_duration=900),), stops=2)
    plan = Solution(
        problem_id=problem.id, unassigned=(), objective_breakdown={},
        status="FEASIBLE",
        routes=(Route(vehicle_id="V1", steps=(
            Step(type="START", location_id="D", arrival=0, start_service=0,
                 departure=0, load_after={"kg": 60}),
            Step(type="DELIVERY", location_id="C1", order_id="O1", arrival=600,
                 start_service=600, departure=660, load_after={"kg": 0}),
            Step(type="RELOAD", location_id="D", arrival=1260,
                 start_service=1260, departure=2160, load_after={"kg": 60}),
            Step(type="DELIVERY", location_id="C2", order_id="O2", arrival=3360,
                 start_service=3360, departure=3420, load_after={"kg": 0}),
            Step(type="END", location_id="D", arrival=4620, start_service=4620,
                 departure=4620, load_after={"kg": 0}),
        )),))

    report = verify(problem, plan)
    assert not failures(report, "INV-5"), failures(report, "INV-5")


def test_reloading_where_it_is_not_permitted_is_rejected():
    """A van cannot reload at a customer's doorstep. `reload_locations` names
    the depots and satellites where stock is."""
    problem = instance((an_order("O1", "C1", kg=60),),
                       (a_van(reload_locations=frozenset({"D"}), max_reloads=1,
                              reload_duration=900),), stops=2)
    plan = Solution(
        problem_id=problem.id, unassigned=(), objective_breakdown={},
        status="FEASIBLE",
        routes=(Route(vehicle_id="V1", steps=(
            Step(type="START", location_id="D", arrival=0, start_service=0,
                 departure=0, load_after={"kg": 60}),
            Step(type="RELOAD", location_id="C2", arrival=1200,
                 start_service=1200, departure=2100, load_after={"kg": 60}),
            Step(type="DELIVERY", location_id="C1", order_id="O1", arrival=2700,
                 start_service=2700, departure=2760, load_after={"kg": 0}),
            Step(type="END", location_id="D", arrival=3360, start_service=3360,
                 departure=3360, load_after={"kg": 0}),
        )),))

    assert failures(verify(problem, plan), "INV-11")


def test_more_reloads_than_permitted_is_rejected():
    problem = instance((an_order("O1", "C1", kg=10),),
                       (a_van(reload_locations=frozenset({"D"}), max_reloads=1,
                              reload_duration=900),), stops=2)
    steps = [Step(type="START", location_id="D", arrival=0, start_service=0,
                  departure=0, load_after={"kg": 10})]
    clock = 0
    for _ in range(2):
        steps.append(Step(type="RELOAD", location_id="D", arrival=clock,
                          start_service=clock, departure=clock + 900,
                          load_after={"kg": 10}))
        clock += 900
    steps.append(Step(type="DELIVERY", location_id="C1", order_id="O1",
                      arrival=clock + 600, start_service=clock + 600,
                      departure=clock + 660, load_after={"kg": 0}))
    steps.append(Step(type="END", location_id="D", arrival=clock + 1260,
                      start_service=clock + 1260, departure=clock + 1260,
                      load_after={"kg": 0}))
    plan = Solution(problem_id=problem.id, unassigned=(),
                    objective_breakdown={}, status="FEASIBLE",
                    routes=(Route(vehicle_id="V1", steps=tuple(steps)),))

    assert failures(verify(problem, plan), "INV-11")


# --------------------------------------------------------------------------
# §6.8's prohibition: the second trip must cost the driver
# --------------------------------------------------------------------------

def test_a_reload_consumes_the_driver_s_day():
    """§6.8: chaining independent single-trip plans "double-counts driver
    availability".

    Two trips through one duty, under hours of service. The reload's own
    duration and the driving either side of it all come out of the same day, so
    a van that could legally do one trip cannot necessarily do two.
    """
    from vrp.hos.schedule import schedule_route

    orders = tuple(an_order(f"O{i}", f"C{i}", kg=60) for i in (1, 2))
    van = a_van(reload_locations=frozenset({"D"}), max_reloads=1,
                reload_duration=3600, hos_rules="EU-561")
    # Legs of 4h: one trip out and back is 8h of driving, which fits EU-561's
    # 9h day. Two trips is 16h, which does not.
    problem = instance(orders, (van,), stops=2, leg=4 * 3600)

    one_trip = schedule_route(problem, "V1", ["O1"], None)
    assert one_trip.steps, "sanity: a single trip is schedulable"

    both = schedule_route(problem, "V1", ["O1", "O2"],
                          __import__("vrp.hos", fromlist=["EU_561"]).EU_561)
    assert not both.legal, (
        "two trips fitted one duty; the driver's day was double-counted")


# --------------------------------------------------------------------------
# FR-19 / §6.9: dock capacity
# --------------------------------------------------------------------------

def test_more_vehicles_loading_than_there_are_bays_is_reported():
    """§6.9: "If 40 vehicles are planned to depart at 06:00 and there are 8
    bays, the plan is fiction." Three vans, one bay, all departing together."""
    depot = Location(id="D", lat=9.9, lon=-84.0, matrix_index=0, dock_capacity=1)
    orders = tuple(an_order(f"O{i}", f"C{i}", kg=10) for i in (1, 2, 3))
    fleet = tuple(a_van(id=f"V{n}") for n in (1, 2, 3))
    problem = instance(orders, fleet, stops=3, depot=depot)

    routes = []
    for n, order in enumerate(orders, start=1):
        routes.append(Route(vehicle_id=f"V{n}", steps=(
            Step(type="START", location_id="D", arrival=0, start_service=0,
                 departure=1800, load_after={"kg": 10}),
            Step(type="DELIVERY", location_id=order.delivery.location_id,
                 order_id=order.id, arrival=1800 + 600 * n,
                 start_service=1800 + 600 * n, departure=1860 + 600 * n,
                 load_after={"kg": 0}),
            Step(type="END", location_id="D", arrival=2460 + 1200 * n,
                 start_service=2460 + 1200 * n, departure=2460 + 1200 * n,
                 load_after={"kg": 0}),
        )))
    plan = Solution(problem_id=problem.id, routes=tuple(routes), unassigned=(),
                    objective_breakdown={}, status="FEASIBLE")

    assert failures(verify(problem, plan), "INV-12")


def test_vehicles_within_the_bay_count_are_accepted():
    """The control, and the reason `dock_capacity` defaults to unlimited: most
    depots are not the constraint, and treating an unset value as zero would
    make every plan fiction."""
    depot = Location(id="D", lat=9.9, lon=-84.0, matrix_index=0, dock_capacity=3)
    orders = tuple(an_order(f"O{i}", f"C{i}", kg=10) for i in (1, 2))
    fleet = tuple(a_van(id=f"V{n}") for n in (1, 2))
    problem = instance(orders, fleet, stops=2, depot=depot)

    routes = []
    for n, order in enumerate(orders, start=1):
        routes.append(Route(vehicle_id=f"V{n}", steps=(
            Step(type="START", location_id="D", arrival=0, start_service=0,
                 departure=1800, load_after={"kg": 10}),
            Step(type="DELIVERY", location_id=order.delivery.location_id,
                 order_id=order.id, arrival=1800 + 600 * n,
                 start_service=1800 + 600 * n, departure=1860 + 600 * n,
                 load_after={"kg": 0}),
            Step(type="END", location_id="D", arrival=2460 + 1200 * n,
                 start_service=2460 + 1200 * n, departure=2460 + 1200 * n,
                 load_after={"kg": 0}),
        )))
    plan = Solution(problem_id=problem.id, routes=tuple(routes), unassigned=(),
                    objective_breakdown={}, status="FEASIBLE")

    assert not failures(verify(problem, plan), "INV-12")


def test_a_depot_with_no_declared_capacity_is_unconstrained():
    """The van must actually occupy a bay for this to test anything.

    An earlier version gave START a zero-length stay, so no span was recorded
    and the capacity check never ran -- perturbing the default to zero left the
    test green.
    """
    orders = (an_order("O1", "C1", kg=10),)
    problem = instance(orders, (a_van(),), stops=1)
    plan = Solution(
        problem_id=problem.id, unassigned=(), objective_breakdown={},
        status="FEASIBLE",
        routes=(Route(vehicle_id="V1", steps=(
            Step(type="START", location_id="D", arrival=0, start_service=0,
                 departure=1800, load_after={"kg": 10}),
            Step(type="DELIVERY", location_id="C1", order_id="O1", arrival=2400,
                 start_service=2400, departure=2460, load_after={"kg": 0}),
            Step(type="END", location_id="D", arrival=3060, start_service=3060,
                 departure=3060, load_after={"kg": 0}),
        )),))
    assert not failures(verify(problem, plan), "INV-12")


# --------------------------------------------------------------------------
# Model validation
# --------------------------------------------------------------------------

def test_reload_settings_must_be_coherent():
    with pytest.raises(Exception, match="reload_duration"):
        a_van(reload_locations=frozenset({"D"}), max_reloads=1,
              reload_duration=-1)
    with pytest.raises(Exception, match="max_reloads"):
        a_van(reload_locations=frozenset({"D"}), max_reloads=-1)
    with pytest.raises(Exception, match="reload_locations"):
        a_van(max_reloads=2)


def test_dock_capacity_must_not_be_negative():
    with pytest.raises(Exception, match="dock_capacity"):
        Location(id="D", lat=9.9, lon=-84.0, matrix_index=0, dock_capacity=-1)
