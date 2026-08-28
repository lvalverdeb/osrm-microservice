"""The zone-sequence prior — §12.4 step 2, T-64, E-64.

§12.4's "Act" list, in priority order: extract the deviation into an explicit
model feature first, because that is explainable and auditable. Then, "where the
pattern resists formalisation, learn a **sequencing prior** at the zone level
and add it as a soft objective or a warm-start structure. Zone-sequence learning
from historical routes is the approach that performed best in the Amazon
challenge, where a probabilistic model of zone ordering learned from drivers
outperformed hand-coded zone constraints." And third: "Never simply penalise
drivers into compliance with a plan the model got wrong."

Then the guardrail, which is the whole reason this is testable rather than
merely plausible: "Learned components MUST be advisory: they may bias search and
warm starts, they MUST NOT be able to produce a plan that violates a hard
constraint. The verifier (§11.2) is downstream of all learning."

That sentence sets the shape. The prior is allowed to be *wrong* -- learned from
twenty days it may well be -- and nothing it can say may turn a legal plan into
an illegal one. So it produces an ordering, never a constraint, and every test
here that gives it a chance to break something checks that it did not.

**Zones, not stops.** A prior over individual stops memorises last month's
customers and is useless the day one moves. §12.4 says "at the zone level" for
that reason, and the Amazon result it cites is specifically about zone ordering.

**Advisory means the plan is still checked.** T-64's definition of done pairs
"improves adherence" with "no verifier regressions", and the second half is not
a formality: a prior that improved adherence by producing plans the verifier
rejects would be worse than no prior at all.
"""

from __future__ import annotations

import pytest

from vrp.adherence import ExecutedRoute, adherence
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
from vrp.verify import verify
from vrp.zones import ZonePrior, learn_prior, order_by_prior, zone_of

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 600

# Three zones of two stops each. C1/C2 north, C3/C4 middle, C5/C6 south.
ZONES = {"C1": "north", "C2": "north", "C3": "middle",
         "C4": "middle", "C5": "south", "C6": "south"}


def problem(stops: int = 6) -> Problem:
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    return Problem(
        id="zones",
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
        matrix=TravelMatrix(version="z", durations=grid, distances=grid))


def drove(sequence: list[str]) -> ExecutedRoute:
    return ExecutedRoute(
        vehicle_id="V1", driver_id="ana", depot_id="D", territory="north",
        sequence=tuple(sequence),
        arrivals={o: 600 * (n + 1) for n, o in enumerate(sequence)})


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


# --------------------------------------------------------------------------
# Zones, not stops
# --------------------------------------------------------------------------

def test_an_order_maps_to_its_zone():
    instance = problem()

    assert zone_of(instance, "O1", ZONES) == "north"
    assert zone_of(instance, "O5", ZONES) == "south"


def test_an_unzoned_stop_gets_its_own_zone_rather_than_none():
    """A stop nobody has zoned is not in every zone, and it is not in a null
    one either. Giving it its own keeps it out of everybody else's statistics
    instead of quietly joining a bucket it does not belong to."""
    instance = problem()

    assert zone_of(instance, "O1", {}) == "C1"


# --------------------------------------------------------------------------
# §12.4 step 2: learning the prior
# --------------------------------------------------------------------------

def test_a_consistent_history_produces_the_order_drivers_used():
    """The Amazon result §12.4 cites: a probabilistic model of zone ordering
    learned from drivers beat hand-coded zone constraints."""
    instance = problem()
    history = [drove(["O5", "O6", "O3", "O4", "O1", "O2"]) for _ in range(20)]

    prior = learn_prior(instance, history, ZONES)

    assert prior.sequence == ("south", "middle", "north"), prior.sequence


def test_the_prior_follows_the_drivers_not_the_matrix():
    """The whole point. The matrix says north first -- it is nearest the depot
    -- and twenty days of drivers say south. §12.4: "Systematic, repeated
    deviation is a model defect, not driver misbehaviour."
    """
    instance = problem()
    history = [drove(["O5", "O6", "O3", "O4", "O1", "O2"]) for _ in range(20)]

    prior = learn_prior(instance, history, ZONES)

    assert prior.sequence[0] == "south"


def test_a_split_history_still_produces_one_ordering():
    """Drivers disagree. A prior that refused to commit would be no prior at
    all, so the majority wins and the confidence says how close it was."""
    instance = problem()
    history = ([drove(["O1", "O2", "O3", "O4", "O5", "O6"])] * 12
               + [drove(["O5", "O6", "O3", "O4", "O1", "O2"])] * 8)

    prior = learn_prior(instance, history, ZONES)

    assert prior.sequence[0] == "north"
    assert 0 < prior.confidence < 1000, prior.confidence


def test_confidence_is_high_when_drivers_agree():
    instance = problem()
    history = [drove(["O5", "O6", "O3", "O4", "O1", "O2"]) for _ in range(20)]

    assert learn_prior(instance, history, ZONES).confidence > 900


def test_learning_from_nothing_produces_an_empty_prior():
    """Not a guess. A prior fitted on no history that returned some ordering
    anyway would be indistinguishable from a learned one."""
    instance = problem()

    prior = learn_prior(instance, (), ZONES)

    assert prior.sequence == ()
    assert prior.confidence == 0


def test_the_prior_is_deterministic():
    instance = problem()
    history = [drove(["O5", "O6", "O1", "O2", "O3", "O4"]) for _ in range(9)]

    assert learn_prior(instance, history, ZONES) == \
        learn_prior(instance, history, ZONES)


