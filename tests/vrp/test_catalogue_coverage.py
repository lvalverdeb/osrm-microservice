"""The catalogue as a gate — `CAT-VRP-003` §13.6, §0.5, §0.6.

§13.6: "The coverage matrix is regenerated in CI from requirement tags on
fixtures, so a requirement landing without a scenario is reported rather than
passing unnoticed." This is that, inverted and made to fail: rather than
publishing a matrix nobody reads, each cross-reference the catalogue depends on
is an assertion.

Four of them are hard, and the choice of which is the whole design.

* A scenario in scope with no fixture fails. Otherwise the corpus is whatever
  somebody happened to write.
* A fixture with no scenario fails. Otherwise a fixture outlives the entry it
  was built for and nobody notices.
* A requirement cited by an entry and defined nowhere fails. v2.1 fixed a batch
  of these by hand once; this is that check, run continuously.
* A requirement defined and cited by nothing fails, against the set §0.6
  documents. That is §13.6's sentence exactly.

Tier coverage does not fail. P2 means "must not be architecturally excluded",
and gating on it would make the catalogue unextendable -- adding a scenario
would break the build, so nobody would add one.
"""

from __future__ import annotations

from vrp.bench.catalogue import (
    by_tier,
    citing,
    coverage,
    declined,
    load,
    operational,
    pathological,
)
from vrp.bench.fixtures import FIXTURES, NOT_AN_INSTANCE

SCENARIOS = load()

# Empty, and that is the interesting state rather than a vacuous one. `FR-32`
# was the last requirement with no operation behind it; §0.6 settled it on
# `UC-171`, where an absence asks exactly what vehicle-count minimisation
# answers. Every requirement the design document defines is now something a
# named real operation asked for, and this assertion is what keeps that true:
# the next requirement written without a scenario fails here.
UNEVIDENCED_BY_AGREEMENT: frozenset[str] = frozenset()


# --------------------------------------------------------------------------
# The extract itself
# --------------------------------------------------------------------------

def test_the_extract_matches_the_documents_own_counts():
    """§0.1 guarantees generated indexes "cannot disagree with the content"."""
    assert len(SCENARIOS) == 157
    assert len(operational(SCENARIOS)) == 142
    assert len(pathological(SCENARIOS)) == 15
    assert len({s.id for s in SCENARIOS}) == len(SCENARIOS), "ids are unique"


def test_every_scenario_carries_the_fields_an_entry_is_judged_on():
    """§0.1's uniform schema, as a property rather than a promise."""
    for scenario in SCENARIOS:
        assert scenario.tier in {"P0", "P1", "P2"}, scenario.id
        assert scenario.status in {"MODELLED", "PARTIALLY_MODELLED",
                                   "NOT_MODELLED"}, scenario.id
        for field in ("description", "binds", "breaks"):
            assert len(getattr(scenario, field)) >= 20, f"{scenario.id}.{field}"


# --------------------------------------------------------------------------
# Fixtures against scenarios, both directions
# --------------------------------------------------------------------------

def test_every_adversarial_instance_has_a_fixture_or_a_written_reason():
    """§13.2 puts all fifteen in the fast tier on every commit, so all fifteen
    must be reachable from code rather than only from prose."""
    covered = set(FIXTURES) | set(NOT_AN_INSTANCE)
    missing = sorted({s.id for s in pathological(SCENARIOS)} - covered)

    assert not missing, (
        f"adversarial instances with no fixture: {missing}. Add a builder to "
        "vrp/bench/fixtures.py, or record why one cannot exist in "
        "NOT_AN_INSTANCE -- an entry with neither is invisible to this gate.")


def test_no_fixture_outlives_the_scenario_it_was_built_for():
    """A fixture whose entry was retired tests an operation nobody has."""
    known = {s.id for s in SCENARIOS}
    orphans = sorted((set(FIXTURES) | set(NOT_AN_INSTANCE)) - known)

    assert not orphans, (
        f"fixtures for scenarios the catalogue no longer defines: {orphans}")


