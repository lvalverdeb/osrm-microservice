"""Route-level exact polishing — ALG-5, T-39, E-39.

ALG-5 asks for two per-route passes after the metaheuristic budget:

* "**Optimal departure-time scheduling** per route: minimise duty duration and
  lateness by shifting departure and distributing waiting, respecting
  driving-hours rules. This is a scheduling problem solvable exactly per route
  and it is nearly free -- many production plans leave several percent of duty
  time on the table by departing at the earliest possible moment by default."

* "Optimal sequencing of each route via TSP-with-time-windows dynamic
  programming where the route is short enough (<= ~14 stops)."

The default this corrects is in this codebase: `schedule_route` starts the clock
at `vehicle.shift.start` unless told otherwise, so every plan departs as early as
it legally can and banks the difference as waiting at the first stop. The driver
is on duty for that wait.

"Solvable exactly" is the claim worth testing hardest, because an approximate
answer here looks identical to an exact one -- both return a departure, both
produce a legal plan, and only a brute-force comparison can tell them apart.
`test_the_departure_matches_an_exhaustive_search` is that comparison, and it is
the test that would catch a heuristic quietly replacing the arithmetic.
"""

from __future__ import annotations

import pytest

from vrp.evaluator import route_is_legal
from vrp.generate import Shape, generate_instance
from vrp.hos.rules import rules_for
from vrp.hos.schedule import schedule_route
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.polish import (
    MAX_DP_STOPS,
    duty_duration,
    optimal_departure,
    polish_route,
    tsptw_sequence,
)

DAY = TimeWindow(start=0, end=14 * 3600)


def exhaustive_best_departure(problem: Problem, vehicle_id: str,
                              order_ids: list[str], step: int = 1,
                              rules=None) -> tuple[int, int]:
    """The best departure and its duty, found by trying every one of them.

    Deliberately stupid, and the only thing in this file that can prove
    `optimal_departure` is exact rather than merely plausible.

    Every second, not every thirtieth. A coarse grid cannot represent an exact
    answer and so cannot judge one: at 30-second steps this search returned a
    duty six seconds *worse* than `optimal_departure` on two generated
    instances, and the test read that as the implementation being wrong. A full
    scan of a twelve-hour shift costs 0.7 s.
    """
    vehicle = problem.vehicle(vehicle_id)
    best = None
    for departure in range(vehicle.shift.start, vehicle.shift.end + 1, step):
        scheduled = schedule_route(problem, vehicle_id, order_ids, rules=rules,
                                   start_time=departure)
        if not _legal(problem, vehicle, scheduled):
            continue
        duty = duty_duration(scheduled.steps)
        if best is None or duty < best[1]:
            best = (departure, duty)
    return best


def _legal(problem: Problem, vehicle: Vehicle, scheduled) -> bool:
    """Whether this timeline breaks a hard window, the shift, or the law."""
    if not scheduled.legal:
        return False
    for step in scheduled.steps:
        if step.order_id is None:
            continue
        stop = (problem.order(step.order_id).delivery
                or problem.order(step.order_id).pickup)
        hard = [w for w in stop.time_windows if w.hardness == "HARD"]
        if hard and not any(w.contains(step.start_service) for w in hard):
            return False
    return scheduled.steps[-1].arrival <= vehicle.shift.end


# --------------------------------------------------------------------------
# Departure-time scheduling
# --------------------------------------------------------------------------

def test_leaving_later_shortens_the_duty_when_the_first_window_opens_late():
    """ALG-5's whole point, in one fixture.

    One stop, an hour away, whose window opens at 09:00. Departing at 00:00 --
    which is what `schedule_route` does by default -- means nine hours of duty
    for one hour of driving and eight of standing about. The driver is paid for
    all of it.
    """
    problem = _one_stop(travel=3600, opens=9 * 3600)

    default = schedule_route(problem, "V0", ["O1"], rules=None)
    departure = optimal_departure(problem, "V0", ["O1"])
    polished = schedule_route(problem, "V0", ["O1"], rules=None,
                              start_time=departure)

    assert departure == 8 * 3600, departure
    assert duty_duration(polished.steps) < duty_duration(default.steps)
    print(f"\n  default {duty_duration(default.steps)}s -> "
          f"polished {duty_duration(polished.steps)}s")


