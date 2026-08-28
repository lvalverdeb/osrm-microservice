"""Territories, consistency and fairness — FR-17, FR-18, FR-35, §6.7, T-47, E-47.

§6.7 opens by refusing to treat this as a concession: "Drivers who serve the
same territory daily accumulate tacit knowledge -- access codes, parking,
receiving-bay habits -- which reduces service time and errors... Consistency is
a genuine cost saver, not a concession."

Three measures, and they are separate on purpose:

* **Workload fairness** (FR-17) -- "penalise the spread of route duration /
  distance / stop count". Three spreads, because a fleet even on stops can be
  wildly uneven on hours, and the driver who notices is the one with the long
  day.
* **Driver consistency** (FR-18) -- "bound the number of distinct drivers
  serving a customer over a horizon". Multi-period by definition; a single day
  cannot be inconsistent.
* **Arrival-time consistency** (FR-18) -- "penalise the spread
  `max(arrival) - min(arrival)` across the horizon for each customer", with
  "departure-time adjustment at the depot as a cheap lever to align arrival
  times without changing sequences". That lever is E-39's `optimal_departure`,
  already exact.

§6.7 then makes the requirement that stops this being decorative: "It MUST be
measurable: report the cost delta of enforcing consistency versus the
unconstrained optimum so the business can price it." A consistency feature that
cannot say what it costs is one nobody can decide to buy, which is why
`test_the_price_of_consistency_is_reported` is the acceptance rather than any of
the spreads.

Tier 6 has been hard-zero since E-13 with the note "the quality tie-breakers are
T-47". This gives it a value.
"""

from __future__ import annotations

from vrp.consistency import (
    Horizon,
    arrival_spread,
    consistency_price,
    distinct_drivers,
    territories,
    workload_spread,
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
from vrp.objective import Mode, ObjectiveSpec, Tier, score, tier_scales

DAY = TimeWindow(start=0, end=12 * 3600)


def problem(stops: int = 8, vans: int = 2) -> Problem:
    size = stops + 1
    # Two clusters, so a territory split has something to find.
    xs = [0.0] + [(-5.0 - i) if i % 2 else (5.0 + i) for i in range(stops)]
    grid = tuple(tuple(int(abs(xs[i] - xs[j]) * 1000) for j in range(size))
                 for i in range(size))
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + xs[i] / 100,
                 lon=-84.0, matrix_index=i)
        for i in range(size))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=60))
        for i in range(1, size))
    vehicles = tuple(
        Vehicle(id=f"V{n}", capacities={"kg": 100}, shift=DAY,
                start_location_id="D", end_location_id="D", cost_per_metre=1)
        for n in range(1, vans + 1))
    return Problem(id="cons", locations=locations, orders=orders,
                   vehicles=vehicles,
                   matrix=TravelMatrix(version="c", durations=grid,
                                       distances=grid))


def plan(instance: Problem, assignment: dict[str, list[str]],
         starts: dict[str, int] | None = None) -> Solution:
    index = {loc.id: loc.matrix_index for loc in instance.locations}
    routes = []
    for vehicle_id, order_ids in assignment.items():
        clock = (starts or {}).get(vehicle_id, 0)
        steps = [Step(type="START", location_id="D", arrival=clock,
                      start_service=clock, departure=clock)]
        here = index["D"]
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


# --------------------------------------------------------------------------
# FR-17: workload fairness, on three measures
# --------------------------------------------------------------------------

def test_an_even_split_has_no_spread():
    instance = problem(stops=8, vans=2)
    even = plan(instance, {"V1": ["O1", "O3", "O5", "O7"],
                           "V2": ["O2", "O4", "O6", "O8"]})

    spread = workload_spread(instance, even)
    assert spread.stops == 0


def test_a_lopsided_split_shows_on_every_measure():
    """FR-17 names duration, distance *and* stop count, and the three do not
    move together. A fleet even on stops can be wildly uneven on hours, and the
    driver who notices is the one with the long day."""
    instance = problem(stops=8, vans=2)
    lopsided = plan(instance, {"V1": [f"O{i}" for i in range(1, 8)],
                               "V2": ["O8"]})

    spread = workload_spread(instance, lopsided)
    assert spread.stops == 6
    assert spread.duration > 0
    assert spread.distance > 0


def test_stop_count_can_be_even_while_hours_are_not():
    """The reason FR-17 asks for three numbers rather than one."""
    instance = problem(stops=4, vans=2)
    # Both vans take two stops; V1's are the far pair.
    uneven = plan(instance, {"V1": ["O2", "O4"], "V2": ["O1", "O3"]})

    spread = workload_spread(instance, uneven)
    assert spread.stops == 0
    assert spread.duration > 0, spread


