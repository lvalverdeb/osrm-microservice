"""Disjoint windows, soft windows, release times — FR-04, FR-06, T-23, E-23.

§6.2: "Multiple disjoint windows per stop, each hard or soft with asymmetric
earliness / lateness costs. Waiting is permitted (arrive early, wait) and MUST
be costed explicitly, because uncosted waiting produces plans that look cheap
and consume the whole driver day."

Three things, of which one is a defect rather than a gap.

**Soft windows were compiled as hard.** The model has accepted
`hardness="SOFT"` with earliness and lateness costs since E-01, and the adapter
passed the bounds to PyVRP regardless. A stop 600 s away with a soft 0-100 s
window came back INFEASIBLE, where a late-but-legal plan plainly exists. The
verifier rejected it on INV-4 — an impossible timeline — which is the right
verdict reached for the wrong reason.

**Multiple disjoint windows were refused.** Now modelled as PyVRP
mutually-exclusive client groups: one client per window, exactly one visited.

**Release times already worked** and are pinned here, since nothing else
exercises them.

What is *not* delivered is stated in the adapter: PyVRP has no soft time
windows, so it will not search for the cheapest lateness. A soft window becomes
a wide hard one, and the penalty is costed after the fact. The plan is legal and
the cost is honest; it is not optimal in the penalty.
"""

from __future__ import annotations

import pytest

from vrp.evaluator import ObjectiveWeights, build_timeline, evaluate
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