def test_the_service_times_do_not_move():
    """Departure scheduling shifts the *driver*, not the customer.

    A pass that improved duty by serving everyone later would be trading the
    customer's window for the driver's timesheet, which is not what ALG-5 asks
    for and would not survive contact with a dispatcher.
    """
    problem = _one_stop(travel=3600, opens=9 * 3600)

    default = schedule_route(problem, "V0", ["O1"], rules=None)
    departure = optimal_departure(problem, "V0", ["O1"])
    polished = schedule_route(problem, "V0", ["O1"], rules=None,
                              start_time=departure)

    served = {s.order_id: s.start_service for s in default.steps if s.order_id}
    after = {s.order_id: s.start_service for s in polished.steps if s.order_id}
    assert served == after


def test_a_route_with_no_waiting_does_not_move():
    """The control. A pass that always reports an improvement is not measuring
    one -- and a route whose first window is already open has nothing to gain."""
    problem = _one_stop(travel=3600, opens=0)

    assert optimal_departure(problem, "V0", ["O1"]) == 0


def test_the_departure_never_makes_a_stop_late():
    problem = _staggered(stops=5)
    order_ids = [order.id for order in problem.orders]

    departure = optimal_departure(problem, "V0", order_ids)
    scheduled = schedule_route(problem, "V0", order_ids, rules=None,
                               start_time=departure)

    assert _legal(problem, problem.vehicle("V0"), scheduled)


def test_the_route_ends_at_the_same_moment_however_late_it_leaves():
    """Why there is no end-of-shift term in the arithmetic.

    The shift is capped at the total waiting, so the push at the last stop is
    zero and the route ends exactly when it ended before. That is what makes
    leaving later free, and it is the reason an `end_room` term in `_slack` was
    dead code no perturbation could reach.

    Asserted directly, because "the route still fits the shift" does not
    distinguish a pass that got this right from one that had slack to spare.
    """
    problem = _one_stop(travel=3600, opens=9 * 3600, shift_end=11 * 3600)

    default = schedule_route(problem, "V0", ["O1"], rules=None)
    departure = optimal_departure(problem, "V0", ["O1"])
    polished = schedule_route(problem, "V0", ["O1"], rules=None,
                              start_time=departure)

    assert departure > 0
    assert polished.steps[-1].arrival == default.steps[-1].arrival
    assert polished.steps[-1].arrival <= 11 * 3600


def test_a_route_already_past_its_shift_is_not_disguised():
    """Moving the departure of an illegal route makes it later, not legal."""
    problem = _one_stop(travel=3600, opens=9 * 3600, shift_end=9 * 3600 + 600)

    assert optimal_departure(problem, "V0", ["O1"]) == 0


@pytest.mark.parametrize("stops", [2, 3, 5, 8])
def test_the_departure_matches_an_exhaustive_search(stops):
    """"Solvable exactly per route", checked against solving it stupidly.

    This is the test that separates an exact answer from a plausible one. Both
    return a departure and both produce a legal plan; only the brute force says
    which is optimal.
    """
    problem = _staggered(stops=stops)
    order_ids = [order.id for order in problem.orders]

    departure = optimal_departure(problem, "V0", order_ids)
    duty = duty_duration(schedule_route(problem, "V0", order_ids, rules=None,
                                        start_time=departure).steps)
    _, best_duty = exhaustive_best_departure(problem, "V0", order_ids)

    assert duty == best_duty, (duty, best_duty)


@pytest.mark.parametrize("seed", [3, 11, 19, 27])
def test_it_is_exact_on_generated_instances_too(seed):
    """Against the E-04 generator rather than a hand-built fixture, so
    exactness is not a property of one carefully chosen shape."""
    problem = generate_instance(seed, shape=Shape.TIGHT_WINDOWS)
    vehicle, order_ids = _a_real_route(problem)
    if vehicle is None:
        pytest.skip("this instance placed no order")

    departure = optimal_departure(problem, vehicle, order_ids)
    duty = duty_duration(schedule_route(problem, vehicle, order_ids, rules=None,
                                        start_time=departure).steps)
    best = exhaustive_best_departure(problem, vehicle, order_ids)

    assert best is not None
    assert duty == best[1], (duty, best[1])


