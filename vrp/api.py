"""The public /verify contract — §9.4, CON-1, T-66.

§9.4 lists `POST /v1/solutions/{id}/verify` and says why it is public rather
than internal: "`/verify` is deliberately public: it lets integrators check
plans produced elsewhere, and it forces the verifier to be genuinely independent
of the solver (CON-1)."

The second clause is the one that gives this module its shape. A verifier that
can only check plans its own solver produced is not independent -- it shares the
solver's assumptions about what a plan looks like, and those assumptions are
where a solver bug hides. Accepting a plan from a system that shares no code
with ours is what proves the boundary is real.

**Parsing is the work, and strictness is the requirement.** An external plan
arrives as JSON from a system with its own types, its own rounding and its own
idea of which fields are optional. The temptation is to be helpful: infer a
missing arrival, coerce `"600"` to `600`, default an absent window. Every one of
those produces a report about a plan the integrator did not send -- and it
would pass, which is worse than failing. So the parser refuses and names the
field.

**Placement.** Transport-agnostic, in Python, for the reason T-15 gave about the
rest of §9.4's surface: how the Rust gateway reaches a Python solver is an open
architectural question the SDD does not answer, and answering it inside a task
that does not need one would bury the decision. `/verify` is the one endpoint in
§9.4 that needs no solver at all -- it is a pure function from (problem, plan) to
a report -- so this is that function plus §9.4's request and response shapes,
ready for whichever process ends up hosting it.

CON-1 is enforced here the way §11.2 enforces it on the verifier: by what this
module is allowed to import, checked in the tests rather than asserted in a
comment.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

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
    ValidationError,
    Vehicle,
)
from vrp.verify import verify

VERIFIER_VERSION = "verifier@1.0.0"


class VerificationError(ValueError):
    """A request that cannot be verified as sent.

    Deliberately distinct from a *failed* verification. "Your plan is invalid"
    and "your request is malformed" are different answers and an integrator
    acts on them differently -- one is a bug in their planner, the other a bug
    in their client.
    """


def verify_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """§9.4's `/verify`: check an externally supplied plan.

    Args:
        payload: `{"problem": {...}, "solution": {...}}`, as JSON would
            deserialise it.

    Returns:
        §9.4's verification block, JSON-serialisable throughout: `ok`,
        `checked_by`, `hard_violations`, `soft_violations`, `invariants_passed`
        and `not_applicable`.

    Raises:
        VerificationError: if the payload cannot be read as sent. Never a
            partial parse and never a default -- see the module docstring.
    """
    for key in ("problem", "solution"):
        if key not in payload:
            raise VerificationError(f"payload is missing {key!r}")

    problem = _problem(payload["problem"])
    solution = _solution(payload["solution"], problem)
    report = verify(problem, solution)

    return {
        "ok": report.ok,
        "checked_by": VERIFIER_VERSION,
        "hard_violations": [
            {"invariant": v.invariant, "detail": v.detail,
             "vehicle_id": v.vehicle_id, "order_id": v.order_id}
            for v in report.violations],
        # Soft violations are priced rather than forbidden (§6.2), so they are
        # not failures and belong in their own list. Empty until the evaluator
        # side of §9.4 is wired -- reported as empty rather than omitted, so
        # nobody reads a missing key as "none found".
        "soft_violations": [],
        "invariants_passed": sorted(
            _EVALUATED - report.not_applicable
            - {v.invariant for v in report.violations}),
        "not_applicable": sorted(report.not_applicable),
    }


_EVALUATED = {f"INV-{n}" for n in range(1, 14)}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def _int(source: Mapping[str, Any], key: str, where: str) -> int:
    """An integer, or a refusal naming the field.

    `bool` is excluded because it is an `int` in Python and `True` is not an
    arrival time. Coercion is refused outright: a client that sends `"600"` is
    wrong about its own types, and that is precisely the class of defect an
    integrator is asking this endpoint to find.
    """
    _require(key in source, f"{where} is missing {key!r}")
    value = source[key]
    _require(isinstance(value, int) and not isinstance(value, bool),
             f"{where} {key!r} must be an integer, got {value!r}")
    return value


def _window(raw: Mapping[str, Any], where: str) -> TimeWindow:
    return TimeWindow(start=_int(raw, "start", where),
                      end=_int(raw, "end", where),
                      hardness=raw.get("hardness", "HARD"))


def _problem(raw: Mapping[str, Any]) -> Problem:
    _require(isinstance(raw, Mapping), "problem must be an object")
    try:
        locations = tuple(
            Location(id=item["id"], lat=float(item["lat"]),
                     lon=float(item["lon"]),
                     matrix_index=_int(item, "matrix_index", "location"),
                     inventory=item.get("inventory"))
            for item in raw.get("locations", ()))
        orders = tuple(_order(item) for item in raw.get("orders", ()))
        vehicles = tuple(_vehicle(item) for item in raw.get("vehicles", ()))
        matrix_raw = raw.get("matrix") or {}
        matrix = TravelMatrix(
            version=matrix_raw.get("version", "external"),
            durations=tuple(tuple(row) for row in matrix_raw.get("durations", ())),
            distances=tuple(tuple(row) for row in matrix_raw.get("distances", ())))
        locks = tuple(Lock(**item) for item in raw.get("locks", ()))
        return Problem(id=raw.get("id", "external"), locations=locations,
                       orders=orders, vehicles=vehicles, matrix=matrix,
                       locks=locks)
    except (KeyError, TypeError, ValidationError) as error:
        raise VerificationError(f"problem is not readable: {error}") from error


def _order(raw: Mapping[str, Any]) -> Order:
    return Order(
        id=raw["id"], kind=raw.get("kind", "JOB"),
        quantities=dict(raw.get("quantities", {})),
        prize=raw.get("prize", 0), priority_tier=raw.get("priority_tier", 0),
        delivery=_stop(raw.get("delivery"), f"order {raw['id']} delivery"),
        pickup=_stop(raw.get("pickup"), f"order {raw['id']} pickup"))


def _stop(raw: Mapping[str, Any] | None, where: str) -> StopSpec | None:
    if raw is None:
        return None
    windows = tuple(_window(w, where) for w in raw.get("time_windows", ()))
    return StopSpec(location_id=raw["location_id"],
                    time_windows=windows,
                    service_fixed=_int(raw, "service_fixed", where))


def _vehicle(raw: Mapping[str, Any]) -> Vehicle:
    return Vehicle(
        id=raw["id"], capacities=dict(raw.get("capacities", {})),
        shift=_window(raw["shift"], f"vehicle {raw['id']} shift"),
        start_location_id=raw["start_location_id"],
        end_location_id=raw.get("end_location_id"),
        hos_rules=raw.get("hos_rules"))


def _solution(raw: Mapping[str, Any], problem: Problem) -> Solution:
    _require(isinstance(raw, Mapping), "solution must be an object")
    known = {order.id for order in problem.orders}
    try:
        routes = []
        for route in raw.get("routes", ()):
            where = f"route {route.get('vehicle_id')!r}"
            steps = []
            for position, step in enumerate(route.get("steps", ())):
                spot = f"{where} steps[{position}]"
                order_id = step.get("order_id")
                if order_id is not None and order_id not in known:
                    raise VerificationError(
                        f"{spot} names order {order_id!r}, which is not in the "
                        f"problem")
                steps.append(Step(
                    type=step["type"], location_id=step["location_id"],
                    arrival=_int(step, "arrival", spot),
                    start_service=_int(step, "start_service", spot),
                    departure=_int(step, "departure", spot),
                    order_id=order_id,
                    load_after=dict(step.get("load_after", {}))))
            routes.append(Route(vehicle_id=route["vehicle_id"],
                                steps=tuple(steps)))
        return Solution(
            problem_id=raw.get("problem_id", problem.id),
            routes=tuple(routes),
            unassigned=tuple(dict(item) for item in raw.get("unassigned", ())),
            objective_breakdown=dict(raw.get("objective_breakdown", {})),
            status=raw.get("status", "FEASIBLE"))
    except VerificationError:
        raise
    except (KeyError, TypeError, ValidationError) as error:
        raise VerificationError(f"solution is not readable: {error}") from error


def as_json(report: Sequence) -> list:
    """Kept for callers that hold a `Report` rather than a payload."""
    return [{"invariant": v.invariant, "detail": v.detail} for v in report]
