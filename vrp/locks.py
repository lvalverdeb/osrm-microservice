"""Minimal conflicting lock sets — §6.6, FR-21, CON-7, T-29.

§6.6: "If locks make the instance infeasible, the system MUST return
`INFEASIBLE` with the minimal conflicting lock set (an IIS-style diagnosis),
never silently drop a lock."

CON-7 says why the wording is that strong: "Human override is a first-class
input, not a failure... The system MUST NOT silently discard operator intent."
There are two ways to fail a dispatcher here and only one of them looks like a
failure. Dropping a lock and returning a plan overrules them without telling
them. Returning a bare "infeasible" tells them nothing they can act on: they
have twelve locks and no idea which two disagree.

**Minimal means irreducible.** Every lock reported must be load-bearing --
remove any one and the instance becomes feasible. Reporting all twelve would be
true and useless; reporting a lock that is not part of the conflict sends
somebody to undo a decision that was fine.

The algorithm is the standard deletion filter, which is O(n) feasibility checks
for n locks and gives one irreducible set. There may be several genuine
conflicts in a badly locked instance; this finds one of them, which is what an
IIS is. Reporting *all* minimal conflicts is exponential and is not what §6.6
asks for.

**What "feasible" means here is deliberately narrow.** It asks whether the locks
leave every order with a vehicle that could serve it, reusing `vrp.diagnose`
rather than running a solve. That is cheap enough for the deletion filter's
repeated calls, and it is the right question: a conflict the solver would find
only after an expensive search is still a conflict, and one that pre-flight can
see is one the dispatcher can be told about immediately.

Placement: Python. Operator intent is constraint modelling.
"""

from __future__ import annotations

from vrp.diagnose import preflight
from vrp.model import Lock, Problem


def _replace_locks(problem: Problem, locks: tuple[Lock, ...]) -> Problem:
    return Problem(id=problem.id, locations=problem.locations,
                   orders=problem.orders, vehicles=problem.vehicles,
                   matrix=problem.matrix, horizon=problem.horizon, locks=locks)


def _deployable(problem: Problem) -> bool:
    """Is any vehicle left to send out at all?

    FORBID_DEPLOY on the whole fleet is a conflict the per-order checks would
    miss: every order individually has an eligible vehicle right up until the
    last one is forbidden.
    """
    forbidden = {lock.vehicle_id for lock in problem.locks
                 if lock.kind == "FORBID_DEPLOY"}
    if not forbidden:
        return True
    return any(vehicle.id not in forbidden for vehicle in problem.vehicles)


def is_feasible_under_locks(problem: Problem) -> bool:
    """Whether every order still has a vehicle that could serve it.

    Not a solve. Pre-flight asks whether an order is servable by *some* vehicle
    ignoring the others, so this can say "no" with certainty and "yes" only
    provisionally -- which is the right way round for a diagnosis. A false
    "feasible" here means the deletion filter keeps a lock it might have
    dropped, so the reported set stays sound; a false "infeasible" would invent
    conflicts, and cannot happen because every code pre-flight emits names a
    real obstacle.
    """
    if not _deployable(problem):
        return False
    return not preflight(problem)


def minimal_conflict(problem: Problem) -> tuple[Lock, ...]:
    """One irreducible set of locks that together make the instance infeasible.

    Args:
        problem: the instance, with the operator's locks on it.

    Returns:
        An irreducible conflicting subset, or `()` when the locks are not the
        cause. Empty covers two different situations that deserve to be
        distinguished by the caller: a feasible instance, and one that is
        infeasible for reasons of its own -- an order too heavy for any van is
        not the dispatcher's fault, and blaming their locks would send them to
        unpick decisions that were never the problem. §6.5's reason codes own
        that case.

    The deletion filter: try the instance without each lock in turn. If it is
    still infeasible without that lock, the lock was not needed for the
    conflict and is dropped permanently. What survives is irreducible by
    construction -- every remaining lock is one whose removal restores
    feasibility.
    """
    if is_feasible_under_locks(problem):
        return ()

    # Early exit, not a correctness guard -- and the distinction is worth
    # keeping straight. When the locks are not the cause, the deletion filter
    # below discards every one of them and returns () anyway; perturbation
    # confirmed removing this line changes no result. It is here to skip n
    # feasibility checks in the common "the instance was always broken" case.
    if not is_feasible_under_locks(_replace_locks(problem, ())):
        return ()

    candidates = list(problem.locks)
    for lock in list(candidates):
        without = tuple(other for other in candidates if other is not lock)
        if not is_feasible_under_locks(_replace_locks(problem, without)):
            # Still broken without it, so it is not carrying the conflict.
            candidates = list(without)

    return tuple(candidates)
