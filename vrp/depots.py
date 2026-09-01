"""Depot inventory, enforced globally — FR-31, DEC-1, §7.8, T-72.

A depot is not a spring. `INV-13` says no depot supplies more than it holds,
counted across every route drawing on it, and until this module existed nothing
made a plan obey it: `vrp.diagnose` reported `DEPOT_STOCKOUT` before the solve
and the independent verifier reported the over-draw afterwards, and in between
the search drew whatever it liked.

**Why this is not a depot-allocation step.** The obvious fix is to assign each
order to a depot first and route within the assignment. `UC-134` names that as
the wrong answer in as many words -- "fixing assignment before routing
forecloses the cheapest plans" -- and §7.8 agrees, requiring allocation to be
"solved *jointly* with routing". A pre-assignment would satisfy `INV-13` by
answering a smaller question.

**So the search keeps choosing, and only impossible choices are withdrawn.**
Solve; if a depot over-draws, forbid the marginal orders *on that depot's
vehicles* and solve again. The search is free to place them anywhere else,
including at a depot it would not have picked first. Each round withdraws at
least one (order, depot) pair from a finite set, so it terminates.

**The withdrawal is a lock, not a private mechanism.** `FORBID_ORDER_ON_VEHICLE`
is `FR-21`'s own construct, the adapter compiles it into the search alongside
skills and site access, and the verifier checks it under `INV-8`. A bespoke ban
would have been a second way to say the same thing, invisible to both.

Which orders are withdrawn is a business question and is answered the way §5.1
orders everything else: the lowest priority tier first, then the smallest prize,
then the identifier, so the answer is deterministic (CON-4) and the work that
survives a shortage is the work that was declared to matter.

Placement: **Python**, per criterion 2. This orchestrates a solver rather than
being one, which is where DEC-1 puts global constraints -- beside the dock
schedule `vrp.decompose` already staggers.
"""

from __future__ import annotations

from collections.abc import Callable

from vrp.model import Lock, Problem, Solution

Solve = Callable[[Problem], Solution]


def drawn_per_depot(problem: Problem,
                    solution: Solution) -> dict[str, dict[str, int]]:
    """What each depot supplied, per dimension. The `INV-13` quantity.

    Recomputed from the orders each route carries rather than read from
    `load_after`, for INV-9's reason: a plan need not carry loads at all, and a
    check that trusts them passes an over-draw by finding nothing to look at.
    A pickup is stock arriving, not leaving, and does not count.
    """
    drawn: dict[str, dict[str, int]] = {}
    for route in solution.routes:
        home = drawn.setdefault(
            problem.vehicle(route.vehicle_id).start_location_id, {})
        for step in route.steps:
            if step.order_id is None:
                continue
            order = problem.order(step.order_id)
            if order.delivery is None:
                continue
            for dimension, amount in order.quantities.items():
                home[dimension] = home.get(dimension, 0) + amount
    return drawn


def over_drawn(problem: Problem, solution: Solution) -> dict[str, dict[str, int]]:
    """By how much each depot exceeds its stock, per dimension."""
    excess: dict[str, dict[str, int]] = {}
    for depot_id, totals in drawn_per_depot(problem, solution).items():
        stock = problem.location(depot_id).inventory or {}
        for dimension, amount in totals.items():
            held = stock.get(dimension)
            if held is not None and amount > held:
                excess.setdefault(depot_id, {})[dimension] = amount - held
    return excess


def _orders_on(problem: Problem, solution: Solution, depot_id: str) -> list[str]:
    """Deliveries this depot supplied, least worth protecting first.

    §5.1's order of business, reused rather than re-argued: priority tier
    decides, the prize breaks a tie within a tier, and the identifier breaks
    what is left so two runs agree (CON-4).
    """
    ids = [step.order_id for route in solution.routes
           if problem.vehicle(route.vehicle_id).start_location_id == depot_id
           for step in route.steps
           if step.order_id and problem.order(step.order_id).delivery]
    return sorted(ids, key=lambda oid: (-problem.order(oid).priority_tier,
                                        problem.order(oid).prize, oid))


def _withdrawals(problem: Problem, solution: Solution,
                 excess: dict[str, dict[str, int]]) -> tuple[Lock, ...]:
    """Locks that take the over-drawn work off the depot that cannot supply it."""
    locks: list[Lock] = []
    fleet = {depot_id: [v.id for v in problem.vehicles
                        if v.start_location_id == depot_id]
             for depot_id in excess}
    for depot_id, shortfalls in sorted(excess.items()):
        remaining = dict(shortfalls)
        for order_id in _orders_on(problem, solution, depot_id):
            if not any(short > 0 for short in remaining.values()):
                break
            quantities = problem.order(order_id).quantities
            if not any(remaining.get(d, 0) > 0 for d in quantities):
                continue
            for dimension, amount in quantities.items():
                if dimension in remaining:
                    remaining[dimension] -= amount
            locks.extend(Lock(kind="FORBID_ORDER_ON_VEHICLE",
                              order_id=order_id, vehicle_id=vehicle_id)
                         for vehicle_id in fleet[depot_id])
    return tuple(locks)


def solve_within_inventory(problem: Problem, solve: Solve,
                           max_rounds: int = 8) -> tuple[Solution, Problem]:
    """Solve until no depot supplies more than it holds. FR-31, DEC-1.

    Args:
        problem: the instance.
        solve: the engine, already carrying its own budget and seed. Injected
            because a module enforcing a global constraint has no business
            choosing a search.
        max_rounds: how many withdrawals to make before giving up. Each round
            removes at least one (order, depot) pair from a finite set, so the
            bound is a guard against a solver that ignores its locks rather
            than against non-termination.

    Returns:
        The plan, and the problem it was solved against -- which carries the
        withdrawal locks, so a caller can see *why* an order went where it did
        and the verifier can check them under INV-8.

    Raises:
        RuntimeError: if the rounds ran out with a depot still over-drawn,
            which means the locks are not reaching the search.
    """
    current = problem
    for _ in range(max_rounds + 1):
        solution = solve(current)
        excess = over_drawn(current, solution)
        if not excess:
            return solution, current
        new_locks = _withdrawals(current, solution, excess)
        if not new_locks:
            break
        current = _with_locks(current, new_locks)
    raise RuntimeError(
        f"{sorted(over_drawn(current, solve(current)))} still supply more than "
        "they hold after withdrawing the over-drawn work. Either the locks are "
        "not reaching the search or no depot has the stock, and the second is "
        "a stockout pre-flight should have reported")


def _with_locks(problem: Problem, locks: tuple[Lock, ...]) -> Problem:
    existing = set(problem.locks)
    return Problem(id=problem.id, locations=problem.locations,
                   orders=problem.orders, vehicles=problem.vehicles,
                   matrix=problem.matrix, horizon=problem.horizon,
                   locks=problem.locks + tuple(x for x in locks
                                               if x not in existing))
