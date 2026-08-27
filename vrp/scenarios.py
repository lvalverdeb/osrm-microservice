"""Tactical fleet sizing over a scenario set — FR-34, US-4, §7.8, T-46.

FR-34: "Given a scenario set of historical or generated demand days, recommend
fleet composition minimising expected total cost (acquisition/lease + routing +
expected failure/recourse cost)."

§7.8 gives the shape: fleet composition is the first-stage decision, routing
over sampled demand days is the second-stage recourse, and the method is
scenario decomposition -- enumerate candidate mixes, evaluate each over the
scenario set with the operational solver at reduced budget, report a
cost/service Pareto front.

It also states, as a fact rather than a caution, that "a deterministic
average-day sizing systematically under-fleets". That is the whole argument for
a sweep. A mean day never happens; size for it and every day above it spills
work, and the spilled work costs more than the van would have. `average_day`
exists so the claim can be measured rather than repeated -- see
`test_sizing_on_the_average_day_under_fleets`.

**Three costs, never one.** AC-4.1 asks for "fixed + variable + failure cost per
mix" separately, and the separation is the point: a mix can be cheapest
precisely by abandoning work, and a single total makes that indistinguishable
from routing well. AC-4.2 adds a service-level column for the same reason.

**The operational solver is injected**, as it is for `marginal_values`. A
planning module has no business owning an engine, 30 days x 10 mixes is 300
routings that must run unattended, and the caller is the one who knows what
budget to give each.

Placement: **Python**, per criterion 2. This orchestrates the domain model, the
evaluator and a solver, and it changes whenever any of them does.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from vrp.evaluator import evaluate
from vrp.model import Order, Problem, Vehicle

# Service level in parts per thousand, for CON-4's reason: 11 of 12 orders is
# not representable as an integer percentage and this project does not
# accumulate floats.
FULL = 1000

Solve = Callable[[Problem], dict[str, list[str]]]
Recovery = Callable[[Problem, Order], int]


@dataclass(frozen=True)
class Scenario:
    """One demand day. §7.8's "sampled demand days"."""

    id: str
    orders: tuple[Order, ...]

    @property
    def order_ids(self) -> tuple[str, ...]:
        return tuple(order.id for order in self.orders)


@dataclass(frozen=True)
class Mix:
    """A candidate fleet composition. §7.8's first-stage decision."""

    name: str
    vehicles: tuple[Vehicle, ...]


@dataclass(frozen=True)
class MixResult:
    """One mix, measured across the whole scenario set. AC-4.1, AC-4.2."""

    mix: str
    days: int
    fixed_cost: int
    routing_cost: int
    failure_cost: int
    served: int
    offered: int

    @property
    def total(self) -> int:
        return self.fixed_cost + self.routing_cost + self.failure_cost

    @property
    def service_level(self) -> int:
        """Orders served within window, in parts per thousand. AC-4.2."""
        if self.offered <= 0:
            return FULL
        return self.served * FULL // self.offered


