"""The adversarial instances of the catalogue's §11 — `UC-060`…`UC-074`.

`CAT-VRP-003` §11 introduces these as "not customer scenarios. These break
implementations, and each has caused a production incident somewhere." §13.2
says where they belong: "hand-built and tiny, in the fast tier, run on every
commit." Both properties are load-bearing. An adversarial instance that takes a
second to solve stops being run, and one that needs a generator stops being
readable.

They are the first tests in this repository keyed to the catalogue rather than
to the design document. The `E-xx` examples answer "does the engine satisfy this
requirement"; these answer "does it survive this instance", which is a different
question with a different failure mode — every one of these passes a
requirements review and fails in production.

Each test states its entry's `Breaks` line as its name and its docstring, so a
failure reads as the operational bug it is rather than as an assertion about an
API.
"""

from __future__ import annotations

import math

import pytest

from vrp.decompose import solve_decomposed
from vrp.diagnose import preflight
from vrp.evaluator import evaluate
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
from vrp.osrm import Snap
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

DAY = TimeWindow(start=0, end=12 * 3600)


# --------------------------------------------------------------------------
# Builders. Tiny by construction: every instance below is a handful of stops.
# --------------------------------------------------------------------------

def grid(size: int, *, leg: int = 600, unreachable: set[tuple[int, int]] = frozenset()
         ) -> TravelMatrix:
    """A uniform matrix, optionally with explicit unreachable arcs (MTX-5)."""
    rows = []
    for i in range(size):
        row = []
        for j in range(size):
            if (i, j) in unreachable or (j, i) in unreachable:
                row.append(UNREACHABLE)
            else:
                row.append(0 if i == j else abs(i - j) * leg)
        rows.append(tuple(row))
    return TravelMatrix(version="path-v1", durations=tuple(rows),
                        distances=tuple(rows))


def sites(count: int, *, lat: float = 9.9, lon: float = -84.0,
          step: float = 0.01) -> tuple[Location, ...]:
    return tuple(Location(id="D" if i == 0 else f"C{i}",
                          lat=lat + i * step, lon=lon, matrix_index=i)
                 for i in range(count))


def drop(order_id: str, stop: str, *, windows: tuple[TimeWindow, ...] = (DAY,),
         service: int = 60, **quantities) -> Order:
    return Order(id=order_id, kind="JOB", quantities=quantities or {"kg": 1},
                 delivery=StopSpec(location_id=stop, time_windows=windows,
                                   service_fixed=service))


def collect(order_id: str, stop: str, **quantities) -> Order:
    return Order(id=order_id, kind="JOB", quantities=quantities,
                 pickup=StopSpec(location_id=stop, time_windows=(DAY,),
                                 service_fixed=60))


def van(vehicle_id: str = "V1", **kwargs) -> Vehicle:
    defaults = {"capacities": {"kg": 100}, "shift": DAY,
                "start_location_id": "D", "end_location_id": "D"}
    return Vehicle(id=vehicle_id, **{**defaults, **kwargs})


def instance(orders, vehicles, *, locations=None, matrix=None,
             locks=()) -> Problem:
    locations = locations or sites(len(orders) + 1)
    return Problem(id="path", locations=locations, orders=tuple(orders),
                   vehicles=tuple(vehicles), locks=tuple(locks),
                   matrix=matrix or grid(len(locations)))


def assignment_of(solution) -> dict[str, list[str]]:
    return {route.vehicle_id: [s.order_id for s in route.steps if s.order_id]
            for route in solution.routes}


def served(solution) -> set[str]:
    return {order_id for seq in assignment_of(solution).values() for order_id in seq}


# --------------------------------------------------------------------------
# UC-060 — order exceeding every vehicle's capacity
# --------------------------------------------------------------------------

