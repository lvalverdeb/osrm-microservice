"""Proving a GPU is optional, on a machine that has none.

Demonstrates the accelerator profile landed for E-67/T-67 (NFR-09, §7.3):

    vrp.accelerate   the flag, and what happens on each side of it

`NFR-09`: "The optimisation core MUST run on commodity CPU. GPU acceleration
MAY be an optional accelerator profile, never a hard dependency."

That is a **negative** requirement, and the interesting thing about a negative
requirement is where you check it. A GPU host can only show the accelerator
works; it cannot show the system runs without one. This machine has no GPU and
no `cuopt` installed, which makes it the right place to demonstrate exactly
what NFR-09 asks.

Four things, in order:

1. **Off is untouched.** Not "tried and failed" -- the solve path never asks
   the import system for the library at all, which is checked here the same
   way the tests check it: with a watcher on the import machinery.

2. **Off changes no plan.** The same instance, the same engines, the same
   stops in the same order.

3. **On, without the library.** A configuration mistake rather than an
   outage -- and reported, because an operator who switched it on needs to
   find out it never came on.

4. **What is not claimed.** No cuOpt has ever run against this code. The
   engine refuses by name rather than returning a plan nobody has checked.

Runs offline.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/infra/accelerator_profile.py
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.accelerate import (
    AcceleratorUnavailable,
    accelerated_portfolio,
    cuopt_engine,
    describe_accelerator,
)
from vrp.bench import fixtures
from vrp.portfolio import Portfolio, run_portfolio
from vrp.solve.pyvrp_adapter import solve as pyvrp_solve


class Watcher:
    """Records every module the import system is asked for."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def find_spec(self, fullname, path=None, target=None):
        self.asked.append(fullname)


def cpu() -> list[Portfolio]:
    return [Portfolio(name="pyvrp", solve=pyvrp_solve)]


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def off_is_untouched() -> None:
    heading("1.", "With the profile off, nothing looks for a GPU library")
    watcher = Watcher()
    sys.meta_path.insert(0, watcher)
    try:
        importlib.util.find_spec("a_module_nobody_has")
        portfolio = accelerated_portfolio(cpu(), enabled=False)
        run_portfolio(fixtures.uc070_single_order_single_vehicle(), portfolio)
        describe_accelerator(enabled=False)
    finally:
        sys.meta_path.remove(watcher)

    looked_for = [name for name in watcher.asked
                  if name.split(".")[0] == "cuopt"]
    print(f"\n   modules the import system was asked for during a solve: "
          f"{len(watcher.asked)}")
    print(f"   of those, anything under 'cuopt': {len(looked_for)}")
    print(f"   (the watcher is attached: it saw "
          f"{'a_module_nobody_has' in watcher.asked})")
    print("\n   Not a guarded import that fails quietly. Off means the code")
    print("   is never reached, so a broken CUDA install on a CPU host cannot")
    print("   raise something an except clause did not expect.")


def off_changes_no_plan() -> None:
    heading("2.", "With the profile off, the plan is the CPU plan")
    problem = fixtures.uc075_delivery_station_sequencing()
    plain = run_portfolio(problem, cpu())
    guarded = run_portfolio(problem, accelerated_portfolio(cpu(),
                                                           enabled=False))
    same = ([s.location_id for r in plain.best.routes for s in r.steps]
            == [s.location_id for r in guarded.best.routes for s in r.steps])
    print(f"\n      {'run':28s} {'winner':>8s} {'score':>10s}")
    for label, outcome in (("plain CPU portfolio", plain),
                           ("through the profile, off", guarded)):
        print(f"      {label:28s} {outcome.winner:>8s} "
              f"{outcome.scores[outcome.winner]:10d}")
    print(f"\n   identical sequence of stops: {same}")


def on_without_the_library() -> None:
    heading("3.", "With the profile on, and no library to switch on")
    report = describe_accelerator(enabled=True)
    print(f"\n      enabled:   {report.enabled}")
    print(f"      available: {report.available}")
    print(f"      active:    {report.active}")
    print(f"\n      {report.reason}")
    outcome = run_portfolio(fixtures.uc075_delivery_station_sequencing(),
                            accelerated_portfolio(cpu(), enabled=True))
    print(f"\n   the round still plans, on {outcome.winner}.")
    print("   A silent fallback would satisfy NFR-09's letter and hide that")
    print("   it did. The reason above is the difference.")


def what_is_not_claimed() -> None:
    heading("4.", "What has not been demonstrated, and says so")
    text = ""
    try:
        cuopt_engine(fixtures.uc070_single_order_single_vehicle())
    except AcceleratorUnavailable as refusal:
        text = str(refusal)
    print()
    for line in textwrap.wrap(text, width=64):
        print(f"      {line}")
    print("\n   §7.3 rejects an engine that raises rather than treating it as")
    print("   fatal, so this is a declined member of a portfolio -- exactly")
    print("   the shape NFR-09 asks the accelerator to have.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nNFR-09 and §7.3. No GPU here, which is why this is the right host.")
    off_is_untouched()
    off_changes_no_plan()
    on_without_the_library()
    what_is_not_claimed()
    print(f"\n{'=' * 72}")
    print("A negative requirement is proved on the machine that lacks the")
    print("thing. Showing cuOpt earns its hardware needs the hardware: T-84.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
