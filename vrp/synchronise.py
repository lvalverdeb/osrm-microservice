"""Making coupled routes actually meet — FR-26, DEC-1, T-76.

`INV-15` checks that two coupled routes meet. This is the half that tries to
make them, and it is a loop rather than a constraint for the reason `UC-131`
gives: "the second-echelon departure depends on the first echelon's arrival,
which is a synchronisation constraint **across two routing problems**." PyVRP
solves one at a time and has no construct relating two routes' timelines, so
nothing can be compiled in. What can be done is to solve, look at when the
first half actually happened, tell the second half it may not start before
that, and solve again.

**The lever is a time window, which is why this converges for a transfer and
not in general.** Pinning the second order to start after the first's departure
is a one-directional tightening: the first is not constrained by the second, so
its departure does not move in response, and each round either meets the
coupling or fails on something the search can report. A convoy is symmetric --
each half constrains the other -- and tightening both can chase itself around
the horizon, so it is bounded and reported rather than promised.

**A coupling also needs its two ends on different vehicles, and that half
cannot be done here at all.** It is an order-to-order constraint, the same
shape as the class incompatibility `T-72` refused to compile, and a window says
nothing about it: one van collecting at the satellite it just delivered to is
simply a good route. Trying to force it with `FORBID_ORDER_ON_VEHICLE` locks
was the first version of this module and it forbade the second order on every
vehicle in turn until the instance had no answer -- a loop steering with a
lever that does not turn. So it is refused by name, and the fix belongs to the
instance: a satellite has a receiving bay and a dispatch bay, and giving them
different eligibility is both realistic and expressible.

**The authority is the verifier, not this.** A loop that ran out of rounds
still returns a plan, and `INV-15` is what says whether it is one. That
division is deliberate: a module that both enforced a constraint and certified
it would be marking its own work.

Placement: **Python**, per criterion 2. It orchestrates a solver, which is
where `DEC-1` puts constraints that span routes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from vrp.model import (
    Problem,
    Solution,
    Synchronisation,
    TimeWindow,
)

Solve = Callable[[Problem], Solution]


def _timing(solution: Solution) -> dict[str, tuple[str, int, int]]:
    """Order id to (vehicle, when service started, when it left)."""
    return {step.order_id: (route.vehicle_id, step.start_service, step.departure)
            for route in solution.routes for step in route.steps
            if step.order_id is not None}


def unmet(problem: Problem, solution: Solution) -> tuple[Synchronisation, ...]:
    """Couplings this plan does not keep.

    Deliberately its own implementation rather than a call into the verifier.
    CON-1 keeps that module free of any solver, and a loop that steered itself
    by the verifier's answer would make the two agree by construction -- which
    is precisely the agreement the verifier exists to withhold.
    """
    seen = _timing(solution)
    broken = []
    for sync in problem.synchronisations:
        if sync.first not in seen or sync.second not in seen:
            continue
        first_vehicle, first_start, first_departure = seen[sync.first]
        second_vehicle, second_start, _ = seen[sync.second]
        if first_vehicle == second_vehicle:
            broken.append(sync)
            continue
        gap = (second_start - first_departure if sync.kind == "TRANSFER"
               else abs(second_start - first_start))
        if gap < sync.min_gap or (sync.max_gap is not None
                                  and gap > sync.max_gap):
            broken.append(sync)
    return tuple(broken)


def _hold_until(problem: Problem, order_id: str, opens: int) -> Problem:
    """Tell one order it may not start before `opens`, keeping its own window."""
    def narrowed(order):
        if order.id != order_id:
            return order
        stop = order.delivery or order.pickup
        windows = tuple(
            TimeWindow(start=max(window.start, min(opens, window.end)),
                       end=window.end, hardness=window.hardness,
                       earliness_cost_per_sec=window.earliness_cost_per_sec,
                       lateness_cost_per_sec=window.lateness_cost_per_sec)
            for window in stop.time_windows)
        moved = replace(stop, time_windows=windows)
        return (replace(order, delivery=moved) if order.delivery is not None
                else replace(order, pickup=moved))

    return replace(problem, orders=tuple(narrowed(o) for o in problem.orders))


def solve_synchronised(problem: Problem, solve: Solve,
                       max_rounds: int = 6) -> tuple[Solution, Problem]:
    """Solve until every coupling is kept, or say which are not. FR-26.

    Args:
        problem: the instance, carrying its synchronisations.
        solve: the engine. Injected, as elsewhere; a module enforcing a
            constraint across routes has no business choosing a search.
        max_rounds: how many tightenings to try. A transfer needs one per
            coupling in the worst case; a convoy may need more and may need
            more than exist, which is what the bound is for.

    Returns:
        The plan, and the problem it was solved against -- which carries the
        narrowed windows, so a reader can see what the coupling cost.

    Raises:
        RuntimeError: when the rounds run out with couplings still unmet. The
            plan is not returned quietly: an unmet coupling is a plan whose two
            halves do not meet, and `UC-131`'s cargo bikes leaving before the
            lorry arrives is not a slightly worse answer.
    """
    current = problem
    for _ in range(max_rounds + 1):
        solution = solve(current)
        broken = unmet(current, solution)
        if not broken:
            return solution, current

        seen = _timing(solution)
        tightened = current
        for sync in broken:
            if sync.first not in seen or sync.second not in seen:
                continue
            if seen[sync.first][0] == seen[sync.second][0]:
                raise NotImplementedError(
                    f"{sync.kind} couples {sync.first} and {sync.second}, and "
                    f"{seen[sync.first][0]} carries both. Keeping them apart is "
                    "an order-to-order constraint -- the same shape as the "
                    "class incompatibility the adapters refuse -- and a time "
                    "window cannot express it: a single vehicle doing both "
                    "ends is simply a good route. Give the two halves "
                    "different eligibility, or plan the echelons as separate "
                    "problems")
            if sync.kind == "TRANSFER":
                _, _, departure = seen[sync.first]
                tightened = _hold_until(tightened, sync.second,
                                        departure + sync.min_gap)
            else:
                # Symmetric, so both halves are pulled towards the later of
                # them: moving only one would simply hand the gap to the other.
                _, first_start, _ = seen[sync.first]
                _, second_start, _ = seen[sync.second]
                meet = max(first_start, second_start)
                tightened = _hold_until(tightened, sync.first, meet)
                tightened = _hold_until(tightened, sync.second, meet)
        if tightened == current:
            break
        current = tightened

    return _refuse(current, solve)


def _refuse(current: Problem, solve: Solve):
    solution = solve(current)
    broken = unmet(current, solution)
    raise RuntimeError(
        f"{[f'{s.kind} {s.first}->{s.second}' for s in broken]} still do not "
        "meet after narrowing every window they allow. A transfer that cannot "
        "be made is usually a horizon too short for two echelons; a convoy "
        "that cannot be formed is usually two windows that never overlap, and "
        "pre-flight reports neither because both are properties of the pair")
