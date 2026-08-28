"""Lexicographic objective with instance-derived scaling — SDD §5, T-13.

§5.1 opens by naming naive weighted sums "the most common modelling error in
production routing", and the reason is worth restating because it is not
obvious: weights tuned on a 200-stop day *silently invert* on a 2,000-stop day.
Nothing fails; the solver simply starts preferring a different thing, and the
first sign is a dispatcher saying the plans have got worse.

So the tiers here are lexicographic. Tier `n` strictly dominates the sum of the
maximum attainable values of every tier beneath it, and that maximum is computed
**from the instance** — never hard-coded, because a constant that dominates on
one instance will not on a larger one.

Two consequences worth knowing:

* The totals are large. A six-customer instance produces scale factors in the
  billions, which is arithmetically fine in Python (unbounded integers) and is
  exactly the overflow risk §5.1 warns about above 10,000 stops. Staged
  optimisation is the specified answer there and is not implemented yet; it is
  `T-13`'s second half and belongs with the solver driver rather than here.
* Comparisons should go through `compare`, which walks levels in order and stops
  at the first difference. It is exact regardless of magnitude, where comparing
  scaled totals relies on the scaling being right.

"Lexicographic" is not quite the whole story, and the exception is §5.2's, not a
shortcut: under `MIN_COST` a vehicle is deployed **iff its fixed cost is repaid
by savings**, which is a trade between fleet and operating cost rather than a
precedence over it. Those two tiers therefore share one level and are compared
in money. `MIN_VEHICLES` is the mode that really does put vehicle count above
distance, and it keeps them separate. See `ObjectiveSpec.levels`.

Placement: Python. This is the definition of "better", which is optimisation
logic and changes as the business changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from itertools import pairwise
from typing import NamedTuple

from vrp.consistency import imbalance
from vrp.model import Problem, Solution


class Tier(IntEnum):
    """§5.1. Lower value means higher priority."""

    HARD = 0            # hard-constraint violations; zero in a FEASIBLE plan
    UNSERVED_P0 = 1     # unserved priority-0 orders
    UNSERVED = 2        # unserved orders by descending priority tier
    FLEET = 3           # fixed cost of deployed vehicles
    OPERATING = 4       # distance, duration, overtime
    SOFT = 5            # earliness / lateness / soft-capacity penalties
    QUALITY = 6         # imbalance, consistency, compactness tie-breakers


class Mode(IntEnum):
    """§5.2 objective modes."""

    MIN_VEHICLES = 0
    MIN_COST = 1
    MIN_DURATION = 2
    MAX_SERVICE = 3
    PRIZE_COLLECTING = 4


@dataclass(frozen=True)
class TierValues:
    """One solution's raw value per tier, before any scaling."""

    values: dict[Tier, int] = field(default_factory=dict)

    def __getitem__(self, tier: Tier) -> int:
        return self.values.get(tier, 0)


@dataclass(frozen=True)
class Score:
    values: TierValues
    total: int


class Rates(NamedTuple):
    """One vehicle's cost structure, resolved. See `ObjectiveSpec.rates`."""

    fixed: int
    per_metre: int
    per_second: int
    per_order: int


