"""Operational allocation — FR-30, FR-33, FR-36, §7.8, T-44, E-44.

§7.8: "Allocation is solved *jointly* with routing by making vehicle deployment
endogenous: each vehicle carries a fixed cost that is charged only if it is
used, so the search decides deployment."

The word carrying the weight is *each*. `Vehicle.fixed_cost`, `cost_per_metre`
and `cost_per_second` have been on the model since E-21, and the model's own
comment says why they were moved there: costs living on `ObjectiveSpec` as one
set for everybody "made a 3.5-tonne van and an artic cost the same per
kilometre -- the 'H' in MDHVRPTW, decorative".

The canonical objective never caught up. It scores `Tier.FLEET` as a *count* of
deployed vehicles and converts it to money with a single flat rate, and it
scores operating cost with one flat rate per metre for the whole fleet. So the
model is heterogeneous, the PyVRP adapter prices each vehicle type separately --
and the evaluator that judges the result, the one INV-9 exists to make
authoritative, cannot tell a cheap fleet from an expensive one.

That is worse than a missing feature. A solver optimising per-vehicle costs and
an evaluator scoring flat ones disagree about which plan is better, and §11.2
says the evaluator wins. The cheaper plan loses.
"""

from __future__ import annotations

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
from vrp.objective import Mode, ObjectiveSpec, Tier, score

DAY = TimeWindow(start=0, end=12 * 3600)
LEG = 10_000


def instance(*vehicles: Vehicle, stops: int = 2, prize: int = 0,
             tier: int = 0) -> Problem:
    size = stops + 1
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 100, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    times = tuple(tuple(abs(i - j) * 600 for j in range(size))
                  for i in range(size))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1}, prize=prize,
              priority_tier=tier,
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=60))
        for i in range(1, size))
    return Problem(id="alloc", locations=locations, orders=orders,
                   vehicles=vehicles,
                   matrix=TravelMatrix(version="a", durations=times,
                                       distances=grid))


def a_van(vehicle_id: str, **kwargs) -> Vehicle:
    defaults = {"capacities": {"kg": 100}, "shift": DAY,
                "start_location_id": "D", "end_location_id": "D"}
    return Vehicle(id=vehicle_id, **{**defaults, **kwargs})


def plan(problem: Problem, assignment: dict[str, list[str]]) -> Solution:
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    routes = []
    for vehicle_id, order_ids in assignment.items():
        steps = [Step(type="START", location_id="D", arrival=0,
                      start_service=0, departure=0)]
        clock, here = 0, index["D"]
        for order_id in order_ids:
            stop = problem.order(order_id).delivery
            there = index[stop.location_id]
            clock += problem.matrix.duration(here, there)
            steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                              order_id=order_id, arrival=clock,
                              start_service=clock, departure=clock + 60))
            clock, here = clock + 60, there
        clock += problem.matrix.duration(here, index["D"])
        steps.append(Step(type="END", location_id="D", arrival=clock,
                          start_service=clock, departure=clock))
        routes.append(Route(vehicle_id=vehicle_id, steps=tuple(steps)))
    served = {o for ids in assignment.values() for o in ids}
    return Solution(
        problem_id=problem.id, routes=tuple(routes),
        unassigned=tuple({"order_id": o.id, "reason_code": "NOT_PLACED",
                          "explanation": "-"}
                         for o in problem.orders if o.id not in served),
        objective_breakdown={}, status="FEASIBLE")


SPEC = ObjectiveSpec(mode=Mode.MIN_COST)


# --------------------------------------------------------------------------
# FR-30: each vehicle carries its own fixed cost
# --------------------------------------------------------------------------

def test_the_fleet_tier_charges_each_vehicle_its_own_fixed_cost():
    """§5.1: "Tier 3 Fleet cost: Sum of fixed_cost(v) over deployed vehicles".

    A count is not a sum unless every vehicle costs the same, which is exactly
    the homogeneity FR-07 exists to reject.
    """
    problem = instance(a_van("CHEAP", fixed_cost=10_000),
                       a_van("DEAR", fixed_cost=90_000))

    cheap = score(problem, plan(problem, {"CHEAP": ["O1", "O2"]}), SPEC)
    dear = score(problem, plan(problem, {"DEAR": ["O1", "O2"]}), SPEC)

    assert cheap.values[Tier.FLEET] == 10_000, cheap.values[Tier.FLEET]
    assert dear.values[Tier.FLEET] == 90_000, dear.values[Tier.FLEET]
    assert cheap.total < dear.total, (
        "the same route on a cheaper vehicle did not score better")