def test_an_idle_vehicle_is_not_counted_as_a_zero_workload_route():
    """Otherwise every fleet with a spare van reports maximum imbalance, and
    the measure becomes a count of spare vans."""
    instance = problem(stops=4, vans=3)
    two_used = plan(instance, {"V1": ["O1", "O2"], "V2": ["O3", "O4"],
                               "V3": []})

    assert workload_spread(instance, two_used).stops == 0


# --------------------------------------------------------------------------
# FR-18: driver consistency across a horizon
# --------------------------------------------------------------------------

def test_one_driver_all_horizon_is_perfect_consistency():
    instance = problem(stops=4, vans=2)
    horizon = Horizon(periods=tuple(
        plan(instance, {"V1": ["O1", "O2"], "V2": ["O3", "O4"]})
        for _ in range(5)))

    assert distinct_drivers(horizon) == {"O1": 1, "O2": 1, "O3": 1, "O4": 1}


def test_swapping_drivers_between_periods_is_visible():
    instance = problem(stops=4, vans=2)
    horizon = Horizon(periods=(
        plan(instance, {"V1": ["O1", "O2"], "V2": ["O3", "O4"]}),
        plan(instance, {"V2": ["O1", "O2"], "V1": ["O3", "O4"]}),
    ))

    assert distinct_drivers(horizon) == {"O1": 2, "O2": 2, "O3": 2, "O4": 2}


def test_a_single_period_horizon_cannot_be_inconsistent():
    """Consistency is a property of a horizon. One day of it is a category
    error, and reporting a violation would be inventing one."""
    instance = problem(stops=4, vans=2)
    horizon = Horizon(periods=(plan(instance, {"V1": ["O1", "O2"],
                                               "V2": ["O3", "O4"]}),))

    assert set(distinct_drivers(horizon).values()) == {1}


# --------------------------------------------------------------------------
# FR-18: arrival-time consistency
# --------------------------------------------------------------------------

def test_identical_days_have_no_arrival_spread():
    instance = problem(stops=4, vans=2)
    same = plan(instance, {"V1": ["O1", "O2"], "V2": ["O3", "O4"]})
    horizon = Horizon(periods=(same, same, same))

    assert set(arrival_spread(horizon).values()) == {0}


def test_a_shifted_departure_moves_every_arrival_behind_it():
    """§6.7's own lever, seen from the other end: "departure-time adjustment at
    the depot as a cheap lever to align arrival times without changing
    sequences". If it can align them it can also scatter them."""
    instance = problem(stops=4, vans=2)
    horizon = Horizon(periods=(
        plan(instance, {"V1": ["O1", "O2"], "V2": ["O3", "O4"]}),
        plan(instance, {"V1": ["O1", "O2"], "V2": ["O3", "O4"]},
             starts={"V1": 3_600}),
    ))

    spread = arrival_spread(horizon)
    assert spread["O1"] == 3_600
    assert spread["O3"] == 0, "V2 never moved"


def test_a_customer_served_in_only_one_period_has_no_spread():
    """A spread over one observation is zero, not undefined and not maximal."""
    instance = problem(stops=4, vans=2)
    horizon = Horizon(periods=(
        plan(instance, {"V1": ["O1"], "V2": ["O3"]}),
        plan(instance, {"V1": ["O2"], "V2": ["O4"]}),
    ))

    assert arrival_spread(horizon)["O1"] == 0


# --------------------------------------------------------------------------
# FR-35: territories
# --------------------------------------------------------------------------

def test_every_order_lands_in_exactly_one_territory():
    instance = problem(stops=8)
    zones = territories(instance, count=2)

    placed = [order_id for members in zones.values() for order_id in members]
    assert sorted(placed) == sorted(o.id for o in instance.orders)


def test_territories_are_balanced_on_stop_count():
    instance = problem(stops=8)
    zones = territories(instance, count=2)

    sizes = sorted(len(members) for members in zones.values())
    assert sizes[-1] - sizes[0] <= 1, sizes


def test_territories_are_geographically_coherent():
    """FR-35 wants territories usable as a warm start, which a scattering is
    not. On a two-cluster instance the split must follow the clusters.

    Asserted on which side of the depot each customer sits, because that is the
    property a warm start depends on. An earlier version asserted a bound on
    matrix indices that was true of any partition whatsoever, and perturbation
    proved it: shuffling the input and dealing in blocks instead of arcs both
    left it green.
    """
    instance = problem(stops=8)
    zones = territories(instance, count=2)

    depot = instance.location("D")
    for members in zones.values():
        sides = {instance.location(instance.order(o).delivery.location_id).lat
                 > depot.lat for o in members}
        assert len(sides) == 1, (members, sides)


