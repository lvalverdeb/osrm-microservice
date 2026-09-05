"""Work worth declining, and work that is not for sale.

Demonstrates optional orders and priority tiers, landed for E-27/T-27
(FR-12, FR-13, §5.1):

    vrp.solve.pyvrp_adapter  `tier_bonuses`, and what makes an order droppable
    vrp.objective            §5.1's tiering, where the lexicographic order lives
    vrp.verify               CON-1, on whatever the solver decided to leave

FR-12: "Support optional orders with prizes so the solver may decline low-value
work when capacity is scarce." FR-13: "Support priority tiers with lexicographic
protection: a higher tier is never sacrificed to improve a lower tier."

The two interact, and the interaction is where this goes wrong. Optionality was
expressed as `required = (prize == 0)` from E-12 onward -- an order carrying a
prize is one the solver may decline -- and nothing consulted `priority_tier`,
whose §4.1 definition begins "0 = must-serve". So a priority-0 order that
happened to carry a prize was droppable, which is exactly FR-13's prohibition.

Four things this shows, in order:

1. **Declining is real.** Given more work than capacity, the solver leaves the
   cheapest-to-lose orders behind rather than returning INFEASIBLE.

2. **Tier 0 is a promise, not a bid.** An order that loses money is still
   served if it is tier 0, whatever its prize says.

3. **Lexicographic means lexicographic.** A weighted objective with a big tier
   multiplier passes almost every test until someone attaches a large enough
   prize to a low tier. The bonuses are checked at 10^3 through 10^15.

4. **Where the end-to-end claim stops holding.** PyVRP declines the low tier
   correctly up to prizes of about 10^6 and misbehaves above 10^9. That is a
   real limit of the engine, and it is printed rather than avoided.

Requires a running gateway: distances are measured on the road network and
there is no straight-line fallback. `examples/.env` points at the FreeBSD jail.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/prizes_and_priority.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

# Importing config puts OSRM_API_URL into the environment, so the
# gateway this example now requires is the one `examples/.env` names.
import config  # noqa: F401
import dataset

from vrp.matrix import PlanarMatrix
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    Vehicle,
    precedence,
)
from vrp.solve.pyvrp_adapter import solve, tier_bonuses
from vrp.verify import verify

DAY = TimeWindow(start=0, end=12 * 3600)


# Two real deliveries around the Guadalupe depot. Their positions and service
# times are the corpus's; the parcel counts below are not, and deliberately so
# -- every section here turns on capacity being exactly scarce enough that one
# order has to go, which is a property of the instance rather than of the data.
LOCATIONS, MATRIX, DELIVERIES, DEPOT = dataset.road_sites(
    4, "spread", "prizes")
RACK = 6


def instance(orders: tuple[Order, ...], capacity: int, stops: int) -> Problem:
    """The instance, over real sites, with capacity the section chooses.

    Args:
        orders: What is on offer.
        capacity: Parcels the single van can carry.
        stops: How many of the real sites to expose.

    Returns:
        The instance.
    """
    return Problem(
        id="prz", locations=LOCATIONS[:stops + 1], orders=orders,
        vehicles=(Vehicle(id="V1", capacities={"parcels": capacity}, shift=DAY,
                          start_location_id="D", end_location_id="D"),),
        matrix=MATRIX)


def an_order(order_id: str, stop: str, parcels: int, **kwargs) -> Order:
    """One order at a real address, with the corpus's own service time."""
    delivery = DELIVERIES[int(stop[1:]) - 1]
    return Order(id=order_id, kind="JOB", quantities={"parcels": parcels},
                 delivery=StopSpec(
                     location_id=stop, time_windows=(DAY,),
                     service_fixed=delivery["service_minutes"] * 60), **kwargs)


def declined(solution) -> set[str]:
    return {entry["order_id"] for entry in solution.unassigned}


def show_declining() -> None:
    """FR-12: capacity is scarce, so some work is not worth taking."""
    print("\n1. More work than the van can carry")
    orders = (an_order("VALUABLE", "C1", parcels=RACK, prize=100_000, priority_tier=2),
              an_order("CHEAP", "C2", parcels=RACK, prize=1, priority_tier=2))
    problem = instance(orders, capacity=RACK, stops=2)
    solution = solve(problem, iterations=600, seed=0)

    print(f"   a {RACK}-parcel rack, two {RACK}-parcel orders, both tier 2")
    print(f"   prizes: VALUABLE {orders[0].prize:,}, CHEAP {orders[1].prize}")
    print(f"   declined: {declined(solution)}")
    print(f"   the plan verifies: {verify(problem, solution).ok}")

    required = (an_order("A", "C1", parcels=RACK), an_order("B", "C2", parcels=RACK))
    same = instance(required, capacity=RACK, stops=2)
    outcome = solve(same, iterations=600, seed=0)
    print(f"   the same instance with no prizes -> status {outcome.status}, "
          f"declined {declined(outcome)}")
    print("   A prizeless order has no price at which declining it is correct,")
    print("   so the shortage becomes the caller's problem rather than being")
    print("   quietly absorbed.")


