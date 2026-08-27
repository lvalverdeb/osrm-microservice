"""Scenario engine and tactical fleet sizing — FR-34, US-4, §7.8, T-46, E-46.

FR-34: "Given a scenario set of historical or generated demand days, recommend
fleet composition minimising expected total cost (acquisition/lease + routing +
expected failure/recourse cost)."

§7.8 sets out the shape: "first-stage decisions are fleet composition... second-
stage recourse is routing over sampled demand days, including the cost of route
failure (a vehicle running out of capacity and needing a recovery trip). Solve
by scenario decomposition: enumerate candidate mixes, evaluate each over the
scenario set with the operational solver at reduced budget, and report a
cost/service Pareto front."

It also makes a claim worth testing rather than quoting: "a deterministic
average-day sizing systematically under-fleets". A mean day is not a typical
day. Size for the mean and every day above it fails, and the failures cost more
than the van would have -- which is the entire reason this is a scenario sweep
rather than one solve on an average.

Two things shape these tests:

**The solver is injected.** `sweep` takes the routing callable, as
`marginal_values` does. It keeps a planning module free of an engine, it makes
30 x 10 = 300 routings run in test time, and it is honest about what the sweep
is: an orchestration over whatever operational solver the caller trusts.

**Service level is not derived from cost.** AC-4.2 asks for it explicitly --
"results include a service-level column (orders served within window), not cost
alone" -- because a mix can be cheapest precisely by serving less.
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
)
from vrp.scenarios import (
    FULL,
    Mix,
    Scenario,
    average_day,
    generate_scenarios,
    pareto,
    recommend,
    sweep,
)
from vrp.scenarios import _recovery_cost as _round_trip

DAY = TimeWindow(start=0, end=10 * 3600)
LEG = 5_000


def base_problem(stops: int = 12) -> Problem:
    size = stops + 1
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 200, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    grid = tuple(tuple(0 if i == j else LEG + abs(i - j) * 500
                       for j in range(size)) for i in range(size))
    times = tuple(tuple(cell // 10 for cell in row) for row in grid)
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 10},
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=120))
        for i in range(1, stops + 1))
    return Problem(id="sizing", locations=locations, orders=orders,
                   vehicles=(a_van("V1"),),
                   matrix=TravelMatrix(version="s", durations=times,
                                       distances=grid))


def a_van(vehicle_id: str, capacity: int = 60, day_rate: int = 40_000) -> Vehicle:
    return Vehicle(id=vehicle_id, capacities={"kg": capacity}, shift=DAY,
                   start_location_id="D", end_location_id="D",
                   fixed_cost=day_rate, cost_per_metre=1)


def mix_of(count: int, capacity: int = 60, day_rate: int = 40_000) -> Mix:
    return Mix(name=f"{count}x{capacity}kg",
               vehicles=tuple(a_van(f"V{n}", capacity, day_rate)
                              for n in range(1, count + 1)))


def first_fit(problem: Problem) -> dict[str, list[str]]:
    """A stand-in operational solver: fill each van, spill the rest.

    Deliberately dumb and deliberately *deterministic*. The sweep's job is to
    orchestrate a solver, not to be one, and a stub that leaves work behind
    when the fleet is short is exactly what exercises the recourse path.
    """
    assignment: dict[str, list[str]] = {v.id: [] for v in problem.vehicles}
    loads = dict.fromkeys(assignment, 0)
    for order in problem.orders:
        need = order.quantities["kg"]
        for vehicle in problem.vehicles:
            room = vehicle.capacities["kg"] - loads[vehicle.id]
            if need <= room:
                assignment[vehicle.id].append(order.id)
                loads[vehicle.id] += need
                break
    return assignment


# --------------------------------------------------------------------------
# The scenario set
# --------------------------------------------------------------------------

def test_a_scenario_set_has_the_days_it_was_asked_for():
    days = generate_scenarios(base_problem(), days=30, seed=0)

    assert len(days) == 30
    assert all(isinstance(day, Scenario) for day in days)


def test_the_days_differ_from_one_another():
    """A scenario set whose days are identical is one day counted thirty
    times, and it would make the sweep's whole premise vacuous."""
    days = generate_scenarios(base_problem(), days=30, seed=0)
    sizes = {len(day.orders) for day in days}

    assert len(sizes) > 1, sizes


