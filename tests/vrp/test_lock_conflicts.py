"""Minimal conflicting lock sets — §6.6, FR-21, CON-7, T-29, E-29.

§6.6: "If locks make the instance infeasible, the system MUST return
`INFEASIBLE` with the minimal conflicting lock set (an IIS-style diagnosis),
never silently drop a lock."

CON-7 explains why that wording is strong: "Human override is a first-class
input, not a failure... The system MUST NOT silently discard operator intent."
A dispatcher who pins a load to a van and gets back a plan using a different van
has been overruled without being told. A dispatcher who gets back "infeasible"
has been told nothing useful — they have twelve locks and no idea which two
disagree.

Minimal means *irreducible*: every lock in the reported set is load-bearing, so
removing any one of them makes the instance feasible again. Returning all
twelve locks would be true and useless; returning a lock that is not part of the
conflict sends someone to undo a decision that was fine.

The two properties are tested separately, because an implementation can easily
get one and not the other. A conflict set that is *sufficient* but not minimal
passes any test that only checks "removing these fixes it".
"""

from __future__ import annotations

from vrp.locks import is_feasible_under_locks, minimal_conflict
from vrp.model import (
    Location,
    Lock,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)

DAY = TimeWindow(start=0, end=12 * 3600)


def instance(locks: tuple[Lock, ...], stops: int = 2, vans: int = 2,
             capacity: int = 100, weights: tuple[int, ...] = ()) -> Problem:
    size = stops + 1
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 1000, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    grid = tuple(tuple(abs(i - j) * 600 for j in range(size)) for i in range(size))
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
    return Problem(id="lc", locations=locations, orders=orders, vehicles=fleet,
                   matrix=TravelMatrix(version="lc", durations=grid,
                                       distances=grid), locks=locks)


def test_a_consistent_lock_set_has_no_conflict():
    """The control. A diagnosis that finds conflicts everywhere is no more
    useful than one that finds none."""
    problem = instance((Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1",
                             vehicle_id="V1"),))
    assert is_feasible_under_locks(problem)
    assert minimal_conflict(problem) == ()


def test_pinning_to_a_vehicle_that_is_forbidden_to_deploy_conflicts():
    """The simplest irreducible pair: the order must go on V2, and V2 must not
    go out. Neither lock is wrong on its own."""
    locks = (Lock(kind="FORBID_DEPLOY", vehicle_id="V2"),
             Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1", vehicle_id="V2"))
    problem = instance(locks)

    assert not is_feasible_under_locks(problem)
    conflict = minimal_conflict(problem)
    assert set(conflict) == set(locks)


def test_pinning_and_forbidding_the_same_pairing_conflicts():
    locks = (Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1", vehicle_id="V1"),
             Lock(kind="FORBID_ORDER_ON_VEHICLE", order_id="O1",
                  vehicle_id="V1"))
    problem = instance(locks)

    assert set(minimal_conflict(problem)) == set(locks)


def test_the_reported_set_is_irreducible():
    """Minimality, tested directly rather than assumed.

    Ten innocent locks alongside one conflicting pair. A diagnosis that
    returned everything would be true and useless -- the dispatcher still has
    to find the two that matter. So every lock reported must be load-bearing:
    drop any one and the instance becomes feasible.
    """
    conflicting = (Lock(kind="FORBID_DEPLOY", vehicle_id="V2"),
                   Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1",
                        vehicle_id="V2"))
    innocent = tuple(
        Lock(kind="FORBID_ORDER_ON_VEHICLE", order_id=f"O{i}", vehicle_id="V2")
        for i in range(2, 12))
    problem = instance((*innocent, *conflicting), stops=11, vans=2)

    conflict = minimal_conflict(problem)
    assert set(conflict) == set(conflicting), (
        f"expected the two conflicting locks, got {len(conflict)}")

    _assert_irreducible(problem, conflict)


def test_a_three_lock_conflict_is_found_whole():
    """Conflicts are not always pairs. Two vehicles both forbidden, and an
    order pinned to a depot only they serve."""
    locks = (Lock(kind="FORBID_DEPLOY", vehicle_id="V1"),
             Lock(kind="FORBID_DEPLOY", vehicle_id="V2"),
             Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1", vehicle_id="V1"))
    problem = instance(locks)

    conflict = minimal_conflict(problem)
    assert not is_feasible_under_locks(problem)
    assert len(conflict) >= 2
    _assert_irreducible(problem, conflict)


def test_an_instance_infeasible_without_any_lock_reports_no_lock_conflict():
    """The distinction that keeps the diagnosis honest.

    An order too heavy for any van is infeasible whatever the operator did, and
    blaming their locks would send them to unpick decisions that were never the
    problem. §6.5's pre-flight codes own that case.
    """
    problem = instance((Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1",
                             vehicle_id="V1"),),
                       capacity=10, weights=(500, 1))

    assert not is_feasible_under_locks(problem)
    assert minimal_conflict(problem) == (), \
        "the locks were blamed for an infeasibility they did not cause"


def test_a_pin_that_only_conflicts_because_of_capacity_is_reported():
    """The other side of the same line: here the order *is* servable, but only
    by the van the operator ruled out. The lock is genuinely the cause."""
    locks = (Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1", vehicle_id="V1"),)
    problem = instance(locks, capacity=10, weights=(1, 1))
    problem = _with_vehicles(problem, small="V1", big="V2", heavy=500)

    assert not is_feasible_under_locks(problem)
    assert set(minimal_conflict(problem)) == set(locks)


def _assert_irreducible(problem: Problem, conflict: tuple[Lock, ...]) -> None:
    """An IIS is infeasible, and every *proper subset of it* is feasible.

    The subsets are taken from the conflict set, not from the original problem
    minus one lock -- which is what an earlier draft of these tests did, and it
    is a different claim. Dropping one member of the conflict still leaves all
    the *other* locks in place, including ones the filter discarded precisely
    because they were redundant, so the instance can stay infeasible for a
    reason the conflict set never claimed to be about.
    """
    assert not is_feasible_under_locks(_with_locks(problem, conflict)), \
        "the reported set is not itself a conflict"
    for dropped in conflict:
        subset = tuple(lock for lock in conflict if lock is not dropped)
        assert is_feasible_under_locks(_with_locks(problem, subset)), (
            f"{dropped.kind} was reported but the rest of the set conflicts "
            f"without it, so the set is not irreducible")


def _with_locks(problem: Problem, locks: tuple[Lock, ...]) -> Problem:
    return Problem(id=problem.id, locations=problem.locations,
                   orders=problem.orders, vehicles=problem.vehicles,
                   matrix=problem.matrix, locks=locks)


def _with_vehicles(problem: Problem, small: str, big: str, heavy: int) -> Problem:
    """One order too heavy for `small`, and a `big` van that could take it."""
    orders = (Order(id="O1", kind="JOB", quantities={"kg": heavy},
                    delivery=problem.orders[0].delivery),
              *problem.orders[1:])
    vehicles = tuple(
        Vehicle(id=v.id, capacities={"kg": 10 if v.id == small else 1_000},
                shift=v.shift, start_location_id=v.start_location_id,
                end_location_id=v.end_location_id)
        for v in problem.vehicles)
    return Problem(id=problem.id, locations=problem.locations, orders=orders,
                   vehicles=vehicles, matrix=problem.matrix, locks=problem.locks)
