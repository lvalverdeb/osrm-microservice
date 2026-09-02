"""EV range and en-route recharging — FR-20, T-41.

`FR-20`: "Support **EV range and en-route recharging** with charging-time
functions." `COULD` priority, and the only requirement in the backlog with no
data source in the stack: nobody here has charger locations or a manufacturer's
charging curve. The corpus is generated for exactly that reason, which is what
the task's own definition of done asks for -- "range never violated on a
generated EV corpus".

The second half of that definition is the one worth writing tests around:
"charging time appears in the duty timeline, not bolted on after". An hour on a
charger that is added to a total at the end is a number in a report. An hour
that appears as a step pushes every subsequent arrival an hour later, and can
therefore break a time window -- which is the whole difference between
modelling a constraint and accounting for it.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from vrp.battery import (
    FULL_PPT,
    ChargeStop,
    ChargingCurve,
    charge_seconds,
    consumed_ppt,
)
from vrp.electric import NoChargerReachable, plan_charging
from vrp.evaluator import build_timeline, route_metrics
from vrp.model import (
    Location,
    Order,
    Problem,
    Route,
    Solution,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.verify import verify

HOUR = 3600
KM = 1000


def taper() -> ChargingCurve:
    """Fast to 80%, then a third of the rate. The shape every EV has."""
    return ChargingCurve(bands=((800, 60_000), (FULL_PPT, 20_000)))


def a_road(stops: int = 4, hop_km: int = 30) -> Problem:
    """Stops strung out along a road, with a charger at the halfway point.

    Distances big enough that a battery matters: a 40 kWh van at 250 Wh/km
    covers 160 km, and four thirty-kilometre hops out and back is 240.
    """
    day = TimeWindow(start=0, end=20 * HOUR)
    ids = ["D"] + [f"C{i}" for i in range(1, stops + 1)] + ["CH"]
    # The charger sits beside the second stop rather than on the way to it, so
    # a plan that visits it has genuinely gone somewhere.
    positions = [0] + list(range(1, stops + 1)) + [2]
    locations = tuple(
        Location(id=site, lat=9.9 + index / 100, lon=-84.0, matrix_index=index)
        for index, site in enumerate(ids))
    grid = tuple(
        tuple(abs(a - b) * hop_km * KM for b in positions) for a in positions)
    durations = tuple(tuple(metres // 10 for metres in row) for row in grid)
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}", service_fixed=300,
                                time_windows=(day,)))
        for i in range(1, stops + 1))
    return Problem(
        id="road", locations=locations, orders=orders,
        vehicles=(an_ev(),),
        matrix=TravelMatrix(version="road", durations=durations, distances=grid))


def an_ev(battery_wh: int = 40_000, wh_per_km: int = 250) -> Vehicle:
    return Vehicle(
        id="V1", capacities={"kg": 100},
        shift=TimeWindow(start=7 * HOUR, end=20 * HOUR),
        start_location_id="D", end_location_id="D",
        battery_wh=battery_wh, consumption_wh_per_km=wh_per_km,
        charger_locations=frozenset({"CH"}), charging_curve=taper())


def a_reachable_road() -> Problem:
    """A round beyond the battery that one charge rescues.

    50 kWh at 250 Wh/km is 200 km of range against a 240 km round: too far to
    drive, and comfortable once the van tops up at the halfway charger. The
    default 40 kWh van cannot finish this round however it charges, which is a
    different right answer and has its own test.
    """
    base = a_road()
    return Problem(id=base.id, locations=base.locations, orders=base.orders,
                   vehicles=(an_ev(battery_wh=50_000),), matrix=base.matrix)


def a_diesel() -> Vehicle:
    return Vehicle(
        id="V1", capacities={"kg": 100},
        shift=TimeWindow(start=7 * HOUR, end=20 * HOUR),
        start_location_id="D", end_location_id="D")


# --------------------------------------------------------------------------
# The charging-time function
# --------------------------------------------------------------------------

def test_the_curve_tapers_or_it_is_not_a_charging_function():
    """FR-20 says "with charging-time functions", plural and deliberate.

    A constant rate is a division, and calling it a curve would make the whole
    feature decorative: the reason the last fifth of a battery is expensive is
    exactly why a plan charges to eighty percent and drives on.
    """
    battery, curve = 40_000, taper()
    lower = charge_seconds(battery, curve, 400, 600)
    upper = charge_seconds(battery, curve, 800, FULL_PPT)

    assert lower > 0 and upper > 0
    assert upper > lower * 2, (
        f"the same fifth of a battery took {lower}s low down and {upper}s at "
        "the top; this curve does not taper and nothing downstream can prefer "
        "a shorter stop")


def test_charging_nothing_takes_no_time_and_charging_backwards_is_refused():
    battery, curve = 40_000, taper()
    assert charge_seconds(battery, curve, 500, 500) == 0
    with pytest.raises(ValueError, match="below"):
        charge_seconds(battery, curve, 600, 500)


def test_consumption_is_proportional_to_distance():
    assert consumed_ppt(40_000, 250, 160 * KM) == FULL_PPT
    assert consumed_ppt(40_000, 250, 80 * KM) == FULL_PPT // 2
    assert consumed_ppt(40_000, 250, 0) == 0


# --------------------------------------------------------------------------
# The timeline
# --------------------------------------------------------------------------

def test_a_diesel_route_carries_no_state_of_charge_at_all():
    """A fleet with no battery must be untouched by any of this."""
    problem = a_road()
    diesel = Problem(id=problem.id, locations=problem.locations,
                     orders=problem.orders, vehicles=(a_diesel(),),
                     matrix=problem.matrix)
    timeline = build_timeline(diesel, "V1", ["O1", "O2"])

    assert all(step.soc_after_ppt is None for step in timeline)
    assert not [step for step in timeline if step.type == "CHARGE"]


def test_the_battery_falls_as_the_van_drives():
    problem = a_road()
    timeline = build_timeline(problem, "V1", ["O1", "O2"])

    charges = [step.soc_after_ppt for step in timeline]
    assert charges[0] == FULL_PPT
    assert charges == sorted(charges, reverse=True), (
        "the state of charge went up without a charger, which is a van "
        "generating electricity")
    assert charges[-1] < FULL_PPT


def test_a_charge_step_appears_between_the_stops_it_falls_between():
    problem = a_road()
    timeline = build_timeline(problem, "V1", ["O1", "O2", "O3"],
                              charges={1: ChargeStop("CH", FULL_PPT)})

    kinds = [step.type for step in timeline]
    assert kinds == ["START", "DELIVERY", "CHARGE", "DELIVERY", "DELIVERY",
                     "END"]
    charge = timeline[2]
    assert charge.location_id == "CH"
    assert charge.order_id is None
    assert charge.departure - charge.start_service > 0
    assert charge.soc_after_ppt == FULL_PPT


def test_the_charge_pushes_every_later_arrival_by_exactly_its_duration():
    """The definition of done, and the reason it is worded the way it is.

    Charging bolted on after is a number in a report. Charging in the timeline
    delays the rest of the day, which is what lets a time window notice it.
    """
    problem = a_road()
    without = build_timeline(problem, "V1", ["O1", "O2", "O3"])
    with_charge = build_timeline(problem, "V1", ["O1", "O2", "O3"],
                                 charges={1: ChargeStop("CH", FULL_PPT)})

    charge = next(s for s in with_charge if s.type == "CHARGE")
    plugged_in = charge.departure - charge.start_service
    assert plugged_in > 0, "a charge of no duration cannot delay anything"

    detour = (charge.arrival - without[1].departure) + (
        with_charge[3].arrival - charge.departure) - (
        without[2].arrival - without[1].departure)
    later = with_charge[3].arrival - without[2].arrival
    assert later == plugged_in + detour, (
        f"the stop after the charger moved {later}s, which is not the "
        f"{plugged_in}s on the plug plus the {detour}s of detour")


def test_a_charge_lands_in_the_duty_metrics_as_service_not_as_nothing():
    problem = a_road()
    plain = route_metrics(problem, build_timeline(problem, "V1", ["O1", "O2"]))
    charged = route_metrics(problem, build_timeline(
        problem, "V1", ["O1", "O2"], charges={1: ChargeStop("CH", FULL_PPT)}))

    assert charged["service_seconds"] > plain["service_seconds"]


# --------------------------------------------------------------------------
# Planning the charge
# --------------------------------------------------------------------------

def test_a_round_beyond_the_range_gets_a_charge_and_finishes():
    problem = a_reachable_road()
    orders = ["O1", "O2", "O3", "O4"]
    flat = build_timeline(problem, "V1", orders)
    assert min(step.soc_after_ppt for step in flat) <= 0, (
        "this round is inside the battery already, so planning a charge into "
        "it proves nothing")

    charges = plan_charging(problem, "V1", orders)
    assert charges, "no charge was planned for a round that cannot be driven"
    timeline = build_timeline(problem, "V1", orders, charges=charges)
    assert min(step.soc_after_ppt for step in timeline) > 0


def test_a_round_inside_the_range_is_left_alone():
    problem = a_road()
    assert plan_charging(problem, "V1", ["O1"]) == {}


def test_a_van_that_cannot_reach_a_charger_is_refused_by_name():
    """CON-11: what cannot be said soundly is refused rather than approximated.

    A plan that quietly drops the stop it cannot reach, or one that charges at
    a customer's doorstep, are both worse than being told the fleet is wrong
    for the round.
    """
    problem = a_road()
    stranded = Problem(
        id=problem.id, locations=problem.locations, orders=problem.orders,
        vehicles=(an_ev(battery_wh=6_000),), matrix=problem.matrix)

    with pytest.raises(NoChargerReachable, match="V1"):
        plan_charging(stranded, "V1", ["O1", "O2", "O3", "O4"])


# --------------------------------------------------------------------------
# INV-16, checked by the verifier rather than by the thing that planned it
# --------------------------------------------------------------------------

def test_the_verifier_catches_a_route_that_runs_the_battery_flat():
    problem = a_road()
    solution = _solution(problem, ["O1", "O2", "O3", "O4"])
    report = verify(problem, solution)

    assert not report.ok
    assert any(failure.invariant == "INV-16" for failure in report.violations)


def test_the_verifier_catches_charging_where_there_is_no_charger():
    problem = a_road()
    solution = _solution(problem, ["O1", "O2"],
                         charges={1: ChargeStop("C3", FULL_PPT)})
    report = verify(problem, solution)

    assert any(failure.invariant == "INV-16"
               and "C3" in failure.detail for failure in report.violations)


def test_the_verifier_does_not_believe_a_charge_the_plug_could_not_deliver():
    """CON-1, and the reason the verifier recomputes rather than reads.

    A plan can write any state of charge it likes into its own steps. This one
    claims a full battery from ninety seconds on the plug -- which the curve
    says is worth a few parts per thousand -- and then drives home on it. A
    verifier that read `soc_after_ppt` would agree the route is fine, which
    would make the field a way of asserting compliance rather than a report of
    it.
    """
    problem = a_reachable_road()
    orders = ["O1", "O2", "O3", "O4"]
    honest = build_timeline(problem, "V1", orders,
                            charges=plan_charging(problem, "V1", orders))
    assert verify(problem, _routed(problem, honest)).ok

    boasting = tuple(
        replace(step, departure=step.start_service + 90)
        if step.type == "CHARGE" else step
        for step in honest)
    claimed = next(s for s in boasting if s.type == "CHARGE")
    assert claimed.soc_after_ppt == FULL_PPT, (
        "the doctored step no longer claims a full battery, so believing it "
        "would cost nothing and this test proves nothing")

    report = verify(problem, _routed(problem, boasting))
    assert any(failure.invariant == "INV-16"
               for failure in report.violations), (
        "ninety seconds on a plug bought a full battery and the verifier "
        "took the plan's word for it")


def test_a_properly_charged_route_passes():
    problem = a_reachable_road()
    orders = ["O1", "O2", "O3", "O4"]
    solution = _solution(problem, orders,
                         charges=plan_charging(problem, "V1", orders))
    report = verify(problem, solution)

    assert not [f for f in report.violations if f.invariant == "INV-16"], (
        [f.detail for f in report.violations if f.invariant == "INV-16"])


# --------------------------------------------------------------------------
# The definition of done: a generated corpus
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(12))
def test_range_is_never_violated_across_a_generated_ev_corpus(seed):
    """"Range never violated on a generated EV corpus."

    Varies the two things that decide whether a round is drivable -- how far
    apart the stops are and how big the battery is -- so the corpus contains
    rounds that need no charge, rounds that need one, and rounds no charge can
    rescue. All three have a right answer and none of them is a flat battery.
    """
    hop = 10 + (seed % 4) * 15
    battery = 20_000 + (seed // 4) * 20_000
    problem = a_road(hop_km=hop)
    problem = Problem(id=problem.id, locations=problem.locations,
                      orders=problem.orders,
                      vehicles=(an_ev(battery_wh=battery),),
                      matrix=problem.matrix)
    orders = ["O1", "O2", "O3", "O4"]

    try:
        charges = plan_charging(problem, "V1", orders)
    except NoChargerReachable:
        return  # refused by name, which is the right answer for this one

    timeline = build_timeline(problem, "V1", orders, charges=charges)
    assert min(step.soc_after_ppt for step in timeline) >= 0
    report = verify(problem, _solution(problem, orders, charges=charges))
    assert not [f for f in report.violations if f.invariant == "INV-16"]


def _solution(problem, orders, charges=None):
    return _routed(problem, build_timeline(problem, "V1", orders,
                                           charges=charges))


def _routed(problem, timeline):
    return Solution(
        problem_id=problem.id, status="FEASIBLE",
        routes=(Route(vehicle_id="V1", steps=timeline),),
        unassigned=())
