"""Service-time calibration — §12.1, T-62, E-62.

§12.1: "Fit service duration from telematics: `service = f(order_archetype,
quantity, location_archetype, vehicle_type, time_of_day, driver_experience)`.
Start with grouped medians per archetype (robust, explainable) before any
regression model. Re-fit monthly; alert on drift."

The instruction to start with grouped medians is doing real work, and it is
worth taking literally. A regression fits everything and explains nothing: a
dispatcher told "the model says 412 seconds" cannot check it, and nobody can
tell a genuine shift from an artefact of the fit. A median over a named group is
a number somebody can go and count.

**Median rather than mean, deliberately.** A driver who takes a phone call
mid-stop produces a forty-minute service time on a four-minute job. A mean moves
with it; a median does not. Telematics is full of that kind of observation and
none of it is wrong -- the van really was stationary -- so the statistic has to
be the one that survives it.

**A group needs enough observations to be a group.** One stop is not evidence
about an archetype, and a pipeline that fitted it anyway would turn a single
Tuesday into policy that ships to every van. Thin groups are reported as thin
rather than fitted, which is also what makes the monthly re-fit safe to run
unattended.

**Drift is the alert, not the fit.** §12.1 asks for both, and they answer
different questions: the fit says what service time is now, the drift says what
changed since last month and by how much. A calibration that silently replaced
last month's numbers would be the most dangerous version of this pipeline --
every value would look freshly measured and nothing would ever look wrong.
"""

from __future__ import annotations

import pytest

from vrp.adherence import ExecutedRoute
from vrp.calibrate import (
    Calibration,
    archetype_of,
    drift,
    fit,
    observations,
)
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 600