@dataclass(frozen=True)
class ObjectiveSpec:
    mode: Mode = Mode.MIN_COST
    vehicle_fixed_cost: int = 50_000
    cost_per_metre: int = 1
    cost_per_second: int = 0

    def levels(self) -> list[tuple[Tier, ...]]:
        """Precedence, as groups. Tiers inside a group are traded against each
        other; groups are strictly ordered.

        This grouping is where §5.2's modes actually live, and it is not a
        detail. `MIN_VEHICLES` says vehicle count *strictly dominates* distance,
        so `FLEET` is its own level. `MIN_COST` says a vehicle is deployed **iff
        its fixed cost is repaid by savings** — which is a trade, not a
        precedence, so `FLEET` and `OPERATING` share a level and are compared in
        money.

        Treating both as lexicographic was the first implementation here, and it
        silently made `MIN_COST` behave as `MIN_VEHICLES`: no vehicle could ever
        pay for itself, because one fewer vehicle always won.

        `MAX_SERVICE` ("tier 2 dominates tier 3-4") is deliberately not a case
        below, because that is already the default arrangement and writing it
        out would suggest a difference that is not there. What actually
        distinguishes the mode is that orders stay required rather than
        droppable, which lives on the order, not in the objective.

        Note `PRIZE_COLLECTING` merges *tier 2* into the money level as well,
        which is what "freely droppable" means. Tier 1 stays above it: a
        priority-0 order is a promise, not a bid.
        """
        head = [(Tier.HARD,), (Tier.UNSERVED_P0,)]
        tail = [(Tier.SOFT,), (Tier.QUALITY,)]
        match self.mode:
            case Mode.MIN_VEHICLES:
                middle = [(Tier.UNSERVED,), (Tier.FLEET,), (Tier.OPERATING,)]
            case Mode.PRIZE_COLLECTING:
                # "Maximise Σ prizes − cost, orders freely droppable". Total
                # prize is a constant of the instance, so maximising
                # `Σ collected − cost` is minimising `forgone + cost` -- one
                # currency, one level. This is the only mode where tier 2 does
                # not dominate cost, and dropping it in with the rest would
                # make prize-collecting unable to ever drop anything.
                middle = [(Tier.UNSERVED, Tier.FLEET, Tier.OPERATING)]
            case _:
                middle = [(Tier.UNSERVED,), (Tier.FLEET, Tier.OPERATING)]
        return head + middle + tail

    def monetary(self, tier: Tier, value: int) -> int:
        """A tier's value in comparable money, for tiers sharing a level.

        Identity now that `Tier.FLEET` carries money rather than a count under
        every mode that shares its level. It stays because `levels()` decides
        which tiers are compared in one currency, and a future tier that is not
        already money needs somewhere to say so.
        """
        return value

    def rates(self, problem: Problem, vehicle_id: str) -> Rates:
        """This vehicle's own cost structure. FR-07, FR-30, FR-33.

        The fallback is **fleet-wide**, not per vehicle: if no vehicle in the
        problem prices anything, the whole fleet uses the spec's rates. That is
        what keeps every instance predating E-21 -- the frozen corpus among
        them -- scoring exactly as it did.

        Per vehicle it would break FR-33. Own capacity is sunk cost and hired
        capacity is not, so a fleet needs to be able to say a vehicle costs
        nothing to deploy; an unstated zero quietly becoming the spec's default
        would turn the own-vs-hire break-even into a comparison of two hire
        prices.
        """
        vehicle = problem.vehicle(vehicle_id)
        if _fleet_prices_itself(problem):
            return Rates(vehicle.fixed_cost, vehicle.cost_per_metre,
                         vehicle.cost_per_second, vehicle.cost_per_order)
        return Rates(self.vehicle_fixed_cost, self.cost_per_metre,
                     self.cost_per_second, 0)

    def tier_bounds(self, problem: Problem) -> dict[Tier, int]:
        """The largest value each tier can take on *this* instance.

        This is what makes the scaling instance-derived. Every bound is a real
        upper bound, deliberately loose rather than tight: a bound that is too
        small breaks the lexicographic guarantee, while one that is too large
        only makes the numbers bigger.
        """
        orders = problem.orders
        vehicles = problem.vehicles
        matrix = problem.matrix
        size = matrix.size
        longest_leg, slowest_leg = matrix.extremes()
        # A route visits at most every location twice (out and back).
        max_distance = longest_leg * max(size, 1) * 2 * max(len(vehicles), 1)
        max_duration = slowest_leg * max(size, 1) * 2 * max(len(vehicles), 1)

        priority_zero = sum(1 for o in orders if o.priority_tier == 0)
        max_prize = sum(max(o.prize, 1) for o in orders)

        return {
            Tier.HARD: max(len(orders) * 4, 1),
            Tier.UNSERVED_P0: max(priority_zero, 1),
            Tier.UNSERVED: max(max_prize, len(orders), 1),
            # Money under every mode but MIN_VEHICLES, where it is a count and
            # the count is the smaller number -- so the money bound covers both.
            Tier.FLEET: max(sum(v.fixed_cost for v in vehicles),
                            len(vehicles) * self.vehicle_fixed_cost,
                            len(vehicles), 1),
            Tier.OPERATING: max(max_distance * self.cost_per_metre
                                + max_duration * self.cost_per_second,
                                max_distance * max((v.cost_per_metre
                                                    for v in vehicles),
                                                   default=0)
                                + max_duration * max((v.cost_per_second
                                                      for v in vehicles),
                                                     default=0)
                                + len(orders) * max((v.cost_per_order
                                                     for v in vehicles),
                                                    default=0), 1),
            Tier.SOFT: max(max_duration, 1),
            # Imbalance is a difference between two routes, so it cannot
            # exceed what one route can reach on any of FR-17's three measures.
            Tier.QUALITY: max(max_distance, max_duration, len(orders), 1),
        }


def tier_scales(problem: Problem, spec: ObjectiveSpec) -> dict[Tier, int]:
    """Multipliers making each tier strictly dominate everything beneath it.

    Built from the bottom up: the lowest tier scales by one, and each tier above
    it by one more than the total value everything below it can reach. That
    "one more" is what makes domination strict rather than merely likely.
    """
    bounds = spec.tier_bounds(problem)
    scales: dict[Tier, int] = {}
    beneath = 0
    for group in reversed(spec.levels()):
        scale = beneath + 1
        # Tiers sharing a level share a scale, so they trade rather than order.
        for tier in group:
            scales[tier] = scale
        beneath += sum(spec.monetary(t, bounds[t]) * scale for t in group)
    return scales


def compare(left: TierValues, right: TierValues, spec: ObjectiveSpec) -> int:
    """Order two solutions. Negative means `left` is better.

    Walks levels in precedence order and returns at the first difference, so the
    result is exact whatever the magnitudes involved — where comparing scaled
    totals would depend on the scaling being right. Within a level the tiers are
    summed in money, which is what makes a vehicle able to pay for itself.

    Takes the spec rather than the scales deliberately. The first version took
    both, which let a caller pass scales built for one mode and a spec for
    another and get a silently wrong ordering — and `compare` does not need the
    scales at all.
    """
    for group in spec.levels():
        difference = sum(spec.monetary(t, left[t]) - spec.monetary(t, right[t])
                         for t in group)
        if difference:
            return -1 if difference < 0 else 1
    return 0


