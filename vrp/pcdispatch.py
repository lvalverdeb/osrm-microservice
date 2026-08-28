"""Prize-collecting dispatch — §8.2 step 2, T-55.

§8.2: "Solve each epoch as a prize-collecting VRPTW in which the prize on each
non-must-go request encodes how much we want it dispatched now. The routing
solver then jointly chooses the dispatch set and the routes. Prizes may start as
a tuned constant and later be predicted by a learned model... This is the
structure that won the competition's dynamic track."

*Jointly* is what separates this from ICD. ICD decides a dispatch set and hands
it to a router; here the router decides both at once, because whether a request
is worth sending now depends on the route it would join, and that is exactly
what a solver already computes.

The mechanism is entirely T-27's. `_is_required` makes an order droppable when
it carries a prize and sits above priority tier 0, and `PRIZE_COLLECTING` puts
forgone prize and routing cost in one currency. So an epoch becomes: must-go
work required, deferrable work priced, solve, and whatever the solver chose to
serve is the dispatch set. No new objective, no new solver mode -- the dispatch
question turns out to be a shape the objective already had.

**The constant is the whole policy, and it needs tuning.** Measured on a
six-stop epoch, a prize of 1,000 dispatches nothing and 5,000 dispatches
everything: below the marginal drive the solver declines the work, above it the
solver takes it. Every interesting policy lives in that band, and where the band
sits is a property of the instance's distances rather than a number worth
hard-coding. `tune` finds it by sweep, which is what "tuned constant" means.

Placement: **Python**, per criterion 2. A policy over the domain model, using
the existing adapter and objective.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from vrp.epochs import Classification, Epoch, Policy
from vrp.model import Problem
from vrp.solve import pyvrp_adapter

DEFAULT_ITERATIONS = 50


def epoch_problem(problem: Problem, open_ids: Sequence[str],
                  split: Classification, prize: int) -> Problem:
    """The open work as a prize-collecting instance. §8.2 step 2.

    Must-go requests keep prize 0, which `_is_required` reads as "not for
    sale" -- AC-3.1 in the model rather than bolted on afterwards. Everything
    else is priced at the tuned constant and sits above tier 0, so the solver
    is free to decline it.
    """
    urgent = set(split.must_go)
    orders = tuple(
        replace(problem.order(order_id),
                prize=0 if order_id in urgent else prize,
                priority_tier=0 if order_id in urgent else 2)
        for order_id in open_ids)
    return replace(problem, orders=orders)


def pc_policy(problem: Problem, prize: int,
              iterations: int = DEFAULT_ITERATIONS, seed: int = 0) -> Policy:
    """§8.2's prize-collecting dispatch, as a policy.

    Args:
        problem: the instance.
        prize: the tuned constant on every deferrable request.
        iterations: solver budget per epoch. §8.4 puts an epoch replan in the
            T2 tier at five minutes; this needs far less, because an epoch
            holds only the open work.
        seed: same seed, same dispatch (CON-4).

    Returns:
        A policy whose dispatch set is whatever the solver chose to serve.

    The epoch is not consulted. Unlike ICD this policy has no model of the
    future at all -- it prices the present and lets the router decide. That is
    the competition-winning structure's actual shape, and it is worth being
    plain that the anticipation §8.2 mentions comes later, from a learned prize
    rather than from this constant one.
    """
    def policy(open_ids: Sequence[str], split: Classification,
               epoch: Epoch) -> Sequence[str]:
        if not open_ids:
            return ()
        sub = epoch_problem(problem, open_ids, split, prize)
        try:
            solution = pyvrp_adapter.solve(sub, iterations=iterations,
                                           seed=seed)
        except (ValueError, RuntimeError):
            # A solver that cannot place the epoch is not a reason to lose
            # work; AC-3.1 still has to hold, so fall back to the must-go set
            # and let `decide` enforce the rest.
            return split.must_go
        return tuple(step.order_id for route in solution.routes
                     for step in route.steps if step.order_id)

    return policy


def tune(problem: Problem, days, epoch_length: int, candidates: Sequence[int],
         iterations: int = DEFAULT_ITERATIONS, seed: int = 0
         ) -> tuple[int, dict[int, int]]:
    """Sweep the constant and return the cheapest, with the whole curve.

    §8.2 says "prizes may start as a tuned constant", and the tuning is not a
    detail: below the marginal drive the solver declines everything and the
    policy is lazy; above it the solver takes everything and the policy is
    greedy. The band between is where the constant does any work, and it sits
    wherever this instance's distances put it.

    Returns:
        The best candidate and the cost of every one, so the curve can be
        reported rather than just its minimum -- a flat curve would mean the
        prize is not controlling anything.
    """
    from vrp.replay import replay

    curve = {}
    for prize in candidates:
        policy = pc_policy(problem, prize, iterations=iterations, seed=seed)
        curve[prize] = sum(replay(problem, day, policy, epoch_length).cost
                           for day in days)
    return min(curve, key=lambda p: curve[p]), curve
