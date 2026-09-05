"""Which vans go out, and what the last one is worth.

Demonstrates operational allocation, landed for E-44/T-44 (FR-30, FR-33, FR-36,
§7.8):

    vrp.objective   per-vehicle fixed and running costs -- the decider
    vrp.evaluator   the same rates in the accountant the portfolio picks on
    vrp.allocate    FR-36's allocation block, and marginal value per vehicle

§7.8: "Allocation is solved *jointly* with routing by making vehicle deployment
endogenous: each vehicle carries a fixed cost that is charged only if it is
used, so the search decides deployment."

There is no allocation solver here, and that is the design rather than an
omission. Deployment is a decision the routing search already makes, once the
cost of making it is on each vehicle instead of averaged across the fleet. What
this shows is that the cost is now in the right place, and what falls out of
putting it there.

Four things, in order:

1. **Deployment is a decision, not an outcome.** Six vehicles offered, and the
   number that go out changes with what they cost -- nothing else about the
   instance moves.

2. **Own versus hire, priced.** Own capacity is sunk cost; a hired van costs a
   day whether it does one drop or twenty (OBJ-4's step function), and a
   contractor paid per drop is a third structure again. The break-even is
   arithmetic and it is printed.

3. **The allocation block.** FR-36: per vehicle, utilisation on every capacity
   dimension, duty used against duty available, and what it cost.

4. **Marginal value.** §7.8's "objective delta from re-solving with that vehicle
   removed", by actually re-solving. Including the vehicle whose delta is not a
   number, because the work cannot be done without it.

Requires a running gateway: distances are measured on the road network and
there is no straight-line fallback. `examples/.env` points at the FreeBSD jail. About 40 s, most of it re-solving.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/alloc/fleet_mix.py
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

# Importing config puts OSRM_API_URL into the environment, so the
# gateway this example now requires is the one `examples/.env` names.
import config  # noqa: F401
import dataset
from pyvrp.PenaltyManager import PenaltyBoundWarning

from vrp.allocate import allocate, marginal_values
from vrp.evaluator import evaluate
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    Vehicle,
)
from vrp.objective import Mode, ObjectiveSpec, Tier, score
from vrp.solve import pyvrp_adapter
from vrp.verify import verify

GATEWAY = os.environ.get("OSRM_API_URL", "http://localhost:8000")
DAY = TimeWindow(start=0, end=10 * 3600)
SPEC = ObjectiveSpec(mode=Mode.MIN_COST)


_ROUND: dict[int, tuple] = {}


def real_round(stops: int) -> tuple:
    """The same real round every scenario is judged on, fetched once.

    Geography is the *constant* in this example: every scenario below plans
    the same customers and varies only the fleet and the load per drop. That
    is what makes the comparison a comparison, so the round is built once and
    cached rather than refetched -- and the load stays a parameter of
    `fleet_problem`, not a property of the data, for the same reason.

    What changes by moving off the generated ring is the shape of the answer.
    On a ring every customer sits the same distance from the depot, so the
    marginal van is worth the same wherever it is added and FR-36's marginal
    values come out flat. Real deliveries are not equidistant, and the last
    van is worth what the work at the edge of the round costs.

    Args:
        stops: How many deliveries the round contains.

    Returns:
        The locations, the road matrix, and the depot record.
    """
    if stops not in _ROUND:
        deliveries, depot = dataset.load().spread(stops)
        points = [(depot["latitude"], depot["longitude"])]
        points += [(d["latitude"], d["longitude"]) for d in deliveries]
        matrix = dataset.road_matrix(points, GATEWAY,
                                                    "fleet-mix")
        locations = (Location(id="D", lat=depot["latitude"],
                              lon=depot["longitude"], matrix_index=0),) + tuple(
            Location(id=f"C{i + 1}", lat=d["latitude"], lon=d["longitude"],
                     matrix_index=i + 1)
            for i, d in enumerate(deliveries))
        _ROUND[stops] = (locations, matrix, depot)
    return _ROUND[stops]


def fleet_problem(vehicles: tuple[Vehicle, ...], stops: int = 12,
                  kg: int = 30, prize: int = 0) -> Problem:
    locations, matrix, _ = real_round(stops)
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": kg}, prize=prize,
              priority_tier=1 if prize else 0,
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=300))
        for i in range(1, stops + 1))
    return Problem(id="mix", locations=locations, orders=orders,
                   vehicles=vehicles, matrix=matrix)


def own(vehicle_id: str, capacity: int = 120) -> Vehicle:
    """Already bought, already insured, driver already salaried."""
    return Vehicle(id=vehicle_id, capacities={"kg": capacity}, shift=DAY,
                   start_location_id="D", end_location_id="D",
                   cost_per_metre=1)


def hired(vehicle_id: str, day_rate: int, capacity: int = 120,
          per_drop: int = 0) -> Vehicle:
    return Vehicle(id=vehicle_id, capacities={"kg": capacity}, shift=DAY,
                   start_location_id="D", end_location_id="D",
                   fixed_cost=day_rate, cost_per_metre=1,
                   cost_per_order=per_drop)


def _money(scored) -> int:
    """The tiers PRIZE_COLLECTING trades in one currency, as money.

    Not `Score.total`: that is scaled so the lexicographic ordering cannot
    invert, and its magnitude (around 10^17 here) is a scaling artefact rather
    than a price. The comparison is honest either way; only one of them is
    readable, and printing the scaled number as if it were colones would be a
    figure nobody could check.
    """
    return (scored.values[Tier.FLEET] + scored.values[Tier.OPERATING]
            + scored.values[Tier.UNSERVED])


def _shown(value: int | None) -> str:
    return "load-bearing" if value is None else f"{value:+,}"


def deployed_of(problem: Problem, solution) -> dict[str, list[str]]:
    return {route.vehicle_id: [s.order_id for s in route.steps if s.order_id]
            for route in solution.routes
            if any(s.order_id for s in route.steps)}


def show_deployment_is_a_decision() -> None:
    """How many vans go out, and what actually decides it."""
    print("\n1. The same fleet, more work each time")
    print(f"   {'kg per drop':>13}{'total kg':>11}{'floor':>8}"
          f"{'deployed':>10}{'legal':>8}{'cost':>12}")

    fleet = tuple(own(f"V{n}", capacity=120) for n in range(1, 7))
    for kg in (10, 20, 30, 40):
        problem = fleet_problem(fleet, stops=12, kg=kg)
        solution = pyvrp_adapter.solve(problem, iterations=2_000, seed=0)
        assignment = deployed_of(problem, solution)
        floor = -(-12 * kg // 120)
        print(f"   {kg:>13}{12 * kg:>11}{floor:>8}{len(assignment):>10}"
              f"{verify(problem, solution).ok!s:>8}"
              f"{evaluate(problem, assignment).total:>12,}")

    print("   Six vans on offer every time, and the number that goes out is")
    print("   the capacity floor -- which nothing told the search. It priced")
    print("   each vehicle and worked the floor out. That is")
    print("   FR-30's \"deployment is a decision\", and it needs no allocation")
    print("   solver, only the fixed cost sitting on the vehicle.")
    print("   It also settles what a day rate can and cannot buy. In a metric")
    print("   instance the triangle inequality makes merging two routes never")
    print("   worse on distance, so an extra van never pays for itself on")
    print("   driving. A vehicle is deployed because the work does not fit")
    print("   without it -- which is why the interesting question is not \"how")
    print("   many\" but \"own or hired\", and that is section 2.")


def show_own_versus_hire() -> None:
    """FR-33's three structures, and the break-even that actually exists."""
    print("\n2. When own capacity runs out: hire, or turn the work away")
    print("   Three own vans hold 360 kg. Twelve drops of 40 kg is 480 kg, so")
    print("   120 kg has nowhere to go without a fourth vehicle.")
    print(f"   {'fourth vehicle':<32}{'hired?':>8}{'served':>8}{'cost':>12}")

    prize = 240_000
    spec = ObjectiveSpec(mode=Mode.PRIZE_COLLECTING)
    base = [own(f"OWN{n}") for n in range(1, 4)]
    offers = {
        "hired at 200,000/day": hired("HIRE", day_rate=200_000),
        "hired at 900,000/day": hired("HIRE", day_rate=900_000),
        "contractor at 60,000/drop": hired("HIRE", day_rate=0, per_drop=60_000),
    }
    for label, fourth in offers.items():
        problem = fleet_problem((*base, fourth), stops=12, kg=40, prize=prize)
        solution = pyvrp_adapter.solve(problem, iterations=3_000, seed=0)
        assignment = deployed_of(problem, solution)
        served = sum(len(ids) for ids in assignment.values())
        money = _money(score(problem, solution, spec))
        print(f"   {label:<30}{'yes' if 'HIRE' in assignment else 'no':>7}"
              f"{served:>8}{money:>14,}")

    print(f"   Each drop is worth {prize:,} if served and forgone if not, and")
    print("   PRIZE_COLLECTING puts the prize, the day rate and the driving in")
    print("   one currency -- so the answer is arithmetic: hire iff")
    print("   `fixed_cost + running < prize forgone`.")
    print("   Own capacity is sunk cost and costs only what it drives. A hired")
    print("   van costs a full day whether it does one drop or twenty: OBJ-4's")
    print("   step function, which must not be amortised per kilometre. A")
    print("   contractor paid per drop is a third structure again, and before")
    print("   T-44 the model could express only two of the three.")
    print("   PyVRP prices the day rate natively but has no per-drop vehicle")
    print("   cost, so the contractor's fee is scored rather than searched")
    print("   against. It still decides the winner, because every plan is")
    print("   re-scored canonically -- but the engine is not optimising it.")


