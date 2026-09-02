"""The optional GPU accelerator profile — NFR-09, §7.3, T-67.

`NFR-09` is a **negative** requirement: "The optimisation core MUST run on
commodity CPU. GPU acceleration MAY be an optional accelerator profile, never a
hard dependency." The thing to demonstrate is therefore an absence — that
nothing in the solve path reaches for a GPU library — and the right machine to
demonstrate it on is one with no GPU and no `cuopt` installed, which is this
one.

T-67 was filed blocked on "needs GPU hardware not present in this environment".
Its definition of done asks for a feature flag and an unaffected CPU path when
disabled. Neither needs a GPU; what needs one is showing cuOpt is *fast*, which
is a benchmark and belongs to §11.

What is deliberately **not** claimed here: that the cuOpt adapter works. No
cuOpt has ever run against this code. `cuopt_engine` refuses by name until
somebody with the hardware validates it, which is `T-84`.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

from vrp.accelerate import (
    AcceleratorUnavailable,
    accelerated_portfolio,
    cuopt_engine,
    describe_accelerator,
)
from vrp.bench import fixtures
from vrp.portfolio import Portfolio, run_portfolio
from vrp.solve.pyvrp_adapter import solve as pyvrp_solve


class ImportWatcher:
    """Records every module the import system is asked for."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def find_module(self, fullname, path=None):  # pragma: no cover - shim
        return self.find_spec(fullname, path)

    def find_spec(self, fullname, path=None, target=None):
        self.asked.append(fullname)


def cpu_portfolio() -> list[Portfolio]:
    return [Portfolio(name="pyvrp", solve=pyvrp_solve)]


# --------------------------------------------------------------------------
# NFR-09: never a hard dependency
# --------------------------------------------------------------------------

def test_the_module_imports_on_a_machine_with_no_gpu_library():
    """This test passing on this machine is the requirement, not a formality."""
    assert "cuopt" not in sys.modules
    assert describe_accelerator().available is False


def test_nothing_reaches_for_the_gpu_library_while_the_profile_is_off():
    """The strongest form of "never a hard dependency": the import is never
    even attempted, so a machine without the library cannot be slowed, warned
    or broken by code that is switched off."""
    watcher = ImportWatcher()
    sys.meta_path.insert(0, watcher)
    try:
        # A module nobody has, to prove the watcher is attached: without this
        # the assertion below passes on a run where the hook saw nothing at
        # all, which is the shape this test failed in when it was written.
        importlib.util.find_spec("a_module_this_repository_does_not_have")
        portfolio = accelerated_portfolio(cpu_portfolio(), enabled=False)
        run_portfolio(fixtures.uc070_single_order_single_vehicle(), portfolio)
        # Reporting is part of the off path too: asking what the accelerator
        # would do must not probe for it either, or "off" quietly means
        # "looked for it and did not find it".
        describe_accelerator(enabled=False)
    finally:
        sys.meta_path.remove(watcher)

    assert "a_module_this_repository_does_not_have" in watcher.asked, (
        "the watcher is not attached, so seeing no cuopt import means nothing")
    assert not [name for name in watcher.asked if name.split(".")[0] == "cuopt"]


def test_the_portfolio_is_untouched_when_the_profile_is_off():
    """"CPU path unaffected when disabled", read strictly: the same engines,
    in the same order, as the same objects."""
    base = cpu_portfolio()
    assert accelerated_portfolio(base, enabled=False) == base


def test_the_plan_is_identical_with_the_profile_off():
    problem = fixtures.uc075_delivery_station_sequencing()
    plain = run_portfolio(problem, cpu_portfolio())
    guarded = run_portfolio(problem, accelerated_portfolio(cpu_portfolio(),
                                                           enabled=False))

    assert plain.winner == guarded.winner
    assert plain.scores == guarded.scores
    assert [step.location_id for route in plain.best.routes
            for step in route.steps] == \
           [step.location_id for route in guarded.best.routes
            for step in route.steps]


# --------------------------------------------------------------------------
# Enabled, on hardware that has none
# --------------------------------------------------------------------------

def test_enabling_it_without_the_library_still_solves_on_the_cpu():
    """NFR-09 again. Asking for the accelerator on a machine that has none is
    a configuration mistake, not an outage."""
    portfolio = accelerated_portfolio(cpu_portfolio(), enabled=True)
    outcome = run_portfolio(fixtures.uc075_delivery_station_sequencing(),
                            portfolio)

    assert outcome.winner == "pyvrp"
    assert outcome.best is not None


def test_falling_back_is_reported_rather_than_silent():
    """A silent fallback is the dangerous version: an operator who switched the
    accelerator on has no way to learn it never came on."""
    report = describe_accelerator(enabled=True)

    assert report.enabled is True
    assert report.available is False
    assert report.active is False
    assert "cuopt" in report.reason.lower()


def test_the_engine_itself_refuses_rather_than_pretending():
    """`cuopt_engine` has never been run against real hardware, and says so
    instead of returning a plan nobody has checked."""
    with pytest.raises(AcceleratorUnavailable, match="never"):
        cuopt_engine(fixtures.uc070_single_order_single_vehicle())


# --------------------------------------------------------------------------
# Enabled, with the library present
# --------------------------------------------------------------------------

def test_the_profile_is_added_when_the_library_is_there(monkeypatch):
    """The wiring, exercised with a stand-in module.

    A stand-in rather than the real thing, and the difference is stated rather
    than papered over: this shows the profile is *appended* when `cuopt`
    imports, which is all the flag is responsible for.
    """
    monkeypatch.setitem(sys.modules, "cuopt", object())
    base = cpu_portfolio()
    portfolio = accelerated_portfolio(base, enabled=True)

    assert [engine.name for engine in portfolio] == ["pyvrp", "cuopt"]
    assert describe_accelerator(enabled=True).active is True


def test_the_cpu_engines_survive_the_accelerator_being_added(monkeypatch):
    """§7.3's portfolio is the point: the accelerator joins it rather than
    replacing it, so a GPU that produces nothing costs only its own slot."""
    monkeypatch.setitem(sys.modules, "cuopt", object())
    portfolio = accelerated_portfolio(cpu_portfolio(), enabled=True)

    outcome = run_portfolio(fixtures.uc075_delivery_station_sequencing(),
                            portfolio)
    assert outcome.winner == "pyvrp", (
        "the accelerator refused, as it must without hardware, and the CPU "
        "engine still has to win rather than the portfolio failing")
    assert "cuopt" in outcome.rejected
