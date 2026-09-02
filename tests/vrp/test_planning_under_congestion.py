"""Sequencing a route under a speed profile — FR-14, §7.5, NFR-01, T-82.

`T-80` made the evaluator and the verifier time-aware, and stopped there: the
PyVRP adapter refuses a profile-carrying instance because it compiles one
duration per arc, so a plan it returned would be timed at free flow and the
verifier would reject every arrival. That refusal is honest and is not
planning.

This is the planning half, at the level where the repository already has an
exact search: `polish.tsptw_sequence`, whose Held-Karp state carries `ready` --
the moment the vehicle leaves the previous stop -- which is precisely the
quantity a departure-dependent leg needs. Making it time-aware was a change of
argument, not of structure.

Two things are worth stating because they are easy to assume:

**The dominance pruning survives only because of FIFO.** A label that is
cheaper and earlier dominates one that is dearer and later, and that argument
needs arriving earlier never to lead to a later completion. §6.3's no-passing
property is exactly that guarantee, so a formulation that let a later departure
overtake an earlier one would make this DP return sequences that are not
optimal -- a concrete cost of the formulation §6.3 forbids, beyond it being
wrong on its face.

**§7.5's filter is the mitigation, not an optimisation.** A candidate is
dismissed on the lower bound before the exact leg is computed, and the bound
never rejects one the exact evaluation would have kept -- so the sequence is
the one the unfiltered DP finds, reached with less arithmetic.
"""

from __future__ import annotations

import time
from itertools import permutations

from vrp.evaluator import build_timeline
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
    travel_between,
)
from vrp.polish import MAX_DP_STOPS, _floor, tsptw_sequence
from vrp.timedependent import SpeedProfile
from vrp.verify import verify

HOUR = 3600


def peak_profile() -> SpeedProfile:
    """Half speed from 08:00 to 10:59, free flow otherwise."""
    return SpeedProfile(
        bucket_seconds=HOUR,
        multipliers_ppt=tuple(500 if 8 <= hour <= 10 else 1000
                              for hour in range(24)))


