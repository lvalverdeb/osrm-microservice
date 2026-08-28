"""The public /verify contract — §9.4, CON-1, T-66, E-66.

§9.4 lists `POST /v1/solutions/{id}/verify` and then explains why it is on the
public surface rather than the internal one: "`/verify` is deliberately public:
it lets integrators check plans produced elsewhere, and it forces the verifier
to be genuinely independent of the solver (CON-1)."

The second clause is the interesting one. A verifier that can only check plans
its own solver produced is not independent -- it shares the solver's assumptions
about what a plan looks like, and those assumptions are exactly where a solver
bug hides. Accepting a plan from a system that shares no code with ours is what
proves the boundary is real, which is why this task exists at all rather than
being a thin wrapper nobody needed.

**Parsing is the work, and strictness is the requirement.** An external plan
arrives as JSON from a system with its own types, its own rounding and its own
idea of which fields are optional. The temptation is to be helpful -- infer a
missing arrival, coerce a string, fill a default. Every one of those turns the
report into a statement about a plan the integrator did not send. So the parser
refuses rather than repairs, and every refusal names the field.

**Placement.** Transport-agnostic, in Python, for the reason T-15 already gave
about the rest of §9.4's surface: how the Rust gateway reaches a Python solver
is an open architectural question the SDD does not answer, and inventing an
answer inside a task that does not need one would bury the decision. `/verify`
is the one endpoint in §9.4 that needs no solver at all -- it is a pure function
from (problem, plan) to a report -- so what is delivered is that function and
the request/response shape §9.4 specifies, ready for whichever process ends up
hosting it.
"""

from __future__ import annotations

import json

import pytest

from vrp.api import VerificationError, verify_request

HOUR = 3600

# An externally supplied plan: hand-written JSON, not built by our model
# classes and not produced by our solver. That is the whole point of the
# endpoint, so the fixture has to be that too -- building it with `Problem(...)`
# would quietly test the round trip of our own types.
EXTERNAL = json.loads("""
{
  "problem": {
    "id": "ext-1",
    "locations": [
      {"id": "D",  "lat": 9.90, "lon": -84.0, "matrix_index": 0},
      {"id": "C1", "lat": 9.91, "lon": -84.0, "matrix_index": 1},
      {"id": "C2", "lat": 9.92, "lon": -84.0, "matrix_index": 2}
    ],
    "orders": [
      {"id": "O1", "kind": "JOB", "quantities": {"kg": 1},
       "delivery": {"location_id": "C1", "service_fixed": 60,
                    "time_windows": [{"start": 0, "end": 43200}]}},
      {"id": "O2", "kind": "JOB", "quantities": {"kg": 1},
       "delivery": {"location_id": "C2", "service_fixed": 60,
                    "time_windows": [{"start": 0, "end": 43200}]}}
    ],
    "vehicles": [
      {"id": "V1", "capacities": {"kg": 10},
       "shift": {"start": 0, "end": 43200},
       "start_location_id": "D", "end_location_id": "D"}
    ],
    "matrix": {
      "version": "ext",
      "durations": [[0, 600, 1200], [600, 0, 600], [1200, 600, 0]],
      "distances": [[0, 600, 1200], [600, 0, 600], [1200, 600, 0]]
    }
  },
  "solution": {
    "problem_id": "ext-1",
    "status": "FEASIBLE",
    "routes": [
      {"vehicle_id": "V1", "steps": [
        {"type": "START", "location_id": "D", "arrival": 0,
         "start_service": 0, "departure": 0},
        {"type": "DELIVERY", "location_id": "C1", "order_id": "O1",
         "arrival": 600, "start_service": 600, "departure": 660},
        {"type": "DELIVERY", "location_id": "C2", "order_id": "O2",
         "arrival": 1260, "start_service": 1260, "departure": 1320},
        {"type": "END", "location_id": "D", "arrival": 2520,
         "start_service": 2520, "departure": 2520}
      ]}
    ],
    "unassigned": []
  }
}
""")


def payload(**changes):
    """The external plan with one thing altered."""
    import copy

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


# --------------------------------------------------------------------------
# §9.4: an externally supplied plan is verifiable
# --------------------------------------------------------------------------

def test_a_plan_from_outside_verifies():
    response = verify_request(EXTERNAL)

    assert response["ok"] is True
    assert response["hard_violations"] == []


def test_the_response_names_the_verifier_that_checked_it():
    """§9.4's own example carries `"checked_by": "verifier@1.4.0"`. An
    integrator keeping a report for an audit needs to know which verifier
    produced it, because the answer can change between versions."""
    response = verify_request(EXTERNAL)

    assert response["checked_by"].startswith("verifier@")


def test_invariants_that_were_evaluated_are_listed():
    response = verify_request(EXTERNAL)

    assert "INV-1" in response["invariants_passed"]


def test_invariants_with_no_subject_are_listed_separately():
    """E-03's lesson, on the public surface this time: an invariant nothing
    could reach passes by never being asked, and reporting it alongside the
    ones that were genuinely checked is a lie an integrator would act on.
    """
    response = verify_request(EXTERNAL)

    assert "INV-7" in response["not_applicable"]
    assert "INV-7" not in response["invariants_passed"]


# --------------------------------------------------------------------------
# It catches what it is for
# --------------------------------------------------------------------------