def total(values: TierValues, scales: dict[Tier, int]) -> int:
    """The single scaled number, for engines that need one."""
    return sum(values[tier] * scale for tier, scale in scales.items())


def _churn_term(previous: Solution | None, candidate: Solution) -> int:
    """§8.3's stability term. Stops moved plus ETA drift in seconds, unweighted.

    Unweighted because Tier 6 is already the bottom of §5.1 and cannot outrank
    a metre of driving whatever it holds; the *pricing* choice belongs to the
    caller and lives in `stability.churn_cost`, where an operation can set it.
    """
    if previous is None:
        return 0
    from vrp.stability import churn

    measured = churn(previous, candidate)
    return measured.moved + measured.eta_shift


def _fleet_prices_itself(problem: Problem) -> bool:
    """Whether any vehicle states a cost of its own. See `ObjectiveSpec.rates`."""
    return any(v.fixed_cost or v.cost_per_metre or v.cost_per_second
               or v.cost_per_order for v in problem.vehicles)


def _operating(problem: Problem, spec: ObjectiveSpec, route) -> int:
    """One route's distance and time cost, at its own vehicle's rates."""
    matrix = problem.matrix
    rates = spec.rates(problem, route.vehicle_id)
    carried = sum(1 for step in route.steps if step.order_id)
    distance = duration = 0
    for previous, current in pairwise(route.steps):
        origin = problem.location(previous.location_id).matrix_index
        destination = problem.location(current.location_id).matrix_index
        distance += matrix.distance(origin, destination)
        duration += matrix.duration(origin, destination)
    per_job = carried * rates.per_order
    if spec.mode is Mode.MIN_DURATION:
        # §5.2: cost per second only, distance ignored. The per-job fee is not
        # a distance cost and survives -- it is what the contractor invoices.
        return duration * max(rates.per_second, 1) + per_job
    return (distance * rates.per_metre + duration * rates.per_second + per_job)


def score(problem: Problem, solution: Solution, spec: ObjectiveSpec,
          previous: Solution | None = None) -> Score:
    """Score a solution across the tiers this module can observe.

    `previous` is the plan this one replaces, when there is one. §8.3 asks for
    churn "as a Tier-6 objective term", so it joins the imbalance there. Without
    a previous plan there is no churn to measure and Tier 6 holds imbalance
    alone -- inventing a baseline would rewrite the objective for every static
    solve in the system.

    Tier 6 carries §5.1's "workload imbalance" since T-47 -- the spread of
    duration, distance and stop count across the drivers who actually worked.
    It is the bottom of the hierarchy and stays there: §6.7 makes consistency a
    tie-breaker, never a reason to drive further, so no amount of imbalance can
    outrank a metre of operating cost.

    Tier 5 is the soft-violation total, and it is taken from the canonical
    evaluator rather than recomputed here. Two implementations of "how late is
    this" would be two chances to disagree, and there is no independence
    argument for separating them -- that argument applies to the verifier,
    which shares nothing with either.
    """
    from vrp.evaluator import soft_penalties
    served = {step.order_id for route in solution.routes
              for step in route.steps if step.order_id}
    unassigned = [o for o in problem.orders if o.id not in served]

    unserved_p0 = sum(1 for o in unassigned if o.priority_tier == 0)
    unserved_rest = sum(max(o.prize, 1) for o in unassigned if o.priority_tier != 0)

    # §7.8: "Empty routes are free and removable at zero cost." A vehicle
    # listed in the plan carrying nothing was never deployed.
    working = [route for route in solution.routes
               if any(step.order_id for step in route.steps)]

    # §5.1 Tier 3 is "Sum of fixed_cost(v) over deployed vehicles", and a count
    # is only that sum when every vehicle costs the same -- the homogeneity
    # FR-07 exists to reject. MIN_VEHICLES is the exception by FR-32's own
    # words: there the number of vehicles is minimised before travel cost, so
    # the tier holds the count it names.
    fleet = (len(working) if spec.mode is Mode.MIN_VEHICLES
             else sum(spec.rates(problem, route.vehicle_id)[0]
                      for route in working))

    operating = sum(_operating(problem, spec, route) for route in working)

    soft = sum(sum(soft_penalties(problem, step))
               for route in solution.routes for step in route.steps)

    values = TierValues({
        Tier.HARD: 0,                 # the verifier owns this; see §11.2
        Tier.UNSERVED_P0: unserved_p0,
        Tier.UNSERVED: unserved_rest,
        Tier.FLEET: fleet,
        Tier.OPERATING: operating,
        Tier.SOFT: soft,
        Tier.QUALITY: imbalance(problem, solution) + _churn_term(previous,
                                                                  solution),
    })
    return Score(values=values, total=total(values, tier_scales(problem, spec)))
