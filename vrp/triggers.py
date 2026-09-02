"""Trigger engine and locked re-optimisation — DYN-5, AC-2.1, AC-2.3, §8.3,
§8.4, T-56.

US-2: "when a vehicle breaks down at 11:00, I re-optimise only the affected and
nearby work while everything already executed or committed stays fixed."

Three requirements meet here, and each is easy to satisfy alone while missing
the point of the other two.

**DYN-5** wants re-optimisation to be event driven -- "on breakdown,
cancellation, large ETA drift, new priority order" -- rather than on a timer.

**AC-2.1** bounds it at thirty seconds with 90% of stops locked, and §8.4 says
how: "Locked LNS on affected + neighbouring routes only." The budget is met by
*not re-solving the plan*. A re-optimisation that touched everything would be a
fresh solve wearing a different name, and it would blow the budget on any fleet
worth the trouble.

**AC-2.3** says what comes back: "the delta versus the previous plan (stops
moved, cost change, new lateness) rather than only the new plan". §8.3 gives the
reason -- "A 0.5% cost gain that reshuffles half the plan at 14:00 is a net
loss" -- and a response carrying only a plan makes that trade invisible. The
dispatcher accepts it without ever being asked.

Most of this composes. T-50 already turns executed work into locks and already
computes which stops changed vehicle, which is AC-2.3's first field. What is new
is deciding what a disruption touches, keeping the rest out of the search, and
pricing the answer in churn as well as money.

Placement: **Python**, per criterion 2. This composes the committed-state
manager, the evaluator and a solver.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from itertools import pairwise

from vrp.committed import (
    commit_locks,
    committed_prefix,
    loading_locks,
    moved_since,
)
from vrp.evaluator import evaluate, route_metrics
from vrp.model import Lock, Problem, Route, Solution

# DYN-5's four events. Anything outside this list is a typo, and a typo that
# silently does nothing is the worst kind: the operator believes they have
# raised an alarm.
TRIGGER_KINDS: dict[str, tuple[str, ...]] = {
    "BREAKDOWN": ("vehicle_id",),
    "CANCELLATION": ("order_id",),
    "ETA_DRIFT": ("vehicle_id",),
    "PRIORITY_ORDER": ("order_id",),
}

FULL = 1000
Solve = Callable[[Problem, dict[str, list[str]]], dict[str, list[str]]]


@dataclass(frozen=True)
class Trigger:
    """One disruption. DYN-5."""

    kind: str
    at: int
    vehicle_id: str | None = None
    order_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in TRIGGER_KINDS:
            raise ValueError(
                f"unknown trigger kind {self.kind!r}; DYN-5 defines "
                f"{', '.join(sorted(TRIGGER_KINDS))}")
        for field_name in TRIGGER_KINDS[self.kind]:
            if not getattr(self, field_name):
                raise ValueError(f"{self.kind} needs {field_name}")


@dataclass(frozen=True)
class Delta:
    """What changed, in the three terms AC-2.3 names.

    Three numbers because they move independently: a cheaper plan that
    reshuffles half the fleet and a cheaper plan that touches nothing are
    different answers, and only one of them is worth accepting at 14:00.
    """

    moved: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    cost_before: int = 0
    cost_after: int = 0
    lateness_before: int = 0
    lateness_after: int = 0

    @property
    def cost_change(self) -> int:
        return self.cost_after - self.cost_before

    @property
    def churn(self) -> int:
        """Stops the plan no longer holds where it did. §8.3's stability measure.

        The sum of the two below, and kept as one number because that is what
        §8.3's churn/cost curve is swept against. Read `reassigned` and
        `displaced` when the question is what actually happened to a customer:
        one of them changes which driver arrives, the other means nobody does.
        """
        return len(self.moved)

    @property
    def reassigned(self) -> dict[str, str]:
        """Stops that changed driver and are still being done. FR-27.

        Ordinary churn: a customer is told a different van, which costs
        dispatcher trust and nothing else.
        """
        return {order_id: now for order_id, (_was, now) in self.moved.items()
                if now is not None}

    @property
    def displaced(self) -> tuple[str, ...]:
        """Stops the plan no longer serves at all. FR-27.

        Not churn in the same sense, and the distinction is the point of
        `T-77`: `moved` reports both with `None` standing in for "no vehicle",
        so a response that counted them together said a plan reshuffling three
        stops and a plan abandoning three were equally stable. `UC-044`'s gas
        escape displaces planned work by design, and what it displaced has to
        be legible as a separate fact.
        """
        return tuple(sorted(order_id
                            for order_id, (_was, now) in self.moved.items()
                            if now is None))


@dataclass(frozen=True)
class Response:
    """A re-optimisation, with the delta AC-2.3 requires beside the plan."""

    plan: Solution
    delta: Delta
    locks: tuple[Lock, ...]
    locked_share: int

    @property
    def worth_it(self) -> bool:
        """§8.3's judgement, made explicit rather than left to the reader."""
        return self.delta.cost_change < 0 or self.delta.churn == 0


