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
    "FLEET_EXHAUSTED": "needs a solve; a pre-flight pass cannot know what the "
                       "fleet had left",
    "DROPPED_BY_PRIZE": "needs a solve; the marginal cost is only known once a "
                        "route exists",
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
    stop = order.delivery or order.pickup
    site = problem.location(stop.location_id)
    fleet = [v for v in problem.vehicles
             if order.required_skills <= v.skills
             and v.id not in forbidden
             and _may_enter(v, site)]
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


def _why_none_eligible(problem: Problem, order: Order) -> str:
    """Which of `_eligible`'s filters actually emptied the fleet.

    NO_ELIGIBLE_VEHICLE covers skills and site access, and phrasing every case
    in terms of skills sent a dispatcher looking for a tail lift when the real
    obstacle was a weight limit. §6.5 requires the reason be produced by an
    explicit pass rather than inferred; the sentence deserves the same care as
    the code, because a wrong code is caught by anything that branches on it
    and a wrong sentence is read by a person who then acts on it.

    Checked in `_eligible`'s own order, so the first filter to reject the whole
    fleet is the one named. Falls back to the bare statement when the fleet was
    emptied by a deployment ban or an operator lock -- naming a constraint that
    nothing violated would be worse than naming none.
    """
    site = problem.location((order.delivery or order.pickup).location_id)
    everyone = problem.vehicles

    if order.required_skills and not any(order.required_skills <= v.skills
                                         for v in everyone):
        return (f"requires {sorted(order.required_skills)}; "
                f"no vehicle carries it")
    if site.access_classes and not any(v.access_class in site.access_classes
                                       for v in everyone):
        return (f"{site.id} admits {sorted(site.access_classes)}; "
                f"no vehicle is of that class")
    if site.max_vehicle_kg is not None and not any(
            _may_enter(v, site) for v in everyone):
        return (f"{site.id} takes at most {site.max_vehicle_kg} kg; "
                f"no vehicle is light enough")
    return "no vehicle qualifies"


def _may_enter(vehicle: Vehicle, site) -> bool:
    """FR-11. Empty `access_classes` means unrestricted, not "admits nothing"."""
    if site.access_classes and vehicle.access_class not in site.access_classes:
        return False
    return not (site.max_vehicle_kg is not None
                and vehicle.gross_weight_kg is not None
                and vehicle.gross_weight_kg > site.max_vehicle_kg)


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


def _forced_together_with_an_enemy(problem: Problem, order: Order,
                                   usable: list[Vehicle]) -> bool:
    """Would serving everything put this order beside a class it forbids?

    True only when the fleet is demonstrably too small: fewer vehicles than
    mutually-incompatible groups. That is decidable without a solve. Anything
    subtler -- capacity or windows forcing a particular pairing -- is
    FLEET_EXHAUSTED's territory and needs the search.
    """
    classes = {other.order_class for other in problem.orders
               if other.order_class}
    if not classes or len(usable) > 1:
        return False
    enemies = {other.order_class for other in problem.orders
               if other.id != order.id and other.order_class
               and (other.order_class in order.incompatible_with
                    or (order.order_class
                        and order.order_class in other.incompatible_with))}
    return bool(enemies)


def _stocked_out(problem: Problem, order: Order,
                 fleet: list[Vehicle]) -> str | None:
    """FR-31: whether no eligible depot holds enough for this order alone.

    Pre-flight's usual narrowness applies -- one order at a time, ignoring
    every other -- and it is what makes the answer trustworthy. A depot short
    of stock does not make an order unservable while another eligible depot
    holds enough, so this reports only when *every* depot the eligible fleet
    starts from is short. Whether the depots can supply the whole day's work
    between them is INV-13's question, and needs the plan.
    """
    homes = {vehicle.start_location_id for vehicle in fleet}
    shortfalls = []
    for home in sorted(homes):
        stock = problem.location(home).inventory
        if not stock:
            return None           # an unstocked depot is unconstrained
        lacking = [f"{dimension}={stock.get(dimension)}"
                   for dimension, amount in sorted(order.quantities.items())
                   if stock.get(dimension) is not None
                   and stock[dimension] < amount]
        if not lacking:
            return None
        shortfalls.append(f"{home} holds {', '.join(lacking)}")
    if not shortfalls:
        return None
    return (f"needs {order.quantities}; no eligible depot holds it "
            f"({'; '.join(shortfalls)})")


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
            report("NO_ELIGIBLE_VEHICLE", _why_none_eligible(problem, order))
            continue

        if not any(_fits(order, vehicle) for vehicle in fleet):
            biggest = max((max(v.capacities.values(), default=0) for v in fleet),
                          default=0)
            report("CAPACITY_EXCEEDED",
                   f"needs {order.quantities}; largest eligible capacity {biggest}")
            continue

        short = _stocked_out(problem, order, fleet)
        if short:
            report("DEPOT_STOCKOUT", short)
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
            continue

        # FR-10, and the code E-14 had to declare unimplemented: an order that
        # can only travel on vehicles the rest of the work also needs, where
        # every one of those pairings is forbidden. Pre-flight can see this
        # only in the narrow case where the fleet is too small to separate
        # them; the general form needs a solve, and saying so beats a
        # confident wrong answer.
        if _forced_together_with_an_enemy(problem, order, usable):
            report("INCOMPATIBLE_ONLY",
                   f"class {order.order_class!r} cannot share a vehicle with "
                   f"the rest of the work, and there are not enough vehicles "
                   f"to separate them")

    return findings