def one_stop(windows: tuple[TimeWindow, ...], *, travel: int = 600,
             release: int = 0, service: int = 120) -> Problem:
    """A depot and a single customer `travel` seconds away."""
    locations = (Location(id="D", lat=9.9, lon=-84.0, matrix_index=0),
                 Location(id="C1", lat=9.91, lon=-84.0, matrix_index=1))
    order = Order(id="O1", kind="JOB", quantities={"units": 1},
                  release_time=release,
                  delivery=StopSpec(location_id="C1", time_windows=windows,
                                    service_fixed=service))
    return Problem(
        id="tw", locations=locations, orders=(order,),
        vehicles=(Vehicle(id="V1", capacities={"units": 9}, shift=DAY,
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="tw-v1",
                            durations=((0, travel), (travel, 0)),
                            distances=((0, 5000), (5000, 0))))


# --------------------------------------------------------------------------
# Soft windows — the defect
# --------------------------------------------------------------------------

def test_a_soft_window_that_cannot_be_met_yields_a_late_plan_not_an_infeasible_one():
    """The defect, stated as a test.

    The stop is 600 s away and the window shuts at 100 s. Under a hard window
    that is genuinely infeasible. Under a soft one it is a late delivery, which
    is what "soft" means -- and the plan must say so rather than refuse.
    """
    soft = TimeWindow(start=0, end=100, hardness="SOFT", lateness_cost_per_sec=1)
    problem = one_stop((soft,))
    solution = solve(problem, iterations=300, seed=0)

    assert solution.status == "FEASIBLE", "a soft window cannot make a plan infeasible"
    assert not solution.unassigned, "the stop is servable, merely late"
    assert verify(problem, solution).ok


def test_a_hard_window_that_cannot_be_met_still_refuses():
    """The control. Softening everything would be a way to pass the test above
    while destroying the meaning of a hard window."""
    hard = TimeWindow(start=0, end=100)          # HARD is the default
    problem = one_stop((hard,))
    solution = solve(problem, iterations=300, seed=0)

    assert solution.status == "INFEASIBLE" or solution.unassigned, \
        "a hard window that cannot be met must not be quietly met"


def test_the_verifier_treats_a_soft_breach_as_a_penalty_not_a_violation():
    """INV-3 is about hard windows. A late soft delivery is costed, not illegal."""
    soft = TimeWindow(start=0, end=100, hardness="SOFT", lateness_cost_per_sec=1)
    problem = one_stop((soft,))
    solution = solve(problem, iterations=300, seed=0)

    report = verify(problem, solution)
    assert report.ok, [str(v) for v in report.violations]


# --------------------------------------------------------------------------
# Asymmetric earliness / lateness costs (§6.2)
# --------------------------------------------------------------------------

def test_earliness_and_lateness_are_costed_separately():
    """§6.2 asks for asymmetric costs, and the asymmetry is the point: a home
    delivery an hour early is an annoyance, an hour late is a failed delivery.
    """
    window = TimeWindow(start=1_000, end=1_100, hardness="SOFT",
                        earliness_cost_per_sec=1, lateness_cost_per_sec=10)
    # Arrives at 600, so 400 s early against the 1,000 s opening.
    problem = one_stop((window,), travel=600)
    timeline = build_timeline(problem, "V1", ["O1"])
    evaluation = evaluate(problem, {"V1": ["O1"]}, ObjectiveWeights())

    assert timeline[1].arrival == 600
    assert evaluation.breakdown["earliness_penalty"] == 400 * 1
    assert evaluation.breakdown["lateness_penalty"] == 0


def test_lateness_is_costed_at_its_own_rate():
    window = TimeWindow(start=0, end=100, hardness="SOFT",
                        earliness_cost_per_sec=1, lateness_cost_per_sec=10)
    problem = one_stop((window,), travel=600)
    evaluation = evaluate(problem, {"V1": ["O1"]}, ObjectiveWeights())

    # Arrives at 600, window shut at 100: 500 s late at 10 per second.
    assert evaluation.breakdown["lateness_penalty"] == 500 * 10
    assert evaluation.breakdown["earliness_penalty"] == 0


def test_a_hard_window_carries_no_penalty_because_it_cannot_be_breached():
    """A breached hard window is a violation the verifier reports, not a cost
    the evaluator quietly absorbs. Costing it twice would double-count."""
    problem = one_stop((TimeWindow(start=0, end=100),), travel=600)
    evaluation = evaluate(problem, {"V1": ["O1"]}, ObjectiveWeights())

    assert evaluation.breakdown["lateness_penalty"] == 0


# --------------------------------------------------------------------------
# Multiple disjoint windows (FR-04)
# --------------------------------------------------------------------------

def test_a_stop_with_two_disjoint_windows_is_served_in_one_of_them():
    """FR-04. Modelled as a PyVRP mutually-exclusive client group: one client
    per window, exactly one visited."""
    windows = (TimeWindow(start=0, end=1_000),
               TimeWindow(start=20_000, end=21_000))
    problem = one_stop(windows, travel=600)
    solution = solve(problem, iterations=400, seed=0)

    assert solution.status == "FEASIBLE"
    served = [s for route in solution.routes for s in route.steps if s.order_id]
    assert len(served) == 1, "the stop must be served once, not once per window"
    assert any(w.contains(served[0].start_service) for w in windows)
    assert verify(problem, solution).ok


def test_the_second_window_is_used_when_the_first_cannot_be_reached():
    """The one that proves the group is real: the early window shuts before the
    vehicle can possibly arrive, so the late one must be chosen."""
    windows = (TimeWindow(start=0, end=100),          # unreachable, 600s away
               TimeWindow(start=20_000, end=21_000))
    problem = one_stop(windows, travel=600)
    solution = solve(problem, iterations=400, seed=0)

    assert solution.status == "FEASIBLE"
    served = [s for route in solution.routes for s in route.steps if s.order_id]
    assert len(served) == 1
    assert 20_000 <= served[0].start_service <= 21_000, \
        f"served at {served[0].start_service}, outside the only reachable window"


def test_windows_must_be_sorted_and_disjoint():
    """Overlapping windows are ambiguous about which one a visit satisfies, and
    the model refuses rather than picking."""
    with pytest.raises(Exception, match="disjoint|sorted|overlap"):
        one_stop((TimeWindow(start=0, end=1_000),
                  TimeWindow(start=500, end=1_500)))


# --------------------------------------------------------------------------
# Release times (FR-06)
# --------------------------------------------------------------------------

def test_an_order_is_not_served_before_its_goods_are_released():
    """FR-06: an order cannot depart before the goods are available.

    Already wired to PyVRP before E-23; nothing else exercised it, so a
    regression would have been silent.
    """
    problem = one_stop((DAY,), travel=600, release=6 * 3600)
    solution = solve(problem, iterations=300, seed=0)

    served = [s for route in solution.routes for s in route.steps if s.order_id]
    assert len(served) == 1
    assert served[0].arrival >= 6 * 3600, (
        f"arrived at {served[0].arrival}, before the {6 * 3600} release")