def affected_routes(problem: Problem, plan: Solution, trigger: Trigger,
                    neighbours: int = 1) -> set[str]:
    """Which routes the search is allowed to touch. §8.4's T1 scope.

    Args:
        problem: the instance.
        plan: the plan as it stands.
        trigger: the disruption.
        neighbours: how many nearby routes to pull in alongside the one hit
            directly. The broken van's work has to go somewhere, and the only
            candidates worth searching are the routes near it.

    Returns:
        Vehicle ids. Everything else stays locked, which is how AC-2.1's budget
        is met.
    """
    hit = _directly_hit(problem, plan, trigger)
    if neighbours <= 0 or not hit:
        return hit

    anchor = _centre(problem, plan, next(iter(hit)))
    others = [route.vehicle_id for route in plan.routes
              if route.vehicle_id not in hit
              and any(step.order_id for step in route.steps)]
    others.sort(key=lambda vehicle_id: (
        abs(_centre(problem, plan, vehicle_id) - anchor), vehicle_id))
    return hit | set(others[:neighbours])


def _directly_hit(problem: Problem, plan: Solution,
                  trigger: Trigger) -> set[str]:
    if trigger.vehicle_id is not None:
        return {trigger.vehicle_id}
    return {route.vehicle_id for route in plan.routes
            for step in route.steps if step.order_id == trigger.order_id}


def _centre(problem: Problem, plan: Solution, vehicle_id: str) -> float:
    """A route's mean stop index, as a cheap stand-in for where it works.

    Deliberately crude. §8.4 gives this tier thirty seconds for the whole
    re-optimisation, and a proper geographic clustering of routes would spend a
    noticeable share of it deciding what to search before searching anything.
    """
    nodes = [problem.location(step.location_id).matrix_index
             for route in plan.routes if route.vehicle_id == vehicle_id
             for step in route.steps if step.order_id]
    return sum(nodes) / len(nodes) if nodes else 0.0


