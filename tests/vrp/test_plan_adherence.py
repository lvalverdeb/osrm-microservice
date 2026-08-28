"""Telematics ingestion and plan adherence — CON-6, §12.4, T-61, E-61.

CON-6: "Trust the plan only as far as it survives contact with reality. Plan
quality MUST be measured against executed reality (GPS/telematics), not against
the solver's own objective."

§12.4 says how: "For each executed route, compute a sequence-dissimilarity score
between the planned stop sequence and the actual sequence, plus the
realised-cost delta. Aggregate by depot, driver, territory, and time of day."

And then the sentence the whole task turns on: "Systematic, repeated deviation
is a **model defect**, not driver misbehaviour. Experienced drivers hold tacit
knowledge about roads that are hard to navigate, when traffic is bad, where
parking is findable, and which stops are conveniently served together --
information that is hard or impossible to formalise in an optimisation model,
which is exactly why drivers deviate from planned sequences."

That reading changes what the metric is *for*. An adherence number used to rank
drivers is a stick; the same number aggregated by territory and read as "this
zone is modelled wrong" is a diagnosis. §12.4's "Act" list puts extracting the
deviation into an explicit model feature first, "always preferable -- it is
explainable and auditable", so the aggregation has to make a *repeated* pattern
visible and separable from one bad afternoon.

Two things this has to get right beyond the arithmetic:

**A deviation is not a failure.** A driver who reorders two stops and arrives
everywhere on time has improved the plan. The metric records the divergence and
declines to call it good or bad, because §12.4 says which of those it usually
is and it is not the one a naive reading assumes.

**Aggregation needs enough observations to mean anything.** One route deviating
is an anecdote. §12.4 asks for depot, driver and territory precisely so a
pattern can be told from a Tuesday.
"""

from __future__ import annotations

import pytest
from vrp.adherence import (
    ExecutedRoute,
    adherence,
    aggregate,
    dissimilarity,
    ingest,
)

from vrp.model import (
    Location,
    Order,
    Problem,
    Route,
    Solution,
    Step,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 600


def problem(stops: int = 6, vans: int = 2) -> Problem:
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    return Problem(
        id="adhere",
        locations=tuple(Location(id="D" if i == 0 else f"C{i}",
                                 lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                        for i in range(size)),
        orders=tuple(Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
                           delivery=StopSpec(location_id=f"C{i}",
                                             time_windows=(DAY,),
                                             service_fixed=60))
                     for i in range(1, size)),
        vehicles=tuple(Vehicle(id=f"V{n}", capacities={"kg": 100}, shift=DAY,
                               start_location_id="D", end_location_id="D",
                               cost_per_metre=1)
                       for n in range(1, vans + 1)),
        matrix=TravelMatrix(version="a", durations=grid, distances=grid))


def plan(instance: Problem, assignment: dict[str, list[str]]) -> Solution:
    index = {loc.id: loc.matrix_index for loc in instance.locations}
    routes = []
    for vehicle_id, order_ids in assignment.items():
        steps = [Step(type="START", location_id="D", arrival=0,
                      start_service=0, departure=0)]
        clock, here = 0, index["D"]
        for order_id in order_ids:
            stop = instance.order(order_id).delivery
            there = index[stop.location_id]
            clock += instance.matrix.duration(here, there)
            steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                              order_id=order_id, arrival=clock,
                              start_service=clock, departure=clock + 60))
            clock, here = clock + 60, there
        clock += instance.matrix.duration(here, index["D"])
        steps.append(Step(type="END", location_id="D", arrival=clock,
                          start_service=clock, departure=clock))
        routes.append(Route(vehicle_id=vehicle_id, steps=tuple(steps)))
    served = {o for ids in assignment.values() for o in ids}
    return Solution(
        problem_id=instance.id, routes=tuple(routes),
        unassigned=tuple({"order_id": o.id, "reason_code": "NOT_PLACED",
                          "explanation": "-"}
                         for o in instance.orders if o.id not in served),
        objective_breakdown={}, status="FEASIBLE")


ROUTES = {"V1": ["O1", "O2", "O3"], "V2": ["O4", "O5", "O6"]}


# --------------------------------------------------------------------------
# §12.4's sequence dissimilarity
# --------------------------------------------------------------------------

