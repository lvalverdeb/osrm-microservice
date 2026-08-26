"""E-02 (T-03) — canonical evaluator: recompute objective and timeline.

SDD §5, INV-9. This is the ground truth a solver's incremental move evaluator
is checked against; the SDD calls objective drift between the two the source of
most silent optimisation bugs.
"""

from __future__ import annotations

from vrp.evaluator import ObjectiveWeights, build_timeline, evaluate
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)


def problem() -> Problem:
    """Depot at index 0, two customers at 1 and 2, on a straight line.

    Travel: depot->A 300s/5000m, A->B 600s/9000m, B->depot 900s/14000m.
    """
    locations = (
        Location(id="D", lat=9.94, lon=-84.05, matrix_index=0),
        Location(id="A", lat=9.95, lon=-84.06, matrix_index=1),
        Location(id="B", lat=9.96, lon=-84.07, matrix_index=2),
    )
    durations = ((0, 300, 800), (300, 0, 600), (900, 600, 0))
    distances = ((0, 5000, 12000), (5000, 0, 9000), (14000, 9000, 0))
    orders = (
        Order(id="OA", kind="JOB", quantities={"weight": 10},
              delivery=StopSpec(location_id="A",
                                time_windows=(TimeWindow(start=0, end=100000),),
                                service_fixed=120)),
        Order(id="OB", kind="JOB", quantities={"weight": 4},
              delivery=StopSpec(location_id="B",
                                time_windows=(TimeWindow(start=3000, end=100000),),
                                service_fixed=60)),
    )
    vehicles = (Vehicle(id="V1", capacities={"weight": 50},
                        shift=TimeWindow(start=0, end=86400),
                        start_location_id="D", end_location_id="D"),)
    return Problem(id="P", locations=locations, orders=orders, vehicles=vehicles,
                   matrix=TravelMatrix(version="m1", durations=durations,
                                       distances=distances))


def test_timeline_is_arithmetic_not_estimation():
    """Hand-worked: every instant below is computed on paper, not from code."""
    timeline = build_timeline(problem(), "V1", ["OA", "OB"], start_time=0)
    depart_depot, a, b, end = timeline

    assert (depart_depot.arrival, depart_depot.departure) == (0, 0)
    # depot -> A is 300s; window opens at 0 so no waiting; 120s of service.
    assert (a.arrival, a.start_service, a.departure) == (300, 300, 420)
    # A -> B is 600s, arriving 1020. B's window opens at 3000, so the vehicle
    # waits 1980s before starting its 60s of service.
    assert (b.arrival, b.start_service, b.departure) == (1020, 3000, 3060)
    assert b.waiting == 1980
    # B -> depot is 900s.
    assert end.arrival == 3960


def test_load_is_tracked_per_dimension_along_the_route():
    timeline = build_timeline(problem(), "V1", ["OA", "OB"], start_time=0)
    # A delivery-only job starts loaded and sheds as it goes: 14 on board at
    # the depot, 4 after A, 0 after B.
    assert timeline[0].load_after == {"weight": 14}
    assert timeline[1].load_after == {"weight": 4}
    assert timeline[2].load_after == {"weight": 0}


def test_objective_components_are_reported_separately():
    weights = ObjectiveWeights(per_metre=1, per_second=0, per_vehicle=10000)
    result = evaluate(problem(), {"V1": ["OA", "OB"]}, weights=weights)
    # 5000 + 9000 + 14000 metres, one vehicle deployed.
    assert result.breakdown["distance"] == 28000
    assert result.breakdown["vehicles"] == 10000
    assert result.total == 38000


def test_waiting_is_not_charged_as_driving_time():
    """Waiting is real elapsed time but not work; conflating them overstates cost."""
    weights = ObjectiveWeights(per_metre=0, per_second=1, per_vehicle=0)
    result = evaluate(problem(), {"V1": ["OA", "OB"]}, weights=weights)
    assert result.breakdown["driving_seconds"] == 300 + 600 + 900
    assert result.breakdown["waiting_seconds"] == 1980
    assert result.breakdown["service_seconds"] == 120 + 60


def test_unassigned_orders_are_charged_their_prize():
    weights = ObjectiveWeights(per_metre=1, per_second=0, per_vehicle=0)
    served = evaluate(problem(), {"V1": ["OA", "OB"]}, weights=weights)
    dropped = evaluate(problem(), {"V1": ["OA"]}, weights=weights)
    assert "unassigned_penalty" in dropped.breakdown
    assert dropped.breakdown["unassigned_penalty"] > 0
    assert served.breakdown.get("unassigned_penalty", 0) == 0


def test_evaluation_is_deterministic():
    weights = ObjectiveWeights(per_metre=1, per_second=1, per_vehicle=1000)
    runs = {evaluate(problem(), {"V1": ["OA", "OB"]}, weights=weights).total
            for _ in range(50)}
    assert len(runs) == 1