def show_allocation_block() -> None:
    """FR-36, on a plan that actually needed the whole fleet."""
    print("\n3. The allocation block (FR-36)")
    fleet = (own("OWN1"), own("OWN2"), own("OWN3", capacity=60),
             hired("HIRE", day_rate=45_000))
    problem = fleet_problem(fleet, stops=12, kg=30)
    solution = pyvrp_adapter.solve(problem, iterations=3_000, seed=0)

    report = allocate(problem, solution, SPEC)
    print(f"   {'vehicle':<9}{'out':>5}{'drops':>7}{'kg used':>10}"
          f"{'duty':>10}{'fixed':>9}{'running':>10}")
    for entry in sorted(report, key=lambda e: e.vehicle_id):
        print(f"   {entry.vehicle_id:<9}{'yes' if entry.deployed else 'no':>5}"
              f"{entry.orders:>7}{entry.utilisation['kg'] / 10:>9.0f}%"
              f"{entry.duty_utilisation / 10:>9.0f}%{entry.fixed_cost:>9,}"
              f"{entry.operating_cost:>10,}")

    print("   Utilisation is peak load, on every dimension the vehicle")
    print("   declares -- a van full by volume and empty by weight is a")
    print("   different purchase decision from one full by both, and one")
    print("   number cannot say that. Duty is reported beside it because a")
    print("   fleet at 40% on capacity and 95% on hours is short of drivers,")
    print("   not of vans.")
    print("   Every figure is recomputed from the matrix and the orders. INV-9")
    print("   says not to trust a solver's own accounting, and an allocation")
    print("   block is exactly where a plausible wrong number would live for")
    print("   years: it is prose, it looks authoritative, and nobody checks it.")


