"""Prize-collecting dispatch — §8.2 step 2, T-55, E-55.

§8.2: "Solve each epoch as a prize-collecting VRPTW in which the prize on each
non-must-go request encodes how much we want it dispatched now. The routing
solver then jointly chooses the dispatch set and the routes... This is the
structure that won the competition's dynamic track."

*Jointly* is what separates this from T-54's ICD. ICD decides a dispatch set and
hands it to a router; here the router decides both at once, because whether a
request is worth sending now depends on the route it would join -- which is
exactly what a solver already computes.

The mechanism is entirely T-27's, which is the pleasant part: `_is_required`
already makes an order droppable when it carries a prize above tier 0, and
`PRIZE_COLLECTING` already puts forgone prize and routing cost in one currency.
An epoch is therefore just an instance with the must-go work required and the
rest priced. No new objective and no new solver mode -- the dispatch question
turns out to be a shape the objective already had.

**The constant is the whole policy.** Below the marginal drive the solver
declines everything and the policy is lazy; above it the solver takes everything
and the policy is greedy. Every interesting policy lives in the band between,
and where that band sits depends on the instance's distances rather than on a
number worth hard-coding -- hence `tune`, and hence
`test_the_prize_curve_is_not_flat`, which is the check that the constant is
controlling anything at all.
"""

from __future__ import annotations

from vrp.epochs import Classification, Epoch
from vrp.icd import icd_policy
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.pcdispatch import epoch_problem, pc_policy, tune
from vrp.policies import greedy, lazy
from vrp.replay import dispatchable, generate_days, replay

HOUR = 3600
DAY = TimeWindow(start=0, end=8 * HOUR)
LEG = 900


def problem(stops: int = 8, vans: int = 2) -> Problem:
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    return Problem(
        id="pc",
        locations=tuple(Location(id="D" if i == 0 else f"C{i}",
                                 lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                        for i in range(size)),
        orders=tuple(Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
                           delivery=StopSpec(location_id=f"C{i}",
                                             time_windows=(DAY,),
                                             service_fixed=300))
                     for i in range(1, size)),
        vehicles=tuple(Vehicle(id=f"V{n}", capacities={"kg": 100}, shift=DAY,
                               start_location_id="D", end_location_id="D",
                               cost_per_metre=1)
                       for n in range(1, vans + 1)),
        matrix=TravelMatrix(version="p", durations=grid, distances=grid))


OPEN = ["O1", "O2", "O3", "O4"]
SPLIT = Classification(must_go=("O1",), deferrable=("O2", "O3", "O4"))
WAVE = Epoch(index=1, start=HOUR, end=2 * HOUR)


# --------------------------------------------------------------------------
# The epoch sub-problem
# --------------------------------------------------------------------------

def test_must_go_work_is_priced_as_required():
    """AC-3.1 expressed in the model rather than bolted on after. `_is_required`
    reads prize 0 at tier 0 as "not for sale", so the solver is never offered
    the chance to decline it."""
    from vrp.solve.pyvrp_adapter import _is_required

    sub = epoch_problem(problem(), OPEN, SPLIT, prize=5_000)

    assert _is_required(sub.order("O1"))
    assert not any(_is_required(sub.order(o)) for o in SPLIT.deferrable)

    # And it is tier 0, because §4.1 defines that as "must-serve" and the epoch
    # sub-problem should be a faithful domain object rather than one that
    # happens to behave right. Prize 0 alone would satisfy `_is_required` --
    # perturbation showed the tier doing no work until this line existed.
    assert sub.order("O1").priority_tier == 0
    assert all(sub.order(o).priority_tier != 0 for o in SPLIT.deferrable)


def test_the_sub_problem_holds_only_the_open_work():
    """An epoch is about what is open now. Handing the solver the whole day
    would have it routing work nobody has heard of yet."""
    sub = epoch_problem(problem(), ["O1", "O2"], SPLIT, prize=5_000)

    assert {order.id for order in sub.orders} == {"O1", "O2"}


def test_the_prize_lands_on_the_deferrable_work():
    sub = epoch_problem(problem(), OPEN, SPLIT, prize=5_000)

    assert sub.order("O1").prize == 0
    assert all(sub.order(o).prize == 5_000 for o in SPLIT.deferrable)


# --------------------------------------------------------------------------
# The policy
# --------------------------------------------------------------------------

def test_a_low_prize_dispatches_only_what_must_go():
    """Below the marginal drive, declining is the right answer and the solver
    gives it."""
    instance = problem()
    chosen = pc_policy(instance, prize=500)(OPEN, SPLIT, WAVE)

    assert set(chosen) == {"O1"}, chosen


def test_a_high_prize_dispatches_everything():
    instance = problem()
    chosen = pc_policy(instance, prize=100_000)(OPEN, SPLIT, WAVE)

    assert set(chosen) == set(OPEN), chosen


