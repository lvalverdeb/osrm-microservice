"""Heterogeneous fleet, per-vehicle cost, multi-depot, open routes — E-21/T-21.

FR-07 and FR-08, and §3.4's target shape for this business: "six depots, mixed
vehicles, and customer windows is not an exotic combination — it is the ordinary
case." Anything treating the fleet as homogeneous or the depot as singular is
called a stepping stone there, not a deliverable.

Two of the four parts already worked before E-21 and are pinned here as
regressions rather than claimed as new: multiple depots, and a vehicle whose
start and end differ. The two that did not:

**Per-vehicle cost.** Costs lived on `ObjectiveSpec`, one set for the whole
fleet, so a 3.5-tonne van and an artic cost the same per kilometre. That is the
"H" in MDHVRPTW and its absence made the letter decorative.

**Open routes.** `end_location_id=None` silently meant "return to the start",
so a subcontractor who finishes at their last drop was charged for a leg they
never drove. PyVRP accepts `end_depot=None` and ignores it — measured, both
give 4000 m on a 2000 m one-way problem — so the adapter builds a zero-cost
sink instead.
"""

from __future__ import annotations

import pytest

from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

DAY = TimeWindow(start=0, end=12 * 3600)


def line(stops: int, *, leg_m: int = 1000, depots: int = 1) -> tuple:
    """Collinear locations: depot(s) then customers, 1 km apart."""
    names = [f"D{i}" for i in range(depots)] + [f"C{i}" for i in range(stops)]
    size = len(names)
    locations = tuple(Location(id=name, lat=9.9 + i / 1000, lon=-84.0,
                               matrix_index=i)
                      for i, name in enumerate(names))
    distances = tuple(tuple(abs(i - j) * leg_m for j in range(size))
                      for i in range(size))
    durations = tuple(tuple(abs(i - j) * 60 for j in range(size))
                      for i in range(size))
    return locations, durations, distances, names


def problem(vehicles: tuple[Vehicle, ...], stops: int = 2,
            depots: int = 1) -> Problem:
    locations, durations, distances, _ = line(stops, depots=depots)
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"units": 1},
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=60))
        for i in range(stops)
    )
    return Problem(id="het", locations=locations, orders=orders,
                   vehicles=vehicles,
                   matrix=TravelMatrix(version="het-v1", durations=durations,
                                       distances=distances))


def van(vehicle_id: str, **kwargs) -> Vehicle:
    defaults = {"capacities": {"units": 10}, "shift": DAY,
                "start_location_id": "D0", "end_location_id": "D0"}
    return Vehicle(id=vehicle_id, **{**defaults, **kwargs})


# --------------------------------------------------------------------------
# Per-vehicle cost (FR-07)
# --------------------------------------------------------------------------

def test_a_vehicle_carries_its_own_costs():
    """The fields must exist before anything can honour them."""
    truck = van("T1", fixed_cost=90_000, cost_per_metre=3,
                cost_per_second=2, overtime_cost_per_second=5)

    assert truck.fixed_cost == 90_000
    assert truck.cost_per_metre == 3
    assert truck.cost_per_second == 2
    assert truck.overtime_cost_per_second == 5


def test_costs_must_not_be_negative():
    """A negative cost is a vehicle that pays to drive, and a solver will find
    that arbitrage immediately."""
    for field in ("fixed_cost", "cost_per_metre", "cost_per_second",
                  "overtime_cost_per_second"):
        with pytest.raises(Exception, match=field):
            van("bad", **{field: -1})


def _deployed(fleet, stops: int = 2) -> list[str]:
    solution = solve(problem(fleet, stops=stops), iterations=300, seed=0)
    return [route.vehicle_id for route in solution.routes
            if any(step.order_id for step in route.steps)]


def test_the_cheaper_vehicle_is_preferred_when_both_can_do_the_work():
    """FR-07's point, tested by *flipping* the costs rather than asserting one
    outcome.

    Asserting only that "CHEAP" is deployed passes without the feature: with
    equal costs the solver picks one anyway, and it happened to pick that one.
    Confirmed by perturbation — dropping the fixed-cost wiring left the
    single-direction test green. Swapping which vehicle is dear must swap which
    is deployed, and no implementation that ignores the field can do that.
    """
    dear_first = (van("A", fixed_cost=500_000), van("B", fixed_cost=1_000))
    dear_second = (van("A", fixed_cost=1_000), van("B", fixed_cost=500_000))

    assert _deployed(dear_first) == ["B"]
    assert _deployed(dear_second) == ["A"]