def test_territories_are_stable_across_calls():
    """FR-35 calls territories *stable*. Ones that move every run are not.

    There is deliberately no seed to vary: the sweep is deterministic, and a
    seed nothing could distinguish would be a knob every reader assumes does
    something. Perturbation is what settled it -- ignoring the seed entirely
    changed no result.
    """
    instance = problem(stops=8)

    assert territories(instance, 2) == territories(instance, 2)


# --------------------------------------------------------------------------
# §6.7's requirement: it MUST be measurable
# --------------------------------------------------------------------------

def test_the_price_of_consistency_is_reported():
    """§6.7: "report the cost delta of enforcing consistency versus the
    unconstrained optimum so the business can price it".

    T-47's definition of done, and the reason the spreads above are not it. A
    consistency feature that cannot say what it costs is one nobody can decide
    to buy.
    """
    instance = problem(stops=8, vans=2)
    free = {"V1": ["O1", "O3", "O5", "O7"], "V2": ["O2", "O4", "O6", "O8"]}
    zoned = {"V1": ["O1", "O2", "O3", "O4"], "V2": ["O5", "O6", "O7", "O8"]}

    price = consistency_price(instance, plan(instance, free),
                              plan(instance, zoned))

    assert price.unconstrained > 0
    assert price.consistent > 0
    assert price.delta == price.consistent - price.unconstrained


def test_consistency_that_costs_nothing_is_reported_as_free():
    """It is not always a trade. §6.7 says consistency "is a genuine cost
    saver, not a concession", and a report that could only ever show a penalty
    would be arguing rather than measuring."""
    instance = problem(stops=8, vans=2)
    same = plan(instance, {"V1": ["O1", "O2", "O3", "O4"],
                           "V2": ["O5", "O6", "O7", "O8"]})

    assert consistency_price(instance, same, same).delta == 0


# --------------------------------------------------------------------------
# Tier 6 stops being hard-zero
# --------------------------------------------------------------------------

def test_tier_six_carries_the_imbalance():
    """E-13 left `Tier.QUALITY` at zero with the note "the quality tie-breakers
    are T-47"."""
    instance = problem(stops=8, vans=2)
    spec = ObjectiveSpec(mode=Mode.MIN_COST)

    even = score(instance, plan(instance, {"V1": ["O1", "O3", "O5", "O7"],
                                           "V2": ["O2", "O4", "O6", "O8"]}),
                 spec)
    lopsided = score(instance, plan(instance,
                                    {"V1": [f"O{i}" for i in range(1, 8)],
                                     "V2": ["O8"]}), spec)

    assert even.values[Tier.QUALITY] < lopsided.values[Tier.QUALITY]


def test_imbalance_never_outranks_a_real_cost():
    """Tier 6 is the bottom of §5.1's hierarchy, and §6.7 makes consistency a
    tie-breaker rather than a reason to drive further.

    Checked on the scales, not on a pair of plans. A fixture can only show the
    property at the magnitudes it happens to produce -- and the first attempt
    here could not show it at all, because both plans it compared had identical
    operating cost and the assertion was decided by nothing. The scaling is
    where the guarantee lives: Tier 6's largest attainable contribution must
    stay under one unit of the tier above it, at any magnitude.
    """
    instance = problem(stops=8, vans=2)
    spec = ObjectiveSpec(mode=Mode.MIN_COST)
    scales = tier_scales(instance, spec)
    bounds = spec.tier_bounds(instance)

    most_imbalance_can_buy = bounds[Tier.QUALITY] * scales[Tier.QUALITY]
    one_unit_of_cost = scales[Tier.OPERATING]

    assert most_imbalance_can_buy < one_unit_of_cost, (
        most_imbalance_can_buy, one_unit_of_cost)


def test_a_balanced_plan_wins_only_when_the_costs_tie():
    """The other side of the same rule, end to end. These two plans drive
    exactly the same distance on this instance, so Tier 6 is all that separates
    them -- which is precisely when a tie-breaker is allowed to decide."""
    instance = problem(stops=8, vans=2)
    spec = ObjectiveSpec(mode=Mode.MIN_COST)

    balanced = score(instance, plan(instance, {"V1": ["O1", "O2", "O3", "O4"],
                                               "V2": ["O5", "O6", "O7", "O8"]}),
                     spec)
    lopsided = score(instance, plan(instance,
                                    {"V1": [f"O{i}" for i in range(1, 8)],
                                     "V2": ["O8"]}), spec)

    assert balanced.values[Tier.OPERATING] == lopsided.values[Tier.OPERATING]
    assert balanced.total < lopsided.total
