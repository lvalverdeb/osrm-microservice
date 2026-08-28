"""The epoch controller and must-go classifier — FR-22, DYN-1, DYN-2, AC-3.1,
§8.1, T-51.

§8.1 frames the slice: "Same-day and on-demand operations are not static
problems solved repeatedly. They are sequential decision problems under
uncertainty... at each epoch the agent observes the requests known so far and
must decide which to **dispatch now** -- committing them to feasible routes --
and which to **postpone** so they can be consolidated with requests that arrive
later. Some requests are **must-go**: postponing them makes their time window
unreachable."

DYN-2 says which way the classifier must be wrong when it is unsure:
"Conservative by construction; false negatives are service failures."

That asymmetry is the design. Calling a deferrable order must-go costs a little
consolidation -- the van goes out slightly emptier than it might have. Calling a
must-go order deferrable costs a delivery that never happens, and the customer
finds out before the dispatcher does. So every uncertain case resolves to
must-go: no fleet, no matrix entry, no way to tell.

AC-3.1's guarantee -- the system "never postpones a `must-go`" -- lives in
`decide` rather than in the policy. T-52's baselines are deliberately dumb, one
of them is literally random, and a service failure must not be reachable by
choosing a bad policy. When the guarantee overrides a policy it says so, because
"this policy is losing money" and "this policy is causing service failures" are
different findings and T-53's replayer has to tell them apart.

Placement: **Python**, per criterion 2. This reads the constraint model and
composes with the dispatch policies; it changes whenever either does.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from vrp.diagnose import _reachable
from vrp.model import Order, Problem, TimeWindow


@dataclass(frozen=True)
class Epoch:
    """One dispatch wave. §8.1's "the horizon is partitioned into epochs"."""

    index: int
    start: int
    end: int


@dataclass(frozen=True)
class Classification:
    """The open work, split. AC-3.1's two categories."""

    must_go: tuple[str, ...]
    deferrable: tuple[str, ...]


@dataclass(frozen=True)
class Dispatch:
    """One epoch's decision. FR-22's "dispatched-now vs postponed-to-next-wave".

    `forced` is the must-go work the policy tried to postpone and was overruled
    on. Reported rather than folded silently into `dispatched`, because a policy
    that has to be overruled is a different finding from one that is merely
    expensive.
    """

    dispatched: tuple[str, ...]
    postponed: tuple[str, ...]
    forced: tuple[str, ...]


Policy = Callable[[Sequence[str], Classification], Sequence[str]]


def epochs(horizon: TimeWindow, length: int) -> tuple[Epoch, ...]:
    """Partition the horizon into dispatch waves. DYN-1.

    Args:
        horizon: the planning day.
        length: epoch length in seconds.

    Returns:
        Contiguous, non-overlapping epochs covering the whole horizon. The last
        one is short where the horizon does not divide evenly -- rounding the
        tail away would silently drop the last minutes of the day, and the
        requests that arrive in them.

    Raises:
        ValueError: if `length` is not positive.
    """
    if length <= 0:
        raise ValueError("an epoch length must be positive")
    waves = []
    start, index = horizon.start, 0
    while start < horizon.end:
        waves.append(Epoch(index=index, start=start,
                           end=min(start + length, horizon.end)))
        start, index = start + length, index + 1
    return tuple(waves)


def must_go(problem: Problem, order: Order, postponed_to: int) -> bool:
    """Whether postponing this order to `postponed_to` makes it unservable.

    Args:
        problem: the instance.
        order: the open request.
        postponed_to: the instant it would wait until -- the next epoch's start.

    Returns:
        True when no vehicle could still reach it inside a hard window. DYN-2's
        "under *any* remaining vehicle": one van in the wrong depot does not
        make an order must-go while another could serve it.

    Conservative wherever the answer is not clearly no. An empty fleet, an
    unreachable stop, a window already closed -- none of these are evidence
    that postponing is *safe*, and an absence of evidence must not be read as
    one. A soft window is the exception and is deliberately not treated as a
    wall: §6.2 prices lateness rather than forbidding it, and treating every
    soft window as impossible would make the whole fleet must-go and postpone
    nothing.
    """
    if not problem.vehicles:
        return True
    return not any(_reachable(problem, order, vehicle, not_before=postponed_to)
                   for vehicle in problem.vehicles)


def classify(problem: Problem, open_ids: Sequence[str],
             postponed_to: int) -> Classification:
    """Split the open work into must-go and deferrable. AC-3.1."""
    must, defer = [], []
    for order_id in open_ids:
        target = must if must_go(problem, problem.order(order_id),
                                 postponed_to) else defer
        target.append(order_id)
    return Classification(must_go=tuple(must), deferrable=tuple(defer))


def decide(problem: Problem, open_ids: Sequence[str], postponed_to: int,
           policy: Policy) -> Dispatch:
    """One epoch's dispatch decision, with AC-3.1 enforced. FR-22, DYN-1.

    Args:
        problem: the instance.
        open_ids: the requests known and not yet dispatched.
        postponed_to: when postponed work would wait until.
        policy: chooses the dispatch set from the open work and its
            classification. §8.2's policies are T-52; this takes whichever.

    Returns:
        A partition of `open_ids` into dispatched and postponed, plus whatever
        must-go work the policy tried to postpone and was overruled on.

    Raises:
        ValueError: if the policy names an order that is not open. Dropping it
            silently would make the partition quietly false.
    """
    split = classify(problem, open_ids, postponed_to)
    chosen = list(dict.fromkeys(policy(open_ids, split)))

    unknown = [order_id for order_id in chosen if order_id not in set(open_ids)]
    if unknown:
        raise ValueError(f"policy dispatched orders that are not open: "
                         f"{unknown}")

    forced = [order_id for order_id in split.must_go if order_id not in chosen]
    dispatched = [order_id for order_id in open_ids
                  if order_id in chosen or order_id in forced]
    postponed = [order_id for order_id in open_ids if order_id not in dispatched]
    return Dispatch(dispatched=tuple(dispatched), postponed=tuple(postponed),
                    forced=tuple(forced))