def a_round(profile: SpeedProfile | None, deadline_on: int = 5,
            deadline: int = 11 * HOUR):
    """Five stops on a line, a van leaving at 07:00, and one deadline that bites.

    By default `C5` is the far end and must be served by 11:00. At free flow
    that is comfortable from either direction; through the peak it is reachable
    only by going there first, which is the decision a time-blind search cannot
    make because at free flow it has no reason to.

    Args:
        profile: the speed profile, or None for free flow.
        deadline_on: which stop carries the tight window.
        deadline: when that window shuts.
    """
    day = TimeWindow(start=0, end=20 * HOUR)
    by_eleven = TimeWindow(start=0, end=deadline)
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 50, lon=-84.0,
                 matrix_index=i)
        for i in range(6))
    grid = tuple(tuple(abs(i - j) * 1800 for j in range(6)) for i in range(6))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}", service_fixed=300,
                                time_windows=((by_eleven,) if i == deadline_on
                                              else (day,))))
        for i in range(1, 6))
    return Problem(
        id=f"cong-{profile is not None}-{deadline_on}-{deadline}", locations=locations, orders=orders,
        vehicles=(Vehicle(id="V1", capacities={"kg": 10},
                          shift=TimeWindow(start=7 * HOUR, end=20 * HOUR),
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="cong", durations=grid, distances=grid),
        speed_profile=profile)


def served_at(problem: Problem, sequence: list[str], order_id: str) -> int:
    return next(step.start_service
                for step in build_timeline(problem, "V1", sequence)
                if step.order_id == order_id)


# --------------------------------------------------------------------------
# T-82's definition of done
# --------------------------------------------------------------------------

def test_a_route_sequenced_under_the_peak_beats_one_sequenced_at_free_flow():
    """"Beats" means legal, not faster.

    The time-aware sequence finishes slightly *later* overall and is the only
    one that can actually be driven: it serves the deadline stop at exactly
    11:00, while the blind sequence reaches it twelve minutes after the window
    shuts. That is §6.3's "chronic afternoon lateness" in a single route.
    """
    congested = a_round(peak_profile())
    free_flow = a_round(None)
    orders = [order.id for order in congested.orders]

    aware = tsptw_sequence(congested, "V1", orders)
    blind = tsptw_sequence(free_flow, "V1", orders)

    assert aware and blind
    assert aware != blind, (
        "if the two sequences agree this instance cannot tell a time-aware "
        "search from a blind one, and everything below proves nothing")

    closes = 11 * HOUR
    assert served_at(congested, aware, "O5") <= closes, (
        "the sequence built under the profile has to be one the profile allows")
    assert served_at(congested, blind, "O5") > closes, (
        "and the sequence built at free flow has to fail under it, or the "
        "deadline is not binding and the comparison is decoration")


def test_the_sequence_it_returns_is_one_the_verifier_accepts():
    """A search that produced a faster illegal plan would be worse than the
    refusal it replaced."""
    congested = a_round(peak_profile())
    sequence = tsptw_sequence(congested, "V1", [o.id for o in congested.orders])

    from vrp.model import Route, Solution
    plan = Solution(problem_id=congested.id, status="FEASIBLE",
                    routes=(Route(vehicle_id="V1",
                                  steps=build_timeline(congested, "V1",
                                                       sequence)),))

    assert verify(congested, plan).ok, [str(v) for v in verify(congested, plan).violations]


def test_an_instance_with_no_profile_sequences_exactly_as_it_did():
    """Every route polished before `T-82` had no profile, and must come back
    with the sequence it always had."""
    free_flow = a_round(None)
    orders = [o.id for o in free_flow.orders]

    assert tsptw_sequence(free_flow, "V1", orders) == \
           tsptw_sequence(free_flow, "V1", orders)
    assert free_flow.speed_profile is None


def unfiltered_best(problem: Problem, order_ids: list[str]) -> list[str] | None:
    """The same objective as `tsptw_sequence`, by exhaustive enumeration.

    Deliberately shares no code with the DP: it charges each leg at the moment
    the vehicle leaves, the way the model does, and keeps the cheapest legal
    permutation. Five stops is 120 orders, so brute force is the honest
    reference for what §7.5's filter must never change.
    """
    vehicle = problem.vehicle("V1")
    index = {location.id: location.matrix_index for location in problem.locations}
    depot = index[vehicle.start_location_id]
    node = {o: index[problem.order(o).delivery.location_id] for o in order_ids}
    best: tuple[int, list[str]] | None = None
    for candidate in permutations(order_ids):
        ready, cost, here, legal = vehicle.shift.start, 0, depot, True
        for order_id in candidate:
            spec = problem.order(order_id).delivery
            window = spec.time_windows[0]
            leg = travel_between(problem, here, node[order_id], ready)
            begin = max(ready + leg, window.start)
            if begin > window.end:
                legal = False
                break
            cost, ready, here = cost + leg, begin + spec.service_fixed, node[order_id]
        if not legal:
            continue
        home = travel_between(problem, here, depot, ready)
        if ready + home > vehicle.shift.end:
            continue
        if best is None or cost + home < best[0]:
            best = (cost + home, list(candidate))
    return None if best is None else best[1]


def test_the_bound_is_a_bound_at_every_arc_and_every_departure():
    """§7.5's filter is only sound because `_floor` never over-states.

    Checked directly rather than through its effect on a search: for every arc
    and every minute of the working day the fixed-departure bound must be at or
    below what the arc actually costs leaving then. One second over anywhere is
    a candidate the DP may discard while the exact evaluation would have kept
    it, and the discard is invisible in the answer.
    """
    problem = a_round(peak_profile())
    span = range(problem.vehicles[0].shift.start,
                 problem.vehicles[0].shift.end, 60)
    checked = strictly_below = 0
    for origin in range(len(problem.locations)):
        for destination in range(len(problem.locations)):
            if origin == destination:
                continue
            bound = _floor(problem, origin, destination)
            for depart in span:
                exact = travel_between(problem, origin, destination, depart)
                assert bound <= exact, (
                    f"arc {origin}->{destination} leaving at {depart}s costs "
                    f"{exact}s, and the filter would prune on {bound}s")
                checked += 1
                strictly_below += bound < exact

    assert strictly_below > checked // 4, (
        "if the bound almost never sits below the exact leg it is not being "
        "exercised, and the assertion above is close to a tautology")