def solve_overloaded(problem: Problem):
    """Solve an instance whose fleet cannot carry the work, quietly.

    The tight fleet in section 4 holds 420 kg and is asked to carry 480, and
    each re-solve below it drops a vehicle from that. Both are infeasible on
    purpose -- an overloaded plan is exactly what the right-hand column
    measures -- so PyVRP reaching its penalty bound is the expected answer
    here rather than something to report.

    On the generated ring this example used to plan, the warning never
    surfaced: uniform legs make an overloaded route cheap to represent. Real
    road distances are not uniform and it does surface, so it is silenced at
    the two call sites that provoke it deliberately, leaving a genuine
    warning anywhere else still visible.

    Args:
        problem: An instance expected to have no feasible solution.

    Returns:
        The best plan found, overloaded and complete.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=PenaltyBoundWarning)
        return pyvrp_adapter.solve(problem, iterations=3_000, seed=0)


def show_marginal_value() -> None:
    """§7.8's delta, by re-solving rather than estimating."""
    print("\n4. Marginal value: re-solve without each vehicle")
    fleet = (own("OWN1"), own("OWN2"), own("OWN3", capacity=60),
             hired("HIRE", day_rate=45_000))
    problem = fleet_problem(fleet, stops=12, kg=20)
    solution = pyvrp_adapter.solve(problem, iterations=3_000, seed=0)

    def resolve(reduced: Problem):
        return solve_overloaded(reduced)

    print(f"   {'vehicle':<9}{'slack fleet':>16}{'tight fleet':>16}")
    tight = fleet_problem(fleet, stops=12, kg=40)
    tight_plan = solve_overloaded(tight)

    slack_values = marginal_values(problem, solution, SPEC, resolve)
    tight_values = marginal_values(tight, tight_plan, SPEC, resolve)
    for vehicle_id in sorted(slack_values):
        print(f"   {vehicle_id:<9}{_shown(slack_values[vehicle_id]):>16}"
              f"{_shown(tight_values[vehicle_id]):>16}")

    print("   Left: 20 kg a drop, 240 kg against 420 kg of fleet, so there is")
    print("   room to give a vehicle back. Right: 40 kg a drop is 480 kg and")
    print("   every van is carrying work no other van can take.")
    print("   The right-hand column is the reason a re-solve is checked rather")
    print("   than trusted. Asked to drop a van it cannot spare, PyVRP does not")
    print("   return an incomplete plan -- every order is required, so it")
    print("   returns a complete, overloaded one. An earlier draft of this")
    print("   example priced those and reported every vehicle as surplus on a")
    print("   fleet that could not carry the work at all.")

    print("   Sign follows cost. +45,000 against OWN1 says losing it forces")
    print("   the hire, so it is worth its day rate. +0 against HIRE says the")
    print("   hire is not earning anything today -- that is the one to stand")
    print("   down. A negative value would be a vehicle costing more than it")
    print("   saves, and the fleet would be better off without it.")
    print("   \"load-bearing\" is not a large number and must never be rendered")
    print("   as one. Expensive and indispensable are opposite answers to a")
    print("   fleet-sizing question, and T-46's sweep would invert on the")
    print("   confusion.")


def main() -> int:
    show_deployment_is_a_decision()
    show_own_versus_hire()
    show_allocation_block()
    show_marginal_value()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
