"""The run record NFR-06 asks for — NFR-06, CON-4, T-87.

`NFR-06`: "Every run emits: objective trajectory over time, incumbent
timestamps, constraint-violation counts, matrix cache hit rate, seed, solver
version, deterministic iteration count."

Seven things. The solver record carried three — seed, solver version, iteration
count. (`T-87`'s row said four; `matrix_version` is in the record and is not one
of NFR-06's seven.)

**The whole risk here is a decorative record.** Every one of these is easy to
emit and hard to make true: a trajectory that is always empty, a hit rate that
is always zero, a violation count nobody increments. So each is tested by
running the *same* thing twice with one dimension changed and asserting the
field moved — a record whose fields do not move is a record of nothing.
"""

from __future__ import annotations

import dataclasses

import pytest

from vrp.bench import fixtures
from vrp.lns import lns_search
from vrp.matrix import PairCache
from vrp.model import Route, Solution, Step
from vrp.observe import NFR_06_FIELDS, Recorder, RunRecord
from vrp.verify import verify


def a_recorder(seed: int = 7) -> Recorder:
    return Recorder(solver="lns:test", seed=seed)


# --------------------------------------------------------------------------
# All seven, from one place
# --------------------------------------------------------------------------

def test_the_record_carries_every_field_the_requirement_names():
    """Mechanical: NFR-06's list, against what the record exposes.

    Named rather than counted, so adding a field to the record cannot make the
    count right while the requirement stays unmet.
    """
    record = a_recorder().finish(iterations=10, violations={}, cache=PairCache())

    missing = [name for name in NFR_06_FIELDS if not hasattr(record, name)]
    assert not missing, f"NFR-06 names these and the record has no such field: {missing}"
    assert len(NFR_06_FIELDS) == 7, (
        "NFR-06 names seven things; the constant has drifted from the "
        "requirement it is supposed to track")


def test_the_record_survives_being_written_down():
    """A run record nobody can store is one nobody can consult. The same
    lesson `T-89` learned about the problem codec, one layer up."""
    recorder = a_recorder()
    recorder.improved(iteration=3, objective=900)
    record = recorder.finish(iterations=10, violations={"INV-4": 2},
                             cache=PairCache())

    assert RunRecord.from_dict(record.to_dict()) == record


# --------------------------------------------------------------------------
# Each field moves when the thing it measures moves
# --------------------------------------------------------------------------

def test_the_trajectory_records_each_improvement_and_only_improvements():
    recorder = a_recorder()
    for iteration, objective in ((0, 1000), (5, 900), (12, 850)):
        recorder.improved(iteration, objective)
    record = recorder.finish(iterations=20, violations={}, cache=PairCache())

    assert [point.objective for point in record.trajectory] == [1000, 900, 850]
    assert [point.iteration for point in record.trajectory] == [0, 5, 12]


def test_a_run_that_improves_more_has_a_longer_trajectory():
    """The anti-decoration check: a trajectory that never moves would pass any
    test that only asked whether the field exists."""
    dull, lively = a_recorder(), a_recorder()
    dull.improved(0, 1000)
    for iteration, objective in ((0, 1000), (4, 950), (8, 900), (11, 880)):
        lively.improved(iteration, objective)

    assert len(lively.finish(20, {}, PairCache()).trajectory) > \
           len(dull.finish(20, {}, PairCache()).trajectory)


def test_incumbent_timestamps_advance_with_the_run():
    recorder = a_recorder()
    recorder.improved(0, 1000)
    recorder.improved(9, 900)
    record = recorder.finish(iterations=20, violations={}, cache=PairCache())

    stamps = [point.elapsed_ns for point in record.trajectory]
    assert stamps == sorted(stamps), "time went backwards"
    assert stamps[-1] > 0, "no time passed at all, so nothing was timed"


def test_violation_counts_move_when_a_plan_violates_something():
    """Counted from the independent verifier rather than from the solver's own
    opinion, which is CON-1's whole point."""
    problem = fixtures.uc070_single_order_single_vehicle()
    order = problem.orders[0].id
    site = (problem.orders[0].delivery or problem.orders[0].pickup).location_id
    late = Solution(
        problem_id=problem.id, status="FEASIBLE",
        routes=(Route(vehicle_id=problem.vehicles[0].id, steps=(
            Step(type="START", location_id=problem.vehicles[0].start_location_id,
                 arrival=0, start_service=0, departure=0),
            Step(type="DELIVERY", location_id=site, order_id=order,
                 arrival=10 ** 7, start_service=10 ** 7, departure=10 ** 7),
            Step(type="END", location_id=problem.vehicles[0].start_location_id,
                 arrival=10 ** 7, start_service=10 ** 7, departure=10 ** 7))),),
        unassigned=())

    counted = Recorder.violations_of(verify(problem, late))
    assert counted, "this plan violates nothing, so the counter cannot be shown"
    assert sum(counted.values()) == len(verify(problem, late).violations)

    clean = a_recorder().finish(10, {}, PairCache())
    dirty = a_recorder().finish(10, counted, PairCache())
    assert dirty.total_violations > clean.total_violations == 0


def test_the_cache_hit_rate_moves_between_a_cold_and_a_warm_cache():
    cold, warm = PairCache(), PairCache()
    pair = ((9.9, -84.0), (9.91, -84.01), "driving")
    warm.put(*pair[:2], pair[2], 120, 1000)
    for cache in (cold, warm):
        cache.get(*pair[:2], pair[2])

    cold_record = a_recorder().finish(10, {}, cold)
    warm_record = a_recorder().finish(10, {}, warm)

    assert cold_record.cache_hit_rate_ppt == 0
    assert warm_record.cache_hit_rate_ppt == 1000
    assert warm_record.cache_lookups == cold_record.cache_lookups == 1