def test_a_seeded_violation_is_caught_and_named():
    """An arrival that does not follow from the matrix -- the shape of bug a
    solver produces and its own tests would not see."""
    broken = payload(**{"solution.routes.0.steps.2.arrival": 900})

    response = verify_request(broken)

    assert response["ok"] is False
    assert any(v["invariant"] == "INV-4" for v in response["hard_violations"])


def test_an_overloaded_route_is_caught_when_the_plan_declares_loads():
    """INV-5 reads `load_after`, so a plan that declares it can be checked for
    capacity."""
    heavy = payload(**{"problem.orders.0.quantities": {"kg": 50},
                       "solution.routes.0.steps.1.load_after": {"kg": 50}})

    response = verify_request(heavy)

    assert response["ok"] is False
    assert any(v["invariant"] == "INV-5" for v in response["hard_violations"])


def test_a_plan_that_declares_no_loads_cannot_be_checked_for_capacity():
    """Worth stating plainly on a public endpoint, because it is the kind of
    gap an integrator would otherwise discover from a passing report.

    INV-5 checks the loads a plan declares. A plan carrying no `load_after` at
    all is not asserting an overload, so there is nothing to contradict -- the
    verifier is silent rather than satisfied. Inferring the loads instead would
    mean reporting on a plan the integrator did not send, which is the one
    thing this endpoint must not do.
    """
    heavy = payload(**{"problem.orders.0.quantities": {"kg": 50}})

    response = verify_request(heavy)

    assert response["ok"] is True
    assert "INV-5" in response["invariants_passed"]


def test_a_violation_carries_enough_to_act_on():
    """A boolean is not a verification report. CON-5's argument again: an
    integrator told "invalid" has to find it themselves."""
    broken = payload(**{"solution.routes.0.steps.2.arrival": 900})

    violation = verify_request(broken)["hard_violations"][0]

    assert violation["invariant"]
    assert len(violation["detail"]) > 10


# --------------------------------------------------------------------------
# Strict parsing: refuse, never repair
# --------------------------------------------------------------------------

def test_a_missing_problem_is_refused():
    with pytest.raises(VerificationError, match="problem"):
        verify_request({"solution": EXTERNAL["solution"]})


def test_a_missing_solution_is_refused():
    with pytest.raises(VerificationError, match="solution"):
        verify_request({"problem": EXTERNAL["problem"]})


def test_a_missing_field_is_refused_rather_than_defaulted():
    """The temptation this endpoint exists to resist. Inferring the arrival
    would produce a report about a plan the integrator did not send, and it
    would pass."""
    with pytest.raises(VerificationError, match="arrival"):
        verify_request(payload(**{"solution.routes.0.steps.1.arrival": None}))


def test_a_string_where_a_number_belongs_is_refused():
    """Coercing "600" would work, and would mean the endpoint silently accepts
    a system that is wrong about its own types -- exactly the class of defect
    an integrator is asking us to find."""
    with pytest.raises(VerificationError, match="arrival"):
        verify_request(payload(**{"solution.routes.0.steps.1.arrival": "600"}))


def test_a_plan_naming_an_unknown_order_is_refused():
    with pytest.raises(VerificationError, match="O99"):
        verify_request(payload(**{"solution.routes.0.steps.1.order_id": "O99"}))


def test_the_error_says_where_the_problem_is():
    """"Invalid payload" sends an integrator to read their whole request."""
    with pytest.raises(VerificationError) as raised:
        verify_request(payload(**{"solution.routes.0.steps.1.arrival": None}))

    assert "steps" in str(raised.value) or "arrival" in str(raised.value)


# --------------------------------------------------------------------------
# CON-1: the endpoint is not a solver
# --------------------------------------------------------------------------

def test_the_endpoint_module_imports_no_solver():
    """§9.4: "/verify... forces the verifier to be genuinely independent of the
    solver (CON-1)". Checked by reading the imports, as E-03 does, so the
    boundary survives somebody adding a convenient helper later.
    """
    import ast
    from pathlib import Path

    source = Path("vrp/api.py").read_text()
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {name for name in imported
                 if "solve" in name or "evaluator" in name
                 or "portfolio" in name or "lns" in name}
    assert not forbidden, forbidden


def test_verifying_twice_gives_the_same_answer():
    """CON-4. A verification report an integrator cannot reproduce is one they
    cannot take to whoever wrote the plan."""
    assert verify_request(EXTERNAL) == verify_request(EXTERNAL)


def test_the_response_is_json_serialisable():
    """It is an HTTP response in everything but transport. A set or a dataclass
    in there is a 500 the day somebody binds it to a route."""
    json.dumps(verify_request(EXTERNAL))
    json.dumps(verify_request(payload(
        **{"solution.routes.0.steps.2.arrival": 900})))


# --------------------------------------------------------------------------
# T-66's definition of done
# --------------------------------------------------------------------------

def test_an_integrator_shaped_round_trip():
    """T-66: "External plans verifiable; used by at least one integrator".

    The first half, end to end: JSON in, JSON out, no shared types with the
    caller and no solver anywhere in the path. The second half needs an
    integrator, and the commit says so rather than counting this as one.
    """
    request = json.dumps(EXTERNAL)
    response = json.dumps(verify_request(json.loads(request)))

    parsed = json.loads(response)
    assert parsed["ok"] is True
    assert set(parsed) >= {"ok", "checked_by", "hard_violations",
                           "soft_violations", "invariants_passed",
                           "not_applicable"}