def test_uc060_an_unliftable_order_is_named_at_preflight_not_after_a_solve():
    """Breaks: reporting it unassigned after a full solve.

    The search cannot place it, so a quarter of an hour goes on proving what one
    comparison against the largest vehicle settles before the solver starts.
    """
    problem = instance((drop("BIG", "C1", kg=10_000), drop("O2", "C2", kg=1)),
                       (van(capacities={"kg": 100}),))

    findings = preflight(problem)

    assert findings["BIG"].code == "CAPACITY_EXCEEDED"
    assert "O2" not in findings, "a routable order must not be swept up with it"


# --------------------------------------------------------------------------
# UC-061 — geocode on an island or in a pedestrian precinct
# --------------------------------------------------------------------------

def test_uc061_an_unreachable_stop_is_a_sentinel_not_a_large_number():
    """Breaks: returning a large finite distance.

    A sentinel that is merely big is a number the optimiser will trade against,
    so the stop is planned, dispatched, and found undeliverable by a driver.
    """
    assert UNREACHABLE < 0, (
        "the sentinel must be outside the range of any real cost, or a "
        "minimising search will treat an unreachable arc as an expensive one")

    problem = instance((drop("ISLAND", "C1"),), (van(),),
                       matrix=grid(2, unreachable={(0, 1)}))

    assert preflight(problem)["ISLAND"].code == "TIME_WINDOW_UNREACHABLE"


# --------------------------------------------------------------------------
# UC-062 — zero-width and inverted time windows
# --------------------------------------------------------------------------

def test_uc062_a_zero_width_window_is_an_appointment_not_an_error():
    """Breaks: conflating the two. Zero-width is a legitimate appointment the
    plan must hit exactly."""
    noon = TimeWindow(start=6 * 3600, end=6 * 3600)
    problem = instance((drop("APPT", "C1", windows=(noon,)),), (van(),))

    solution = solve(problem, iterations=200, seed=0)

    assert served(solution) == {"APPT"}
    assert verify(problem, solution).ok


def test_uc062_an_inverted_window_is_a_validation_error_not_an_infeasibility():
    """Breaks: answering "infeasible" to a corrupt record, which hides a data
    error behind a plausible routing result."""
    with pytest.raises(ValidationError):
        TimeWindow(start=7 * 3600, end=5 * 3600)


# --------------------------------------------------------------------------
# UC-063 — route crossing midnight, shift crossing a DST boundary
# --------------------------------------------------------------------------

def test_uc063_a_duty_crossing_midnight_is_measured_in_elapsed_seconds():
    """Breaks: arithmetic on local wall-clock times.

    The model carries no wall-clock type at all — every instant is whole seconds
    from the horizon's origin — so midnight is not a boundary and a repeated or
    missing DST hour cannot be represented. That is why this passes, and it is
    worth pinning: the first field typed as a local time re-opens the bug.
    """
    night = TimeWindow(start=20 * 3600, end=30 * 3600)   # 20:00 to 06:00 next day
    # The second stop is due after midnight, so the duty has to cross it: a
    # planner that wrapped at 86,400 would find no legal arrival at all.
    after_midnight = TimeWindow(start=25 * 3600, end=26 * 3600)
    problem = Problem(
        id="night", locations=sites(3),
        orders=(drop("O1", "C1", windows=(night,)),
                drop("O2", "C2", windows=(after_midnight,))),
        vehicles=(van(shift=night),), matrix=grid(3))

    solution = solve(problem, iterations=300, seed=0)

    assert served(solution) == {"O1", "O2"}
    report = verify(problem, solution)
    assert report.ok, report.violations
    arrivals = {s.order_id: s.arrival for s in solution.routes[0].steps if s.order_id}
    assert arrivals["O2"] > 24 * 3600, "the stop after midnight is served after it"


# --------------------------------------------------------------------------
# UC-064 — driver arriving with hours already consumed
# --------------------------------------------------------------------------

