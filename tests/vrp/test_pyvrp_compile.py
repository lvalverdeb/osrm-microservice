"""Characterisation of `compile_problem`: the PyVRP model, read back.

`compile_problem` translates a `Problem` into a PyVRP `Model`. Until this file
nothing tested the translation itself: a compile error surfaced, if at all, as
a plan the verifier accepted but should have beaten. These tests read every
observable fact back out of the compiled model and check it against the
`Problem` it came from -- locations and their order, the depot set, each
client's window, service, prize and requirement, each vehicle type's capacity,
depots, costs and limits, and the edge set of every profile. The expectations
are derived from the `Problem`, not from the compiler, so a refactor of the
compiler is checked against something that did not change with it.

Shared helpers (`tier_bonuses`, `service_time`, `must_be_served`,
`_eligibility_key`) are reused for the expected values. They are not what the
compiler's structure is made of, and recomputing a tier bonus by hand here
would be a second implementation of FR-13 rather than a check on the first.

Sources are the whole fixture registry and the benchmark corpus, plus one
hand-built problem for the branches neither reaches (see `everything_else`).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pyvrp import Model
from pyvrp._pyvrp import VehicleType

from vrp.bench.corpus import CORPUS, build_instance
from vrp.bench.fixtures import FIXTURES, drop, instance, maximal_problem, van
from vrp.model import (
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    must_be_served,
    precedence,
    service_time,
)
from vrp.solve.pyvrp_adapter import (
    _eligibility_key,
    compile_problem,
    delivery_deadline,
    tier_bonuses,
)

SINK = "__open_route_sink__"

# Compiled through `solve()` only after a refusal that these instances exist to
# trigger; `test_refusals_still_refuse` pins them instead.
REFUSED = {"UC-067", "UC-071"}


def everything_else() -> Problem:
    """The branches no registry fixture reaches, in one instance.

    A shipment, an order with two disjoint hard windows (a client group), a
    soft window (widened to the shift), a release time with a prize on a
    declinable tier, duration and distance limits, every vehicle cost, and a
    second load dimension carried by one vehicle only.
    """
    orders = (
        drop("J1", "C1", windows=(TimeWindow(start=0, end=3600),
                                  TimeWindow(start=7200, end=10800))),
        drop("J2", "C2", windows=(TimeWindow(start=100, end=200, hardness="SOFT"),)),
        Order(id="J3", kind="JOB", quantities={"kg": 2}, priority_tier=1, prize=7,
              release_time=500,
              delivery=StopSpec(location_id="C3",
                                time_windows=(TimeWindow(start=1000, end=5000),),
                                service_fixed=90)),
        Order(id="S1", kind="SHIPMENT", quantities={"kg": 3},
              pickup=StopSpec(location_id="C4",
                              time_windows=(TimeWindow(start=0, end=4000),),
                              service_fixed=30),
              delivery=StopSpec(location_id="C1",
                                time_windows=(TimeWindow(start=0, end=9000),),
                                service_fixed=45)),
    )
    vehicles = (
        van("V1", max_duration=8 * 3600, max_distance=100_000, fixed_cost=500,
            cost_per_metre=2, cost_per_second=1),
        van("V2", capacities={"kg": 50, "m3": 4}),
    )
    return instance("everything-else", orders, vehicles)


def sources() -> dict[str, Callable[[], Problem]]:
    out: dict[str, Callable[[], Problem]] = {
        name: build for name, build in FIXTURES.items() if name not in REFUSED}
    for spec in CORPUS:
        out[f"corpus:{spec.name}"] = lambda spec=spec: build_instance(spec)
    out["everything-else"] = everything_else
    return out


SOURCES = sources()


@pytest.fixture(params=sorted(SOURCES), ids=str)
def compiled(request):
    problem = SOURCES[request.param]()
    return problem, compile_problem(problem)


def missing_edge() -> int:
    """The value PyVRP stores for an arc that was never added."""
    model = Model()
    a = model.add_location(x=0, y=0)
    b = model.add_location(x=1, y=1)
    depot = model.add_depot(location=a)
    model.add_client(location=b, delivery=[0], pickup=[0])
    model.add_vehicle_type(capacity=[0], start_depot=depot, end_depot=depot)
    return int(model.data().distance_matrix(0)[0][1])


MISSING = missing_edge()

# The compiler passes a cost or limit only when the vehicle sets one, so an
# unset field compiles to whatever PyVRP defaults it to -- and that default is
# part of the behaviour being characterised (an unset distance cost is 1, not 0).
BARE = VehicleType()


def dimensions(problem: Problem) -> list[str]:
    names = {d for order in problem.orders for d in order.quantities}
    names |= {d for vehicle in problem.vehicles for d in vehicle.capacities}
    return sorted(names)


def shift_bounds(problem: Problem) -> tuple[int, int]:
    return (min(v.shift.start for v in problem.vehicles),
            max(v.shift.end for v in problem.vehicles))


def window_bounds(window: TimeWindow | None, problem: Problem) -> tuple[int, int]:
    if window is None or window.hardness == "SOFT":
        return shift_bounds(problem)
    return window.start, window.end


def by_index(problem: Problem):
    return sorted(problem.locations, key=lambda location: location.matrix_index)


def test_locations_are_the_problems_in_matrix_order_plus_a_sink_if_open(compiled):
    problem, result = compiled
    expected = [location.id for location in by_index(problem)]
    if any(vehicle.open_route for vehicle in problem.vehicles):
        expected.append(SINK)
    assert [location.name for location in result.model.locations] == expected


def test_depots_are_where_vehicles_start_end_and_reload(compiled):
    problem, result = compiled
    expected = {vehicle.start_location_id for vehicle in problem.vehicles}
    expected |= {vehicle.ends_at for vehicle in problem.vehicles
                 if not vehicle.open_route}
    expected |= {name for vehicle in problem.vehicles
                 for name in vehicle.reload_locations}
    if any(vehicle.open_route for vehicle in problem.vehicles):
        expected.add(SINK)
    depots = result.model.depots
    assert {depot.name for depot in depots} == expected
    index_of = {location.id: location.matrix_index for location in problem.locations}
    index_of[SINK] = len(problem.locations)
    start, end = shift_bounds(problem)
    for depot in depots:
        assert depot.location == index_of[depot.name]
        assert (depot.tw_early, depot.tw_late) == (start, end)


def expected_clients(problem: Problem) -> list[dict]:
    dims = dimensions(problem)
    bonuses = tier_bonuses(problem)
    rows = []
    for order in problem.orders:
        if order.kind == "SHIPMENT":
            continue
        stop = order.delivery or order.pickup
        location = problem.location(stop.location_id)
        amounts = [order.quantities.get(name, 0) for name in dims]
        zeros = [0] * len(dims)
        windows = stop.time_windows or (None,)
        for window in windows:
            rows.append({
                "name": order.id,
                "location": location.matrix_index,
                "delivery": amounts if order.delivery is not None else zeros,
                "pickup": amounts if order.delivery is None else zeros,
                "service": service_time(order, problem.vehicles[0], location),
                "release": order.release_time,
                "prize": order.prize + bonuses[precedence(order)],
                "required": False if len(windows) > 1 else must_be_served(order),
                "window": window_bounds(window, problem),
                "grouped": len(windows) > 1,
            })
    return rows


def test_clients_carry_each_orders_facts_one_per_window(compiled):
    problem, result = compiled
    expected = expected_clients(problem)
    clients = result.model.clients
    assert len(clients) == len(expected)
    assert result.order_by_client == {i: row["name"] for i, row in enumerate(expected)}
    for client, row in zip(clients, expected, strict=True):
        assert client.name == row["name"]
        assert client.location == row["location"]
        assert list(client.delivery) == row["delivery"]
        assert list(client.pickup) == row["pickup"]
        assert client.service_duration == row["service"]
        assert client.release_time == row["release"]
        assert client.prize == row["prize"]
        assert client.required is row["required"]
        assert (client.tw_early, client.tw_late) == row["window"]
        assert (client.group is not None) is row["grouped"]


def test_an_order_with_several_windows_is_one_required_exclusive_group(compiled):
    problem, result = compiled
    multi = [order for order in problem.orders if order.kind != "SHIPMENT"
             and len((order.delivery or order.pickup).time_windows) > 1]
    groups = result.model.groups
    assert len(groups) == len(multi)
    for group, order in zip(groups, multi, strict=True):
        assert group.required and group.mutually_exclusive
        assert len(group.clients) == len((order.delivery or order.pickup).time_windows)


def test_shipments_carry_both_ends(compiled):
    problem, result = compiled
    dims = dimensions(problem)
    bonuses = tier_bonuses(problem)
    orders = [order for order in problem.orders if order.kind == "SHIPMENT"]
    shipments = result.model.shipments
    assert len(shipments) == len(orders)
    assert result.order_by_shipment == {i: order.id for i, order in enumerate(orders)}
    start, end = shift_bounds(problem)
    for shipment, order in zip(shipments, orders, strict=True):
        collect = problem.location(order.pickup.location_id)
        deliver = problem.location(order.delivery.location_id)
        assert shipment.name == order.id
        assert shipment.pickup.location == collect.matrix_index
        assert shipment.delivery.location == deliver.matrix_index
        assert list(shipment.amount) == [order.quantities.get(d, 0) for d in dims]
        assert shipment.prize == order.prize + bonuses[precedence(order)]
        assert shipment.required is must_be_served(order)
        pickup_windows = order.pickup.time_windows
        assert shipment.pickup.tw_early == (pickup_windows[0].start if pickup_windows else start)
        assert shipment.pickup.tw_late == (pickup_windows[0].end if pickup_windows else end)
        delivery_windows = order.delivery.time_windows
        assert shipment.delivery.tw_early == (
            delivery_windows[0].start if delivery_windows else start)
        assert shipment.delivery.tw_late == delivery_deadline(problem, order)
        assert shipment.pickup.service_duration == service_time(
            order, problem.vehicles[0], collect)
        assert shipment.delivery.service_duration == service_time(
            order, problem.vehicles[0], deliver)


def test_vehicle_types_carry_the_fleet(compiled):
    problem, result = compiled
    dims = dimensions(problem)
    types = result.model.vehicle_types
    depots = result.model.depots
    assert len(types) == len(problem.vehicles)
    assert result.vehicle_ids == [vehicle.id for vehicle in problem.vehicles]
    for kind, vehicle in zip(types, problem.vehicles, strict=True):
        assert kind.name == vehicle.id
        assert kind.num_available == 1
        assert list(kind.capacity) == [vehicle.capacities.get(d, 0) for d in dims]
        assert depots[kind.start_depot].name == vehicle.start_location_id
        assert depots[kind.end_depot].name == (SINK if vehicle.open_route
                                               else vehicle.ends_at)
        assert (kind.tw_early, kind.tw_late) == (vehicle.shift.start, vehicle.shift.end)
        assert kind.fixed_cost == (vehicle.fixed_cost or BARE.fixed_cost)
        assert kind.unit_distance_cost == (vehicle.cost_per_metre
                                           or BARE.unit_distance_cost)
        assert kind.unit_duration_cost == (vehicle.cost_per_second
                                           or BARE.unit_duration_cost)
        assert kind.unit_overtime_cost == (vehicle.overtime_cost_per_second
                                           or BARE.unit_overtime_cost)
        assert kind.shift_duration == (BARE.shift_duration
                                       if vehicle.max_duration is None
                                       else vehicle.max_duration)
        assert kind.max_distance == (BARE.max_distance
                                     if vehicle.max_distance is None
                                     else vehicle.max_distance)
        if vehicle.max_reloads and vehicle.reload_locations:
            names = {depot.name for depot in depots}
            assert [depots[i].name for i in kind.reload_depots] == sorted(
                name for name in vehicle.reload_locations if name in names)
            assert kind.max_reloads == vehicle.max_reloads
        else:
            assert list(kind.reload_depots) == []
            assert kind.max_reloads == BARE.max_reloads


def test_one_profile_per_distinct_eligibility_set(compiled):
    problem, result = compiled
    keys = {_eligibility_key(problem, vehicle) for vehicle in problem.vehicles}
    assert len(result.model.profiles) == len(keys)
    profile_of_key: dict[frozenset[int], int] = {}
    for kind, vehicle in zip(result.model.vehicle_types, problem.vehicles, strict=True):
        key = _eligibility_key(problem, vehicle)
        assert profile_of_key.setdefault(key, kind.profile) == kind.profile, (
            "vehicles with the same restrictions must share one profile")


def test_edges_are_the_matrix_minus_the_forbidden_and_unreachable(compiled):
    problem, result = compiled
    data = result.model.data()
    matrix = problem.matrix
    n = len(problem.locations)
    has_sink = any(vehicle.open_route for vehicle in problem.vehicles)
    forbidden_of = {}
    for kind, vehicle in zip(result.model.vehicle_types, problem.vehicles, strict=True):
        forbidden_of[kind.profile] = _eligibility_key(problem, vehicle)
    assert set(forbidden_of) == set(range(data.num_profiles))
    for profile, forbidden in forbidden_of.items():
        distance = data.distance_matrix(profile)
        duration = data.duration_matrix(profile)
        for origin in range(n):
            for destination in range(n):
                if origin == destination:
                    assert distance[origin][destination] == 0
                    continue
                absent = (destination in forbidden
                          or not matrix.is_reachable(origin, destination))
                if absent:
                    assert distance[origin][destination] == MISSING, (origin, destination)
                    assert duration[origin][destination] == MISSING
                else:
                    assert distance[origin][destination] == matrix.distance(origin, destination)
                    assert duration[origin][destination] == matrix.duration(origin, destination)
            if has_sink:
                assert distance[origin][n] == 0 and duration[origin][n] == 0
        if has_sink:
            # Nothing leaves the sink: an open route ends there.
            assert all(distance[n][j] == MISSING for j in range(n))


def test_refusals_still_refuse():
    """Instances the compiler must decline, not quietly build."""
    with pytest.raises(NotImplementedError, match="incompatibility"):
        compile_problem(FIXTURES["UC-067"]())
    with pytest.raises(NotImplementedError, match="speed profile"):
        compile_problem(maximal_problem())
    # `solve()` turns an empty fleet into "nothing to dispatch" before compiling;
    # this pins that the compiler itself does not build a model with no fleet.
    with pytest.raises(ValueError):
        compile_problem(FIXTURES["UC-071"]())


def test_the_sources_walk_every_compile_branch():
    """A characterisation over fixtures only characterises what they contain."""
    seen: set[str] = set()
    for build in SOURCES.values():
        problem = build()
        if any(v.open_route for v in problem.vehicles):
            seen.add("open route")
        if any(o.kind == "SHIPMENT" for o in problem.orders):
            seen.add("shipment")
        jobs = [o for o in problem.orders if o.kind != "SHIPMENT"]
        if any(len((o.delivery or o.pickup).time_windows) > 1 for o in jobs):
            seen.add("several windows")
        if any(o.delivery is None for o in jobs):
            seen.add("pickup only")
        if any(w.hardness == "SOFT" for o in problem.orders for s in o.stops
               for w in s.time_windows):
            seen.add("soft window")
        if any(o.release_time for o in problem.orders):
            seen.add("release time")
        if any(o.prize for o in problem.orders):
            seen.add("prize")
        if problem.locks:
            seen.add("locks")
        if any(v.skills for v in problem.vehicles):
            seen.add("skills")
        if any(v.max_reloads and v.reload_locations for v in problem.vehicles):
            seen.add("reloads")
        if any(v.max_duration is not None or v.max_distance is not None
               for v in problem.vehicles):
            seen.add("limits")
        if any(v.fixed_cost or v.cost_per_metre or v.cost_per_second
               for v in problem.vehicles):
            seen.add("costs")
        n = len(problem.locations)
        if any(not problem.matrix.is_reachable(i, j)
               for i in range(n) for j in range(n) if i != j):
            seen.add("unreachable arc")
        if len({_eligibility_key(problem, v) for v in problem.vehicles}) > 1:
            seen.add("several profiles")
    assert seen == {
        "open route", "shipment", "several windows", "pickup only", "soft window",
        "release time", "prize", "locks", "skills", "reloads", "limits", "costs",
        "unreachable arc", "several profiles",
    }
