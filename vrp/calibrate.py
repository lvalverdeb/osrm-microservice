"""Service-time calibration — §12.1, T-62.

§12.1: "Fit service duration from telematics: `service = f(order_archetype,
quantity, location_archetype, vehicle_type, time_of_day, driver_experience)`.
Start with grouped medians per archetype (robust, explainable) before any
regression model. Re-fit monthly; alert on drift."

The instruction to start with grouped medians is worth taking literally. A
regression fits everything and explains nothing: a dispatcher told "the model
says 412 seconds" cannot check it, and nobody can separate a genuine shift from
an artefact of the fit. A median over a named group is a number somebody can go
and count, which is what makes it safe to ship to every van in a monthly job.

**Median, not mean.** A driver who takes a phone call mid-stop produces a
forty-minute service on a four-minute job. The van really was stationary, so the
observation is not wrong and cannot be filtered on principle -- the statistic
simply has to be the one that survives it.

**A group needs enough observations to be a group.** One stop is not evidence
about an archetype. Thin groups are reported as thin rather than fitted, with
the count, because "not enough data" is not actionable and "three of the five
needed" tells an operator whether next month will fix it.

**Drift is a separate output from the fit**, and §12.1 asks for both. The fit
says what service time is now; the drift says what changed and by how much. A
pipeline that silently replaced last month's numbers would be the dangerous
version of this: every value would look freshly measured and nothing would ever
look wrong.

Placement: **Python**, per criterion 2. It reads executed routes and the domain
model, and it changes whenever either does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from vrp.adherence import ExecutedRoute
from vrp.model import Order, Problem, Vehicle

# Quantity bands, in the order they are tested. §12.1 says archetype, not
# fingerprint: grouping by exact quantity gives one observation per group and
# nothing to take a median of.
BANDS = ((10, "tiny"), (50, "small"), (200, "medium"), (1_000, "large"))

# Time-of-day buckets. A city-centre drop at 08:00 and the same drop at 17:00
# are not the same job, which is why §12.1 lists time of day at all.
PARTS = ((10 * 3600, "early"), (14 * 3600, "midday"), (18 * 3600, "late"))


@dataclass(frozen=True)
class Archetype:
    """§12.1's grouping key. Named parts, so a fitted number can be explained."""

    order_kind: str
    quantity_band: str
    location_kind: str
    vehicle_type: str
    time_of_day: str


@dataclass(frozen=True)
class Observation:
    """One stop, as telematics saw it."""

    order_id: str
    archetype: Archetype
    seconds: int


@dataclass(frozen=True)
class Calibration:
    """One month's fit."""

    by_archetype: dict[Archetype, int]
    thin: dict[Archetype, int]


@dataclass(frozen=True)
class Drift:
    """One archetype that moved. `was`/`now` are None for appear/disappear."""

    archetype: Archetype
    was: int | None
    now: int | None

    @property
    def change(self) -> int:
        return (self.now or 0) - (self.was or 0)


def _band(quantity: int) -> str:
    for ceiling, name in BANDS:
        if quantity <= ceiling:
            return name
    return "bulk"


def _part(at: int) -> str:
    for ceiling, name in PARTS:
        if at < ceiling:
            return name
    return "night"


def archetype_of(problem: Problem, order: Order, vehicle: Vehicle,
                 at: int) -> Archetype:
    """Which group this stop belongs to. §12.1's `f(...)` arguments.

    Driver experience is named by §12.1 and is not modelled here: nothing in the
    domain carries it, and inventing a proxy -- tenure inferred from route count,
    say -- would put a number in an explainable pipeline that nobody could
    check. It is an omission rather than a decision, and stating it is cheaper
    than pretending the archetype is complete.
    """
    stop = order.delivery or order.pickup
    site = problem.location(stop.location_id)
    return Archetype(
        order_kind=order.kind,
        quantity_band=_band(sum(order.quantities.values())),
        location_kind="restricted" if site.access_classes else "open",
        vehicle_type=f"cap{sum(vehicle.capacities.values())}",
        time_of_day=_part(at))


