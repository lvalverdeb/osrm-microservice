"""The zone-sequence prior — §12.4 step 2, T-64.

§12.4's "Act" list is in priority order, and this is step 2. Step 1 is to
extract the deviation into an explicit model feature, "always preferable -- it
is explainable and auditable". Only "where the pattern resists formalisation"
does §12.4 reach for learning: "learn a **sequencing prior** at the zone level
and add it as a soft objective or a warm-start structure. Zone-sequence learning
from historical routes is the approach that performed best in the Amazon
challenge, where a probabilistic model of zone ordering learned from drivers
outperformed hand-coded zone constraints."

Step 3 is the one worth quoting anyway: "Never simply penalise drivers into
compliance with a plan the model got wrong."

**The guardrail decides the design.** §12.4: "Learned components MUST be
advisory: they may bias search and warm starts, they MUST NOT be able to produce
a plan that violates a hard constraint. The verifier (§11.2) is downstream of
all learning."

So this returns an *ordering* and never a constraint. It is allowed to be wrong
-- learned from twenty days it may well be -- and the arrangement that makes
that safe is that nothing it produces skips the verifier. There is deliberately
no way to express the prior as a lock or a penalty the search cannot overrule.

**Zones, not stops.** A prior over individual stops memorises last month's
customers and is worthless the day one moves. §12.4 says "at the zone level" for
that reason, and the Amazon result it cites is specifically about zone ordering.

Placement: **Python**, per criterion 2. It reads executed routes and produces a
warm-start ordering; it changes whenever either does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from vrp.adherence import ExecutedRoute
from vrp.model import Problem

FULL = 1000


@dataclass(frozen=True)
class ZonePrior:
    """A learned zone ordering, and how much the drivers agreed.

    `confidence` is parts per thousand of the observed zone transitions that
    the chosen ordering explains. It is reported rather than acted on here: a
    caller warm-starting a search may want to ignore a prior fitted on drivers
    who did something different every day, and that judgement is theirs.
    """

    sequence: tuple[str, ...]
    confidence: int


def zone_of(problem: Problem, order_id: str,
            zones: Mapping[str, str]) -> str:
    """Which zone an order's stop belongs to.

    A stop nobody has zoned gets its own zone named after the location. It is
    not in every zone and it is not in a null one: giving it its own keeps it
    out of everybody else's statistics rather than quietly joining a bucket it
    does not belong to.
    """
    order = problem.order(order_id)
    stop = order.delivery or order.pickup
    return zones.get(stop.location_id, stop.location_id)


def learn_prior(problem: Problem, history: Sequence[ExecutedRoute],
                zones: Mapping[str, str]) -> ZonePrior:
    """Learn the order drivers actually visit zones in. §12.4 step 2.

    Args:
        problem: the instance, for mapping orders to stops.
        history: executed routes, from T-61's telematics.
        zones: location id to zone name.

    Returns:
        The ordering and its confidence. An empty history gives an empty prior
        rather than a guess -- a prior fitted on nothing that returned some
        ordering anyway would be indistinguishable from a learned one.

    Raises:
        ValueError: if the history names an order the instance does not have.

    Counts observed zone-to-zone transitions and orders the zones so that as
    many as possible run forwards. Drivers disagree, so the majority wins and
    the confidence says how close it was; a prior that refused to commit when
    they differ would be no prior at all.
    """
    known = {order.id for order in problem.orders}
    transitions: dict[tuple[str, str], int] = {}
    seen: set[str] = set()

    for route in history:
        for order_id in route.sequence:
            if order_id not in known:
                raise ValueError(
                    f"history names order {order_id!r}, which is not in "
                    f"problem {problem.id!r}")
        visited = []
        for order_id in route.sequence:
            zone = zone_of(problem, order_id, zones)
            if not visited or visited[-1] != zone:
                visited.append(zone)
        seen.update(visited)
        for earlier, later in pairwise(visited):
            transitions[(earlier, later)] = transitions.get(
                (earlier, later), 0) + 1

    if not transitions:
        return ZonePrior(sequence=(), confidence=0)

    ordering = _rank(seen, transitions)
    forwards = sum(count for (a, b), count in transitions.items()
                   if ordering.index(a) < ordering.index(b))
    total = sum(transitions.values())
    return ZonePrior(sequence=tuple(ordering),
                     confidence=forwards * FULL // total)


def _rank(zones: set[str], transitions: Mapping[tuple[str, str], int]
          ) -> list[str]:
    """Order zones so that most observed transitions run forwards.

    A zone that drivers usually leave *for* others belongs early; one they
    usually arrive at belongs late. Sorting on that difference is a cheap
    approximation to the feedback-arc-set problem, which is NP-hard and not
    worth solving exactly for an advisory hint. Ties break on the name so the
    result is reproducible (CON-4).
    """
    def score(zone: str) -> tuple[int, str]:
        out = sum(count for (a, _), count in transitions.items() if a == zone)
        into = sum(count for (_, b), count in transitions.items() if b == zone)
        return (into - out, zone)

    return sorted(zones, key=score)


def order_by_prior(problem: Problem, order_ids: Sequence[str],
                   prior: ZonePrior, zones: Mapping[str, str]) -> list[str]:
    """Reorder a route to follow the prior. A warm start, not a constraint.

    Args:
        problem: the instance.
        order_ids: the route as it stands.
        prior: the learned ordering.
        zones: location id to zone name.

    Returns:
        The same orders, grouped by zone in the prior's order. Never fewer and
        never more -- §12.4's guardrail is about hard constraints, and the
        cheapest way to violate one is to quietly lose the order that carried
        it.

    Stops inside a zone keep their relative order: the prior is about zones,
    and rearranging within one would be inventing guidance it never learned. A
    zone the prior never saw goes last rather than being dropped, because a new
    zone is not evidence about anything and losing its stops would turn an
    advisory ordering into a way to lose deliveries.

    What comes back is an ordering. It may be a bad one -- a prior learned from
    twenty days can be -- and it is checked by the verifier like any other, which
    is exactly the arrangement §12.4 requires: "The verifier (§11.2) is
    downstream of all learning."
    """
    if not prior.sequence:
        return list(order_ids)

    rank = {zone: position for position, zone in enumerate(prior.sequence)}
    unseen = len(rank)
    return sorted(order_ids,
                  key=lambda order_id: rank.get(
                      zone_of(problem, order_id, zones), unseen))
