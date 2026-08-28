"""The historical replayer — DYN-6, AC-3.2, §8.1, T-53.

DYN-6: "Simulator/replayer -- replays historical days epoch-by-epoch to evaluate
policies offline". AC-3.2: "The dispatch policy is selectable and its expected
cost is reported against a greedy-dispatch baseline over a replayed historical
day."

This is the gate the rest of Slice 5 stands on. T-54's ICD policy must "beat
greedy and lazy on the replay corpus"; T-55's prize-collecting must be
"comparable or better than ICD". Neither claim means anything without a
measurement both are made against, which is why §8.2's baselines were built
first and the replayer before the policies that need it.

**What makes it a replayer rather than a loop.** §8.1's premise is that requests
are not all known at the start: "at each epoch the agent observes the requests
known so far". Hand every request to epoch 0 and this is a static solve wearing
a costume -- every policy scores identically because there is nothing left to
consolidate. So a `Day` is an arrival schedule, and an order is invisible until
its epoch.

**Determinism is in the definition of done.** Ninety days replayed the same way
twice must agree exactly, for CON-4's reason: a policy comparison nobody can
reproduce is an anecdote rather than evidence.

**Cost is not the whole report.** A policy can be cheap by being late, so the
report carries how many epochs it dispatched in and how often AC-3.1 had to
overrule it -- "losing money" and "causing service failures" being different
findings.

Placement: **Python**, per criterion 2. This composes the epoch controller, the
policies and the evaluator; it changes whenever any of them does.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from vrp.epochs import Policy, classify, decide, epochs
from vrp.evaluator import evaluate
from vrp.model import Problem, TimeWindow

BASELINE = "greedy"

Construct = Callable[[Problem], dict[str, list[str]]]


@dataclass(frozen=True)
class Day:
    """One historical day: which request became known when. §8.1."""

    id: str
    arrivals: dict[str, int]

    def known_by(self, instant: int) -> list[str]:
        """The requests visible at `instant`, in arrival order then id."""
        return [order_id for order_id, seen in
                sorted(self.arrivals.items(), key=lambda kv: (kv[1], kv[0]))
                if seen <= instant]


@dataclass(frozen=True)
class EpochRecord:
    """What one epoch decided, kept so a run can be inspected rather than
    trusted."""

    index: int
    dispatched: tuple[str, ...]
    postponed: tuple[str, ...]
    must_go: tuple[str, ...]
    forced: tuple[str, ...]
    cost: int
    # §8.3's "ETA shifts communicated to customers", in order-seconds: how long
    # the work dispatched here had been waiting since it arrived. Charged once,
    # at dispatch, rather than accruing every wave -- an order held three hours
    # waited three hours, not one plus two plus three, and a per-wave accrual
    # would make the penalty quadratic in the wait for no defensible reason.
    delay: int = 0


@dataclass(frozen=True)
class Run:
    """One policy over one day."""

    day: str
    epochs: tuple[EpochRecord, ...] = field(default_factory=tuple)

    @property
    def cost(self) -> int:
        return sum(record.cost for record in self.epochs)

    @property
    def delay(self) -> int:
        """Total order-seconds of postponement across the day."""
        return sum(record.delay for record in self.epochs)

    @property
    def dispatched(self) -> tuple[str, ...]:
        return tuple(order_id for record in self.epochs
                     for order_id in record.dispatched)

    @property
    def dispatch_epochs(self) -> int:
        """How many waves the policy actually sent a van out in."""
        return sum(1 for record in self.epochs if record.dispatched)


@dataclass(frozen=True)
class PolicyResult:
    """One policy across the whole corpus. AC-3.2's row."""

    policy: str
    days: int
    cost: int
    dispatch_epochs: int
    forced: int
    versus_baseline: int
    delay: int = 0


@dataclass(frozen=True)
class Comparison:
    """The report AC-3.2 asks for, against a greedy denominator."""

    baseline: str
    results: dict[str, PolicyResult]
    # Which delay price the table was measured at. A policy comparison taken at
    # one price is not comparable with one taken at another, and a table that
    # did not say which would silently invite that.
    #
    # No default: `compare` is the only thing that builds one and always knows
    # the price, so a default here would be unreachable -- perturbation showed
    # it could be set to anything without a test noticing.
    delay_price: int


