"""PyVRP adapter — model compiler and solution mapper. SDD §7.3, T-12.

Two directions, deliberately separated:

*Compile* turns a `Problem` into a PyVRP model. Every travel cost comes from the
pinned matrix rather than from coordinates — PyVRP will compute Euclidean
distances from `x`/`y` if you let it, and silently disagreeing with the matrix
the plan is later verified against is exactly the drift INV-4 exists to catch.
Coordinates are passed for display only.

*Map* turns the result back into a `Solution`, carrying **PyVRP's own arrival
and service times** rather than times recomputed here. That is the point: the
independent verifier then checks the solver's arithmetic against the matrix,
instead of checking our evaluator against itself.

Placement: this is Python, not gateway. It is optimisation logic whose value is
the PyVRP ecosystem, it is not on the request path, and constraint semantics
change far more often than transport behaviour. See "Placement" in
docs/planning/VRP_TDD_EXAMPLES.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyvrp import Model
from pyvrp.stop import MaxIterations

from vrp.model import (
    Problem,
    Route,
    Solution,
    Step,
    has_skills_for,
    may_enter,
    precedence,
    service_time,
)

# PyVRP addresses capacity dimensions positionally, so the order must be pinned
# and used identically when compiling and when mapping back. Sorted rather than
# insertion-ordered: two Problems describing the same fleet must compile to the
# same model whichever order their dicts were built in, or a cached plan and a
# fresh one disagree about which number is the pallets.

# FR-07 lists a per-vehicle routing profile, and a profile is a matrix. A
# Problem pins exactly one, so a mixed-profile fleet would route a bicycle and
# an artic over identical travel while appearing to differ. PyVRP supports
# per-profile edge sets (`Model.add_profile`), so the gap is in the domain
# model rather than the solver -- it needs a Problem that can carry several
# matrices. Refused loudly until then.
_UNSUPPORTED_MIXED_PROFILES = (
    "a fleet mixing routing profiles needs one matrix per profile, which "
    "Problem does not yet carry; every vehicle must share one profile")


@dataclass(frozen=True)
class _Compiled:
    model: Model
    dimensions: tuple[str, ...]
    # PyVRP client index (0-based, in insertion order) -> our order id.
    order_by_client: dict[int, str]
    # PyVRP numbers shipments in their own space, from zero, overlapping the
    # client indices. Two maps, because one would silently conflate them.
    order_by_shipment: dict[int, str]
    vehicle_ids: list[str]


def shift_start_of(problem: Problem) -> int:
    return min(v.shift.start for v in problem.vehicles)


def shift_end_of(problem: Problem) -> int:
    return max(v.shift.end for v in problem.vehicles)


def _is_required(order) -> bool:
    """Whether the solver may decline this order at all. FR-12, FR-25, §4.1.

    Tier 0 is must-serve whatever it is worth. Everything else is declinable
    once it carries a prize -- an order with no prize has no price at which
    declining is acceptable, so the solver must place it or report infeasible.

    A statutory obligation needs no clause of its own here, and adding one was
    a mistake worth recording: `Order` refuses a `STATUTORY` order that carries
    a prize, so every such order reaches this function prizeless and the second
    test below already covers it. The extra condition read like enforcement and
    enforced nothing -- perturbing it away changed no result, which is how it
    was caught. `UC-046`'s "no address may be declined" is carried by the model
    invariant, where a contradiction belongs, rather than by a solver detail.
    """
    return order.priority_tier == 0 or order.prize == 0


def tier_bonuses(problem: Problem) -> dict[int, int]:
    """Prize bonuses making priority tiers lexicographic. FR-13.

    PyVRP declines an order by forgoing its prize, and a prize is one number.
    FR-13 wants "lexicographic protection: a higher tier is never sacrificed to
    improve a lower tier", which one number can express only if a tier's
    bonus strictly exceeds everything obtainable from every tier beneath it.

    Derived from the instance, never a constant, for the reason §5.1 gives
    about weighted sums: a multiplier that dominates on today's prizes will not
    on a day when somebody attaches a larger one, and nothing fails -- the
    solver simply starts declining work it should have protected.

    Bonuses compound across tiers, so a deep tier stack with large prizes can
    grow them considerably. Python integers are unbounded but PyVRP's are
    int64; the same overflow ceiling §5.1 flags for staged optimisation applies
    here, and is not yet guarded.
    """
    by_tier: dict[tuple[int, int], list[int]] = {}
    for order in problem.orders:
        by_tier.setdefault(precedence(order), []).append(order.prize)

    bonuses: dict[tuple[int, int], int] = {}
    beneath = 0
    # Least protected first: each rank must outrank the total of everything
    # below it, so the totals accumulate upwards. The key is (tier, source),
    # so a statutory order outranks an SLA one on the same tier and an SLA
    # one outranks a commercial one -- FR-25's ordering, priced.
    for tier in sorted(by_tier, reverse=True):
        bonuses[tier] = beneath
        beneath += sum(prize + beneath for prize in by_tier[tier]) + 1
    return bonuses


def _delivery_deadline(problem: Problem, order) -> int:
    """When a shipment must be delivered by. FR-04 and, if set, FR-24.

    `add_shipment` takes no ride-time bound, so the only lever is the delivery
    window -- which is not the same constraint, and the entries citing FR-24
    say so directly. What *can* be said soundly is a deadline: a shipment
    collected no earlier than its pickup window opens and aboard for at most
    `max_ride_time` cannot legally be delivered after `pickup_opens + ride`.
    Any plan violating the ride bound violates that deadline too, so no illegal
    plan survives it.

    "No earlier" means the earliest the collection could physically happen, not
    the instant its window opens. Those differ by the drive out, and using the
    window alone made feasible instances infeasible: a shipment whose window
    opens at 00:00 but whose depot is an hour away was being held to a deadline
    an hour tighter than the operation allows. The floor is therefore the later
    of the window and the earliest any vehicle could be standing there, loaded.

    It remains **conservative, and exact when the collection time is fixed.** A
    shipment collected later than it could have been has ride time to spare
    that this deadline does not give back. For a scheduled collection -- a
    school stop, a booked ward round -- the two coincide. Where they do not,
    `INV-14` is the exact check and this is the search's safe approximation of
    it.
    """
    window_end = (order.delivery.time_windows[0].end
                  if order.delivery.time_windows else shift_end_of(problem))
    if order.max_ride_time is None:
        return window_end
    return min(window_end,
               _earliest_departure(problem, order) + order.max_ride_time)


def _earliest_departure(problem: Problem, order) -> int:
    """The soonest a shipment could leave its pickup, loaded.

    A lower bound, deliberately: it takes the quickest vehicle from its own
    start, ignores everything else that vehicle might have to do first, and
    adds the collection's own service. Anything looser would make the deadline
    unsound; anything tighter would need to know the route, which is what is
    being decided.
    """
    collect = problem.location(order.pickup.location_id)
    opens = (order.pickup.time_windows[0].start
             if order.pickup.time_windows else shift_start_of(problem))
    reachable = min(
        (vehicle.shift.start + problem.matrix.duration(
            problem.location(vehicle.start_location_id).matrix_index,
            collect.matrix_index)
         for vehicle in problem.vehicles
         if problem.matrix.is_reachable(
             problem.location(vehicle.start_location_id).matrix_index,
             collect.matrix_index)),
        default=opens)
    return max(opens, reachable) + service_time(
        order, problem.vehicles[0], collect)


def _bounds(window, shift_start: int, shift_end: int) -> tuple[int, int]:
    """PyVRP bounds for one window. FR-04's hard/soft distinction lives here.

    PyVRP has no soft time windows -- `PenaltyManager` is its internal search
    mechanism, not a user-facing feature -- so a soft window is widened to the
    shift and its breach costed afterwards by the evaluator. That is a real
    limitation and worth naming: the solver will not *search* for the cheapest
    lateness, it merely stops treating a soft window as a wall.

    Passing a soft window through as a hard bound, which is what this did
    before T-23, made a stop 600 s away with a soft 100 s window come back
    INFEASIBLE -- refusing a plan that any dispatcher would call "late".
    """
    if window is None:
        return shift_start, shift_end
    if window.hardness == "SOFT":
        return shift_start, shift_end
    return window.start, window.end


def _may_serve(problem: Problem, vehicle, order) -> bool:
    """Whether this vehicle is allowed this order at all. FR-10, FR-11, FR-21.

    Three sources of the same answer, and the operator's is not the weakest:
    a qualification, a site restriction, and a lock a dispatcher set by hand.
    `vrp.diagnose` asks the same question to decide `NO_ELIGIBLE_VEHICLE` and
    `LOCK_CONFLICT`; asking it differently here would let the search build what
    pre-flight had already called impossible.
    """
    if not has_skills_for(vehicle, order):
        return False
    if any(not may_enter(vehicle, problem.location(stop.location_id))
           for stop in order.stops):
        return False
    for lock in problem.locks:
        if lock.kind == "FORBID_DEPLOY" and lock.vehicle_id == vehicle.id:
            return False
        if lock.order_id != order.id:
            continue
        if lock.kind == "PIN_ORDER_TO_VEHICLE" and lock.vehicle_id != vehicle.id:
            return False
        if lock.kind == "FORBID_ORDER_ON_VEHICLE" and lock.vehicle_id == vehicle.id:
            return False
        if lock.kind == "PIN_DEPOT" and vehicle.start_location_id != lock.depot_id:
            return False
    return True


def _eligibility_key(problem: Problem, vehicle) -> frozenset[int]:
    """The matrix indices this vehicle may not visit. FR-10, FR-11, FR-21.

    Its own depots are never in the set. A vehicle barred from the yard it
    starts in is a pre-flight problem (`NO_ELIGIBLE_VEHICLE`) and encoding it
    here would produce an instance with no answer and no explanation.
    """
    forbidden: set[int] = set()
    for order in problem.orders:
        allowed = _may_serve(problem, vehicle, order)
        for stop in order.stops:
            site = problem.location(stop.location_id)
            if not allowed:
                forbidden.add(site.matrix_index)
    home = {vehicle.start_location_id, vehicle.ends_at, *vehicle.reload_locations}
    forbidden -= {problem.location(name).matrix_index
                  for name in home if name is not None}
    return frozenset(forbidden)


def _eligibility_profiles(problem: Problem, model: Model) -> dict:
    """One profile per distinct eligibility set, and none when nobody is barred.

    A profile carries its own full edge set, so the build cost is multiplied by
    the number of *distinct* restrictions rather than by the fleet size -- a
    hundred identically-qualified vans share one. Instances with no skills and
    no site restrictions get a single profile and the edge loop they always had.
    """
    if problem.speed_profile is not None:
        raise NotImplementedError(
            "this instance declares a speed profile (FR-14) and PyVRP compiles "
            "one duration per arc, so the plan it returns would be timed at "
            "free flow and the verifier would reject every arrival under "
            "INV-4. The evaluator and the verifier are time-aware (T-80); "
            "planning under a profile needs a search that carries §7.5's "
            "lower-bound filter, which is T-82. Solve at free flow and "
            "evaluate under the profile to see what the peak costs")
    _refuse_order_incompatibility(problem)
    _refuse_ambiguous_eligibility(problem)
    keys = {_eligibility_key(problem, vehicle) for vehicle in problem.vehicles}
    return {key: (model.add_profile(name=f"eligibility-{index}"), key)
            for index, key in enumerate(sorted(keys, key=sorted))}


def _refuse_order_incompatibility(problem: Problem) -> None:
    """Refuse an instance whose orders may not share a route. FR-10, §6.5.

    Incompatibility is a predicate over a route's *composition*: each order is
    legal alone and the pair is illegal only once both are aboard. Nothing in
    PyVRP says that. Profiles restrict which places a vehicle may visit, which
    is a different shape of constraint, and splitting a vehicle into one type
    per order class would let the search deploy both and plan two vans where
    the depot has one.

    So this is refused rather than approximated. Until `T-72` the search simply
    did not know: it loaded a hazardous class beside foodstuff, reported
    `FEASIBLE`, and the independent verifier rejected the plan afterwards on
    `INV-10`. A refusal names the constraint that cannot be honoured; a plan
    that violates it names nothing and looks like an answer.

    Only a conflict that could actually arise is refused. An order declaring
    itself incompatible with a class no other order in the instance carries
    constrains nothing, and refusing that would be refusing arithmetic.
    """
    classes = {order.order_class for order in problem.orders if order.order_class}
    binding = sorted(
        f"{order.id} ({order.order_class}) excludes "
        f"{sorted(order.incompatible_with & classes - {order.order_class})}"
        for order in problem.orders
        if order.incompatible_with & classes - {order.order_class or ""})
    if binding:
        raise NotImplementedError(
            "order-class incompatibility is a constraint on which orders may "
            "share a route, and this adapter has no way to express it: "
            f"{'; '.join(binding)}. The verifier checks INV-10 and pre-flight "
            "reports INCOMPATIBLE_ONLY, but a plan violating it would be built "
            "before either noticed. Separate the classes into different "
            "planning runs until T-72 carries this into the search")


def _refuse_ambiguous_eligibility(problem: Problem) -> None:
    """Refuse what a per-place encoding cannot say exactly.

    PyVRP profiles restrict *places*, and several orders may share one. Where
    two orders at the same address differ in what they need -- one wants a gas
    ticket, the other does not -- barring the place for a vehicle bars work it
    was entitled to do, and permitting it permits work it was not. Neither is
    the instance the caller described, so it is refused by name rather than
    approximated.
    """
    by_place: dict[str, list] = {}
    for order in problem.orders:
        for stop in order.stops:
            by_place.setdefault(stop.location_id, []).append(order)
    for location_id, orders in by_place.items():
        if len(orders) < 2:
            continue
        for vehicle in problem.vehicles:
            allowed = {_may_serve(problem, vehicle, order) for order in orders}
            if len(allowed) > 1:
                raise NotImplementedError(
                    f"orders at {location_id} differ in whether {vehicle.id} "
                    f"may serve them -- a skill, a site restriction or a lock "
                    f"-- and eligibility is compiled per place: the encoding "
                    f"cannot admit one and bar the other. Split the location, "
                    f"or make the orders agree")


def _single_profile(problem: Problem) -> str:
    """The fleet's shared routing profile, or a refusal. FR-07."""
    profiles = {vehicle.profile for vehicle in problem.vehicles}
    if len(profiles) > 1:
        raise NotImplementedError(
            f"{_UNSUPPORTED_MIXED_PROFILES} (found {sorted(profiles)})")
    return next(iter(profiles), "driving")