def _uneconomic() -> tuple[Problem, int, str]:
    """One real delivery far enough out that serving it loses money.

    The old version invented a five-hour leg. It did not have to: the corpus
    reaches Guanacaste, and a single order at the end of a real six-hour run is
    exactly the shape this section needs -- an order whose prize cannot cover
    the driving, kept only because tier 0 is a promise.

    Returns:
        `(instance, one-way seconds, the hub it is near)`.
    """
    corpus = dataset.load()
    depot = corpus.depots[0]
    around, _ = corpus.around_each_depot(24)
    remote = around[16]
    lat_km = 110.57
    lon_km = 111.32 * math.cos(math.radians(depot["latitude"]))
    matrix = PlanarMatrix(version="uneconomic-v1", coordinates=(
        (0.0, 0.0),
        ((remote["longitude"] - depot["longitude"]) * lon_km,
         (remote["latitude"] - depot["latitude"]) * lat_km)))
    shift = TimeWindow(start=0, end=24 * 3600)
    problem = Problem(
        id="uneconomic",
        locations=(Location(id="D", lat=depot["latitude"],
                            lon=depot["longitude"], matrix_index=0),
                   Location(id="C1", lat=remote["latitude"],
                            lon=remote["longitude"], matrix_index=1)),
        orders=(Order(id="MUST", kind="JOB", quantities={"parcels": 1},
                      prize=1, priority_tier=0,
                      delivery=StopSpec(
                          location_id="C1", time_windows=(shift,),
                          service_fixed=remote["service_minutes"] * 60)),),
        vehicles=(Vehicle(id="V1", capacities={"parcels": 100}, shift=shift,
                          start_location_id="D", end_location_id="D"),),
        matrix=matrix)
    return problem, matrix.duration(0, 1), remote["hub"]


def show_tier_zero() -> None:
    """FR-13's floor: tier 0 is not a bid."""
    print("\n2. Tier 0 is a promise")
    from vrp.model import must_be_served

    print(f"   {'order':<34}{'declinable?':>12}")
    for label, order in (
            ("tier 3, prize 5", an_order("D", "C1", parcels=1, prize=5,
                                         priority_tier=3)),
            ("tier 3, no prize", an_order("C", "C1", parcels=1, priority_tier=3)),
            ("tier 0, prize 100,000", an_order("A", "C1", parcels=1, prize=100_000,
                                               priority_tier=0))):
        print(f"   {label:<34}{not must_be_served(order)!s:>12}")

    print("   Asserted on the predicate because through a solve it is invisible:")
    print("   the tier bonus already makes a tier-0 order the most valuable")
    print("   thing in the instance, so it survives whether or not anything")
    print("   marks it required. Perturbation proved that -- reverting the fix")
    print("   passed every end-to-end test.")

    far, leg, where = _uneconomic()
    solution = solve(far, iterations=400, seed=0)
    print(f"\n   a tier-0 order near {where}, {leg // 3600} hours out, "
          f"prize 1: declined {declined(solution)}")
    print("   Here `required` does real work. Serving it loses money even after")
    print("   the tier bonus, so an optional order would be dropped. A promise")
    print("   is not renegotiated because it turned out to be a bad one.")


def show_lexicographic() -> None:
    """The property, checked where it lives rather than through a solve."""
    print("\n3. No prize is large enough to invert the tiers")
    print(f"   {'low-tier prize':>22}{'tier 1 rank':>22}{'tier 4 rank':>22}"
          f"{'protected':>11}")

    for magnitude in (10 ** 3, 10 ** 6, 10 ** 9, 10 ** 12, 10 ** 15):
        orders = (an_order("HIGH", "C1", parcels=1, prize=1, priority_tier=1),
                  an_order("LOW", "C2", parcels=1, prize=magnitude, priority_tier=4))
        bonuses = tier_bonuses(instance(orders, capacity=100, stops=2))
        # Keyed by `precedence`, not by the bare tier: FR-25 ranks a statutory
        # order above an SLA one on the same tier, so the key is a (tier,
        # source) pair. Looking it up with the same function the adapter keys
        # with is what stops this drifting again.
        high = 1 + bonuses[precedence(orders[0])]
        low = magnitude + bonuses[precedence(orders[1])]
        print(f"   {magnitude:>22,}{high:>22,}{low:>22,}{high > low!s:>11}")

    print("   Checked on the bonuses, so it is exact at any magnitude. A")
    print("   weighted objective with a large tier multiplier passes every one")
    print("   of these rows until the prize outgrows the multiplier -- which is")
    print("   precisely the failure FR-13's word \"lexicographic\" forbids.")


def show_the_engine_limit() -> None:
    """Where the end-to-end version stops working, printed rather than hidden."""
    print("\n4. The same claim through PyVRP, until it is not")
    print(f"   {'prize on tier 5':>18}{'status':>14}{'declined':>22}")

    for magnitude in (10 ** 3, 10 ** 6, 10 ** 9):
        orders = (an_order("TIER1", "C1", parcels=RACK, prize=magnitude,
                           priority_tier=1),
                  an_order("TIER5", "C2", parcels=RACK, prize=magnitude,
                           priority_tier=5))
        problem = instance(orders, capacity=RACK, stops=2)
        solution = solve(problem, iterations=600, seed=0)
        print(f"   {magnitude:>18,}{solution.status:>14}"
              f"{sorted(declined(solution))!s:>22}")

    print("   At 10^9 the prize overwhelms PyVRP's internal capacity penalty,")
    print("   so overloading the van looks cheaper than declining the work and")
    print("   it returns INFEASIBLE with nothing dropped. The tiering is still")
    print("   correct -- section 3 proves that at 10^15 -- but the engine can no")
    print("   longer act on it. A real ceiling, in the record rather than")
    print("   designed around by a fixture tuned until it passed.")


def main() -> int:
    show_declining()
    show_tier_zero()
    show_lexicographic()
    show_the_engine_limit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
