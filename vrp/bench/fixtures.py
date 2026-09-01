"""Executable instances for catalogue scenarios — `CAT-VRP-003` §13.

§13.1 says "P0 scenarios become seeded synthetic fixtures first", and §13.2 that
the adversarial instances of §11 are "hand-built and tiny, in the fast tier, run
on every commit". This module is where those instances live, so that the same
instance a test asserts against is the one the coverage gate counts.

**One canonical instance per scenario.** Several scenarios are only interesting
against a control -- an exhausted driver against a rested one, a scarce fleet
against an ample one, a ring of stops on the antimeridian against the same ring
at longitude zero. The registry holds the instance the entry is *about*; the
control is built by the test that needs it, from the same helpers.

**Instances are built, never stored.** Following `vrp/bench/corpus.py`: a builder
plus fixed numbers reproduces the same problem every time, so the fixture set
reads as a few hundred lines rather than a directory of JSON nobody opens.

Placement: **Python**. Test infrastructure for the domain model, off the request
path entirely.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from vrp.model import (
    UNREACHABLE,
    Location,
    Lock,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)


# ------------------------------------------------------------------- helpers

def grid(size: int, *, leg: int = 600,
         cut: frozenset[tuple[int, int]] = frozenset()) -> TravelMatrix:
    """A uniform matrix, with `cut` pairs marked unreachable (MTX-5)."""
    rows = tuple(
        tuple(UNREACHABLE if (i, j) in cut or (j, i) in cut
              else (0 if i == j else abs(i - j) * leg)
              for j in range(size))
        for i in range(size))
    return TravelMatrix(version="fixture", durations=rows, distances=rows)


def sites(count: int) -> tuple[Location, ...]:
    """A depot and `count - 1` customers, evenly spaced north of San José."""
    return tuple(Location(id="D" if i == 0 else f"C{i}",
                          lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                 for i in range(count))


def drop(order_id: str, stop: str, *, windows: tuple[TimeWindow, ...] = (DAY,),
         service: int = 60, **quantities) -> Order:
    return Order(id=order_id, kind="JOB", quantities=quantities or {"kg": 1},
                 delivery=StopSpec(location_id=stop, time_windows=windows,
                                   service_fixed=service))


def collect(order_id: str, stop: str, *,
            windows: tuple[TimeWindow, ...] = (DAY,), **quantities) -> Order:
    return Order(id=order_id, kind="JOB", quantities=quantities,
                 pickup=StopSpec(location_id=stop, time_windows=windows,
                                 service_fixed=60))


def van(vehicle_id: str = "V1", **kwargs) -> Vehicle:
    defaults = {"capacities": {"kg": 100}, "shift": DAY,
                "start_location_id": "D", "end_location_id": "D"}
    return Vehicle(id=vehicle_id, **{**defaults, **kwargs})


def instance(problem_id: str, orders, vehicles, *, matrix=None,
             locks=(), locations=None) -> Problem:
    locations = locations or sites(len(orders) + 1)
    return Problem(id=problem_id, locations=locations, orders=tuple(orders),
                   vehicles=tuple(vehicles), locks=tuple(locks),
                   matrix=matrix or grid(len(locations)))


# ------------------------------------------------- §11 adversarial instances

def uc060_unliftable_order() -> Problem:
    """An order larger than every vehicle, beside one that is routable."""
    return instance("uc060", (drop("PALLET", "C1", kg=10_000),
                              drop("PARCEL", "C2", kg=1)),
                    (van(capacities={"kg": 100}),))


def uc061_unreachable_stop() -> Problem:
    """A stop the fleet cannot reach: the depot arc is an explicit sentinel."""
    return instance("uc061", (drop("ISLAND", "C1"),), (van(),),
                    matrix=grid(2, cut=frozenset({(0, 1)})))


def uc062_zero_width_window() -> Problem:
    """An appointment with no slack at all, which is legitimate."""
    noon = TimeWindow(start=6 * HOUR, end=6 * HOUR)
    return instance("uc062", (drop("APPT", "C1", windows=(noon,)),), (van(),))


def uc063_duty_across_midnight() -> Problem:
    """A night run whose second stop is due after 24:00 has elapsed."""
    shift = TimeWindow(start=20 * HOUR, end=30 * HOUR)
    evening = TimeWindow(start=20 * HOUR, end=22 * HOUR)
    small_hours = TimeWindow(start=25 * HOUR, end=26 * HOUR)
    return Problem(id="uc063", locations=sites(3),
                   orders=(drop("EVENING", "C1", windows=(evening,)),
                           drop("SMALLHOURS", "C2", windows=(small_hours,))),
                   vehicles=(van(shift=shift),), matrix=grid(3))


def uc064_four_hour_leg_under_eu561() -> Problem:
    """One four-hour leg, which a rested driver may run and a spent one may not."""
    long_day = TimeWindow(start=0, end=14 * HOUR)
    far = TravelMatrix(version="fixture", durations=((0, 4 * HOUR), (4 * HOUR, 0)),
                       distances=((0, 100_000), (100_000, 0)))
    return Problem(id="uc064", locations=sites(2),
                   orders=(drop("O1", "C1", windows=(long_day,)),),
                   vehicles=(van(shift=long_day, hos_rules="EU-561"),),
                   matrix=far)


def uc065_one_hour_for_everything(vehicles: int = 1) -> Problem:
    """Three stops an hour apart, all due inside the same hour."""
    hour = TimeWindow(start=8 * HOUR, end=9 * HOUR)
    return Problem(id=f"uc065-{vehicles}", locations=sites(4),
                   orders=tuple(drop(f"O{i}", f"C{i}", windows=(hour,))
                                for i in (1, 2, 3)),
                   vehicles=tuple(van(f"V{i}") for i in range(1, vehicles + 1)),
                   matrix=grid(4, leg=HOUR))


def uc066_peak_load_beyond_capacity(vehicles: int = 1) -> Problem:
    """Deliveries total 60kg and pickups 80kg in a 100kg van; the windows force
    the pickup first, so a shared route peaks at 140kg."""
    morning = TimeWindow(start=8 * HOUR, end=9 * HOUR)
    afternoon = TimeWindow(start=14 * HOUR, end=15 * HOUR)
    shift = TimeWindow(start=7 * HOUR, end=17 * HOUR)
    orders = (collect("COLLECT", "C2", windows=(morning,), kg=80),
              drop("DELIVER", "C1", windows=(afternoon,), kg=60))
    return instance(f"uc066-{vehicles}", orders,
                    tuple(van(f"V{i}", capacities={"kg": 100}, shift=shift)
                          for i in range(1, vehicles + 1)))


def uc067_incompatible_pair() -> Problem:
    """Foodstuff and a hazardous class: each legal alone, illegal together."""
    food = Order(id="FOOD", kind="JOB", quantities={"kg": 1},
                 delivery=StopSpec(location_id="C1", time_windows=(DAY,),
                                   service_fixed=60),
                 order_class="FOODSTUFF")
    hazard = Order(id="HAZ", kind="JOB", quantities={"kg": 1},
                   delivery=StopSpec(location_id="C2", time_windows=(DAY,),
                                     service_fixed=60),
                   order_class="HAZARDOUS",
                   incompatible_with=frozenset({"FOODSTUFF"}))
    return instance("uc067", (food, hazard), (van("V1"), van("V2")))


def uc068_contradictory_locks() -> Problem:
    """Two vehicles, so neither lock conflicts alone. Only the pair does."""
    locks = (Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1", vehicle_id="V1"),
             Lock(kind="FORBID_ORDER_ON_VEHICLE", order_id="O1", vehicle_id="V1"))
    return instance("uc068", (drop("O1", "C1"),), (van("V1"), van("V2")),
                    locks=locks)


def uc069_two_hundred_at_one_address() -> Problem:
    """A block delivery: every drop shares one geocode, so every arc is zero."""
    block = (Location(id="D", lat=9.9, lon=-84.0, matrix_index=0),
             Location(id="BLOCK", lat=9.91, lon=-84.0, matrix_index=1))
    return Problem(id="uc069", locations=block,
                   orders=tuple(drop(f"O{i}", "BLOCK", service=30, kg=1)
                                for i in range(200)),
                   vehicles=(van(capacities={"kg": 1_000}),), matrix=grid(2))


def uc070_single_order_single_vehicle() -> Problem:
    """The smallest thing that is still a routing problem."""
    return instance("uc070", (drop("O1", "C1"),), (van(),))


def uc071_no_vehicles_at_all() -> Problem:
    """A well-formed instance with an empty fleet, as a failed roster produces."""
    return instance("uc071", (drop("O1", "C1"), drop("O2", "C2")), ())


def uc073_ring_across_the_antimeridian(centre_lon: float = 180.0) -> Problem:
    """A ring of eight stops about a centre, which a sweep should cut into arcs.

    The default straddles longitude 180. Passing `centre_lon=0.0` builds the
    identical ring where no wrap exists, which is the control: the geography is
    the same, so the partition must be too.
    """
    count, lat, radius = 8, 10.0, 0.1
    points = [(lat + radius * math.sin(2 * math.pi * k / count),
               _wrap(centre_lon + radius * math.cos(2 * math.pi * k / count)))
              for k in range(count)]
    locations = ((Location(id="D", lat=lat, lon=_wrap(centre_lon),
                           matrix_index=0),)
                 + tuple(Location(id=f"C{k + 1}", lat=plat, lon=plon,
                                  matrix_index=k + 1)
                         for k, (plat, plon) in enumerate(points)))
    return Problem(id=f"uc073-{centre_lon:.0f}", locations=locations,
                   orders=tuple(drop(f"O{k + 1}", f"C{k + 1}", kg=1)
                                for k in range(count)),
                   vehicles=tuple(van(f"V{i}", capacities={"kg": 10})
                                  for i in (1, 2)),
                   matrix=grid(count + 1, leg=100))


def uc074_at_the_decomposition_threshold() -> Problem:
    """Sized so that solving whole and decomposing are both defined."""
    return Problem(id="uc074", locations=sites(25),
                   orders=tuple(drop(f"O{i}", f"C{i}", kg=5)
                                for i in range(1, 25)),
                   vehicles=tuple(van(f"V{i}", capacities={"kg": 60})
                                  for i in range(1, 5)),
                   matrix=grid(25, leg=300))


def _wrap(lon: float) -> float:
    return (lon + 180.0) % 360.0 - 180.0


# ------------------------------------------------------------- the registry

FIXTURES: dict[str, Callable[..., Problem]] = {
    "UC-060": uc060_unliftable_order,
    "UC-061": uc061_unreachable_stop,
    "UC-062": uc062_zero_width_window,
    "UC-063": uc063_duty_across_midnight,
    "UC-064": uc064_four_hour_leg_under_eu561,
    "UC-065": uc065_one_hour_for_everything,
    "UC-066": uc066_peak_load_beyond_capacity,
    "UC-067": uc067_incompatible_pair,
    "UC-068": uc068_contradictory_locks,
    "UC-069": uc069_two_hundred_at_one_address,
    "UC-070": uc070_single_order_single_vehicle,
    "UC-071": uc071_no_vehicles_at_all,
    "UC-073": uc073_ring_across_the_antimeridian,
    "UC-074": uc074_at_the_decomposition_threshold,
}

# Named rather than omitted, on the same principle as `diagnose.UNIMPLEMENTED`:
# a scenario with no fixture and a scenario with no *possible* fixture look
# identical from outside the registry, and only one of them is work to do.
NOT_AN_INSTANCE: dict[str, str] = {
    "UC-072": "its subject is the matrix build, not a routing instance: a "
              "provider that stops responding mid-build has no Problem to "
              "attach to. Exercised against the matrix layer instead.",
}
