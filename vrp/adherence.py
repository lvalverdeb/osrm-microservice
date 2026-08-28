"""Telematics ingestion and plan adherence — CON-6, §12.4, T-61.

CON-6: "Trust the plan only as far as it survives contact with reality. Plan
quality MUST be measured against executed reality (GPS/telematics), not against
the solver's own objective."

§12.4 says how: "For each executed route, compute a sequence-dissimilarity score
between the planned stop sequence and the actual sequence, plus the
realised-cost delta. Aggregate by depot, driver, territory, and time of day."

And then the sentence that decides what the metric is *for*: "Systematic,
repeated deviation is a **model defect**, not driver misbehaviour. Experienced
drivers hold tacit knowledge about roads that are hard to navigate, when traffic
is bad, where parking is findable, and which stops are conveniently served
together -- information that is hard or impossible to formalise in an
optimisation model, which is exactly why drivers deviate from planned
sequences."

An adherence number used to rank drivers is a stick. The same number aggregated
by territory and read as "this zone is modelled wrong" is a diagnosis, and
§12.4's "Act" list puts extracting the deviation into an explicit model feature
first -- "always preferable, it is explainable and auditable". So nothing here
grades a route. There is no `compliant` field, and the absence is deliberate:
the moment one exists, somebody builds a leaderboard from it.

**Two silent failures this metric invites**, both refused rather than smoothed
over. A van whose tracker was off did not drive a perfect route -- it produced
no evidence, so it is absent from the results rather than counted as adherent. A
record naming an order nobody planned is a data fault, not a zero: ingesting it
quietly would make adherence look better than it was.

Placement: **Python**, per criterion 2. It reads plans and executed traces; it
changes whenever the domain model does.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from vrp.model import Problem, Solution

# Dissimilarity in parts per thousand, per CON-4: this project does not
# accumulate floats, and an adherence figure that cannot be reproduced exactly
# is one nobody can argue with a depot about.
FULL = 1000

DIMENSIONS = ("driver_id", "depot_id", "territory")


@dataclass(frozen=True)
class ExecutedRoute:
    """What a vehicle actually did, from telematics. §12.4."""

    vehicle_id: str
    driver_id: str
    depot_id: str
    territory: str
    sequence: tuple[str, ...]
    arrivals: dict[str, int]


@dataclass(frozen=True)
class Adherence:
    """One route, planned against executed. §12.4's per-route measure."""

    vehicle_id: str
    driver_id: str
    depot_id: str
    territory: str
    dissimilarity: int
    planned_cost: int
    realised_cost: int

    @property
    def cost_delta(self) -> int:
        """Negative means the driver's version was cheaper -- which happens,
        and is the finding §12.4 cares most about."""
        return self.realised_cost - self.planned_cost


@dataclass(frozen=True)
class Group:
    """One slice of the dashboard. §12.4's "aggregate by"."""

    key: str
    routes: int
    mean_dissimilarity: int
    mean_cost_delta: int


def dissimilarity(planned: Sequence[str], executed: Sequence[str]) -> int:
    """How differently the route was driven, 0 to 1000. §12.4.

    Counts the adjacent pairs the two sequences share: a route driven as
    planned keeps every pair, a reversed one keeps none. Pairs rather than
    positions because a driver who does the whole route one stop later has not
    rearranged anything, and a positional measure would call that a total
    deviation.

    Stops in one sequence and not the other count against the score. A stop
    that never happened is the largest deviation there is, and a measure
    comparing only what both have would score it zero.
    """
    if not planned and not executed:
        return 0

    missing = set(planned) ^ set(executed)
    shared_pairs = set(pairwise(planned)) & set(pairwise(executed))
    possible = max(len(planned) - 1, 0)

    kept = shared_pairs
    if possible == 0:
        similarity = FULL if not missing else 0
    else:
        similarity = len(kept) * FULL // possible

    # Every stop only one side saw pushes the score towards total divergence.
    penalty = len(missing) * FULL // max(len(set(planned) | set(executed)), 1)
    return min(FULL, max(0, FULL - similarity + penalty))


