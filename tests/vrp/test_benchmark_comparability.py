"""`T-100` — §13.3, as data rather than as prose.

"Each variant section contributes at least one benchmark-comparable fixture so
public benchmark performance and production performance can be related." The
relation existed only in `benchmarks/instances/README.md`, which meant deleting
an instance, renaming one, or adding a variant broke nothing and told nobody.

Two ways this gate can be satisfied dishonestly, so it checks both. Pointing
every variant at the same convenient instance would pass a membership test, so
an anchor must *show the structure its variant is about* -- a `PDPTW` anchor
carrying no shipments is not a `PDPTW` anchor whatever the file's `TYPE` line
says. And a variant with nothing to compare against is a legitimate answer, so
it may record a reason instead -- but the reason is read, not merely present.

`lrc206` is why the structural half exists. It declares `TYPE: PDPTW`, and
until `T-100` it crossed as 102 independent jobs because the reader never
looked at `PICKUP_AND_DELIVERY_SECTION`. It would have passed any test that
trusted the file's own label.
"""

from __future__ import annotations

import pytest

from vrp.bench import comparable
from vrp.benchmarks import read_benchmark


def test_every_variant_has_an_anchor_or_a_written_reason():
    """§13.3, the whole of it, in one assertion."""
    covered = set(comparable.ANCHOR) | set(comparable.NO_ANCHOR)
    missing = sorted(set(comparable.variants()) - covered)

    assert not missing, (
        f"variants with neither an anchor nor a reason: {missing}. Name a file "
        "in ANCHOR, or record in NO_ANCHOR why this variant has nothing to "
        "compare against -- a variant in neither table is invisible to §13.3.")


def test_the_registry_does_not_outlive_the_catalogue():
    """A variant that stops existing should not keep an entry explaining it."""
    known = set(comparable.variants())
    stale = sorted((set(comparable.ANCHOR) | set(comparable.NO_ANCHOR)) - known)

    assert not stale, f"entries for variants the catalogue no longer has: {stale}"


def test_no_variant_is_both_anchored_and_excused():
    both = sorted(set(comparable.ANCHOR) & set(comparable.NO_ANCHOR))
    assert not both, f"anchored and excused at once: {both}"


@pytest.mark.parametrize("variant", sorted(comparable.ANCHOR))
def test_an_anchor_is_an_instance_that_still_reads(variant: str):
    """A renamed or deleted file has to fail here rather than at a demo."""
    path = comparable.anchor_path(variant)
    assert path.exists(), f"{variant}'s anchor {path.name} is not in the repo"

    benchmark = read_benchmark(path)
    assert benchmark.problem.orders, f"{path.name} read as an empty problem"


@pytest.mark.parametrize("variant", sorted(comparable.ANCHOR))
def test_an_anchor_shows_the_structure_its_variant_is_about(variant: str):
    """The half that stops every variant pointing at one convenient file.

    Structure is read out of the crossed `Problem`, not out of the file's
    `TYPE` line, because the defect this was written for is exactly a file
    whose `TYPE` said `PDPTW` while its problem said otherwise.
    """
    benchmark = read_benchmark(comparable.anchor_path(variant))
    shown = comparable.structure(benchmark)
    wanted = comparable.DEFINING[variant]

    assert wanted <= shown, (
        f"{variant}'s anchor {comparable.ANCHOR[variant]} shows {sorted(shown)} "
        f"but {variant} is about {sorted(wanted)}; missing "
        f"{sorted(wanted - shown)}. An anchor that does not exhibit its "
        "variant relates nothing to anything.")


@pytest.mark.parametrize("variant", sorted(comparable.NO_ANCHOR))
def test_a_reason_says_something_a_reader_can_act_on(variant: str):
    """`NOT_AN_INSTANCE`'s rule: a placeholder is worse than a gap, because a
    gap is visible."""
    reason = comparable.NO_ANCHOR[variant]

    assert len(reason) > 60, f"{variant}'s reason is a placeholder: {reason!r}"
    assert not reason.lower().startswith(("todo", "n/a", "none")), reason


def test_the_five_advertised_variants_are_all_anchored():
    """§2 calls `TSP`, `CVRP`, `VRPTW`, `MDHVRPTW` and `PDPTW` "the five
    variants the engine advertises". Advertising one and comparing it to
    nothing is the gap §13.3 exists to close, so these five may not be
    excused -- only the four beyond them may."""
    unanchored = sorted(set(comparable.ADVERTISED) - set(comparable.ANCHOR))

    assert not unanchored, (
        f"advertised variants with no public anchor: {unanchored}")
