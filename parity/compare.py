"""Recursive response comparison with per-endpoint tolerance.

The comparator is the load-bearing part of the harness: one that is too lenient
reports green on a broken port, and that failure is invisible from the outside.
So it is a pure function over two decoded bodies, and it is unit-tested against
hand-written pairs rather than against a live gateway.

Two decisions worth stating. It walks **both** trees, so a key present on only
one side is a failure -- missing-field and extra-field bugs are the most common
porting defect, and a `for key in reference` walk misses half of them. And a
numeric delta that falls *within* tolerance is still recorded, as an advisory,
so drift stays quantified instead of being rounded away to silence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Verdict(IntEnum):
    """Ordered worst-last, so `max()` aggregates a set of verdicts."""

    OK = 0
    ADVISORY = 1
    FAIL = 2


@dataclass(frozen=True)
class Diff:
    """One difference between two responses."""

    path: str
    message: str
    reference: Any
    candidate: Any
    verdict: Verdict

    def __str__(self) -> str:
        return f"{self.verdict.name} {self.path}: {self.message}"


@dataclass(frozen=True)
class Tolerance:
    """How closely two values must match.

    Args:
        exact: Reject any numeric difference at all. Used where the two
            implementations proxy identical upstream bytes (`/matrix`), so a
            delta is a real signal rather than float re-formatting noise.
        abs_tol: Absolute tolerance. The default suits geographic coordinates
            in degrees: 1e-9 deg is ~0.1 mm, six orders above the ~1e-15 drift
            observed between the two JSON float parsers and six below any real
            routing difference.
        rel_tol: Relative tolerance, which carries the large magnitudes
            (distances in metres, durations in seconds).
        ignore_paths: JSON paths excluded entirely, each needing a reason in
            the rule that sets it.
    """

    exact: bool = False
    abs_tol: float = 1e-9
    rel_tol: float = 1e-12
    ignore_paths: frozenset[str] = field(default_factory=frozenset)


def _is_number(value: Any) -> bool:
    """True for JSON numbers, excluding bools.

    `isinstance(True, int)` is True in Python, so booleans must be screened out
    before any numeric path or `true` would compare equal to `1`.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numeric_diff(path: str, ref: float, cand: float, tol: Tolerance) -> list[Diff]:
    """Compare two numbers, distinguishing 'equal', 'within tolerance', 'not'."""
    if ref == cand and math.copysign(1.0, ref) == math.copysign(1.0, cand):
        return []
    if math.isnan(ref) and math.isnan(cand):
        return []
    delta = abs(ref - cand)
    allowed = 0.0 if tol.exact else max(tol.abs_tol, tol.rel_tol * max(abs(ref), abs(cand)))
    verdict = Verdict.ADVISORY if delta <= allowed else Verdict.FAIL
    return [Diff(path, f"numeric delta {delta:.3g} (allowed {allowed:.3g})", ref, cand, verdict)]


def _mapping_diff(path: str, ref: dict, cand: dict, tol: Tolerance) -> list[Diff]:
    """Compare two objects, reporting keys unique to either side."""
    diffs: list[Diff] = []
    for key in sorted(set(ref) - set(cand)):
        diffs.append(Diff(f"{path}.{key}", "present in reference, absent in candidate",
                          ref[key], None, Verdict.FAIL))
    for key in sorted(set(cand) - set(ref)):
        diffs.append(Diff(f"{path}.{key}", "present in candidate, absent in reference",
                          None, cand[key], Verdict.FAIL))
    for key in sorted(set(ref) & set(cand)):
        diffs.extend(compare(ref[key], cand[key], tol, f"{path}.{key}"))
    return diffs


def _sequence_diff(path: str, ref: list, cand: list, tol: Tolerance) -> list[Diff]:
    """Compare two arrays elementwise; a length mismatch stops the descent."""
    if len(ref) != len(cand):
        return [Diff(path, f"length {len(ref)} vs {len(cand)}", len(ref), len(cand), Verdict.FAIL)]
    diffs: list[Diff] = []
    for index, (ref_item, cand_item) in enumerate(zip(ref, cand)):
        diffs.extend(compare(ref_item, cand_item, tol, f"{path}[{index}]"))
    return diffs


def compare(reference: Any, candidate: Any, tol: Tolerance, path: str = "$") -> list[Diff]:
    """Compare two decoded JSON values.

    Args:
        reference: The incumbent implementation's value.
        candidate: The value under test.
        tol: Tolerance to apply to numbers.
        path: JSON path of the current position, used in reported diffs.

    Returns:
        Every difference found, deepest-first within each container. An empty
        list means the two values matched exactly.
    """
    if path in tol.ignore_paths:
        return []
    if _is_number(reference) and _is_number(candidate):
        return _numeric_diff(path, float(reference), float(candidate), tol)
    if type(reference) is not type(candidate):
        return [Diff(path, f"type {type(reference).__name__} vs {type(candidate).__name__}",
                     reference, candidate, Verdict.FAIL)]
    if isinstance(reference, dict):
        return _mapping_diff(path, reference, candidate, tol)
    if isinstance(reference, list):
        return _sequence_diff(path, reference, candidate, tol)
    if reference != candidate:
        return [Diff(path, "values differ", reference, candidate, Verdict.FAIL)]
    return []


def worst(diffs: list[Diff]) -> Verdict:
    """Return the most severe verdict in `diffs`, or OK when there are none."""
    return max((d.verdict for d in diffs), default=Verdict.OK)