def _dimensions(problem: Problem) -> tuple[str, ...]:
    """Every capacity dimension in play, in a stable order. FR-02, §6.1."""
    names = {d for order in problem.orders for d in order.quantities}
    names |= {d for vehicle in problem.vehicles for d in vehicle.capacities}
    return tuple(sorted(names))


def compile_problem(problem: Problem) -> _Compiled:
    """Build the PyVRP model. Travel comes from the matrix, never the geometry."""
    dimensions = _dimensions(problem)
    _single_profile(problem)
    bonuses = tier_bonuses(problem)
    model = Model()

    # One PyVRP location per domain location, in matrix-index order so the edge
    # loop below can address them by index without a second mapping.
    ordered = sorted(problem.locations, key=lambda location: location.matrix_index)
    handles = [model.add_location(x=round(location.lon * 10_000),
                                  y=round(location.lat * 10_000),
                                  name=location.id)
               for location in ordered]

    depot_ids = {vehicle.start_location_id for vehicle in problem.vehicles}
    depot_ids |= {vehicle.ends_at for vehicle in problem.vehicles
                  if not vehicle.open_route}
    # A reload location must exist as a depot in the model, or PyVRP has
    # nowhere to send the vehicle back to.
    depot_ids |= {name for vehicle in problem.vehicles
                  for name in vehicle.reload_locations}
    depots = {
        location.id: model.add_depot(location=handles[location.matrix_index],
                                     tw_early=min(v.shift.start for v in problem.vehicles),
                                     tw_late=max(v.shift.end for v in problem.vehicles),
                                     name=location.id)
        for location in ordered if location.id in depot_ids
    }

    # FR-08's "end-anywhere". PyVRP requires an end depot and accepts
    # `end_depot=None` by silently closing the route -- measured, a 2 km
    # one-way problem reports 4 km either way. A sink reachable from every
    # location at zero cost is the construction that actually works.
    open_sink = None
    if any(vehicle.open_route for vehicle in problem.vehicles):
        sink_handle = model.add_location(x=0, y=0, name="__open_route_sink__")
        open_sink = model.add_depot(
            location=sink_handle,
            tw_early=min(v.shift.start for v in problem.vehicles),
            tw_late=max(v.shift.end for v in problem.vehicles),
            name="__open_route_sink__")

    order_by_client: dict[int, str] = {}
    order_by_shipment: dict[int, str] = {}
    for index, order in enumerate(problem.orders):
        if order.kind == "SHIPMENT":
            # FR-01: goods move from one place to another, so PyVRP models it
            # as a pair with precedence and same-vehicle built in rather than
            # as two clients we would then have to constrain ourselves.
            if len(order.pickup.time_windows) > 1 or len(order.delivery.time_windows) > 1:
                raise NotImplementedError(
                    "a shipment end with several windows needs client groups, "
                    "which add_shipment does not take")
            collect = problem.location(order.pickup.location_id)
            drop = problem.location(order.delivery.location_id)
            model.add_shipment(
                pickup_location=handles[collect.matrix_index],
                delivery_location=handles[drop.matrix_index],
                pickup_tw_early=order.pickup.time_windows[0].start
                if order.pickup.time_windows else shift_start_of(problem),
                pickup_tw_late=order.pickup.time_windows[0].end
                if order.pickup.time_windows else shift_end_of(problem),
                pickup_service_duration=service_time(
                    order, problem.vehicles[0], collect),
                delivery_tw_early=order.delivery.time_windows[0].start
                if order.delivery.time_windows else shift_start_of(problem),
                delivery_tw_late=_delivery_deadline(problem, order),
                delivery_service_duration=service_time(
                    order, problem.vehicles[0], drop),
                amount=[order.quantities.get(name, 0) for name in dimensions],
                prize=order.prize + bonuses[precedence(order)],
                required=_is_required(order),
                name=order.id,
            )
            order_by_shipment[len(order_by_shipment)] = order.id
            continue

        stop = order.delivery or order.pickup
        location = problem.location(stop.location_id)
        # §6.1's signed load: a quantity is applied at pickup and released at
        # delivery, so which list it goes in is what makes the load profile
        # rise or fall. A pickup-only order compiled as a delivery -- which is
        # what happened before E-20 -- inverts the profile silently.
        amounts = [order.quantities.get(name, 0) for name in dimensions]
        delivered = amounts if order.delivery is not None else [0] * len(dimensions)
        collected = amounts if order.delivery is None else [0] * len(dimensions)
        shift_end = max(v.shift.end for v in problem.vehicles)
        shift_start = min(v.shift.start for v in problem.vehicles)
        windows = stop.time_windows or (None,)

        # FR-04: several disjoint windows become several clients at the same
        # place in one mutually-exclusive group, so exactly one is visited.
        # PyVRP requires group members to be optional and the *group* to carry
        # the requirement -- a required client inside a group is rejected.
        group = (model.add_client_group(required=True)
                 if len(windows) > 1 else None)

        for window in windows:
            early, late = _bounds(window, shift_start, shift_end)
            if order.release_time > late:
                # PyVRP raises "release_time must be <= tw_late" from deep
                # inside Client(), naming no order. Our model accepts the
                # combination -- it is infeasible, not invalid -- so it is
                # `preflight()`'s RELEASE_AFTER_WINDOW to report, and the
                # adapter's job is to say which order rather than die opaquely.
                raise ValueError(
                    f"order {order.id} is released at {order.release_time}, "
                    f"after its window closes at {late}; run "
                    f"vrp.diagnose.preflight() first -- this is "
                    f"RELEASE_AFTER_WINDOW, not a malformed instance")
            client = model.add_client(
                location=handles[location.matrix_index],
                delivery=delivered,
                pickup=collected,
                # FR-05, composed. PyVRP takes one service duration per
                # client, so a fleet whose vehicles differ in handling speed
                # cannot be expressed here -- see the note below.
                service_duration=service_time(order, problem.vehicles[0],
                                              location),
                tw_early=early,
                tw_late=late,
                release_time=order.release_time,
                prize=order.prize + bonuses[precedence(order)],
                # FR-12 and FR-13 together. A prize makes an order declinable,
                # but §4.1 defines tier 0 as must-serve, so a prize on a tier-0
                # order must not quietly make it optional -- which is what
                # `prize == 0` alone did.
                required=(False if group is not None
                          else _is_required(order)),
                group=group,
                name=order.id,
            )
            order_by_client[len(order_by_client)] = order.id
            del client

    # Built before the fleet, because a vehicle type names the
    # profile it routes on.
    profiles = _eligibility_profiles(problem, model)
    vehicle_ids: list[str] = []
    for vehicle in problem.vehicles:
        # PyVRP spells the duration limit `shift_duration`, and both limits
        # must be omitted rather than passed as None when unset.
        limits = {}
        if vehicle.max_duration is not None:
            limits["shift_duration"] = vehicle.max_duration
        if vehicle.max_distance is not None:
            limits["max_distance"] = vehicle.max_distance
        # FR-07: costs come from the vehicle. PyVRP names them differently and
        # takes them natively, so this is wiring rather than modelling.
        costs = {}
        if vehicle.fixed_cost:
            costs["fixed_cost"] = vehicle.fixed_cost
        if vehicle.cost_per_metre:
            costs["unit_distance_cost"] = vehicle.cost_per_metre
        if vehicle.cost_per_second:
            costs["unit_duration_cost"] = vehicle.cost_per_second
        if vehicle.overtime_cost_per_second:
            costs["unit_overtime_cost"] = vehicle.overtime_cost_per_second

        # FR-09: PyVRP models multi-trip natively as reload depots, so this is
        # wiring. §6.8 forbids approximating it by chaining single-trip plans,
        # which is what a hand-rolled version would end up doing.
        if vehicle.max_reloads and vehicle.reload_locations:
            limits["reload_depots"] = [
                depots[name] for name in sorted(vehicle.reload_locations)
                if name in depots]
            limits["max_reloads"] = vehicle.max_reloads

        model.add_vehicle_type(
            profile=profiles[_eligibility_key(problem, vehicle)][0],
            num_available=1,
            capacity=[vehicle.capacities.get(name, 0) for name in dimensions],
            start_depot=depots[vehicle.start_location_id],
            end_depot=open_sink if vehicle.open_route else depots[vehicle.ends_at],
            tw_early=vehicle.shift.start,
            tw_late=vehicle.shift.end,
            name=vehicle.id,
            **costs,
            **limits,
        )
        vehicle_ids.append(vehicle.id)

    matrix = problem.matrix
    # FR-10 and FR-11 are eligibility, and eligibility is a property of the
    # (vehicle, place) pair rather than of the plan. PyVRP expresses it with
    # profiles: a vehicle type routes on its own edge set, and a place it may
    # not enter simply has no edge into it. Omitting the edge is the library's
    # own "unconnected" marker, not a large finite cost the search can trade
    # against -- the same distinction MTX-5 makes for unreachable arcs.
    #
    # Before this, `add_vehicle_type` carried capacity, depots, shifts and
    # costs and nothing that made a client ineligible, so the search assigned
    # gas work to an electricity-only crew and the verifier rejected the plan
    # afterwards. Detecting a plan you had no way to avoid building is not
    # enforcement.
    for profile, forbidden in profiles.values():
        for origin in ordered:
            for destination in ordered:
                if origin.matrix_index == destination.matrix_index:
                    continue
                if destination.matrix_index in forbidden:
                    # Not reachable *for this profile*. Edges out of a
                    # forbidden place are harmless once nothing can arrive.
                    continue
                if not matrix.is_reachable(origin.matrix_index,
                                           destination.matrix_index):
                    # MTX-5: an unreachable pair is a hard-infeasible arc, so
                    # the edge is simply absent. Adding it at any finite cost
                    # is what lets a solver route through a road that does not
                    # exist.
                    continue
                model.add_edge(
                    handles[origin.matrix_index],
                    handles[destination.matrix_index],
                    distance=matrix.distance(origin.matrix_index,
                                             destination.matrix_index),
                    duration=matrix.duration(origin.matrix_index,
                                             destination.matrix_index),
                    profile=profile,
                )
        if open_sink is not None:
            for origin in ordered:
                model.add_edge(handles[origin.matrix_index], sink_handle,
                               distance=0, duration=0, profile=profile)

    return _Compiled(model=model, dimensions=dimensions,
                     order_by_client=order_by_client,
                     order_by_shipment=order_by_shipment,
                     vehicle_ids=vehicle_ids)