def test_a_fleet_that_prices_nothing_still_scores_the_way_it_used_to():
    """Every instance predating E-21 states its costs on the spec, and those
    instances must keep scoring exactly as they did -- the frozen corpus among
    them. So the spec's rates are the fallback for a fleet that prices nothing.
    """
    problem = instance(a_van("V1"))
    scored = score(problem, plan(problem, {"V1": ["O1", "O2"]}), SPEC)

    assert scored.values[Tier.FLEET] == SPEC.vehicle_fixed_cost


def test_a_free_vehicle_beside_a_priced_one_is_genuinely_free():
    """The fallback is fleet-wide, not per vehicle, and this is why.

    Own capacity is sunk cost and hired capacity is not -- FR-33's whole
    subject. If an unstated zero silently became the spec's default price, an
    own vehicle could not be expressed at all, and the own-vs-hire break-even
    would be a comparison between two hire prices.
    """
    # OWN states nothing at all. A per-vehicle fallback would read that as
    # "unpriced" and hand it the spec's default day rate, which is exactly the
    # confusion this rule exists to prevent -- so OWN must stay free while
    # HIRED, in the same fleet, keeps its price.
    problem = instance(a_van("OWN"),
                       a_van("HIRED", fixed_cost=70_000, cost_per_metre=1))

    own = score(problem, plan(problem, {"OWN": ["O1", "O2"]}), SPEC)
    hired = score(problem, plan(problem, {"HIRED": ["O1", "O2"]}), SPEC)

    assert own.values[Tier.FLEET] == 0
    assert hired.values[Tier.FLEET] == 70_000
    assert own.total < hired.total


def test_an_empty_route_is_free():
    """§7.8: "Empty routes are free and removable at zero cost." A vehicle
    listed in the plan but carrying nothing was never deployed."""
    problem = instance(a_van("V1", fixed_cost=50_000),
                       a_van("V2", fixed_cost=50_000))
    both = plan(problem, {"V1": ["O1", "O2"], "V2": []})
    one = plan(problem, {"V1": ["O1", "O2"]})

    assert (score(problem, both, SPEC).values[Tier.FLEET]
            == score(problem, one, SPEC).values[Tier.FLEET] == 50_000)


# --------------------------------------------------------------------------
# FR-07 / FR-30: operating cost is per vehicle too
# --------------------------------------------------------------------------

def test_operating_cost_uses_each_vehicle_s_own_rate():
    """The artic and the van again. Same route, same metres, different money."""
    problem = instance(a_van("VAN", cost_per_metre=1),
                       a_van("ARTIC", cost_per_metre=4))

    van = score(problem, plan(problem, {"VAN": ["O1", "O2"]}), SPEC)
    artic = score(problem, plan(problem, {"ARTIC": ["O1", "O2"]}), SPEC)

    assert artic.values[Tier.OPERATING] == 4 * van.values[Tier.OPERATING], (
        van.values[Tier.OPERATING], artic.values[Tier.OPERATING])


def test_time_cost_is_charged_per_vehicle_as_well():
    problem = instance(a_van("SLOWPAID", cost_per_second=1),
                       a_van("UNPAID", cost_per_second=0))

    paid = score(problem, plan(problem, {"SLOWPAID": ["O1"]}), SPEC)
    unpaid = score(problem, plan(problem, {"UNPAID": ["O1"]}), SPEC)

    assert paid.values[Tier.OPERATING] > unpaid.values[Tier.OPERATING]


