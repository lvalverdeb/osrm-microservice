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
import random
from collections.abc import Callable

from vrp.matrix import PlanarMatrix
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


# ----------------------------------------------- §13.1 P0 operational sizes

# §13.1: "one per P0 scenario, at three sizes". Small is the fast tier and runs
# on every commit; the other two exist so the same operation can be measured at
# a size where search actually has to work. Stop counts, not vehicle counts --
# the fleet follows from the work in each builder below.
SIZES: dict[str, int] = {"small": 12, "medium": 60, "large": 300}

# Three of the P0 operations are one driver's single tour -- a delivery-station
# round, a technician's day, the stops left after a missed one. For those, size
# cannot mean "more stops for the same driver": three hundred drops is not a
# large round, it is an impossible one, and a fixture that grows past a duty is
# measuring nothing but the verifier's ability to notice. What scales in that
# operation is the number of rounds, which is a different scenario. So they
# scale within a day instead, and say so.
TOUR_SIZES: dict[str, int] = {"small": 10, "medium": 18, "large": 28}

# And an appointment day is smaller still: a 45-minute install plus travel is
# eight or nine jobs, not thirty. Sizing this one from the round table produced
# a day no technician could work, which the verifier duly reported as a wall of
# INV-4s -- a fixture measuring nothing but its own impossibility.
DAY_SIZES: dict[str, int] = {"small": 5, "medium": 8, "large": 12}

KM_PER_DEGREE = 111.0


def stops_for(size: str, sizes: dict[str, int] | None = None) -> int:
    table = sizes if sizes is not None else SIZES
    try:
        return table[size]
    except KeyError:
        raise ValueError(f"unknown size {size!r}; have {sorted(table)}") from None


def scatter(count: int, seed: int, *, spread_km: float = 12.0,
            clusters: int = 1) -> tuple[tuple[float, float], ...]:
    """Deterministic customer coordinates in kilometres about a depot at 0,0.

    Clustered when asked, because a scattered instance and a clustered one make
    routing hard in different ways and a corpus of one shape measures one thing
    -- the same argument `vrp/bench/corpus.py` makes for its own specs.
    """
    rng = random.Random(seed)
    centres = [(rng.uniform(-spread_km, spread_km), rng.uniform(-spread_km, spread_km))
               for _ in range(clusters)]
    points = []
    for index in range(count):
        cx, cy = centres[index % clusters]
        tight = spread_km / (3.0 * clusters)
        points.append((round(cx + rng.uniform(-tight, tight), 3),
                       round(cy + rng.uniform(-tight, tight), 3)))
    return tuple(points)


def planar_sites(coordinates: tuple[tuple[float, float], ...], *,
                 depots: int = 1, **location_kwargs) -> tuple[Location, ...]:
    """Locations for a depot-then-customers coordinate list, ids `D`/`D2`.../`C1`...

    The coordinates are kilometres, which is what `PlanarMatrix` measures in;
    the latitude and longitude are derived from them so that anything reading
    geometry rather than the matrix -- the decomposition sweep, the zone prior
    -- sees the same layout the matrix does.
    """
    sites_out = []
    for index, (x, y) in enumerate(coordinates):
        if index < depots:
            site_id = "D" if index == 0 else f"D{index + 1}"
            extra = location_kwargs
        else:
            site_id = f"C{index - depots + 1}"
            extra = {}
        sites_out.append(Location(id=site_id, lat=round(9.9 + y / KM_PER_DEGREE, 6),
                                  lon=round(-84.0 + x / KM_PER_DEGREE, 6),
                                  matrix_index=index, **extra))
    return tuple(sites_out)


def planar_matrix(name: str,
                  coordinates: tuple[tuple[float, float], ...]) -> PlanarMatrix:
    return PlanarMatrix(version=f"{name}-v1", coordinates=coordinates)


# ------------------------------------------------- §13.1 P0 operational set

def uc075_delivery_station_sequencing(size: str = "small") -> Problem:
    """One van, one fixed set of stops, three streets. Only the order is open.

    The stops carry a street in their id suffix so a zone prior learned from
    executed rounds has something to be a prior *about*: §4.2's finding is that
    the executed sequence follows zone structure, not distance.
    """
    count = stops_for(size, TOUR_SIZES)
    coordinates = ((0.0, 0.0),) + scatter(count, seed=75, spread_km=6.0, clusters=3)
    return Problem(
        id=f"uc075-{size}", locations=planar_sites(coordinates),
        orders=tuple(drop(f"O{i}", f"C{i}", service=180, kg=1)
                     for i in range(1, count + 1)),
        vehicles=(van(capacities={"kg": count}, shift=TimeWindow(0, 10 * HOUR)),),
        matrix=planar_matrix(f"uc075-{size}", coordinates))