def reoptimise(problem: Problem, plan: Solution, trigger: Trigger, now: int,
               neighbours: int = 1, solve: Solve | None = None,
               churn_weight: int = 0) -> Response:
    """Re-optimise the affected routes only, and report the delta. §8.3, §8.4.

    Args:
        problem: the instance.
        plan: the plan as it stands.
        trigger: what happened.
        now: the instant; everything committed by then stays put.
        neighbours: how many nearby routes to open alongside the affected one.
        solve: re-plans the open work over the open routes. Injected, as
            elsewhere; the default is a cheapest-insertion pass, which is what
            §8.4's T0/T1 tiers describe and is fast enough to leave the
            thirty-second budget almost untouched.
        churn_weight: what moving a stop to a different vehicle costs, in the
            same units as distance. §8.3's "optionally penalise"; zero is the
            behaviour before T-57. T-57's curve is this weight swept.

    Returns:
        The new plan, the delta AC-2.3 asks for, the locks that held the rest
        in place, and how much of the plan was locked.
    """
    build = solve or _cheapest_insertion
    open_routes = affected_routes(problem, plan, trigger, neighbours)
    locks = commit_locks(problem, plan, now)

    frozen: dict[str, list[str]] = {}
    loose: list[str] = []
    for route in plan.routes:
        carried = [step.order_id for step in route.steps if step.order_id]
        if route.vehicle_id not in open_routes:
            frozen[route.vehicle_id] = carried
            continue
        # Inside an open route, work already committed is still committed --
        # §8.3 is explicit that re-optimisation never moves executed work.
        held = committed_prefix(problem, route, now, include_en_route=True)
        frozen[route.vehicle_id] = list(held)
        loose.extend(order_id for order_id in carried if order_id not in held)

    if trigger.kind == "BREAKDOWN":
        # The broken van keeps only what it has already done.
        loose.extend(frozen.get(trigger.vehicle_id, [])[len(committed_prefix(
            problem, _route_of(plan, trigger.vehicle_id), now,
            include_en_route=True)):])
        frozen[trigger.vehicle_id] = committed_prefix(
            problem, _route_of(plan, trigger.vehicle_id), now,
            include_en_route=True)
    if trigger.kind == "CANCELLATION":
        loose = [order_id for order_id in loose if order_id != trigger.order_id]

    usable = {vehicle_id for vehicle_id in open_routes
              if not (trigger.kind == "BREAKDOWN"
                      and vehicle_id == trigger.vehicle_id)}
    rebuilt = build(problem, {**frozen, **{v: frozen.get(v, []) for v in usable}})
    home = {step.order_id: route.vehicle_id for route in plan.routes
            for step in route.steps if step.order_id}
    for order_id in loose:
        _insert(problem, rebuilt, order_id, usable,
                churn_weight=churn_weight, home=home.get(order_id))

    after = _rebuild(problem, plan, rebuilt)
    locked = sum(len(ids) for vehicle_id, ids in frozen.items()
                 if vehicle_id not in open_routes)
    total = sum(1 for route in plan.routes for step in route.steps
                if step.order_id)
    return Response(
        plan=after,
        delta=_delta(problem, plan, after),
        locks=locks,
        locked_share=locked * FULL // max(total, 1))


def preempt(problem: Problem, plan: Solution, arriving: str, now: int,
            solve: Callable[[Problem], Solution]) -> Response:
    """Let urgent work displace planned work that has not been done yet. FR-27.

    `UC-044` is the operation: "A P1 gas escape preempts work already in
    progress, requiring the plan to be interruptible mid-route." The arriving
    job is not an insertion looking for slack -- if there is no slack it takes
    somebody else's, and the question is whose.

    That question is already answered. `FR-13`'s tiers and `FR-25`'s sources
    say what may be given up for what, and `T-75` priced them so a protected
    order outranks everything beneath it. So this does not choose: it pins what
    has been executed, leaves the rest open, requires the arriving order, and
    lets the objective decide. A preemption that picked its own victims would
    be a second, quieter priority scheme competing with the declared one.

    What it does own is the reporting. `FR-27` requires displaced work to be
    "re-planned rather than silently dropped", and the delta separates the two
    outcomes: `reassigned` is a customer told a different van, `displaced` is a
    customer told nobody is coming. Before `T-77` both arrived as one number.

    Args:
        problem: the instance, with the arriving order among its orders.
        plan: the plan as it stands.
        arriving: the urgent order's id.
        now: the instant; everything executed by then is immovable.
        solve: the engine. A real one -- displacement is a choice between
            orders, which a cheapest-insertion pass cannot make: it either
            finds room or gives up, and giving up is how an urgent job gets
            quietly dropped instead of a routine one.

    Returns:
        The plan, its delta, and the locks that held the executed work.

    Raises:
        ValueError: if the arriving order is not in the problem, or is already
            planned -- preemption is about work that has just appeared, and a
            re-optimisation of work already placed is `reoptimise`.
    """
    if arriving not in {order.id for order in problem.orders}:
        raise ValueError(f"{arriving} is not an order in this problem")
    if any(step.order_id == arriving
           for route in plan.routes for step in route.steps):
        raise ValueError(
            f"{arriving} is already planned; preemption is for work that has "
            "just arrived, and moving placed work is what reoptimise does")

    locks = commit_locks(problem, plan, now)
    after = solve(replace(problem, locks=problem.locks + locks))

    served = {step.order_id for route in after.routes
              for step in route.steps if step.order_id}
    if arriving not in served:
        raise RuntimeError(
            f"{arriving} arrived urgent and was not planned. Either it is "
            "declinable -- check its tier, source and prize -- or no vehicle "
            "is eligible for it, which pre-flight reports before any of this")

    delta = _delta(problem, plan, after)
    total = sum(1 for route in plan.routes for step in route.steps
                if step.order_id)
    executed = sum(len(lock.order_ids) for lock in locks
                   if lock.kind == "FIX_ROUTE_PREFIX")
    return Response(plan=_report_displaced(problem, after, delta, arriving),
                    delta=delta, locks=locks,
                    locked_share=executed * FULL // max(total, 1))


def _report_displaced(problem: Problem, after: Solution, delta: Delta,
                      arriving: str) -> Solution:
    """Say who was displaced and by what, rather than leaving a gap.

    `FR-27`: displaced work is "re-planned rather than silently dropped". Where
    it could not be re-planned, the plan has to name it -- an order that simply
    stops appearing is the silent drop the requirement forbids.
    """
    displaced = set(delta.displaced)
    if not displaced:
        return after
    return replace(after, unassigned=tuple(
        {**row,
         **({"reason_code": "PREEMPTED",
             "explanation": f"displaced by {arriving}, which outranks it"}
            if row["order_id"] in displaced else {})}
        for row in after.unassigned))


def recover_from_absence(problem: Problem, plan: Solution,
                         absent: Sequence[str],
                         solve: Callable[[Problem], Solution]) -> Response:
    """Strip the vans nobody is driving and redistribute their work. `UC-171`.

    A driver calling in sick at 05:30 for a 06:00 departure is not the
    disruption `reoptimise` above is built for. That one protects *executed*
    work and opens the affected routes to a cheapest-insertion pass, which is
    right at 14:00 when most of the day is behind the fleet. At shift start
    nothing is executed, so there is nothing for the freeze to protect and the
    insertion pass has the whole round to place at once -- measured on
    `UC-171`'s own fixture it dropped half of it.

    What *is* committed at 05:30 is the loading. The vans that are coming are
    packed, and an order moving between them is two drivers unloading and
    repacking in the yard, which is why the entry says "the practical question
    is which stops to strip and redistribute, not how to re-plan the day".

    So the policy is exactly that, and it is expressed in locks the search
    already honours: pin every order aboard a van that is coming, forbid the
    vans that are not, and solve. The absent stock is the only thing free to
    move, which is the smallest question that answers the morning.

    Args:
        problem: the instance.
        plan: the plan the vehicles were loaded to.
        absent: the vehicles that will not arrive.
        solve: the engine. Injected, as `reoptimise`'s is, and a real one this
            time: the work being placed is a whole van's round rather than a
            single insertion.

    Returns:
        The recovered plan with its delta, and the locks that produced it -- so
        a dispatcher can see that nothing already loaded was asked to move.
    """
    missing = tuple(absent)
    if not missing:
        raise ValueError("an absence needs an absent vehicle")

    locks = loading_locks(problem, plan, absent=missing) + tuple(
        Lock(kind="FORBID_DEPLOY", vehicle_id=vehicle_id)
        for vehicle_id in missing)
    stripped = replace(problem, locks=problem.locks + locks)
    after = solve(stripped)

    committed = sum(1 for lock in locks
                    if lock.kind == "PIN_ORDER_TO_VEHICLE")
    total = sum(1 for order in problem.orders)
    return Response(plan=after, delta=_delta(problem, plan, after), locks=locks,
                    locked_share=committed * FULL // max(total, 1))


def _route_of(plan: Solution, vehicle_id: str) -> Route:
    for route in plan.routes:
        if route.vehicle_id == vehicle_id:
            return route
    return Route(vehicle_id=vehicle_id, steps=())


def _cheapest_insertion(problem: Problem,
                        assignment: dict[str, list[str]]
                        ) -> dict[str, list[str]]:
    """The default re-planner: keep what is there. §8.4's T0 shape."""
    return {vehicle_id: list(ids) for vehicle_id, ids in assignment.items()}


def _insert(problem: Problem, assignment: dict[str, list[str]], order_id: str,
            usable: set[str], churn_weight: int = 0,
            home: str | None = None) -> None:
    """Put one order on the cheapest open route that can carry it.

    "Cheapest" includes `churn_weight` when the candidate is not the vehicle
    the order started on. That is how §8.3's penalty actually changes a plan
    rather than merely scoring one: a weight large enough makes staying put
    win, and the sweep between the two is T-57's curve.
    """
    from vrp.evaluator import route_is_legal

    best = None
    for vehicle_id in sorted(usable):
        current = assignment.setdefault(vehicle_id, [])
        for position in range(len(current) + 1):
            candidate = current[:position] + [order_id] + current[position:]
            if not route_is_legal(problem, vehicle_id, candidate):
                continue
            cost = _length(problem, vehicle_id, candidate)
            if home is not None and vehicle_id != home:
                cost += churn_weight
            if best is None or cost < best[0]:
                best = (cost, vehicle_id, candidate)
    if best is not None:
        assignment[best[1]] = best[2]


def _length(problem: Problem, vehicle_id: str, order_ids: list[str]) -> int:
    vehicle = problem.vehicle(vehicle_id)
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    nodes = [index[vehicle.start_location_id]]
    for order_id in order_ids:
        order = problem.order(order_id)
        stop = order.delivery or order.pickup
        nodes.append(index[stop.location_id])
    nodes.append(index[vehicle.end_location_id or vehicle.start_location_id])
    return sum(problem.matrix.distance(a, b)
               for a, b in pairwise(nodes))


def _rebuild(problem: Problem, previous: Solution,
             assignment: dict[str, list[str]]) -> Solution:
    from vrp.evaluator import build_timeline

    routes = tuple(Route(vehicle_id=vehicle_id,
                         steps=build_timeline(problem, vehicle_id, ids))
                   for vehicle_id, ids in sorted(assignment.items()))
    served = {order_id for ids in assignment.values() for order_id in ids}
    return Solution(
        problem_id=previous.problem_id, routes=routes,
        unassigned=tuple({"order_id": order.id, "reason_code": "NOT_PLACED",
                          "explanation": "displaced by re-optimisation"}
                         for order in problem.orders if order.id not in served),
        objective_breakdown={}, status="FEASIBLE")


def _delta(problem: Problem, before: Solution, after: Solution) -> Delta:
    return Delta(
        moved=moved_since(before, after),
        cost_before=_cost(problem, before), cost_after=_cost(problem, after),
        lateness_before=_lateness(problem, before),
        lateness_after=_lateness(problem, after))


def _cost(problem: Problem, plan: Solution) -> int:
    assignment = {route.vehicle_id: [s.order_id for s in route.steps
                                     if s.order_id]
                  for route in plan.routes}
    return evaluate(problem, assignment).total


def _lateness(problem: Problem, plan: Solution) -> int:
    return sum(route_metrics(problem, route.steps)["lateness_penalty"]
               for route in plan.routes)
