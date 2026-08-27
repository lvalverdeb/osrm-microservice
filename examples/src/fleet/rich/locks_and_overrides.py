"""Which two of your twelve instructions disagree.

Demonstrates operator locks and IIS-style conflict diagnosis, landed for
E-29/T-29 (FR-21, CON-7, §6.6):

    vrp.model      §6.6's eight lock kinds, and what each needs to mean anything
    vrp.verify     INV-8, "all locks are satisfied exactly"
    vrp.locks      `minimal_conflict`, the deletion filter behind the diagnosis

CON-7: "Human override is a first-class input, not a failure... The system MUST
NOT silently discard operator intent." §6.6 turns that into a requirement with
teeth: "If locks make the instance infeasible, the system MUST return
`INFEASIBLE` with the minimal conflicting lock set (an IIS-style diagnosis),
never silently drop a lock."

Both halves of that sentence are load-bearing. A dispatcher who pins a load to a
van and gets back a plan using a different van has been overruled without being
told. A dispatcher who gets back "infeasible" has been told nothing useful --
they have twelve locks and no idea which two disagree.

Four things this shows, in order:

1. **Every kind, satisfied and broken.** All eight of §6.6's kinds, each with a
   plan that honours it and a plan that does not. A checker written only
   against satisfying plans cannot fail.

2. **A conflict, named.** Two locks, each perfectly reasonable alone, that
   cannot both hold.

3. **Minimal means irreducible.** Ten innocent locks alongside a conflicting
   pair. Reporting all twelve would be true and useless; every lock reported is
   checked to be load-bearing by removing it and watching feasibility return.

4. **When the locks are not the cause.** An order too heavy for any van is not
   the dispatcher's fault, and blaming their locks would send them to unpick
   decisions that were never the problem.

Runs offline. No gateway required.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/locks_and_overrides.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.locks import is_feasible_under_locks, minimal_conflict
from vrp.model import (
    Location,
    Lock,
    Order,
    Problem,
    Route,
    Solution,
    Step,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.verify import verify

DAY = TimeWindow(start=0, end=12 * 3600)


def instance(locks: tuple[Lock, ...] = (), stops: int = 3, vans: int = 2,
             capacity: int = 100, weights: tuple[int, ...] = ()) -> Problem:
    size = stops + 1
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 1000, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    grid = tuple(tuple(abs(i - j) * 600 for j in range(size))
                 for i in range(size))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB",
              quantities={"kg": weights[i - 1] if weights else 1},
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=60))
        for i in range(1, size))
    fleet = tuple(
        Vehicle(id=f"V{n}", capacities={"kg": capacity}, shift=DAY,
                start_location_id="D", end_location_id="D")
        for n in range(1, vans + 1))
    return Problem(id="locks", locations=locations, orders=orders,
                   vehicles=fleet, locks=locks,
                   matrix=TravelMatrix(version="l", durations=grid,
                                       distances=grid))


def plan(assignment: dict[str, list[str]], problem: Problem) -> Solution:
    routes = []
    for vehicle_id, order_ids in assignment.items():
        steps = [Step(type="START", location_id="D", arrival=0,
                      start_service=0, departure=0)]
        clock = 0
        for order_id in order_ids:
            clock += 600
            stop = problem.order(order_id).delivery
            steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                              order_id=order_id, arrival=clock,
                              start_service=clock, departure=clock + 60))
            clock += 60
        steps.append(Step(type="END", location_id="D", arrival=clock + 600,
                          start_service=clock + 600, departure=clock + 600))
        routes.append(Route(vehicle_id=vehicle_id, steps=tuple(steps)))
    served = {o for ids in assignment.values() for o in ids}
    return Solution(
        problem_id=problem.id, routes=tuple(routes),
        unassigned=tuple({"order_id": o.id, "reason_code": "NOT_PLACED",
                          "explanation": "not in the assignment"}
                         for o in problem.orders if o.id not in served),
        objective_breakdown={}, status="FEASIBLE")


def honoured(locks: tuple[Lock, ...], assignment: dict[str, list[str]]) -> bool:
    problem = instance(locks)
    broken = [v for v in verify(problem, plan(assignment, problem)).violations
              if v.invariant == "INV-8"]
    return not broken


def show_every_kind() -> None:
    """All eight kinds, each judged on a plan that keeps it and one that does not."""
    print("\n1. §6.6's eight lock kinds")
    print(f"   {'kind':<24}{'honoured by':<26}{'and broken by':<26}{'ok':>4}")

    cases = (
        ("PIN_ORDER_TO_VEHICLE",
         (Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1", vehicle_id="V1"),),
         {"V1": ["O1"]}, {"V2": ["O1"]}),
        ("FORBID_ORDER_ON_VEHICLE",
         (Lock(kind="FORBID_ORDER_ON_VEHICLE", order_id="O1",
               vehicle_id="V2"),),
         {"V1": ["O1"]}, {"V2": ["O1"]}),
        ("FIX_ROUTE_PREFIX",
         (Lock(kind="FIX_ROUTE_PREFIX", vehicle_id="V1",
               order_ids=("O1", "O2")),),
         {"V1": ["O1", "O2", "O3"]}, {"V1": ["O2", "O1", "O3"]}),
        ("FIX_SEQUENCE",
         (Lock(kind="FIX_SEQUENCE", vehicle_id="V1", order_ids=("O1", "O3")),),
         {"V1": ["O1", "O2", "O3"]}, {"V1": ["O3", "O2", "O1"]}),
        ("FORCE_DEPLOY",
         (Lock(kind="FORCE_DEPLOY", vehicle_id="V2"),),
         {"V1": ["O1"], "V2": ["O2"]}, {"V1": ["O1", "O2"]}),
        ("FORBID_DEPLOY",
         (Lock(kind="FORBID_DEPLOY", vehicle_id="V2"),),
         {"V1": ["O1", "O2"]}, {"V1": ["O1"], "V2": ["O2"]}),
        ("PIN_DEPOT",
         (Lock(kind="PIN_DEPOT", order_id="O1", depot_id="D"),),
         {"V1": ["O1"]}, None),
        ("FREEZE_UNTIL",
         (Lock(kind="FREEZE_UNTIL", instant=100),),
         {"V1": ["O1"]}, None),
    )

    for name, locks, keeps, breaks in cases:
        kept = honoured(locks, keeps)
        if breaks is None:
            broke = _special_break(name)
        else:
            broke = not honoured(locks, breaks)
        verdict = "yes" if kept and broke else "NO"
        broken_label = "(see below)" if breaks is None else _route(breaks)
        print(f"   {name:<24}{_route(keeps):<26}{broken_label:<26}"
              f"{verdict:>4}")

    print("   Left column: a plan honouring the lock, accepted. Right: one")
    print("   breaking it, rejected. Both are needed -- a checker exercised")
    print("   only against satisfying plans passes by never being asked.")
    print("   PIN_DEPOT breaks when pinned to a depot the route never starts")
    print("   from; FREEZE_UNTIL when the plan sits inside the frozen window")
    print("   without being marked as committed work.")


def _route(assignment: dict[str, list[str]]) -> str:
    return "  ".join(f"{van}: {' '.join(orders)}"
                     for van, orders in assignment.items())


def _special_break(name: str) -> bool:
    """The two kinds whose violation needs a different instance, not a
    different assignment."""
    if name == "PIN_DEPOT":
        problem = instance((Lock(kind="PIN_DEPOT", order_id="O1",
                                 depot_id="OTHER"),))
    else:
        problem = instance((Lock(kind="FREEZE_UNTIL", instant=10 ** 6),))
    assignment = {"V1": ["O1"]} if name == "PIN_DEPOT" else {"V1": ["O1", "O2"]}
    return any(v.invariant == "INV-8"
               for v in verify(problem, plan(assignment, problem)).violations)


def show_a_conflict() -> None:
    """Two reasonable instructions that cannot both hold."""
    print("\n2. A conflict, named")
    locks = (Lock(kind="FORBID_DEPLOY", vehicle_id="V2"),
             Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1", vehicle_id="V2"))
    problem = instance(locks)

    print(f"   feasible under these locks: {is_feasible_under_locks(problem)}")
    for lock in minimal_conflict(problem):
        print(f"     {lock.kind:<24} "
              f"{lock.order_id or '':<4} {lock.vehicle_id or ''}")
    print("   Neither instruction is wrong. \"Keep V2 in the yard\" is fine.")
    print("   \"O1 goes on V2\" is fine. Together they are not, and the answer")
    print("   the dispatcher needs is that pair, not the word INFEASIBLE.")


def show_irreducibility() -> None:
    """Minimality, checked rather than asserted."""
    print("\n3. Two locks that matter among twelve")
    guilty = (Lock(kind="FORBID_DEPLOY", vehicle_id="V2"),
              Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1",
                   vehicle_id="V2"))
    innocent = tuple(
        Lock(kind="FORBID_ORDER_ON_VEHICLE", order_id=f"O{i}", vehicle_id="V2")
        for i in range(2, 12))
    problem = instance((*innocent, *guilty), stops=11, vans=2)

    conflict = minimal_conflict(problem)
    print(f"   locks on the instance: {len(problem.locks)}")
    print(f"   locks reported:        {len(conflict)}")

    print(f"   {'removing':<46}{'feasible again?':>16}")
    for lock in conflict:
        without = instance(tuple(l for l in problem.locks if l != lock),
                           stops=11, vans=2)
        label = f"{lock.kind} {lock.order_id or ''}{lock.vehicle_id or ''}"
        print(f"   {label:<46}{is_feasible_under_locks(without)!s:>16}")

    print("   Every reported lock is load-bearing: drop either one and the")
    print("   instance is feasible again. That is what irreducible means, and")
    print("   it is why the answer is two locks rather than twelve. A set that")
    print("   is sufficient but not minimal passes any test that only asks")
    print("   \"does removing these fix it\".")


def show_not_the_locks() -> None:
    """The line that keeps the diagnosis honest."""
    print("\n4. When the locks are not the cause")
    lock = Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1", vehicle_id="V1")
    too_heavy = instance((lock,), stops=2, capacity=10, weights=(500, 1))

    print("   a 500 kg order, 10 kg vans, one innocent pin")
    print(f"   feasible: {is_feasible_under_locks(too_heavy)}")
    print(f"   locks blamed: {minimal_conflict(too_heavy) or 'none'}")
    print("   Infeasible whatever the operator did, so the locks are not")
    print("   reported. §6.5's pre-flight codes own that case. Blaming the pin")
    print("   would send someone to undo a decision that was never the problem")
    print("   -- and they would undo it, because the system said so.")


def main() -> int:
    show_every_kind()
    show_a_conflict()
    show_irreducibility()
    show_not_the_locks()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
