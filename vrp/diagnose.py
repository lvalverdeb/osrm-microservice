"""Pre-flight rejection diagnosis — §6.5, §7.9, FR-01, AC-1.3, T-14.

§6.5 ends with the sentence this module exists to honour: "Each reason MUST be
produced by an explicit diagnostic pass, not inferred." A solver that cannot
place an order knows only that it could not. Turning that into
`CAPACITY_EXCEEDED` by looking at what seems likely is how a dispatcher is told
the wrong thing with total confidence, spends an afternoon finding a larger van,
and discovers the real problem was a tail lift.

**Pre-flight means before any solve, and one order at a time.** The question is
whether a single order is servable by *any* vehicle, ignoring every other order.
That narrowness is what makes the answer trustworthy: it depends on nothing that
a search might have done differently. An order that passes here can still go
unassigned, and that is a different code with a different fix.

Six of §6.5's ten codes are decidable this way. The other four are declared in
`UNIMPLEMENTED` with the reason, because a caller waiting for a code that never
arrives has no way to tell "not applicable" from "not built".

Placement: Python. This reads the constraint model and is what the `/solve`
response quotes when it reports an unassigned order; it changes whenever the
constraints do.
"""

from __future__ import annotations

from dataclasses import dataclass

from vrp.hos.rules import rules_for
from vrp.model import Order, Problem, Vehicle

# §6.5's closed vocabulary. Emitting anything outside it gives consumers a
# string they cannot branch on.
REASONS: dict[str, str] = {
    "NO_ELIGIBLE_VEHICLE": "no vehicle has the required skills / access class",
    "CAPACITY_EXCEEDED": "order alone exceeds every eligible vehicle's capacity",
    "TIME_WINDOW_UNREACHABLE": "no eligible vehicle can arrive within any window",
    "RELEASE_AFTER_WINDOW": "goods available only after the last window closes",
    "DUTY_LIMIT": "serving it cannot fit any legal duty",
    "INCOMPATIBLE_ONLY": "eligible only with orders it is incompatible with",
    "FLEET_EXHAUSTED": "feasible but no capacity remained at this priority",
    "DROPPED_BY_PRIZE": "prize below marginal cost in prize-collecting mode",
    "LOCK_CONFLICT": "operator lock made assignment impossible",
    "DEPOT_STOCKOUT": "no depot with inventory can serve it in window",
}

# Named rather than omitted: "we cannot decide this" and "this never happens"
# look identical from outside, and only one of them is a reason to wait.
UNIMPLEMENTED: dict[str, str] = {
    "INCOMPATIBLE_ONLY": "order-to-order incompatibility is not modelled (§6.5)",
    "FLEET_EXHAUSTED": "needs a solve; a pre-flight pass cannot know what the "
                       "fleet had left",
    "DROPPED_BY_PRIZE": "needs a solve; the marginal cost is only known once a "
                        "route exists",
    "DEPOT_STOCKOUT": "depot inventory is not modelled",
}


@dataclass(frozen=True)
class Finding:
    """Why one order cannot be served, and what was ruled out getting there."""

    order_id: str
    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.order_id}: {self.code} -- {self.detail}"


def _eligible(problem: Problem, order: Order) -> list[Vehicle]:
    """Vehicles allowed to take this order at all. §6.5, plus §6.6's pins.

    A pin narrows eligibility rather than being checked afterwards, which is
    what makes LOCK_CONFLICT fall out of the ordinary checks instead of needing
    its own parallel set of them.
    """
    # A vehicle forbidden to deploy is not eligible for anything. Missing this
    # made an order pinned to a forbidden vehicle look perfectly servable --
    # the pin said "only V2" and nothing said V2 was staying in the yard.
    forbidden = {lock.vehicle_id for lock in problem.locks
                 if lock.kind == "FORBID_DEPLOY"}
    fleet = [v for v in problem.vehicles
             if order.required_skills <= v.skills and v.id not in forbidden]
    for lock in problem.locks:
        if lock.order_id != order.id:
            continue
        if lock.kind == "PIN_ORDER_TO_VEHICLE":
            fleet = [v for v in fleet if v.id == lock.vehicle_id]
        elif lock.kind == "FORBID_ORDER_ON_VEHICLE":
            fleet = [v for v in fleet if v.id != lock.vehicle_id]
        elif lock.kind == "PIN_DEPOT":
            fleet = [v for v in fleet if v.start_location_id == lock.depot_id]
    return fleet


def _pinned(problem: Problem, order: Order) -> bool:
    """Whether an operator lock narrowed this order's options.

    FORBID_DEPLOY counts: it does not name the order, but it is what removed
    the vehicle the order needed, and a dispatcher told "no eligible vehicle"
    would go looking for a missing skill rather than at the van they grounded.
    """
    if any(lock.kind == "FORBID_DEPLOY" for lock in problem.locks):
        return True
    return any(lock.order_id == order.id
               and lock.kind in ("PIN_ORDER_TO_VEHICLE", "PIN_DEPOT",
                                 "FORBID_ORDER_ON_VEHICLE")
               for lock in problem.locks)