def test_the_cheaper_per_metre_vehicle_is_preferred_on_a_long_run():
    """Distance cost, not just the fixed component. Flipped for the same
    reason as the test above."""
    dear_first = (van("A", cost_per_metre=100), van("B", cost_per_metre=1))
    dear_second = (van("A", cost_per_metre=1), van("B", cost_per_metre=100))

    assert _deployed(dear_first, stops=4) == ["B"]
    assert _deployed(dear_second, stops=4) == ["A"]


# --------------------------------------------------------------------------
# Open routes (FR-08, "end-anywhere")
# --------------------------------------------------------------------------

def test_an_open_route_does_not_pay_for_the_leg_home():
    """FR-08's "end-anywhere". A subcontractor finishing at their last drop
    does not drive back, and must not be charged for it.

    Two stops 1 km apart from a depot 1 km before them: closed is 4 km out and
    back, open is 2 km one way.
    """
    closed = solve(problem((van("CLOSED"),), stops=2), iterations=300, seed=0)
    opened = solve(problem((van("OPEN", open_route=True),), stops=2),
                   iterations=300, seed=0)

    assert [s.type for s in opened.routes[0].steps][-1] == "END"
    assert opened.routes[0].steps[-1].location_id == "C1", \
        "an open route ends at the last stop, not the depot"
    assert closed.routes[0].steps[-1].location_id == "D0"


def test_an_open_route_verifies():
    """The verifier must accept a route that legitimately does not return.

    INV-4 walks consecutive steps against the matrix, so a route ending
    somewhere other than its start is exactly the shape that would trip a
    verifier assuming a closed tour.
    """
    solution = solve(problem((van("OPEN", open_route=True),), stops=3),
                     iterations=300, seed=0)
    report = verify(problem((van("OPEN", open_route=True),), stops=3), solution)

    assert report.ok, [str(v) for v in report.violations]


def test_open_and_closed_are_distinguishable_on_the_vehicle():
    """`end_location_id=None` already meant "end where you started", and a
    great deal of code relies on that. Open routes are a separate, explicit
    flag rather than a reinterpretation of the existing one."""
    assert van("A").ends_at == "D0"
    assert not van("A").open_route
    assert van("B", end_location_id=None).ends_at == "D0", "unchanged meaning"
    assert van("C", open_route=True).open_route


# --------------------------------------------------------------------------
# Multi-depot and distinct start/end — regressions, working before E-21
# --------------------------------------------------------------------------

def test_vehicles_may_start_from_different_depots():
    # One unit of capacity each, so both must be deployed and both depots are
    # actually exercised. With slack capacity the solver rightly uses one van
    # and the test would assert nothing about the second depot.
    fleet = (van("V0", start_location_id="D0", end_location_id="D0",
                 capacities={"units": 1}),
             van("V1", start_location_id="D1", end_location_id="D1",
                 capacities={"units": 1}))
    instance = problem(fleet, stops=2, depots=2)
    solution = solve(instance, iterations=300, seed=0)

    assert verify(instance, solution).ok
    starts = {r.vehicle_id: r.steps[0].location_id for r in solution.routes}
    assert starts["V0"] == "D0" and starts["V1"] == "D1"


def test_a_vehicle_may_start_and_end_in_different_places():
    fleet = (van("V0", start_location_id="D0", end_location_id="D1"),)
    instance = problem(fleet, stops=2, depots=2)
    solution = solve(instance, iterations=300, seed=0)

    assert verify(instance, solution).ok
    steps = solution.routes[0].steps
    assert steps[0].location_id == "D0"
    assert steps[-1].location_id == "D1"


# --------------------------------------------------------------------------
# Per-vehicle profile — refused, not silently ignored
# --------------------------------------------------------------------------

def test_a_fleet_with_mixed_profiles_is_refused_rather_than_flattened():
    """FR-07 lists a per-vehicle routing profile, and a profile is a *matrix*.

    A `Problem` pins one matrix, so a bicycle and an artic sharing it would be
    routed identically while appearing to differ. PyVRP supports per-profile
    edge sets, so this is a domain-model gap rather than a solver one — and it
    is refused loudly until the model can carry more than one matrix.
    """
    fleet = (van("VAN", profile="driving"), van("BIKE", profile="cycling"))

    with pytest.raises(NotImplementedError, match="profile"):
        solve(problem(fleet), iterations=50, seed=0)


def test_a_fleet_sharing_one_profile_is_fine():
    """The control: refusing mixed profiles must not refuse the ordinary case."""
    fleet = (van("V0", profile="driving"), van("V1", profile="driving"))
    solution = solve(problem(fleet), iterations=200, seed=0)

    assert verify(problem(fleet), solution).ok
