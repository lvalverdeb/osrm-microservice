"""Skills, incompatibility and site access — FR-10, FR-11, §6.5, T-22, E-22.

§6.5 names three kinds of compatibility and they fail in three different ways:

* **Vehicle↔order.** The load needs a tail lift, refrigeration, ADR
  certification. `Vehicle.skills` and `Order.required_skills` have existed
  since E-01 and `preflight` has checked them since E-14 — but nothing checked
  a *finished plan* against them. An order could be assigned to a van without
  the equipment and every invariant would pass.
* **Order↔order.** "Foodstuff must not share a compartment with hazardous
  goods; competing retailers may forbid co-loading." Not modelled at all before
  E-22, which is why E-14 had to declare `INCOMPATIBLE_ONLY` unimplemented.
* **Vehicle↔site.** Weight limits, low-emission zones, restricted streets. A
  7.5-tonne lorry sent to an address only a van can reach produces a plan that
  looks perfect and cannot be driven.

The first is the interesting one to test, because the machinery already existed
and gave every appearance of working. A skill requirement that nothing enforces
is worse than no skill model: it invites people to rely on it.
"""

from __future__ import annotations

import pytest

from vrp.diagnose import preflight
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

DAY = TimeWindow(start=0, end=12 * 3600)


def instance(orders: tuple[Order, ...], vehicles: tuple[Vehicle, ...],
             locations: tuple[Location, ...] | None = None) -> Problem:
    if locations is None:
        locations = (Location(id="D", lat=9.9, lon=-84.0, matrix_index=0),
                     Location(id="C1", lat=9.91, lon=-84.0, matrix_index=1),
                     Location(id="C2", lat=9.92, lon=-84.0, matrix_index=2))
    size = len(locations)
    grid = tuple(tuple(abs(i - j) * 600 for j in range(size)) for i in range(size))
    return Problem(id="cmp", locations=locations, orders=orders,
                   vehicles=vehicles,
                   matrix=TravelMatrix(version="c", durations=grid, distances=grid))


def an_order(order_id: str, stop: str, **kwargs) -> Order:
    return Order(id=order_id, kind="JOB", quantities={"kg": 1},
                 delivery=StopSpec(location_id=stop, time_windows=(DAY,),
                                   service_fixed=60), **kwargs)


def a_van(vehicle_id: str = "V1", **kwargs) -> Vehicle:
    defaults = {"capacities": {"kg": 100}, "shift": DAY,
                "start_location_id": "D", "end_location_id": "D"}
    return Vehicle(id=vehicle_id, **{**defaults, **kwargs})


def plan(problem: Problem, assignment: dict[str, list[str]]) -> Solution:
    """A timeline honest about travel, so INV-4 does not fire on the fixture.

    An earlier version added a flat 600 s per leg, which is right only when
    every stop is adjacent to the depot. INV-4 caught it -- correctly -- and
    the resulting failure looked like a compatibility bug.
    """
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    routes = []
    for vehicle_id, order_ids in assignment.items():
        steps = [Step(type="START", location_id="D", arrival=0,
                      start_service=0, departure=0)]
        clock, here = 0, index["D"]
        for order_id in order_ids:
            stop = problem.order(order_id).delivery
            there = index[stop.location_id]
            clock += problem.matrix.duration(here, there)
            steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                              order_id=order_id, arrival=clock,
                              start_service=clock, departure=clock + 60))
            clock += 60
            here = there
        clock += problem.matrix.duration(here, index["D"])
        steps.append(Step(type="END", location_id="D", arrival=clock,
                          start_service=clock, departure=clock))
        routes.append(Route(vehicle_id=vehicle_id, steps=tuple(steps)))
    served = {o for ids in assignment.values() for o in ids}
    return Solution(problem_id=problem.id, routes=tuple(routes),
                    unassigned=tuple({"order_id": o.id, "reason_code": "NOT_PLACED",
                                      "explanation": "-"}
                                     for o in problem.orders if o.id not in served),
                    objective_breakdown={}, status="FEASIBLE")


def failures(report, invariant: str = "INV-10") -> list[str]:
    return [v.detail for v in report.violations if v.invariant == invariant]


# --------------------------------------------------------------------------
# Vehicle to order: the machinery that existed and enforced nothing
# --------------------------------------------------------------------------

def test_a_plan_assigning_an_order_to_an_unskilled_vehicle_is_rejected():
    """`required_skills` has existed since E-01 and been checked by pre-flight
    since E-14. Nothing checked a finished plan, so a plan could put a
    tail-lift load on a van without one and pass every invariant."""
    problem = instance((an_order("O1", "C1",
                                 required_skills=frozenset({"TAIL_LIFT"})),),
                       (a_van(),))
    report = verify(problem, plan(problem, {"V1": ["O1"]}))

    assert not report.ok
    assert failures(report), [str(v) for v in report.violations]


def test_a_skilled_vehicle_is_accepted():
    problem = instance((an_order("O1", "C1",
                                 required_skills=frozenset({"TAIL_LIFT"})),),
                       (a_van(skills=frozenset({"TAIL_LIFT", "ADR"})),))
    assert verify(problem, plan(problem, {"V1": ["O1"]})).ok


# --------------------------------------------------------------------------
# Order to order (FR-10)
# --------------------------------------------------------------------------

def test_incompatible_orders_may_not_share_a_route():
    """§6.5's example: "Foodstuff must not share a compartment with hazardous
    goods". The two are individually fine and only their pairing is not."""
    orders = (an_order("FOOD", "C1", order_class="FOOD"),
              an_order("HAZMAT", "C2", order_class="HAZMAT",
                       incompatible_with=frozenset({"FOOD"})))
    problem = instance(orders, (a_van(),))

    together = verify(problem, plan(problem, {"V1": ["FOOD", "HAZMAT"]}))
    assert not together.ok
    assert failures(together)