def observations(problem: Problem,
                 executed: Sequence[ExecutedRoute]) -> list[Observation]:
    """Service durations, as telematics recorded them.

    Raises:
        ValueError: on a departure before its arrival. That is a clock fault,
            and averaging it in would corrupt an archetype nobody would think
            to re-check.

    A stop whose departure was not recorded is skipped rather than treated as
    zero: a tracker that missed one end has not told us the service was
    instant, and filling it in would drag every median it lands in towards
    nothing.
    """
    seen = []
    for route in executed:
        vehicle = problem.vehicle(route.vehicle_id)
        for order_id in route.sequence:
            arrival = route.arrivals.get(order_id)
            departure = route.departures.get(order_id)
            if arrival is None or departure is None:
                continue
            if departure < arrival:
                raise ValueError(
                    f"{route.vehicle_id} departure {departure} precedes "
                    f"arrival {arrival} at {order_id}")
            order = problem.order(order_id)
            seen.append(Observation(
                order_id=order_id,
                archetype=archetype_of(problem, order, vehicle, arrival),
                seconds=departure - arrival))
    return seen


def fit(seen: Sequence[Observation], minimum: int) -> Calibration:
    """Grouped medians per archetype. §12.1's first model.

    Args:
        seen: observations from `observations`.
        minimum: how many observations a group needs before it is fitted.

    Returns:
        The fitted archetypes and, separately, the thin ones with their counts.
    """
    grouped: dict[Archetype, list[int]] = {}
    for observation in seen:
        grouped.setdefault(observation.archetype, []).append(observation.seconds)

    fitted, thin = {}, {}
    for archetype, values in grouped.items():
        if len(values) < minimum:
            thin[archetype] = len(values)
            continue
        fitted[archetype] = _median(values)
    return Calibration(by_archetype=fitted, thin=thin)


def _median(values: Sequence[int]) -> int:
    """The lower of the two middle values on an even count.

    Not their average: CON-4 forbids the float, and truncating an average would
    round differently on different data for no reason anybody could predict.
    The lower middle is deterministic and replayable, which is what an audit
    against a depot needs.
    """
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def drift(previous: Calibration, current: Calibration,
          threshold: int) -> tuple[Drift, ...]:
    """What moved since last month, beyond `threshold` seconds. §12.1.

    Appearances and disappearances are reported too. A group that did not exist
    last month is a change worth seeing -- a new depot, a new customer type --
    and work that stopped arriving is a change nobody would otherwise notice,
    because the number simply stops being re-fitted.
    """
    keys = set(previous.by_archetype) | set(current.by_archetype)
    alerts = []
    for archetype in sorted(keys, key=lambda a: tuple(vars(a).values())):
        was = previous.by_archetype.get(archetype)
        now = current.by_archetype.get(archetype)
        if was is not None and now is not None and abs(now - was) <= threshold:
            continue
        if was is None and now is None:
            continue
        alerts.append(Drift(archetype=archetype, was=was, now=now))
    return tuple(alerts)


def as_service_fixed(calibration: Calibration,
                     problem: Problem) -> Mapping[str, int]:
    """The fitted seconds each order's `service_fixed` would become.

    Offered rather than applied. §12.4's "Act" list puts extracting a deviation
    into an explicit model feature first *because* it is auditable, and a
    calibration that rewrote the instance on its own would remove the review
    step that makes it so.
    """
    proposed = {}
    for order in problem.orders:
        for vehicle in problem.vehicles:
            stop = order.delivery or order.pickup
            windows = [w for w in stop.time_windows if w.hardness == "HARD"]
            at = windows[0].start if windows else 0
            key = archetype_of(problem, order, vehicle, at)
            if key in calibration.by_archetype:
                proposed[order.id] = calibration.by_archetype[key]
                break
    return proposed
