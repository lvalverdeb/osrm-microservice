"""Insertion and removal quotes — NFR-02, §9.4, T-85.

`NFR-02`: "Interactive latency. Single-order insertion / removal quote: p95 ≤ 2
s." §9.4 gives it an endpoint: `POST /v1/solutions/{id}/quote -> insertion price
for candidate order(s), T0 latency`.

**A quote is not a re-plan, and that is the whole requirement.** A dispatcher on
the phone to a customer wants a price for adding this stop to today's round. A
function that returned a better plan with everything moved would be answering a
question nobody asked, however good the plan and however fast it arrived --
every other stop already has a promised time, and half of them are on vans that
have left. So the existing routes keep their order and one position is opened
for the candidate.

**The price is what serving the stop costs, not what not serving it costs.**
The canonical objective carries an `unassigned_penalty`, and it dominates: on a
twelve-stop instance one unserved order is worth a million against forty-five
thousand of distance, so a naive delta says inserting an order *saves* the
better part of a million. That number is real and it is not a price -- the
penalty is how the planner is told to prefer serving things, and quoting it to
a dispatcher would say every stop pays for itself. The quote is therefore the
change in the objective **excluding** that penalty: the driving, the service,
the waiting and the windows that adding this stop actually costs.

**The scan chooses and the canonical evaluator prices.** Trying every position
on every vehicle is cheap and local; scoring each trial through
`vrp.objective.evaluate` would be neither. So the scan picks the cheapest legal
placement by route length, and the price quoted is then the canonical objective
of the resulting plan minus the canonical objective of the current one -- two
evaluations, whatever the fleet size. Quoting the scan's own arithmetic would
be an engine scoring its own work, which is what `CON-1` exists to prevent.

**No room is refused rather than priced.** A quote of some enormous number
reads to a dispatcher as "expensive", and they will take it to a customer. A
refusal that names the order says the fleet cannot do it today, and they hire a
van instead.

Placement: **Python**, per criterion 2. It reads the domain model and the
canonical objective, and changes whenever either does.
"""

from __future__ import annotations

from dataclasses import dataclass

from vrp.evaluator import ObjectiveWeights, evaluate, route_is_legal
from vrp.model import Problem


class NoRoomForOrder(Exception):
    """No vehicle can legally take this order, so there is no price."""


@dataclass(frozen=True)
class Quote:
    """What one change to a plan would cost.

    Attributes:
        order_id: the order being priced.
        price: the change in the canonical objective. Positive to add a stop,
            negative to drop one.
        vehicle_id: whose route changes.
        route: that route as it would be, so a caller can show the sequence
            rather than describe it.
    """

    order_id: str
    price: int
    vehicle_id: str
    route: tuple[str, ...]


def quote_insertion(problem: Problem, assignment: dict[str, list[str]],
                    order_id: str,
                    weights: ObjectiveWeights | None = None) -> Quote:
    """What adding one order to an existing plan would cost.

    Args:
        problem: the instance.
        assignment: the current plan, vehicle to ordered stops. Not modified.
        order_id: the candidate.
        weights: the canonical objective's weights.

    Returns:
        The cheapest legal placement and its price.

    Raises:
        NoRoomForOrder: when no vehicle can legally take it. Refused rather
            than priced at some large number, which a dispatcher reads as
            expensive rather than as impossible.
    """
    weights = weights or ObjectiveWeights()
    best: tuple[int, str, list[str]] | None = None
    for vehicle_id in sorted(assignment):
        current = assignment[vehicle_id]
        # The *detour*, not the resulting route's length. Comparing absolute
        # lengths across vehicles picks whichever van has the fewest stops
        # already -- it would always beat a full route however short the
        # detour onto it, which is the opposite of a cheapest insertion.
        base = _length(problem, vehicle_id, current)
        for position in range(len(current) + 1):
            route = current[:position] + [order_id] + current[position:]
            if not route_is_legal(problem, vehicle_id, route):
                continue
            # The detour picks; the canonical objective prices. Scoring every
            # trial canonically would make a quote O(fleet) evaluations.
            detour = _length(problem, vehicle_id, route) - base
            if best is None or detour < best[0]:
                best = (detour, vehicle_id, route)

    if best is None:
        raise NoRoomForOrder(
            f"no vehicle can legally carry {order_id}: every position on every "
            f"route in this plan is refused by capacity, a time window, skills "
            f"or site access. The fleet cannot serve it today, which is a "
            "different answer from an expensive one")

    _, vehicle_id, route = best
    trial = {vehicle: list(orders) for vehicle, orders in assignment.items()}
    trial[vehicle_id] = route
    price = (_cost_of_serving(problem, trial, weights)
             - _cost_of_serving(problem, assignment, weights))
    return Quote(order_id=order_id, price=price, vehicle_id=vehicle_id,
                 route=tuple(route))


def quote_removal(problem: Problem, assignment: dict[str, list[str]],
                  order_id: str,
                  weights: ObjectiveWeights | None = None) -> Quote:
    """What dropping one order from an existing plan would save.

    Args:
        problem: the instance.
        assignment: the current plan. Not modified.
        order_id: the order to remove.
        weights: the canonical objective's weights.

    Returns:
        The quote, with a negative price -- a saving.

    Raises:
        KeyError: if no route carries the order. A removal quote for something
            nobody has is a question about a different plan, and answering it
            with a saving of zero would look like a stop that costs nothing.
    """
    weights = weights or ObjectiveWeights()
    carrier = next((vehicle_id for vehicle_id, orders in assignment.items()
                    if order_id in orders), None)
    if carrier is None:
        raise KeyError(
            f"{order_id} is not on any route in this plan, so there is nothing "
            "to remove; check the plan the quote is about")

    route = [order for order in assignment[carrier] if order != order_id]
    trial = {vehicle: list(orders) for vehicle, orders in assignment.items()}
    trial[carrier] = route
    price = (_cost_of_serving(problem, trial, weights)
             - _cost_of_serving(problem, assignment, weights))
    return Quote(order_id=order_id, price=price, vehicle_id=carrier,
                 route=tuple(route))


def _cost_of_serving(problem: Problem, assignment: dict[str, list[str]],
                     weights: ObjectiveWeights) -> int:
    """The canonical objective without the penalty for what is not served.

    `unassigned_penalty` exists to make a solver prefer serving an order to
    dropping it, and on any realistic weighting it dwarfs the running cost. A
    quote that included it would report a saving for every insertion and a
    charge for every removal -- both backwards, and both confidently wrong to
    the one person who reads a quote and repeats it to a customer.
    """
    result = evaluate(problem, assignment, weights)
    return result.total - result.breakdown.get("unassigned_penalty", 0)


def _length(problem: Problem, vehicle_id: str, order_ids: list[str]) -> int:
    """Travel distance for a route, depot to depot.

    Distance rather than the canonical objective on purpose: this runs once per
    candidate position, and the canonical score is taken twice at the end.
    """
    vehicle = problem.vehicle(vehicle_id)
    index = {location.id: location.matrix_index for location in problem.locations}
    here = index[vehicle.start_location_id]
    total = 0
    for order_id in order_ids:
        stop = problem.order(order_id).delivery or problem.order(order_id).pickup
        there = index[stop.location_id]
        total += problem.matrix.distance(here, there)
        here = there
    return total + problem.matrix.distance(here, index[vehicle.ends_at])