def test_some_days_are_busier_than_a_typical_one():
    """The half a first version got wrong, and it made §7.8's whole argument
    untestable.

    Drawing from the whole pool and capping each day at its size lets demand
    only fall. The peak day then equals the mean day, no fleet is ever short,
    and "average-day sizing under-fleets" has nothing to bite on. A scenario
    set with no upside is not a scenario set.
    """
    days = generate_scenarios(base_problem(stops=18), days=30, seed=0,
                              typical=12)
    sizes = [len(day.orders) for day in days]

    assert max(sizes) > 12, sizes
    assert min(sizes) < 12, sizes


def test_the_same_seed_gives_the_same_days():
    """CON-4. A sizing recommendation nobody can reproduce is an opinion."""
    left = generate_scenarios(base_problem(), days=30, seed=7)
    right = generate_scenarios(base_problem(), days=30, seed=7)

    assert [d.order_ids for d in left] == [d.order_ids for d in right]


def test_a_different_seed_gives_different_days():
    left = generate_scenarios(base_problem(), days=30, seed=1)
    right = generate_scenarios(base_problem(), days=30, seed=2)

    assert [d.order_ids for d in left] != [d.order_ids for d in right]


# --------------------------------------------------------------------------
# AC-4.1: the sweep
# --------------------------------------------------------------------------

def test_the_sweep_covers_every_mix_over_every_day():
    problem = base_problem()
    days = generate_scenarios(problem, days=30, seed=0)
    mixes = [mix_of(n) for n in range(1, 11)]

    results = sweep(problem, mixes, days, first_fit)

    assert len(results) == 10
    assert {r.mix for r in results} == {m.name for m in mixes}
    assert all(r.days == 30 for r in results)


def test_cost_is_reported_in_the_three_parts_ac_4_1_asks_for():
    """"reports fixed + variable + failure cost per mix". Three numbers, not a
    total: a mix that is cheap because it abandons work looks identical to one
    that is cheap because it routes well, until they are separated."""
    problem = base_problem()
    days = generate_scenarios(problem, days=30, seed=0)

    thin, thick = sweep(problem, [mix_of(1), mix_of(6)], days, first_fit)

    assert thin.fixed_cost < thick.fixed_cost
    assert thin.failure_cost > thick.failure_cost
    assert all(r.total == r.fixed_cost + r.routing_cost + r.failure_cost
               for r in (thin, thick))


def test_the_lease_is_charged_for_every_day_in_the_set():
    """AC-4.1 asks for the cost to be *reported*, not merely ranked.

    A day rate multiplied by the wrong number of days ranks mixes identically
    -- the factor is the same for all of them and cancels -- so no comparison
    catches it. It is still a figure an analyst would take to a board, wrong by
    the length of the scenario set. Acquisition or lease is owed whether or not
    the vehicle moves, which is what makes it a tactical cost rather than the
    operational one T-44 reports.
    """
    problem = base_problem()
    days = generate_scenarios(problem, days=30, seed=0)

    result, = sweep(problem, [mix_of(3)], days, first_fit)

    assert result.fixed_cost == 3 * 40_000 * 30, result.fixed_cost


def test_service_level_is_reported_and_not_inferred_from_cost():
    """AC-4.2: "not cost alone"."""
    problem = base_problem()
    days = generate_scenarios(problem, days=30, seed=0)

    thin, thick = sweep(problem, [mix_of(1), mix_of(6)], days, first_fit)

    assert thin.served < thin.offered
    assert thick.service_level > thin.service_level
    assert 0 <= thin.service_level <= 1000


def test_a_fleet_large_enough_never_fails():
    problem = base_problem()
    days = generate_scenarios(problem, days=30, seed=0)

    ample, = sweep(problem, [mix_of(12)], days, first_fit)

    assert ample.failure_cost == 0
    assert ample.service_level == 1000


def test_the_sweep_is_deterministic():
    problem = base_problem()
    days = generate_scenarios(problem, days=30, seed=0)
    mixes = [mix_of(n) for n in (2, 4, 6)]

    first = sweep(problem, mixes, days, first_fit)
    again = sweep(problem, mixes, days, first_fit)

    assert [r.total for r in first] == [r.total for r in again]


# --------------------------------------------------------------------------
# The Pareto front
# --------------------------------------------------------------------------

def test_the_front_keeps_only_non_dominated_mixes():
    """Dominated means: something else is at least as cheap *and* at least as
    good on service. Those mixes are not trade-offs, they are mistakes."""
    problem = base_problem()
    days = generate_scenarios(problem, days=30, seed=0)
    results = sweep(problem, [mix_of(n) for n in range(1, 11)], days, first_fit)

    front = pareto(results)

    assert front
    for kept in front:
        assert not any(other.total <= kept.total
                       and other.service_level >= kept.service_level
                       and (other.total, other.service_level)
                       != (kept.total, kept.service_level)
                       for other in results), kept