def generate_scenarios(problem: Problem, days: int, seed: int,
                       typical: int | None = None,
                       spread: int = 40) -> tuple[Scenario, ...]:
    """A scenario set: `days` demand days sampled around a typical day.

    Args:
        problem: the instance whose orders form the demand pool.
        days: how many days to generate. AC-4.1 wants at least 30.
        seed: same seed, same set (CON-4). A sizing recommendation nobody can
            reproduce is an opinion.
        typical: orders on an ordinary day. Defaults to two thirds of the pool,
            leaving room above as well as below.
        spread: percent by which a day's order count may vary either way.

    Returns:
        One `Scenario` per day, each a subset of the pool.

    Days vary in *how much* work arrives, which is the uncertainty fleet sizing
    exists to price. Sampling without replacement from the pool keeps every day
    a real instance of the same problem -- same locations, same matrix, same
    windows -- so differences between days are demand and nothing else.

    The pool is deliberately larger than a typical day. A first version drew
    from the whole pool and capped each day at its size, which meant demand
    could only ever fall: there were no busy days, the peak equalled the mean,
    and §7.8's under-fleeting claim had nothing to bite on. A scenario set with
    no upside is not a scenario set.
    """
    rng = random.Random(seed)
    pool = list(problem.orders)
    typical = typical if typical is not None else max(1, len(pool) * 2 // 3)
    low = max(1, typical - typical * spread // 100)
    high = min(len(pool), typical + typical * spread // 100)

    scenarios = []
    for day in range(days):
        count = rng.randint(low, high)
        chosen = sorted(rng.sample(pool, count), key=lambda order: order.id)
        scenarios.append(Scenario(id=f"day-{day:03d}", orders=tuple(chosen)))
    return tuple(scenarios)


def average_day(scenarios: Sequence[Scenario]) -> Scenario:
    """The single day a deterministic sizing would use. §7.8's counter-example.

    Carries the mean order count, drawn from the most frequently occurring
    orders so it stays a real instance rather than a fractional one. It is
    built here rather than in a test because §7.8's claim -- that sizing this
    way under-fleets -- is only worth anything if the average day is honestly
    average.
    """
    if not scenarios:
        raise ValueError("an average needs at least one scenario")
    mean = round(sum(len(day.orders) for day in scenarios) / len(scenarios))

    seen: dict[str, tuple[int, Order]] = {}
    for day in scenarios:
        for order in day.orders:
            count, _ = seen.get(order.id, (0, order))
            seen[order.id] = (count + 1, order)
    ranked = sorted(seen.values(), key=lambda pair: (-pair[0], pair[1].id))
    orders = sorted((order for _, order in ranked[:mean]),
                    key=lambda order: order.id)
    return Scenario(id="average", orders=tuple(orders))


def sweep(problem: Problem, mixes: Sequence[Mix],
          scenarios: Sequence[Scenario], solve: Solve,
          recovery: Recovery | None = None) -> list[MixResult]:
    """Evaluate every mix over every day. §7.8's scenario decomposition.

    Args:
        problem: the base instance -- locations, matrix, and the order pool.
        mixes: candidate fleet compositions.
        scenarios: the demand days to evaluate against.
        solve: the operational solver, called once per mix per day. §7.8
            expects it at reduced budget; the caller sets that.
        recovery: what one spilled order costs, defaulting to the dedicated
            depot round trip §7.8 describes. Injected because that default is
            a floor rather than a price: a real failed delivery carries the
            redelivery, the admin and whatever the service agreement says, and
            an operator who knows those figures should not have to accept the
            drive as a stand-in for them. The choice moves the answer -- see
            `test_a_dearer_failure_makes_the_average_day_under_fleet`.

    Returns:
        One `MixResult` per mix, in the order the mixes were given.

    Raises:
        ValueError: if the scenario set is empty. A sweep over no days would
            report every mix as perfect and free.
    """
    if not scenarios:
        raise ValueError("a sweep needs at least one scenario day")
    price = recovery or _recovery_cost
    return [_measure(problem, mix, scenarios, solve, price) for mix in mixes]


def _measure(problem: Problem, mix: Mix, scenarios: Sequence[Scenario],
             solve: Solve, recovery: Recovery) -> MixResult:
    routing = failure = served = offered = 0
    for scenario in scenarios:
        day = replace(problem, orders=scenario.orders, vehicles=mix.vehicles)
        assignment = solve(day)
        placed = {order_id for ids in assignment.values() for order_id in ids}

        offered += len(scenario.orders)
        served += len(placed)
        routing += evaluate(day, assignment).breakdown["distance"]
        failure += sum(recovery(day, order)
                       for order in scenario.orders if order.id not in placed)

    count = len(scenarios)
    return MixResult(
        mix=mix.name, days=count,
        # Acquisition or lease is owed whether or not the vehicle moves, so it
        # is charged per day rather than per deployment -- that is what makes
        # this a *tactical* cost and not the operational one T-44 reports.
        fixed_cost=sum(v.fixed_cost for v in mix.vehicles) * count,
        routing_cost=routing, failure_cost=failure,
        served=served, offered=offered)


def _recovery_cost(problem: Problem, order: Order) -> int:
    """§7.8's route failure: "a vehicle running out of capacity and needing a
    recovery trip".

    Priced as the dedicated depot round trip that trip actually is, rather than
    as a flat penalty. A flat figure would make failure cost depend on how many
    orders spilled and not at all on where they were, which is the wrong shape:
    a missed drop across town is not a missed drop next door, and a sizing that
    cannot tell them apart will buy vans for the wrong depot.
    """
    stop = order.delivery or order.pickup
    depot = problem.vehicles[0].start_location_id if problem.vehicles else None
    if depot is None:
        return 0
    here = problem.location(depot).matrix_index
    there = problem.location(stop.location_id).matrix_index
    rate = max((v.cost_per_metre for v in problem.vehicles), default=1) or 1
    return 2 * problem.matrix.distance(here, there) * rate


def pareto(results: Sequence[MixResult]) -> list[MixResult]:
    """The non-dominated mixes, cheapest first. §7.8's "cost/service Pareto front".

    A mix is dominated when another is at least as cheap *and* at least as good
    on service, and strictly better on one of them. Those are not trade-offs an
    analyst should be asked to weigh; they are mistakes, and showing them
    invites someone to pick one.
    """
    front = [
        candidate for candidate in results
        if not any(_dominates(other, candidate) for other in results)
    ]
    return sorted(front, key=lambda r: (r.total, -r.service_level, r.mix))


def _dominates(left: MixResult, right: MixResult) -> bool:
    return (left.total <= right.total
            and left.service_level >= right.service_level
            and (left.total, left.service_level)
            != (right.total, right.service_level))


def recommend(problem: Problem, mixes: Sequence[Mix],
              scenarios: Sequence[Scenario], solve: Solve,
              recovery: Recovery | None = None) -> Mix:
    """The mix FR-34 asks for: least expected total cost over the scenario set.

    Chosen from the Pareto front rather than from the raw results, so the
    recommendation is never a mix that something else beats outright. Where two
    share a total, the front's ordering prefers the better service level.
    """
    front = pareto(sweep(problem, mixes, scenarios, solve, recovery))
    best = min(front, key=lambda r: (r.total, -r.service_level, r.mix))
    return next(mix for mix in mixes if mix.name == best.mix)
