"""The epoch controller and must-go classifier — FR-22, DYN-1, DYN-2, AC-3.1,
§8.1, T-51, E-51.

§8.1 frames the whole slice: "Same-day and on-demand operations are not static
problems solved repeatedly. They are sequential decision problems under
uncertainty... at each epoch the agent observes the requests known so far and
must decide which to **dispatch now** -- committing them to feasible routes --
and which to **postpone** so they can be consolidated with requests that arrive
later. Some requests are **must-go**: postponing them makes their time window
unreachable."

DYN-2 says how the classifier must be built and, unusually, which way it must
be wrong: "determine whether postponement to the next epoch preserves
feasibility under *any* remaining vehicle. Conservative by construction; false
negatives are service failures."

That asymmetry is the whole design. Calling a deferrable order must-go costs a
little consolidation. Calling a must-go order deferrable costs a delivery that
never happens, and the customer finds out before the dispatcher does. So every
uncertain case resolves to must-go, and
`test_an_order_whose_window_is_unclear_is_treated_as_must_go` is that rule.

AC-3.1 states the guarantee: the system "never postpones a `must-go`". It is
enforced in the controller rather than trusted to the policy, because T-52's
policies are deliberately dumb -- one of them is random -- and a service
failure must not be reachable by choosing a bad policy.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from vrp.epochs import classify, decide, epochs, must_go
from vrp.model import (
    UNREACHABLE,
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 1800


def problem(windows: dict[str, TimeWindow] | None = None,
            stops: int = 3) -> Problem:
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    windows = windows or {}
    return Problem(
        id="epoch",
        locations=tuple(Location(id="D" if i == 0 else f"C{i}",
                                 lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                        for i in range(size)),
        orders=tuple(Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
                           delivery=StopSpec(location_id=f"C{i}",
                                             time_windows=(windows.get(
                                                 f"O{i}", DAY),),
                                             service_fixed=60))
                     for i in range(1, size)),
        vehicles=(Vehicle(id="V1", capacities={"kg": 100}, shift=DAY,
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="e", durations=grid, distances=grid))


# --------------------------------------------------------------------------
# DYN-1: the epoch controller
# --------------------------------------------------------------------------

def test_the_horizon_is_partitioned_into_epochs():
    waves = epochs(DAY, length=HOUR)

    assert len(waves) == 12
    assert waves[0].start == 0 and waves[0].end == HOUR
    assert waves[-1].end == 12 * HOUR


def test_epochs_are_contiguous_and_do_not_overlap():
    """A gap loses the requests that arrive in it; an overlap dispatches them
    twice."""
    waves = epochs(DAY, length=HOUR)

    for earlier, later in pairwise(waves):
        assert earlier.end == later.start


def test_a_horizon_that_does_not_divide_evenly_keeps_a_short_last_epoch():
    """Rounding the tail away would silently drop the last minutes of the day,
    and the requests in them."""
    waves = epochs(TimeWindow(start=0, end=5_000), length=HOUR)

    assert len(waves) == 2
    assert waves[-1].start == HOUR and waves[-1].end == 5_000


def test_an_epoch_length_must_be_positive():
    with pytest.raises(ValueError, match="length"):
        epochs(DAY, length=0)


# --------------------------------------------------------------------------
# DYN-2: the must-go classifier
# --------------------------------------------------------------------------

def test_an_order_still_reachable_next_epoch_is_deferrable():
    instance = problem()

    assert not must_go(instance, instance.order("O1"), postponed_to=HOUR)


def test_an_order_whose_window_closes_before_the_next_epoch_is_must_go():
    """The plain case: postponing makes the window unreachable."""
    closes_early = {"O1": TimeWindow(start=0, end=HOUR // 2)}
    instance = problem(windows=closes_early)

    assert must_go(instance, instance.order("O1"), postponed_to=HOUR)


def test_travel_time_counts_against_the_window():
    """"Postponement makes its time window unreachable" is about arrival, not
    about the window's clock. A stop 30 minutes out with a window closing 20
    minutes into the next epoch is already gone."""
    instance = problem(windows={"O1": TimeWindow(start=0,
                                                 end=HOUR + LEG // 2)})

    assert must_go(instance, instance.order("O1"), postponed_to=HOUR)


def test_an_order_with_no_hard_window_is_deferrable():
    """A soft window is priced, not a wall. Treating lateness as impossibility
    would make every soft-windowed order must-go and postpone nothing."""
    late_ok = TimeWindow(start=0, end=HOUR // 2, hardness="SOFT",
                         lateness_cost_per_sec=1)
    instance = problem(windows={"O1": late_ok})

    assert not must_go(instance, instance.order("O1"), postponed_to=HOUR)


def test_reachability_is_judged_against_every_vehicle_not_just_the_first():
    """DYN-2: "under *any* remaining vehicle". One van in the wrong depot does
    not make an order must-go while another could still serve it."""
    from dataclasses import replace

    instance = problem(windows={"O1": TimeWindow(start=0, end=2 * HOUR)})
    far = Vehicle(id="FAR", capacities={"kg": 100},
                  shift=TimeWindow(start=6 * HOUR, end=12 * HOUR),
                  start_location_id="D", end_location_id="D")
    near = instance.vehicles[0]

    only_far = replace(instance, vehicles=(far,))
    assert must_go(only_far, only_far.order("O1"), postponed_to=HOUR)

    both = replace(instance, vehicles=(far, near))
    assert not must_go(both, both.order("O1"), postponed_to=HOUR)


def test_an_order_no_vehicle_can_serve_at_all_is_must_go():
    """Conservative by construction. An order already infeasible is not made
    safe by postponing it, and calling it deferrable would quietly let the
    dispatch policy drop it forever."""
    from dataclasses import replace

    instance = problem(windows={"O1": TimeWindow(start=0, end=1)})
    assert must_go(instance, instance.order("O1"), postponed_to=HOUR)

    fleetless = replace(instance, vehicles=())
    assert must_go(fleetless, fleetless.order("O1"), postponed_to=HOUR)


def test_an_order_whose_window_is_unclear_is_treated_as_must_go():
    """DYN-2's asymmetry, stated directly: "Conservative by construction; false
    negatives are service failures".

    An unreachable stop -- no matrix entry -- is not evidence that postponing is
    safe. It is an absence of evidence, and the two must not be confused.
    """
    instance = problem()
    unreachable = TravelMatrix(
        version="e",
        durations=((0, UNREACHABLE, LEG, LEG),
                   (UNREACHABLE, 0, LEG, LEG),
                   (LEG, LEG, 0, LEG), (LEG, LEG, LEG, 0)),
        distances=instance.matrix.distances)
    from dataclasses import replace
    blind = replace(instance, matrix=unreachable)

    assert must_go(blind, blind.order("O1"), postponed_to=HOUR)


def test_classify_splits_the_open_work():
    instance = problem(windows={"O1": TimeWindow(start=0, end=HOUR // 2)})

    split = classify(instance, ["O1", "O2", "O3"], postponed_to=HOUR)

    assert split.must_go == ("O1",)
    assert split.deferrable == ("O2", "O3")


# --------------------------------------------------------------------------
# AC-3.1: the guarantee
# --------------------------------------------------------------------------

def test_the_controller_dispatches_what_the_policy_asks_for():
    instance = problem()

    decision = decide(instance, ["O1", "O2", "O3"], postponed_to=HOUR,
                      policy=lambda ids, split: ("O1",))

    assert decision.dispatched == ("O1",)
    assert decision.postponed == ("O2", "O3")


def test_a_policy_that_tries_to_postpone_a_must_go_is_overruled():
    """AC-3.1: the system "never postpones a `must-go`".

    Enforced here rather than trusted to the policy. T-52's baselines are
    deliberately dumb -- one of them is random -- and a service failure must
    not be reachable by choosing a bad policy.
    """
    instance = problem(windows={"O1": TimeWindow(start=0, end=HOUR // 2)})

    decision = decide(instance, ["O1", "O2"], postponed_to=HOUR,
                      policy=lambda ids, split: ("O2",))

    assert "O1" in decision.dispatched
    assert "O1" not in decision.postponed


def test_the_override_is_reported_rather_than_silent():
    """A policy being overruled is information: it is the difference between a
    policy that is losing money and one that is causing service failures, and
    T-53's replayer needs to tell them apart."""
    instance = problem(windows={"O1": TimeWindow(start=0, end=HOUR // 2)})

    decision = decide(instance, ["O1", "O2"], postponed_to=HOUR,
                      policy=lambda ids, split: ("O2",))

    assert decision.forced == ("O1",)


def test_nothing_is_forced_when_the_policy_behaves():
    instance = problem(windows={"O1": TimeWindow(start=0, end=HOUR // 2)})

    decision = decide(instance, ["O1", "O2"], postponed_to=HOUR,
                      policy=lambda ids, split: ("O1", "O2"))

    assert decision.forced == ()


def test_every_open_order_is_either_dispatched_or_postponed():
    """FR-22 is a partition. An order in neither set has been lost, and an
    order in both has been dispatched twice."""
    instance = problem()

    decision = decide(instance, ["O1", "O2", "O3"], postponed_to=HOUR,
                      policy=lambda ids, split: ("O2",))

    assert set(decision.dispatched) | set(decision.postponed) == {"O1", "O2",
                                                                  "O3"}
    assert not set(decision.dispatched) & set(decision.postponed)


def test_a_policy_naming_an_order_that_is_not_open_is_refused():
    """Silently dropping it would make the partition above quietly false."""
    instance = problem()

    with pytest.raises(ValueError, match="not open"):
        decide(instance, ["O1"], postponed_to=HOUR,
               policy=lambda ids, split: ("O1", "O99"))


# --------------------------------------------------------------------------
# T-51's definition of done
# --------------------------------------------------------------------------

def test_no_must_go_is_ever_postponed_across_a_replayed_day():
    """T-51: "Zero must-go postponements across the replay corpus".

    A day of epochs, with a policy that postpones everything it is allowed to
    -- the worst case for this guarantee, and the one a lazy dispatch policy
    actually is. Every epoch's classification is recomputed against that
    epoch's own horizon, because an order deferrable at 08:00 is must-go at
    15:00 and a controller that classified once would postpone it forever.
    """
    windows = {f"O{i}": TimeWindow(start=0, end=(i + 1) * 2 * HOUR)
               for i in range(1, 4)}
    instance = problem(windows=windows)
    open_ids = ["O1", "O2", "O3"]
    dispatched: list[str] = []

    for wave in epochs(DAY, length=HOUR):
        if not open_ids:
            break
        decision = decide(instance, open_ids, postponed_to=wave.end,
                          policy=lambda ids, split: ())
        # The guarantee: nothing postponed here may be must-go.
        for order_id in decision.postponed:
            assert not must_go(instance, instance.order(order_id),
                               postponed_to=wave.end), (
                f"{order_id} was postponed at epoch {wave.index} despite being "
                f"must-go")
        dispatched.extend(decision.dispatched)
        open_ids = list(decision.postponed)

    assert sorted(dispatched) == ["O1", "O2", "O3"], dispatched