def ingest(problem: Problem,
           records: Iterable[Mapping]) -> tuple[ExecutedRoute, ...]:
    """Turn raw telematics into executed routes. §12.4's input side.

    Raises:
        ValueError: if a record is missing a field §12.4 aggregates by, or
            names an order that is not in the instance. Both are data faults,
            and a fault absorbed silently shows up later as an adherence figure
            nobody can explain.
    """
    known = {order.id for order in problem.orders}
    executed = []
    for record in records:
        for field in ("vehicle_id", "driver_id", "depot_id", "territory"):
            if not record.get(field):
                raise ValueError(f"telematics record is missing {field}: "
                                 f"{dict(record)}")
        stops = list(record.get("stops", ()))
        for stop in stops:
            if stop["order_id"] not in known:
                raise ValueError(
                    f"telematics names order {stop['order_id']!r}, which is "
                    f"not in problem {problem.id!r}")
        executed.append(ExecutedRoute(
            vehicle_id=record["vehicle_id"], driver_id=record["driver_id"],
            depot_id=record["depot_id"], territory=record["territory"],
            sequence=tuple(stop["order_id"] for stop in stops),
            arrivals={stop["order_id"]: stop["arrival"] for stop in stops}))
    return tuple(executed)


def adherence(problem: Problem, planned: Solution,
              executed: Sequence[ExecutedRoute]) -> list[Adherence]:
    """Every executed route against what was planned for it. CON-6.

    Routes with no telematics are absent from the result rather than scored.
    A van whose tracker was off did not drive a perfect route; it produced no
    evidence, and counting it as adherent would make a broken fleet look
    obedient.
    """
    by_vehicle = {route.vehicle_id: [step.order_id for step in route.steps
                                     if step.order_id]
                  for route in planned.routes}
    rows = []
    for actual in executed:
        intended = by_vehicle.get(actual.vehicle_id, [])
        rows.append(Adherence(
            vehicle_id=actual.vehicle_id, driver_id=actual.driver_id,
            depot_id=actual.depot_id, territory=actual.territory,
            dissimilarity=dissimilarity(intended, actual.sequence),
            planned_cost=_length(problem, actual.vehicle_id, intended),
            realised_cost=_length(problem, actual.vehicle_id,
                                  list(actual.sequence))))
    return rows


def _length(problem: Problem, vehicle_id: str, order_ids: list[str]) -> int:
    """What driving this sequence costs, recomputed from the matrix.

    §11.4 calls plan adherence "the metric that tells you whether the model is
    right", so it cannot be measured with the solver's own figures -- that is
    CON-6's point, one layer down.
    """
    if not order_ids:
        return 0
    vehicle = problem.vehicle(vehicle_id)
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    nodes = [index[vehicle.start_location_id]]
    for order_id in order_ids:
        order = problem.order(order_id)
        stop = order.delivery or order.pickup
        nodes.append(index[stop.location_id])
    nodes.append(index[vehicle.end_location_id or vehicle.start_location_id])
    return sum(problem.matrix.distance(a, b) for a, b in pairwise(nodes))


def aggregate(rows: Sequence[Adherence], by: str) -> dict[str, Group]:
    """The dashboard §12.4 asks for, sliced one way.

    Args:
        rows: per-route adherence.
        by: "driver_id", "depot_id" or "territory".

    Returns:
        One `Group` per key, each reporting how many routes it rests on --
        because one observation and thirty are not the same claim, and a mean
        without a count lets a single Tuesday condemn a territory.

    Raises:
        ValueError: on any other dimension. §12.4 names these three (and time
            of day, which needs a timestamp this ingestion does not yet carry);
            inventing a fourth would produce a slice nobody specified.
    """
    if by not in DIMENSIONS:
        raise ValueError(f"unknown aggregation dimension {by!r}; §12.4 names "
                         f"{', '.join(DIMENSIONS)}")

    buckets: dict[str, list[Adherence]] = {}
    for row in rows:
        buckets.setdefault(getattr(row, by), []).append(row)

    return {
        key: Group(
            key=key, routes=len(group),
            mean_dissimilarity=sum(r.dissimilarity for r in group) // len(group),
            mean_cost_delta=sum(r.cost_delta for r in group) // len(group))
        for key, group in sorted(buckets.items())}