def test_every_registered_fixture_builds_a_usable_instance():
    """A registry entry that raises is worse than no registry entry: it reports
    coverage the gate above then counts."""
    for uc_id, build in sorted(FIXTURES.items()):
        problem = build()
        assert problem.orders, f"{uc_id} builds an instance with no orders"
        assert problem.locations, f"{uc_id} builds an instance with no locations"


def test_a_not_an_instance_entry_states_why():
    """Naming a gap is only useful with the reason attached."""
    for uc_id, reason in NOT_AN_INSTANCE.items():
        assert len(reason) >= 40, f"{uc_id}'s reason is too short to act on"


# --------------------------------------------------------------------------
# Requirements, both directions (§0.5's fourth query)
# --------------------------------------------------------------------------

def test_no_entry_cites_a_requirement_the_design_document_does_not_define():
    """v2.1 replaced a batch of dangling references by hand. This keeps them
    replaced."""
    report = coverage(SCENARIOS)

    assert not report.dangling, (
        f"entries cite {sorted(report.dangling)}, which SDD-VRP-001 §3 does not "
        "define. Either the reference is wrong or the requirement is missing.")


def test_proposed_requirements_are_held_apart_from_real_ones():
    """§0.6: a reader who greps the design document for `FR-Pnn` "will not find
    it, which is correct". They are proposals, not dangling references."""
    report = coverage(SCENARIOS)

    assert report.proposed == {"FR-P02", "FR-P03"}, (
        f"proposed requirements in use are {sorted(report.proposed)}; §0.6's "
        "table must list every one, with the entries asking for it")
    assert not (report.proposed & report.defined), (
        "a proposed identifier that the design document now defines should be "
        "renumbered to its real FR-nn across the entries citing it")


def test_a_requirement_with_no_scenario_is_reported():
    """§13.6, in one assertion: a requirement landing without an operation
    behind it is reported rather than passing unnoticed."""
    report = coverage(SCENARIOS)

    assert report.unevidenced == UNEVIDENCED_BY_AGREEMENT, (
        f"requirements with no scenario: {sorted(report.unevidenced)}. Every "
        "requirement in §3 is meant to be something a named operation asked "
        "for, so a new one here means a requirement landed with nothing behind "
        "it -- write the scenario, or do not write the requirement.")


def test_the_requirements_every_p0_scenario_cites_are_all_implemented():
    """P0 is "must work at v1", so a P0 entry citing an unbuilt requirement is
    a contradiction the backlog should have caught."""
    blocked = {"FR-14", "FR-20"}          # T-40 and T-41, both blocked on data
    for scenario in by_tier(operational(SCENARIOS), "P0"):
        clash = set(scenario.requirements) & blocked
        assert not clash, (
            f"{scenario.id} is P0 and cites {sorted(clash)}, whose task is "
            "blocked. Either the tier or the blocker is wrong.")


# --------------------------------------------------------------------------
# Tier coverage: reported, never failed
# --------------------------------------------------------------------------

def test_the_coverage_report_is_internally_consistent(capsys):
    """The numbers are informational; that they add up is not."""
    report = coverage(SCENARIOS)
    lines = ["", "catalogue coverage", "------------------"]
    total_by_tier = 0
    for tier in ("P0", "P1", "P2"):
        entries = by_tier(operational(SCENARIOS), tier)
        with_fixture = [s for s in entries if s.id in FIXTURES]
        total_by_tier += len(entries)
        share = 100 * len(with_fixture) / len(entries) if entries else 0.0
        lines.append(f"  {tier} operational   {len(with_fixture):3d} / "
                     f"{len(entries):3d} fixtures  ({share:5.1f}%)")
    adversarial = pathological(SCENARIOS)
    lines.append(f"  adversarial      "
                 f"{len([s for s in adversarial if s.id in FIXTURES]):3d} / "
                 f"{len(adversarial):3d} fixtures  "
                 f"(+{len(NOT_AN_INSTANCE)} not an instance)")
    lines.append(f"  requirements     {len(report.cited):3d} / "
                 f"{len(report.defined):3d} cited by at least one scenario")
    lines.append("  most-cited       " + ", ".join(
        f"{r} ({len(citing(SCENARIOS, r))})"
        for r in sorted(report.cited, key=lambda r: -len(citing(SCENARIOS, r)))[:3]))
    with capsys.disabled():
        print("\n".join(lines))

    assert total_by_tier == len(operational(SCENARIOS)), (
        "every operational scenario must fall in exactly one tier")