def map_solution(problem: Problem, compiled: _Compiled, best,
                 feasible: bool = True, solver: dict | None = None) -> Solution:
    """Turn a PyVRP solution back into ours, keeping the solver's own timings.

    `feasible` is PyVRP's own verdict and must be passed through. An early
    version of this mapper hardcoded `FEASIBLE`, and a one-vehicle instance
    with four times too little capacity came back labelled feasible with
    nothing unassigned. The independent verifier caught it -- `INV-5 load
    units=48 exceeds capacity 12` -- which is precisely the job it exists to
    do, but the adapter should not be the one lying.
    """
    dimensions = compiled.dimensions
    index_to_location = {location.matrix_index: location
                         for location in problem.locations}
    routes: list[Route] = []
    served: set[str] = set()

    for route in best.routes():
        vehicle_id = compiled.vehicle_ids[route.vehicle_type()]
        vehicle = problem.vehicle(vehicle_id)
        # Load is reconstructed rather than read back: PyVRP reports a route
        # total, and INV-5 is about the load carried at each step.
        # The vehicle leaves the depot carrying everything it will drop, and
        # nothing it will collect. Reconstructed per dimension rather than read
        # back: PyVRP reports a route total, and INV-5 is about the load at
        # each step -- which for a route that both drops and collects is a
        # different number (§6.1's peak, not the total).
        # Only job deliveries are loaded at the depot, and -- with multi-trip
        # (§6.8) -- only the ones for the *current* trip. Summing the whole
        # route had a 100 kg van leaving the depot carrying 180 kg of a
        # three-trip day, which INV-5 rightly rejected.
        #
        # Shipments are excluded by `is_client()` alone: a shipment activity is
        # never a client. An explicit `kind == "JOB"` test alongside it was
        # unfalsifiable, which is the signature of a guard that reads as
        # protection and provides none.
        activities = list(route)
        # From 1, not 0: position 0 is the START depot itself, and a scan
        # beginning there stops on its own boundary and loads nothing.
        on_board = _trip_load(problem, compiled, activities, 1, dimensions)

        steps: list[Step] = []
        for position, activity in enumerate(activities):
            if activity.is_depot():
                # A depot visit in the middle of a route is a reload, not an
                # end (§6.8). Mapping every one after the first as END gave a
                # multi-trip route three ENDs, which no invariant expected and
                # which hid the reload from INV-11 entirely.
                last = position == len(activities) - 1
                kind = "START" if not steps else ("END" if last else "RELOAD")
                if kind == "RELOAD":
                    # Restocked for the next trip only, which is what makes
                    # multi-trip legal at all: the van never holds more than one
                    # trip's worth.
                    on_board.update(_trip_load(problem, compiled, activities,
                                               position + 1, dimensions))
                    steps.append(Step(
                        type="RELOAD", location_id=vehicle.reload_locations and
                        next(iter(sorted(vehicle.reload_locations))) or
                        vehicle.start_location_id,
                        arrival=activity.start_time,
                        start_service=activity.start_time,
                        departure=activity.end_time,
                        load_after=dict(on_board)))
                    continue
                if kind == "END" and problem.vehicle(vehicle_id).open_route:
                    # The sink is a modelling device, not a place. An open
                    # route ends where it last stopped, which is the step
                    # already recorded -- so the END carries that location and
                    # the zero-cost arc to the sink never appears in the plan.
                    steps.append(Step(type="END", location_id=steps[-1].location_id,
                                      arrival=steps[-1].departure,
                                      start_service=steps[-1].departure,
                                      departure=steps[-1].departure,
                                      load_after=dict(steps[-1].load_after)))
                    continue
                location = index_to_location[
                    problem.location(
                        _depot_location_id(problem, route, activity)).matrix_index]
                steps.append(Step(type=kind, location_id=location.id,
                                  arrival=activity.start_time,
                                  start_service=activity.start_time,
                                  departure=activity.end_time,
                                  load_after=dict(on_board)))
                continue

            # Which index space this activity belongs to decides which order
            # it names. PyVRP numbers clients and shipments separately from
            # zero, so reading `idx` without checking would map shipment 0 onto
            # client 0 and report a well-formed plan naming the wrong stops.
            if activity.is_shipment():
                order_id = compiled.order_by_shipment[activity.idx]
                order = problem.order(order_id)
                collecting = activity.is_pickup()
                stop = order.pickup if collecting else order.delivery
                kind = "PICKUP" if collecting else "DELIVERY"
            else:
                order_id = compiled.order_by_client[activity.idx]
                order = problem.order(order_id)
                stop = order.delivery or order.pickup
                collecting = order.delivery is None
                kind = "DELIVERY" if order.delivery is not None else "PICKUP"

            served.add(order_id)
            for name in dimensions:
                quantity = order.quantities.get(name, 0)
                on_board[name] += quantity if collecting else -quantity
            steps.append(Step(
                type=kind,
                location_id=stop.location_id, order_id=order_id,
                # `start_time` is when service begins; arrival is that minus any
                # wait. PyVRP reports the wait separately, so this reconstructs
                # the arrival it implies rather than inventing one.
                arrival=activity.start_time - activity.wait_duration,
                start_service=activity.start_time,
                departure=activity.end_time,
                load_after=dict(on_board),
            ))
        routes.append(Route(vehicle_id=vehicle_id, steps=tuple(steps)))

    unassigned = tuple(
        {"order_id": order.id, "reason_code": "NOT_PLACED",
         "explanation": "the solver could not place this order within the "
                        "fleet's capacity and time constraints"}
        for order in problem.orders if order.id not in served
    )
    return Solution(problem_id=problem.id, routes=tuple(routes),
                    unassigned=unassigned,
                    objective_breakdown={},
                    status="FEASIBLE" if feasible else "INFEASIBLE",
                    # NFR-04: the plan carries what its matrix was. A plan
                    # costed against arcs nobody measured is not wrong, but a
                    # dispatcher deciding whether to send it needs to know,
                    # and the matrix is the only thing that does.
                    degraded=problem.matrix.degraded,
                    solver=dict(solver or {}) or None)


