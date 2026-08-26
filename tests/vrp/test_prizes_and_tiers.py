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
)
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

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

def test_tier_zero_is_required_regardless_of_its_prize():
    """§4.1: "0 = must-serve". Asserted on the predicate itself.

    Through a solve this is untestable in the ordinary case, because the tier
    bonus already makes a tier-0 order the most valuable thing in the instance
    -- so it is kept whether or not anything marks it required. Perturbation
    proved that: reverting the fix passed every end-to-end test here.
    """
    from vrp.solve.pyvrp_adapter import _is_required

    assert _is_required(an_order("A", "C1", kg=1, prize=100_000,
                                 priority_tier=0)), \
        "a tier-0 order carrying a prize was made declinable"
    assert _is_required(an_order("B", "C1", kg=1, priority_tier=0))
    assert _is_required(an_order("C", "C1", kg=1, priority_tier=3)), \
        "no prize means no price at which declining is acceptable"
    assert not _is_required(an_order("D", "C1", kg=1, prize=5, priority_tier=3))


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

    protected = orders[0].prize + bonuses[orders[0].priority_tier]
    tempting = orders[1].prize + bonuses[orders[1].priority_tier]
    assert protected > tempting, (
        f"a 10^15 prize on tier 5 outranked tier 1: {tempting} >= {protected}")


def test_the_tier_ordering_holds_at_every_magnitude():
    """The same property swept, because one pair could coincide by luck."""
    from vrp.solve.pyvrp_adapter import tier_bonuses

    for magnitude in (10 ** 3, 10 ** 6, 10 ** 9, 10 ** 12, 10 ** 15):
        orders = (an_order("HIGH", "C1", kg=1, prize=1, priority_tier=1),
                  an_order("LOW", "C2", kg=1, prize=magnitude, priority_tier=4))
        bonuses = tier_bonuses(instance(orders, capacity=100, stops=2))
        assert (1 + bonuses[1]) > (magnitude + bonuses[4]), magnitude


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
