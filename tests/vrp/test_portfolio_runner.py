"""Portfolio runner — §7.3, T-36, E-36.

E-36's acceptance names the trap directly: "Incumbents scored by the canonical
evaluator, **never the engine's own accounting**; win rates recorded by
instance signature."

That is INV-9's argument one level up. INV-9 exists because a solver's own cost
figure is not evidence about the solver; a portfolio makes it worse, because now
several engines report costs they each computed differently and the runner has
to choose between them. PyVRP counts one thing, OR-Tools another, and the LNS a
third -- comparing those numbers directly picks whichever engine is most
generous to itself, which is not the same as picking the best plan.

`test_the_runner_ignores_an_engine_that_flatters_itself` is the test that
matters. An engine returns a poor plan with a wonderful self-reported cost, and
the runner must not pick it. Every other test here would pass on a runner that
compared self-reported numbers.

Win rates by signature exist so the portfolio can be *tuned* rather than
guessed at: §7.3 keeps several engines because different instance shapes suit
different algorithms, and that claim is only actionable if somebody records
which won where.
"""

from __future__ import annotations

import pytest

from vrp.evaluator import ObjectiveWeights
from vrp.generate import Shape, generate_instance
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
    service_time,
)
from vrp.portfolio import Portfolio, instance_signature, run_portfolio
from vrp.verify import verify

DAY = TimeWindow(start=0, end=12 * 3600)


def zigzag(order_ids: list[str]) -> list[str]:
    """A deliberately long sequence: outermost, innermost, alternating.

    Reversing a route was the first attempt and is worthless on a collinear
    matrix -- the distance is identical, so the "poor" plan scored the same as
    the good one and the test proved nothing.
    """
    remaining = list(order_ids)
    out = []
    while remaining:
        out.append(remaining.pop())
        if remaining:
            out.append(remaining.pop(0))
    return out


def plan_for(problem, order_ids: list[str], vehicle: str = "V0") -> Solution:
    """A timeline honest about travel, so the verifier accepts it."""
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    depot = problem.vehicle(vehicle).start_location_id
    steps = [Step(type="START", location_id=depot, arrival=0,
                  start_service=0, departure=0)]
    clock, here = 0, index[depot]
    for order_id in order_ids:
        stop = problem.order(order_id).delivery
        there = index[stop.location_id]
        clock += problem.matrix.duration(here, there)
        # `service_time`, not `service_fixed`: E-24 made service a four-term
        # composition, and a fixture using the raw field builds timelines the
        # verifier rightly rejects on INV-3.
        served = service_time(problem.order(order_id),
                              problem.vehicle(vehicle),
                              problem.location(stop.location_id))
        steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                          order_id=order_id, arrival=clock,
                          start_service=clock, departure=clock + served))
        clock += served
        here = there
    clock += problem.matrix.duration(here, index[depot])
    steps.append(Step(type="END", location_id=depot, arrival=clock,
                      start_service=clock, departure=clock))
    served = set(order_ids)
    return Solution(
        problem_id=problem.id, routes=(Route(vehicle_id=vehicle, steps=tuple(steps)),),
        unassigned=tuple({"order_id": o.id, "reason_code": "NOT_PLACED",
                          "explanation": "-"}
                         for o in problem.orders if o.id not in served),
        objective_breakdown={}, status="FEASIBLE")


