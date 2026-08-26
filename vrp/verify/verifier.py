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

Invariants with no subject yet are reported **not applicable**, never passed:
INV-7 needs the hours-of-service rules engine (T-25) and INV-8 needs locks
(T-29). Returning "ok" for an invariant that was never evaluated is a lie that
survives until someone ships an illegal duty timeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise

from vrp.model import Problem, Solution, Step

# Invariants this verifier cannot yet evaluate, and why.
NOT_APPLICABLE = {
    "INV-7": "no hours-of-service rules engine (T-25)",
    "INV-8": "no lock model (T-29)",
}


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

    _check_coverage(problem, solution, report)               # INV-1, INV-2
    for route in solution.routes:
        _check_route(problem, route, report)                 # INV-3..INV-6
    _check_objective(problem, solution, report)              # INV-9
    return report


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


def _service_of(problem: Problem, step: Step) -> int:
    if step.order_id is None:
        return 0
    order = problem.order(step.order_id)
    spec = order.delivery if step.type == "DELIVERY" else order.pickup
    overhead = problem.location(step.location_id).dwell_overhead
    return (spec.service_fixed if spec is not None else 0) + overhead


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
        expected_departure = step.start_service + _service_of(problem, step)
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

    # INV-4: every arrival follows from the previous departure and the pinned
    # matrix. Recomputed here from the matrix, never taken from the solution.
    matrix = problem.matrix
    for previous, current in pairwise(steps):
        origin = problem.location(previous.location_id).matrix_index
        destination = problem.location(current.location_id).matrix_index
        expected = previous.departure + matrix.duration(origin, destination)
        if current.arrival != expected:
            report.fail("INV-4", f"arrival {current.arrival} at "
                                 f"{current.location_id} should be {expected} "
                                 f"per matrix {matrix.version}",
                        vehicle_id=route.vehicle_id, order_id=current.order_id)

    # INV-6: duration, distance, and the shift window.
    duration = steps[-1].arrival - steps[0].departure
    distance = sum(
        matrix.distance(problem.location(a.location_id).matrix_index,
                        problem.location(b.location_id).matrix_index)
        for a, b in pairwise(steps)
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