def dispatchable(problem: Problem, horizon: TimeWindow,
                 window: int) -> Problem:
    """The same instance with windows narrow enough for dispatch to be a
    decision. §8.1.

    Measured, and this is the point: with day-long windows nothing is ever
    must-go until the final wave, postponing costs nothing, and "hold
    everything" is optimal by a wide margin -- 882,000 against greedy's
    1,465,200 on a 30-day corpus, with the best of forty sampled probabilities
    at 1,013,400. No policy can beat lazy there, so T-54's "beats greedy and
    lazy" is unreachable and every dispatch policy is measured on a problem
    that has no trade in it.

    §8.1's premise is that postponing *risks* something. Narrow the windows and
    the trade appears: on the same corpus lazy becomes dearer than greedy.
    That is the regime the competition instances are in and the one a dispatch
    policy is for.

    Args:
        problem: the instance.
        horizon: the planning day.
        window: how long each order's window stays open, staggered across the
            day so the whole fleet is not urgent at once.
    """
    from dataclasses import replace as _replace

    span = max(horizon.end - horizon.start - window, 1)
    orders = []
    for index, order in enumerate(problem.orders):
        opens = horizon.start + (index * span // max(len(problem.orders), 1))
        stop = order.delivery or order.pickup
        narrowed = _replace(stop, time_windows=(
            TimeWindow(start=opens, end=min(opens + window, horizon.end)),))
        orders.append(_replace(order, **{
            "delivery" if order.delivery else "pickup": narrowed}))
    return _replace(problem, orders=tuple(orders))


def generate_days(problem: Problem, count: int, seed: int,
                  horizon: TimeWindow) -> tuple[Day, ...]:
    """A synthetic corpus of arrival schedules.

    Args:
        problem: the instance whose orders form the request pool.
        count: how many days. T-53's definition of done wants 90.
        seed: same seed, same corpus (CON-4).
        horizon: the planning day.

    Returns:
        One `Day` per day, each giving every order an arrival instant strictly
        inside the horizon. A request arriving after the day ends could never
        be dispatched and would read as a policy failure rather than a corpus
        one.

    Real historical data would replace this wholesale; §12.4's telematics
    ingestion is T-61. Until then the corpus is generated, and says so.
    """
    rng = random.Random(seed)
    span = max(horizon.end - horizon.start - 1, 1)
    return tuple(
        Day(id=f"day-{index:03d}",
            arrivals={order.id: horizon.start + rng.randrange(span)
                      for order in problem.orders})
        for index in range(count))


def replay(problem: Problem, day: Day, policy: Policy, epoch_length: int,
           construct: Construct | None = None, delay_price: int = 0) -> Run:
    """Replay one day, epoch by epoch, under one policy. DYN-6.

    Args:
        problem: the instance.
        day: the arrival schedule.
        policy: the dispatch policy under test.
        epoch_length: wave length in seconds.
        delay_price: what keeping a customer waiting costs, per *thousand*
            order-seconds, in the same units as distance. §8.3 asks for churn
            to be "optionally" penalised and names "ETA shifts communicated to
            customers" as one of its two forms; postponing a request is exactly
            that shift.

            Per thousand rather than per second because measurement said so.
            At one metre per order-second the term swamps routing entirely --
            90 days of work carries roughly 5 million order-seconds of delay
            against 4.3 million metres of driving -- and greedy wins at every
            price above zero, which is a unit problem masquerading as a result.
            The interesting band sits below one metre per second, and CON-4
            forbids the float that would express it, so the scale moves instead.
            Parts per thousand is the convention this project already uses for
            utilisation, service factors and dissimilarity.

            Zero by default, which is the behaviour every earlier measurement
            was taken under -- T-53's baseline table, T-54's seed sweep and
            T-55's tuning curve would all be invalidated by a term that moved
            the numbers without being asked.

            It matters because without it postponing is free: AC-3.1 guarantees
            no window is missed and a day costs only the routing of each wave,
            so "hold until forced" is close to optimal by construction and the
            room any cleverer policy has to beat it is correspondingly thin.
        construct: builds an assignment for one epoch's dispatch set, so the
            cost is whatever the caller's operational solver would charge.
            Defaults to a first-fit, which is enough to separate policies and
            cheap enough to run ninety days three times over. §8.4 puts a full
            epoch replan in the T2 tier at five minutes; nothing here needs it.

    Returns:
        A `Run` holding every epoch's decision and its routing cost.

    Work postponed at one epoch is carried into the next, and requests become
    visible only when they arrive. The final epoch dispatches whatever is left
    regardless of policy: a replayer that quietly lost work would flatter every
    lazy policy, because the cheapest day is the one where nothing goes out.
    """
    build = construct or _first_fit
    waves = epochs(problem.vehicles[0].shift, epoch_length)
    records, carried, dispatched_ever = [], [], set()
    # When each request first became visible, so the wait can be charged once
    # against the moment it went out rather than against the epoch it landed in.
    first_seen: dict[str, int] = {}

    for wave in waves:
        last = wave is waves[-1]
        # Decisions are made at the epoch boundary with what was known then --
        # that is what an epoch *is*. The last wave is the exception and sweeps
        # to the end of the horizon: a request arriving at 19:41 can only ever
        # be dispatched by the 20:00 boundary, and a replayer that let it fall
        # off the end would lose work silently and flatter every lazy policy,
        # because the cheapest day is the one where nothing goes out.
        visible = day.known_by(wave.end if last else wave.start)
        arrived = [order_id for order_id in visible
                   if order_id not in dispatched_ever
                   and order_id not in carried]
        for order_id in arrived:
            # A plain assignment, not `setdefault`: `arrived` already excludes
            # anything carried or dispatched, so an order reaches here exactly
            # once. Perturbation confirmed the defensive version was
            # unreachable, and a guard no test can justify reads as a case
            # somebody has thought about.
            first_seen[order_id] = wave.start
        open_ids = carried + arrived
        if not open_ids:
            continue

        split = classify(problem, open_ids, postponed_to=wave.end)
        if last:
            # Nothing may be left over at the end of the day.
            decision = decide(problem, open_ids, wave,
                              policy=lambda ids, s, e: tuple(ids))
        else:
            decision = decide(problem, open_ids, wave, policy=policy)

        waited = sum(max(wave.start - first_seen.get(order_id, wave.start), 0)
                     for order_id in decision.dispatched)
        records.append(EpochRecord(
            index=wave.index, dispatched=decision.dispatched,
            postponed=decision.postponed, must_go=split.must_go,
            forced=decision.forced, delay=waited,
            cost=_cost_of(problem, decision.dispatched, build)
            + waited * delay_price // 1_000))
        dispatched_ever.update(decision.dispatched)
        carried = list(decision.postponed)

    return Run(day=day.id, epochs=tuple(records))


def _cost_of(problem: Problem, order_ids: Sequence[str],
             construct: Construct) -> int:
    """What sending this set out costs, on the canonical accountant's scale."""
    if not order_ids:
        return 0
    wave = replace(problem, orders=tuple(problem.order(o) for o in order_ids))
    return evaluate(wave, construct(wave)).total


def _first_fit(problem: Problem) -> dict[str, list[str]]:
    """Fill each vehicle in turn, on whatever dimensions the instance declares.

    Deliberately not `generate.plan_greedily`: that one is hard-coded to the
    generator's own `units` dimension and is a property-harness helper, not a
    constructor. Borrowing it here would have tied the replayer to instances
    shaped like the generator's.
    """
    assignment: dict[str, list[str]] = {v.id: [] for v in problem.vehicles}
    loads: dict[str, dict[str, int]] = {v.id: {} for v in problem.vehicles}
    for order in problem.orders:
        for vehicle in problem.vehicles:
            carried = loads[vehicle.id]
            if all(carried.get(dimension, 0) + amount
                   <= vehicle.capacities.get(dimension, 0)
                   for dimension, amount in order.quantities.items()):
                assignment[vehicle.id].append(order.id)
                for dimension, amount in order.quantities.items():
                    carried[dimension] = carried.get(dimension, 0) + amount
                break
    return assignment


def compare(problem: Problem, days: Sequence[Day],
            policies: Mapping[str, Policy], epoch_length: int,
            delay_price: int = 0) -> Comparison:
    """Replay every policy over the corpus and report against greedy. AC-3.2.

    Raises:
        ValueError: if no greedy baseline is present. AC-3.2 names it
            specifically, and a report against an arbitrary denominator is not
            the report the acceptance asks for.
    """
    if BASELINE not in policies:
        raise ValueError(f"AC-3.2 reports against a {BASELINE!r} baseline; "
                         f"got {sorted(policies)}")

    runs = {name: [replay(problem, day, policy, epoch_length,
                          delay_price=delay_price) for day in days]
            for name, policy in policies.items()}
    totals = {name: sum(run.cost for run in these)
              for name, these in runs.items()}

    return Comparison(baseline=BASELINE, delay_price=delay_price, results={
        name: PolicyResult(
            policy=name, days=len(days), cost=totals[name],
            dispatch_epochs=sum(run.dispatch_epochs for run in these),
            forced=sum(len(record.forced) for run in these
                       for record in run.epochs),
            versus_baseline=totals[name] - totals[BASELINE],
            delay=sum(run.delay for run in these))
        for name, these in runs.items()})