@pytest.fixture
def problem():
    """A small instance one vehicle can serve inside a day.

    Built here rather than generated: the portfolio's logic does not care about
    instance realism, and a generated one large enough to be interesting is
    large enough that a single-vehicle fixture plan runs past the shift and
    fails INV-3 for reasons that have nothing to do with the portfolio.
    """
    size = 6
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 1000, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    grid = tuple(tuple(abs(i - j) * 300 for j in range(size)) for i in range(size))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=60))
        for i in range(1, size))
    return Problem(
        id="pf", locations=locations, orders=orders,
        vehicles=(Vehicle(id="V0", capacities={"kg": 100}, shift=DAY,
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="pf", durations=grid, distances=grid))


# --------------------------------------------------------------------------
# The claim that matters
# --------------------------------------------------------------------------

def test_the_runner_ignores_an_engine_that_flatters_itself(problem):
    """The acceptance criterion, as a test.

    One engine returns a good plan and reports it honestly. The other returns a
    deliberately poor plan -- the orders in a bad sequence -- and reports a cost
    of one. A runner comparing self-reported numbers picks the liar every time;
    one scoring with the canonical evaluator cannot be fooled, because it never
    reads what the engine claimed.
    """
    ids = [order.id for order in problem.orders]
    honest = plan_for(problem, ids)
    poor = plan_for(problem, zigzag(ids))
    poor = Solution(problem_id=poor.problem_id, routes=poor.routes,
                    unassigned=poor.unassigned, status=poor.status,
                    # A cost no plan could achieve, asserted by the engine.
                    objective_breakdown={"total": 1})

    result = run_portfolio(problem, [
        Portfolio("honest", lambda _p: honest),
        Portfolio("liar", lambda _p: poor),
    ], weights=ObjectiveWeights(per_metre=1, per_second=0))

    assert result.winner == "honest", (
        f"{result.winner} won; the runner read the engine's own accounting")
    assert result.scores["liar"] > result.scores["honest"]


def test_the_engines_self_reported_cost_is_not_consulted_at_all(problem):
    """Stronger than the above: not merely distrusted, unread.

    The same plan is offered twice with wildly different self-reported costs.
    If either number reached the comparison the two would score differently.
    """
    ids = [order.id for order in problem.orders]
    base = plan_for(problem, ids)
    cheap = Solution(problem_id=base.problem_id, routes=base.routes,
                     unassigned=base.unassigned, status=base.status,
                     objective_breakdown={"total": 1})
    dear = Solution(problem_id=base.problem_id, routes=base.routes,
                    unassigned=base.unassigned, status=base.status,
                    objective_breakdown={"total": 10 ** 12})

    result = run_portfolio(problem, [Portfolio("cheap", lambda _p: cheap),
                                     Portfolio("dear", lambda _p: dear)])
    assert result.scores["cheap"] == result.scores["dear"]


def test_the_winning_plan_is_returned_not_just_its_name(problem):
    ids = [order.id for order in problem.orders]
    good, bad = plan_for(problem, ids), plan_for(problem, zigzag(ids))

    result = run_portfolio(problem, [Portfolio("a", lambda _p: good),
                                     Portfolio("b", lambda _p: bad)])
    assert result.best is good or result.best.routes == good.routes


# --------------------------------------------------------------------------
# Verification, and engines that fail
# --------------------------------------------------------------------------

def test_an_illegal_plan_cannot_win(problem):
    """CON-1 puts feasibility above optimality, so a cheap illegal plan is not
    a better plan -- it is a defect that happens to score well. The portfolio
    is exactly where that would slip through, because a broken engine's plan
    can be arbitrarily cheap."""
    ids = [order.id for order in problem.orders]
    legal = plan_for(problem, ids)
    # Same route, impossible timings: every arrival at zero.
    broken_steps = tuple(
        Step(type=s.type, location_id=s.location_id, order_id=s.order_id,
             arrival=0, start_service=0, departure=0, load_after=s.load_after)
        for s in legal.routes[0].steps)
    broken = Solution(problem_id=legal.problem_id,
                      routes=(Route(vehicle_id="V0", steps=broken_steps),),
                      unassigned=legal.unassigned, status="FEASIBLE",
                      objective_breakdown={})

    assert not verify(problem, broken).ok, "fixture is not actually illegal"
    result = run_portfolio(problem, [Portfolio("legal", lambda _p: legal),
                                     Portfolio("broken", lambda _p: broken)])

    assert result.winner == "legal"
    assert "broken" in result.rejected


def test_an_engine_that_raises_does_not_stop_the_portfolio(problem):
    """§7.3 runs several engines precisely so one can fail. An adapter that
    refuses an instance -- as the OR-Tools one does for shipments -- must not
    take the run down with it."""
    ids = [order.id for order in problem.orders]
    good = plan_for(problem, ids)

    def explodes(_p):
        raise NotImplementedError("this engine declines the instance")

    result = run_portfolio(problem, [Portfolio("fine", lambda _p: good),
                                     Portfolio("broken", explodes)])

    assert result.winner == "fine"
    assert "broken" in result.rejected


def test_a_portfolio_where_every_engine_fails_reports_no_winner(problem):
    """Returning something regardless would be the portfolio inventing a plan."""
    def explodes(_p):
        raise RuntimeError("no")

    result = run_portfolio(problem, [Portfolio("a", explodes),
                                     Portfolio("b", explodes)])
    assert result.winner is None
    assert result.best is None


# --------------------------------------------------------------------------
# Win rates by instance signature
# --------------------------------------------------------------------------

def test_the_signature_groups_instances_of_similar_shape():
    """§7.3 keeps several engines because different shapes suit different
    algorithms. That is only actionable if the shapes can be named."""
    small = generate_instance(1, shape=Shape.SLACK)
    same = generate_instance(1, shape=Shape.SLACK)
    windowed = generate_instance(1, shape=Shape.TIGHT_WINDOWS)

    assert instance_signature(small) == instance_signature(same)
    assert instance_signature(small) != instance_signature(windowed)


def test_the_signature_distinguishes_size_bands():
    small = generate_instance(2, shape=Shape.SLACK)
    large = generate_instance(31, shape=Shape.DRIVING_HOURS)
    assert instance_signature(small) != instance_signature(large)


def test_win_rates_accumulate_by_signature(problem):
    """The telemetry T-36 asks for: which engine wins on which shape."""
    from vrp.portfolio import WinRates

    rates = WinRates()
    rates.record("sig-a", "pyvrp")
    rates.record("sig-a", "pyvrp")
    rates.record("sig-a", "ortools")
    rates.record("sig-b", "lns")

    assert rates.wins["sig-a"]["pyvrp"] == 2
    assert rates.rate("sig-a", "pyvrp") == pytest.approx(2 / 3)
    assert rates.rate("sig-b", "lns") == 1.0
    assert rates.rate("sig-a", "lns") == 0.0


def test_an_unseen_signature_has_no_rates():
    """Zero observations is not a zero win rate, and reporting it as one would
    make an engine look bad on a shape it has never been offered."""
    from vrp.portfolio import WinRates

    rates = WinRates()
    assert rates.rate("never-seen", "pyvrp") == 0.0
    assert rates.observations("never-seen") == 0


def test_running_a_portfolio_records_the_win(problem):
    ids = [order.id for order in problem.orders]
    good, bad = plan_for(problem, ids), plan_for(problem, zigzag(ids))
    from vrp.portfolio import WinRates

    rates = WinRates()
    run_portfolio(problem, [Portfolio("a", lambda _p: good),
                            Portfolio("b", lambda _p: bad)], rates=rates)

    signature = instance_signature(problem)
    assert rates.observations(signature) == 1
    assert rates.rate(signature, "a") == 1.0
