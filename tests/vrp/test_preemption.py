"""Urgent work displacing planned work — FR-27, DYN-5, T-77.

`UC-044` is the operation and states the requirement in one line: "A P1 gas
escape preempts work already in progress, requiring the plan to be
interruptible mid-route." The arriving job is not an insertion looking for
slack. If there is no slack it takes somebody else's, and the only questions
are whose and whether anybody is told.

Whose is already answered elsewhere and deliberately not re-answered here.
`FR-13`'s tiers and `FR-25`'s sources say what may be given up for what, and
`T-75` priced them; a preemption that chose its own victims would be a second,
quieter priority scheme competing with the declared one.

Whether anybody is told is this module's subject. `FR-27` requires displaced
work to be "re-planned rather than silently dropped", and until `T-77` the
delta reported a reassignment and an abandonment as the same number -- so a
plan that moved three stops between drivers and a plan that gave up on three
customers were equally stable by the only measure anyone had.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from vrp.bench import fixtures
from vrp.model import Order, StopSpec, TimeWindow
from vrp.solve.pyvrp_adapter import solve
from vrp.triggers import preempt

DAY = TimeWindow(start=0, end=12 * 3600)


def routine(count: int, prize: int = 500_000) -> tuple[Order, ...]:
    """Declinable work: a prize is the price at which giving it up is fair."""
    return tuple(
        Order(id=f"R{i}", kind="JOB", quantities={"kg": 10}, priority_tier=2,
              prize=prize,
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=600))
        for i in range(1, count + 1))


def emergency(stop: str = "C6") -> Order:
    """A statutory obligation: no price, so no price at which it is declined."""
    return Order(id="GAS", kind="JOB", quantities={"kg": 10}, priority_tier=0,
                 priority_source="STATUTORY",
                 delivery=StopSpec(location_id=stop, time_windows=(DAY,),
                                   service_fixed=600))


def morning(vehicles: int = 1, capacity: int = 40):
    """A van loaded to its limit, and the day's plan for it."""
    work = routine(6)
    problem = fixtures.instance(
        "preempt", work,
        tuple(fixtures.van(f"V{n}", capacities={"kg": capacity})
              for n in range(1, vehicles + 1)))
    return problem, solve(problem, iterations=400, seed=0)


def served(solution) -> set[str]:
    return {step.order_id for route in solution.routes
            for step in route.steps if step.order_id}


def engine(problem):
    return solve(problem, iterations=400, seed=0)


def test_an_emergency_is_planned_even_though_the_van_was_full():
    """Breaks: uniform priority. Without preemption the gas escape queues
    behind six routine jobs because the van has no room, which is the correct
    answer to a question nobody asked."""
    problem, plan = morning()
    before = served(plan)
    assert len(before) < 6, "the van is deliberately too small for the round"

    arrived = replace(problem, orders=problem.orders + (emergency(),))
    response = preempt(arrived, plan, "GAS", now=0, solve=engine)

    assert "GAS" in served(response.plan), (
        "a statutory obligation carries no prize, so there is no price at "
        "which declining it is acceptable")


def test_what_the_emergency_displaced_is_named_and_attributed():
    """FR-27: displaced work is "re-planned rather than silently dropped". An
    order that simply stops appearing is the silent drop it forbids."""
    problem, plan = morning()
    before = served(plan)

    arrived = replace(problem, orders=problem.orders + (emergency(),))
    response = preempt(arrived, plan, "GAS", now=0, solve=engine)

    displaced = set(response.delta.displaced)
    assert displaced, "the van was full; something had to give"
    assert displaced <= before, "only work that was planned can be displaced"

    reported = {row["order_id"]: row for row in response.plan.unassigned}
    for order_id in displaced:
        assert reported[order_id]["reason_code"] == "PREEMPTED"
        assert "GAS" in reported[order_id]["explanation"], (
            "the report has to name what took the slot, or a dispatcher "
            "cannot tell a preemption from a solver that gave up")


