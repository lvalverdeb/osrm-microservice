"""E-01 (T-02) — domain model types with exhaustive validation.

SDD §4.1/§4.2. Times and quantities are integers throughout: floating-point
seconds accumulate error along a route, and an arrival time that is out by a
microsecond makes INV-4 unfalsifiable.
"""

from __future__ import annotations

import pytest

from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    ValidationError,
    Vehicle,
)


def window(start: int, end: int, hardness: str = "HARD") -> TimeWindow:
    return TimeWindow(start=start, end=end, hardness=hardness)


def test_time_window_rejects_an_inverted_range():
    with pytest.raises(ValidationError) as excinfo:
        window(600, 300)
    assert "end" in str(excinfo.value)


def test_time_window_rejects_non_integer_instants():
    with pytest.raises(ValidationError):
        TimeWindow(start=0.5, end=600, hardness="HARD")


def test_soft_window_requires_penalties_and_hard_window_forbids_them():
    soft = TimeWindow(start=0, end=600, hardness="SOFT",
                      earliness_cost_per_sec=1, lateness_cost_per_sec=2)
    assert soft.hardness == "SOFT"
    with pytest.raises(ValidationError):
        TimeWindow(start=0, end=600, hardness="HARD", lateness_cost_per_sec=2)


def test_location_requires_a_matrix_index():
    with pytest.raises(ValidationError):
        Location(id="L1", lat=9.9, lon=-84.0, matrix_index=-1)


def test_job_needs_exactly_one_of_pickup_or_delivery():
    stop = StopSpec(location_id="L1", time_windows=(window(0, 3600),), service_fixed=300)
    Order(id="O1", kind="JOB", delivery=stop, quantities={"weight": 5})
    with pytest.raises(ValidationError):
        Order(id="O2", kind="JOB", quantities={"weight": 5})


def test_shipment_needs_both_ends():
    stop = StopSpec(location_id="L1", time_windows=(window(0, 3600),), service_fixed=60)
    with pytest.raises(ValidationError):
        Order(id="O3", kind="SHIPMENT", pickup=stop, quantities={"weight": 1})


def test_disjoint_windows_must_be_sorted_and_non_overlapping():
    with pytest.raises(ValidationError):
        StopSpec(location_id="L1",
                 time_windows=(window(600, 1200), window(0, 900)),
                 service_fixed=60)


def test_quantities_must_be_integers():
    stop = StopSpec(location_id="L1", time_windows=(window(0, 3600),), service_fixed=60)
    with pytest.raises(ValidationError):
        Order(id="O4", kind="JOB", delivery=stop, quantities={"weight": 2.5})


def test_matrix_must_be_square_and_cover_every_location():
    with pytest.raises(ValidationError):
        TravelMatrix(version="v1", durations=((0, 10), (10, 0)), distances=((0, 100),))


def test_problem_rejects_an_order_referencing_an_unknown_location():
    stop = StopSpec(location_id="NOPE", time_windows=(window(0, 3600),), service_fixed=60)
    with pytest.raises(ValidationError) as excinfo:
        Problem(
            id="P1",
            locations=(Location(id="L1", lat=9.9, lon=-84.0, matrix_index=0),),
            orders=(Order(id="O1", kind="JOB", delivery=stop, quantities={"weight": 1}),),
            vehicles=(Vehicle(id="V1", capacities={"weight": 100},
                              shift=window(0, 28800), start_location_id="L1"),),
            matrix=TravelMatrix(version="v1", durations=((0,),), distances=((0,),)),
        )
    assert "NOPE" in str(excinfo.value)


def test_problem_round_trips_through_plain_dicts():
    """The model must survive JSON, because that is how a problem arrives."""
    stop = StopSpec(location_id="L1", time_windows=(window(0, 3600),), service_fixed=60)
    problem = Problem(
        id="P1",
        locations=(Location(id="L1", lat=9.9, lon=-84.0, matrix_index=0),),
        orders=(Order(id="O1", kind="JOB", delivery=stop, quantities={"weight": 1}),),
        vehicles=(Vehicle(id="V1", capacities={"weight": 100},
                          shift=window(0, 28800), start_location_id="L1"),),
        matrix=TravelMatrix(version="v1", durations=((0,),), distances=((0,),)),
    )
    assert Problem.from_dict(problem.to_dict()) == problem