def test_the_iteration_count_is_the_one_actually_achieved():
    """CON-4: "MUST record the deterministic iteration count actually achieved
    so that any run can be replayed" — the budget is not the record."""
    stopped_early = a_recorder().finish(iterations=37, violations={},
                                        cache=PairCache())
    assert stopped_early.iterations == 37


def test_the_seed_and_the_solver_version_are_what_was_run():
    record = Recorder(solver="pyvrp:0.9.1", seed=99).finish(1, {}, PairCache())
    assert record.seed == 99 and record.solver == "pyvrp:0.9.1"


# --------------------------------------------------------------------------
# CON-4: which half of a timestamp replays
# --------------------------------------------------------------------------

def test_the_iteration_trajectory_replays_and_the_wall_clock_does_not():
    """NFR-06 wants "over time" and CON-4 wants replayability, and those are
    two different clocks.

    The iteration index is what a replay reproduces; the elapsed nanoseconds
    are what an operator reads and will never match twice. Recording only the
    wall clock would make every run unreproducible for a reason that has
    nothing to do with the plan; recording only iterations would lose the
    thing NFR-06 asked for. Both, and the record says which is which.
    """
    def run() -> RunRecord:
        recorder = a_recorder(seed=11)
        for iteration, objective in ((0, 1000), (7, 940), (15, 900)):
            recorder.improved(iteration, objective)
        return recorder.finish(20, {}, PairCache())

    first, second = run(), run()
    assert first.replayable() == second.replayable(), (
        "the same seed produced different replayable content")
    assert first != second, (
        "the two runs are identical including the wall clock, so this test "
        "cannot show that the timestamps are excluded from what replays")


def test_two_records_differing_only_in_timing_replay_the_same():
    slow = a_recorder(seed=3)
    slow.improved(0, 1000)
    quick = a_recorder(seed=3)
    quick.improved(0, 1000)

    a = slow.finish(5, {}, PairCache())
    b = dataclasses.replace(
        quick.finish(5, {}, PairCache()),
        trajectory=tuple(dataclasses.replace(point, elapsed_ns=point.elapsed_ns + 10 ** 9)
                         for point in a.trajectory))
    assert a.replayable() == b.replayable()
    assert a.trajectory != b.trajectory


@pytest.mark.parametrize("objective", [900, 950])
def test_an_improvement_that_is_not_one_is_refused(objective):
    """Equal counts as not-an-improvement.

    A caller that logged every iteration would otherwise fill the trajectory
    with the same number and make it a loop counter, which reads exactly like a
    search that kept finding better plans.
    """
    recorder = a_recorder()
    recorder.improved(0, 900)
    with pytest.raises(ValueError, match="not better"):
        recorder.improved(5, objective)


# --------------------------------------------------------------------------
# A real search, producing a real record
# --------------------------------------------------------------------------

def a_plan_worth_improving():
    """A round bad enough that the search has somewhere to go.

    Deliberately not `uc074`: its matrix is uniform, so every ordering costs
    the same and the search improves nothing. A trajectory test on that
    instance passes only because there is nothing to record.
    """
    problem = fixtures.uc075_delivery_station_sequencing()
    index = {location.id: location.matrix_index for location in problem.locations}
    stops = [index[(order.delivery or order.pickup).location_id]
             for order in problem.orders]
    return problem.matrix, [stops]


def test_the_search_fills_the_trajectory_it_is_given():
    """The record has to come from a run, not from a test calling `improved`.

    A module that assembles a record nobody produces is the decorative version
    of NFR-06, and every test above it would still pass.
    """
    matrix, plan = a_plan_worth_improving()
    recorder = Recorder(solver="lns", seed=4)
    lns_search(matrix, plan, iterations=200, seed=4, recorder=recorder)
    record = recorder.finish(iterations=200, cache=PairCache())

    assert len(record.trajectory) > 1, (
        "the search improved at most once, so this instance cannot show a "
        "trajectory being filled")
    assert record.trajectory[-1].objective < record.trajectory[0].objective
    assert record.trajectory[-1].iteration <= 200


def test_two_runs_of_the_same_seed_have_the_same_replayable_record():
    """CON-4, end to end: the plan and the trajectory that produced it."""
    matrix, plan = a_plan_worth_improving()

    def run():
        recorder = Recorder(solver="lns", seed=9)
        best = lns_search(matrix, plan, iterations=150, seed=9,
                          recorder=recorder)
        return best, recorder.finish(iterations=150, cache=PairCache())

    (first_plan, first), (second_plan, second) = run(), run()
    assert first_plan == second_plan
    assert first.replayable() == second.replayable()


def test_a_different_seed_moves_the_trajectory():
    """Otherwise the seed is in the record and does nothing."""
    matrix, plan = a_plan_worth_improving()

    def run(seed: int):
        recorder = Recorder(solver="lns", seed=seed)
        lns_search(matrix, plan, iterations=150, seed=seed, recorder=recorder)
        return recorder.finish(iterations=150, cache=PairCache())

    assert run(1).replayable() != run(2).replayable()


def test_a_search_that_is_not_watched_behaves_identically():
    """Observability must not change the plan."""
    matrix, plan = a_plan_worth_improving()
    watched = lns_search(matrix, plan, iterations=120, seed=5,
                         recorder=Recorder(solver="lns", seed=5))
    unwatched = lns_search(matrix, plan, iterations=120, seed=5)

    assert watched == unwatched
