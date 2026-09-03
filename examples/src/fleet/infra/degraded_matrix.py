"""A provider that stops answering should cost you the arcs, not the day.

Demonstrates graceful matrix degradation landed for E-79/T-79 (NFR-04, MTX-11):

    vrp.matrix   the tiled build, and what it keeps when a tile never arrives
    vrp.model    `TravelMatrix.degraded` and `Solution.degraded`
    vrp.solve    which carries one to the other

NFR-04: "If the matrix provider is unavailable, fall back to a cached matrix
and mark the plan `DEGRADED`; never fall back silently to haversine for a
committed plan."

`UC-072` is the instance: a provider that stops responding partway through a
large build. The requirement asks for three things and the engine had one of
them for a long time -- it refused to invent arcs, which is safe, and then
threw the whole build away, which is not graceful. A provider dying on the last
of forty tiles discarded thirty-nine good ones and the plan with them.

Three things, in order:

1. **What survives.** The tiles that arrived are kept. The ones that did not
   stay `UNREACHABLE`, which MTX-5 makes a hard-infeasible arc rather than a
   guess: the plan covers less ground, and nothing in it is invented.

2. **What the matrix says about itself.** A sentence, not a flag. An operator
   deciding whether to dispatch needs to know which arcs are missing and why;
   `degraded=True` tells them the plan is suspect and nothing about what to do.

3. **Where a dispatcher actually looks.** The plan, not the matrix. A
   degradation recorded only on the input is recorded where nobody deciding
   will see it, which is the failure this example exists to close.

Runs offline: the provider is a stub that answers once and then times out.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/infra/degraded_matrix.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

import httpx

import vrp.matrix as matrix_module
from vrp.bench import fixtures
from vrp.model import UNREACHABLE
from vrp.osrm import Snap
from vrp.solve.pyvrp_adapter import solve

COORDS = [(9.90 + i / 100, -84.0) for i in range(6)]


def failing_provider(answers: int):
    """A provider that answers `answers` tiles and then stops.

    Returns:
        The call counter, and the `snap`/`fetch` pair to hand `build_large_matrix`.

    Injected through the public seam rather than assigned over the module's
    globals. That is not tidiness: an integrator who wants to know how their
    own service behaves when the matrix provider dies needs exactly this, and
    a demonstration that reached inside `vrp.matrix` would have shown them a
    technique they cannot use in their own code.
    """
    calls = {"n": 0}

    def snap(*_args, **_kwargs):
        return [Snap(location=point, snapped=point, distance_m=0.0)
                for point in COORDS]

    def fetch(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] > answers:
            raise httpx.TimeoutException("gateway stopped responding")
        size = len(COORDS)
        return {"durations": [[60] * size for _ in range(size)],
                "distances": [[600] * size for _ in range(size)]}

    return calls, snap, fetch


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def what_survives() -> None:
    heading("1.", "What survives")
    answers = 1
    calls, snap, fetch = failing_provider(answers=answers)
    matrix, _snaps = matrix_module.build_large_matrix(
        "http://gateway", COORDS, max_cells=9, snap=snap, fetch=fetch)

    cells = [cell for row in matrix.durations for cell in row]
    known = sum(1 for cell in cells if cell != UNREACHABLE)
    # `calls` counts every attempt, including the ones that raised.
    print(f"\n   the provider answered {min(answers, calls['n'])} of "
          f"{calls['n']} tile requests, then stopped")
    print(f"   arcs known:   {known:3d}")
    print(f"   arcs unknown: {len(cells) - known:3d}  (UNREACHABLE, not zero)")
    print("\n   Nothing was invented. MTX-5 makes an unreachable pair a hard-")
    print("   infeasible arc, so the plan covers less ground rather than being")
    print("   costed against a road network that does not exist.")


def what_the_matrix_says() -> None:
    heading("2.", "What the matrix says about itself")
    _calls, snap, fetch = failing_provider(answers=1)
    matrix, _snaps = matrix_module.build_large_matrix(
        "http://gateway", COORDS, max_cells=9, snap=snap, fetch=fetch)

    print(f"\n   degraded: {matrix.degraded}")
    print("\n   A sentence rather than a flag. `degraded=True` tells an operator")
    print("   the plan is suspect and nothing about what to do with it.")


def where_a_dispatcher_looks() -> None:
    heading("3.", "Where a dispatcher actually looks")
    problem = fixtures.FIXTURES["UC-070"]()
    ordinary = solve(problem, iterations=100, seed=0)

    marked = replace(problem, matrix=replace(
        problem.matrix, degraded="two tiles were never fetched"))
    degraded = solve(marked, iterations=100, seed=0)

    print(f"\n   {'plan':10s} {'status':11s} degraded")
    for label, plan in (("ordinary", ordinary), ("degraded", degraded)):
        print(f"      {label:10s} {plan.status:11s} {plan.degraded or '—'}")
    print("\n   `DEGRADED` is not `INFEASIBLE`: the plan works, and it is costed")
    print("   against arcs nobody measured. Those are different facts and an")
    print("   operator needs both, so they are different fields.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nNFR-04 and MTX-11, and the instance that found them unbuilt: "
          "CAT-VRP-003 UC-072.")
    what_survives()
    what_the_matrix_says()
    where_a_dispatcher_looks()
    print(f"\n{'=' * 72}")
    print("Refusing to guess was always the safe half. The graceful half is")
    print("keeping what arrived, saying what did not, and carrying that as far")
    print("as the person who has to decide whether to send the vans.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
