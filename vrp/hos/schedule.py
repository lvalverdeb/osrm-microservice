"""Break insertion as a scheduling subproblem inside route evaluation — §6.4.

§6.4 is explicit that this must not be a post-processing pass, and names the
symptom of getting it wrong: routes that were feasible before breaks and
infeasible after, showing up as a plan that "loses" its last two stops per route
on publication. The difference is structural, not a matter of care. A post-hoc
pass computes arrival times, then inserts breaks between them, so the arrivals
it reports are the ones it computed *before* the breaks existed. Here the clock
and the driver state advance together, so a break inserted at hour four pushes
every later arrival by its duration, and a stop that no longer fits is visible
as a violation rather than as an absence.

Breaks are taken **as late as the rules allow**. For a single break duration
this is optimal -- driving longer before the first break can never require more
breaks -- and it is what a driver actually does. EU's split break (15 min then
30 min) is a genuine dynamic-programming problem and is not attempted; the
module refuses rather than approximating, because a break plan that is nearly
legal has no value.

A leg longer than the driving allowance is split mid-arc, which is why
`Placement.ANYWHERE_ON_ARC` is the default. Breaks that must happen at a
qualifying facility need facility candidates in the matrix (§7.2, `T-25`'s
placement half) and are not yet placeable.

Placement: Python. Route evaluation with regulatory semantics -- it belongs with
the constraint model, not the transport layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from vrp.hos.rules import Activity, Break, DriverState, HoursOfServiceRules, Placement
from vrp.model import Problem, Step, service_time


@dataclass(frozen=True)
class ScheduledRoute:
    """A timeline, and whether it is legal.

    `legal` is reported rather than raised, and every ordered stop stays in
    `steps` even when the duty is over hours. That is deliberate: the caller
    needs to see *which* plan is illegal and where it went wrong, and a
    scheduler that returned the legal prefix would be reproducing exactly the
    silent-shortening failure §6.4 warns about.
    """

    steps: tuple[Step, ...]
    legal: bool
    violation: str | None
    state: DriverState


def _drive(problem: Problem, clock: int, state: DriverState,
           rules: HoursOfServiceRules | None, travel: int, destination: str,
           steps: list[Step], violation: str | None,
           on_board: dict[str, int]) -> tuple[int, DriverState, str | None]:
    """Advance across one leg, inserting breaks as they fall due.

    Returns the clock, the driver state, and the first violation seen. Once a
    violation exists the remaining legs are driven without further enforcement,
    so the timeline still shows every stop the caller asked for.
    """
    if rules is None:
        return clock + travel, state, violation

    remaining = travel
    while remaining > 0:
        if violation is not None:
            # Already over hours; keep building so the plan stays inspectable.
            return clock + remaining, state, violation

        allowance = rules.drive_until_break(state)
        if allowance == 0:
            required: Break | None = rules.required_break(state)
            if required is None:
                # Out of daily hours, not merely due a break. A rest is the
                # only cure and a rest ends the duty, so this plan is illegal.
                exhausted = (f"{rules.name}: duty exhausted with {remaining}s "
                             f"of driving left to reach {destination}")
                return clock + remaining, state, exhausted
            steps.append(Step(type="BREAK", location_id=destination,
                              arrival=clock, start_service=clock,
                              departure=clock + required.duration,
                              rule_ref=required.rule_ref,
                              placement=required.placement.value,
                              load_after=dict(on_board)))
            clock += required.duration
            state = rules.advance(state, Activity.BREAK, required.duration)
            continue

        chunk = min(allowance, remaining)
        clock += chunk
        state = rules.advance(state, Activity.DRIVE, chunk)
        remaining -= chunk
    return clock, state, violation


def schedule_route(problem: Problem, vehicle_id: str, order_ids: list[str],
                   rules: HoursOfServiceRules | None,
                   initial_state: DriverState | None = None,
                   start_time: int | None = None) -> ScheduledRoute:
    """Expand an order sequence into a timeline with breaks placed legally.

    `rules=None` schedules without hours-of-service at all, which is what the
    rest of the planner did before `T-25`. It is kept so the two can be compared
    directly -- that comparison is the gate test for this module.

    `initial_state` defaults to the vehicle's own, so a problem that declares an
    exhausted driver is planned as one. Pass it explicitly only to ask a
    what-if question about a different starting state.
    """
    vehicle = problem.vehicle(vehicle_id)
    matrix = problem.matrix
    index_of = {location.id: location.matrix_index for location in problem.locations}

    # The vehicle's own carry-over is the default. Taking it only as an
    # argument meant a Problem describing an exhausted driver scheduled as
    # though they were rested -- silently, since nothing was missing, only
    # understated. An explicit argument still wins, for what-if analysis.
    carry_over = initial_state if initial_state is not None else vehicle.initial_state
    state = rules.init_state(carry_over) if rules else (carry_over or DriverState())
    clock = start_time if start_time is not None else vehicle.shift.start
    violation: str | None = None

    # Delivery-only routes leave the depot fully laden and shed load along the
    # way. The same rule as the canonical evaluator's, restated rather than
    # imported: this module must schedule a route without depending on the
    # evaluator, and the rule is four lines. Without it every step reported no
    # load at all, and INV-5 -- which checks the loads a step reports -- passed
    # any HOS-scheduled plan without examining its capacity.
    orders = [problem.order(order_id) for order_id in order_ids]
    dimensions = {d for order in orders for d in order.quantities}
    on_board = {
        dimension: sum(order.quantities.get(dimension, 0)
                       for order in orders if order.delivery is not None)
        for dimension in dimensions
    }

    steps: list[Step] = [Step(type="START", location_id=vehicle.start_location_id,
                              arrival=clock, start_service=clock, departure=clock,
                              load_after=dict(on_board))]

    position = index_of[vehicle.start_location_id]
    for order_id in order_ids:
        order = problem.order(order_id)
        stop = order.delivery or order.pickup
        destination = index_of[stop.location_id]

        clock, state, violation = _drive(
            problem, clock, state, rules, matrix.duration(position, destination),
            stop.location_id, steps, violation, on_board)

        arrival = clock
        window = stop.time_windows[0] if stop.time_windows else None
        start_service = max(arrival, window.start) if window else arrival
        if start_service > arrival and rules is not None:
            # Waiting for a window burns the duty clock without driving.
            state = rules.advance(state, Activity.WAIT, start_service - arrival)
        service = service_time(order, vehicle,
                               problem.location(stop.location_id))
        departure = start_service + service
        if rules is not None:
            state = rules.advance(state, Activity.WORK, service)

        for dimension, quantity in order.quantities.items():
            on_board[dimension] += -quantity if order.delivery else quantity
        steps.append(Step(type="DELIVERY" if order.delivery else "PICKUP",
                          location_id=stop.location_id, order_id=order_id,
                          arrival=arrival, start_service=start_service,
                          departure=departure, load_after=dict(on_board)))
        clock = departure
        position = destination

    end = index_of[vehicle.ends_at]
    clock, state, violation = _drive(problem, clock, state, rules,
                                     matrix.duration(position, end),
                                     vehicle.ends_at, steps, violation, on_board)
    steps.append(Step(type="END", location_id=vehicle.ends_at, arrival=clock,
                      start_service=clock, departure=clock,
                      load_after=dict(on_board)))

    if violation is None and clock > vehicle.shift.end:
        violation = (f"route ends at {clock} which is past the shift end "
                     f"{vehicle.shift.end}")

    return ScheduledRoute(steps=tuple(steps), legal=violation is None,
                          violation=violation, state=state)


__all__ = ["Placement", "ScheduledRoute", "schedule_route"]
