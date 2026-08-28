"""Shadow mode and canary rollout — §11.4, T-65, E-65.

§11.4 opens with the sentence the whole task serves: "Benchmarks validate the
algorithm; only production validates the model." Then three stages:

* **Shadow mode.** "Produce plans daily without executing them; measure the gap
  between the shadow plan and the executed plan, and interrogate every large
  divergence."
* **Canary.** "One depot, one month, with explicit rollback criteria agreed in
  advance."
* **Plan adherence.** "This is the metric that tells you whether the model is
  right" -- T-61's, reused rather than reinvented.

**"Agreed in advance" is the load-bearing phrase**, and it is the one a
rollout tool can actually enforce. Criteria chosen after seeing the results are
not criteria; they are a rationalisation, and the failure mode is entirely
ordinary -- the run comes in at 4% worse, somebody says "well, 5% was always the
real line", and the canary has proved nothing. So the criteria are fixed at
construction, they are fingerprinted, and the decision carries the fingerprint
so a reader can tell whether the bar moved.

**A canary is one depot.** Evaluating it against the whole fleet's data would
make it a rollout with extra steps, and the point of a canary is that the blast
radius is bounded.

**Any criterion failing is a no-go.** Not a score, not a majority. §11.4 calls
them "rollback criteria", and a criterion that can be outvoted is a preference.

The half this cannot deliver is the run itself: "one depot canary run completed
with written go/no-go" needs a depot and a month. What is here is the tooling
and the written decision it produces.
"""

from __future__ import annotations

import pytest

from vrp.adherence import ExecutedRoute
from vrp.model import (
    Location,
    Order,
    Problem,
    Route,
    Solution,
    Step,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.rollout import (
    Canary,
    Criterion,
    decide,
    divergences,
    shadow,
)

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 600


def problem(stops: int = 4) -> Problem:
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    return Problem(
        id="rollout",
        locations=tuple(Location(id="D" if i == 0 else f"C{i}",
                                 lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                        for i in range(size)),
        orders=tuple(Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
                           delivery=StopSpec(location_id=f"C{i}",
                                             time_windows=(DAY,),
                                             service_fixed=60))
                     for i in range(1, size)),
        vehicles=(Vehicle(id="V1", capacities={"kg": 100}, shift=DAY,
                          start_location_id="D", end_location_id="D",
                          cost_per_metre=1),),
        matrix=TravelMatrix(version="r", durations=grid, distances=grid))


def plan(instance: Problem, order_ids: list[str]) -> Solution:
    index = {loc.id: loc.matrix_index for loc in instance.locations}
    steps = [Step(type="START", location_id="D", arrival=0, start_service=0,
                  departure=0)]
    clock, here = 0, index["D"]
    for order_id in order_ids:
        stop = instance.order(order_id).delivery
        there = index[stop.location_id]
        clock += instance.matrix.duration(here, there)
        steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                          order_id=order_id, arrival=clock, start_service=clock,
                          departure=clock + 60))
        clock, here = clock + 60, there
    clock += instance.matrix.duration(here, index["D"])
    steps.append(Step(type="END", location_id="D", arrival=clock,
                      start_service=clock, departure=clock))
    return Solution(problem_id=instance.id,
                    routes=(Route(vehicle_id="V1", steps=tuple(steps)),),
                    unassigned=(), objective_breakdown={}, status="FEASIBLE")


def drove(sequence: list[str], depot: str = "D1") -> ExecutedRoute:
    return ExecutedRoute(
        vehicle_id="V1", driver_id="ana", depot_id=depot, territory="north",
        sequence=tuple(sequence),
        arrivals={o: 600 * (n + 1) for n, o in enumerate(sequence)})


ORDER = ["O1", "O2", "O3", "O4"]
REVERSED = list(reversed(ORDER))


# --------------------------------------------------------------------------
# §11.4: shadow mode
# --------------------------------------------------------------------------

def test_a_shadow_day_compares_the_plan_with_what_happened():
    instance = problem()
    days = shadow(instance, lambda day: plan(instance, ORDER),
                  [drove(ORDER)])

    assert len(days) == 1
    assert days[0].dissimilarity == 0


def test_a_shadow_plan_that_differs_shows_a_gap():
    instance = problem()
    days = shadow(instance, lambda day: plan(instance, ORDER),
                  [drove(REVERSED)])

    assert days[0].dissimilarity > 0


def test_the_shadow_plan_is_never_executed():
    """§11.4: "Produce plans daily without executing them". The tool takes what
    actually happened as input and returns a comparison; there is deliberately
    no path by which a shadow plan reaches a vehicle."""
    instance = problem()
    executed = [drove(REVERSED)]
    before = [route.sequence for route in executed]

    shadow(instance, lambda day: plan(instance, ORDER), executed)

    assert [route.sequence for route in executed] == before


def test_large_divergences_are_surfaced_for_interrogation():
    """§11.4: "interrogate every large divergence". Not every divergence -- a
    list that flagged all of them would be a list nobody reads."""
    instance = problem()
    days = shadow(instance, lambda day: plan(instance, ORDER),
                  [drove(ORDER), drove(REVERSED), drove(ORDER)])

    flagged = divergences(days, threshold=500)

    assert len(flagged) == 1
    assert flagged[0].dissimilarity > 500


def test_nothing_is_flagged_when_the_model_matches_reality():
    instance = problem()
    days = shadow(instance, lambda day: plan(instance, ORDER),
                  [drove(ORDER)] * 5)

    assert divergences(days, threshold=500) == []


# --------------------------------------------------------------------------
# §11.4: criteria agreed in advance
# --------------------------------------------------------------------------