def test_incompatible_orders_on_separate_routes_are_fine():
    """The control: the constraint is about sharing a vehicle, not about the
    orders existing."""
    orders = (an_order("FOOD", "C1", order_class="FOOD"),
              an_order("HAZMAT", "C2", order_class="HAZMAT",
                       incompatible_with=frozenset({"FOOD"})))
    problem = instance(orders, (a_van("V1"), a_van("V2")))

    apart = verify(problem, plan(problem, {"V1": ["FOOD"], "V2": ["HAZMAT"]}))
    assert apart.ok, [str(v) for v in apart.violations]


def test_incompatibility_is_symmetric_in_effect():
    """Declared on one side only. A model that checked the declaring order's
    class but not the other's would let the same pair through whenever the
    orders happened to be listed the other way round."""
    orders = (an_order("HAZMAT", "C1", order_class="HAZMAT",
                       incompatible_with=frozenset({"FOOD"})),
              an_order("FOOD", "C2", order_class="FOOD"))
    problem = instance(orders, (a_van(),))

    assert failures(verify(problem, plan(problem, {"V1": ["HAZMAT", "FOOD"]})))
    assert failures(verify(problem, plan(problem, {"V1": ["FOOD", "HAZMAT"]})))


def test_preflight_reports_an_order_incompatible_with_everything_else():
    """E-14 declared INCOMPATIBLE_ONLY unimplemented because nothing could
    express it. It can now."""
    orders = (an_order("HAZMAT", "C1", order_class="HAZMAT",
                       incompatible_with=frozenset({"FOOD"})),
              an_order("FOOD1", "C2", order_class="FOOD"))
    # One vehicle, and the two orders cannot share it.
    problem = instance(orders, (a_van(),))

    found = preflight(problem)
    assert "HAZMAT" in found or "FOOD1" in found
    codes = {finding.code for finding in found.values()}
    assert codes == {"INCOMPATIBLE_ONLY"}, codes


# --------------------------------------------------------------------------
# Vehicle to site (FR-11)
# --------------------------------------------------------------------------

def restricted(stop_id: str, index: int, **access) -> Location:
    return Location(id=stop_id, lat=9.9 + index / 1000, lon=-84.0,
                    matrix_index=index, **access)


def test_a_vehicle_of_the_wrong_class_may_not_serve_a_restricted_site():
    """A 7.5-tonne lorry sent to a street only a van can enter yields a plan
    that looks perfect and cannot be driven."""
    locations = (Location(id="D", lat=9.9, lon=-84.0, matrix_index=0),
                 restricted("C1", 1, access_classes=frozenset({"VAN"})))
    problem = instance((an_order("O1", "C1"),),
                       (a_van(access_class="RIGID"),), locations)

    report = verify(problem, plan(problem, {"V1": ["O1"]}))
    assert not report.ok
    assert failures(report)


def test_the_permitted_class_is_accepted():
    locations = (Location(id="D", lat=9.9, lon=-84.0, matrix_index=0),
                 restricted("C1", 1, access_classes=frozenset({"VAN", "BIKE"})))
    problem = instance((an_order("O1", "C1"),),
                       (a_van(access_class="VAN"),), locations)
    assert verify(problem, plan(problem, {"V1": ["O1"]})).ok


def test_a_site_with_no_restriction_admits_anything():
    """Empty means unrestricted, not "admits nothing" -- the inverse reading
    would make every ordinary address unservable."""
    problem = instance((an_order("O1", "C1"),), (a_van(access_class="ARTIC"),))
    assert verify(problem, plan(problem, {"V1": ["O1"]})).ok


def test_a_site_weight_limit_is_respected():
    """FR-11 lists weight among the access restrictions, and it is the one that
    collapses bridges rather than merely annoying a resident."""
    locations = (Location(id="D", lat=9.9, lon=-84.0, matrix_index=0),
                 restricted("C1", 1, max_vehicle_kg=3_500))
    problem = instance((an_order("O1", "C1"),),
                       (a_van(gross_weight_kg=7_500),), locations)

    assert failures(verify(problem, plan(problem, {"V1": ["O1"]})))

    lighter = instance((an_order("O1", "C1"),),
                       (a_van(gross_weight_kg=3_400),), locations)
    assert verify(lighter, plan(lighter, {"V1": ["O1"]})).ok


def test_preflight_reports_a_site_no_vehicle_may_enter():
    locations = (Location(id="D", lat=9.9, lon=-84.0, matrix_index=0),
                 restricted("C1", 1, access_classes=frozenset({"BIKE"})))
    problem = instance((an_order("O1", "C1"),),
                       (a_van(access_class="RIGID"),), locations)

    assert preflight(problem)["O1"].code == "NO_ELIGIBLE_VEHICLE"


# --------------------------------------------------------------------------
# Model validation
# --------------------------------------------------------------------------

def test_an_order_declaring_incompatibility_must_have_a_class():
    """Otherwise nothing can be incompatible *with* it, and the declaration is
    one-directional in a way nobody intends."""
    with pytest.raises(Exception, match="order_class"):
        an_order("O1", "C1", incompatible_with=frozenset({"FOOD"}))


def test_a_negative_weight_limit_is_refused():
    with pytest.raises(Exception, match="max_vehicle_kg"):
        restricted("C1", 1, max_vehicle_kg=-1)