def test_an_unpriced_fleet_falls_back_to_the_spec_s_distance_rate():
    problem = instance(a_van("V1"))
    spec = ObjectiveSpec(mode=Mode.MIN_COST, cost_per_metre=7)

    scored = score(problem, plan(problem, {"V1": ["O1"]}), spec)
    assert scored.values[Tier.OPERATING] == 2 * LEG * 7, (
        scored.values[Tier.OPERATING])


# --------------------------------------------------------------------------
# §5.2 MIN_COST: "deployed iff its fixed cost is repaid by savings"
# --------------------------------------------------------------------------

def test_hiring_a_vehicle_is_worth_it_only_when_the_work_repays_the_day():
    """The break-even T-44 asks to be reproduced on fixtures, at the only place
    one exists.

    Not between one vehicle and two. In a metric space merging two routes is
    never worse on distance -- the triangle inequality guarantees
    `d(D,A) + d(D,B) >= d(A,B)` -- so splitting cannot save distance and no
    fixed cost is ever repaid by splitting. A first draft of this test asserted
    exactly that and was wrong about arithmetic, not about the implementation.

    The real trade is FR-33's: hire a vehicle for a full day (OBJ-4's step
    cost), or forgo the work. In PRIZE_COLLECTING the two share a level and are
    compared in one currency, so the answer is arithmetic:

        serve  iff  fixed_cost + operating  <  prize forgone
    """
    spec = ObjectiveSpec(mode=Mode.PRIZE_COLLECTING)
    prize, round_trip = 100_000, 2 * LEG

    for hire, worth_it in ((prize - round_trip - 1, True),
                           (prize - round_trip + 1, False)):
        problem = instance(a_van("HIRED", fixed_cost=hire, cost_per_metre=1),
                           stops=1, prize=prize, tier=2)
        served = score(problem, plan(problem, {"HIRED": ["O1"]}), spec)
        declined = score(problem, plan(problem, {}), spec)

        if worth_it:
            assert served.total < declined.total, (
                f"a day costing {hire} against {prize} of work was declined")
        else:
            assert declined.total < served.total, (
                f"a day costing {hire} against {prize} of work was taken")


def test_min_vehicles_ignores_the_break_even_entirely():
    """§5.2: under MIN_VEHICLES, count strictly dominates cost.

    One expensive vehicle against two nearly free ones. In money the pair is
    cheaper by almost the whole fixed cost; under MIN_VEHICLES it must still
    lose, because no quantity of a lower tier buys any of a higher one.
    """
    problem = instance(a_van("DEAR", fixed_cost=90_000, cost_per_metre=1),
                       a_van("V2", fixed_cost=1, cost_per_metre=1),
                       a_van("V3", fixed_cost=1, cost_per_metre=1))
    one = plan(problem, {"DEAR": ["O1", "O2"]})
    two = plan(problem, {"V2": ["O1"], "V3": ["O2"]})

    cheap = ObjectiveSpec(mode=Mode.MIN_COST)
    assert score(problem, two, cheap).total < score(problem, one, cheap).total

    strict = ObjectiveSpec(mode=Mode.MIN_VEHICLES)
    assert score(problem, one, strict).total < score(problem, two, strict).total


# --------------------------------------------------------------------------
# FR-33: hired capacity has its own cost structure
# --------------------------------------------------------------------------

def test_a_per_job_cost_is_charged_once_per_order_carried():
    """FR-33 names three hire structures: "per-job, per-day, per-km". Per-day is
    `fixed_cost` and per-km is `cost_per_metre`; per-job had nowhere to live.

    A contractor paid per drop is not the same as one paid per kilometre, and
    amortising the first into the second is exactly what OBJ-4 forbids.
    """
    problem = instance(a_van("CONTRACT", cost_per_order=5_000))

    one = score(problem, plan(problem, {"CONTRACT": ["O1"]}), SPEC)
    two = score(problem, plan(problem, {"CONTRACT": ["O1", "O2"]}), SPEC)

    assert two.values[Tier.OPERATING] - one.values[Tier.OPERATING] == 5_000