def _trip_load(problem: Problem, compiled: _Compiled, activities: list,
               start: int, dimensions: tuple[str, ...]) -> dict[str, int]:
    """What the vehicle carries out of the depot for one trip. §6.8.

    A trip runs from `start` to the next depot visit. Everything the vehicle
    will drop before then is on board when it leaves; everything after is
    collected on a later reload.
    """
    total = dict.fromkeys(dimensions, 0)
    for activity in activities[start:]:
        if activity.is_depot():
            break
        if not activity.is_client():
            continue
        order = problem.order(compiled.order_by_client[activity.idx])
        if order.delivery is None:
            continue
        for name in dimensions:
            total[name] += order.quantities.get(name, 0)
    return total


def _depot_location_id(problem: Problem, route, activity) -> str:
    """Which depot a depot-activity refers to.

    A route may start and end at different depots, so the first depot activity
    is the start and any later one is the end.
    """
    vehicle = problem.vehicle(
        problem.vehicles[route.vehicle_type()].id)
    first = next(iter(route))
    return vehicle.start_location_id if activity is first else vehicle.ends_at


def _pyvrp_version() -> str:
    """PyVRP's version, or "unknown" -- it exposes no __version__ attribute."""
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("pyvrp")
    except PackageNotFoundError:
        return "unknown"