def uc077_single_technician_day(size: str = "small") -> Problem:
    """One engineer, appointment windows, and a response budget rather than a
    quality one: the useful behaviour is re-sequencing from the van."""
    count = stops_for(size, DAY_SIZES)
    coordinates = ((0.0, 0.0),) + scatter(count, seed=77, spread_km=8.0)
    shift = TimeWindow(start=8 * HOUR, end=17 * HOUR)
    orders = []
    for i in range(1, count + 1):
        opens = 8 * HOUR + (i * 9 * HOUR) // (count + 1)
        orders.append(drop(f"O{i}", f"C{i}", service=20 * 60,
                           windows=(TimeWindow(start=opens, end=opens + 2 * HOUR),),
                           kg=1))
    return Problem(id=f"uc077-{size}", locations=planar_sites(coordinates),
                   orders=tuple(orders),
                   vehicles=(van(capacities={"kg": count}, shift=shift),),
                   matrix=planar_matrix(f"uc077-{size}", coordinates))


def uc087_resequence_from_here(size: str = "small") -> Problem:
    """The remaining stops, from wherever the van is now.

    The vehicle starts at `C1` -- the stop it has just left -- and does not
    return to the depot, which is an open tour with a fixed origin rather than
    a fresh day.
    """
    count = stops_for(size, TOUR_SIZES)
    coordinates = ((0.0, 0.0),) + scatter(count, seed=87, spread_km=10.0)
    rest_of_shift = TimeWindow(start=11 * HOUR, end=18 * HOUR)
    return Problem(
        id=f"uc087-{size}", locations=planar_sites(coordinates),
        orders=tuple(drop(f"O{i}", f"C{i}", kg=1, windows=(rest_of_shift,))
                     for i in range(2, count + 1)),
        vehicles=(van(capacities={"kg": count}, start_location_id="C1",
                      end_location_id=None, open_route=True,
                      shift=TimeWindow(11 * HOUR, 18 * HOUR)),),
        matrix=planar_matrix(f"uc087-{size}", coordinates))


