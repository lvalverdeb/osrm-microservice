"""`dataset.load_kg` -- turning a fractional corpus weight into a capacity.

`vrp.model` takes integer quantities, so the conversion has to happen
somewhere, and the direction it rounds is a correctness question rather than a
tidiness one. Six examples used `max(1, round(weight_kg))`, which rounds to
nearest and so understates 42% of this corpus by up to half a kilo an item.

Understating a load is the one unsafe direction. It does not produce an illegal
plan that a verifier would catch -- it produces a legal plan for a lighter load
than the one being carried, because the model was told the wrong weight. These
tests pin the direction.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "examples" / "src"))

import dataset


def weighing(kg: float) -> dict[str, float]:
    return {"weight_kg": kg}


def test_a_load_is_never_understated():
    """The property. Rounding to nearest breaks it; rounding up cannot."""
    for grams in range(50, 30_000, 50):
        kg = grams / 1000
        assert dataset.load_kg(weighing(kg)) >= kg, kg


def test_rounding_to_nearest_would_have_understated_these():
    """The specific values the old idiom got wrong, kept as evidence."""
    for kg in (1.4, 4.47, 12.49, 29.4):
        assert round(kg) < kg          # what the six call sites did
        assert dataset.load_kg(weighing(kg)) >= kg


def test_anything_positive_still_weighs_at_least_one():
    """`max(1, ...)` guaranteed this and callers rely on it."""
    assert dataset.load_kg(weighing(0.05)) == 1
    assert dataset.load_kg(weighing(0.5)) == 1
    assert dataset.load_kg(weighing(1.0)) == 1


def test_a_whole_number_of_kilos_is_not_inflated():
    """Rounding up must not add a kilo to a weight that needed no rounding."""
    assert dataset.load_kg(weighing(4.0)) == 4
    assert dataset.load_kg(weighing(30.0)) == 30


def test_kilograms_still_cannot_separate_sub_kilo_freight():
    """Why this is a fix and not an endorsement of the unit.

    A 50 g envelope and a 950 g parcel are the same number here. Freight that
    is genuinely sub-kilogram needs a finer dimension -- grams, as
    `fleet/tw/envelope_round.py` uses -- and this helper does not pretend
    otherwise.
    """
    assert dataset.load_kg(weighing(0.05)) == dataset.load_kg(weighing(0.95))
