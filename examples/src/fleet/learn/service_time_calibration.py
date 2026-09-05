"""Five minutes on paper, seven in the street.

Demonstrates service-time calibration, landed for E-62/T-62 (§12.1):

    vrp.calibrate   the archetypes, the grouped medians, the drift alerts
    vrp.adherence   T-61's telematics, which this is the first thing to learn
                    from
    vrp.model       `service_time`, the composition being calibrated

§12.1: "Fit service duration from telematics: `service = f(order_archetype,
quantity, location_archetype, vehicle_type, time_of_day, driver_experience)`.
Start with grouped medians per archetype (robust, explainable) before any
regression model. Re-fit monthly; alert on drift."

That instruction is worth taking literally. A regression fits everything and
explains nothing: a dispatcher told "the model says 412 seconds" cannot check
it. A median over a named group is a number somebody can go and count.

Four things, in order:

1. **The archetype**, which is what makes a fitted number explainable.

2. **Why the median**, on the observation that breaks a mean.

3. **A month re-fitted**, and the groups too thin to fit.

4. **Drift**, which is a separate output from the fit and needs to be.

Requires a running gateway: distances are measured on the road network and
there is no straight-line fallback. `examples/.env` points at the FreeBSD jail.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/learn/service_time_calibration.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

# Importing config puts OSRM_API_URL into the environment, so the
# gateway this example now requires is the one `examples/.env` names.
import config  # noqa: F401
import dataset

from vrp.adherence import ExecutedRoute
from vrp.calibrate import archetype_of, as_service_fixed, drift, fit, observations
from vrp.model import (
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    Vehicle,
)

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 600


def instance(stops: int = 4) -> Problem:
    """Four real deliveries around the Guadalupe depot.

    Their quantities and planned service times are the corpus's own, which is
    what makes the archetype table below worth reading: the bands are drawn
    over numbers somebody recorded rather than numbers chosen to make the
    grouping come out tidy.

    The access restriction on the third stop is added here and is not in the
    corpus -- it records a delivery's address, not whether there is a barrier
    across it. Stated rather than smuggled in, because an archetype the data
    cannot support is exactly what §12.1 warns against.
    """
    locations, matrix, deliveries, _depot = dataset.road_sites(
        stops, strategy="spread", name="calib")
    # A bulk drop among small ones, so the quantity band separates something.
    units = [d["units"] for d in deliveries]
    units[-1] = max(units) * 40

    locations = tuple(
        replace(loc, access_classes=frozenset({"BIKE"}))
        if loc.id == "C3" else loc for loc in locations)

    return Problem(
        id="calib", locations=locations,
        orders=tuple(
            Order(id=f"O{i + 1}", kind="JOB", quantities={"kg": units[i]},
                  delivery=StopSpec(location_id=f"C{i + 1}",
                                    time_windows=(DAY,),
                                    service_fixed=d["service_minutes"] * 60))
            for i, d in enumerate(deliveries)),
        vehicles=(Vehicle(id="V1", capacities={"kg": max(units) * 50},
                          shift=DAY, start_location_id="D",
                          end_location_id="D"),),
        matrix=matrix)


def route(observed: dict[str, tuple[int, int]]) -> ExecutedRoute:
    return ExecutedRoute(
        vehicle_id="V1", driver_id="ana", depot_id="D", territory="north",
        sequence=tuple(observed),
        arrivals={o: pair[0] for o, pair in observed.items()},
        departures={o: pair[1] for o, pair in observed.items()})


def show_archetypes(problem: Problem) -> None:
    print("\n1. The archetype (§12.1)")
    vehicle = problem.vehicles[0]
    print(f"   {'order':<7}{'kind':<6}{'quantity':<10}{'location':<12}"
          f"{'when':<8}")
    for order in problem.orders:
        key = archetype_of(problem, order, vehicle, at=9 * HOUR)
        print(f"   {order.id:<7}{key.order_kind:<6}{key.quantity_band:<10}"
              f"{key.location_kind:<12}{key.time_of_day:<8}")

    print("   O1 and O2 share a group: same kind, same quantity band, same")
    print("   sort of address, same part of the day. O3 is behind an access")
    print("   restriction and O4 is a bulk drop, so neither belongs with them.")
    print("   Named parts, which is the whole point: a fitted number can be")
    print("   explained, and a dispatcher told \"420 seconds for a small drop")
    print("   at an open address before ten\" can go and count them.")
    print("   §12.1 also names driver experience. Nothing in the domain model")
    print("   carries it, so it is absent rather than proxied -- inventing one")
    print("   would put an unauditable number in an auditable pipeline.")


def show_the_median(problem: Problem) -> None:
    print("\n2. Why the median (§12.1: \"robust, explainable\")")
    day = [route({"O1": (9 * HOUR, 9 * HOUR + s)})
           for s in (400, 410, 420, 430, 2_700)]
    seen = observations(problem, day)
    values = sorted(o.seconds for o in seen)

    print(f"   observed: {values}")
    print(f"   mean:   {sum(values) // len(values)} s")
    print(f"   median: {fit(seen, minimum=5).by_archetype.popitem()[1]} s")
    print("   The 2,700 is a driver taking a phone call mid-stop. The van")
    print("   really was stationary, so the observation is not wrong and")
    print("   cannot be filtered on principle -- the statistic simply has to be")
    print("   the one that survives it. A mean would ship 872 seconds to every")
    print("   van in the fleet.")


def show_the_month(problem: Problem) -> None:
    print("\n3. A month re-fitted")
    month = []
    for _ in range(22):
        month.append(route({"O1": (9 * HOUR, 9 * HOUR + 420),
                            "O2": (9 * HOUR + 1_000, 9 * HOUR + 1_400)}))
    month.append(route({"O4": (9 * HOUR, 9 * HOUR + 1_500)}))

    calibration = fit(observations(problem, month), minimum=10)

    print(f"   {'archetype':<46}{'fitted':>9}")
    for key, seconds in calibration.by_archetype.items():
        label = (f"{key.order_kind}/{key.quantity_band}/{key.location_kind}"
                 f"/{key.time_of_day}")
        print(f"   {label:<46}{seconds:>7} s")
    for key, count in calibration.thin.items():
        label = (f"{key.order_kind}/{key.quantity_band}/{key.location_kind}"
                 f"/{key.time_of_day}")
        print(f"   {label:<46}{'thin':>9}  ({count} seen, 10 needed)")

    print("   The bulk drop is not fitted: one observation is not evidence")
    print("   about an archetype, and fitting it would turn a single Tuesday")
    print("   into policy shipping to every van. The count is reported because")
    print("   \"not enough data\" is not actionable and \"1 of 10\" tells an")
    print("   operator whether next month will fix it.")

    proposed = as_service_fixed(calibration, problem)
    print(f"\n   proposed service_fixed: {proposed}")
    print(f"   current in the model:   "
          f"{ {o.id: o.delivery.service_fixed for o in problem.orders} }")
    print("   Offered, not applied. §12.4's own priority order prefers an")
    print("   explicit model change because it is \"explainable and")
    print("   auditable\", and a pipeline that rewrote the instance on its own")
    print("   would remove the review step that makes it so.")


def show_drift(problem: Problem) -> None:
    print("\n4. Drift (§12.1: \"alert on drift\")")
    january = fit(observations(problem, [
        route({"O1": (9 * HOUR, 9 * HOUR + 420)}) for _ in range(12)]),
        minimum=10)
    february = fit(observations(problem, [
        route({"O1": (9 * HOUR, 9 * HOUR + 445)}) for _ in range(12)]),
        minimum=10)
    march = fit(observations(problem, [
        route({"O1": (9 * HOUR, 9 * HOUR + 700)}) for _ in range(12)]),
        minimum=10)

    for label, before, after in (("Jan -> Feb", january, february),
                                 ("Feb -> Mar", february, march)):
        alerts = drift(before, after, threshold=60)
        if not alerts:
            print(f"   {label}: no alert (moved less than the threshold)")
        for alert in alerts:
            print(f"   {label}: {alert.was} s -> {alert.now} s "
                  f"({alert.change:+} s)")

    print("   Everything moves a little every month. An alert that fired on")
    print("   noise is one an operator learns to ignore, which is worse than")
    print("   no alert at all.")
    print("   Drift is a separate output from the fit, and §12.1 asks for both")
    print("   deliberately: the fit says what service time is now, the drift")
    print("   says what changed. A pipeline that silently replaced last")
    print("   month's numbers would be the dangerous version -- every value")
    print("   would look freshly measured and nothing would ever look wrong.")


def main() -> int:
    problem = instance()
    show_archetypes(problem)
    show_the_median(problem)
    show_the_month(problem)
    show_drift(problem)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
