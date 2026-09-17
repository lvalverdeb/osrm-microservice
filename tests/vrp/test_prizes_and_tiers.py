"""Optional orders and priority tiers — FR-12, FR-13, T-27, E-27.

FR-12: "Support optional orders with prizes so the solver may decline low-value
work when capacity is scarce."

FR-13: "Support priority tiers with lexicographic protection: a higher tier is
never sacrificed to improve a lower tier."

The two interact, and the interaction is where this goes wrong. Optionality has
been expressed as `required = (prize == 0)` since E-12 — an order carrying a
prize is one the solver may decline. But §4.1 defines `priority_tier` with "0 =
must-serve", and nothing consulted it. A priority-0 order that happened to carry
a prize was therefore droppable, which is precisely FR-13's prohibition and
contradicts what E-13's own objective tests claim ("a priority-0 order is a
promise, not a bid").

"Lexicographic" is the load-bearing word in FR-13 and the easiest thing to fake.
A weighted objective with a large enough tier multiplier passes almost every
test — until someone attaches a big enough prize to a low-tier order and the
weighting inverts. `test_no_prize_is_large_enough_to_invert_the_tiers` is that
case, at magnitudes chosen to break a weighting rather than to look plausible.
"""

from __future__ import annotations

import pytest

from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
    precedence,
)
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

# `precedence`'s second element for an ordinary commercial order.
COMMERCIAL = 2

DAY = TimeWindow(start=0, end=12 * 3600)


