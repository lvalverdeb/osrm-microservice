"""Service time: fixed + per-unit + vehicle factor + dwell — FR-05, §6.2, T-24.

§6.2 makes the case in a paragraph headed "Critical engineering note": "Service
time is not a rounding error. In dense urban last-mile, travel time between
stops is a minority of the driver's day; parking and walking dominate... A model
with accurate matrices and guessed service times will be worse than one with
approximate matrices and calibrated service times."

Until E-24 the model had two of the four components. `service_fixed` and
`Location.dwell_overhead` existed; the per-unit term and the per-vehicle-type
factor did not, so a twenty-parcel drop cost the same as a one-parcel drop and a
tail-lift van unloaded as fast as two people with a trolley.

The composition order is a real decision and is tested rather than assumed. The
vehicle factor scales the *handling* — the part a tail lift or a second crew
member actually changes — and the dwell overhead is added afterwards, because
§6.2 defines it as "parking/walking overhead independent of the order" and a
tail lift does not make a parking space closer.

Arithmetic stays integer (CON-4 prohibits floating-point accumulation), so the
factor is in parts per thousand. 1000 means unchanged.
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
    Vehicle,
    service_time,
)

DAY = TimeWindow(start=0, end=12 * 3600)


def an_order(**stop_kwargs) -> Order:
    quantities = stop_kwargs.pop("quantities", {"parcels": 1})
    return Order(id="O1", kind="JOB", quantities=quantities,
                 delivery=StopSpec(location_id="C1", time_windows=(DAY,),
                                   **stop_kwargs))


def a_van(**kwargs) -> Vehicle:
    defaults = {"capacities": {"parcels": 100}, "shift": DAY,
                "start_location_id": "D", "end_location_id": "D"}
    return Vehicle(id="V1", **{**defaults, **kwargs})


def at(dwell: int = 0) -> Location:
    return Location(id="C1", lat=9.9, lon=-84.0, matrix_index=1,
                    dwell_overhead=dwell)


# --------------------------------------------------------------------------
# The four components
# --------------------------------------------------------------------------

def test_fixed_alone_is_the_old_behaviour():
    """Every existing caller passes only `service_fixed`, so the default of
    every new component must leave that untouched."""
    assert service_time(an_order(service_fixed=300), a_van(), at()) == 300


def test_per_unit_scales_with_the_quantity():
    """A twenty-parcel drop is not a one-parcel drop. Without this term the
    model charges the same for both, which is wrong in the direction that
    makes big drops look cheap."""
    order = an_order(service_fixed=120, service_per_unit=15,
                     quantities={"parcels": 20})
    assert service_time(order, a_van(), at()) == 120 + 15 * 20


def test_per_unit_uses_the_dimension_it_is_told_to():
    """A stop handling 400 kg of two parcels takes two parcels' worth of
    handling, not four hundred kilograms' worth."""
    order = an_order(service_fixed=60, service_per_unit=30,
                     service_per_unit_dimension="parcels",
                     quantities={"parcels": 2, "kg": 400})
    assert service_time(order, a_van(), at()) == 60 + 30 * 2


def test_the_vehicle_factor_scales_the_handling():
    """FR-05's per-vehicle-type component. A tail-lift van at 60% of manual
    handling, in parts per thousand because CON-4 forbids float accumulation."""
    order = an_order(service_fixed=100, service_per_unit=10,
                     quantities={"parcels": 10})
    manual = service_time(order, a_van(), at())
    assisted = service_time(order, a_van(service_factor_ppt=600), at())

    assert manual == 200
    assert assisted == 120, "600/1000 of 200"


def test_dwell_overhead_is_added_after_the_factor():
    """§6.2 calls dwell "parking/walking overhead independent of the order", so
    a tail lift does not make the parking space closer. Scaling it with the
    handling factor would be quietly claiming otherwise.
    """
    order = an_order(service_fixed=100, quantities={"parcels": 1})
    assisted = service_time(order, a_van(service_factor_ppt=500), at(dwell=90))

    assert assisted == 100 // 2 + 90, "the dwell is not halved"


def test_all_four_components_compose():
    order = an_order(service_fixed=120, service_per_unit=15,
                     quantities={"parcels": 8})
    result = service_time(order, a_van(service_factor_ppt=750), at(dwell=60))

    handling = 120 + 15 * 8                       # 240
    assert result == handling * 750 // 1000 + 60  # 180 + 60


# --------------------------------------------------------------------------
# Integer discipline (CON-4)
# --------------------------------------------------------------------------

def test_the_result_is_always_a_whole_number_of_seconds():
    """CON-4: integers in fixed units, no float accumulation. A factor that
    does not divide evenly must truncate deterministically rather than produce
    a float that two machines might round differently."""
    order = an_order(service_fixed=100, quantities={"parcels": 1})
    result = service_time(order, a_van(service_factor_ppt=333), at())

    assert isinstance(result, int)
    assert result == 33, "100 * 333 // 1000, truncated"


def test_truncation_is_deterministic_across_repeats():
    order = an_order(service_fixed=777, service_per_unit=13,
                     quantities={"parcels": 7})
    van = a_van(service_factor_ppt=317)
    results = {service_time(order, van, at(dwell=11)) for _ in range(50)}
    assert len(results) == 1


