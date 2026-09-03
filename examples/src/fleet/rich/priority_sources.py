"""A legal duty, a contract and a preference are not three weights on one scale.

Demonstrates the priority split landed for E-75/T-75 (FR-25, FR-13, §12.2):

    vrp.model      `priority_source`, `precedence`, `sla_window`
    vrp.solve      the bonus that makes a tier lexicographic, refined by source
    vrp.verify     INV-9, which judges the plan either way

FR-25: "Commercial priority, a contractual SLA clock, and a statutory
obligation are separate attributes, not one tier number: they are ordered
differently, they expire differently, and only one of them is negotiable.
`FR-13`'s tiers remain the mechanism; this requires that what fills them is not
conflated."

`UC-117` is the operation that asked for it, and says it in a line: "Three
tiers with different clocks are three different constraints, not three weights
on one."

Four things, in order:

1. **Ordered differently.** Three orders on the same tier, told apart only by
   what put them there. Before this the sole way to say a legal duty outranked
   a paid preference was to give it a lower tier, which made the two
   indistinguishable in the plan that came back.

2. **The tier still decides first.** The split refines `FR-13` rather than
   replacing it: a tier-1 preference still outranks a tier-2 obligation,
   because the tier is the mechanism and the source is what fills it.

3. **Only one of them is negotiable.** A prize is the price at which declining
   is acceptable. `UC-046`'s universal service obligation has no such price,
   so the model refuses to let one be written down -- and an order with no
   price is one the solver may not decline.

4. **They expire differently.** `UC-116`'s SLA window is computed at intake
   from the fault timestamp plus the response target, so two faults of one
   severity reported six hours apart are due six hours apart. One window for
   both turns a four-hour target into a ten-hour one for half the estate.

Runs offline.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/priority_sources.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.bench import fixtures
from vrp.model import (
    PRIORITY_SOURCES,
    Order,
    StopSpec,
    ValidationError,
    must_be_served,
    precedence,
    sla_window,
)
from vrp.solve.pyvrp_adapter import tier_bonuses

HOUR = 3600


def an_order(order_id: str, *, tier: int, source: str, prize: int = 0) -> Order:
    return Order(id=order_id, kind="JOB", quantities={"kg": 1},
                 priority_tier=tier, priority_source=source, prize=prize,
                 delivery=StopSpec(location_id="C1",
                                   time_windows=(fixtures.DAY,),
                                   service_fixed=60))


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def ordered_differently() -> None:
    heading("1.", "Ordered differently")
    same_tier = [an_order(source, tier=2, source=source)
                 for source in reversed(PRIORITY_SOURCES)]
    print("\n   three orders, all tier 2, told apart only by what put them there:")
    for order in sorted(same_tier, key=precedence):
        print(f"      {precedence(order)}  {order.priority_source}")
    print("\n   A legal obligation outranks a contract, and a contract outranks")
    print("   a preference somebody paid for. Before FR-25 the only way to say")
    print("   so was to give the duty a lower tier, which made a statutory")
    print("   obligation and a top-paying customer the same thing in the plan.")


def the_tier_still_decides_first() -> None:
    heading("2.", "The tier still decides first")
    orders = (an_order("premium", tier=1, source="COMMERCIAL", prize=500),
              an_order("obliged", tier=2, source="STATUTORY"),
              an_order("contracted", tier=2, source="SLA", prize=500),
              an_order("ordinary", tier=2, source="COMMERCIAL", prize=500))
    problem = fixtures.instance("prio", orders, (fixtures.van(),))
    bonuses = tier_bonuses(problem)
    print(f"\n   {'order':12s} {'tier':>4s} {'source':11s} {'worth if kept':>16s}")
    for order in orders:
        worth = order.prize + bonuses[precedence(order)]
        print(f"      {order.id:12s} {order.priority_tier:>4d} "
              f"{order.priority_source:11s} {worth:>13,}")
    print("\n   FR-25: \"`FR-13`'s tiers remain the mechanism; this requires that")
    print("   what fills them is not conflated.\" So tier 1 still outranks every")
    print("   source on tier 2, and the source only separates orders the tier")
    print("   cannot tell apart.")


def only_one_is_negotiable() -> None:
    heading("3.", "Only one of them is negotiable")
    obliged = an_order("uso", tier=3, source="STATUTORY")
    paid = an_order("paid", tier=3, source="COMMERCIAL", prize=10_000)
    print(f"\n   statutory, tier 3, prize {obliged.prize}  -> "
          f"declinable: {not must_be_served(obliged)}")
    print(f"   commercial, tier 3, prize {paid.prize:,} -> "
          f"declinable: {not must_be_served(paid)}")
    try:
        an_order("both", tier=3, source="STATUTORY", prize=1)
    except ValidationError as refusal:
        print("\n   putting a price on the obligation: refused")
        print(f"      {refusal}")
    print("\n   UC-046: under a universal service obligation \"no address may be")
    print("   declined, so the drop-the-unprofitable-stop behaviour that helps")
    print("   elsewhere is prohibited\". The contradiction is refused where it")
    print("   is written, not worked around where it is read.")


def they_expire_differently() -> None:
    heading("4.", "They expire differently")
    target = 4 * HOUR
    print("\n   one severity, one four-hour response target, two faults:")
    for reported in (8 * HOUR, 14 * HOUR):
        window = sla_window(reported_at=reported, respond_within=target)
        print(f"      reported {reported // 3600:02d}:00 -> due by "
              f"{window.end // 3600:02d}:00")
    print("\n   UC-116 breaks on \"fixed windows. The window is derived from the")
    print("   fault timestamp plus the SLA, so it is computed at intake and")
    print("   differs per order.\" One window for both would turn a four-hour")
    print("   target into a ten-hour one for half the estate, and every report")
    print("   would say the SLA was met.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nFR-25, from CAT-VRP-003 §12.2 -- five operations asked for this "
          "split.")
    ordered_differently()
    the_tier_still_decides_first()
    only_one_is_negotiable()
    they_expire_differently()
    print(f"\n{'=' * 72}")
    print("The tier is still the mechanism. What changed is that the plan can")
    print("now say which of three different promises put an order where it is,")
    print("and a dispatcher asked to break one can see which one it would be.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
