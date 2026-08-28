"""Baseline dispatch policies — §8.2 step 1, T-52, E-52.

§8.2 lists three and says why they exist: "*Greedy* -- dispatch everything known
now. *Lazy* -- dispatch only must-go requests. *Random* -- dispatch must-go plus
each other request with probability p. These are the competition-standard
baselines and MUST be retained permanently as the denominator for every policy
claim."

The word doing the work is *denominator*. A dispatch policy that beats nothing
in particular has not been shown to be any good, and §8.2 orders the whole slice
around that: baselines first, then prize-collecting (T-55), then ICD (T-54),
each measured against these. A baseline quietly deleted or tuned later would
invalidate every claim made against it, which is why `BASELINES` is a registry
rather than three loose functions -- T-53's replayer enumerates it, and a
policy that disappears from it is a policy whose comparisons disappear too.

Two things worth stating about these three:

**They are allowed to be bad.** Lazy postpones everything it legally can, which
is terrible consolidation and exactly the point: it is the floor. Greedy is the
ceiling on service and the floor on consolidation. Random spans them.

**None of them can cause a service failure**, because AC-3.1 is enforced in
`decide` rather than trusted to the policy. That is the property T-51 built and
this is the first thing that could have violated it -- so
`test_no_baseline_can_postpone_a_must_go` runs all three against work that must
go now.
"""

from __future__ import annotations

import pytest

from vrp.epochs import Classification, decide, epochs
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.policies import BASELINES, greedy, lazy, random_policy

HOUR = 3600
DAY = TimeWindow(start=0, end=8 * HOUR)
LEG = 1800


