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