def test_uc064_a_partly_spent_clock_binds_before_the_statutory_maximum():
    """Breaks: planning from a full clock.

    Every duty is built against nine hours the driver does not have, so the
    first break falls too late and the plan is illegal before the van leaves.
    """
    long_day = TimeWindow(start=0, end=14 * 3600)
    far = TravelMatrix(version="far",
                       durations=((0, 4 * 3600), (4 * 3600, 0)),
                       distances=((0, 100_000), (100_000, 0)))
    problem = Problem(id="hos", locations=sites(2),
                      orders=(drop("O1", "C1", windows=(long_day,)),),
                      vehicles=(van(shift=long_day, hos_rules="EU-561"),),
                      matrix=far)

    fresh = schedule_route(problem, "V1", ["O1"], EU_561)
    spent = schedule_route(problem, "V1", ["O1"], EU_561,
                           initial_state=DriverState(drive_used=4 * 3600,
                                                     duty_used=4 * 3600,
                                                     since_last_break=4 * 3600))

    from_fresh = _break_starts(fresh)
    from_spent = _break_starts(spent)

    assert from_fresh, "4.5h of driving compels a break under EC-561/2006 Art.7"
    assert from_spent, "a driver 4h into the drive limit must break sooner still"
    assert from_spent[0] < from_fresh[0], (
        "the carried-in hours must pull the break earlier, not leave it where a "
        f"full clock would put it: spent {from_spent[0]}, fresh {from_fresh[0]}")


def _break_starts(scheduled) -> list[int]:
    return sorted(step.start_service for step in scheduled.steps
                  if step.type == "BREAK")


# --------------------------------------------------------------------------
# UC-065 — every order in the same one-hour window
# --------------------------------------------------------------------------

def test_uc065_window_overlap_sets_the_fleet_size_and_the_window_is_not_relaxed():
    """Breaks: widening the window to fit the fleet.

    The true answer is that the work needs more vehicles than exist, and a
    solver that quietly relaxes the constraint returns a plan every stop of
    which is late.
    """
    hour = TimeWindow(start=8 * 3600, end=9 * 3600)
    # Three stops an hour apart from each other, all due inside the same hour.
    far = grid(4, leg=3_600)
    orders = tuple(drop(f"O{i}", f"C{i}", windows=(hour,)) for i in (1, 2, 3))

    one_van = Problem(id="one", locations=sites(4), orders=orders,
                      vehicles=(van("V1"),), matrix=far)
    three_vans = Problem(id="three", locations=sites(4), orders=orders,
                         vehicles=tuple(van(f"V{i}") for i in (1, 2, 3)),
                         matrix=far)

    scarce = solve(one_van, iterations=400, seed=0)
    ample = solve(three_vans, iterations=400, seed=0)

    # One van physically cannot make three stops an hour apart inside one hour.
    # The engine may drop stops or report the plan infeasible; what it may not
    # do is hand back a feasible plan serving all three.
    assert not (scarce.status == "FEASIBLE" and len(served(scarce)) == 3), (
        "a feasible plan serving all three means the window was relaxed")
    assert served(ample) == {"O1", "O2", "O3"}
    assert verify(three_vans, ample).ok, "fleet size follows window overlap"


# --------------------------------------------------------------------------
# UC-066 — totals fit but peak load does not
# --------------------------------------------------------------------------