def test_spillover_to_hire_happens_only_when_the_own_fleet_runs_out():
    """FR-33: "allow spillover to hire when own fleet is exhausted".

    No special mechanism -- endogenous deployment already does it. An own
    vehicle is sunk cost and a hired one costs a day, so the search takes the
    hire only when the work will not fit without it.
    """
    own_only = instance(a_van("OWN", capacities={"kg": 10}),
                        a_van("HIRE", fixed_cost=80_000, cost_per_metre=1),
                        stops=2)

    fits = score(own_only, plan(own_only, {"OWN": ["O1", "O2"]}), SPEC)
    spilled = score(own_only,
                    plan(own_only, {"OWN": ["O1"], "HIRE": ["O2"]}), SPEC)

    assert fits.total < spilled.total, "hired a day that was not needed"


# --------------------------------------------------------------------------
# FR-36: the allocation block
# --------------------------------------------------------------------------

def test_the_report_covers_every_vehicle_deployed_or_not():
    """FR-36 asks for "every deployed vehicle", and the ones left in the yard
    are the more interesting half of a fleet-mix conversation: a vehicle that
    was never worth deploying is the answer to "can I sell it"."""
    from vrp.allocate import allocate

    problem = instance(a_van("USED", fixed_cost=1_000, cost_per_metre=1),
                       a_van("IDLE", fixed_cost=1_000, cost_per_metre=1))
    report = allocate(problem, plan(problem, {"USED": ["O1", "O2"]}), SPEC)

    assert {entry.vehicle_id for entry in report} == {"USED", "IDLE"}
    assert report["USED"].deployed
    assert not report["IDLE"].deployed


def test_an_idle_vehicle_costs_nothing():
    from vrp.allocate import allocate

    problem = instance(a_van("USED", fixed_cost=1_000, cost_per_metre=1),
                       a_van("IDLE", fixed_cost=1_000, cost_per_metre=1))
    report = allocate(problem, plan(problem, {"USED": ["O1", "O2"]}), SPEC)

    assert report["IDLE"].fixed_cost == 0
    assert report["IDLE"].operating_cost == 0
    assert report["USED"].fixed_cost == 1_000


def test_utilisation_is_reported_on_every_capacity_dimension():
    """FR-36: "utilisation on each capacity dimension". Every dimension, not
    the binding one -- a van full by volume and empty by weight is a different
    purchase decision from one full by both."""
    from vrp.allocate import allocate

    problem = instance(a_van("V1", capacities={"kg": 4, "m3": 100}))
    report = allocate(problem, plan(problem, {"V1": ["O1", "O2"]}), SPEC)

    assert report["V1"].utilisation == {"kg": 500, "m3": 0}, \
        report["V1"].utilisation


def test_duty_used_is_reported_against_duty_available():
    """FR-36's second measure. A fleet at 40% on capacity and 95% on hours is
    short of drivers, not of vans, and one number cannot say that."""
    from vrp.allocate import allocate

    problem = instance(a_van("V1"))
    entry = allocate(problem, plan(problem, {"V1": ["O1"]}), SPEC)["V1"]

    assert entry.duty_available == DAY.end - DAY.start
    assert 0 < entry.duty_used < entry.duty_available


def test_marginal_value_is_the_objective_delta_from_removing_the_vehicle():
    """§7.8: "the objective delta from re-solving with that vehicle removed".

    The re-solver is passed in rather than imported. `allocate` would otherwise
    depend on a solver, and §11.2's independence argument applies to anything
    that judges a plan -- but more practically, a caller with a warm start and
    an iteration budget knows more about how to spend it than this module does.
    """
    from vrp.allocate import marginal_values

    problem = instance(a_van("V1", fixed_cost=1_000, cost_per_metre=1),
                       a_van("V2", fixed_cost=1_000, cost_per_metre=1))
    incumbent = plan(problem, {"V1": ["O1"], "V2": ["O2"]})

    def resolve(reduced: Problem):
        survivor = reduced.vehicles[0].id
        return plan(reduced, {survivor: ["O1", "O2"]})

    values = marginal_values(problem, incumbent, SPEC, resolve)

    assert set(values) == {"V1", "V2"}
    # Removing either leaves one van doing both stops: cheaper here, because
    # the split was paying two fixed costs to drive further. A negative
    # marginal value is a vehicle that is costing more than it saves.
    assert all(value < 0 for value in values.values()), values
    # FR-36 asks for "the marginal cost of removing it", so this is money, and
    # it is checkable by hand. Split: V1 drives D-C1-D (20 km) and V2 drives
    # D-C2-D (40 km), two fixed costs -- 62,000. Merged: one van drives
    # D-C1-C2-D (40 km) on one fixed cost -- 41,000. The vehicle is costing
    # 21,000 more than it saves.
    assert values["V1"] == 41_000 - 62_000, values["V1"]


