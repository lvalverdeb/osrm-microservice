"""E-13 (T-13) — lexicographic objective with instance-derived scaling.

SDD §5.1, FR-13. The specification opens by calling naive weighted sums "the
most common modelling error in production routing": weights that balance on a
200-stop day silently invert on a 2,000-stop day.

So the property under test is not "the numbers look sensible" but the one that
actually matters — **a higher tier is never traded for a lower one, at any
magnitude**. A test that only checks plausible cases would pass on exactly the
weighted sum §5.1 forbids.
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
)
from vrp.objective import (
    Mode,
    ObjectiveSpec,
    Tier,
    TierValues,
    compare,
    score,
    tier_scales,
)

DAY = TimeWindow(start=0, end=12 * 3600)


def problem(customers: int = 6, vehicles: int = 3) -> Problem:
    size = customers + 1
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 100,
                 lon=-84.0 - i / 100, matrix_index=i)
        for i in range(size)
    )
    durations = tuple(tuple(0 if i == j else 600 for j in range(size)) for i in range(size))
    distances = tuple(tuple(0 if i == j else 5000 for j in range(size)) for i in range(size))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"units": 3},
              priority_tier=0 if i <= 2 else 1,
              prize=100 * i,
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=300))
        for i in range(1, size)
    )
    fleet = tuple(
        Vehicle(id=f"V{v}", capacities={"units": 100}, shift=DAY,
                start_location_id="D", end_location_id="D")
        for v in range(1, vehicles + 1)
    )
    return Problem(id="obj", locations=locations, orders=orders, vehicles=fleet,
                   matrix=TravelMatrix(version="flat", durations=durations,
                                       distances=distances))


def values(**kwargs) -> TierValues:
    return TierValues({Tier[k.upper()]: v for k, v in kwargs.items()})


# --- the property the whole design exists for ----------------------------

@pytest.mark.parametrize("lower_tier_advantage", [1, 1_000, 10**9, 10**15])
def test_a_higher_tier_is_never_traded_for_a_lower_one(lower_tier_advantage):
    """One unserved priority-0 order outranks any operating saving whatsoever.

    Parametrised to absurd magnitudes on purpose: this is exactly where a
    weighted sum inverts, and where "it looked fine in testing" comes from.
    """
    spec = ObjectiveSpec(mode=Mode.MIN_COST)

    serves_everyone = values(unserved_p0=0, operating=lower_tier_advantage)
    drops_one = values(unserved_p0=1, operating=0)

    assert compare(serves_everyone, drops_one, spec) < 0, \
        "dropping a priority-0 order was traded away for operating cost"


def test_tier_scales_come_from_the_instance_not_a_constant():
    """§5.1: weights are computed from the instance, not hard-coded.

    A 200-stop day and a 2,000-stop day need different multipliers; identical
    scales across the two is the bug this guards against.
    """
    small = tier_scales(problem(customers=6), ObjectiveSpec(mode=Mode.MIN_COST))
    large = tier_scales(problem(customers=200, vehicles=40),
                        ObjectiveSpec(mode=Mode.MIN_COST))
    assert large[Tier.UNSERVED_P0] > small[Tier.UNSERVED_P0]
    assert large[Tier.FLEET] > small[Tier.FLEET]


def test_each_level_strictly_dominates_everything_beneath_it():
    """The invariant that makes the ordering lexicographic rather than blended.

    Checked per *level*, not per tier: tiers sharing a level (fleet and
    operating under MIN_COST) are meant to trade against each other, so
    demanding domination between them would be demanding the wrong thing.
    """
    p = problem(customers=30, vehicles=8)
    for mode in Mode:
        spec = ObjectiveSpec(mode=mode)
        scales, bounds = tier_scales(p, spec), spec.tier_bounds(p)
        levels = spec.levels()
        for index, group in enumerate(levels):
            beneath = sum(spec.monetary(t, bounds[t]) * scales[t]
                          for lower in levels[index + 1:] for t in lower)
            assert scales[group[0]] > beneath, (
                f"{mode.name}: {[t.name for t in group]} does not dominate below")


# --- modes (§5.2) --------------------------------------------------------

def test_min_vehicles_puts_fleet_above_operating_cost():
    spec = ObjectiveSpec(mode=Mode.MIN_VEHICLES)
    fewer_vehicles = values(fleet=1, operating=10**6)
    cheaper_driving = values(fleet=2, operating=0)
    assert compare(fewer_vehicles, cheaper_driving, spec) < 0


def test_min_cost_lets_a_vehicle_pay_for_itself():
    """Under MIN_COST an extra vehicle is worth it if it saves more than it costs."""
    spec = ObjectiveSpec(mode=Mode.MIN_COST)
    extra_vehicle_but_cheaper = values(fleet=2, operating=1_000)
    fewer_but_dearer = values(fleet=1, operating=100_000)
    assert compare(extra_vehicle_but_cheaper, fewer_but_dearer, spec) < 0


def test_max_service_puts_unserved_above_fleet_and_operating():
    spec = ObjectiveSpec(mode=Mode.MAX_SERVICE)
    serves_more = values(unserved=0, fleet=9, operating=10**6)
    serves_less = values(unserved=1, fleet=1, operating=0)
    assert compare(serves_more, serves_less, spec) < 0


def test_hard_violations_outrank_absolutely_everything():
    """Tier 0. A plan with a hard violation is never preferable. CON-1."""
    for mode in Mode:
        spec = ObjectiveSpec(mode=mode)
        legal = values(hard=0, unserved_p0=5, fleet=9, operating=10**9)
        illegal = values(hard=1)
        assert compare(legal, illegal, spec) < 0, f"{mode} preferred an illegal plan"


# --- scoring a real solution --------------------------------------------

def test_scoring_a_solution_populates_the_tiers_it_can_observe():
    from vrp.evaluator import build_timeline
    from vrp.model import Route, Solution

    p = problem()
    served = [o.id for o in p.orders][:4]          # two orders left unserved
    timeline = build_timeline(p, "V1", served)
    solution = Solution(problem_id=p.id,
                        routes=(Route(vehicle_id="V1", steps=timeline),),
                        unassigned=tuple({"order_id": o.id, "reason_code": "X",
                                          "explanation": ""} for o in p.orders[4:]))
    spec = ObjectiveSpec(mode=Mode.MIN_COST)
    result = score(p, solution, spec)
    # §5.1 Tier 3 is "Sum of fixed_cost(v) over deployed vehicles", so this is
    # money. This fleet prices nothing, so it falls back to the spec's rate --
    # which is what the tier held before T-44 made it per vehicle, multiplied
    # through by `monetary` instead of at source.
    assert result.values[Tier.FLEET] == spec.vehicle_fixed_cost
    # FR-32's other half: under MIN_VEHICLES the tier holds the count it names.
    assert score(p, solution,
                 ObjectiveSpec(mode=Mode.MIN_VEHICLES)).values[Tier.FLEET] == 1
    assert result.values[Tier.OPERATING] > 0
    # §5.1: tier 2's penalty is "prize forgone", so this is money, not a count.
    # Both unserved orders here carry a prize, hence a positive value.
    assert result.values[Tier.UNSERVED] > 0
    assert result.values[Tier.UNSERVED_P0] == 0     # both are priority tier 1
    assert result.total > 0


def test_prize_collecting_lets_an_order_be_worth_less_than_the_drive():
    """§5.2: "maximise Σ prizes − cost, orders freely droppable".

    Total prize is a constant of the instance, so maximising `Σ collected − cost`
    is the same thing as minimising `prize forgone + cost`. That is a trade in
    one currency, not a precedence — which makes `PRIZE_COLLECTING` the one mode
    where tier 2 does *not* dominate tiers 3-4.
    """
    spec = ObjectiveSpec(mode=Mode.PRIZE_COLLECTING)

    # A 500-prize order sitting 90km off the round: not worth collecting.
    drops_a_cheap_order = TierValues({Tier.UNSERVED: 500, Tier.OPERATING: 10_000})
    drives_out_for_it = TierValues({Tier.UNSERVED: 0, Tier.OPERATING: 90_000})
    assert compare(drops_a_cheap_order, drives_out_for_it, spec) < 0

    # A 500,000-prize order at the same detour: worth collecting.
    drops_a_dear_order = TierValues({Tier.UNSERVED: 500_000, Tier.OPERATING: 10_000})
    assert compare(drives_out_for_it, drops_a_dear_order, spec) < 0


def test_only_prize_collecting_trades_service_against_cost():
    """Every other mode keeps unserved orders strictly above cost.

    `MAX_SERVICE` is the mode that names this ("tier 2 dominates tier 3-4"), but
    it is the default everywhere except prize-collecting, so this pins all of
    them rather than just that one.
    """
    drops_one_to_save_a_fortune = TierValues({Tier.UNSERVED: 1, Tier.OPERATING: 0})
    serves_all_expensively = TierValues({Tier.UNSERVED: 0, Tier.OPERATING: 10**9})
    for mode in Mode:
        if mode is Mode.PRIZE_COLLECTING:
            continue
        spec = ObjectiveSpec(mode=mode)
        assert compare(serves_all_expensively, drops_one_to_save_a_fortune, spec) < 0, (
            f"{mode.name} bought a dropped order with distance savings")


def test_a_priority_zero_order_is_never_droppable_even_when_collecting_prizes():
    """"Freely droppable" is about tier 2, not tier 1.

    §5.1 keeps unserved priority-0 orders in their own tier above everything,
    and prize-collecting does not buy its way past that: a P0 order is a promise,
    not a bid.
    """
    spec = ObjectiveSpec(mode=Mode.PRIZE_COLLECTING)
    drops_a_p0 = TierValues({Tier.UNSERVED_P0: 1, Tier.OPERATING: 0})
    keeps_it = TierValues({Tier.UNSERVED_P0: 0, Tier.OPERATING: 10**12})
    assert compare(keeps_it, drops_a_p0, spec) < 0