def _nothing_to_dispatch(problem: Problem, solver: dict) -> Solution:
    """The empty fleet, answered rather than raised. `UC-071`.

    A bank-holiday roster or a failed fleet feed produces a well-formed problem
    with no vehicles in it. That is a state of the world, not a malformed
    request: the answer is a feasible plan with no routes and every order
    unassigned with a reason an operator can act on. Compiling it instead took
    `max()` over an empty fleet and raised `ValueError`, which reaches a
    dispatcher as a 500 and says nothing about the roster.
    """
    return Solution(
        problem_id=problem.id, routes=(), degraded=problem.matrix.degraded,
        unassigned=tuple(
            {"order_id": order.id, "reason_code": "FLEET_EXHAUSTED",
             "explanation": "no vehicles are available in this planning run"}
            for order in problem.orders),
        objective_breakdown={}, status="FEASIBLE", solver=solver or None)


def solve(problem: Problem, iterations: int = 500, seed: int = 0) -> Solution:
    """Compile, solve, and map back. Deterministic for a given seed (CON-4)."""
    record = {"solver": f"pyvrp:{_pyvrp_version()}", "seed": seed,
              "iterations": iterations, "matrix_version": problem.matrix.version}
    if not problem.vehicles:
        return _nothing_to_dispatch(problem, record)
    compiled = compile_problem(problem)
    result = compiled.model.solve(stop=MaxIterations(iterations), seed=seed,
                                  display=False)
    return map_solution(problem, compiled, result.best,
                        feasible=result.is_feasible(),
                        # CON-4's replay record: everything needed to reproduce
                        # this exact plan, written down rather than remembered.
                        solver=record)