def test_marginal_value_is_money_rather_than_the_scaled_objective():
    """FR-36's words are "the marginal cost of removing it".

    `Score.total` is scaled so the lexicographic ordering cannot invert, and on
    a four-vehicle instance its magnitude is around 10^17. That number orders
    plans correctly and prices nothing -- a dispatcher reading "marginal value
    -2.4e16" has learnt less than they knew before. Callers wanting the
    ordering can score the two plans themselves.
    """
    from vrp.allocate import marginal_values

    problem = instance(a_van("BUSY", fixed_cost=7_000, cost_per_metre=1),
                       a_van("SPARE", fixed_cost=7_000, cost_per_metre=1))
    incumbent = plan(problem, {"BUSY": ["O1", "O2"]})

    values = marginal_values(
        problem, incumbent, SPEC,
        lambda reduced: (plan(reduced, {"BUSY": ["O1", "O2"]})
                         if any(v.id == "BUSY" for v in reduced.vehicles)
                         else None))

    # Removing the spare changes nothing at all, so the cost delta is zero --
    # and zero is only recognisable when the units are money.
    assert values["SPARE"] == 0, values["SPARE"]


def test_a_re_solve_that_abandons_required_work_reports_no_marginal_value():
    """A plan that drops a priority-0 order is not a cheaper plan. §4.1 makes
    tier 0 must-serve, so a re-solve that leaves one behind has not answered
    the question -- it has changed it, and pricing the difference would offer
    the fleet a saving it is not allowed to take."""
    from vrp.allocate import marginal_values

    problem = instance(a_van("V1", fixed_cost=5_000, cost_per_metre=1),
                       a_van("V2", fixed_cost=5_000, cost_per_metre=1))
    incumbent = plan(problem, {"V1": ["O1"], "V2": ["O2"]})

    # The re-solve quietly abandons O2 rather than admitting defeat.
    abandons = marginal_values(problem, incumbent, SPEC,
                               lambda reduced: plan(reduced, {"V1": ["O1"]}))

    assert abandons == {"V1": None, "V2": None}, abandons


def test_a_vehicle_that_cannot_be_removed_reports_no_marginal_value():
    """When the re-solve cannot serve the work without it, the delta is not a
    number. Reporting a large one would say "expensive"; the truth is
    "load-bearing", and a fleet-sizing sweep must not confuse the two."""
    from vrp.allocate import marginal_values

    problem = instance(a_van("V1", capacities={"kg": 1}),
                       a_van("V2", capacities={"kg": 1}))
    incumbent = plan(problem, {"V1": ["O1"], "V2": ["O2"]})

    values = marginal_values(problem, incumbent, SPEC, lambda reduced: None)

    assert values == {"V1": None, "V2": None}


# --------------------------------------------------------------------------
# The accountant, which is the path the portfolio actually decides on
# --------------------------------------------------------------------------

def test_the_accountant_charges_each_vehicle_its_own_rates_too():
    """`vrp.objective` is the decider and `vrp.evaluator` the accountant, and
    its own docstring says so. But `vrp.portfolio` picks its winner on
    `evaluate(...).total`, so an account that prices a 3.5-tonne van and an
    artic identically decides as well as accounts -- and decides wrongly.

    Fixing `objective` alone left T-44 working everywhere except the path a
    solve actually takes.
    """
    from vrp.evaluator import evaluate

    # Identical fixed costs, so only the per-kilometre rate can separate them.
    problem = instance(a_van("VAN", fixed_cost=1_000, cost_per_metre=1),
                       a_van("ARTIC", fixed_cost=1_000, cost_per_metre=4))

    van = evaluate(problem, {"VAN": ["O1", "O2"]})
    artic = evaluate(problem, {"ARTIC": ["O1", "O2"]})

    assert van.breakdown["vehicles"] == artic.breakdown["vehicles"] == 1_000
    assert artic.total - 1_000 == 4 * (van.total - 1_000), (
        van.total, artic.total)