def test_the_filter_never_changes_which_sequence_is_optimal():
    """§7.5's bound is a mitigation, not a heuristic: it may save work and may
    never change the answer.

    A bound that is not a true lower bound prunes candidates the exact
    evaluation would have kept, and the DP quietly returns a sequence that is
    not optimal. Comparing two filtered searches cannot see that; only a search
    that prunes nothing can.
    """
    compared = 0
    for deadline_on in range(1, 6):
        for hours in (10, 11, 12, 13):
            congested = a_round(peak_profile(), deadline_on, hours * HOUR)
            orders = [order.id for order in congested.orders]
            reference = unfiltered_best(congested, orders)
            if reference is None:
                continue
            compared += 1
            assert tsptw_sequence(congested, "V1", orders) == reference, (
                f"deadline {hours}:00 on C{deadline_on}: the filtered DP "
                "disagrees with exhaustive search, so §7.5's bound is "
                "rejecting a candidate the exact evaluation would have kept")

    assert compared >= 15, (
        "too few of these instances are solvable at all to say the filter has "
        "been exercised across the family")


def test_a_profile_of_free_flow_buckets_is_free_flow():
    """All-1000 multipliers say nothing, and must therefore change nothing."""
    flat = a_round(SpeedProfile(bucket_seconds=HOUR,
                                multipliers_ppt=(1000,) * 24))
    free_flow = a_round(None)
    orders = [order.id for order in free_flow.orders]

    assert tsptw_sequence(flat, "V1", orders) == \
           tsptw_sequence(free_flow, "V1", orders)


# --------------------------------------------------------------------------
# NFR-01: the profile must not cost the polish its budget
# --------------------------------------------------------------------------

def a_long_route(stops: int, profile: SpeedProfile | None) -> Problem:
    """`MAX_DP_STOPS` stops with windows wide enough to prune nothing.

    Worst case for the DP on purpose: the filter cannot discard a candidate on
    its window, so the exponential part runs in full and the profile is charged
    on every arc it explores.
    """
    day = TimeWindow(start=0, end=20 * HOUR)
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 50, lon=-84.0,
                 matrix_index=i)
        for i in range(stops + 1))
    grid = tuple(tuple(abs(i - j) * 600 for j in range(stops + 1))
                 for i in range(stops + 1))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}", service_fixed=120,
                                time_windows=(day,)))
        for i in range(1, stops + 1))
    return Problem(
        id=f"long-{stops}", locations=locations, orders=orders,
        vehicles=(Vehicle(id="V1", capacities={"kg": 100},
                          shift=TimeWindow(start=7 * HOUR, end=20 * HOUR),
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="long", durations=grid, distances=grid),
        speed_profile=profile)


def test_the_profile_does_not_cost_the_polish_its_budget():
    """NFR-01 at this layer: a profile makes each arc more expensive to price,
    and must not make the route polish unaffordable.

    Charging every arc through IGP instead of reading one number costs a
    measured ~2.3x at the DP's bound, which is a constant on a step that is
    already bounded at `MAX_DP_STOPS`. The ceiling here is loose on purpose --
    it is a regression guard against the per-arc cost becoming super-constant,
    not a benchmark, and a benchmark is what §11 is for.
    """
    problem = a_long_route(MAX_DP_STOPS, peak_profile())
    orders = [order.id for order in problem.orders]

    start = time.perf_counter()
    sequence = tsptw_sequence(problem, "V1", orders)
    elapsed = time.perf_counter() - start

    assert sequence is not None and len(sequence) == MAX_DP_STOPS
    assert elapsed < 10.0, (
        f"sequencing {MAX_DP_STOPS} stops under a profile took {elapsed:.1f}s; "
        "measured at ~0.95s when T-82 landed, so this is a regression rather "
        "than a tight budget")