def test_every_dropped_mix_is_dominated_by_something_on_the_front():
    """The other half. A front that drops a mix nobody beats has thrown away
    an option the analyst was entitled to see."""
    problem = base_problem()
    days = generate_scenarios(problem, days=30, seed=0)
    results = sweep(problem, [mix_of(n) for n in range(1, 11)], days, first_fit)

    front = pareto(results)
    for dropped in [r for r in results if r not in front]:
        assert any(kept.total <= dropped.total
                   and kept.service_level >= dropped.service_level
                   for kept in front), dropped


def test_the_front_is_ordered_by_cost():
    """So it reads as a curve rather than a set."""
    problem = base_problem()
    days = generate_scenarios(problem, days=30, seed=0)
    front = pareto(sweep(problem, [mix_of(n) for n in range(1, 11)], days,
                         first_fit))

    assert [r.total for r in front] == sorted(r.total for r in front)


# --------------------------------------------------------------------------
# §7.8's claim: the average day under-fleets
# --------------------------------------------------------------------------

def peaky() -> tuple:
    """A pool larger than a typical day, and vans small enough that the peak
    needs more of them than the mean does."""
    problem = base_problem(stops=18)
    days = generate_scenarios(problem, days=30, seed=0, typical=12)
    mixes = [mix_of(n, capacity=30, day_rate=12_000) for n in range(1, 8)]
    return problem, days, mixes


def test_the_average_day_overstates_what_the_fleet_will_actually_serve():
    """§7.8: "a deterministic average-day sizing systematically under-fleets".

    The first half of the claim, and it holds without any assumption about what
    a failure costs. Both methods here choose the same four vans -- the fifth
    does not pay for itself when a spilled order costs only a recovery trip --
    but the average day reports that fleet serving everything, and across the
    distribution it serves 93.9%. The fleet is not wrong; the *belief about it*
    is, and that is what an average-day sizing buys you.
    """
    problem, days, mixes = peaky()

    across = sweep(problem, mixes, days, first_fit)
    average = sweep(problem, mixes, (average_day(days),), first_fit)
    chosen = recommend(problem, mixes, days, first_fit).name

    here = next(r for r in across if r.mix == chosen)
    there = next(r for r in average if r.mix == chosen)

    assert there.service_level == FULL
    assert here.service_level < FULL, here.service_level


def test_a_dearer_failure_makes_the_average_day_under_fleet():
    """The second half, and the condition §7.8 leaves implicit.

    A missed delivery does not cost one drive. It costs the redelivery, the
    admin, and whatever the service agreement says, and priced that way the
    marginal van starts paying for itself on the days it is needed -- days the
    mean cannot see. Then the two methods disagree about the fleet itself.

    Measured across recourse multipliers: at 1x both choose four vans; from 3x
    upward the distribution chooses five and the average day still chooses
    four. Stating the condition is the honest form of the claim. Where recourse
    is genuinely cheap, under-fleeting is not an error -- it is the right
    answer, and both methods reach it.
    """
    problem, days, mixes = peaky()
    dear = lambda instance, order: _round_trip(instance, order) * 3

    across = recommend(problem, mixes, days, first_fit, dear)
    average = recommend(problem, mixes, (average_day(days),), first_fit, dear)

    assert len(average.vehicles) < len(across.vehicles), (
        average.name, across.name)


def test_the_recommendation_is_the_cheapest_on_the_front():
    problem = base_problem()
    days = generate_scenarios(problem, days=30, seed=0)
    mixes = [mix_of(n) for n in range(1, 11)]

    chosen = recommend(problem, mixes, days, first_fit)
    front = pareto(sweep(problem, mixes, days, first_fit))

    assert chosen.name == min(front, key=lambda r: r.total).mix


def test_the_average_day_carries_the_mean_demand():
    """The control for the claim above: if the average day were not actually
    average, the under-fleeting result would be an artefact of a bad fixture."""
    problem = base_problem()
    days = generate_scenarios(problem, days=30, seed=0)

    mean = sum(len(day.orders) for day in days) / len(days)
    assert abs(len(average_day(days).orders) - mean) <= 1


def test_a_sweep_needs_at_least_one_day():
    with pytest.raises(ValueError, match="scenario"):
        sweep(base_problem(), [mix_of(1)], (), first_fit)