def instance(orders: tuple[Order, ...], capacity: int, stops: int) -> Problem:
    size = stops + 1
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 1000, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    grid = tuple(tuple(abs(i - j) * 600 for j in range(size)) for i in range(size))
    return Problem(
        id="prz", locations=locations, orders=orders,
        vehicles=(Vehicle(id="V1", capacities={"kg": capacity}, shift=DAY,
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="p", durations=grid, distances=grid))


def an_order(order_id: str, stop: str, kg: int, **kwargs) -> Order:
    return Order(id=order_id, kind="JOB", quantities={"kg": kg},
                 delivery=StopSpec(location_id=stop, time_windows=(DAY,),
                                   service_fixed=60), **kwargs)


def dropped(solution) -> set[str]:
    return {entry["order_id"] for entry in solution.unassigned}


# --------------------------------------------------------------------------
# FR-12: optional orders
# --------------------------------------------------------------------------

def test_a_prizeless_order_is_never_declined():
    """No prize means no price at which declining is acceptable, so the solver
    must place it or report the instance infeasible."""
    orders = (an_order("O1", "C1", kg=1), an_order("O2", "C2", kg=1))
    solution = solve(instance(orders, capacity=100, stops=2),
                     iterations=200, seed=0)
    assert dropped(solution) == set()


def test_low_value_work_is_declined_when_capacity_is_scarce():
    """FR-12's stated purpose. Two orders, room for one, and one worth far less
    than the other -- the cheap one goes."""
    orders = (an_order("VALUABLE", "C1", kg=60, prize=100_000, priority_tier=2),
              an_order("CHEAP", "C2", kg=60, prize=1, priority_tier=2))
    problem = instance(orders, capacity=60, stops=2)
    solution = solve(problem, iterations=600, seed=0)

    assert dropped(solution) == {"CHEAP"}, dropped(solution)
    assert verify(problem, solution).ok


# --------------------------------------------------------------------------
# FR-13: tiers, and the bug optionality hid
# --------------------------------------------------------------------------

def test_tier_zeromust_be_served_regardless_of_its_prize():
    """§4.1: "0 = must-serve". Asserted on the predicate itself.

    Through a solve this is untestable in the ordinary case, because the tier
    bonus already makes a tier-0 order the most valuable thing in the instance
    -- so it is kept whether or not anything marks it required. Perturbation
    proved that: reverting the fix passed every end-to-end test here.
    """
    from vrp.model import must_be_served

    assert must_be_served(an_order("A", "C1", kg=1, prize=100_000,
                                 priority_tier=0)), \
        "a tier-0 order carrying a prize was made declinable"
    assert must_be_served(an_order("B", "C1", kg=1, priority_tier=0))
    assert must_be_served(an_order("C", "C1", kg=1, priority_tier=3)), \
        "no prize means no price at which declining is acceptable"
    assert not must_be_served(an_order("D", "C1", kg=1, prize=5, priority_tier=3))


def test_an_uneconomic_tier_zero_order_is_still_served():
    """The end-to-end case where `required` actually does the work.

    The stop is a very long way out and its prize is small, so declining is
    economically preferred even after the tier bonus. An optional order here
    would be dropped; a must-serve one may not be.
    """
    far = Location(id="C1", lat=9.9, lon=-84.0, matrix_index=1)
    depot = Location(id="D", lat=9.9, lon=-84.0, matrix_index=0)
    leg = 5 * 3600
    grid = ((0, leg), (leg, 0))
    order = Order(id="MUST", kind="JOB", quantities={"kg": 1}, prize=1,
                  priority_tier=0,
                  delivery=StopSpec(location_id="C1", time_windows=(DAY,),
                                    service_fixed=60))
    problem = Problem(
        id="uneconomic", locations=(depot, far), orders=(order,),
        vehicles=(Vehicle(id="V1", capacities={"kg": 100}, shift=DAY,
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="u", durations=grid,
                            distances=((0, leg * 20), (leg * 20, 0))))

    solution = solve(problem, iterations=400, seed=0)
    assert dropped(solution) == set(), (
        "a must-serve order was declined because serving it lost money")


def test_a_higher_tier_is_not_sacrificed_for_a_lower_one():
    """The ordinary case: tier 1 survives, tier 3 goes."""
    # Prizes well above the round trip's ~1,200 of distance cost. At prize=50
    # the solver correctly declined *both* -- serving either lost money, so the
    # test never reached the question it was asking about.
    orders = (an_order("TIER1", "C1", kg=60, prize=1_000_000, priority_tier=1),
              an_order("TIER3", "C2", kg=60, prize=1_000_000, priority_tier=3))
    problem = instance(orders, capacity=60, stops=2)
    solution = solve(problem, iterations=600, seed=0)

    assert dropped(solution) == {"TIER3"}, dropped(solution)


def test_no_prize_is_large_enough_to_invert_the_tiers():
    """FR-13 says *lexicographic*, and this tests it where it actually lives.

    The property belongs to the tier bonuses, not to a particular solve, and
    checking it there makes it exact at any magnitude. Through the solver it is
    only checkable up to a ceiling: measured, PyVRP declines the low tier
    correctly at prizes of 10^6, and at 10^9 and above it returns INFEASIBLE
    with nothing dropped -- the prize overwhelms its internal capacity penalty,
    so violating the van's capacity looks cheaper than declining the work. That
    is a real limit and belongs in the record rather than in a fixture tuned
    until it passes.
    """
    from vrp.solve.pyvrp_adapter import tier_bonuses

    orders = (an_order("TIER1", "C1", kg=60, prize=1, priority_tier=1),
              an_order("TIER5", "C2", kg=60, prize=10 ** 15, priority_tier=5))
    bonuses = tier_bonuses(instance(orders, capacity=60, stops=2))

    protected = orders[0].prize + bonuses[precedence(orders[0])]
    tempting = orders[1].prize + bonuses[precedence(orders[1])]
    assert protected > tempting, (
        f"a 10^15 prize on tier 5 outranked tier 1: {tempting} >= {protected}")


def test_the_tier_ordering_holds_at_every_magnitude():
    """The same property swept, because one pair could coincide by luck."""
    from vrp.solve.pyvrp_adapter import tier_bonuses

    for magnitude in (10 ** 3, 10 ** 6, 10 ** 9, 10 ** 12, 10 ** 15):
        orders = (an_order("HIGH", "C1", kg=1, prize=1, priority_tier=1),
                  an_order("LOW", "C2", kg=1, prize=magnitude, priority_tier=4))
        bonuses = tier_bonuses(instance(orders, capacity=100, stops=2))
        assert (1 + bonuses[(1, COMMERCIAL)]) > (magnitude + bonuses[(4, COMMERCIAL)]), \
            magnitude


def test_the_solver_declines_the_low_tier_within_pyvrp_s_working_range():
    """The same claim end to end, at a magnitude PyVRP handles."""
    orders = (an_order("TIER1", "C1", kg=60, prize=1_000_000, priority_tier=1),
              an_order("TIER5", "C2", kg=60, prize=1_000_000, priority_tier=5))
    problem = instance(orders, capacity=60, stops=2)
    solution = solve(problem, iterations=600, seed=0)

    assert dropped(solution) == {"TIER5"}, dropped(solution)
    assert verify(problem, solution).ok


def test_tiers_are_respected_across_several_levels():
    """Three tiers, room for one. The survivor must be the most protected."""
    orders = (an_order("T1", "C1", kg=60, prize=10, priority_tier=1),
              an_order("T2", "C2", kg=60, prize=10_000, priority_tier=2),
              an_order("T3", "C3", kg=60, prize=1_000_000, priority_tier=3))
    problem = instance(orders, capacity=60, stops=3)
    solution = solve(problem, iterations=800, seed=0)

    assert "T1" not in dropped(solution), dropped(solution)


def test_within_a_tier_the_prize_decides():
    """Tiers order between levels; prizes order within one. Otherwise the
    prize would be decorative wherever tiers are used at all."""
    orders = (an_order("RICH", "C1", kg=60, prize=100_000, priority_tier=2),
              an_order("POOR", "C2", kg=60, prize=10, priority_tier=2))
    problem = instance(orders, capacity=60, stops=2)
    solution = solve(problem, iterations=600, seed=0)

    assert dropped(solution) == {"POOR"}, dropped(solution)


# --------------------------------------------------------------------------
# What fills a tier is not the tier — FR-25, T-75
# --------------------------------------------------------------------------

def test_three_orders_equal_on_tier_are_ordered_by_what_put_them_there():
    """FR-25: commercial priority, an SLA clock and a statutory obligation are
    "separate attributes, not one tier number: they are ordered differently,
    they expire differently, and only one of them is negotiable".

    `UC-117` is the operation: "Three tiers with different clocks are three
    different constraints, not three weights on one." Before this, the only way
    to say a legal duty outranked a paid preference was to give it a lower
    tier, which made the two indistinguishable in the plan that came back.
    """
    from vrp.model import precedence

    same_tier = [an_order(f"O{i}", "C1", kg=1, priority_tier=2,
                          priority_source=source)
                 for i, source in enumerate(("COMMERCIAL", "SLA", "STATUTORY"))]

    by_protection = sorted(same_tier, key=precedence)

    assert [o.priority_source for o in by_protection] == [
        "STATUTORY", "SLA", "COMMERCIAL"], (
        "a legal obligation outranks a contract, and a contract outranks a "
        "preference somebody paid for")


def test_the_bonus_that_protects_a_tier_also_separates_its_sources():
    """FR-13's lexicographic protection, applied to FR-25's split.

    The tier still decides first -- FR-25 says "`FR-13`'s tiers remain the
    mechanism" -- and the source only separates orders the tier cannot tell
    apart.
    """
    from vrp.model import precedence
    from vrp.solve.pyvrp_adapter import tier_bonuses

    problem = instance(tuple(
        an_order(f"O{i}", "C1", kg=1, priority_tier=tier,
                 priority_source=source,
                 prize=0 if source == "STATUTORY" else 500)
        for i, (tier, source) in enumerate(
            ((1, "COMMERCIAL"), (2, "STATUTORY"), (2, "SLA"), (2, "COMMERCIAL")))),
        capacity=100, stops=1)
    bonuses = tier_bonuses(problem)
    worth = {o.id: o.prize + bonuses[precedence(o)] for o in problem.orders}

    assert worth["O0"] > worth["O1"], (
        "tier 1 outranks every source on tier 2; the split refines the tier "
        "rather than replacing it")
    assert worth["O1"] > worth["O2"] > worth["O3"], (
        "and within tier 2 the statutory obligation outranks the SLA, which "
        "outranks the commercial preference")


def test_a_statutory_obligation_may_not_be_declined_at_any_price():
    """`UC-046`: under a universal service obligation "no address may be
    declined, so the drop-the-unprofitable-stop behaviour that helps elsewhere
    is prohibited"."""
    import pytest

    from vrp.model import ValidationError, must_be_served

    obliged = an_order("USO", "C1", kg=1, priority_tier=3,
                       priority_source="STATUTORY")
    paid_for = an_order("PAID", "C1", kg=1, priority_tier=3, prize=10_000)

    # The chain, in the order it actually runs: the model refuses to put a
    # price on a statutory duty, and an order with no price is one the solver
    # may not decline. The obligation is carried by the invariant rather than
    # by a special case in the adapter -- an earlier version had both, and the
    # special case enforced nothing because the invariant had already run.
    with pytest.raises(ValidationError, match="may not carry a prize"):
        an_order("BOTH", "C1", kg=1, priority_source="STATUTORY", prize=1)

    assert obliged.prize == 0, "there is no price, because none may be set"
    assert must_be_served(obliged), (
        "and an order with no price is one the solver may not decline, at any "
        "tier. Before FR-25 the only way to say this was to claim the address "
        "was tier 0, which conflates a legal duty with the top commercial one")
    assert not must_be_served(paid_for), "a prize is a price, and this one has one"


def test_an_sla_window_is_computed_from_when_the_fault_was_reported():
    """`UC-116` breaks on "fixed windows. The window is derived from the fault
    timestamp plus the SLA, so it is computed at intake and differs per
    order"."""
    from vrp.model import sla_window

    four_hours = 4 * 3600
    morning = sla_window(reported_at=8 * 3600, respond_within=four_hours)
    afternoon = sla_window(reported_at=14 * 3600, respond_within=four_hours)

    assert morning.end == 12 * 3600 and afternoon.end == 18 * 3600
    assert morning.end != afternoon.end, (
        "two faults of one severity reported six hours apart are due six hours "
        "apart; one window for both turns a four-hour target into a ten-hour "
        "one for half the estate")


# --------------------------------------------------------------------------
# The ceiling under FR-13's mechanism
# --------------------------------------------------------------------------
# `tier_bonuses` makes tiers lexicographic by giving each one a prize bonus
# exceeding everything obtainable beneath it, so the bonuses compound: the
# recurrence is multiplicative in the number of *distinct* tiers. Python
# integers are unbounded and PyVRP's are int64, and the module said so in a
# docstring -- "not yet guarded" -- which is a comment, not a guard.
#
# A wrapped bonus does not fail. It inverts the ordering this whole section
# exists to guarantee, silently, on an instance that looks ordinary.


def deep_instance(tiers: int) -> Problem:
    orders = tuple(an_order(f"O{t}", f"C{t + 1}", kg=1, priority_tier=t + 1,
                            prize=1000)
                   for t in range(tiers))
    return instance(orders, capacity=10_000, stops=tiers)


def test_a_tier_stack_the_encoding_cannot_carry_is_refused_by_name():
    """Measured: the bonuses pass int64 between 50 and 55 distinct tiers.

    A real operation reaches that by accident rather than by design -- map a
    priority *score* to a tier and a three-class scheme becomes a thousand.
    """
    with pytest.raises(NotImplementedError, match="int64"):
        solve(deep_instance(80), iterations=10, seed=0)


def test_the_refusal_says_how_deep_the_stack_was():
    """A limit a reader cannot measure against is a limit they cannot design
    around."""
    with pytest.raises(NotImplementedError) as refusal:
        solve(deep_instance(80), iterations=10, seed=0)

    assert "80" in str(refusal.value)


def test_an_ordinary_tier_stack_is_untouched():
    """The guard must not narrow what already worked. Three tiers is the
    shape every shipped model and every operation in the catalogue uses."""
    solution = solve(deep_instance(3), iterations=100, seed=0)

    assert solution.status in ("FEASIBLE", "OPTIMAL")


# --------------------------------------------------------------------------
# An arc that does not exist — `MTX-5`, `T-105`
# --------------------------------------------------------------------------
# The adapter already skips unreachable pairs when it builds edges, citing
# MTX-5: "the edge is simply absent. Adding it at any finite cost is what lets
# a solver route through a road that does not exist." Absent is not forbidden.
# PyVRP routes through the missing edge anyway and reports INFEASIBLE, so the
# comment claims a guarantee the library does not give.
#
# It matters because the sentinel is -1. In a minimisation that is not "outside
# the range of any real cost" -- it is the most attractive value there is, so
# the unreachable stop is visited *first*. Found on real Costa Rica road data,
# where disconnected pairs are ordinary.


def islanded() -> Problem:
    """Three customers, one of them with no road connection either way."""
    from vrp.model import UNREACHABLE
    size = 4
    grid = [[0 if i == j else 600 for j in range(size)] for i in range(size)]
    for i in range(size):
        if i != 3:
            grid[i][3] = UNREACHABLE
            grid[3][i] = UNREACHABLE
    rows = tuple(tuple(row) for row in grid)
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 100, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    orders = tuple(an_order(f"O{i}", f"C{i}", kg=1) for i in (1, 2, 3))
    return Problem(
        id="island", locations=locations, orders=orders,
        vehicles=(Vehicle(id="V1", capacities={"kg": 10}, shift=DAY,
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="i", durations=rows, distances=rows))


def test_an_instance_with_an_unreachable_arc_is_refused_by_name():
    """Refusing is the only honest answer this adapter can give.

    It cannot express a forbidden arc -- omitting the edge does not forbid it
    -- so an instance containing one is beyond the mapping, and `MTX-5` says
    the arc is hard-infeasible rather than expensive. `diagnose.preflight`
    reports which order is stranded; this is the backstop for a caller who did
    not ask.
    """
    with pytest.raises(NotImplementedError, match="unreachable"):
        solve(islanded(), iterations=50, seed=0)


def test_the_refusal_names_a_pair_a_reader_can_look_up():
    with pytest.raises(NotImplementedError) as refusal:
        solve(islanded(), iterations=50, seed=0)

    assert "C3" in str(refusal.value) or "3" in str(refusal.value)


def test_a_fully_connected_instance_is_untouched():
    """The guard must not narrow what already worked."""
    solution = solve(deep_instance(3), iterations=100, seed=0)

    assert solution.status in ("FEASIBLE", "OPTIMAL")


def severed() -> Problem:
    """Two customers reachable from the depot but not from each other.

    The shape real road data produced: a one-way system or a river between two
    addresses that the depot can reach separately. A depot-to-stop check sees
    nothing wrong, and a route serving both in sequence crosses an arc that
    does not exist.
    """
    from vrp.model import UNREACHABLE
    size = 4
    grid = [[0 if i == j else 600 for j in range(size)] for i in range(size)]
    grid[1][2] = UNREACHABLE
    grid[2][1] = UNREACHABLE
    rows = tuple(tuple(row) for row in grid)
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 100, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    orders = tuple(an_order(f"O{i}", f"C{i}", kg=1) for i in (1, 2, 3))
    return Problem(
        id="severed", locations=locations, orders=orders,
        vehicles=(Vehicle(id="V1", capacities={"kg": 10}, shift=DAY,
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="s", durations=rows, distances=rows))


def test_two_stops_unreachable_from_each_other_are_caught_too():
    """Every pair among the stops, not only the arcs out of the depot.

    This is the case that actually occurred: `ddn`'s day-one run failed on
    `no route from 2336 to 1443`, two delivery points, each perfectly
    reachable from its facility.
    """
    with pytest.raises(NotImplementedError, match="unreachable"):
        solve(severed(), iterations=50, seed=0)
