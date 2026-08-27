"""Route-level exact polishing — ALG-5, T-39.

Two per-route passes, applied after the metaheuristic has spent its budget.

**Departure-time scheduling.** ALG-5: "minimise duty duration and lateness by
shifting departure and distributing waiting, respecting driving-hours rules.
This is a scheduling problem solvable exactly per route and it is nearly free --
many production plans leave several percent of duty time on the table by
departing at the earliest possible moment by default."

That default is this codebase's: `schedule_route` starts the clock at
`vehicle.shift.start` unless told otherwise, so every plan leaves as early as it
legally can and banks the difference as waiting at the first stop. The customer
is served at the same moment either way. The driver is on duty for the wait.

The pass is exact and linear, which is worth setting out because it is not
obvious. Shift the departure later by `d`. Arrival at the first stop moves by
`d`, but service begins at `max(arrival + d, window_start)` -- so the shift is
absorbed by whatever wait was already there, and only the excess `d - w1`
propagates. Writing `W_i` for the wait accumulated before stop `i`, the push at
stop `i` is `max(0, d - W_i)`, and the route stays legal while that push fits
the remaining slack `l_i - s_i`. So

    d_max = min over stops of (W_i + l_i - s_i)

and duty falls one-for-one with `d` until `d` reaches the total wait `W_n`,
after which the end of the route moves with the departure and duty stops
improving. The answer is therefore `min(d_max, W_n)`: the smallest departure
that achieves the shortest duty, which leaves the rest of the slack unspent.

Hours-of-service breaks are placed against *driving* time, not wall clock, so a
uniform shift does not change which breaks are required. It can still change
whether the shifted timeline fits, so the result is confirmed against
`schedule_route` and walked back deterministically if it does not.

**TSPTW sequencing.** ALG-5: "Optimal sequencing of each route via
TSP-with-time-windows dynamic programming where the route is short enough
(<= ~14 stops)". Held-Karp over subsets, carrying the earliest feasible arrival
alongside the cost, which is what makes it *TW* rather than plain TSP. Above the
limit it declines rather than truncating: an optimal sequence for the first
fourteen stops of a twenty-stop route is an answer to a question nobody asked.

Placement: **Python**. Per-route polish inside the solver, off the request path.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from vrp.hos.schedule import schedule_route
from vrp.model import Problem, Step, Vehicle

# ALG-5's "<= ~14 stops". Held-Karp is O(2^n * n^2): fourteen stops is about
# 3.2 million state transitions, which is a second or so; fifteen doubles it.
MAX_DP_STOPS = 14

# Granularity of the deterministic walk-back when hours-of-service rules make
# the computed departure infeasible. Seconds, and a count of halvings rather
# than a clock, for CON-4.
_WALKBACK_STEPS = 24


@dataclass(frozen=True)
class PolishedRoute:
    """A route after both passes, and what they were worth."""

    order_ids: list[str]
    departure: int
    duty: int
    steps: tuple[Step, ...]
    resequenced: bool


def duty_duration(steps: tuple[Step, ...]) -> int:
    """How long the driver is on duty: leaving the depot until returning.

    Measured from the first step's departure rather than its arrival, so that
    loading time at the depot -- which §6.9 makes a real interval -- is not
    counted twice when a caller has already charged for it separately.
    """
    if not steps:
        return 0
    return steps[-1].arrival - steps[0].departure


def _windows_of(problem: Problem, order_id: str):
    order = problem.order(order_id)
    stop = order.delivery or order.pickup
    return [w for w in stop.time_windows if w.hardness == "HARD"]


def _slack(problem: Problem, vehicle: Vehicle,
           steps: tuple[Step, ...]) -> int | None:
    """The largest shift that keeps every stop inside its window.

    `min(W_i + l_i - s_i)` from the module docstring, with the end of the shift
    treated as one more deadline -- a route that finishes at the edge of the
    shift cannot be delayed even if every window would allow it.
    """
    waiting = 0
    limit: int | None = None

    for step in steps:
        if step.order_id is None:
            continue
        waiting += step.start_service - step.arrival
        windows = _windows_of(problem, step.order_id)
        if not windows:
            continue
        # The latest of the hard windows containing this service is the one
        # binding it: an order with two windows may be delayed into the second.
        latest = max((w.end for w in windows if w.contains(step.start_service)),
                     default=None)
        if latest is None:
            return None                       # already outside every window
        room = waiting + (latest - step.start_service)
        limit = room if limit is None else min(limit, room)

    # No end-of-shift term, and that is a claim rather than an omission. The
    # chosen shift is capped at the total wait `W_n`, so the push at the last
    # stop is `max(0, d - W_n) = 0`: the route ends at exactly the moment it
    # ended before, however much later the driver leaves. An earlier version
    # carried an `end_room` term here and no perturbation could make it matter,
    # which is what sent us looking for the proof.
    #
    # A route already finishing past its shift is a different matter: it is
    # illegal before this pass touches it, and moving its departure would
    # disguise that rather than fix it.
    if steps[-1].arrival > vehicle.shift.end:
        return 0
    return max(0, limit) if limit is not None else 0


def _total_wait(problem: Problem, steps: tuple[Step, ...]) -> int:
    """Waiting is the only thing a later departure can absorb, so it is also
    the point past which leaving later stops helping."""
    return sum(step.start_service - step.arrival
               for step in steps if step.order_id is not None)


def optimal_departure(problem: Problem, vehicle_id: str,
                      order_ids: list[str], rules=None) -> int:
    """The departure that minimises duty duration. ALG-5, exact.

    Args:
        problem: the instance.
        vehicle_id: whose duty is being shortened.
        order_ids: the route, in the order it will be driven.
        rules: an hours-of-service rule set, or None for no legal limit.

    Returns:
        A departure no earlier than the shift's start. The earliest departure
        achieving the shortest duty, so the slack that is not needed stays
        available to whatever runs next.

    The arithmetic is exact and linear (see the module docstring). With rules in
    play the answer is confirmed against `schedule_route` and halved back
    deterministically if the shifted timeline does not fit -- breaks are placed
    against driving time so a uniform shift should not disturb them, but
    "should not" is not a thing to return a plan on.
    """
    vehicle = problem.vehicle(vehicle_id)
    if not order_ids:
        return vehicle.shift.start

    base = schedule_route(problem, vehicle_id, order_ids, rules=rules)
    room = _slack(problem, vehicle, base.steps)
    if not room:
        return vehicle.shift.start

    shift = min(room, _total_wait(problem, base.steps))
    if shift <= 0:
        return vehicle.shift.start

    candidate = vehicle.shift.start + shift
    if rules is None:
        return candidate

    for _ in range(_WALKBACK_STEPS):
        if _fits(problem, vehicle, vehicle_id, order_ids, candidate, rules):
            return candidate
        shift //= 2
        if shift <= 0:
            break
        candidate = vehicle.shift.start + shift
    return vehicle.shift.start


def _fits(problem: Problem, vehicle: Vehicle, vehicle_id: str,
          order_ids: list[str], departure: int, rules) -> bool:
    scheduled = schedule_route(problem, vehicle_id, order_ids, rules=rules,
                               start_time=departure)
    if not scheduled.legal:
        return False
    for step in scheduled.steps:
        if step.order_id is None:
            continue
        windows = _windows_of(problem, step.order_id)
        if windows and not any(w.contains(step.start_service) for w in windows):
            return False
    return scheduled.steps[-1].arrival <= vehicle.shift.end


# --------------------------------------------------------------------------
# TSPTW sequencing
# --------------------------------------------------------------------------

def _route_nodes(problem: Problem, vehicle_id: str,
                 order_ids: list[str]) -> tuple[int, int, list[int], list[int]]:
    vehicle = problem.vehicle(vehicle_id)
    index = {location.id: location.matrix_index for location in problem.locations}
    stops = [index[(problem.order(o).delivery or problem.order(o).pickup
                    ).location_id] for o in order_ids]
    return (index[vehicle.start_location_id], index[vehicle.end_location_id],
            stops, [])


def _service_and_window(problem: Problem, vehicle: Vehicle, order_id: str):
    from vrp.model import service_time

    order = problem.order(order_id)
    stop = order.delivery or order.pickup
    location = problem.location(stop.location_id)
    windows = _windows_of(problem, order_id)
    opens = min((w.start for w in windows), default=0)
    closes = max((w.end for w in windows), default=vehicle.shift.end)
    return service_time(order, vehicle, location), opens, closes


def tsptw_sequence(problem: Problem, vehicle_id: str,
                   order_ids: list[str]) -> list[str] | None:
    """The optimal visiting order for a short route. ALG-5, Held-Karp with windows.

    Args:
        problem: the instance.
        vehicle_id: the vehicle that will drive it.
        order_ids: the stops, in any order.

    Returns:
        The cheapest legal sequence by travel time, or None when the route is
        longer than `MAX_DP_STOPS` or has no legal sequence at all. None rather
        than a best effort: ALG-5 bounds the DP because it is exponential, and
        an optimal sequence for the first fourteen stops of a twenty-stop route
        answers a question nobody asked.

    The state carries earliest feasible arrival alongside cost, which is what
    makes this TSPTW rather than TSP. Two partial routes over the same subset
    ending at the same stop are not interchangeable if one arrives earlier: the
    cheaper one may have no legal completion. Both are kept when neither
    dominates.
    """
    if len(order_ids) > MAX_DP_STOPS or not order_ids:
        return None

    vehicle = problem.vehicle(vehicle_id)
    start, end, stops, _ = _route_nodes(problem, vehicle_id, order_ids)
    matrix = problem.matrix
    count = len(order_ids)
    facts = [_service_and_window(problem, vehicle, o) for o in order_ids]

    # state[(mask, last)] -> list of non-dominated (cost, ready, path)
    state: dict[tuple[int, int], list[tuple[int, int, tuple[int, ...]]]] = {}
    for i in range(count):
        service, opens, closes = facts[i]
        arrival = vehicle.shift.start + matrix.duration(start, stops[i])
        begin = max(arrival, opens)
        if begin > closes:
            continue
        state[(1 << i, i)] = [(matrix.duration(start, stops[i]),
                               begin + service, (i,))]

    for _ in range(count - 1):
        nxt: dict[tuple[int, int], list[tuple[int, int, tuple[int, ...]]]] = {}
        for (mask, last), labels in state.items():
            for cost, ready, path in labels:
                for i in range(count):
                    if mask & (1 << i):
                        continue
                    service, opens, closes = facts[i]
                    leg = matrix.duration(stops[last], stops[i])
                    begin = max(ready + leg, opens)
                    if begin > closes:
                        continue
                    _push(nxt, (mask | (1 << i), i),
                          (cost + leg, begin + service, (*path, i)))
        if not nxt:
            return None
        state = _merge(state, nxt)

    full = (1 << count) - 1
    best = None
    for (mask, last), labels in state.items():
        if mask != full:
            continue
        for cost, ready, path in labels:
            total = cost + matrix.duration(stops[last], end)
            if ready + matrix.duration(stops[last], end) > vehicle.shift.end:
                continue
            if best is None or total < best[0]:
                best = (total, path)

    return None if best is None else [order_ids[i] for i in best[1]]


def _push(table, key, label) -> None:
    """Insert a label, dropping any it dominates and skipping any that
    dominates it. Cheaper *and* readier is strictly better; one of each is not."""
    cost, ready, _ = label
    kept = []
    for other in table.get(key, ()):
        if other[0] <= cost and other[1] <= ready:
            return
        if not (cost <= other[0] and ready <= other[1]):
            kept.append(other)
    kept.append(label)
    table[key] = kept


def _merge(older, newer):
    """Layers are disjoint by subset size, so the newer layer simply replaces."""
    _ = older
    return newer


# --------------------------------------------------------------------------
# Both passes
# --------------------------------------------------------------------------

def polish_route(problem: Problem, vehicle_id: str, order_ids: list[str],
                 rules=None) -> PolishedRoute:
    """Resequence where the DP applies, then schedule the departure. ALG-5.

    The two passes are independent, and the departure pass runs whether or not
    the DP did. That matters: the DP declines above ~14 stops, and giving up on
    both would drop the half ALG-5 calls "nearly free" precisely on the long
    routes where the waiting adds up.
    """
    sequence = list(order_ids)
    resequenced = False

    # Taken whenever the DP produced one, without a "is it cheaper?" guard.
    # The DP's answer is optimal *among legal sequences*, so when the incoming
    # sequence is legal the guard can never fire, and when it is illegal the
    # guard fires wrongly: it compared travel time alone and so rejected a legal
    # 1,100-second sequence in favour of an illegal 300-second one.
    improved = tsptw_sequence(problem, vehicle_id, sequence)
    if improved is not None and improved != sequence:
        sequence, resequenced = improved, True

    departure = optimal_departure(problem, vehicle_id, sequence, rules=rules)
    scheduled = schedule_route(problem, vehicle_id, sequence, rules=rules,
                               start_time=departure)
    return PolishedRoute(order_ids=sequence, departure=departure,
                         duty=duty_duration(scheduled.steps),
                         steps=scheduled.steps, resequenced=resequenced)


def _travel_time(problem: Problem, vehicle_id: str,
                 order_ids: list[str]) -> int:
    vehicle = problem.vehicle(vehicle_id)
    index = {location.id: location.matrix_index for location in problem.locations}
    nodes = [index[vehicle.start_location_id]]
    nodes += [index[(problem.order(o).delivery or problem.order(o).pickup
                     ).location_id] for o in order_ids]
    nodes.append(index[vehicle.end_location_id])
    return sum(problem.matrix.duration(a, b) for a, b in pairwise(nodes))