def test_work_that_was_never_planned_is_not_blamed_on_the_preemption():
    """Attribution has to be narrow to be worth anything: the round was
    already larger than the van before the emergency arrived."""
    problem, plan = morning()
    never_planned = {o.id for o in problem.orders} - served(plan)
    assert never_planned, "the fixture's point is a round that does not fit"

    arrived = replace(problem, orders=problem.orders + (emergency(),))
    response = preempt(arrived, plan, "GAS", now=0, solve=engine)

    reported = {row["order_id"]: row["reason_code"]
                for row in response.plan.unassigned}
    for order_id in never_planned - set(response.delta.displaced):
        assert reported.get(order_id) != "PREEMPTED", (
            f"{order_id} was already unplanned before the emergency; calling "
            "it preempted would credit the gas escape with a shortfall that "
            "was there all morning")


def test_a_reassignment_and_an_abandonment_are_different_facts():
    """`Delta.moved` reports both, with `None` standing in for "no vehicle".
    Counting them together said a plan reshuffling three stops and a plan
    abandoning three were equally stable."""
    # One van, so the emergency has to take somebody's slot: `displaced` is
    # non-empty and the split has something to separate. With two vans the
    # displaced set is empty and every assertion below holds vacuously, which
    # is how the first version of this test passed while `reassigned` was
    # perturbed to return everything.
    problem, plan = morning(vehicles=1)
    arrived = replace(problem, orders=problem.orders + (emergency(),))

    response = preempt(arrived, plan, "GAS", now=0, solve=engine)
    delta = response.delta

    assert delta.displaced, "the split needs something to separate"
    assert set(delta.reassigned) | set(delta.displaced) == set(delta.moved)
    assert not (set(delta.reassigned) & set(delta.displaced)), (
        "a stop is either still being done by somebody else, or not being "
        "done; it cannot be both")
    assert delta.churn == len(delta.reassigned) + len(delta.displaced)
    for order_id in delta.reassigned:
        assert order_id in served(response.plan), (
            "reassigned means a different driver, not no driver")
    for order_id in delta.displaced:
        assert order_id not in served(response.plan)


def test_a_second_van_re_plans_the_displaced_work_rather_than_dropping_it():
    """FR-27's preference, in order: re-plan it, and only report it dropped
    when there is nowhere for it to go."""
    one_van, plan_one = morning(vehicles=1)
    two_vans, plan_two = morning(vehicles=2)

    dropped_with_one = preempt(
        replace(one_van, orders=one_van.orders + (emergency(),)),
        plan_one, "GAS", now=0, solve=engine).delta.displaced
    dropped_with_two = preempt(
        replace(two_vans, orders=two_vans.orders + (emergency(),)),
        plan_two, "GAS", now=0, solve=engine).delta.displaced

    assert dropped_with_one, "one van cannot absorb an emergency for free"
    assert len(dropped_with_two) < len(dropped_with_one), (
        "with somewhere to put it, displaced work is re-planned; dropping it "
        "is the last resort, not the mechanism")


def test_preemption_refuses_what_belongs_to_reoptimise():
    """An order already in the plan has not just arrived, and moving placed
    work is a different operation with a different scope."""
    problem, plan = morning()
    planned = next(iter(served(plan)))

    with pytest.raises(ValueError, match="already planned"):
        preempt(problem, plan, planned, now=0, solve=engine)
    with pytest.raises(ValueError, match="not an order in this problem"):
        preempt(problem, plan, "NOT-AN-ORDER", now=0, solve=engine)


def test_an_arriving_order_nobody_must_serve_is_reported_not_silently_lost():
    """`preempt` is for work that outranks something. Handed a declinable
    order it cannot guarantee placement, and says so rather than returning a
    plan that quietly ignored it."""
    problem, plan = morning()
    declinable = Order(id="MAYBE", kind="JOB", quantities={"kg": 10},
                       priority_tier=2, prize=1,
                       delivery=StopSpec(location_id="C6",
                                         time_windows=(DAY,),
                                         service_fixed=600))
    arrived = replace(problem, orders=problem.orders + (declinable,))

    with pytest.raises(RuntimeError, match="arrived urgent and was not planned"):
        preempt(arrived, plan, "MAYBE", now=0, solve=engine)
