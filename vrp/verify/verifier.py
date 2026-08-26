"""The independent verifier — SDD §11.2, CON-1, INV-1…INV-9.

Answers one question: *is this plan legal?* — independently of whatever
produced it.

**It shares no code with the evaluator or any solver.** Every number is
recomputed here from the raw step sequences and the matrix pinned in the
problem. Importing `vrp.evaluator` would defeat the entire purpose: a solver
graded by the arithmetic it already used will agree with itself, and the class
of bug this exists to catch — drift between an incremental move evaluator and
ground truth — would pass unnoticed. `test_the_verifier_does_not_import_the_
evaluator` enforces that by reading this file's imports.

The domain types are shared, and only those. They are data definitions rather
than logic, and both sides must agree on what a `Step` is or they cannot discuss
the same plan.

Invariants with no subject are reported **not applicable**, never passed.
INV-8 still needs locks (T-29). INV-7 is evaluated whenever a vehicle declares
an hours-of-service rule set, and reported not applicable only when none does.
Returning "ok" for an invariant that was never evaluated is a lie that survives
until someone ships an illegal duty timeline.

INV-7 imports the *rule sets* but never the scheduler. The rules are shared
reference data in the same sense as the domain types -- both sides must agree on
what EC-561/2006 Art.7 says or they cannot discuss the same duty -- whereas the
scheduler is the thing under judgement. The driver state here is rebuilt from
the timeline's own arrival and departure stamps, so a scheduler that miscounts
its driving hours is caught rather than confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise

from vrp.hos.rules import Activity, rules_for
from vrp.model import Problem, Solution, Step, service_time

# Invariants this verifier cannot yet evaluate, and why. Empty: every invariant
# now has a subject when its instance provides one. INV-7 and INV-8 are added
# per-problem below when nothing declares hours or locks.
NOT_APPLICABLE: dict[str, str] = {}
NO_HOS_DECLARED = "no vehicle declares an hours-of-service rule set"
NO_LOCKS_DECLARED = "the problem declares no locks"
NO_HOS_DECLARED = "no vehicle declares an hours-of-service rule set"


@dataclass(frozen=True)
class Violation:
    invariant: str
    detail: str
    vehicle_id: str | None = None
    order_id: str | None = None

    def __str__(self) -> str:
        where = f" [{self.vehicle_id or '-'}/{self.order_id or '-'}]"
        return f"{self.invariant}{where} {self.detail}"


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)
    not_applicable: set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return not self.violations

    def fail(self, invariant: str, detail: str, **where) -> None:
        self.violations.append(Violation(invariant, detail, **where))


def verify(problem: Problem, solution: Solution) -> Report:
    """Check every invariant that has a subject. §4.3."""
    report = Report(not_applicable=set(NOT_APPLICABLE))
    if not any(vehicle.hos_rules for vehicle in problem.vehicles):
        report.not_applicable.add("INV-7")
    if not problem.locks:
        report.not_applicable.add("INV-8")

    _check_coverage(problem, solution, report)               # INV-1, INV-2
    for route in solution.routes:
        _check_route(problem, route, report)                 # INV-3..INV-6
        _check_hours_of_service(problem, route, report)      # INV-7
    for route in solution.routes:
        _check_compatibility(problem, route, report)         # INV-10
    _check_locks(problem, solution, report)                  # INV-8
    _check_objective(problem, solution, report)              # INV-9
    return report


def _check_compatibility(problem: Problem, route, report: Report) -> None:
    """INV-10: skills, order-to-order classes, and site access. FR-10, FR-11.

    Numbered past §4.3's INV-9 deliberately. §4.3 lists nine invariants and
    none of them covers compatibility, so a plan putting a tail-lift load on a
    van without one satisfied every invariant the specification names. The
    machinery existed -- `required_skills` since E-01, checked by `preflight`
    since E-14 -- and nothing checked a finished plan against it, which is
    worse than having no skill model at all because it invites people to rely
    on one.

    Order-to-order incompatibility is tracked as a running set of classes on
    the route rather than by comparing every pair. §6.5 requires that: pairwise
    checking is O(n^2) per move, and this is the same shape the solver would
    need.
    """
    vehicle = problem.vehicle(route.vehicle_id)
    carried: set[str] = set()
    forbidden: set[str] = set()

    for step in route.steps:
        if step.order_id is None:
            continue
        order = problem.order(step.order_id)

        missing = order.required_skills - vehicle.skills
        if missing:
            report.fail("INV-10", f"{vehicle.id} lacks {sorted(missing)}",
                        vehicle_id=route.vehicle_id, order_id=step.order_id)

        # Both directions from one pass: what this order forbids, and what
        # already on board forbids it. Checking only the first would let the
        # same pair through whenever they were loaded the other way round.
        if order.order_class and order.order_class in forbidden:
            report.fail("INV-10",
                        f"{order.order_class} shares a route with a class that "
                        f"forbids it", vehicle_id=route.vehicle_id,
                        order_id=step.order_id)
        clash = order.incompatible_with & carried
        if clash:
            report.fail("INV-10",
                        f"incompatible with {sorted(clash)} already on board",
                        vehicle_id=route.vehicle_id, order_id=step.order_id)
        if order.order_class:
            carried.add(order.order_class)
        forbidden |= order.incompatible_with

        location = problem.location(step.location_id)
        if (location.access_classes
                and vehicle.access_class not in location.access_classes):
            report.fail("INV-10",
                        f"{vehicle.id} is {vehicle.access_class!r}; "
                        f"{location.id} admits {sorted(location.access_classes)}",
                        vehicle_id=route.vehicle_id, order_id=step.order_id)
        if (location.max_vehicle_kg is not None
                and vehicle.gross_weight_kg is not None
                and vehicle.gross_weight_kg > location.max_vehicle_kg):
            report.fail("INV-10",
                        f"{vehicle.gross_weight_kg} kg exceeds {location.id}'s "
                        f"{location.max_vehicle_kg} kg limit",
                        vehicle_id=route.vehicle_id, order_id=step.order_id)


def _check_locks(problem: Problem, solution: Solution, report: Report) -> None:
    """INV-8: every lock in §6.6 satisfied exactly.

    "Exactly" is the operative word. A lock is an operator overriding the
    optimiser, usually because they know something the model does not, so a
    lock that is *nearly* honoured is an instruction that was ignored.

    This judges a finished plan. Making the solver plan within locks, and
    diagnosing a conflicting set down to a minimal irreducible core (§6.6's
    IIS requirement), are the rest of `T-29` and are not here -- so a violation
    reported by this function today is most likely the solver not knowing about
    locks at all, which is itself worth seeing.
    """
    if not problem.locks:
        return

    route_of: dict[str, str] = {}
    sequence: dict[str, list[str]] = {}
    for route in solution.routes:
        served = [step.order_id for step in route.steps if step.order_id]
        sequence[route.vehicle_id] = served
        for order_id in served:
            route_of[order_id] = route.vehicle_id
    deployed = {vehicle_id for vehicle_id, served in sequence.items() if served}

    for lock in problem.locks:
        _check_one_lock(problem, solution, lock, route_of, sequence,
                        deployed, report)


def _check_one_lock(problem: Problem, solution: Solution, lock, route_of: dict,
                    sequence: dict, deployed: set, report: Report) -> None:
    """One lock, one verdict. Split out so each kind reads as its own rule."""
    def fail(detail: str) -> None:
        report.fail("INV-8", f"{lock.kind}: {detail}",
                    vehicle_id=lock.vehicle_id, order_id=lock.order_id)

    if lock.kind == "PIN_ORDER_TO_VEHICLE":
        carrier = route_of.get(lock.order_id)
        if carrier != lock.vehicle_id:
            fail(f"{lock.order_id} is on {carrier or 'no route'}, "
                 f"not {lock.vehicle_id}")

    elif lock.kind == "FORBID_ORDER_ON_VEHICLE":
        if route_of.get(lock.order_id) == lock.vehicle_id:
            fail(f"{lock.order_id} is on {lock.vehicle_id}, which is forbidden")

    elif lock.kind == "FIX_ROUTE_PREFIX":
        served = sequence.get(lock.vehicle_id, [])
        wanted = list(lock.order_ids)
        if served[:len(wanted)] != wanted:
            fail(f"{lock.vehicle_id} begins {served[:len(wanted)]}, "
                 f"not {wanted}")

    elif lock.kind == "FIX_SEQUENCE":
        served = sequence.get(lock.vehicle_id, [])
        positions = [served.index(o) for o in lock.order_ids if o in served]
        if positions != sorted(positions):
            fail(f"{list(lock.order_ids)} are out of order on {lock.vehicle_id}")

    elif lock.kind == "FORCE_DEPLOY":
        if lock.vehicle_id not in deployed:
            fail(f"{lock.vehicle_id} was required to deploy and did not")

    elif lock.kind == "FORBID_DEPLOY":
        if lock.vehicle_id in deployed:
            fail(f"{lock.vehicle_id} was forbidden to deploy and did")

    elif lock.kind == "PIN_DEPOT":
        carrier = route_of.get(lock.order_id)
        if carrier is None:
            fail(f"{lock.order_id} is unassigned, so no depot serves it")
        else:
            start = problem.vehicle(carrier).start_location_id
            if start != lock.depot_id:
                fail(f"{lock.order_id} is served from {start}, "
                     f"not {lock.depot_id}")

    elif lock.kind == "FREEZE_UNTIL":
        # Everything happening before the horizon must already have been
        # committed, which a plan expresses by locking it in place. Anything
        # else inside the window is the optimiser rewriting the past.
        pinned = {
            order_id
            for other in problem.locks
            if other.kind in ("FIX_ROUTE_PREFIX", "FIX_SEQUENCE",
                              "PIN_ORDER_TO_VEHICLE")
            for order_id in ((other.order_id,) if other.order_id
                             else other.order_ids)
        }
        early = [step.order_id for route in solution.routes
                 for step in route.steps
                 if step.order_id and step.start_service < lock.instant
                 and step.order_id not in pinned]
        if early:
            fail(f"{len(early)} stop(s) are served before the freeze at "
                 f"{lock.instant} without being pinned: {early[:3]}")


def _check_hours_of_service(problem: Problem, route, report: Report) -> None:
    """INV-7: the driving-hours timeline satisfies the active rule set.

    Rebuilt from the timeline rather than read back from the scheduler. Time
    between one step's departure and the next step's arrival is driving; a
    BREAK step is a break; waiting at a stop is WAIT and servicing it is WORK.
    That is the same reading a tachograph would take of the plan, which is the
    point -- it is the reading that has legal consequences.
    """
    vehicle = problem.vehicle(route.vehicle_id)
    if not vehicle.hos_rules:
        return
    try:
        rules = rules_for(vehicle.hos_rules)
    except ValueError as unknown:
        report.fail("INV-7", str(unknown), vehicle_id=route.vehicle_id)
        return

    state = rules.init_state(vehicle.initial_state)
    for previous, current in pairwise(route.steps):
        driving = current.arrival - previous.departure
        if driving < 0:
            report.fail("INV-7", f"step at {current.location_id} arrives before "
                                 f"the previous departure", vehicle_id=route.vehicle_id)
            return
        if driving:
            # Checked before it is consumed: the question is whether this leg
            # was legal to drive, not whether the totals happen to add up after.
            if not rules.can_drive(state, driving):
                report.fail(
                    "INV-7",
                    f"drove {driving}s to {current.location_id} with only "
                    f"{rules.drive_until_break(state)}s legally available "
                    f"under {rules.name}", vehicle_id=route.vehicle_id)
                return
            state = rules.advance(state, Activity.DRIVE, driving)

        span = current.departure - current.arrival
        if current.type == "BREAK":
            if span < rules.break_duration:
                report.fail("INV-7", f"break of {span}s is shorter than the "
                                     f"{rules.break_duration}s {rules.name} requires",
                            vehicle_id=route.vehicle_id)
                return
            state = rules.advance(state, Activity.BREAK, span)
            continue
        if current.waiting:
            state = rules.advance(state, Activity.WAIT, current.waiting)
        state = rules.advance(state, Activity.WORK,
                              current.departure - current.start_service)

    if state.drive_used > rules.max_drive:
        report.fail("INV-7", f"duty drove {state.drive_used}s past the "
                             f"{rules.max_drive}s {rules.name} daily limit",
                    vehicle_id=route.vehicle_id)
    if state.duty_used > rules.max_duty:
        report.fail("INV-7", f"duty spanned {state.duty_used}s past the "
                             f"{rules.max_duty}s {rules.name} window",
                    vehicle_id=route.vehicle_id)


def _served_orders(solution: Solution) -> list[tuple[str, str]]:
    """(order_id, vehicle_id) for every step that serves an order."""
    return [(step.order_id, route.vehicle_id)
            for route in solution.routes
            for step in route.steps
            if step.order_id is not None]


def _check_coverage(problem: Problem, solution: Solution, report: Report) -> None:
    """INV-1: exactly once across routes ∪ unassigned. INV-2: shipment pairing."""
    served = _served_orders(solution)
    counts: dict[str, int] = {}
    for order_id, _vehicle in served:
        counts[order_id] = counts.get(order_id, 0) + 1

    unassigned = {entry["order_id"] for entry in solution.unassigned}

    for order in problem.orders:
        appearances = counts.get(order.id, 0)
        expected = 2 if order.kind == "SHIPMENT" else 1
        if order.id in unassigned:
            if appearances:
                report.fail("INV-1", "listed unassigned but also served",
                            order_id=order.id)
            continue
        if appearances == 0:
            report.fail("INV-1", "neither served nor listed unassigned",
                        order_id=order.id)
        elif appearances != expected:
            report.fail("INV-1", f"served {appearances} times, expected {expected}",
                        order_id=order.id)

    known = {order.id for order in problem.orders}
    for order_id, _vehicle in served:
        if order_id not in known:
            report.fail("INV-1", "served an order the problem does not contain",
                        order_id=order_id)

    # INV-2: both ends of a shipment on one route, pickup strictly first.
    for order in problem.orders:
        if order.kind != "SHIPMENT":
            continue
        positions = [(route.vehicle_id, index, step.type)
                     for route in solution.routes
                     for index, step in enumerate(route.steps)
                     if step.order_id == order.id]
        if len(positions) != 2:
            continue                                   # already reported by INV-1
        (vehicle_a, index_a, type_a), (vehicle_b, index_b, type_b) = positions
        if vehicle_a != vehicle_b:
            report.fail("INV-2", "pickup and delivery on different routes",
                        order_id=order.id)
        elif not (type_a == "PICKUP" and type_b == "DELIVERY" and index_a < index_b):
            report.fail("INV-2", "delivery does not follow its pickup",
                        order_id=order.id)


def _windows_for(problem: Problem, step: Step) -> tuple:
    """The windows that apply to a step, or () when none do."""
    if step.order_id is None:
        return ()
    order = problem.order(step.order_id)
    spec = order.delivery if step.type == "DELIVERY" else order.pickup
    return spec.time_windows if spec is not None else ()


def _service_of(problem: Problem, step: Step, vehicle) -> int:
    """How long the problem says this stop takes. FR-05.

    Uses `vrp.model.service_time`, which is a statement of the problem rather
    than the evaluator's arithmetic -- the same category as the travel matrix
    and the hours-of-service rule sets, which this verifier also shares. What
    it must not share is any *computation over a plan*, and it does not.

    Restating FR-05's four-term formula here instead would give the project two
    definitions of how long a delivery takes, and the verifier would eventually
    reject correct plans for disagreeing with itself.
    """
    if step.order_id is None:
        return 0
    order = problem.order(step.order_id)
    spec = order.delivery if step.type == "DELIVERY" else order.pickup
    if spec is None:
        return 0
    return service_time(order, vehicle, problem.location(step.location_id))


def _check_route(problem: Problem, route, report: Report) -> None:
    """INV-3 timeline consistency, INV-4 travel, INV-5 load, INV-6 limits."""
    vehicle = problem.vehicle(route.vehicle_id)
    steps = route.steps
    if not steps:
        return

    for step in steps:
        # INV-3: arrival ≤ start_service, and service takes exactly as long as
        # the problem says it does.
        if step.arrival > step.start_service:
            report.fail("INV-3", f"service starts {step.start_service} before "
                                 f"arrival {step.arrival}",
                        vehicle_id=route.vehicle_id, order_id=step.order_id)
        expected_departure = step.start_service + _service_of(
            problem, step, vehicle)
        if step.order_id is not None and step.departure != expected_departure:
            report.fail("INV-3", f"departure {step.departure} does not equal "
                                 f"start_service + service ({expected_departure})",
                        vehicle_id=route.vehicle_id, order_id=step.order_id)

        windows = _windows_for(problem, step)
        hard = [w for w in windows if w.hardness == "HARD"]
        if hard and not any(w.contains(step.start_service) for w in hard):
            report.fail("INV-3", f"service at {step.start_service} falls outside "
                                 f"every hard window",
                        vehicle_id=route.vehicle_id, order_id=step.order_id)

        # INV-5: load within capacity on every dimension, and never negative.
        for dimension, amount in step.load_after.items():
            if amount < 0:
                report.fail("INV-5", f"load {dimension}={amount} is negative",
                            vehicle_id=route.vehicle_id, order_id=step.order_id)
            limit = vehicle.capacities.get(dimension)
            if limit is not None and amount > limit:
                report.fail("INV-5", f"load {dimension}={amount} exceeds "
                                     f"capacity {limit}",
                            vehicle_id=route.vehicle_id, order_id=step.order_id)

    # INV-4: every arrival follows from the previous departure, the pinned
    # matrix, and any breaks taken on the way. Recomputed here from the matrix,
    # never taken from the solution.
    #
    # A break splits one leg into two, so BREAK steps are collapsed rather than
    # treated as destinations: the invariant is over consecutive *stops*, with
    # en-route break time added. Reading them as ordinary steps made every
    # break-bearing route fail INV-4 twice -- once for the shortened leg into
    # the break and once for the zero-length leg out of it.
    matrix = problem.matrix
    en_route_breaks = 0
    previous = None
    for current in steps:
        if current.type == "BREAK":
            en_route_breaks += current.departure - current.arrival
            continue
        if previous is None:
            previous = current
            continue
        origin = problem.location(previous.location_id).matrix_index
        destination = problem.location(current.location_id).matrix_index
        expected = (previous.departure + matrix.duration(origin, destination)
                    + en_route_breaks)
        en_route_breaks = 0
        if current.arrival != expected:
            report.fail("INV-4", f"arrival {current.arrival} at "
                                 f"{current.location_id} should be {expected} "
                                 f"per matrix {matrix.version}",
                        vehicle_id=route.vehicle_id, order_id=current.order_id)
        previous = current

    # INV-6: duration, distance, and the shift window. Breaks are excluded from
    # the distance walk for the same reason as INV-4 -- a break is a pause on an
    # arc, not a place the vehicle drove to, and counting it as one would add a
    # spurious leg to and from the break's nominal location.
    driving_steps = [s for s in steps if s.type != "BREAK"]
    duration = steps[-1].arrival - steps[0].departure
    distance = sum(
        matrix.distance(problem.location(a.location_id).matrix_index,
                        problem.location(b.location_id).matrix_index)
        for a, b in pairwise(driving_steps)
    )
    if vehicle.max_duration is not None and duration > vehicle.max_duration:
        report.fail("INV-6", f"duration {duration} exceeds max_duration "
                             f"{vehicle.max_duration}", vehicle_id=route.vehicle_id)
    if vehicle.max_distance is not None and distance > vehicle.max_distance:
        report.fail("INV-6", f"distance {distance} exceeds max_distance "
                             f"{vehicle.max_distance}", vehicle_id=route.vehicle_id)
    if steps[0].departure < vehicle.shift.start or steps[-1].arrival > vehicle.shift.end:
        report.fail("INV-6", f"route spans {steps[0].departure}..{steps[-1].arrival}, "
                             f"outside shift {vehicle.shift.start}..{vehicle.shift.end}",
                    vehicle_id=route.vehicle_id)


def _check_objective(problem: Problem, solution: Solution, report: Report) -> None:
    """INV-9 — recompute the reported breakdown from the routes themselves.

    The SDD calls this the single most valuable test in the system, because
    objective drift is invisible: the solver reports a number it believes and
    nothing else ever recomputes it.

    Only components the solution actually reports are checked; the verifier does
    not impose a cost model, it checks the one that was claimed.
    """
    reported = solution.objective_breakdown
    if not reported:
        return
    matrix = problem.matrix

    recomputed: dict[str, int] = {"distance": 0, "driving_seconds": 0}
    for route in solution.routes:
        for previous, current in pairwise(route.steps):
            origin = problem.location(previous.location_id).matrix_index
            destination = problem.location(current.location_id).matrix_index
            recomputed["distance"] += matrix.distance(origin, destination)
            recomputed["driving_seconds"] += matrix.duration(origin, destination)

    for component, claimed in reported.items():
        if component not in recomputed:
            continue                       # nothing to check it against
        if claimed != recomputed[component]:
            report.fail("INV-9", f"reported {component}={claimed}, "
                                 f"recomputed {recomputed[component]}")