def test_uc066_a_route_within_its_totals_is_rejected_on_peak_load():
    """Breaks: checking capacity against route totals.

    The vehicle is over capacity at a stop it is nominally emptying, which is
    the canonical production capacity bug and is invisible to aggregate tests.
    """
    # Deliveries total 60 and pickups total 80, both inside a 100kg van. The
    # windows force the pickup first, so a shared route carries 60 outbound and
    # collects 80 on top of it: 140 at C2, which no total ever shows.
    morning = TimeWindow(start=8 * 3600, end=9 * 3600)
    afternoon = TimeWindow(start=14 * 3600, end=15 * 3600)
    shift = TimeWindow(start=7 * 3600, end=17 * 3600)
    orders = (Order(id="PICK", kind="JOB", quantities={"kg": 80},
                    pickup=StopSpec(location_id="C2", time_windows=(morning,),
                                    service_fixed=60)),
              drop("DROP", "C1", windows=(afternoon,), kg=60))

    one = instance(orders, (van(capacities={"kg": 100}, shift=shift),))
    two = instance(orders, (van("V1", capacities={"kg": 100}, shift=shift),
                            van("V2", capacities={"kg": 100}, shift=shift)))

    assert sum(o.quantities["kg"] for o in orders if o.pickup) <= 100
    assert sum(o.quantities["kg"] for o in orders if o.delivery) <= 100

    shared = solve(one, iterations=400, seed=0)
    assert not (shared.status == "FEASIBLE" and len(served(shared)) == 2), (
        "both orders on one van peaks at 140kg in a 100kg van; a plan that "
        "reports feasible has checked the totals rather than the peak")

    split = solve(two, iterations=400, seed=0)
    assert verify(two, split).ok, verify(two, split).violations
    for sequence in assignment_of(split).values():
        assert not {"PICK", "DROP"} <= set(sequence)


# --------------------------------------------------------------------------
# UC-067 — mutually incompatible but individually feasible orders
# --------------------------------------------------------------------------

def test_uc067_incompatibility_is_a_property_of_the_route_not_of_the_order():
    """Breaks: testing compatibility per order at assignment time.

    Each passes alone and the pair is illegal only once both are aboard.
    """
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

    assert preflight(problem) == {}, "each order is servable on its own"

    # The field is consumed with set algebra, so a tuple that survives
    # construction raises TypeError inside the verifier instead of naming the
    # caller that built it.
    with pytest.raises(ValidationError):
        Order(id="X", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id="C1", time_windows=(DAY,)),
              order_class="HAZARDOUS", incompatible_with=("FOODSTUFF",))

    solution = solve(problem, iterations=400, seed=0)

    assert served(solution) == {"FOOD", "HAZ"}
    violations = verify(problem, solution).violations
    assert all(v.invariant == "INV-10" for v in violations), violations
    assert violations, (
        "if the search has learned the constraint, this instance is now clean "
        "and test_uc067_the_search_itself_does_not_know_about_incompatibility "
        "should be un-expected-failed along with UC-067's catalogue status")


@pytest.mark.xfail(strict=True, reason=(
    "T-22 built order-class incompatibility as a check, not as a constraint: "
    "INV-10 lives in the verifier and in pre-flight, and neither the PyVRP "
    "adapter nor the local search knows about `incompatible_with`. So the "
    "search happily loads a hazardous class alongside foodstuff and the plan is "
    "rejected after the fact. `UC-067` is PARTIALLY_MODELLED for this reason."))
def test_uc067_the_search_itself_does_not_know_about_incompatibility():
    """Breaks: the pair is illegal only once both are aboard, so nothing that
    filters per order can prevent it — only the search can."""
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

    solution = solve(problem, iterations=400, seed=0)

    for sequence in assignment_of(solution).values():
        assert not {"FOOD", "HAZ"} <= set(sequence)


# --------------------------------------------------------------------------
# UC-068 — contradictory operator locks
# --------------------------------------------------------------------------

def test_uc068_contradictory_locks_return_the_minimal_conflicting_set():
    """Breaks: dropping the losing lock silently.

    A dispatcher who pinned an order and finds it moved has no reason to trust
    the next plan either.
    """
    locks = (Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1", vehicle_id="V1"),
             Lock(kind="FORBID_ORDER_ON_VEHICLE", order_id="O1", vehicle_id="V1"))
    # Two vehicles, so neither lock is a conflict on its own: the forbid alone
    # simply sends the order to V2. Only the pair is contradictory, which is
    # what makes this a test of minimality rather than of detection.
    problem = instance((drop("O1", "C1"),), (van("V1"), van("V2")), locks=locks)

    conflict = minimal_conflict(problem)

    assert set(conflict) == set(locks), (
        "both locks are needed to produce the contradiction, and neither alone "
        "is the reason")


