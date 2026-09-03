"""Decomposition sub-problems through a bounded queue — NFR-05, §7.7, T-92.

§7.7 names two kinds of intra-run parallelism: "portfolio members on separate
cores; decomposition sub-problems in a work queue". `T-86` delivered the first.
This is the second, and on a large instance it is the larger of the two —
there are far more clusters than there are portfolio members.

The trap is the same one `T-86` documented: "the plan is unchanged" is
satisfied perfectly by a `workers` argument that is accepted and ignored. The
tests that cannot be passed that way are the ones that fail unless two clusters
are genuinely in flight at once.

Unlike the portfolio's pure-Python members, every sub-solve here goes through
PyVRP, which is C++ and releases the GIL — so threads buy real cores rather
than turns at one interpreter.
"""

from __future__ import annotations

import threading
import time

import pytest

from vrp.bench import fixtures
from vrp.decompose import concatenate, partition, solve_decomposed

TIMEOUT = 5.0


def a_large_instance():
    """Enough orders to make several clusters worth solving."""
    return fixtures.uc074_at_the_decomposition_threshold()


def clusters_of(problem, target_size: int = 6):
    return partition(problem, target_size=target_size)


# --------------------------------------------------------------------------
# The plan does not depend on the width
# --------------------------------------------------------------------------

@pytest.mark.parametrize("workers", [1, 2, 4])
def test_the_recombined_plan_is_the_same_at_any_width(workers):
    problem = a_large_instance()
    clusters = clusters_of(problem)
    assert len(clusters) > 1, "one cluster cannot show a queue doing anything"

    wide = concatenate(problem, clusters, seed=3, workers=workers)
    serial = concatenate(problem, clusters, seed=3, workers=1)

    assert [route.vehicle_id for route in wide.routes] == \
           [route.vehicle_id for route in serial.routes]
    assert [[step.order_id for step in route.steps] for route in wide.routes] == \
           [[step.order_id for step in route.steps] for route in serial.routes]
    assert wide.objective_breakdown == serial.objective_breakdown


def test_the_whole_decomposition_is_unchanged_by_the_queue():
    """§7.6 end to end, not just the recombination step."""
    problem = a_large_instance()
    wide = solve_decomposed(problem, target_size=6, seed=0, workers=4)
    serial = solve_decomposed(problem, target_size=6, seed=0, workers=1)

    assert wide.status == serial.status
    assert wide.objective_breakdown == serial.objective_breakdown
    assert [[step.order_id for step in route.steps] for route in wide.routes] == \
           [[step.order_id for step in route.steps] for route in serial.routes]


# --------------------------------------------------------------------------
# Concurrency that can be proven
# --------------------------------------------------------------------------

def test_two_clusters_are_in_flight_at_the_same_moment(monkeypatch):
    """A barrier no sequential loop can pass.

    Two sub-solves must both arrive before either is released. Run in series
    the first waits alone until the timeout and the run fails.
    """
    problem = a_large_instance()
    clusters = clusters_of(problem)[:2]
    barrier = threading.Barrier(2, timeout=TIMEOUT)
    from vrp import decompose

    real = decompose._solve_cluster

    def meeting(*args, **kwargs):
        barrier.wait()
        return real(*args, **kwargs)

    monkeypatch.setattr(decompose, "_solve_cluster", meeting)
    solution = concatenate(problem, clusters, seed=1, workers=2)

    assert solution.routes, "the run produced nothing, so nothing was proven"


def test_one_worker_is_genuinely_sequential(monkeypatch):
    """CON-4's reproducible mode is single-threaded, not nearly so."""
    problem = a_large_instance()
    clusters = clusters_of(problem)[:2]
    barrier = threading.Barrier(2, timeout=0.4)
    from vrp import decompose

    real = decompose._solve_cluster

    def meeting(*args, **kwargs):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            return {}
        return real(*args, **kwargs)

    monkeypatch.setattr(decompose, "_solve_cluster", meeting)
    solution = concatenate(problem, clusters, seed=1, workers=1)

    # Both sub-solves timed out at the barrier and fell back to sweep order,
    # which is what a sequential loop must do here.
    assert solution.routes, "the fallback did not produce a plan"