def test_a_negative_component_is_refused():
    """Negative service time is a stop that gives the driver time back, and a
    solver will happily route through it repeatedly."""
    with pytest.raises(Exception, match="service_per_unit"):
        StopSpec(location_id="C1", time_windows=(DAY,), service_per_unit=-1)
    with pytest.raises(Exception, match="service_factor_ppt"):
        a_van(service_factor_ppt=-1)


def test_a_zero_factor_is_refused():
    """A factor of zero is a vehicle that services every stop instantly, which
    is never what anybody means and is precisely what a solver would exploit."""
    with pytest.raises(Exception, match="service_factor_ppt"):
        a_van(service_factor_ppt=0)


# --------------------------------------------------------------------------
# It has to reach the plan
# --------------------------------------------------------------------------

def test_the_timeline_uses_the_composed_service_time():
    """The components are worthless if the evaluator still reads
    `service_fixed` directly -- the model would be richer and every plan
    identical."""
    from vrp.evaluator import build_timeline

    locations = (Location(id="D", lat=9.9, lon=-84.0, matrix_index=0), at(dwell=60))
    grid = ((0, 600), (600, 0))
    order = Order(id="O1", kind="JOB", quantities={"parcels": 10},
                  delivery=StopSpec(location_id="C1", time_windows=(DAY,),
                                    service_fixed=120, service_per_unit=15))
    problem = Problem(id="st", locations=locations, orders=(order,),
                      vehicles=(a_van(service_factor_ppt=500),),
                      matrix=TravelMatrix(version="v", durations=grid,
                                          distances=grid))

    timeline = build_timeline(problem, "V1", ["O1"])
    stop = next(step for step in timeline if step.order_id)
    served = stop.departure - stop.start_service

    assert served == (120 + 15 * 10) * 500 // 1000 + 60, served


def test_the_solver_is_told_the_composed_service_time():
    """And the adapter likewise, or the solver plans a day that does not exist."""
    from vrp.solve.pyvrp_adapter import solve
    from vrp.verify import verify

    locations = (Location(id="D", lat=9.9, lon=-84.0, matrix_index=0), at(dwell=60))
    grid = ((0, 600), (600, 0))
    order = Order(id="O1", kind="JOB", quantities={"parcels": 10},
                  delivery=StopSpec(location_id="C1", time_windows=(DAY,),
                                    service_fixed=120, service_per_unit=15))
    problem = Problem(id="st", locations=locations, orders=(order,),
                      vehicles=(a_van(service_factor_ppt=500),),
                      matrix=TravelMatrix(version="v", durations=grid,
                                          distances=grid))

    solution = solve(problem, iterations=100, seed=0)
    assert verify(problem, solution).ok, [
        str(v) for v in verify(problem, solution).violations]

    stop = next(s for r in solution.routes for s in r.steps if s.order_id)
    assert stop.departure - stop.start_service == (120 + 15 * 10) * 500 // 1000 + 60


def test_the_dataset_s_service_minutes_still_round_trip():
    """The nearest thing to §6.2's "telematics fixtures" this repository has.

    `docs/dataset_prep.md` generates `service_minutes` per delivery and every
    example converts it to `service_fixed`. Those examples must keep producing
    the same service time now that three more components exist, or E-24 has
    quietly changed every plan built before it.
    """
    for minutes in (5, 10, 20, 45):
        order = an_order(service_fixed=minutes * 60)
        assert service_time(order, a_van(), at()) == minutes * 60


def a_shipment(pickup_at: str = "C1", delivery_at: str = "C2") -> Order:
    """A collection of 8 minutes and a drop of 6 -- the corpus's own figures."""
    return Order(id="S1", kind="SHIPMENT", quantities={"parcels": 1},
                 pickup=StopSpec(location_id=pickup_at, time_windows=(DAY,),
                                 service_fixed=480),
                 delivery=StopSpec(location_id=delivery_at,
                                   time_windows=(DAY,), service_fixed=360))


def a_site(site_id: str) -> Location:
    return Location(id=site_id, lat=9.9, lon=-84.0, matrix_index=1)


def test_a_shipment_pickup_is_served_at_its_own_rate():
    """A collection and a drop are different work and take different times.

    §6.2 defines service against a *stop*, and a shipment has two of them.
    `service_time` resolved them as `delivery or pickup` and so charged every
    pickup at the delivery's rate; the verifier (INV-3), the evaluator, the HOS
    scheduler, `vrp.polish` and both adapters all inherited it. Invisible
    whenever the two are equal, which is what every other test here does.
    """
    order = a_shipment()
    assert service_time(order, a_van(), a_site("C1")) == 480
    assert service_time(order, a_van(), a_site("C2")) == 360


def test_a_shipment_serving_both_ends_at_one_site_keeps_the_delivery():
    """The one case a location cannot disambiguate, pinned rather than guessed.

    Collection and drop at the same location id are indistinguishable to
    `service_time`, which keeps the delivery -- the reading every caller had
    before either stop could be told from the other. Nothing here depends on
    it; it is written down so a change of mind is deliberate.
    """
    order = a_shipment(pickup_at="C1", delivery_at="C1")
    assert service_time(order, a_van(), a_site("C1")) == 360


def test_the_vehicle_factor_still_applies_to_a_pickup():
    """The fix picks the stop; it must not bypass the rest of FR-05."""
    order = a_shipment()
    van = a_van(service_factor_ppt=500)
    assert service_time(order, van, a_site("C1")) == 240
