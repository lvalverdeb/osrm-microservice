"""A gas escape does not queue behind six routine jobs.

Demonstrates preemption, landed for E-77/T-77 (FR-27, DYN-5, §12.2):

    vrp.triggers   `preempt`, and a delta that tells two outcomes apart
    vrp.model      FR-13's tiers and FR-25's sources, which decide whose slot
    vrp.solve      the objective, which does the deciding
    vrp.committed  the executed work, which is never a candidate

FR-27: "Support **preemption**: higher-priority work arriving mid-shift may
displace planned work that has not yet been executed, with the displaced work
re-planned rather than silently dropped."

`UC-044` is the operation: "A P1 gas escape preempts work already in progress,
requiring the plan to be interruptible mid-route." An urgent job is not an
insertion looking for slack. If there is no slack it takes somebody else's, and
the only questions are whose and whether anybody is told.

Three things, in order:

1. **Whose slot.** Not a decision this makes. FR-13's tiers and FR-25's sources
   already say what may be given up for what, so preemption pins the executed
   work and lets the objective choose. A preemption picking its own victims
   would be a second, quieter priority scheme competing with the declared one.

2. **Told, in the plan.** Work that stops appearing is the silent drop FR-27
   forbids, so displaced orders carry `PREEMPTED` and name what took the slot.
   Work that was never planned keeps its own reason: the round was already
   larger than the van before the emergency arrived, and blaming the shortfall
   on the gas escape would be flattering it.

3. **Re-planned where there is room.** With a second van the displaced work
   moves rather than disappearing, which is FR-27's stated preference and the
   difference between churn and loss.

Requires a running gateway: distances are measured on the road network and
there is no straight-line fallback. `examples/.env` points at the FreeBSD jail.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/dynamic/preemption.py
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

from vrp.bench import fixtures
from vrp.model import Order, StopSpec, TimeWindow
from vrp.solve.pyvrp_adapter import solve
from vrp.triggers import preempt

DAY = TimeWindow(start=0, end=12 * 3600)


def routine(count: int = 6) -> tuple[Order, ...]:
    return tuple(
        Order(id=f"R{i}", kind="JOB", quantities={"kg": 10}, priority_tier=2,
              prize=500_000,
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=600))
        for i in range(1, count + 1))


def gas_escape() -> Order:
    return Order(id="GAS", kind="JOB", quantities={"kg": 10}, priority_tier=0,
                 priority_source="STATUTORY",
                 delivery=StopSpec(location_id="C6", time_windows=(DAY,),
                                   service_fixed=600))


def morning(vans: int):
    """The round the emergency lands in the middle of.

    Real addresses around the Guadalupe depot rather than the bench's grid:
    what a preemption costs is the drive to the stop it displaces, and on a
    uniform grid that cost is the same wherever it falls.
    """
    work = routine()
    locations, matrix, _deliveries, _depot = dataset.road_sites(
        len(work), strategy="spread", name=f"pre{vans}")
    problem = fixtures.instance(
        f"pre{vans}", work,
        tuple(fixtures.van(f"V{n}", capacities={"kg": 40})
              for n in range(1, vans + 1)),
        locations=locations, matrix=matrix)
    return problem, solve(problem, iterations=400, seed=0)


def served(solution) -> set[str]:
    return {step.order_id for route in solution.routes
            for step in route.steps if step.order_id}


def engine(problem):
    return solve(problem, iterations=400, seed=0)


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def whose_slot() -> None:
    heading("1.", "Whose slot, and who decides")
    problem, plan = morning(vans=1)
    print(f"\n   one van, six routine jobs, room for {len(served(plan))}:")
    print(f"      planned: {sorted(served(plan))}")
    print(f"      waiting: {sorted({o.id for o in problem.orders} - served(plan))}")

    arrived = replace(problem, orders=problem.orders + (gas_escape(),))
    response = preempt(arrived, plan, "GAS", now=0, solve=engine)
    print("\n   a gas escape arrives. It is tier 0 and STATUTORY, so it carries")
    print("   no prize -- there is no price at which declining it is fair:")
    print(f"      planned: {sorted(served(response.plan))}")
    print(f"      displaced: {list(response.delta.displaced)}")
    print("\n   The objective chose, not this module. FR-13's tiers and FR-25's")
    print("   sources already say what may be given up for what.")


def told_in_the_plan() -> None:
    heading("2.", "Told, in the plan")
    problem, plan = morning(vans=1)
    before = served(plan)
    arrived = replace(problem, orders=problem.orders + (gas_escape(),))
    response = preempt(arrived, plan, "GAS", now=0, solve=engine)

    print(f"\n   {'order':7s} {'reason':11s} explanation")
    for row in sorted(response.plan.unassigned, key=lambda r: r["order_id"]):
        was_planned = "was planned" if row["order_id"] in before else "never was"
        print(f"      {row['order_id']:7s} {row['reason_code']:11s} "
              f"{row['explanation'][:44]}")
        print(f"      {'':7s} {'':11s} ({was_planned} before the emergency)")
    print("\n   Only work that was planned can be displaced. The round was")
    print("   already larger than the van, and blaming that shortfall on the")
    print("   gas escape would credit it with a problem it did not cause.")


def re_planned_where_there_is_room() -> None:
    heading("3.", "Re-planned where there is room")
    for vans in (1, 2):
        problem, plan = morning(vans=vans)
        arrived = replace(problem, orders=problem.orders + (gas_escape(),))
        delta = preempt(arrived, plan, "GAS", now=0, solve=engine).delta
        print(f"\n   {vans} van{'s' if vans > 1 else ' '}: "
              f"reassigned {len(delta.reassigned)}, "
              f"displaced {len(delta.displaced)} {list(delta.displaced)}")
    print("\n   FR-27 asks for displaced work to be \"re-planned rather than")
    print("   silently dropped\", in that order. Before T-77 the delta reported")
    print("   both as one number, so a plan moving three stops between drivers")
    print("   and a plan abandoning three customers were equally stable by the")
    print("   only measure anybody had.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nFR-27, from CAT-VRP-003 §12.2 -- three operations asked for it.")
    whose_slot()
    told_in_the_plan()
    re_planned_where_there_is_room()
    print(f"\n{'=' * 72}")
    print("Churn and loss are different answers. One costs a dispatcher some")
    print("trust; the other means nobody is coming. A response that reports")
    print("them as one number cannot be used to choose between them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
