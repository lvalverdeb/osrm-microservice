"""The gaps two catalogue entries name — UC-045, UC-175.

Both are `PARTIALLY_MODELLED`: each has a half the system supports and a half
it does not, and until now nothing pinned the boundary. An entry whose gap is
described only in prose is one nobody notices closing and nobody notices
widening — the status line and the code drift apart in silence, which is what
`tests/test_traceability.py` has been reporting these two for.

Each pair here is the same shape: a passing test for the half that works, and
a **strict** xfail for the half that does not. Strict matters — if somebody
closes the gap the xfail xpasses and *fails the suite*, which is the only way
the catalogue entry gets updated rather than quietly becoming wrong.
"""

from __future__ import annotations

import pytest

from vrp.bench import fixtures
from vrp.consistency import territories
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)

HOUR = 3600
DAY = TimeWindow(start=0, end=10 * HOUR)


# --------------------------------------------------------------------------
# UC-045 — Mobile vaccination and screening clinic siting (LRP)
#
# "Breaks: treating locations as given. The primary decision is where to go,
# which is a facility location problem with routing nested inside it."
# --------------------------------------------------------------------------

def a_campaign(sites: int = 6) -> Problem:
    """Candidate clinic sites, each with demand, reachable from one depot.

    The sites are strung out so that which subset you open changes the routing
    cost by a lot -- an instance where every choice cost the same could not
    tell a joint optimum from a sweep's first guess.
    """
    size = sites + 1
    grid = tuple(tuple(abs(i - j) * 900 for j in range(size))
                 for i in range(size))
    return Problem(
        id="clinics",
        locations=tuple(Location(id="D" if i == 0 else f"S{i}",
                                 lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                        for i in range(size)),
        orders=tuple(Order(id=f"O{i}", kind="JOB", quantities={"kg": 10},
                           delivery=StopSpec(location_id=f"S{i}",
                                             time_windows=(DAY,),
                                             service_fixed=1800))
                     for i in range(1, size)),
        vehicles=tuple(Vehicle(id=f"V{n}", capacities={"kg": 60}, shift=DAY,
                               start_location_id="D", end_location_id="D",
                               cost_per_metre=1)
                       for n in range(1, 3)),
        matrix=TravelMatrix(version="clinic", durations=grid, distances=grid))


def test_uc045_a_sweep_over_candidate_sitings_is_what_is_supported():
    """The half that works. FR-34's scenario sweep prices a *given* set of
    candidate configurations, which is a real and useful thing."""
    from vrp.scenarios import Mix, generate_scenarios, sweep

    def first_fit(day: Problem) -> dict[str, list[str]]:
        """A stand-in operational solver, as `Solve` is spelled: an assignment,
        not a Solution. Deterministic, and it spills when the fleet is short,
        which is what exercises the recourse half of a sweep."""
        assignment: dict[str, list[str]] = {v.id: [] for v in day.vehicles}
        loads = dict.fromkeys(assignment, 0)
        for order in day.orders:
            need = order.quantities["kg"]
            for vehicle in day.vehicles:
                if need <= vehicle.capacities["kg"] - loads[vehicle.id]:
                    assignment[vehicle.id].append(order.id)
                    loads[vehicle.id] += need
                    break
        return assignment

    problem = a_campaign()
    scenarios = generate_scenarios(problem, days=3, seed=1)
    mixes = [Mix(name="two-vans", vehicles=problem.vehicles),
             Mix(name="one-van", vehicles=problem.vehicles[:1])]

    results = sweep(problem, mixes, scenarios, first_fit)

    assert {result.mix for result in results} == {"two-vans", "one-van"}
    assert all(result.routing_cost > 0 for result in results), (
        "a sweep that prices every candidate at nothing is not evaluating them")


@pytest.mark.xfail(strict=True, reason=(
    "UC-045 is PARTIALLY_MODELLED and this is the missing half. Siting is a "
    "facility-location decision with routing nested inside it: which sites to "
    "open and how to serve them are one problem, and solving them separately "
    "gets a defensible answer to neither. What exists is FR-34's sweep, which "
    "prices a set of candidate configurations somebody else enumerated -- so "
    "the quality of the answer is the quality of the human's shortlist. "
    "Nothing in `vrp/` chooses the sites. Closing this means an LRP "
    "formulation, and the entry stays PARTIALLY_MODELLED until it exists."))
def test_uc045_the_system_chooses_which_sites_to_open():
    """What UC-045 actually asks for: open `k` of `n` candidate sites, chosen
    for the routing they imply rather than supplied as a shortlist."""
    from vrp import siting

    problem = a_campaign()
    chosen = siting.choose_sites(problem, open_count=3)

    assert len(chosen) == 3
    assert set(chosen) <= {f"S{i}" for i in range(1, 7)}


# --------------------------------------------------------------------------
# UC-175 — Postal walk and round design (CARP)
#
# "Breaks: zone-granularity territory design. Rounds are defined by which side
# of which street a postie walks, which is finer than any zone model
# represents."
# --------------------------------------------------------------------------

def test_uc175_zone_level_territory_design_is_what_is_supported():
    """The half that works. FR-35 draws stable, balanced, geographically
    coherent territories, which is the right tool one level up from a walk."""
    problem = fixtures.uc134_overlapping_depot_catchments()

    zones = territories(problem, count=3)

    assert len(zones) == 3
    served = [order for orders in zones.values() for order in orders]
    assert sorted(served) == sorted(order.id for order in problem.orders), (
        "a territory design that drops or duplicates work is not one")
    sizes = sorted(len(orders) for orders in zones.values())
    assert sizes[-1] - sizes[0] <= max(2, sizes[-1] // 2), (
        "the zones are not balanced, so FR-35's workload half is not met "
        "either and this test is not measuring the boundary it claims to")


@pytest.mark.xfail(strict=True, reason=(
    "UC-175 is PARTIALLY_MODELLED and this is the missing half. A postal walk "
    "is demand on *arcs* -- which side of which street -- and the domain model "
    "has only nodes: `StopSpec` names a location, and there is no way to say "
    "'service this segment' at all. Representing each street side as a node "
    "explodes the instance and still cannot express that walking a street "
    "serves it. This is the same ground UC-042 and UC-174 were declined on; "
    "UC-175 is partial rather than declined only because its zone-level half "
    "is genuinely supported. Closing this means a CARP formulation."))
def test_uc175_demand_can_be_placed_on_a_street_segment():
    """What UC-175 actually asks for: a unit of work that is a stretch of
    street, served by traversing it, rather than a point visited."""
    segment = StopSpec(location_id="Q", time_windows=(DAY,),
                       service_fixed=600)

    # There is no arc-demand spelling in the model. The entry's whole content
    # is that this is unrepresentable, so the assertion is on the model's
    # vocabulary rather than on a plan.
    assert hasattr(segment, "segment_from") and hasattr(segment, "segment_to")