def test_a_route_driven_as_planned_scores_zero():
    assert dissimilarity(["O1", "O2", "O3"], ["O1", "O2", "O3"]) == 0


def test_a_reversed_route_scores_at_the_top():
    assert dissimilarity(["O1", "O2", "O3"], ["O3", "O2", "O1"]) == 1000


def test_one_swap_scores_between_the_two():
    score = dissimilarity(["O1", "O2", "O3", "O4"],
                          ["O2", "O1", "O3", "O4"])

    assert 0 < score < 1000, score


def test_a_bigger_rearrangement_scores_higher():
    """The property that makes it a *score* rather than a flag."""
    small = dissimilarity(["O1", "O2", "O3", "O4"],
                          ["O2", "O1", "O3", "O4"])
    large = dissimilarity(["O1", "O2", "O3", "O4"],
                          ["O4", "O3", "O2", "O1"])

    assert large > small


def test_a_stop_the_driver_skipped_counts_as_divergence():
    """A stop that never happened is the largest deviation there is, and a
    metric comparing only the stops both have would score it zero."""
    assert dissimilarity(["O1", "O2", "O3"], ["O1", "O2"]) > 0


def test_a_stop_the_driver_added_counts_too():
    assert dissimilarity(["O1", "O2"], ["O1", "O2", "O9"]) > 0


def test_an_empty_plan_and_an_empty_execution_agree():
    assert dissimilarity([], []) == 0


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------

def test_telematics_becomes_executed_routes():
    instance = problem()
    records = [
        {"vehicle_id": "V1", "driver_id": "ana", "depot_id": "D",
         "territory": "north", "stops": [
             {"order_id": "O1", "arrival": 600},
             {"order_id": "O2", "arrival": 1400}]},
    ]

    executed = ingest(instance, records)

    assert len(executed) == 1
    assert executed[0].driver_id == "ana"
    assert executed[0].sequence == ("O1", "O2")


def test_a_record_missing_its_driver_is_refused():
    """§12.4 aggregates by driver. A record without one cannot be aggregated
    and would quietly shrink every denominator it should have been in."""
    instance = problem()
    with pytest.raises(ValueError, match="driver_id"):
        ingest(instance, [{"vehicle_id": "V1", "depot_id": "D",
                           "territory": "north", "stops": []}])


def test_a_record_naming_an_unknown_order_is_refused():
    """Silently dropping it would make adherence look better than it was."""
    instance = problem()
    with pytest.raises(ValueError, match="O99"):
        ingest(instance, [{"vehicle_id": "V1", "driver_id": "ana",
                           "depot_id": "D", "territory": "north",
                           "stops": [{"order_id": "O99", "arrival": 1}]}])


# --------------------------------------------------------------------------
# CON-6: measured against reality, not against the objective
# --------------------------------------------------------------------------

def test_adherence_pairs_a_plan_with_what_happened():
    instance = problem()
    planned = plan(instance, ROUTES)
    executed = (ExecutedRoute(vehicle_id="V1", driver_id="ana", depot_id="D",
                              territory="north",
                              sequence=("O2", "O1", "O3"),
                              arrivals={"O2": 700, "O1": 1500, "O3": 2400}),)

    measured = adherence(instance, planned, executed)

    assert len(measured) == 1
    assert measured[0].vehicle_id == "V1"
    assert measured[0].dissimilarity > 0


def test_the_realised_cost_delta_is_reported():
    """§12.4 asks for "the realised-cost delta" beside the dissimilarity,
    because they answer different questions: one is how much the driver
    changed, the other is whether it was worth changing."""
    instance = problem()
    planned = plan(instance, ROUTES)
    executed = (ExecutedRoute(vehicle_id="V1", driver_id="ana", depot_id="D",
                              territory="north",
                              sequence=("O3", "O2", "O1"),
                              arrivals={"O3": 1800, "O2": 2500, "O1": 3200}),)

    measured = adherence(instance, planned, executed)[0]

    assert measured.planned_cost > 0
    assert measured.realised_cost > 0
    assert measured.cost_delta == measured.realised_cost - measured.planned_cost