def test_it_respects_driving_hours():
    """ALG-5: "respecting driving-hours rules". Breaks move with the departure,
    so a departure computed as though the driver never rested is a departure
    that puts the last stops outside their windows."""
    # 20-minute legs, not two-hour ones: at two hours this route needs nine
    # hours of driving, which EU-561 forbids at *every* departure, so the test
    # asserted legality no departure could deliver.
    problem = _staggered(stops=4, travel=1200, shift_end=24 * 3600)
    order_ids = [order.id for order in problem.orders]
    rules = rules_for("EU-561")

    departure = optimal_departure(problem, "V0", order_ids, rules=rules)
    scheduled = schedule_route(problem, "V0", order_ids, rules=rules,
                               start_time=departure)

    assert departure > problem.vehicle("V0").shift.start, (
        "fell back to the earliest departure; the rules path is inert")
    assert scheduled.legal, scheduled.violation
    assert _legal(problem, problem.vehicle("V0"), scheduled)


# --------------------------------------------------------------------------
# TSPTW sequencing
# --------------------------------------------------------------------------

def test_the_dp_finds_the_optimal_sequence():
    """Checked against every permutation, which is the only way to know."""
    from itertools import permutations

    problem = _scattered(stops=7)
    order_ids = [order.id for order in problem.orders]

    best = min(
        (seq for seq in permutations(order_ids)
         if route_is_legal(problem, "V0", list(seq))),
        key=lambda seq: _travel(problem, "V0", list(seq)))

    chosen = tsptw_sequence(problem, "V0", order_ids)

    assert _travel(problem, "V0", chosen) == _travel(problem, "V0", list(best))


def test_the_dp_respects_time_windows():
    """A shorter sequence that arrives late is not a better sequence. Without
    the window check the DP returns the plain TSP tour, which is cheaper and
    undeliverable."""
    problem = _windowed_detour()
    order_ids = [order.id for order in problem.orders]

    chosen = tsptw_sequence(problem, "V0", order_ids)

    assert chosen is not None
    assert route_is_legal(problem, "V0", chosen), chosen
    assert chosen == ["O2", "O1"], chosen


def test_the_dp_declines_a_route_that_is_too_long():
    """ALG-5 bounds the DP at "<= ~14 stops" because it is exponential.
    Declining is the honest answer; silently truncating would return an optimal
    sequence for a route nobody asked about."""
    problem = _scattered(stops=MAX_DP_STOPS + 1)
    order_ids = [order.id for order in problem.orders]

    assert tsptw_sequence(problem, "V0", order_ids) is None


def test_the_dp_reports_when_no_legal_sequence_exists():
    problem = _impossible_windows()

    assert tsptw_sequence(problem, "V0", ["O1", "O2"]) is None


def test_the_dp_keeps_a_dearer_path_that_arrives_earlier():
    """Label dominance, without which the DP quietly returns "no sequence".

    Two ways to reach the same three stops ending at the same one: A-B-C is
    cheaper in travel but waits for A's window and arrives at 5,100; B-A-C
    costs more and arrives at 3,200. Neither dominates. Only the earlier one
    can still get home inside the shift, so a DP that keeps just the cheapest
    label per state finds nothing at all and reports the route impossible.

    Dropping dominance was perturbed first against the other DP tests and every
    one of them passed -- the fixtures had no state two paths could reach.
    """
    problem = _dominance_trap()

    chosen = tsptw_sequence(problem, "V0", ["O1", "O2", "O3"])

    assert chosen == ["O2", "O1", "O3"], chosen


def test_the_dp_never_returns_a_worse_sequence_than_it_was_given():
    problem = _scattered(stops=6)
    order_ids = [order.id for order in problem.orders]

    chosen = tsptw_sequence(problem, "V0", order_ids)

    assert _travel(problem, "V0", chosen) <= _travel(problem, "V0", order_ids)


# --------------------------------------------------------------------------
# Both passes together
# --------------------------------------------------------------------------

def test_polishing_a_route_applies_both_passes():
    """Both, and the departure pass runs on the *resequenced* route.

    Scheduling the departure of the order it was handed and then reordering it
    would produce a departure optimal for a route nobody drives. The assertion
    is therefore against `optimal_departure` of the sequence that came back,
    not merely against a departure being non-zero -- this fixture resequences
    into a route with no waiting left, whose optimal departure is zero, and
    "departure > 0" failed it for being right.
    """
    problem = _windowed_detour()
    order_ids = [order.id for order in problem.orders]

    polished = polish_route(problem, "V0", order_ids)

    assert polished.resequenced
    assert polished.order_ids == ["O2", "O1"]
    assert polished.departure == optimal_departure(problem, "V0",
                                                   polished.order_ids)
    assert polished.duty == duty_duration(polished.steps)