def test_must_go_work_survives_even_the_lowest_prize():
    instance = problem()

    for prize in (0, 1, 100, 500):
        assert "O1" in pc_policy(instance, prize=prize)(OPEN, SPLIT, WAVE)


def test_a_solver_failure_still_sends_the_must_go_work():
    """AC-3.1 does not get suspended because the adapter had a bad day.

    An untested `except` is a branch that only looks like safety -- perturbation
    showed it returning nothing at all and no test noticing, because the solver
    never fails on a well-formed epoch.
    """
    import vrp.pcdispatch as module

    def explode(*args, **kwargs):
        raise RuntimeError("adapter unavailable")

    original = module.pyvrp_adapter.solve
    module.pyvrp_adapter.solve = explode
    try:
        chosen = pc_policy(problem(), prize=4_000)(OPEN, SPLIT, WAVE)
    finally:
        module.pyvrp_adapter.solve = original

    assert set(chosen) == {"O1"}, chosen


def test_the_same_seed_gives_the_same_dispatch():
    instance = problem()
    left = pc_policy(instance, prize=4_000, seed=3)
    right = pc_policy(instance, prize=4_000, seed=3)

    assert tuple(left(OPEN, SPLIT, WAVE)) == tuple(right(OPEN, SPLIT, WAVE))


def test_an_empty_epoch_dispatches_nothing():
    """Handing an instance with no orders to the adapter is a needless way to
    find out what it does."""
    assert pc_policy(problem(), prize=4_000)([], Classification((), ()),
                                             WAVE) == ()


# --------------------------------------------------------------------------
# The tuned constant
# --------------------------------------------------------------------------

def test_the_prize_curve_is_not_flat():
    """The check that the constant controls anything.

    A flat curve would mean the prize is decorative and the policy is really
    just its solver's default behaviour.
    """
    instance = dispatchable(problem(stops=8), DAY, window=3 * HOUR)
    days = generate_days(instance, count=20, seed=0, horizon=DAY)

    _, curve = tune(instance, days, HOUR,
                    candidates=(500, 2_000, 4_000, 20_000))

    assert len(set(curve.values())) > 1, curve


def test_tune_returns_the_cheapest_candidate():
    instance = dispatchable(problem(stops=8), DAY, window=3 * HOUR)
    days = generate_days(instance, count=20, seed=0, horizon=DAY)

    best, curve = tune(instance, days, HOUR,
                       candidates=(500, 2_000, 4_000, 20_000))

    assert curve[best] == min(curve.values())


def test_the_extremes_of_the_curve_are_the_baselines():
    """Not a coincidence: a prize below the drive makes the solver decline all
    optional work, which is lazy; a prize far above it makes the solver take
    all of it, which is greedy. The tuned constant interpolates between the two
    baselines §8.2 made permanent."""
    instance = dispatchable(problem(stops=8), DAY, window=3 * HOUR)
    days = generate_days(instance, count=20, seed=0, horizon=DAY)

    def total(policy):
        return sum(replay(instance, day, policy, epoch_length=HOUR).cost
                   for day in days)

    assert total(pc_policy(instance, prize=500)) == total(lazy)
    assert total(pc_policy(instance, prize=200_000)) == total(greedy)


# --------------------------------------------------------------------------
# T-55's definition of done
# --------------------------------------------------------------------------

def test_it_is_better_than_icd_on_the_corpus():
    """T-55: "Comparable or better than ICD on at least one instance family".

    Measured over 90 days on the dispatchable corpus:

        greedy                     4,332,600
        lazy                       3,933,000
        icd (8 scenarios, seed 0)  3,925,800   (-0.18% vs lazy)
        prize-collecting @ 4,000   3,666,600   (-6.77% vs lazy)

    Better, and not narrowly: 6.6% under ICD and 15.4% under greedy. The reason
    is structural rather than lucky. ICD samples futures and then asks a
    separate router to carry the set it chose; prize-collecting asks one solver
    both questions at once, so a request is judged against the route it would
    actually join. §8.2 calls this "the structure that won the competition's
    dynamic track", and on this corpus the margin over sampling is visible
    rather than theoretical.
    """
    instance = dispatchable(problem(stops=8), DAY, window=3 * HOUR)
    days = generate_days(instance, count=90, seed=0, horizon=DAY)

    def total(policy):
        return sum(replay(instance, day, policy, epoch_length=HOUR).cost
                   for day in days)

    icd = total(icd_policy(instance, horizon=8 * HOUR, scenarios=8, seed=0))
    pc = total(pc_policy(instance, prize=4_000))

    assert pc < icd, (pc, icd)
    assert pc < total(greedy)
    assert pc < total(lazy)