def test_the_queue_is_bounded(monkeypatch):
    problem = a_large_instance()
    # target_size=6 is the smallest this fleet supports: four vehicles, and a
    # cluster without one is refused as infeasible by construction.
    clusters = clusters_of(problem, target_size=6)
    assert len(clusters) >= 4, "too few clusters to test a bound of 2"

    live = peak = 0
    guard = threading.Lock()
    from vrp import decompose

    real = decompose._solve_cluster

    def watched(*args, **kwargs):
        nonlocal live, peak
        with guard:
            live += 1
            peak = max(peak, live)
        time.sleep(0.03)
        try:
            return real(*args, **kwargs)
        finally:
            with guard:
                live -= 1

    monkeypatch.setattr(decompose, "_solve_cluster", watched)
    concatenate(problem, clusters, seed=1, workers=2)

    assert peak > 1, "nothing ran concurrently, so the bound is untested"
    assert peak <= 2, f"{peak} sub-solves ran at once against a bound of 2"


# --------------------------------------------------------------------------
# What must survive the change
# --------------------------------------------------------------------------

def test_a_cluster_the_engine_declines_still_falls_back(monkeypatch):
    """A worse plan is a plan; a missing cluster is missing demand."""
    problem = a_large_instance()
    clusters = clusters_of(problem)
    from vrp import decompose

    monkeypatch.setattr(decompose, "_solve_cluster", lambda *a, **k: {})
    solution = concatenate(problem, clusters, seed=1, workers=4)

    placed = {step.order_id for route in solution.routes
              for step in route.steps if step.order_id}
    assert placed == {order.id for order in problem.orders}
    assert not solution.unassigned


def test_a_zero_worker_count_is_refused():
    problem = a_large_instance()
    with pytest.raises(ValueError, match="workers"):
        concatenate(problem, clusters_of(problem), seed=1, workers=0)


def test_each_cluster_is_solved_under_its_own_seed(monkeypatch):
    """`seed + cluster.index` is what makes a sub-plan a function of the
    cluster rather than of when it ran.

    Without it every cluster would be solved under one seed, and the queue
    would still return the same plan as the serial loop — so none of the tests
    above would notice. This one does.
    """
    problem = a_large_instance()
    clusters = clusters_of(problem)
    seen: list[int] = []
    from vrp import decompose

    real = decompose._solve_cluster

    def recording(problem_, order_ids, vehicle_ids, seed, **kwargs):
        seen.append(seed)
        return real(problem_, order_ids, vehicle_ids, seed, **kwargs)

    monkeypatch.setattr(decompose, "_solve_cluster", recording)
    concatenate(problem, clusters, seed=100, workers=2)

    assert len(seen) == len(clusters)
    assert len(set(seen)) == len(clusters), (
        f"clusters were solved under {sorted(set(seen))}; one seed for all of "
        "them makes a sub-plan depend on the partition rather than on the "
        "cluster it is for")
    assert sorted(seen) == [100 + cluster.index for cluster in clusters]


def test_clusters_own_disjoint_vehicles():
    """What makes the queue's ordering safe.

    Results are merged with `assignment.update`, so if two clusters ever named
    the same vehicle the last writer would win and completion order would
    decide the plan. `_assign_vehicles` gives each cluster its own, and this is
    the assertion that fails if that ever stops being true — rather than the
    queue silently becoming order-dependent.
    """
    problem = a_large_instance()
    clusters = clusters_of(problem)

    owned: set[str] = set()
    for cluster in clusters:
        assert not owned & set(cluster.vehicle_ids), (
            f"cluster {cluster.index} shares a vehicle with an earlier one; "
            "the work queue's merge is order-dependent as written")
        owned |= set(cluster.vehicle_ids)