def test_polishing_keeps_every_order():
    problem = _scattered(stops=6)
    order_ids = [order.id for order in problem.orders]

    polished = polish_route(problem, "V0", order_ids)

    assert sorted(polished.order_ids) == sorted(order_ids)


def test_polishing_a_long_route_still_schedules_its_departure():
    """The DP declines above ~14 stops; the departure pass does not. A polish
    that gave up on both because one was out of range would leave the cheap
    half of ALG-5 on the table -- and it is the half ALG-5 calls "nearly free"."""
    problem = _staggered(stops=MAX_DP_STOPS + 2)
    order_ids = [order.id for order in problem.orders]

    polished = polish_route(problem, "V0", order_ids)

    assert polished.order_ids == order_ids       # sequence untouched
    assert polished.departure > 0                # departure still chosen


# --------------------------------------------------------------------------
# T-39's acceptance: duty-duration reduction, measured and reported
# --------------------------------------------------------------------------

def test_duty_duration_is_measurably_reduced():
    """E-39's acceptance: "Duty duration measurably reduced by departure-time
    choice". T-39's: "Duty-duration reduction measured and reported".

    Measured on the frozen corpus, against the default this codebase actually
    has -- `schedule_route` starting at `vehicle.shift.start` -- because that is
    the "earliest possible moment by default" ALG-5 says production plans lose
    several percent to.

    The result splits cleanly, and both halves matter:

        c20-clustered-slack        9.1h ->   9.1h    0.0%
        c20-scattered-slack        9.3h ->   9.3h    0.0%
        c30-clustered-tight       46.3h ->  33.5h   27.8%
        c30-scattered-tight       45.9h ->  33.7h   26.6%
        c50-clustered-pressure    22.6h ->  22.6h    0.0%

    Where windows are slack there is no waiting, so there is nothing to
    recover and the pass correctly does nothing -- that is the control, and a
    pass reporting an improvement there would be shifting departures for its
    own sake. Where windows are tight it recovers about a quarter of the duty,
    which is well beyond ALG-5's "several percent".

    A generated TIGHT_WINDOWS sample gives 57%, and that number is not used
    here: those instances put the shift start at midnight and the first window
    hours later, so most of what is "recovered" is an artefact of a fixture no
    dispatcher would roster.
    """
    from vrp.bench.corpus import CORPUS, build_instance
    from vrp.solve import pyvrp_adapter

    tight, slack = [], []
    for spec in CORPUS:
        problem = build_instance(spec)
        solution = pyvrp_adapter.solve(problem, iterations=300, seed=0)

        saved = total = 0
        for route in solution.routes:
            order_ids = [s.order_id for s in route.steps if s.order_id]
            if len(order_ids) < 2:
                continue
            before = duty_duration(schedule_route(problem, route.vehicle_id,
                                                  order_ids, rules=None).steps)
            departure = optimal_departure(problem, route.vehicle_id, order_ids)
            after = duty_duration(
                schedule_route(problem, route.vehicle_id, order_ids, rules=None,
                               start_time=departure).steps)
            assert after <= before, (spec.name, route.vehicle_id, before, after)
            saved += before - after
            total += before

        share = saved / total * 100 if total else 0.0
        (tight if spec.tight_windows else slack).append(share)
        print(f"  {spec.name:<24}{total / 3600:>7.1f}h -> "
              f"{(total - saved) / 3600:>6.1f}h {share:>7.1f}%")

    assert tight, "the corpus has no tight-window instance"
    mean_tight = sum(tight) / len(tight)
    print(f"  {'tight-window mean':<24}{'':>7} {'':>9}{mean_tight:>7.1f}%")
    assert mean_tight > 0, "departure-time choice reduced no duty at all"
    assert all(share == 0 for share in slack), (
        f"slack instances reported {slack}; there is no waiting to recover "
        "there, so a gain means departures are moving for their own sake")


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def _travel(problem: Problem, vehicle_id: str, order_ids: list[str]) -> int:
    from itertools import pairwise

    vehicle = problem.vehicle(vehicle_id)
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    nodes = [index[vehicle.start_location_id]]
    nodes += [index[problem.order(o).delivery.location_id] for o in order_ids]
    nodes.append(index[vehicle.end_location_id])
    return sum(problem.matrix.duration(a, b) for a, b in pairwise(nodes))