def test_the_accountant_charges_each_vehicle_its_own_day_rate():
    from vrp.evaluator import evaluate

    problem = instance(a_van("OWN", cost_per_metre=1),
                       a_van("HIRED", fixed_cost=9_000, cost_per_metre=1))

    assert evaluate(problem, {"OWN": ["O1", "O2"]}).breakdown["vehicles"] == 0
    assert evaluate(problem,
                    {"HIRED": ["O1", "O2"]}).breakdown["vehicles"] == 9_000


def test_the_accountant_keeps_its_flat_weights_for_a_fleet_that_prices_nothing():
    """Same fleet-wide fallback, and the same reason: the frozen corpus prices
    no vehicle, and a benchmark whose numbers move because the accounting
    changed is not a benchmark."""
    from vrp.evaluator import ObjectiveWeights, evaluate

    problem = instance(a_van("V1"), stops=1)
    weights = ObjectiveWeights(per_metre=2, per_vehicle=7_000)

    accounted = evaluate(problem, {"V1": ["O1"]}, weights)
    assert accounted.breakdown["vehicles"] == 7_000
    assert accounted.total == 2 * LEG * 2 + 7_000


def test_marginal_value_is_measured_on_one_scale():
    """Both plans must be scored against the same instance, or the delta is
    nonsense.

    `tier_scales` derives its multipliers from the instance -- that is what
    makes the lexicographic ordering hold without hard-coded constants -- so a
    problem with one fewer vehicle has *different* scales. Scoring the re-solve
    against the reduced instance and subtracting the incumbent's score
    therefore subtracts two numbers in different currencies. Measured on a
    four-van fixture it produced marginal values around -7e16 on an objective
    whose whole range was about 10^6.

    Here removing an idle vehicle changes nothing a dispatcher would notice, so
    the honest delta is exactly zero. Under the bug it is astronomical.
    """
    from vrp.allocate import marginal_values

    problem = instance(a_van("BUSY", fixed_cost=1_000, cost_per_metre=1),
                       a_van("IDLE", fixed_cost=1_000, cost_per_metre=1))
    incumbent = plan(problem, {"BUSY": ["O1", "O2"]})

    values = marginal_values(problem, incumbent, SPEC,
                             lambda reduced: plan(reduced, {"BUSY": ["O1", "O2"]})
                             if any(v.id == "BUSY" for v in reduced.vehicles)
                             else None)

    assert values["IDLE"] == 0, values["IDLE"]
    assert values["BUSY"] is None


def test_a_re_solve_that_comes_back_illegal_is_not_priced():
    """The failure mode a `resolve` callback cannot be trusted to catch.

    When every order is required, an engine short of capacity does not return
    an incomplete plan -- it returns a complete, *overloaded* one. Checking
    `unassigned` therefore sees nothing wrong, and the marginal value of a
    genuinely load-bearing vehicle comes back as a tidy saving. Measured on the
    E-44 fixture, a fleet that could not carry the work at all reported every
    vehicle as costing more than it saved.

    INV-9 and §11.2 already say how to settle this: ask the verifier, which
    shares nothing with the engine that produced the plan.
    """
    from vrp.allocate import marginal_values

    problem = instance(a_van("V1", capacities={"kg": 1}, fixed_cost=1_000),
                       a_van("V2", capacities={"kg": 1}, fixed_cost=1_000))
    incumbent = plan(problem, {"V1": ["O1"], "V2": ["O2"]})

    # An engine that "solves" the reduced instance by overloading the survivor.
    values = marginal_values(problem, incumbent, SPEC,
                             lambda reduced: plan(reduced,
                                                  {reduced.vehicles[0].id:
                                                   ["O1", "O2"]}))

    assert values == {"V1": None, "V2": None}, values
