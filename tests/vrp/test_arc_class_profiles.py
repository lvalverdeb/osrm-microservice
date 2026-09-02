"""Per-arc-class speed profiles — §6.3, MTX-9, T-83.

§6.3 specifies "per-arc (or per-zone) piecewise-constant **speed** profiles";
§12.2 fits multipliers per arc class; and until this landed `Problem` carried
one profile for a whole instance. One profile says congestion slows a motorway
exactly as it slows a residential street, which is not true of any real
afternoon and is the arithmetic behind §6.3's own failure mode.

The task was filed blocked on the grounds that no instance had ever shown more
than one arc class. Measuring the fixtures said otherwise: sixteen of the
twenty-seven span two or three, most of those are majority `arterial` or
`trunk`, and the eleven single-class ones are the degenerate fixtures with two
to six arcs.
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
    profile_for_arc,
    travel_between,
)
from vrp.polish import _floor
from vrp.timedependent import ARC_CLASSES, SpeedProfile, arc_class_of

HOUR = 3600


def half_speed(hours: range | tuple[int, ...]) -> SpeedProfile:
    return SpeedProfile(
        bucket_seconds=HOUR,
        multipliers_ppt=tuple(500 if hour in hours else 1000
                              for hour in range(24)))


def a_mixed_network(profiles=None, profile=None) -> Problem:
    """Three stops: one a short hop away, one a long haul.

    The point of the instance is that `D->C1` is `local` and `D->C2` is
    `trunk`, so a single profile has to say the same thing about a side street
    and a motorway.
    """
    day = TimeWindow(start=0, end=20 * HOUR)
    locations = tuple(
        Location(id=site, lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
        for i, site in enumerate(("D", "C1", "C2")))
    # 120 s is local (<= 300), 3600 s is trunk (> 1200).
    grid = ((0, 120, 3600), (120, 0, 3600), (3600, 3600, 0))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}", service_fixed=60,
                                time_windows=(day,)))
        for i in (1, 2))
    return Problem(
        id="mixed", locations=locations, orders=orders,
        vehicles=(Vehicle(id="V1", capacities={"kg": 10},
                          shift=TimeWindow(start=7 * HOUR, end=20 * HOUR),
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="mixed", durations=grid, distances=grid),
        speed_profile=profile, speed_profiles=profiles)


def test_the_classifier_names_the_bands_the_calibration_fits():
    assert arc_class_of(120) == "local"
    assert arc_class_of(600) == "arterial"
    assert arc_class_of(3600) == "trunk"
    assert {name for _, name in ARC_CLASSES} | {"trunk"} == {
        "local", "arterial", "trunk"}


# --------------------------------------------------------------------------
# What the single profile could not say
# --------------------------------------------------------------------------

def test_two_classes_are_slowed_by_different_amounts_at_the_same_instant():
    """The whole point: rush hour on the ring road is not rush hour on a lane.

    The motorway crawls at eight in the morning while the side street is
    unaffected, which one profile per instance cannot express at all.
    """
    problem = a_mixed_network(profiles={
        "local": half_speed(()),            # never congested
        "arterial": half_speed((8,)),
        "trunk": half_speed(range(7, 11)),  # a long peak on the motorway
    })
    at_eight = 8 * HOUR

    assert travel_between(problem, 0, 1, at_eight) == 120, (
        "the short hop was slowed by a profile that says free flow all day")
    assert travel_between(problem, 0, 2, at_eight) > 3600, (
        "the motorway was not slowed, so the per-class lookup is not "
        "reaching the trunk profile")


def test_one_profile_for_the_instance_still_applies_to_every_arc():
    """The single-profile form is not deprecated: it is the honest shape when
    a fit has only ever seen one class, and every instance built before T-83
    uses it."""
    problem = a_mixed_network(profile=half_speed((8,)))
    at_eight = 8 * HOUR

    assert travel_between(problem, 0, 1, at_eight) > 120
    assert travel_between(problem, 0, 2, at_eight) > 3600


def test_an_instance_with_no_profile_at_all_is_untouched():
    problem = a_mixed_network()
    assert travel_between(problem, 0, 1, 8 * HOUR) == 120
    assert travel_between(problem, 0, 2, 8 * HOUR) == 3600
    assert profile_for_arc(problem, 120) is None


# --------------------------------------------------------------------------
# What it refuses rather than approximates
# --------------------------------------------------------------------------

def test_declaring_both_forms_is_refused():
    """One profile for everything and one per class are two answers to the
    same question, and a silent precedence rule would decide which arc got
    which without anybody choosing it."""
    with pytest.raises(Exception, match="both"):
        a_mixed_network(profile=half_speed((8,)),
                        profiles={"local": half_speed(())})


def test_profiles_that_miss_a_class_the_matrix_contains_are_refused():
    """CON-11 again: what cannot be said soundly is refused by name.

    Falling back to free flow for the unprofiled class is the dangerous
    version -- the motorway is silently the one arc nobody modelled, and the
    plan looks fully time-aware.
    """
    with pytest.raises(Exception, match="trunk"):
        a_mixed_network(profiles={"local": half_speed(()),
                                  "arterial": half_speed((8,))})


def test_an_empty_mapping_is_refused_rather_than_meaning_free_flow():
    with pytest.raises(Exception, match="empty"):
        a_mixed_network(profiles={})


# --------------------------------------------------------------------------
# The fit has somewhere to land
# --------------------------------------------------------------------------

def test_a_calibration_hands_back_a_mapping_the_model_accepts():
    """T-83's definition of done: `SpeedCalibration.profile` stops being the
    only way to apply a fit."""
    from vrp.speedfit import SpeedCalibration
    from vrp.timedependent import ArcKey

    calibration = SpeedCalibration(
        bucket_seconds=HOUR, buckets=24,
        by_key={ArcKey("local", 8): 900, ArcKey("trunk", 8): 400},
        thin={}, straddled=0)

    profiles = calibration.as_profiles()
    assert set(profiles) == {"local", "trunk"}
    assert profiles["trunk"].multipliers_ppt[8] == 400
    assert profiles["local"].multipliers_ppt[8] == 900
    # A bucket nobody drove in is free flow, as it is for a single profile.
    assert profiles["trunk"].multipliers_ppt[3] == 1000


# --------------------------------------------------------------------------
# §7.5's bound has to be the arc's own
# --------------------------------------------------------------------------

def test_the_lower_bound_stays_a_bound_when_classes_have_different_profiles():
    """§7.5's filter prunes on the fastest an arc can ever be.

    With one profile per instance the bound could be read off whichever
    profile happened to be there. Per class it has to be the arc's own: a road
    that is sometimes *faster* than the engine's free-flow guess has a lower
    bound below free flow, and pruning a different class against it over-states
    -- which discards legal sequences and leaves no trace that it did.
    """
    problem = a_mixed_network(profiles={
        # The side street beats the engine's guess at eight in the morning.
        "local": SpeedProfile(bucket_seconds=HOUR,
                              multipliers_ppt=tuple(2000 if h == 8 else 1000
                                                    for h in range(24))),
        "arterial": half_speed(()),
        "trunk": half_speed(()),
    })
    span = range(7 * HOUR, 20 * HOUR, 600)

    strictly_below = 0
    for origin, destination in ((0, 1), (0, 2), (1, 2)):
        bound = _floor(problem, origin, destination)
        for depart in span:
            exact = travel_between(problem, origin, destination, depart)
            assert bound <= exact, (
                f"arc {origin}->{destination} leaving at {depart}s costs "
                f"{exact}s and would be pruned on {bound}s")
            strictly_below += bound < exact

    assert strictly_below > 0, (
        "the bound never sits below an exact arc here, so it is not being "
        "exercised and the assertion above is a tautology")
