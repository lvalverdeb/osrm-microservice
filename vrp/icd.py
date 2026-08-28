"""Iterative conditional dispatch — §8.2 step 3, T-54.

§8.2: "Sample future request scenarios, solve each sampled instance, and use
consensus across scenarios (requests dispatched in most scenarios are
dispatched; those dispatched in almost none are postponed) with thresholds
applied iteratively. Reported to come close to the winning learned approach on
the competition instances while being far simpler and requiring no training
pipeline. **This is the recommended default for v1** -- it needs no labelled
data and degrades gracefully."

The idea in one line: an open request should go now if waiting would not buy it
company. Sampling answers that. Draw plausible futures, ask in each one whether
this request would still have been sent, and let the ones that keep coming back
go.

**What a sampled scenario decides.** Postponing is worth something only when
later work lands near enough to share a route. So in each scenario the request
is "dispatched" when postponing it would leave it travelling alone -- no sampled
future arrival close enough to consolidate with -- or when the wait would make
it must-go. Both are cheap to evaluate, which matters: this runs once per open
request per scenario per epoch.

**Iteratively** is the other half of §8.2's sentence and does real work. One
pass fixes the confident cases at either end; the requests in the middle are
re-judged with those decisions taken as given, which moves some of them off the
fence. Without it the undecided band is simply split by a single threshold and
the word "conditional" means nothing.

Placement: **Python**, per criterion 2. A policy over the domain model, consumed
by the epoch controller and measured by the replayer.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from vrp.epochs import Classification, Epoch, Policy, must_go
from vrp.model import Problem

FULL = 1000


@dataclass(frozen=True)
class Thresholds:
    """§8.2's two consensus cut-offs, in parts per thousand.

    A request dispatched in at least `dispatch` of the scenarios goes now; one
    dispatched in at most `postpone` waits. The band between them is what the
    iteration is for.
    """

    dispatch: int = 600
    postpone: int = 200

    def __post_init__(self) -> None:
        if not 0 <= self.postpone < self.dispatch <= FULL:
            raise ValueError(
                f"thresholds must satisfy 0 <= postpone < dispatch <= {FULL}; "
                f"got postpone={self.postpone}, dispatch={self.dispatch}")


def icd_policy(problem: Problem, horizon: int, scenarios: int = 8,
               rounds: int = 3, thresholds: Thresholds | None = None,
               seed: int = 0) -> Policy:
    """§8.2's ICD, as a dispatch policy.

    Args:
        problem: the instance, for geography and windows.
        horizon: the end of the planning day, bounding sampled arrivals.
        scenarios: how many futures to draw. §8.2 gives no number; more costs
            linearly and the consensus stops moving quickly.
        rounds: how many times to re-judge the undecided band. One round is a
            plain threshold split, which is what "iteratively" rules out.
        thresholds: the two consensus cut-offs.
        seed: same seed, same decisions (CON-4).

    Returns:
        A policy closing over its own generator, advancing across epochs so a
        request is not judged against the same imagined future all day.
    """
    cuts = thresholds or Thresholds()
    rng = random.Random(seed)

    def policy(open_ids: Sequence[str], split: Classification,
               epoch: Epoch) -> Sequence[str]:
        # The epoch is the whole reason this policy differs from lazy. Sampling
        # a future that ignores how much day is left judges the last wave by
        # the same imagined arrivals as the first, and the consensus never
        # moves -- measured, that scored identically to lazy at 4, 8 and 16
        # scenarios, which is what a policy that is not thinking looks like.
        postponed_to = epoch.end
        decided: dict[str, bool] = {order_id: True for order_id in split.must_go}
        undecided = [o for o in open_ids if o not in decided]

        for _ in range(rounds):
            if not undecided:
                break
            votes = _consensus(problem, undecided, decided, postponed_to,
                               horizon, scenarios, rng)
            still: list[str] = []
            for order_id in undecided:
                share = votes[order_id]
                if share >= cuts.dispatch:
                    decided[order_id] = True
                elif share <= cuts.postpone:
                    decided[order_id] = False
                else:
                    still.append(order_id)
            if len(still) == len(undecided):
                break            # nothing moved; further rounds cannot either
            undecided = still

        # Whatever is still on the fence after the last round waits. §8.2 makes
        # ICD the conservative default, and AC-3.1 already guarantees the only
        # work that cannot wait is dispatched regardless.
        return tuple(order_id for order_id in open_ids
                     if decided.get(order_id, False))

    return policy


def _consensus(problem: Problem, undecided: Sequence[str],
               decided: dict[str, bool], postponed_to: int, horizon: int,
               scenarios: int, rng: random.Random) -> dict[str, int]:
    """How often each undecided request is dispatched across sampled futures.

    The "conditional" in ICD: requests already fixed this epoch are part of the
    picture, so a request judged in round two is judged against a future that
    includes round one's dispatches.
    """
    going = [order_id for order_id, yes in decided.items() if yes]
    tally = dict.fromkeys(undecided, 0)

    for _ in range(scenarios):
        future = _sample_future(problem, postponed_to, horizon, rng)
        for order_id in undecided:
            if _would_send(problem, order_id, going, future, postponed_to):
                tally[order_id] += 1

    return {order_id: count * FULL // max(scenarios, 1)
            for order_id, count in tally.items()}


def _sample_future(problem: Problem, postponed_to: int, horizon: int,
                   rng: random.Random) -> set[str]:
    """One plausible set of requests still to arrive after this epoch.

    Drawn from the instance's own order pool, which is the only distribution
    available offline. §12.4's telematics would replace it with a fitted
    arrival model; until then the sample says what it is.
    """
    remaining = max(horizon - postponed_to, 0)
    span = max(horizon - problem.vehicles[0].shift.start, 1)
    likelihood = remaining * FULL // span
    return {order.id for order in problem.orders
            if rng.randrange(FULL) < likelihood}


def _would_send(problem: Problem, order_id: str, going: Sequence[str],
                future: set[str], postponed_to: int) -> bool:
    """Whether this scenario says to send the request now rather than wait.

    Two reasons to send: waiting would make it must-go, or waiting would leave
    it alone. Consolidation is the only thing postponement buys, so a request
    with no sampled company later is a request that gains nothing by waiting --
    and will make the same trip tomorrow morning with one fewer stop on it.
    """
    if must_go(problem, problem.order(order_id), postponed_to):
        return True

    stop = _node(problem, order_id)
    neighbours = [other for other in future
                  if other != order_id and other not in going]
    if not neighbours:
        return True

    # "Near enough to share a route" is judged against this instance's own
    # scale rather than a constant: a metre is a long way in a warehouse and
    # nothing at all between cities.
    longest, _ = problem.matrix.extremes()
    reach = max(longest // 2, 1)
    return not any(problem.matrix.distance(stop, _node(problem, other)) <= reach
                   for other in neighbours)


def _node(problem: Problem, order_id: str) -> int:
    order = problem.order(order_id)
    stop = order.delivery or order.pickup
    return problem.location(stop.location_id).matrix_index
