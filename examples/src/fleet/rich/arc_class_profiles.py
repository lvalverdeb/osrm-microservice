"""Rush hour on the ring road is not rush hour on a lane.

Demonstrates per-arc-class speed profiles landed for E-83/T-83 (§6.3, §12.2):

    vrp.model.profile_for_arc        which profile governs which arc
    vrp.speedfit.as_profiles         a whole fit, applied at once

§6.3 asks for "per-arc (or per-zone) piecewise-constant **speed** profiles".
Until this landed a `Problem` carried one, which says congestion slows a
motorway exactly as much as it slows a residential street. Nobody who has
driven at half past eight believes that.

Four things, in order:

1. **The measurement that unblocked it.** This task was filed blocked on the
   grounds that no instance had ever shown more than one class of road. That
   was checked against a corpus invented for another example. Against the
   twenty-seven real fixtures, sixteen span two or three -- and the
   eleven that do not are the degenerate ones with two or six arcs.

2. **What one profile has to claim.** The same arc pair, timed under a single
   instance-wide profile: the side street and the motorway are slowed by
   exactly the same factor, because there is nothing else the model can say.

3. **What three profiles say instead.** The motorway crawls from seven to
   eleven; the side street does not care.

4. **What it refuses.** A mapping missing a class the matrix contains is
   refused by name rather than defaulted to free flow -- the default would
   make the motorway the one road nobody modelled while the plan still looked
   fully time-aware.

Runs offline. The profiles are invented; T-63 fits real ones.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/arc_class_profiles.py
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.bench import fixtures
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
    travel_between,
)
from vrp.timedependent import SpeedProfile, arc_class_of

HOUR = 3600
ARCS = (("D", "C1", 0, 1, "local"), ("D", "C2", 0, 2, "trunk"))


def profile(congested: tuple[int, ...]) -> SpeedProfile:
    return SpeedProfile(
        bucket_seconds=HOUR,
        multipliers_ppt=tuple(500 if hour in congested else 1000
                              for hour in range(24)))


def a_network(profile_=None, profiles=None) -> Problem:
    """A depot, a shop two minutes away, and one an hour up the motorway."""
    day = TimeWindow(start=0, end=20 * HOUR)
    locations = tuple(
        Location(id=site, lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
        for i, site in enumerate(("D", "C1", "C2")))
    grid = ((0, 120, 3600), (120, 0, 3600), (3600, 3600, 0))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}", service_fixed=60,
                                time_windows=(day,)))
        for i in (1, 2))
    return Problem(
        id="network", locations=locations, orders=orders,
        vehicles=(Vehicle(id="V1", capacities={"kg": 10},
                          shift=TimeWindow(start=7 * HOUR, end=20 * HOUR),
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="net", durations=grid, distances=grid),
        speed_profile=profile_, speed_profiles=profiles)


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def the_measurement() -> None:
    heading("1.", "How many classes of road a real instance actually has")
    names = sorted(n for n in dir(fixtures)
                   if n.startswith("uc") and callable(getattr(fixtures, n)))
    spread = collections.Counter()
    for name in names:
        problem = getattr(fixtures, name)()
        classes = set()
        size = problem.matrix.size
        for origin in range(size):
            for destination in range(size):
                if origin == destination:
                    continue
                if not problem.matrix.is_reachable(origin, destination):
                    continue
                cost = problem.matrix.duration(origin, destination)
                if cost > 0:
                    classes.add(arc_class_of(cost))
        if classes:
            spread[len(classes)] += 1
    total = sum(spread.values())
    print(f"\n   {total} fixtures in vrp.bench:\n")
    for count in sorted(spread):
        word = "class" if count == 1 else "classes"
        print(f"      {spread[count]:2d} span {count} {word}")
    print(f"\n   {total - spread[1]} of {total} carry more than one, so one")
    print("   profile per instance was never describing the roads it timed.")


def what_one_profile_claims() -> None:
    heading("2.", "One profile: the same factor on every road")
    problem = a_network(profile_=profile((7, 8, 9, 10)))
    print(f"\n      {'arc':10s} {'class':9s} {'free flow':>10s} {'at 08:00':>10s}")
    for origin_id, destination_id, origin, destination, kind in ARCS:
        free = problem.matrix.duration(origin, destination)
        peak = travel_between(problem, origin, destination, 8 * HOUR)
        print(f"      {origin_id + '->' + destination_id:10s} {kind:9s} "
              f"{free // 60:7d} min {peak // 60:7d} min")
    print("\n   Both doubled. The model has no way to say the lane is fine.")


def what_three_profiles_say() -> None:
    heading("3.", "One profile per class: the motorway crawls, the lane does not")
    problem = a_network(profiles={
        "local": profile(()),
        "arterial": profile((8,)),
        "trunk": profile((7, 8, 9, 10)),
    })
    print(f"\n      {'arc':10s} {'class':9s} {'free flow':>10s} {'at 08:00':>10s}")
    for origin_id, destination_id, origin, destination, kind in ARCS:
        free = problem.matrix.duration(origin, destination)
        peak = travel_between(problem, origin, destination, 8 * HOUR)
        print(f"      {origin_id + '->' + destination_id:10s} {kind:9s} "
              f"{free // 60:7d} min {peak // 60:7d} min")
    print("\n   Which is what a driver would have told you, and what §12.2")
    print("   fits: the multipliers are grouped per arc class already.")


def what_it_refuses() -> None:
    heading("4.", "The mapping that does not cover the network")
    try:
        a_network(profiles={"local": profile(()), "arterial": profile((8,))})
    except Exception as refusal:
        print(f"\n   leaving the motorway out:\n\n      {refusal}")
    print("\n   Not defaulted to free flow. A silent default makes the road")
    print("   nobody modelled indistinguishable from the roads that were,")
    print("   and the plan still reports itself as time-aware.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\n§6.3 and §12.2. The profiles are invented; T-63 fits real ones.")
    the_measurement()
    what_one_profile_claims()
    what_three_profiles_say()
    what_it_refuses()
    print(f"\n{'=' * 72}")
    print("Storage is one profile per class, not per arc: the class is derived")
    print("from the arc's own free-flow cost, and an individual arc is driven")
    print("far too rarely to fit a profile of its own from traces.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
