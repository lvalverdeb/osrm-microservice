"""Time-dependent travel on the route path — FR-14, §6.3, §7.5, T-80.

`T-40` built the construction and nothing consumed it. This wires it into the
two places that compute when a vehicle is anywhere: the canonical evaluator's
timeline, and the independent verifier's INV-4 recomputation. Both use
`vrp.model.travel_between`, on the same footing as `service_time`, which the
verifier has always shared -- CON-1 forbids it sharing code with a *solver*, and
a domain primitive both compute from is the model.

§7.5 is explicit about what integration costs: "Under time-dependent travel
(§6.3), exact O(1) concatenation is not generally available. Required
mitigation: evaluate candidate moves against a **fixed-departure lower bound**
matrix for filtering, then re-evaluate surviving candidates exactly. Record the
filter's false-negative rate." `T-40`'s bound is that filter, and
`test_the_lower_bound_filter_is_the_mitigation` checks it holds on route-shaped
work rather than on synthetic arcs.

The profiles are still invented. `T-63` fits real ones and needs telematics
volume; nothing here claims an afternoon in San José looks like this.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

from vrp.bench import fixtures
from vrp.evaluator import build_timeline, evaluate, route_metrics
from vrp.model import travel_between
from vrp.solve.pyvrp_adapter import solve
from vrp.timedependent import (
    SpeedProfile,
    fastest_possible,
    filter_moves,
    travel,
)
from vrp.verify import verify

HOUR = 3600


def peak_profile() -> SpeedProfile:
    """Half speed through a three-hour morning peak, free flow otherwise."""
    return SpeedProfile(
        bucket_seconds=HOUR,
        multipliers_ppt=tuple(500 if 7 <= hour <= 9 else 1000
                              for hour in range(24)))


def a_morning_round(profile: SpeedProfile | None = None):
    """A round that starts inside the peak, so the profile has something to do."""
    problem = fixtures.FIXTURES["UC-009"]()
    shifted = replace(problem, id=f"td-{profile is not None}",
                      speed_profile=profile)
    return shifted


def assignment_of(solution) -> dict[str, list[str]]:
    return {route.vehicle_id: [s.order_id for s in route.steps if s.order_id]
            for route in solution.routes}


# --------------------------------------------------------------------------
# T-80's definition of done
# --------------------------------------------------------------------------

def test_the_same_plan_is_later_through_the_peak_than_at_free_flow():
    """The whole requirement in one comparison. §6.3's failure mode without it
    is "chronic afternoon lateness, dispatcher distrust, drivers padding
    service times to compensate" -- which happens when the plan believes the
    free-flow matrix and the road does not."""
    free_flow = a_morning_round()
    congested = a_morning_round(peak_profile())
    plan = solve(free_flow, iterations=400, seed=0)
    sequence = assignment_of(plan)

    quick = build_timeline(free_flow, "V1", sequence["V1"])
    slow = build_timeline(congested, "V1", sequence["V1"])

    assert slow[-1].arrival > quick[-1].arrival, (
        f"the same sequence of stops finished at {slow[-1].arrival} through "
        f"the peak and {quick[-1].arrival} at free flow; if they agree the "
        "profile is not reaching the timeline")
    assert route_metrics(congested, slow)["driving_seconds"] > \
           route_metrics(free_flow, quick)["driving_seconds"]


def test_the_verifier_agrees_with_the_evaluator_about_when_the_van_arrived():
    """INV-4 recomputes every arrival, so a timeline built with a profile and
    checked without one would fail at every stop. Both share `travel_between`
    for that reason, and this is what stops them drifting."""
    congested = a_morning_round(peak_profile())
    sequence = assignment_of(solve(a_morning_round(), iterations=400, seed=0))
    plan = _plan_from(congested, sequence)

    report = verify(congested, plan)

    assert report.ok, [str(v) for v in report.violations]


def test_the_search_refuses_an_instance_it_would_mis_time():
    """PyVRP compiles one duration per arc, so a plan it returns under a
    profile is timed at free flow and the verifier rejects every arrival.

    Refusing is the same answer this engine gives for order-class
    incompatibility: an encoding that cannot carry a constraint is not a
    partial implementation of it. Solve at free flow, evaluate under the
    profile, and the difference is what the peak costs.
    """
    import pytest

    with pytest.raises(NotImplementedError, match="speed profile"):
        solve(a_morning_round(peak_profile()), iterations=100, seed=0)


def _plan_from(problem, assignment):
    """A timeline for an assignment, so the verifier can judge it."""
    from vrp.model import Route, Solution

    timelines = evaluate(problem, assignment).timelines
    return Solution(problem_id=problem.id, status="FEASIBLE",
                    routes=tuple(Route(vehicle_id=vehicle_id, steps=steps)
                                 for vehicle_id, steps in timelines.items()))


def test_an_instance_with_no_profile_is_untouched():
    """Every instance that existed before `T-80` declares no profile, and its
    plans must be the ones it always had. A change that silently re-timed the
    whole corpus would be indistinguishable from this feature working."""
    problem = fixtures.FIXTURES["UC-009"]()
    assert problem.speed_profile is None

    sequence = assignment_of(solve(problem, iterations=400, seed=0))["V1"]
    timeline = build_timeline(problem, "V1", sequence)

    matrix = problem.matrix
    for previous, current in pairwise(timeline):
        origin = problem.location(previous.location_id).matrix_index
        destination = problem.location(current.location_id).matrix_index
        assert travel_between(problem, origin, destination,
                              previous.departure) == \
               matrix.duration(origin, destination)


def test_the_profile_reaches_the_objective_not_only_the_timeline():
    """A cost that ignores congestion would let the search prefer a route it
    cannot drive on time, which is the failure §6.3 describes rather than a
    reporting nicety."""
    free_flow = a_morning_round()
    congested = a_morning_round(peak_profile())
    sequence = assignment_of(solve(free_flow, iterations=400, seed=0))

    # The default weights price distance, not duration (§5.3), and congestion
    # does not lengthen a road. What it lengthens is the driving, so that is
    # where the profile has to show up -- and a caller who prices duration then
    # gets a total that differs.
    quick = evaluate(free_flow, sequence).breakdown["driving_seconds"]
    slow = evaluate(congested, sequence).breakdown["driving_seconds"]

    assert slow > quick, (
        f"{slow} against {quick}: the objective's own accounting has to see "
        "the peak, or a duration-weighted run would price it at free flow")
    assert evaluate(free_flow, sequence).breakdown["distance"] == \
           evaluate(congested, sequence).breakdown["distance"], (
        "congestion does not move the stops")


# --------------------------------------------------------------------------
# §7.5's required mitigation
# --------------------------------------------------------------------------

def test_the_lower_bound_filter_is_the_mitigation_section_7_5_asks_for():
    """§7.5: "evaluate candidate moves against a fixed-departure lower bound
    matrix for filtering, then re-evaluate surviving candidates exactly.
    Record the filter's false-negative rate."

    Measured on route-shaped work -- every arc of a real plan against the
    deadline its own stop carries -- rather than on synthetic pairs, because a
    filter's usefulness is entirely a property of the distribution it sees.
    """
    congested = a_morning_round(peak_profile())
    profile = congested.speed_profile
    plan = _plan_from(congested, assignment_of(
        solve(a_morning_round(), iterations=400, seed=0)))

    moves = []
    for route in plan.routes:
        for previous, current in pairwise(route.steps):
            if current.order_id is None:
                continue
            origin = congested.location(previous.location_id).matrix_index
            destination = congested.location(current.location_id).matrix_index
            deadline = congested.order(
                current.order_id).delivery.time_windows[0].end
            moves.append((congested.matrix.duration(origin, destination),
                          previous.departure, deadline))

    assert moves, "the plan has to have arcs for this to measure anything"
    report = filter_moves(moves, profile)

    assert report.considered == len(moves)
    assert 0 <= report.false_negative_rate_ppt <= 1_000

    # Admissibility, on this instance's own arcs: the bound may fail to prune
    # and must never prune a move the exact evaluation would have accepted.
    for free_flow, depart, deadline in moves:
        if depart + fastest_possible(free_flow, profile) > deadline:
            assert depart + travel(free_flow, depart, profile) > deadline, (
                f"pruned an arc of {free_flow}s leaving at {depart} that "
                "would have made its deadline")
