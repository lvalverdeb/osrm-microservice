"""Send it now, or wait and send it with the next three.

Demonstrates the epoch controller and must-go classifier landed for E-51/T-51
(FR-22, DYN-1, DYN-2, AC-3.1, §8.1):

    vrp.epochs      the waves, the classifier, and the guarantee
    vrp.diagnose    the reachability test it reuses rather than re-derives
    vrp.committed   T-50's locks, which hold whatever this dispatches

§8.1: "Same-day and on-demand operations are not static problems solved
repeatedly. They are sequential decision problems under uncertainty... at each
epoch the agent observes the requests known so far and must decide which to
dispatch now -- committing them to feasible routes -- and which to postpone so
they can be consolidated with requests that arrive later."

Postponing is not procrastination. A van that leaves at 09:00 with four drops
and one that leaves at 10:00 with seven are different costs, and the second is
usually the better one. What makes it a decision rather than a free lunch is
that some requests cannot wait.

Four things, in order:

1. **The waves.** A day partitioned, with a short tail rather than a rounded
   one -- because the requests in the last forty minutes are still requests.

2. **Must-go, and why it is about arrival rather than the clock.** A stop
   four minutes out with a window closing on the hour is already gone if the
   van is held to the hour.

3. **The asymmetry.** DYN-2 says "conservative by construction; false negatives
   are service failures", so every uncertain case resolves to must-go.

4. **The guarantee, against a policy actively trying to break it.** AC-3.1's
   "never postpones a must-go" is enforced by the controller, not trusted to
   the policy.

Requires a running gateway: distances are measured on the road network and
there is no straight-line fallback. `examples/.env` points at the FreeBSD jail.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/dynamic/dispatch_waves.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

# Importing config puts OSRM_API_URL into the environment, so the
# gateway this example now requires is the one `examples/.env` names.
import config  # noqa: F401
import dataset

from vrp.epochs import classify, decide, epochs, must_go
from vrp.model import (
    UNREACHABLE,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)

HOUR = 3600
DAY = TimeWindow(start=0, end=8 * HOUR)
LEG = 1800


def clock(seconds: int) -> str:
    return f"{8 + seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def instance(closes: dict[str, int]) -> Problem:
    """Real deliveries, each with the hour its window shuts.

    The windows are the subject and stay the caller's -- a cut-off is a
    commercial fact, not a geographic one. The addresses and service times are
    the corpus's, so the drive between two stops is a real drive and the
    question "can this still wait?" has a real answer.
    """
    ids = sorted(closes)
    locations, matrix, deliveries, _depot = dataset.road_sites(
        len(ids), strategy="spread", name="waves")
    return Problem(
        id="waves", locations=locations,
        orders=tuple(
            Order(id=order_id, kind="JOB", quantities={"kg": 1},
                  delivery=StopSpec(
                      location_id=f"C{n}",
                      time_windows=(TimeWindow(start=0, end=closes[order_id]),),
                      service_fixed=deliveries[n - 1]["service_minutes"] * 60))
            for n, order_id in enumerate(ids, start=1)),
        vehicles=(Vehicle(id="V1", capacities={"kg": 100}, shift=DAY,
                          start_location_id="D", end_location_id="D"),),
        matrix=matrix)


CLOSES = {"O1": 1 * HOUR, "O2": 2 * HOUR, "O3": 4 * HOUR, "O4": 8 * HOUR}


def show_the_waves() -> None:
    print("\n1. The day, in waves (DYN-1)")
    waves = epochs(TimeWindow(start=0, end=8 * HOUR + 2400), length=2 * HOUR)
    for wave in waves:
        span = wave.end - wave.start
        tail = "   <- short tail, kept" if span < 2 * HOUR else ""
        print(f"   epoch {wave.index}  {clock(wave.start)}-{clock(wave.end)}"
              f"  ({span // 60} min){tail}")
    print("   Rounding that tail away would drop the last forty minutes of the")
    print("   day and every request that arrives in them.")


def show_must_go() -> None:
    print("\n2. Must-go is about arrival, not the window's clock (DYN-2)")
    problem = instance(CLOSES)
    drives = [problem.matrix.duration(
        0, problem.location(problem.order(o).delivery.location_id).matrix_index)
        for o in sorted(CLOSES)]
    services = [problem.order(o).delivery.service_fixed for o in sorted(CLOSES)]
    print(f"   real stops: {min(drives) // 60}-{max(drives) // 60} min out, "
          f"service {min(services) // 60}-{max(services) // 60} min")
    print(f"   {'order':<7}{'window closes':>15}{'postponed to 10:00':>22}"
          f"{'arrives':>10}")

    for order_id in sorted(CLOSES):
        order = problem.order(order_id)
        node = problem.location(order.delivery.location_id).matrix_index
        arrives = 2 * HOUR + problem.matrix.duration(0, node)
        verdict = "MUST-GO" if must_go(problem, order,
                                       postponed_to=2 * HOUR) else "deferrable"
        print(f"   {order_id:<7}{clock(CLOSES[order_id]):>15}{verdict:>22}"
              f"{clock(arrives):>10}")

    drive = problem.matrix.duration(
        0, problem.location(problem.order("O2").delivery.location_id).matrix_index)
    print(f"   O2's window closes at 10:00 and it is {drive // 60} min "
          f"{drive % 60}s out, so a van")
    print(f"   held to 10:00 arrives at {clock(2 * HOUR + drive)} to a shut "
          "door. The window has")
    print("   not closed yet; the chance to use it has. Four minutes is")
    print("   enough -- the margin does not have to be large to be fatal.")


def _with_unreachable_arc(problem: Problem, node: int) -> TravelMatrix:
    """A stored copy of the matrix with one depot arc marked unreachable."""
    size = problem.matrix.size
    cells = tuple(tuple(problem.matrix.duration(i, j) for j in range(size))
                  for i in range(size))
    durations = tuple(
        tuple(UNREACHABLE if (i, j) in ((0, node), (node, 0)) else cell
              for j, cell in enumerate(row))
        for i, row in enumerate(cells))
    return TravelMatrix(version="waves-cut", durations=durations,
                        distances=durations)


def show_the_asymmetry() -> None:
    print("\n3. Conservative by construction (DYN-2)")
    problem = instance(CLOSES)

    cases = {
        "no vehicles left": replace(problem, vehicles=()),
        # A planar matrix computes its cells rather than storing them, so an
        # unreachable arc has to be punched into a materialised copy. Worth
        # seeing: a lazy matrix cannot carry MTX-5's sentinel, which is a real
        # constraint on where it can be used.
        "stop unreachable in the matrix": replace(
            problem, matrix=_with_unreachable_arc(problem, 4)),
    }
    for label, variant in cases.items():
        verdict = must_go(variant, variant.order("O4"), postponed_to=2 * HOUR)
        print(f"   {label:<34}{'MUST-GO' if verdict else 'deferrable':>12}")

    soft = replace(problem, orders=tuple(
        replace(o, delivery=replace(o.delivery, time_windows=(
            TimeWindow(start=0, end=HOUR, hardness="SOFT",
                       lateness_cost_per_sec=1),)))
        if o.id == "O4" else o for o in problem.orders))
    print(f"   {'a soft window, late but priced':<34}"
          f"{'MUST-GO' if must_go(soft, soft.order('O4'), 2 * HOUR) else 'deferrable':>12}")

    print("   The first two are not evidence that postponing is safe -- they")
    print("   are an absence of evidence, and DYN-2 says which way to resolve")
    print("   that: \"false negatives are service failures\". Calling a")
    print("   deferrable order must-go costs a slightly emptier van. Calling a")
    print("   must-go order deferrable costs a delivery that never happens.")
    print("   The soft window is the deliberate exception: §6.2 prices")
    print("   lateness rather than forbidding it, and treating every soft")
    print("   window as a wall would make the whole fleet must-go and postpone")
    print("   nothing at all.")


def show_the_guarantee() -> None:
    print("\n4. A day dispatched by a policy that wants to postpone everything")
    problem = instance(CLOSES)
    open_ids = sorted(CLOSES)
    print(f"   {'epoch':<7}{'open':<26}{'dispatched':<20}{'overruled':<20}")

    for wave in epochs(DAY, length=2 * HOUR):
        if not open_ids:
            break
        split = classify(problem, open_ids, postponed_to=wave.end)
        # `decide` takes the epoch rather than the instant it would postpone
        # to: it derives that from `epoch.end` itself, so the caller cannot
        # pass a horizon that disagrees with the wave it is deciding.
        # A `Policy` is `(open_ids, classification, epoch) -> chosen`. This
        # one chooses nothing, every wave, which is the point: what still
        # gets dispatched is what AC-3.1 refuses to let it postpone.
        decision = decide(problem, open_ids, wave,
                          policy=lambda ids, split, epoch: ())
        print(f"   {wave.index:<7}{open_ids!s:<26}"
              f"{list(decision.dispatched)!s:<20}"
              f"{list(decision.forced)!s:<20}")
        open_ids = list(decision.postponed)
        assert not any(order_id in split.must_go
                       for order_id in decision.postponed)

    print("   The policy asked to postpone everything, every time. Nothing was")
    print("   lost, because AC-3.1 is enforced in the controller rather than")
    print("   trusted to the policy -- and T-52's baselines include one that is")
    print("   literally random. A service failure must not be reachable by")
    print("   choosing a bad policy.")
    print("   `overruled` is reported rather than folded into `dispatched`: a")
    print("   policy that has to be overruled is a different finding from one")
    print("   that is merely expensive, and T-53's replayer has to tell them")
    print("   apart.")


def main() -> int:
    show_the_waves()
    show_must_go()
    show_the_asymmetry()
    show_the_guarantee()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