def _fits(order: Order, vehicle: Vehicle) -> bool:
    """Every dimension, because a van full on any one of them is full (§6.1)."""
    return all(quantity <= vehicle.capacities.get(dimension, 0)
               for dimension, quantity in order.quantities.items())


def _reachable(problem: Problem, order: Order, vehicle: Vehicle) -> bool:
    """Could this vehicle start at its depot and serve the stop in a window?

    Optimistic on purpose: straight out from the depot with nothing else on
    board. If that cannot make the window, nothing can, and a pre-flight pass
    that guessed pessimistically would reject work the solver could have done.
    """
    stop = order.delivery or order.pickup
    hard = [w for w in stop.time_windows if w.hardness == "HARD"]
    if not hard:
        return True                    # soft windows are priced, not walls

    start = problem.location(vehicle.start_location_id).matrix_index
    destination = problem.location(stop.location_id).matrix_index
    if not problem.matrix.is_reachable(start, destination):
        return False
    arrival = max(vehicle.shift.start, order.release_time) \
        + problem.matrix.duration(start, destination)
    return any(arrival <= window.end for window in hard)


def _duty_fits(problem: Problem, order: Order, vehicle: Vehicle) -> bool:
    """Can a legal duty contain the round trip to this stop and back?

    Out and back with one service is the cheapest possible duty containing the
    order, so failing it means no duty contains it.
    """
    if not vehicle.hos_rules:
        return True
    stop = order.delivery or order.pickup
    matrix = problem.matrix
    start = problem.location(vehicle.start_location_id).matrix_index
    destination = problem.location(stop.location_id).matrix_index
    home = problem.location(vehicle.ends_at).matrix_index
    if not (matrix.is_reachable(start, destination)
            and matrix.is_reachable(destination, home)):
        return False

    driving = matrix.duration(start, destination) + matrix.duration(destination, home)
    rules = rules_for(vehicle.hos_rules)
    return driving <= rules.remaining_drive(rules.init_state(vehicle.initial_state))


def preflight(problem: Problem) -> dict[str, Finding]:
    """Diagnose every order that no vehicle can serve on its own.

    Args:
        problem: the instance, including any operator locks.

    Returns:
        One `Finding` per unservable order, keyed by order id. Servable orders
        are absent rather than present-and-empty, so a caller iterating the
        result is iterating problems.

    The checks run in a fixed order and the first failure wins, because an
    order usually fails several ways at once and only one of them is worth
    telling somebody about. Eligibility comes first since it decides which
    vehicles the rest even consider; capacity next because no rescheduling
    fixes it; then timing, which sometimes can be.
    """
    findings: dict[str, Finding] = {}

    for order in problem.orders:
        fleet = _eligible(problem, order)
        conflict = _pinned(problem, order)

        def report(code: str, detail: str, order=order, conflict=conflict) -> None:
            # A pinned order failing a check is a LOCK_CONFLICT: the underlying
            # reason is real, but what a dispatcher needs to know is that their
            # own instruction is what removed the alternatives.
            findings[order.id] = Finding(
                order_id=order.id,
                code="LOCK_CONFLICT" if conflict else code,
                detail=f"{detail} (under an operator lock)" if conflict else detail)

        if not fleet:
            report("NO_ELIGIBLE_VEHICLE",
                   f"requires {sorted(order.required_skills) or 'no skills'}; "
                   f"no vehicle qualifies")
            continue

        if not any(_fits(order, vehicle) for vehicle in fleet):
            biggest = max((max(v.capacities.values(), default=0) for v in fleet),
                          default=0)
            report("CAPACITY_EXCEEDED",
                   f"needs {order.quantities}; largest eligible capacity {biggest}")
            continue

        stop = order.delivery or order.pickup
        hard = [w for w in stop.time_windows if w.hardness == "HARD"]
        if hard and order.release_time > max(w.end for w in hard):
            report("RELEASE_AFTER_WINDOW",
                   f"released at {order.release_time}, last window closes "
                   f"at {max(w.end for w in hard)}")
            continue

        usable = [v for v in fleet if _fits(order, v)]
        if not any(_reachable(problem, order, vehicle) for vehicle in usable):
            report("TIME_WINDOW_UNREACHABLE",
                   "no eligible vehicle can reach the stop inside a hard window")
            continue

        if not any(_duty_fits(problem, order, vehicle) for vehicle in usable):
            report("DUTY_LIMIT",
                   "the round trip alone exceeds every eligible driver's "
                   "remaining legal driving time")

    return findings