def uc013_waste_round_with_tips(size: str = "small") -> Problem:
    """A hopper that fills two or three times a shift, and a tip to empty it at.

    Capacity is deliberately a third of the round, so a single-trip model cannot
    express the day at all and a multi-trip one must reload.
    """
    count = stops_for(size)
    coordinates = ((0.0, 0.0),) + scatter(count, seed=13, spread_km=8.0, clusters=2)
    # A hopper holds a third of a round and empties three times a shift, so one
    # round is about twelve stops whatever the size. More work means more
    # rounds, not a bigger hopper.
    hopper, per_round = 4, 12
    rounds = max(1, -(-count // per_round))
    return Problem(
        id=f"uc013-{size}", locations=planar_sites(coordinates),
        orders=tuple(collect(f"O{i}", f"C{i}", m3=1) for i in range(1, count + 1)),
        vehicles=tuple(van(f"V{v}", capacities={"m3": hopper},
                           shift=TimeWindow(6 * HOUR, 16 * HOUR),
                           reload_locations=("D",), max_reloads=3,
                           reload_duration=15 * 60)
                       for v in range(1, rounds + 1)),
        matrix=planar_matrix(f"uc013-{size}", coordinates))


def uc004_beverage_with_empties(size: str = "small") -> Problem:
    """Full crates out, empties back, on the same stop.

    Half the round hands empties back, so the load stops falling monotonically
    and the binding number becomes the highest point along the way.
    """
    count = stops_for(size)
    coordinates = ((0.0, 0.0),) + scatter(count, seed=4, spread_km=20.0, clusters=2)
    orders = []
    for i in range(1, count + 1):
        if i % 2:
            orders.append(drop(f"O{i}", f"C{i}", service=600, kg=60))
        else:
            orders.append(collect(f"O{i}", f"C{i}", kg=55))
    return Problem(
        id=f"uc004-{size}", locations=planar_sites(coordinates),
        orders=tuple(orders),
        vehicles=tuple(van(f"V{v}", capacities={"kg": 300},
                           shift=TimeWindow(6 * HOUR, 16 * HOUR))
                       for v in range(1, max(2, count // 4) + 1)),
        matrix=planar_matrix(f"uc004-{size}", coordinates))


def uc001_grocery_evening_peak(size: str = "small") -> Problem:
    """Slots, not volume. Two thirds of the orders book the evening peak.

    Every order is small enough that volume never binds; what decides the fleet
    is how many customers want the same two hours.
    """
    count = stops_for(size)
    coordinates = ((0.0, 0.0),) + scatter(count, seed=1, spread_km=10.0, clusters=3)
    peak = TimeWindow(start=17 * HOUR, end=19 * HOUR)
    daytime = TimeWindow(start=9 * HOUR, end=16 * HOUR)
    orders = tuple(drop(f"O{i}", f"C{i}", service=300,
                        windows=(peak if i % 3 else daytime,), kg=4)
                   for i in range(1, count + 1))
    return Problem(
        id=f"uc001-{size}", locations=planar_sites(coordinates), orders=orders,
        vehicles=tuple(van(f"V{v}", capacities={"kg": 400},
                           shift=TimeWindow(8 * HOUR, 21 * HOUR))
                       for v in range(1, max(2, count // 3) + 1)),
        matrix=planar_matrix(f"uc001-{size}", coordinates))


def uc003_receiving_bay_hours(size: str = "small") -> Problem:
    """Goods-in closes and does not reopen. The window is hard, not expensive."""
    count = stops_for(size)
    coordinates = ((0.0, 0.0),) + scatter(count, seed=3, spread_km=30.0)
    bay = TimeWindow(start=6 * HOUR, end=11 * HOUR, hardness="HARD")
    return Problem(
        id=f"uc003-{size}", locations=planar_sites(coordinates),
        orders=tuple(drop(f"O{i}", f"C{i}", service=45 * 60, windows=(bay,),
                          kg=200) for i in range(1, count + 1)),
        vehicles=tuple(van(f"V{v}", capacities={"kg": 1_200},
                           shift=TimeWindow(4 * HOUR, 14 * HOUR),
                           hos_rules="EU-561")
                       for v in range(1, max(2, count // 4) + 1)),
        matrix=planar_matrix(f"uc003-{size}", coordinates))


def uc009_parcel_last_mile(size: str = "small") -> Problem:
    """Stops per hour, where the hour is mostly parking and walking.

    §4.2 reports travel at roughly a third of the driver's day. Half the stops
    here carry a `dwell_overhead` -- the walk from a legal parking space -- so a
    plan can be shorter in distance and longer in the only unit that matters.
    """
    count = stops_for(size)
    coordinates = ((0.0, 0.0),) + scatter(count, seed=9, spread_km=4.0, clusters=4)
    sites_out = tuple(
        Location(id=site.id, lat=site.lat, lon=site.lon,
                 matrix_index=site.matrix_index,
                 dwell_overhead=0 if index == 0 or index % 2 else 8 * 60)
        for index, site in enumerate(planar_sites(coordinates)))
    return Problem(
        id=f"uc009-{size}", locations=sites_out,
        orders=tuple(drop(f"O{i}", f"C{i}", service=120, kg=1)
                     for i in range(1, count + 1)),
        vehicles=tuple(van(f"V{v}", capacities={"kg": count},
                           shift=TimeWindow(8 * HOUR, 18 * HOUR))
                       for v in range(1, max(2, count // 30) + 1)),
        matrix=planar_matrix(f"uc009-{size}", coordinates))


def uc019_home_start_technicians(size: str = "small") -> Problem:
    """Engineers who start and end at their own front doors.

    There is one office and it is not where anybody begins, so the instance is
    multi-depot with a single depot -- which is the entry's whole point.
    """
    count = stops_for(size)
    crews = max(2, -(-count // 3))
    homes = scatter(crews, seed=190, spread_km=12.0)
    work = scatter(count, seed=19, spread_km=12.0)
    coordinates = ((0.0, 0.0),) + homes + work
    locations = [Location(id="D", lat=9.9, lon=-84.0, matrix_index=0)]
    for index, (x, y) in enumerate(homes):
        locations.append(Location(id=f"H{index + 1}",
                                  lat=round(9.9 + y / KM_PER_DEGREE, 6),
                                  lon=round(-84.0 + x / KM_PER_DEGREE, 6),
                                  matrix_index=1 + index))
    for index, (x, y) in enumerate(work):
        locations.append(Location(id=f"C{index + 1}",
                                  lat=round(9.9 + y / KM_PER_DEGREE, 6),
                                  lon=round(-84.0 + x / KM_PER_DEGREE, 6),
                                  matrix_index=1 + crews + index))
    shift = TimeWindow(start=8 * HOUR, end=17 * HOUR)
    orders = []
    for i in range(1, count + 1):
        # Appointments are offered up to mid-afternoon, not up to the moment the
        # shift ends: a 45-minute install booked at 16:00 leaves no room for it
        # and no room for the drive home, and the crew overruns by construction.
        opens = 8 * HOUR + (i * 5 * HOUR) // (count + 1)
        orders.append(Order(
            id=f"O{i}", kind="JOB", quantities={"jobs": 1},
            required_skills=frozenset({"GAS" if i % 2 else "ELEC"}),
            delivery=StopSpec(location_id=f"C{i}", service_fixed=45 * 60,
                              time_windows=(TimeWindow(start=opens,
                                                       end=opens + 4 * HOUR),))))
    vehicles = tuple(
        Vehicle(id=f"T{v}", capacities={"jobs": count}, shift=shift,
                start_location_id=f"H{v}", end_location_id=f"H{v}",
                skills=frozenset({"GAS", "ELEC"} if v % 2 else {"ELEC"}))
        for v in range(1, crews + 1))
    return Problem(id=f"uc019-{size}", locations=tuple(locations),
                   orders=tuple(orders), vehicles=vehicles,
                   matrix=planar_matrix(f"uc019-{size}", coordinates))


def uc002_multi_temperature_replenishment(size: str = "small") -> Problem:
    """Frozen, chilled and ambient in one physically partitioned vehicle.

    Each compartment is its own dimension, and the frozen one is the small one.
    The totals fit comfortably; the frozen compartment does not.
    """
    count = stops_for(size)
    coordinates = ((0.0, 0.0),) + scatter(count, seed=2, spread_km=18.0, clusters=2)
    orders = tuple(
        Order(id=f"O{i}", kind="JOB",
              quantities={"frozen": 3 if i % 3 == 0 else 0,
                          "chilled": 4, "ambient": 9},
              order_class="GROCERY",
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=420))
        for i in range(1, count + 1))
    return Problem(
        id=f"uc002-{size}", locations=planar_sites(coordinates), orders=orders,
        vehicles=tuple(van(f"V{v}",
                           capacities={"frozen": 6, "chilled": 40, "ambient": 120},
                           shift=TimeWindow(5 * HOUR, 15 * HOUR))
                       for v in range(1, max(2, count // 3) + 1)),
        matrix=planar_matrix(f"uc002-{size}", coordinates))


def uc134_overlapping_depot_catchments(size: str = "small") -> Problem:
    """Three depots whose catchments overlap, and only one of them has stock.

    The depot nearest most of the work is empty, so nearest-depot assignment
    forecloses every plan that works.
    """
    count = stops_for(size)
    depots = ((0.0, 0.0), (14.0, 0.0), (-14.0, 0.0))
    coordinates = depots + scatter(count, seed=134, spread_km=13.0, clusters=3)
    stocked = {"D": 0, "D2": count * 10, "D3": count * 10}
    locations = []
    for index, site in enumerate(planar_sites(coordinates, depots=3)):
        if index < 3:
            locations.append(Location(id=site.id, lat=site.lat, lon=site.lon,
                                      matrix_index=index,
                                      inventory={"kg": stocked[site.id]}))
        else:
            locations.append(site)
    vehicles = tuple(
        van(f"V{v}", capacities={"kg": 60},
            start_location_id=depot, end_location_id=depot,
            shift=TimeWindow(6 * HOUR, 18 * HOUR))
        for v, depot in enumerate(["D", "D2", "D3"] * max(1, count // 6), start=1))
    return Problem(
        id=f"uc134-{size}", locations=tuple(locations),
        orders=tuple(drop(f"O{i}", f"C{i}", kg=10) for i in range(1, count + 1)),
        vehicles=vehicles,
        matrix=planar_matrix(f"uc134-{size}", coordinates))


def uc032_breakdown_at_midday(size: str = "small") -> Problem:
    """A day's plan, built to be interrupted. The trigger is applied by the test."""
    count = stops_for(size)
    coordinates = ((0.0, 0.0),) + scatter(count, seed=32, spread_km=16.0, clusters=3)
    return Problem(
        id=f"uc032-{size}", locations=planar_sites(coordinates),
        orders=tuple(drop(f"O{i}", f"C{i}", service=300, kg=5)
                     for i in range(1, count + 1)),
        vehicles=tuple(van(f"V{v}", capacities={"kg": 40},
                           shift=TimeWindow(7 * HOUR, 19 * HOUR))
                       for v in range(1, max(3, count // 4) + 1)),
        matrix=planar_matrix(f"uc032-{size}", coordinates))


def uc033_urgent_injection(size: str = "small") -> Problem:
    """Routes with windows tight enough that slack is the scarce thing.

    The urgent order the test injects is `URGENT`, held out of the base plan.
    """
    count = stops_for(size)
    coordinates = ((0.0, 0.0),) + scatter(count, seed=33, spread_km=14.0, clusters=2)
    orders = []
    for i in range(1, count + 1):
        opens = 8 * HOUR + (i * 6 * HOUR) // (count + 1)
        orders.append(drop(f"O{i}", f"C{i}", service=600, kg=5,
                           windows=(TimeWindow(start=opens,
                                               end=opens + 90 * 60),)))
    return Problem(
        id=f"uc033-{size}", locations=planar_sites(coordinates),
        orders=tuple(orders),
        vehicles=tuple(van(f"V{v}", capacities={"kg": 60},
                           shift=TimeWindow(7 * HOUR, 18 * HOUR))
                       for v in range(1, max(3, count // 4) + 1)),
        matrix=planar_matrix(f"uc033-{size}", coordinates))


def uc171_driver_absent_at_shift_start(size: str = "small") -> Problem:
    """A loaded fleet, priorities, and prizes on the work that may be dropped.

    The absence is applied by the test by removing a vehicle. Priority tiers and
    prizes exist so that stripping is a *choice* rather than an arbitrary loss.
    """
    count = stops_for(size)
    coordinates = ((0.0, 0.0), (12.0, 6.0)) + scatter(count, seed=171,
                                                      spread_km=15.0, clusters=2)
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 8},
              priority_tier=0 if i % 4 == 0 else 1,
              prize=0 if i % 4 == 0 else 100_000,
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=300))
        for i in range(1, count + 1))
    vehicles = tuple(
        van(f"V{v}", capacities={"kg": 48}, shift=TimeWindow(7 * HOUR, 17 * HOUR),
            start_location_id="D" if v % 2 else "D2",
            end_location_id="D" if v % 2 else "D2")
        for v in range(1, max(3, count // 5) + 1))
    return Problem(id=f"uc171-{size}", locations=planar_sites(coordinates, depots=2),
                   orders=orders, vehicles=vehicles,
                   matrix=planar_matrix(f"uc171-{size}", coordinates))


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
    # §13.1's P0 operational set, at three sizes each.
    "UC-001": uc001_grocery_evening_peak,
    "UC-002": uc002_multi_temperature_replenishment,
    "UC-003": uc003_receiving_bay_hours,
    "UC-004": uc004_beverage_with_empties,
    "UC-009": uc009_parcel_last_mile,
    "UC-013": uc013_waste_round_with_tips,
    "UC-019": uc019_home_start_technicians,
    "UC-032": uc032_breakdown_at_midday,
    "UC-033": uc033_urgent_injection,
    "UC-075": uc075_delivery_station_sequencing,
    "UC-077": uc077_single_technician_day,
    "UC-087": uc087_resequence_from_here,
    "UC-134": uc134_overlapping_depot_catchments,
    "UC-171": uc171_driver_absent_at_shift_start,
}

# Named rather than omitted, on the same principle as `diagnose.UNIMPLEMENTED`:
# a scenario with no fixture and a scenario with no *possible* fixture look
# identical from outside the registry, and only one of them is work to do.
NOT_AN_INSTANCE: dict[str, str] = {
    "UC-072": "its subject is the matrix build, not a routing instance: a "
              "provider that stops responding mid-build has no Problem to "
              "attach to. Exercised against the matrix layer instead.",
}