def test_a_canary_is_scoped_to_one_depot():
    """§11.4: "One depot, one month". Evaluating the whole fleet would make it
    a rollout with extra steps, and the point of a canary is a bounded blast
    radius."""
    instance = problem()
    canary = Canary(depot_id="D1", criteria=(
        Criterion(name="adherence", limit=500),))
    days = shadow(instance, lambda day: plan(instance, ORDER),
                  [drove(ORDER, depot="D1"), drove(REVERSED, depot="D2")])

    decision = decide(canary, days)

    assert decision.days_considered == 1


def test_criteria_are_fingerprinted():
    """The enforcement of "agreed in advance". A decision carrying the
    fingerprint of the criteria it was judged against lets a reader tell
    whether the bar moved between agreeing and reporting."""
    tight = Canary(depot_id="D1",
                   criteria=(Criterion(name="adherence", limit=100),))
    loose = Canary(depot_id="D1",
                   criteria=(Criterion(name="adherence", limit=900),))

    assert tight.fingerprint != loose.fingerprint
    assert tight.fingerprint == Canary(
        depot_id="D1",
        criteria=(Criterion(name="adherence", limit=100),)).fingerprint


def test_the_decision_carries_the_fingerprint_it_was_judged_against():
    instance = problem()
    canary = Canary(depot_id="D1",
                    criteria=(Criterion(name="adherence", limit=500),))
    days = shadow(instance, lambda day: plan(instance, ORDER),
                  [drove(ORDER)])

    assert decide(canary, days).fingerprint == canary.fingerprint


def test_a_canary_with_no_criteria_is_refused():
    """A canary that cannot fail is not a canary. §11.4 says "explicit rollback
    criteria agreed in advance"; none is not explicit."""
    with pytest.raises(ValueError, match="criteria"):
        Canary(depot_id="D1", criteria=())


# --------------------------------------------------------------------------
# The go / no-go
# --------------------------------------------------------------------------

def test_a_clean_run_is_a_go():
    instance = problem()
    canary = Canary(depot_id="D1",
                    criteria=(Criterion(name="adherence", limit=500),))
    days = shadow(instance, lambda day: plan(instance, ORDER),
                  [drove(ORDER)] * 20)

    decision = decide(canary, days)

    assert decision.go is True
    assert decision.failed == ()


def test_any_criterion_failing_is_a_no_go():
    """Not a score and not a majority. §11.4 calls them rollback criteria, and
    a criterion that can be outvoted is a preference."""
    instance = problem()
    canary = Canary(depot_id="D1", criteria=(
        Criterion(name="adherence", limit=100),
        Criterion(name="cost_delta", limit=10 ** 9)))
    days = shadow(instance, lambda day: plan(instance, ORDER),
                  [drove(REVERSED)] * 20)

    decision = decide(canary, days)

    assert decision.go is False
    assert [f.name for f in decision.failed] == ["adherence"]


def test_the_decision_says_which_criterion_failed_and_by_how_much():
    """"No-go" sends somebody to read the whole run. "Adherence 1000 against a
    limit of 100" sends them to the right place."""
    instance = problem()
    canary = Canary(depot_id="D1",
                    criteria=(Criterion(name="adherence", limit=100),))
    days = shadow(instance, lambda day: plan(instance, ORDER),
                  [drove(REVERSED)] * 20)

    failure = decide(canary, days).failed[0]

    assert failure.limit == 100
    assert failure.observed > 100


def test_a_canary_with_no_days_is_a_no_go_rather_than_a_go():
    """The silent failure this tool invites. A month where the data never
    arrived has not demonstrated anything, and a rollout tool whose default is
    "ship it" is the wrong way round.
    """
    instance = problem()
    canary = Canary(depot_id="D1",
                    criteria=(Criterion(name="adherence", limit=500),))

    decision = decide(canary, shadow(instance,
                                     lambda day: plan(instance, ORDER), []))

    assert decision.go is False
    assert "no days" in decision.summary.lower()


def test_a_short_run_is_a_no_go():
    """§11.4 says one month. Three good days is not a month, and a canary that
    passed on them would be a canary that measured nothing."""
    instance = problem()
    canary = Canary(depot_id="D1",
                    criteria=(Criterion(name="adherence", limit=500),),
                    minimum_days=20)
    days = shadow(instance, lambda day: plan(instance, ORDER),
                  [drove(ORDER)] * 3)

    decision = decide(canary, days)

    assert decision.go is False
    assert "3" in decision.summary


# --------------------------------------------------------------------------
# T-65's definition of done
# --------------------------------------------------------------------------

def test_the_decision_is_written_down():
    """T-65: "One depot canary run completed with written go/no-go".

    The written half: a decision that reads as a paragraph a human can put in a
    change record, naming the depot, the days, the criteria fingerprint and
    every criterion with its limit and what was observed. A boolean is not a
    go/no-go, it is the answer to one.
    """
    instance = problem()
    canary = Canary(depot_id="D1", criteria=(
        Criterion(name="adherence", limit=500),
        Criterion(name="cost_delta", limit=1_000)), minimum_days=20)
    days = shadow(instance, lambda day: plan(instance, ORDER),
                  [drove(ORDER)] * 20)

    decision = decide(canary, days)
    written = decision.summary

    assert "D1" in written
    assert "20" in written
    assert canary.fingerprint[:8] in written
    assert "adherence" in written and "cost_delta" in written


def test_the_written_decision_is_the_same_every_time():
    """CON-4, and a change record whose text moves between readings is one
    nobody can cite."""
    instance = problem()
    canary = Canary(depot_id="D1",
                    criteria=(Criterion(name="adherence", limit=500),),
                    minimum_days=1)
    days = shadow(instance, lambda day: plan(instance, ORDER),
                  [drove(ORDER)] * 5)

    assert decide(canary, days).summary == decide(canary, days).summary