# --------------------------------------------------------------------------
# UC-069 — two hundred orders at one address
# --------------------------------------------------------------------------

def test_uc069_two_hundred_drops_at_one_geocode_do_not_degenerate():
    """Breaks: ranking neighbours by distance.

    Two hundred candidates tie at zero, so a granular neighbourhood degenerates
    into an arbitrary subset and local search explores one corner of a plateau.
    """
    block = (Location(id="D", lat=9.9, lon=-84.0, matrix_index=0),
             Location(id="BLOCK", lat=9.91, lon=-84.0, matrix_index=1))
    orders = tuple(drop(f"O{i}", "BLOCK", service=30, kg=1) for i in range(200))
    problem = Problem(id="block", locations=block, orders=orders,
                      vehicles=(van(capacities={"kg": 1_000}),), matrix=grid(2))

    solution = solve(problem, iterations=200, seed=0)

    assert len(served(solution)) == 200, "every drop is at the door already"
    assert verify(problem, solution).ok


# --------------------------------------------------------------------------
# UC-070 — single order, single vehicle
# --------------------------------------------------------------------------

def test_uc070_the_trivial_instance_is_solved_trivially():
    """Breaks: taking measurable time. A trivial instance that consumes a search
    budget is reporting fixed overhead every real instance also pays."""
    problem = instance((drop("O1", "C1"),), (van(),))

    solution = solve(problem, iterations=1, seed=0)

    assert served(solution) == {"O1"}
    assert verify(problem, solution).ok
    steps = [s.type for s in solution.routes[0].steps]
    assert steps == ["START", "DELIVERY", "END"]


# --------------------------------------------------------------------------
# UC-071 — zero available vehicles
# --------------------------------------------------------------------------

def test_uc071_an_empty_fleet_is_a_result_not_a_crash():
    """Breaks: treating an empty fleet as an error.

    "There is nothing to dispatch today" is a result an operator can act on and
    a stack trace is not.
    """
    problem = instance((drop("O1", "C1"), drop("O2", "C2")), ())

    solution = solve(problem, iterations=100, seed=0)

    assert solution.routes == ()
    assert {row["order_id"] for row in solution.unassigned} == {"O1", "O2"}
    assert {row["reason_code"] for row in solution.unassigned} == {"FLEET_EXHAUSTED"}
    assert solution.status == "FEASIBLE"


# --------------------------------------------------------------------------
# UC-072 — matrix provider timeout mid-build
# --------------------------------------------------------------------------

def test_uc072_a_matrix_gap_is_never_filled_with_straight_line_distance(monkeypatch):
    """Breaks: filling the gap with straight-line distance.

    A silent haversine substitution yields a plan that looks ordinary and is
    costed against a road network that does not exist.
    """
    import httpx

    import vrp.matrix as matrix_module

    coords = [(9.90 + i / 100, -84.0) for i in range(6)]
    monkeypatch.setattr(matrix_module, "_snap_all",
                        lambda *a, **k: [Snap(location=point, snapped=point,
                                              distance_m=0.0)
                                         for point in coords])

    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise httpx.TimeoutException("gateway stopped responding")
        size = len(coords)
        return {"durations": [[60] * size for _ in range(size)],
                "distances": [[600] * size for _ in range(size)]}

    monkeypatch.setattr(matrix_module, "_fetch_tile", flaky)

    with pytest.raises(httpx.TimeoutException):
        matrix_module.build_large_matrix("http://gateway", coords, max_cells=9)

    assert calls["n"] > 1, "the build must actually have reached a second tile"


@pytest.mark.xfail(strict=True, reason=(
    "NFR-04 and MTX-11 require a mid-build failure to fall back to the cached "
    "matrix and mark the plan DEGRADED. Neither the label nor the fallback "
    "exists: `build_large_matrix` propagates the error, which is safe but is "
    "not graceful degradation. `UC-072` is PARTIALLY_MODELLED for this reason."))
