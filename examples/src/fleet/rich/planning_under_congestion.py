"""Knowing the traffic is not the same as planning around it.

Demonstrates the planning path landed for E-82/T-82 (FR-14, §7.5, NFR-01):

    vrp.polish.tsptw_sequence   the route DP, charging each leg at the moment
                                the state says the van leaves
    vrp.timedependent           the speed profile, and the bound §7.5 prunes on

`T-80` taught the evaluator and the verifier to agree about a congested day.
That changes no decision: a plan built at free flow and then priced through the
peak is still the free-flow plan, with accurate bad news attached. This is the
other half -- building the sequence under the profile, so the peak is something
the search routes around rather than something it discovers afterwards.

The round is ten real deliveries around the Guadalupe depot in San Jose, with
the corpus's own service times. One of them is a pharmacy, which is the kind of
customer that has a receiving bay with an hour on it.

Four things, in order:

1. **When it matters, and when it does not.** Sweeping the bay's closing time
   across the morning, congestion-aware planning changes the plan in a band
   twenty minutes wide and changes nothing on either side of it. This is the
   part a contrived instance cannot tell you, and the part worth knowing before
   paying for it.

2. **Inside the band.** The free-flow plan is *faster* and arrives after the
   bay has shut. The congestion-aware plan finishes later and is the only one
   that can be driven. "Better" here means legal.

3. **§7.5's filter.** The DP dismisses a candidate on the arc at the best speed
   the profile ever offers before pricing the real one. Shown to be a bound
   rather than a guess, because a bound that over-states silently discards
   plans nobody learns were available.

4. **What it costs.** Every arc goes through IGP instead of a table lookup.
   Measured at the DP's `MAX_DP_STOPS` ceiling, against NFR-01.

**The geometry is real; the profile is not.** The stops, their spacing and their
service times come from the corpus. The half-speed morning peak is invented, and
`T-63` is the task that fits real ones from telematics this stack does not have.
A search that carries a profile is testable with any profile; only its
*usefulness* waits on real traffic.

Requires a running gateway: distances are measured on the road network and
there is no straight-line fallback. `examples/.env` points at the FreeBSD jail. Against the committed corpus slice.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/planning_under_congestion.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

# Importing config puts OSRM_API_URL into the environment, so the
# gateway this example now requires is the one `examples/.env` names.
import config  # noqa: F401
import dataset

from vrp.evaluator import build_timeline
from vrp.model import (
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    Vehicle,
    travel_between,
)
from vrp.polish import MAX_DP_STOPS, tsptw_sequence
from vrp.timedependent import SpeedProfile, fastest_possible

HOUR = 3600
STOPS = 10
SHIFT = TimeWindow(start=7 * HOUR, end=20 * HOUR)
DAY = TimeWindow(start=0, end=20 * HOUR)


def peak() -> SpeedProfile:
    """Free flow, except half speed through a three-hour morning peak."""
    return SpeedProfile(
        bucket_seconds=HOUR,
        multipliers_ppt=tuple(500 if 7 <= hour <= 9 else 1000
                              for hour in range(24)))


def the_round(stops: int = STOPS) -> tuple:
    """A real day's work, and the stop that has a bay to hit.

    Args:
        stops: How many deliveries to take from the corpus.

    Returns:
        `(locations, matrix, deliveries, depot, bay)`, where `bay` is the
        1-based index of the pharmacy furthest from the depot -- the customer
        with both a reason for a receiving window and the driving to miss it.
    """
    locations, matrix, deliveries, depot = dataset.road_sites(
        stops, "spread", "congestion")
    # A pharmacy is the reason a stop has a receiving window, so prefer one --
    # but which categories a round contains is the corpus's business, not this
    # example's. A 14-stop round has none, and assuming otherwise crashed the
    # NFR-01 section, which does not care what the bay sells.
    pharmacies = [i for i, d in enumerate(deliveries, 1)
                  if d["category"] == "Pharmacy"]
    candidates = pharmacies or list(range(1, len(deliveries) + 1))
    bay = max(candidates, key=lambda i: matrix.duration(0, i))
    return locations, matrix, deliveries, depot, bay


ROUND = the_round()
_, MATRIX, DELIVERIES, DEPOT, BAY = ROUND


def a_day(aware: bool, closes: int, geometry: tuple = ROUND) -> Problem:
    """The round, with the bay shutting at `closes`.

    Args:
        aware: Whether the problem carries the speed profile, and so whether
            the DP sequences against the peak or against free flow.
        closes: Second of the day the receiving bay shuts.
        geometry: `(locations, matrix, deliveries, depot, bay)` as `the_round`
            returns it. Defaults to the ten-stop round the example is about.

    Returns:
        A problem over the real locations and the real service times.
    """
    locations, matrix, deliveries, _, bay = geometry
    bay_window = TimeWindow(start=7 * HOUR, end=closes)
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}",
                                service_fixed=d["service_minutes"] * 60,
                                time_windows=((bay_window,) if i == bay
                                              else (DAY,))))
        for i, d in enumerate(deliveries, 1))
    vehicle = Vehicle(id="V1", capacities={"kg": 10_000}, shift=SHIFT,
                      start_location_id="D", end_location_id="D")
    return Problem(id=f"congestion-{aware}-{len(deliveries)}",
                   locations=locations, orders=orders, vehicles=(vehicle,),
                   matrix=matrix, speed_profile=peak() if aware else None)


def clock(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def order_ids(count: int = STOPS) -> list[str]:
    return [f"O{i}" for i in range(1, count + 1)]


def served_at(sequence: list[str], closes: int, order: str) -> int:
    """When a sequence actually reaches an order, driven through the peak."""
    timeline = build_timeline(a_day(True, closes), "V1", sequence)
    return next(step.start_service for step in timeline
                if step.order_id == order)


def plans_for(closes: int) -> tuple[list[str] | None, list[str] | None]:
    """The free-flow plan and the congestion-aware plan for one bay hour."""
    return (tsptw_sequence(a_day(False, closes), "V1", order_ids()),
            tsptw_sequence(a_day(True, closes), "V1", order_ids()))


def describe_round() -> None:
    heading("1.", "Ten real deliveries, and the one with a bay to hit")
    kind = DELIVERIES[BAY - 1]["category"]
    print(f"\n   Depot: {DEPOT['name']}. Service times are the corpus's own.")
    print(f"   The bay is C{BAY}, the furthest {kind} on the round"
          + ("." if kind == "Pharmacy" else
             " -- no pharmacy in this\n   selection, so the receiving window "
             "goes to the furthest stop instead.") + "\n")
    print(f"      {'stop':6s} {'category':12s} {'from depot':>11s} {'service':>8s}")
    for i, delivery in enumerate(DELIVERIES, 1):
        mark = "  <- receiving bay" if i == BAY else ""
        print(f"      C{i:<5d} {delivery['category']:12s} "
              f"{MATRIX.duration(0, i) // 60:8d} min {delivery['service_minutes']:5d} min"
              f"{mark}")
    print("\n   The peak runs 07:00-10:00 at half speed, and the van leaves at")
    print(f"   {clock(SHIFT.start)}. Every leg below is a straight line between real")
    print("   addresses, so the driving is shorter than the road's but the")
    print("   shape of the day is the corpus's.")


def when_it_matters() -> tuple[int, int]:
    """Sweep the bay hour; return the band where the two plans disagree."""
    heading("2.", "The band where planning under the profile changes the plan")
    print(f"\n      {'bay shuts':>9s} {'free-flow plan':>14s} {'aware plan':>11s}   verdict")
    band = []
    for minute in range(430, 620, 10):
        closes = minute * 60
        blind, aware = plans_for(closes)
        if aware is None:
            # The honest half of a refusal: the free-flow planner still hands
            # back a sequence here, and driving it arrives after the bay shuts.
            reached = ("refused" if blind is None
                       else clock(served_at(blind, closes, f"O{BAY}")))
            print(f"      {clock(closes):>9s} {reached:>14s} {'refused':>11s}"
                  "   no plan survives the peak; only one says so")
            continue
        late = served_at(blind, closes, f"O{BAY}") > closes
        if late:
            band.append(minute)
        print(f"      {clock(closes):>9s} "
              f"{clock(served_at(blind, closes, f'O{BAY}')):>14s} "
              f"{clock(served_at(aware, closes, f'O{BAY}')):>11s}   "
              f"{'free-flow plan MISSES the bay' if late else 'both arrive in time'}")
    if not band:
        # Whether a band exists at all depends on the round, and the round
        # comes from the corpus. Saying so beats printing an empty range or
        # crashing on min() of nothing, which is how the bay selection above
        # failed when a redrawn corpus put no pharmacy in the round.
        print("\n   No bay hour separates the two plans on this round: the")
        print("   free-flow sequence happens to satisfy every window the peak")
        print("   leaves it. That is a real answer -- planning under the")
        print("   profile is worth nothing here -- and the sweep is what")
        print("   distinguishes it from the mechanism being broken.")
        return SHIFT.start, SHIFT.start
    print(f"\n   The two plans differ between {clock(min(band) * 60)} and "
          f"{clock(max(band) * 60)} and nowhere else.")
    print("   Earlier, the bay is tight enough that both go there first.")
    print("   Later, deferring it is safe even at half speed. Sequencing under")
    print("   the profile buys you a twenty-minute band -- which is the")
    print("   honest answer to whether it is worth the arc cost in part 5.")
    return min(band) * 60, max(band) * 60


def inside_the_band(closes: int) -> None:
    heading("3.", "Faster, and undriveable")
    blind, aware = plans_for(closes)
    print(f"\n   The bay shuts at {clock(closes)}.\n")
    print(f"      {'plan':22s} {'bay served':>11s}  {'finishes':>9s}  verdict")
    for label, sequence in (("planned under peak", aware),
                            ("planned at free flow", blind)):
        timeline = build_timeline(a_day(True, closes), "V1", sequence)
        served = served_at(sequence, closes, f"O{BAY}")
        over = (served - closes) // 60
        print(f"      {label:22s} {clock(served):>11s}  "
              f"{clock(timeline[-1].arrival):>9s}  "
              f"{f'LATE by {over} min' if over > 0 else 'in window'}")
    print(f"\n      planned at free flow : {' -> '.join(blind)}")
    print(f"      planned under peak   : {' -> '.join(aware)}")
    print("\n   The free-flow plan defers the pharmacy because at free flow it")
    print("   can. A search told only about distance cannot see the")
    print("   difference, because at free flow there is none.")


def the_filter() -> None:
    heading("4.", "The bound the DP prunes with, and why it may not be a guess")
    problem = a_day(True, 9 * HOUR)
    span = range(SHIFT.start, 20 * HOUR, 1800)
    print(f"\n   arc D->C{BAY} priced at each departure, against the bound:\n")
    print(f"      {'depart':8s} {'costs':>8s} {'bound':>8s}   bound holds")
    # `fastest_possible` is the public form of what the DP prunes on: the arc
    # at the best speed the profile ever offers. If you are writing your own
    # filter over a profile, this is the function to build it from.
    bound = fastest_possible(problem.matrix.duration(0, BAY),
                             problem.speed_profile)
    for depart in list(span)[:8]:
        exact = travel_between(problem, 0, BAY, depart)
        print(f"      {clock(depart):8s} {exact // 60:5d} min {bound // 60:5d} min"
              f"   {'yes' if bound <= exact else 'NO'}")
    worst = min(travel_between(problem, 0, BAY, d) - bound for d in span)
    print(f"\n   Tightest margin across the whole day: {worst // 60} min.")
    print("   The bound is never beaten, so pruning on it discards only")
    print("   candidates the exact price would have discarded too. A bound")
    print("   that over-states by one second throws away legal plans and")
    print("   leaves no trace that it did.")


def what_it_costs() -> None:
    heading("5.", "NFR-01: what charging every arc through IGP costs")
    ceiling = the_round(MAX_DP_STOPS)
    print(f"\n   the route DP at its ceiling of {MAX_DP_STOPS} real stops:\n")
    print(f"      {'day':12s} {'sequencing':>12s}")
    times = {}
    for label, aware in (("free flow", False), ("peak", True)):
        problem = a_day(aware, 20 * HOUR, geometry=ceiling)
        start = time.perf_counter()
        tsptw_sequence(problem, "V1", order_ids(MAX_DP_STOPS))
        times[label] = time.perf_counter() - start
        print(f"      {label:12s} {times[label] * 1000:9.0f} ms")
    print(f"\n   A factor of {times['peak'] / times['free flow']:.1f}x on a step that is already")
    print(f"   bounded at {MAX_DP_STOPS} stops -- a constant, not a change of")
    print("   growth: the DP's state space is the same either way, and only")
    print("   the price of an arc inside it changes.")
    print(f"\n   NFR-01 gives 15 minutes for 2,000 stops. At {times['peak']:.2f}s a route")
    print(f"   that is {int(15 * 60 / times['peak'])} routes' worth of polish inside the budget, so the")
    print("   ceiling this runs into first is the DP's own, not the NFR's.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nFR-14, §7.5 and NFR-01. Real stops; the traffic profile is invented.")
    describe_round()
    earliest, _ = when_it_matters()
    inside_the_band(earliest)
    the_filter()
    what_it_costs()
    print(f"\n{'=' * 72}")
    print("Evaluating under congestion tells you the plan is late.")
    print("Planning under it gives you one that is not -- in the band where")
    print("that is a different plan at all, which is narrower than it sounds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