def _build(legs: list[list[int]], windows: dict[int, TimeWindow],
           shift_end: int, service: int = 60) -> Problem:
    size = len(legs)
    grid = tuple(tuple(row) for row in legs)
    shift = TimeWindow(start=0, end=shift_end)
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 100, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}", service_fixed=service,
                                time_windows=(windows[i],)))
        for i in range(1, size))
    return Problem(
        id="polish", locations=locations, orders=orders,
        vehicles=(Vehicle(id="V0", capacities={"kg": 100}, shift=shift,
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="polish", durations=grid, distances=grid))


def _one_stop(travel: int, opens: int,
              shift_end: int = 14 * 3600) -> Problem:
    return _build([[0, travel], [travel, 0]],
                  {1: TimeWindow(start=opens, end=shift_end)}, shift_end)


def _staggered(stops: int, travel: int = 600,
               shift_end: int = 14 * 3600) -> Problem:
    """Stops in a line, each window opening later than the last."""
    size = stops + 1
    legs = [[abs(i - j) * travel for j in range(size)] for i in range(size)]
    windows = {i: TimeWindow(start=3600 + i * 1200, end=shift_end)
               for i in range(1, size)}
    return _build(legs, windows, shift_end)


def _scattered(stops: int) -> Problem:
    """Stops at varied distances, all windows wide: a pure sequencing problem."""
    size = stops + 1
    positions = [0] + [((i * 37) % 23) + 1 for i in range(stops)]
    legs = [[abs(positions[i] - positions[j]) * 120 for j in range(size)]
            for i in range(size)]
    windows = {i: TimeWindow(start=0, end=14 * 3600) for i in range(1, size)}
    return _build(legs, windows, 14 * 3600)


def _windowed_detour() -> Problem:
    """The cheap sequence is illegal; the legal one costs nearly four times more.

    Deliberately **asymmetric**. A symmetric matrix cannot express this at all:
    a tour and its reverse use the same edges between the same depot, so they
    always cost the same and any "the DP picked the cheaper one" assertion is
    decided by a tie-break rather than by the model. The first version of this
    fixture was symmetric and asserted an order the DP had no reason to prefer.

    Here O2 closes at 150 s. Serving O1 first arrives at O2 at 260 -- illegal.
    Serving O2 first is legal and costs 1,100 against the illegal 300, so a DP
    that ignores windows returns the wrong answer and a cheaper number.
    """
    legs = [[0, 100, 100],
            [100, 0, 100],
            [100, 900, 0]]
    windows = {1: TimeWindow(start=0, end=8 * 3600),
               2: TimeWindow(start=0, end=150)}
    return _build(legs, windows, 12 * 3600)


def _impossible_windows() -> Problem:
    """Two stops whose windows cannot both be met from one depot."""
    legs = [[0, 3600, 3600],
            [3600, 0, 3600],
            [3600, 3600, 0]]
    windows = {1: TimeWindow(start=0, end=3700),
               2: TimeWindow(start=0, end=3700)}
    return _build(legs, windows, 12 * 3600)


def _a_real_route(problem: Problem):
    """A route the generator actually planned, rather than one invented here.

    Taking the first five orders in declaration order and hoping they form a
    legal duty does not work on tight-window instances -- it never did, and the
    tests using it skipped every case rather than failing, which is the quieter
    kind of wrong.
    """
    from vrp.generate import build_plan

    assignment, _timelines = build_plan(problem)
    for vehicle_id, orders in sorted(assignment.items()):
        if len(orders) >= 2:
            return vehicle_id, list(orders)
    return None, []


def _dominance_trap() -> Problem:
    """Two non-dominated paths to one state, and only the dearer one finishes.

    Asymmetric on purpose: with a symmetric matrix every alternative ordering
    here is also legal, and the DP has no need of the label it is being tested
    for. O1's window opens at 3,000, so reaching it early buys nothing but a
    wait -- which is what makes the cheap path late.
    """
    legs = [[0, 100, 3000, 100],
            [100, 0, 100, 100],
            [3000, 100, 0, 2000],
            [100, 9000, 9000, 0]]
    windows = {1: TimeWindow(start=3000, end=10_000),
               2: TimeWindow(start=0, end=10_000),
               3: TimeWindow(start=0, end=10_000)}
    return _build(legs, windows, shift_end=3400, service=0)