def test_every_p0_operational_scenario_has_a_fixture():
    """§13.1: "P0 scenarios become seeded synthetic fixtures first".

    A strict xfail while Phase 3 was outstanding, promoted when it landed --
    which is what the xfail existed to force.
    """
    missing = sorted(s.id for s in by_tier(operational(SCENARIOS), "P0")
                     if s.id not in FIXTURES)

    assert not missing, (
        f"P0 scenarios with no fixture: {missing}. P0 is \"must work at v1\", "
        "so an operation with no executable instance is a claim nobody checks.")


def test_every_p0_fixture_offers_the_three_sizes_13_1_asks_for():
    """§13.1: "one per P0 scenario, at three sizes"."""
    for scenario in by_tier(operational(SCENARIOS), "P0"):
        build = FIXTURES[scenario.id]
        counts = [len(build(size=size).orders) for size in ("small", "medium", "large")]
        assert counts[0] < counts[1] < counts[2], (
            f"{scenario.id} builds {counts} at small/medium/large. The sizes "
            "must actually differ, or two thirds of the corpus measures the "
            "same instance twice.")


def test_a_proposal_below_the_evidence_bar_is_held_rather_than_written():
    """§12.2 sets the bar at three scenarios: "fewer than that is an
    observation about one customer, not evidence about a market."

    Both surviving proposals sit at two. Holding them is the decision; this
    pins it, so a third scenario arriving is what promotes them rather than
    somebody's enthusiasm.
    """
    report = coverage(SCENARIOS)

    for proposal in sorted(report.proposed):
        supporters = citing(SCENARIOS, proposal)
        assert len(supporters) < 3, (
            f"{proposal} now has {len(supporters)} scenarios "
            f"({sorted(s.id for s in supporters)}), which clears §12.2's bar. "
            "Write it into SDD-VRP-001 §3 and renumber the entries citing it.")


# --------------------------------------------------------------------------
# What the engine declines, and how it declines it
# --------------------------------------------------------------------------

def test_nothing_the_engine_declines_is_claimed_as_a_v1_requirement():
    """§10.5 calls the boundary "deliberately partial", and a boundary is only
    honest if it is drawn in the same document that makes the promises.

    A `NOT_MODELLED` entry at P0 would be the engine claiming something must
    work at v1 and recording that it does not.
    """
    for scenario in declined(SCENARIOS):
        if scenario.status == "NOT_MODELLED":
            assert scenario.tier != "P0", (
                f"{scenario.id} is P0 and NOT_MODELLED, which is a promise and "
                "its own contradiction in one entry")


def test_every_partial_or_declined_entry_says_which_half_is_missing():
    """A status without a note is a status nobody can act on: it says the
    engine falls short and not of what."""
    for scenario in declined(SCENARIOS):
        assert len(scenario.status_note) >= 40, (
            f"{scenario.id} is {scenario.status} with a note of "
            f"{len(scenario.status_note)} characters; say which half is owed")


def test_the_arc_routing_refusal_stays_a_refusal():
    """`UC-042` is described as "declined explicitly. The most common request
    the platform should refuse". A refusal that quietly acquires a fixture has
    stopped being one, and CARP demand lies on arcs -- expressing it as stops
    either explodes the instance or silently drops coverage."""
    arc_routing = [s for s in SCENARIOS if s.variant == "CARP"]

    assert arc_routing, "the CARP entries exist to be declined, not deleted"
    for scenario in arc_routing:
        assert scenario.status != "MODELLED", f"{scenario.id}"
        assert scenario.id not in FIXTURES, (
            f"{scenario.id} has a fixture, so something is being tested that "
            "the catalogue says the platform refuses")
