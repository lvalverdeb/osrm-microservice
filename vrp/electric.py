"""Planning where an electric van charges — FR-20, T-41.

A constraint that spans the whole route rather than one stop, so it lives where
the other such constraints live: an orchestrator around the search, the same
shape as `vrp.depots` for inventory and `vrp.synchronise` for paired visits, and
for the same reason `DEC-1` gives. PyVRP compiles capacity dimensions, not a
state that a *detour* replenishes on a non-linear curve, so an electric instance
is refused by the adapter by name and arrives here instead.

The design rule this follows is CON-11's: the model carries the constraint, the
verifier checks it independently (INV-16), the search is told what can be said
soundly, and what cannot be said is refused by name. `NoChargerReachable` is
that refusal. A plan that silently dropped the stop it could not reach, or that
charged at a customer's doorstep, would both be worse than being told the fleet
is wrong for the round -- a dispatcher can hire a diesel van in ten minutes and
cannot un-strand a driver.

**Where to charge is chosen greedily and it is not claimed to be optimal.**
The van charges as late as it can: at the last charger it can still reach
before the battery would go flat. Charging late is better than charging early
on a tapering curve, because a battery that arrives emptier takes current
faster -- the same reason a driver runs down to twenty percent before stopping.
Choosing charge points and charge amounts *jointly with the route* is the
electric VRP proper and is a search problem, not a repair one; this is the
repair, and it is honest about being one.

**How much to charge is answered by the rest of the round.** Enough to finish,
plus a reserve, capped at full -- rather than always filling. Filling spends the
taper the round did not need, and on a real curve that is twenty minutes a van
could have been delivering.
"""

from __future__ import annotations

from vrp.battery import FULL_PPT, ChargeStop, consumed_ppt
from vrp.evaluator import build_timeline
from vrp.model import Problem, Vehicle

# What a plan must still hold when it gets home. Ten percent: a plan that
# lands on empty is one traffic jam from a recovery, and every operator's own
# rule is some number like this rather than zero.
RESERVE_PPT = 100


class NoChargerReachable(Exception):
    """No charger this vehicle can still reach would rescue the round."""


def plan_charging(problem: Problem, vehicle_id: str,
                  order_ids: list[str]) -> dict[int, ChargeStop]:
    """Where an electric van has to stop, and how full to leave.

    Args:
        problem: the instance.
        vehicle_id: whose round it is.
        order_ids: the stops, in the order they will be served.

    Returns:
        `{index: ChargeStop}` for `build_timeline`. Empty when the round is
        inside the battery already, which is the common case and costs nothing.

    Raises:
        NoChargerReachable: when no reachable charger keeps the van above the
            reserve for the rest of the round.

    Repairs one shortfall at a time, rebuilding the timeline after each, because
    a charge changes every state of charge after it and the second shortfall is
    frequently not where it looked before the first was fixed.
    """
    vehicle = problem.vehicle(vehicle_id)
    if not vehicle.is_electric or not order_ids:
        return {}

    charges: dict[int, ChargeStop] = {}
    # One repair per stop is the ceiling: each charge fixes the earliest
    # shortfall, so a round cannot need more stops than it has.
    for _ in range(len(order_ids) + 1):
        timeline = build_timeline(problem, vehicle_id, order_ids,
                                  charges=charges or None)
        shortfall = _first_shortfall(timeline)
        if shortfall is None:
            return charges
        index = _order_index(timeline, shortfall, order_ids)
        charges[index] = _charge_before(problem, vehicle, order_ids, index,
                                        charges)
    raise NoChargerReachable(
        f"{vehicle_id} still runs flat after charging at every stop on the "
        f"round; the fleet is wrong for this work rather than the plan")


def _first_shortfall(timeline) -> int | None:
    """Index of the first step the van reaches below the reserve."""
    for position, step in enumerate(timeline):
        if step.soc_after_ppt is not None and step.soc_after_ppt < RESERVE_PPT:
            return position
    return None


def _order_index(timeline, shortfall: int, order_ids: list[str]) -> int:
    """Which order the charge has to come before.

    The step that runs flat may be the run home, in which case the charge goes
    before the last stop -- there is no stop after it to hang one on.
    """
    for step in timeline[shortfall::-1]:
        if step.order_id is not None:
            return order_ids.index(step.order_id)
    return 0


def _charge_before(problem: Problem, vehicle: Vehicle, order_ids: list[str],
                   index: int, charges: dict[int, ChargeStop]) -> ChargeStop:
    """The charger to use before `order_ids[index]`, and how full to leave.

    Raises:
        NoChargerReachable: if the van cannot reach any charger from where it
            is, or if no charger leaves it able to finish.
    """
    if not vehicle.charger_locations:
        raise NoChargerReachable(
            f"{vehicle.id} runs flat on this round and has no charger "
            f"locations, so there is nowhere for it to stop")

    timeline = build_timeline(problem, vehicle.id, order_ids,
                              charges=charges or None)
    here = _position_before(problem, timeline, order_ids, index)
    available = timeline[max(index, 0)].soc_after_ppt or 0

    for charger_id in sorted(vehicle.charger_locations):
        charger = problem.location(charger_id)
        leg = consumed_ppt(vehicle.battery_wh, vehicle.consumption_wh_per_km,
                           problem.matrix.distance(here, charger.matrix_index))
        if leg > available:
            continue
        return ChargeStop(charger_id, _target(problem, vehicle, order_ids,
                                              index, charger.matrix_index))
    raise NoChargerReachable(
        f"{vehicle.id} cannot reach a charger from {problem.locations[here].id} "
        f"with {available} parts per thousand of charge; chargers available: "
        f"{sorted(vehicle.charger_locations)}")


def _position_before(problem: Problem, timeline, order_ids: list[str],
                     index: int) -> int:
    """The matrix index the van is at just before serving `order_ids[index]`.

    Before the first stop that is the depot; otherwise it is wherever the
    previous order was, which the timeline knows and the order list does not.
    """
    if index <= 0:
        return problem.location(timeline[0].location_id).matrix_index
    previous = order_ids[index - 1]
    for step in timeline:
        if step.order_id == previous:
            return problem.location(step.location_id).matrix_index
    return problem.location(timeline[0].location_id).matrix_index


def _target(problem: Problem, vehicle: Vehicle, order_ids: list[str],
            index: int, charger: int) -> int:
    """How full to leave: enough for the rest of the round, plus the reserve.

    Filling to a hundred percent spends the slow end of the curve on charge the
    round does not need. Capped at full, and floored at the reserve so a stop
    that turns out to be unnecessary still leaves with something.
    """
    needed = RESERVE_PPT
    position = charger
    for order_id in order_ids[index:]:
        stop = problem.order(order_id).delivery or problem.order(order_id).pickup
        destination = problem.location(stop.location_id).matrix_index
        needed += consumed_ppt(vehicle.battery_wh, vehicle.consumption_wh_per_km,
                               problem.matrix.distance(position, destination))
        position = destination
    home = problem.location(vehicle.ends_at).matrix_index
    needed += consumed_ppt(vehicle.battery_wh, vehicle.consumption_wh_per_km,
                           problem.matrix.distance(position, home))
    return min(FULL_PPT, max(RESERVE_PPT, needed))
