"""Checking a plan we did not make.

Demonstrates the public /verify contract landed for E-66/T-66 (§9.4, CON-1):

    vrp.api      the request and response shapes, and the strict parser
    vrp.verify   T-04's independent verifier, unchanged since the Slice 0 gate

§9.4 puts `/verify` on the public surface deliberately: "it lets integrators
check plans produced elsewhere, and it forces the verifier to be genuinely
independent of the solver (CON-1)."

The second clause is the point. A verifier that can only check plans its own
solver produced is not independent -- it shares the solver's assumptions about
what a plan looks like, and those assumptions are where a solver bug hides.

Four things, in order:

1. **A plan from outside**, as JSON, checked and passing.

2. **The same plan with a seeded fault**, caught and named.

3. **What the parser refuses**, and why refusing beats repairing.

4. **What it cannot tell you**, which a public endpoint owes its callers.

Runs offline. No gateway required.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/verify/external_plan.py
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.api import VerificationError, verify_request

# Hand-written JSON, as an integrator's planner would emit it. Nothing here
# comes from our model classes, which is what makes this a test of the boundary
# rather than a round trip of our own types.
EXTERNAL = json.loads("""
{
  "problem": {
    "id": "acme-2026-08-28",
    "locations": [
      {"id": "DEPOT", "lat": 9.90, "lon": -84.0, "matrix_index": 0},
      {"id": "SHOP",  "lat": 9.91, "lon": -84.0, "matrix_index": 1},
      {"id": "CAFE",  "lat": 9.92, "lon": -84.0, "matrix_index": 2}
    ],
    "orders": [
      {"id": "A-1", "kind": "JOB", "quantities": {"kg": 4},
       "delivery": {"location_id": "SHOP", "service_fixed": 60,
                    "time_windows": [{"start": 0, "end": 43200}]}},
      {"id": "A-2", "kind": "JOB", "quantities": {"kg": 3},
       "delivery": {"location_id": "CAFE", "service_fixed": 60,
                    "time_windows": [{"start": 0, "end": 43200}]}}
    ],
    "vehicles": [
      {"id": "VAN-7", "capacities": {"kg": 10},
       "shift": {"start": 0, "end": 43200},
       "start_location_id": "DEPOT", "end_location_id": "DEPOT"}
    ],
    "matrix": {"version": "acme-osrm-3",
               "durations": [[0, 600, 1200], [600, 0, 600], [1200, 600, 0]],
               "distances": [[0, 600, 1200], [600, 0, 600], [1200, 600, 0]]}
  },
  "solution": {
    "problem_id": "acme-2026-08-28",
    "status": "FEASIBLE",
    "routes": [
      {"vehicle_id": "VAN-7", "steps": [
        {"type": "START", "location_id": "DEPOT", "arrival": 0,
         "start_service": 0, "departure": 0, "load_after": {"kg": 7}},
        {"type": "DELIVERY", "location_id": "SHOP", "order_id": "A-1",
         "arrival": 600, "start_service": 600, "departure": 660,
         "load_after": {"kg": 3}},
        {"type": "DELIVERY", "location_id": "CAFE", "order_id": "A-2",
         "arrival": 1260, "start_service": 1260, "departure": 1320,
         "load_after": {"kg": 0}},
        {"type": "END", "location_id": "DEPOT", "arrival": 2520,
         "start_service": 2520, "departure": 2520, "load_after": {"kg": 0}}
      ]}
    ],
    "unassigned": []
  }
}
""")


def altered(**changes):
    body = copy.deepcopy(EXTERNAL)
    for path, value in changes.items():
        target = body
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[int(part)] if part.isdigit() else target[part]
        last = parts[-1]
        if value is None:
            target.pop(int(last) if last.isdigit() else last, None)
        else:
            target[int(last) if last.isdigit() else last] = value
    return body


def show_a_good_plan() -> None:
    print("\n1. A plan from outside")
    response = verify_request(EXTERNAL)
    print(f"   ok:              {response['ok']}")
    print(f"   checked_by:      {response['checked_by']}")
    print(f"   passed:          {len(response['invariants_passed'])} invariants")
    print(f"   not applicable:  {response['not_applicable']}")
    print("   Nothing in that request came from our types. It arrived as JSON")
    print("   from a planner that shares no code with ours, which is exactly")
    print("   what §9.4 wants: \"it forces the verifier to be genuinely")
    print("   independent of the solver (CON-1)\".")
    print("   The not-applicable list is separate from the passed list on")
    print("   purpose. An invariant nothing could reach passes by never being")
    print("   asked, and reporting it as checked is a claim an integrator")
    print("   would act on.")


def show_a_bad_plan() -> None:
    print("\n2. The same plan, one number changed")
    broken = altered(**{"solution.routes.0.steps.2.arrival": 900})
    response = verify_request(broken)

    print(f"   ok: {response['ok']}")
    for violation in response["hard_violations"]:
        print(f"     {violation['invariant']}: {violation['detail']}")
    print("   The plan claims the van reached the cafe at 900 s. The matrix")
    print("   says it left the shop at 660 and the leg takes 600. This is the")
    print("   shape of bug a solver's own tests will not see, because they")
    print("   trust the same arithmetic that produced it.")


def show_what_is_refused() -> None:
    print("\n3. What the parser refuses")
    cases = {
        "a missing arrival":
            altered(**{"solution.routes.0.steps.1.arrival": None}),
        "an arrival sent as a string":
            altered(**{"solution.routes.0.steps.1.arrival": "600"}),
        "a stop naming an unknown order":
            altered(**{"solution.routes.0.steps.1.order_id": "A-99"}),
        "no problem at all": {"solution": EXTERNAL["solution"]},
    }
    for label, request in cases.items():
        try:
            verify_request(request)
            print(f"   {label:<34} accepted  <- would be a bug")
        except VerificationError as error:
            print(f"   {label:<34} refused: {error}")

    print("   Every one of these could have been repaired. Infer the arrival,")
    print("   coerce the string, drop the unknown stop -- and every repair")
    print("   produces a report about a plan the integrator did not send, which")
    print("   would pass. Passing wrongly is worse than failing.")
    print("   A refusal is also a different answer from a failed verification:")
    print("   \"your plan is invalid\" is a bug in their planner, \"your request")
    print("   is malformed\" is a bug in their client, and they are fixed by")
    print("   different people.")


def show_the_limits() -> None:
    print("\n4. What it cannot tell you")
    thin = altered(**{"problem.orders.0.quantities": {"kg": 50}})
    for step in thin["solution"]["routes"][0]["steps"]:
        step.pop("load_after", None)
    response = verify_request(thin)

    print("   a 50 kg order in a 10 kg van, with no load_after declared:")
    print(f"     ok: {response['ok']}")
    print("   INV-5 checks the loads a plan *declares*. A plan carrying none is")
    print("   not asserting an overload, so there is nothing to contradict --")
    print("   the verifier is silent rather than satisfied. Inferring the loads")
    print("   would mean reporting on a plan nobody sent.")
    print("   Worth saying on a public endpoint, because it is the kind of gap")
    print("   an integrator would otherwise discover from a passing report.")
    print("   The fix is on their side: declare the loads and the check has")
    print("   something to check.")
    print("\n   T-66 also asks for \"used by at least one integrator\". That")
    print("   needs an integrator. What is here is the half that does not: a")
    print("   contract they can call, with no solver anywhere in the path.")


def main() -> int:
    show_a_good_plan()
    show_a_bad_plan()
    show_what_is_refused()
    show_the_limits()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