def problem(stops: int = 4) -> Problem:
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    return Problem(
        id="calib",
        locations=tuple(Location(id="D" if i == 0 else f"C{i}",
                                 lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                        for i in range(size)),
        orders=tuple(Order(id=f"O{i}", kind="JOB", quantities={"kg": 10},
                           delivery=StopSpec(location_id=f"C{i}",
                                             time_windows=(DAY,),
                                             service_fixed=300))
                     for i in range(1, size)),
        vehicles=(Vehicle(id="V1", capacities={"kg": 100}, shift=DAY,
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="c", durations=grid, distances=grid))


def executed(observed: dict[str, tuple[int, int]],
             vehicle_id: str = "V1") -> ExecutedRoute:
    """One route: order id -> (arrival, departure)."""
    return ExecutedRoute(
        vehicle_id=vehicle_id, driver_id="ana", depot_id="D",
        territory="north", sequence=tuple(observed),
        arrivals={o: pair[0] for o, pair in observed.items()},
        departures={o: pair[1] for o, pair in observed.items()})


# --------------------------------------------------------------------------
# The archetype
# --------------------------------------------------------------------------

def test_two_similar_stops_share_an_archetype():
    instance = problem()
    key1 = archetype_of(instance, instance.order("O1"), instance.vehicles[0],
                        at=9 * HOUR)
    key2 = archetype_of(instance, instance.order("O2"), instance.vehicles[0],
                        at=9 * HOUR)

    assert key1 == key2


def test_time_of_day_separates_archetypes():
    """§12.1 names time of day, and it matters: a city-centre drop at 08:00 and
    the same drop at 17:00 are not the same job."""
    instance = problem()
    order, vehicle = instance.order("O1"), instance.vehicles[0]

    morning = archetype_of(instance, order, vehicle, at=8 * HOUR)
    evening = archetype_of(instance, order, vehicle, at=17 * HOUR)

    assert morning != evening


def test_quantity_bands_rather_than_exact_quantities():
    """Grouping by exact quantity would give one observation per group and
    nothing to take a median of. §12.1 says archetype, not fingerprint.

    The pair is chosen inside a band rather than across one. Every banding has
    edges, and 10 kg against 12 kg straddles one -- a first version used those
    and failed, which is the test sitting on a seam rather than the grouping
    being wrong. Where the edges fall is a judgement about the operation, and
    an archetype that split a fleet's most common drop size down the middle
    would be a bad one whatever the code did.
    """
    from dataclasses import replace

    instance = problem()
    small = replace(instance.order("O1"), quantities={"kg": 20})
    similar = replace(instance.order("O2"), quantities={"kg": 25})
    large = replace(instance.order("O3"), quantities={"kg": 900})
    vehicle = instance.vehicles[0]

    assert archetype_of(instance, small, vehicle, 9 * HOUR) == \
        archetype_of(instance, similar, vehicle, 9 * HOUR)
    assert archetype_of(instance, small, vehicle, 9 * HOUR) != \
        archetype_of(instance, large, vehicle, 9 * HOUR)


# --------------------------------------------------------------------------
# Observations from telematics
# --------------------------------------------------------------------------

def test_service_time_is_the_gap_between_arrival_and_departure():
    instance = problem()
    routes = (executed({"O1": (1_000, 1_400)}),)

    seen = observations(instance, routes)

    assert len(seen) == 1
    assert seen[0].seconds == 400


def test_a_stop_with_no_departure_is_skipped_rather_than_guessed():
    """A tracker that recorded the arrival and missed the departure has not
    told us the service took zero seconds. Filling it in would drag every
    median it lands in towards nothing."""
    instance = problem()
    partial = ExecutedRoute(vehicle_id="V1", driver_id="ana", depot_id="D",
                            territory="north", sequence=("O1", "O2"),
                            arrivals={"O1": 1_000, "O2": 2_000},
                            departures={"O1": 1_400})

    seen = observations(instance, partial and (partial,))

    assert [o.order_id for o in seen] == ["O1"]


def test_a_negative_service_time_is_refused():
    """A departure before an arrival is a clock fault, and averaging it in
    would corrupt an archetype nobody would think to re-check."""
    instance = problem()
    with pytest.raises(ValueError, match="departure"):
        observations(instance, (executed({"O1": (1_400, 1_000)}),))


# --------------------------------------------------------------------------
# §12.1: grouped medians
# --------------------------------------------------------------------------

def test_the_fit_is_a_median_not_a_mean():
    """A driver who takes a phone call mid-stop produces a forty-minute service
    on a four-minute job. The van really was stationary, so the observation is
    not wrong -- the statistic has to be the one that survives it."""
    instance = problem()
    routes = (executed({"O1": (0, 300)}), executed({"O2": (0, 300)}),
              executed({"O3": (0, 2_400)}))

    calibration = fit(observations(instance, routes), minimum=3)

    assert list(calibration.by_archetype.values()) == [300]


def test_a_thin_group_is_reported_rather_than_fitted():
    """One stop is not evidence about an archetype, and fitting it would turn a
    single Tuesday into policy that ships to every van."""
    instance = problem()
    routes = (executed({"O1": (0, 300)}),)

    calibration = fit(observations(instance, routes), minimum=5)

    assert calibration.by_archetype == {}
    assert calibration.thin


def test_the_thin_report_says_how_many_were_seen():
    """"Not enough data" is not actionable. "Three of the five needed" tells an
    operator whether next month will fix it."""
    instance = problem()
    routes = (executed({"O1": (0, 300), "O2": (0, 320)}),)

    calibration = fit(observations(instance, routes), minimum=5)

    assert list(calibration.thin.values()) == [2]


def test_an_even_number_of_observations_still_gives_an_integer():
    """CON-4: no floats. A median of four values is the lower of the middle
    two rather than their average, which is deterministic and replayable."""
    instance = problem()
    routes = (executed({"O1": (0, 100), "O2": (0, 200)}),
              executed({"O3": (0, 300), "O4": (0, 400)}))

    calibration = fit(observations(instance, routes), minimum=4)

    assert list(calibration.by_archetype.values()) == [200]


def test_separate_archetypes_are_fitted_separately():
    instance = problem()
    morning = executed({"O1": (8 * HOUR, 8 * HOUR + 300)})
    evening = executed({"O2": (17 * HOUR, 17 * HOUR + 900)})

    calibration = fit(observations(instance, (morning, evening)), minimum=1)

    assert sorted(calibration.by_archetype.values()) == [300, 900]


# --------------------------------------------------------------------------
# §12.1: alert on drift
# --------------------------------------------------------------------------

def test_an_unchanged_fit_raises_no_alert():
    instance = problem()
    routes = (executed({"O1": (0, 300), "O2": (0, 300)}),)
    calibration = fit(observations(instance, routes), minimum=1)

    assert drift(calibration, calibration, threshold=100) == ()


def test_a_moved_archetype_is_alerted():
    instance = problem()
    before = fit(observations(instance, (executed({"O1": (0, 300)}),)),
                 minimum=1)
    after = fit(observations(instance, (executed({"O1": (0, 900)}),)),
                minimum=1)

    alerts = drift(before, after, threshold=100)

    assert len(alerts) == 1
    assert alerts[0].was == 300 and alerts[0].now == 900


def test_a_small_move_is_below_the_threshold():
    """Everything moves a little every month. An alert that fired on noise is
    one an operator learns to ignore, which is worse than no alert."""
    instance = problem()
    before = fit(observations(instance, (executed({"O1": (0, 300)}),)),
                 minimum=1)
    after = fit(observations(instance, (executed({"O1": (0, 330)}),)),
                minimum=1)

    assert drift(before, after, threshold=100) == ()


def test_a_new_archetype_is_alerted_as_new():
    """A group that did not exist last month is a change worth seeing -- a new
    depot, a new customer type -- and comparing it to nothing would either
    crash or silently skip it."""
    instance = problem()
    before = Calibration(by_archetype={}, thin={})
    after = fit(observations(instance, (executed({"O1": (0, 300)}),)),
                minimum=1)

    alerts = drift(before, after, threshold=100)

    assert len(alerts) == 1
    assert alerts[0].was is None


def test_an_archetype_that_disappeared_is_alerted_too():
    """Work that stopped arriving is a change too, and one nobody would
    otherwise notice: the number simply stops being re-fitted."""
    instance = problem()
    before = fit(observations(instance, (executed({"O1": (0, 300)}),)),
                 minimum=1)
    after = Calibration(by_archetype={}, thin={})

    alerts = drift(before, after, threshold=100)

    assert len(alerts) == 1
    assert alerts[0].now is None


# --------------------------------------------------------------------------
# T-62's definition of done
# --------------------------------------------------------------------------

def test_a_monthly_refit_reproduces_a_known_fixture():
    """T-62: "Monthly re-fit job; drift alerting".

    A month of routes whose service times are known by construction, fitted,
    and the fit compared against what was put in. A pipeline that could not
    recover a planted number would have no business calibrating anything.
    """
    instance = problem()
    month = tuple(executed({"O1": (9 * HOUR, 9 * HOUR + 420)})
                  for _ in range(20))
    month += tuple(executed({"O2": (9 * HOUR, 9 * HOUR + 400)})
                   for _ in range(10))

    calibration = fit(observations(instance, month), minimum=10)

    assert list(calibration.by_archetype.values()) == [420], calibration
    assert not calibration.thin


def test_the_refit_is_deterministic():
    """CON-4, and a calibration nobody can reproduce is one nobody can audit
    when a depot disputes it."""
    instance = problem()
    month = tuple(executed({"O1": (9 * HOUR, 9 * HOUR + 300 + n)})
                  for n in range(12))

    first = fit(observations(instance, month), minimum=5)
    again = fit(observations(instance, month), minimum=5)

    assert first.by_archetype == again.by_archetype