def test_a_deviation_that_saved_money_is_not_called_a_failure():
    """§12.4: "Systematic, repeated deviation is a model defect, not driver
    misbehaviour."

    The metric records the divergence and declines to grade it. A driver who
    reorders two stops and gets everywhere on time has improved the plan, and a
    dashboard that flagged them would be teaching the wrong lesson.
    """
    instance = problem()
    planned = plan(instance, {"V1": ["O3", "O1", "O2"]})
    executed = (ExecutedRoute(vehicle_id="V1", driver_id="ana", depot_id="D",
                              territory="north",
                              sequence=("O1", "O2", "O3"),
                              arrivals={"O1": 600, "O2": 1300, "O3": 2000}),)

    measured = adherence(instance, planned, executed)[0]

    assert measured.dissimilarity > 0
    assert measured.cost_delta < 0
    assert not hasattr(measured, "compliant")


def test_a_vehicle_with_no_telematics_is_absent_rather_than_perfect():
    """The silent failure this metric invites. A van whose tracker was off did
    not drive a perfect route; it produced no evidence, and counting it as
    adherent would make a broken fleet look obedient."""
    instance = problem()
    planned = plan(instance, ROUTES)
    executed = (ExecutedRoute(vehicle_id="V1", driver_id="ana", depot_id="D",
                              territory="north", sequence=("O1", "O2", "O3"),
                              arrivals={"O1": 600, "O2": 1300, "O3": 2000}),)

    measured = adherence(instance, planned, executed)

    assert {row.vehicle_id for row in measured} == {"V1"}


# --------------------------------------------------------------------------
# §12.4's aggregation
# --------------------------------------------------------------------------

def _rows(instance, planned, deviations):
    executed = tuple(
        ExecutedRoute(vehicle_id="V1", driver_id=driver, depot_id=depot,
                      territory=territory, sequence=tuple(sequence),
                      arrivals={order_id: 600 * (n + 1)
                                for n, order_id in enumerate(sequence)})
        for driver, depot, territory, sequence in deviations)
    return adherence(instance, planned, executed)


def test_aggregation_by_each_dimension_12_4_names():
    instance = problem()
    planned = plan(instance, {"V1": ["O1", "O2", "O3"]})
    rows = _rows(instance, planned, [
        ("ana", "D", "north", ["O2", "O1", "O3"]),
        ("ben", "D", "south", ["O1", "O2", "O3"]),
        ("ana", "D", "north", ["O3", "O2", "O1"]),
    ])

    for dimension in ("driver_id", "depot_id", "territory"):
        summary = aggregate(rows, by=dimension)
        assert summary, dimension
        assert all(group.routes > 0 for group in summary.values())


def test_a_repeated_deviation_shows_up_as_a_pattern():
    """§12.4's whole purpose: "Systematic, repeated deviation is a model
    defect". One route deviating is a Tuesday. Three in the same territory is
    something to go and look at."""
    instance = problem()
    planned = plan(instance, {"V1": ["O1", "O2", "O3"]})
    rows = _rows(instance, planned, [
        ("ana", "D", "north", ["O3", "O2", "O1"]),
        ("ben", "D", "north", ["O3", "O2", "O1"]),
        ("cid", "D", "north", ["O3", "O2", "O1"]),
        ("dee", "D", "south", ["O1", "O2", "O3"]),
    ])

    by_territory = aggregate(rows, by="territory")

    assert by_territory["north"].mean_dissimilarity > \
        by_territory["south"].mean_dissimilarity
    assert by_territory["north"].routes == 3


def test_an_aggregate_reports_how_many_routes_it_rests_on():
    """One observation and thirty observations are not the same claim, and a
    dashboard that showed only the mean would let a single Tuesday condemn a
    territory."""
    instance = problem()
    planned = plan(instance, {"V1": ["O1", "O2", "O3"]})
    rows = _rows(instance, planned, [("ana", "D", "north", ["O2", "O1", "O3"])])

    assert aggregate(rows, by="territory")["north"].routes == 1


def test_aggregating_by_an_unknown_dimension_is_refused():
    instance = problem()
    planned = plan(instance, {"V1": ["O1", "O2", "O3"]})
    rows = _rows(instance, planned, [("ana", "D", "north", ["O1", "O2", "O3"])])

    with pytest.raises(ValueError, match="dimension"):
        aggregate(rows, by="astrological_sign")
