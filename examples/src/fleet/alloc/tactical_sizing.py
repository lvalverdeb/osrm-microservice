"""How many vans to own, decided against thirty days instead of one.

Demonstrates the scenario engine and fleet-sizing sweep landed for E-46/T-46
(FR-34, US-4, §7.8):

    vrp.scenarios   the scenario set, the sweep, the Pareto front
    vrp.solve       the operational solver, injected at reduced budget
    vrp.allocate    T-44's per-vehicle costing, which this prices mixes with

FR-34 asks for a fleet composition "minimising expected total cost
(acquisition/lease + routing + expected failure/recourse cost)" over "a scenario
set of historical or generated demand days". US-4 adds two acceptance criteria
worth reading as written: AC-4.1 wants the three costs reported separately over
at least thirty days and ten mixes, unattended; AC-4.2 wants a service-level
column, "not cost alone".

Both exist because a single total hides the thing an analyst is choosing
between. A mix can be cheapest precisely by abandoning work.

Four things, in order:

1. **The sweep.** Ten mixes over thirty days, and the three costs apart.

2. **The Pareto front.** What survives, and what was dominated -- a mix that is
   beaten on cost *and* on service is not a trade-off, it is a mistake, and
   showing it invites someone to pick it.

3. **§7.8's claim, tested rather than quoted.** "A deterministic average-day
   sizing systematically under-fleets." The average day says the fleet serves
   everything. The distribution says it does not.

4. **And the condition the claim leaves implicit.** Under-fleeting is only an
   error when a missed delivery costs more than a drive. Priced cheaply, a
   small fleet is the right answer and both methods find it.

Requires a running gateway: distances are measured on the road network and
there is no straight-line fallback. `examples/.env` points at the FreeBSD jail. About 20 s.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/alloc/tactical_sizing.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

# Importing config puts OSRM_API_URL into the environment, so the
# gateway this example now requires is the one `examples/.env` names.
import config  # noqa: F401
import dataset

from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    Vehicle,
)
from vrp.scenarios import (
    FULL,
    Mix,
    average_day,
    generate_scenarios,
    pareto,
    recommend,
    sweep,
)
from vrp.scenarios import recovery_cost as round_trip

GATEWAY = os.environ.get("OSRM_API_URL", "http://localhost:8000")
DAY = TimeWindow(start=0, end=10 * 3600)
POOL, TYPICAL, DAYS = 18, 12, 30
CAPACITY, DAY_RATE = 30, 12_000


def base_problem(stops: int = POOL, path: Path | None = None,
                 gateway: str = "") -> Problem:
    """The order pool the demand days are drawn from, on real road travel.

    US-4 asks for a sweep over "a set of historical or generated demand days",
    and the sizing answer is only as good as the geography underneath it: a
    pool laid out along one line of longitude makes every van's day the same
    shape, so the marginal van always costs the same and the Pareto front is a
    straight line by construction. Real deliveries around a real depot put the
    curvature back.

    Demand stays a constant weight per drop on purpose. The sweep's variables
    are which drops fall on a day and how many vans are owned; letting the
    weights vary too would add a dimension the report does not show.

    Args:
        stops: Size of the order pool.
        path: Where the delivery corpus lives.
        gateway: Base URL of the OSRM API gateway, for the road matrix.

    Returns:
        A `Problem` over real coordinates and real road travel.
    """
    deliveries, depot = dataset.load(path or dataset.DEFAULT_PATH).nearest(stops)
    points = [(depot["latitude"], depot["longitude"])]
    points += [(d["latitude"], d["longitude"]) for d in deliveries]
    matrix = dataset.road_matrix(points, gateway or GATEWAY,
                                                 "tactical")

    locations = (Location(id="D", lat=depot["latitude"], lon=depot["longitude"],
                          matrix_index=0),) + tuple(
        Location(id=d["product_id"], lat=d["latitude"], lon=d["longitude"],
                 matrix_index=i + 1)
        for i, d in enumerate(deliveries))
    orders = tuple(
        Order(id=f"O{i + 1}", kind="JOB", quantities={"kg": 10},
              delivery=StopSpec(location_id=d["product_id"],
                                time_windows=(DAY,),
                                service_fixed=d["service_minutes"] * 60))
        for i, d in enumerate(deliveries))
    return Problem(id="sizing", locations=locations, orders=orders,
                   vehicles=(_van("V1"),), matrix=matrix)


def _van(vehicle_id: str) -> Vehicle:
    return Vehicle(id=vehicle_id, capacities={"kg": CAPACITY}, shift=DAY,
                   start_location_id="D", end_location_id="D",
                   fixed_cost=DAY_RATE, cost_per_metre=1)


def candidates(most: int = 10) -> list[Mix]:
    return [Mix(name=f"{n} van{'s' if n > 1 else ''}",
                vehicles=tuple(_van(f"V{k}") for k in range(1, n + 1)))
            for n in range(1, most + 1)]


def first_fit(problem: Problem) -> dict[str, list[str]]:
    """The operational solver, at the reduced budget §7.8 calls for.

    Injected rather than imported: a planning module has no business owning an
    engine, and 30 days x 10 mixes is 300 routings that have to run unattended.
    A real sweep would pass PyVRP here with a small iteration count.
    """
    assignment: dict[str, list[str]] = {v.id: [] for v in problem.vehicles}
    loads = dict.fromkeys(assignment, 0)
    for order in problem.orders:
        need = order.quantities["kg"]
        for vehicle in problem.vehicles:
            if need <= vehicle.capacities["kg"] - loads[vehicle.id]:
                assignment[vehicle.id].append(order.id)
                loads[vehicle.id] += need
                break
    return assignment


def show_the_sweep(problem, days, mixes) -> list:
    print(f"\n1. {len(mixes)} mixes over {len(days)} days (AC-4.1)")
    sizes = [len(day.orders) for day in days]
    print(f"   demand: {min(sizes)} to {max(sizes)} drops a day, "
          f"mean {sum(sizes) / len(sizes):.1f}")
    print(f"   {'mix':<9}{'lease':>12}{'routing':>12}{'failure':>12}"
          f"{'total':>13}{'service':>10}")

    results = sweep(problem, mixes, days, first_fit)
    for r in results:
        print(f"   {r.mix:<9}{r.fixed_cost:>12,}{r.routing_cost:>12,}"
              f"{r.failure_cost:>12,}{r.total:>13,}"
              f"{r.service_level / 10:>9.1f}%")

    print("   Three costs, never one. A mix can be cheapest precisely by")
    print("   abandoning work, and a single total makes that indistinguishable")
    print("   from routing well -- which is why AC-4.1 asks for them apart and")
    print("   AC-4.2 asks for service \"not cost alone\".")
    return results


def show_the_front(results) -> None:
    print("\n2. The Pareto front")
    front = pareto(results)
    kept = {r.mix for r in front}
    for r in results:
        mark = "on the front" if r.mix in kept else "dominated"
        print(f"   {r.mix:<9}{r.total:>13,}{r.service_level / 10:>9.1f}%"
              f"   {mark}")
    print("   Dominated means something else is at least as cheap *and* at")
    print("   least as good on service. Those are not trade-offs an analyst")
    print("   should be asked to weigh; showing them invites someone to pick")
    print("   one.")


def show_the_average_day(problem, days, mixes) -> None:
    print("\n3. What one average day would have told you")
    mean = average_day(days)
    across = sweep(problem, mixes, days, first_fit)
    on_mean = sweep(problem, mixes, (mean,), first_fit)
    chosen = recommend(problem, mixes, days, first_fit).name

    here = next(r for r in across if r.mix == chosen)
    there = next(r for r in on_mean if r.mix == chosen)
    print(f"   the recommended mix is {chosen} ({len(mean.orders)} drops on "
          f"the average day)")
    print(f"   {'measured on':<24}{'service':>10}")
    print(f"   {'the average day':<24}{there.service_level / 10:>9.1f}%")
    print(f"   {'all ' + str(len(days)) + ' days':<24}"
          f"{here.service_level / 10:>9.1f}%")

    print("   Same fleet, two answers. §7.8 says a deterministic average-day")
    print("   sizing \"systematically under-fleets\", and this is the half that")
    print("   needs no assumptions: the mean day never happens, so a fleet")
    print("   sized against it looks adequate and is not. The error is in the")
    print("   belief before it is in the van count.")


def show_the_condition(problem, days, mixes) -> None:
    print("\n4. When it becomes a van-count error too")
    print(f"   {'a missed drop costs':<24}{'across 30 days':>16}"
          f"{'on the average day':>20}")

    for multiplier in (1, 3, 6):
        def price(instance, order, m=multiplier):
            return round_trip(instance, order) * m
        across = recommend(problem, mixes, days, first_fit, price)
        on_mean = recommend(problem, mixes, (average_day(days),), first_fit,
                            price)
        label = ("one recovery trip" if multiplier == 1
                 else f"{multiplier} recovery trips")
        print(f"   {label:<24}{across.name:>16}{on_mean.name:>20}")

    print("   At one drive they agree, and they are right to: where recourse")
    print("   is genuinely cheap, a small fleet is the correct answer and")
    print("   under-fleeting is not an error. From three upward the marginal")
    print("   van starts paying for itself on days the mean cannot see, and")
    print("   the two methods part company.")
    print("   §7.8 states the claim without the condition. Stating it is the")
    print("   honest form: a real missed delivery carries the redelivery, the")
    print("   admin and the service agreement, so the recourse price is the")
    print("   caller's to set -- and it moves the recommendation.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=dataset.DEFAULT_PATH)
    args = parser.parse_args()

    print(f"fetching a road matrix from {GATEWAY}")
    problem = base_problem(POOL, args.dataset, GATEWAY)
    days = generate_scenarios(problem, days=DAYS, seed=0, typical=TYPICAL)
    mixes = candidates()

    results = show_the_sweep(problem, days, mixes)
    show_the_front(results)
    show_the_average_day(problem, days, mixes)
    show_the_condition(problem, days, mixes)

    assert all(0 <= r.service_level <= FULL for r in results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