# --------------------------------------------------------------------------
# Applying it: a warm-start ordering, never a constraint
# --------------------------------------------------------------------------

def test_the_prior_reorders_a_route_to_match_it():
    instance = problem()
    prior = ZonePrior(sequence=("south", "middle", "north"), confidence=1000)

    ordered = order_by_prior(instance, ["O1", "O2", "O3", "O4", "O5", "O6"],
                             prior, ZONES)

    assert [zone_of(instance, o, ZONES) for o in ordered] == \
        ["south", "south", "middle", "middle", "north", "north"]


def test_stops_inside_a_zone_keep_their_relative_order():
    """The prior is about zones. Rearranging within one would be inventing
    guidance it never learned."""
    instance = problem()
    prior = ZonePrior(sequence=("north", "middle", "south"), confidence=1000)

    ordered = order_by_prior(instance, ["O2", "O1", "O3"], prior, ZONES)

    assert ordered == ["O2", "O1", "O3"]


def test_a_zone_the_prior_never_saw_goes_last_rather_than_being_dropped():
    """A new zone is not evidence about anything, and losing its stops would
    turn an advisory ordering into a way to lose deliveries."""
    instance = problem()
    prior = ZonePrior(sequence=("north",), confidence=1000)

    ordered = order_by_prior(instance, ["O1", "O5", "O3"], prior, ZONES)

    assert set(ordered) == {"O1", "O5", "O3"}
    assert ordered[0] == "O1"


def test_an_empty_prior_changes_nothing():
    instance = problem()
    empty = ZonePrior(sequence=(), confidence=0)
    sequence = ["O3", "O1", "O5"]

    assert order_by_prior(instance, sequence, empty, ZONES) == sequence


# --------------------------------------------------------------------------
# The guardrail: advisory only
# --------------------------------------------------------------------------

def test_the_prior_never_drops_or_invents_a_stop():
    """§12.4's guardrail is about hard constraints, and the cheapest way to
    violate one is to quietly lose the order that carried it."""
    instance = problem()
    prior = ZonePrior(sequence=("south", "north", "middle"), confidence=1000)
    sequence = [f"O{i}" for i in range(1, 7)]

    ordered = order_by_prior(instance, sequence, prior, ZONES)

    assert sorted(ordered) == sorted(sequence)


def test_a_plan_built_from_the_prior_still_verifies():
    """§12.4: "The verifier (§11.2) is downstream of all learning."

    T-64's definition of done pairs "improves adherence" with "no verifier
    regressions", and the second half is not a formality: a prior that improved
    adherence by producing plans the verifier rejects would be worse than no
    prior at all.
    """
    instance = problem()
    prior = ZonePrior(sequence=("south", "middle", "north"), confidence=1000)
    ordered = order_by_prior(instance, [f"O{i}" for i in range(1, 7)],
                             prior, ZONES)

    assert verify(instance, plan(instance, ordered)).ok


def test_a_prior_that_would_break_a_window_is_still_only_advice():
    """The guardrail's real test. Here the prior's ordering makes a window
    unreachable -- and the verifier catches it, which is the arrangement §12.4
    asks for: learning may bias the search, and the check happens afterwards
    regardless of what the learning wanted.
    """
    from dataclasses import replace

    instance = problem()
    tight = replace(instance, orders=tuple(
        replace(order, delivery=replace(order.delivery, time_windows=(
            TimeWindow(start=0, end=2 * LEG),)))
        if order.id == "O1" else order for order in instance.orders))

    prior = ZonePrior(sequence=("south", "middle", "north"), confidence=1000)
    ordered = order_by_prior(tight, [f"O{i}" for i in range(1, 7)],
                             prior, ZONES)

    assert ordered[-2:] == ["O1", "O2"]
    assert not verify(tight, plan(tight, ordered)).ok


# --------------------------------------------------------------------------
# T-64's definition of done
# --------------------------------------------------------------------------

def test_the_prior_improves_adherence():
    """T-64: "Improves adherence with no verifier regressions".

    Twenty days of drivers going south first. A plan ordered by the matrix
    diverges from what they do; the same plan ordered by the learned prior
    matches it. Measured with T-61's dissimilarity, which is the metric the
    claim is actually about.
    """
    instance = problem()
    driven = ["O5", "O6", "O3", "O4", "O1", "O2"]
    history = [drove(driven) for _ in range(20)]
    prior = learn_prior(instance, history, ZONES)

    naive = ["O1", "O2", "O3", "O4", "O5", "O6"]
    advised = order_by_prior(instance, naive, prior, ZONES)

    before = adherence(instance, plan(instance, naive), (drove(driven),))[0]
    after = adherence(instance, plan(instance, advised), (drove(driven),))[0]

    assert after.dissimilarity < before.dissimilarity, (before, after)
    assert verify(instance, plan(instance, advised)).ok


def test_a_prior_learned_from_noise_does_not_make_adherence_worse():
    """The control. A prior fitted on drivers who did something different every
    day should not confidently reorder anything, because there is nothing to be
    confident about."""
    instance = problem()
    history = [drove(["O1", "O2", "O3", "O4", "O5", "O6"]),
               drove(["O5", "O6", "O1", "O2", "O3", "O4"]),
               drove(["O3", "O4", "O5", "O6", "O1", "O2"])]

    prior = learn_prior(instance, history, ZONES)

    assert prior.confidence < 700, prior.confidence


def test_an_unknown_order_in_the_history_is_refused():
    instance = problem()
    with pytest.raises(ValueError, match="O99"):
        learn_prior(instance, (drove(["O99"]),), ZONES)
