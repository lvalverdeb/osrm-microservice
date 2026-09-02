"""The optional GPU accelerator profile — NFR-09, §7.3, T-67.

`NFR-09`: "The optimisation core MUST run on commodity CPU. GPU acceleration
MAY be an optional accelerator profile, never a hard dependency."

That is a **negative** requirement, and this module exists to make the absence
checkable. The demonstration is that nothing in the solve path reaches for a
GPU library, and the machine that demonstrates it best is one with no GPU --
which is where the tests run.

**The flag is a parameter, not an environment variable.** Nothing else in
`vrp/` reads the environment; the library is configured by its caller and the
deployment decides at the edge. An accelerator that switched itself on because
of an ambient variable would also be one that switched itself on in a test run,
and CON-4's replayability would be a matter of what was exported that day.

**The import is not attempted while the profile is off.** Guarding a top-level
`import cuopt` with a try/except would still make every process pay for the
lookup and would still fail loudly in an environment where a broken CUDA
install raises something other than `ImportError`. Off means untouched.

**Falling back is reported.** An operator who switched the accelerator on and
got a CPU plan has to be able to learn that. A silent fallback is the dangerous
version of NFR-09's "never a hard dependency": it satisfies the letter by
degrading, and hides that it did.

**What this module does not do.** It does not claim cuOpt works. No cuOpt has
ever run against this code -- there is no GPU here -- so `cuopt_engine` refuses
by name rather than returning a plan nobody has checked. Compiling a `Problem`
into cuOpt's model, and showing it is worth the hardware, needs the hardware:
that is `T-84`. What is delivered here is exactly what T-67's definition of
done asks for -- the flag, and a CPU path that is unaffected when it is off.

Placement: **Python**, per criterion 2. It composes `vrp.portfolio`'s engine
list and changes whenever that does.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

from vrp.model import Problem, Solution
from vrp.portfolio import Portfolio

ACCELERATOR_MODULE = "cuopt"
ACCELERATOR_NAME = "cuopt"


class AcceleratorUnavailable(RuntimeError):
    """The accelerator was asked for and cannot answer."""


@dataclass(frozen=True)
class AcceleratorReport:
    """What the accelerator profile did, and why.

    Attributes:
        enabled: whether the caller asked for it.
        available: whether the library could be found.
        active: whether it will actually be in the portfolio. `enabled and
            available`, kept as its own field because the interesting case is
            the one where the first two disagree.
        reason: a sentence an operator can act on.
    """

    enabled: bool
    available: bool
    active: bool
    reason: str


def accelerator_available() -> bool:
    """Whether the GPU library can be found, without importing it.

    `find_spec` asks the import system where a module is and stops there, so a
    machine with a broken CUDA install is reported as not having it rather than
    raising out of a top-level import nobody asked for.
    """
    if ACCELERATOR_MODULE in _loaded_modules():
        return True
    try:
        return importlib.util.find_spec(ACCELERATOR_MODULE) is not None
    except (ImportError, ValueError):
        # A namespace package with a broken parent raises rather than
        # returning None. Not having it is the honest reading.
        return False


def _loaded_modules():
    import sys

    return sys.modules


def describe_accelerator(enabled: bool = False) -> AcceleratorReport:
    """Whether the accelerator will be used, and what to do if not.

    Args:
        enabled: whether the caller is asking for it.

    Returns:
        The report. Call it before a solve to log what the run will be, or
        after to explain what it was.
    """
    if not enabled:
        return AcceleratorReport(
            enabled=False, available=False, active=False,
            reason="the accelerator profile is off; the portfolio is the "
                   "commodity-CPU one NFR-09 requires")
    available = accelerator_available()
    if not available:
        return AcceleratorReport(
            enabled=True, available=False, active=False,
            reason=f"the accelerator profile is on and {ACCELERATOR_MODULE!r} "
                   "is not installed here, so the run is on CPU; install it on "
                   "a GPU host or switch the profile off to stop asking")
    return AcceleratorReport(
        enabled=True, available=True, active=True,
        reason=f"{ACCELERATOR_MODULE!r} is present and joins the portfolio "
               "alongside the CPU engines, never in place of them")


def accelerated_portfolio(engines: list[Portfolio],
                          enabled: bool = False) -> list[Portfolio]:
    """The portfolio, with the accelerator appended if it is on and present.

    Args:
        engines: the commodity-CPU portfolio. Returned unchanged when the
            profile is off -- the same list object's contents, in order, so
            "CPU path unaffected" is a thing a test can assert rather than
            infer.
        enabled: whether to ask for the accelerator.

    Returns:
        The engines to run. §7.3's protocol scores every member on the
        canonical objective, so an accelerator that returns nothing costs its
        own slot and no more.

    Appended rather than substituted. `NFR-09` forbids the GPU being a
    dependency, and a portfolio that dropped its CPU members when a GPU
    appeared would have made it one for the duration of that run.
    """
    if not enabled:
        return engines
    if not accelerator_available():
        return engines
    return [*engines, Portfolio(name=ACCELERATOR_NAME, solve=cuopt_engine)]


def cuopt_engine(problem: Problem) -> Solution:
    """The cuOpt member of the portfolio. Refuses, on purpose.

    Args:
        problem: the instance, unused.

    Raises:
        AcceleratorUnavailable: always. No cuOpt has ever been run against this
            code -- there is no GPU in the environment it was written in -- and
            an adapter compiled from the documentation alone would be a plan
            nobody has checked wearing an engine's name. §7.3 rejects an engine
            that raises rather than treating it as fatal, so the portfolio
            behaves exactly as it does for any engine that declines an
            instance.

    Writing the model compilation is `T-84`, which genuinely needs the
    hardware: the thing it has to demonstrate is that the accelerator is worth
    the machine, and that is not a claim anybody can make from here.
    """
    raise AcceleratorUnavailable(
        f"{ACCELERATOR_NAME} has never been run against this code: there is no "
        "GPU in the environment it was written in, so the adapter is not "
        "implemented rather than implemented untested (T-84). The CPU "
        "portfolio is unaffected -- NFR-09 makes the accelerator optional "
        "precisely so this is a declined engine and not an outage")