def test_uc072_a_degraded_matrix_is_labelled_rather_than_fatal():
    """Breaks: a fallback nobody can see is the same defect as no fallback."""
    from vrp.model import TravelMatrix as _TravelMatrix

    assert hasattr(_TravelMatrix, "degraded")


# --------------------------------------------------------------------------
# UC-073 — antimeridian and high-latitude coordinates
# --------------------------------------------------------------------------

def test_uc073_a_spatial_partition_does_not_assume_a_euclidean_plane():
    """Breaks: subtracting coordinates.

    A planar difference across the antimeridian is 359 degrees rather than one.

    The instance is a ring of stops around a centre, which the sweep partitioner
    should cut into contiguous arcs. Translating the identical ring across
    longitude 180 changes nothing geographically, so it must not change the
    shape of the answer -- and a partitioner that subtracts raw longitudes sees
    half the ring jump 360 degrees and cuts it into interleaved pieces.
    """
    home = _ring_clusters(centre_lon=0.0)
    dateline = _ring_clusters(centre_lon=180.0)

    assert home == dateline, (
        f"the same ring partitions as {home} at longitude 0 and {dateline} "
        "across the antimeridian")


def _ring_clusters(centre_lon: float) -> list[list[int]]:
    """Partition a ring of eight stops, reported as their positions on it."""
    from vrp.decompose import partition

    count = 8
    lat, radius = 10.0, 0.1
    points = [(lat + radius * math.sin(2 * math.pi * k / count),
               _wrap(centre_lon + radius * math.cos(2 * math.pi * k / count)))
              for k in range(count)]
    locations = ((Location(id="D", lat=lat, lon=_wrap(centre_lon),
                           matrix_index=0),)
                 + tuple(Location(id=f"C{k + 1}", lat=plat, lon=plon,
                                  matrix_index=k + 1)
                         for k, (plat, plon) in enumerate(points)))
    orders = tuple(drop(f"O{k + 1}", f"C{k + 1}", kg=1) for k in range(count))
    problem = Problem(id=f"ring{centre_lon:.0f}", locations=locations,
                      orders=orders,
                      vehicles=tuple(van(f"V{i}", capacities={"kg": 10})
                                     for i in (1, 2)),
                      matrix=grid(count + 1, leg=100))

    clusters = partition(problem, target_size=4, seed=0)
    return sorted(sorted(int(order_id[1:]) for order_id in cluster.order_ids)
                  for cluster in clusters)


def _wrap(lon: float) -> float:
    return (lon + 180.0) % 360.0 - 180.0


# --------------------------------------------------------------------------
# UC-074 — instance at the decomposition threshold
# --------------------------------------------------------------------------

def test_uc074_both_paths_agree_at_the_size_where_they_both_apply():
    """Breaks: comparing the two paths on different instances.

    The threshold is the one size at which both are defined, and objectives that
    diverge there mean the decomposition is not solving the same problem.
    """
    orders = tuple(drop(f"O{i}", f"C{i}", kg=5) for i in range(1, 25))
    problem = Problem(id="threshold", locations=sites(25), orders=orders,
                      vehicles=tuple(van(f"V{i}", capacities={"kg": 60})
                                     for i in range(1, 5)),
                      matrix=grid(25, leg=300))

    whole = solve(problem, iterations=600, seed=0)
    split = solve_decomposed(problem, target_size=8, seed=0)

    assert verify(problem, whole).ok
    assert verify(problem, split).ok
    assert served(whole) == served(split)

    cost_whole = evaluate(problem, assignment_of(whole)).total
    cost_split = evaluate(problem, assignment_of(split)).total
    assert math.isclose(cost_split, cost_whole, rel_tol=0.25), (
        f"decomposed {cost_split} against whole {cost_whole}: at the threshold "
        "the two paths must be solving the same problem")