def problem(closes: dict[str, int]) -> Problem:
    ids = sorted(closes)
    size = len(ids) + 1
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    return Problem(
        id="policy",
        locations=tuple(Location(id="D" if i == 0 else f"C{i}",
                                 lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                        for i in range(size)),
        orders=tuple(
            Order(id=order_id, kind="JOB", quantities={"kg": 1},
                  delivery=StopSpec(
                      location_id=f"C{n}",
                      time_windows=(TimeWindow(start=0, end=closes[order_id]),),
                      service_fixed=300))
            for n, order_id in enumerate(ids, start=1)),
        vehicles=(Vehicle(id="V1", capacities={"kg": 100}, shift=DAY,
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="p", durations=grid, distances=grid))


OPEN = ["O1", "O2", "O3", "O4"]
SPLIT = Classification(must_go=("O1",), deferrable=("O2", "O3", "O4"))
LATE = {f"O{i}": 8 * HOUR for i in range(1, 5)}


# --------------------------------------------------------------------------
# §8.2's three
# --------------------------------------------------------------------------

def test_greedy_dispatches_everything_known_now():
    assert list(greedy(OPEN, SPLIT)) == OPEN


def test_lazy_dispatches_only_the_must_go_work():
    assert list(lazy(OPEN, SPLIT)) == ["O1"]


def test_lazy_dispatches_nothing_when_nothing_must_go():
    """The floor, and it is meant to look bad: lazy postpones everything it
    legally can. A baseline that behaved sensibly would flatter every policy
    measured against it."""
    nothing_urgent = Classification(must_go=(), deferrable=tuple(OPEN))

    assert list(lazy(OPEN, nothing_urgent)) == []


def test_random_takes_the_must_go_work_and_some_of_the_rest():
    chosen = list(random_policy(probability=500, seed=0)(OPEN, SPLIT))

    assert "O1" in chosen
    assert set(chosen) <= set(OPEN)


def test_random_at_probability_zero_is_lazy():
    """The endpoints are the other two baselines, which is a useful check that
    `p` means what it says rather than merely varying something.

    Sampled over many epochs rather than once. A single draw cannot tell `p = 0`
    from an off-by-one that fires on an exact zero -- that is one epoch in a
    thousand, and perturbation showed a `<=` in place of `<` sailing past a
    one-shot assertion.
    """
    never = random_policy(probability=0, seed=0)

    for _ in range(2_000):
        assert list(never(OPEN, SPLIT)) == list(lazy(OPEN, SPLIT))


def test_random_at_full_probability_is_greedy():
    always = random_policy(probability=1000, seed=0)

    assert sorted(always(OPEN, SPLIT)) == sorted(greedy(OPEN, SPLIT))


def test_random_draws_afresh_each_epoch():
    """§8.2 says "each other request with probability p" -- per epoch, which is
    the only reading under which `p` is a rate rather than a single coin toss.

    A first version seeded the generator inside the policy, so the same open
    set produced the same draw forever. With p = 0.5 and four requests, two of
    them were never dispatched at all until must-go forced them, and had the
    first draw come up empty nothing would ever have been dispatched. A
    baseline that gets stuck is not a denominator.
    """
    open_ids = ["O1", "O2", "O3", "O4"]
    nothing_urgent = Classification(must_go=(), deferrable=tuple(open_ids))
    policy = random_policy(probability=500, seed=3)

    draws = [tuple(policy(open_ids, nothing_urgent)) for _ in range(8)]
    assert len(set(draws)) > 1, draws
    assert set().union(*draws) == set(open_ids), draws


def test_random_is_reproducible_for_a_seed():
    """CON-4. A baseline nobody can reproduce is not a denominator -- every
    comparison against it would be against a different number."""
    left = random_policy(probability=500, seed=7)
    right = random_policy(probability=500, seed=7)

    assert [tuple(left(OPEN, SPLIT)) for _ in range(5)] == \
           [tuple(right(OPEN, SPLIT)) for _ in range(5)], \
        "two policies built from one seed diverged"


def test_a_different_seed_gives_a_different_draw():
    draws = {tuple(random_policy(probability=500, seed=s)(OPEN, SPLIT))
             for s in range(12)}

    assert len(draws) > 1, draws


def test_random_refuses_a_probability_outside_the_scale():
    """Parts per thousand, per CON-4: this project does not accumulate floats,
    and a probability silently clamped is a baseline quietly redefined."""
    for bad in (-1, 1001):
        with pytest.raises(ValueError, match="probability"):
            random_policy(probability=bad, seed=0)


# --------------------------------------------------------------------------
# The registry §8.2 calls permanent
# --------------------------------------------------------------------------

def test_all_three_baselines_are_registered():
    assert set(BASELINES) == {"greedy", "lazy", "random"}


def test_every_registered_baseline_is_callable_as_a_policy():
    for name, policy in BASELINES.items():
        chosen = policy(OPEN, SPLIT)
        assert set(chosen) <= set(OPEN), name


def test_the_registry_is_what_a_replayer_enumerates():
    """§8.2: "MUST be retained permanently as the denominator for every policy
    claim". A registry rather than three loose functions, so T-53's replayer
    iterates one thing and a baseline cannot quietly stop being compared."""
    instance = problem(LATE)

    for name, policy in BASELINES.items():
        decision = decide(instance, OPEN, postponed_to=HOUR, policy=policy)
        assert set(decision.dispatched) | set(decision.postponed) == set(OPEN), \
            name


# --------------------------------------------------------------------------
# AC-3.1 holds for all three
# --------------------------------------------------------------------------

def test_no_baseline_can_postpone_a_must_go():
    """The first thing that could have broken T-51's guarantee.

    Lazy is the dangerous one by construction -- it postpones everything it is
    allowed to -- and random is the one nobody can predict. Neither can cause a
    service failure, because AC-3.1 lives in `decide` rather than in the
    policy.
    """
    urgent = problem({"O1": HOUR // 2, "O2": 8 * HOUR, "O3": 8 * HOUR,
                      "O4": 8 * HOUR})

    for name, policy in BASELINES.items():
        decision = decide(urgent, OPEN, postponed_to=HOUR, policy=policy)
        assert "O1" in decision.dispatched, name
        assert "O1" not in decision.postponed, name


def test_lazy_is_overruled_and_says_so():
    """A policy being overruled is information, not an error. Lazy asks to
    postpone the must-go work every time; the controller reports each override
    so T-53 can tell a policy that is losing money from one that would be
    causing service failures."""
    urgent = problem({"O1": HOUR // 2, "O2": 8 * HOUR, "O3": 8 * HOUR,
                      "O4": 8 * HOUR})

    decision = decide(urgent, OPEN, postponed_to=HOUR, policy=lazy)

    assert decision.forced == ()
    assert decision.dispatched == ("O1",)


def test_greedy_is_never_overruled():
    """It already dispatches everything, so there is nothing left to force."""
    urgent = problem({"O1": HOUR // 2, "O2": 8 * HOUR, "O3": 8 * HOUR,
                      "O4": 8 * HOUR})

    assert decide(urgent, OPEN, postponed_to=HOUR, policy=greedy).forced == ()


# --------------------------------------------------------------------------
# What the denominator is for
# --------------------------------------------------------------------------

def test_the_baselines_separate_on_a_replayed_day():
    """§8.2's purpose in one measurement: three policies, one day, different
    answers. Baselines that agreed would be a denominator of one.
    """
    instance = problem({"O1": 2 * HOUR, "O2": 4 * HOUR, "O3": 6 * HOUR,
                        "O4": 8 * HOUR})
    waves = epochs(DAY, length=HOUR)

    epochs_used: dict[str, int] = {}
    for name, policy in BASELINES.items():
        open_ids, used = list(OPEN), 0
        for wave in waves:
            if not open_ids:
                break
            decision = decide(instance, open_ids, postponed_to=wave.end,
                              policy=policy)
            if decision.dispatched:
                used += 1
            open_ids = list(decision.postponed)
        epochs_used[name] = used

    assert epochs_used["greedy"] == 1, epochs_used
    assert epochs_used["lazy"] > epochs_used["greedy"], epochs_used
