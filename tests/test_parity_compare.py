"""Unit tests for the parity comparator.

These matter more than they look. A comparator that is too lenient reports a
broken port as green, and that is the one failure mode of the whole differential
design that is invisible from the outside -- so it is pinned here, where it
costs milliseconds and needs no engine.
"""

from __future__ import annotations

from parity.compare import Diff, Tolerance, Verdict, compare, worst

LOOSE = Tolerance()
EXACT = Tolerance(exact=True)


def test_identical_values_produce_no_diffs():
    assert compare({"code": "Ok", "n": 1}, {"code": "Ok", "n": 1}, LOOSE) == []


def test_key_order_is_not_a_difference():
    assert compare({"a": 1, "b": 2}, {"b": 2, "a": 1}, LOOSE) == []


def test_missing_key_fails():
    diffs = compare({"a": 1, "b": 2}, {"a": 1}, LOOSE)
    assert worst(diffs) is Verdict.FAIL
    assert diffs[0].path == "$.b"


def test_extra_key_fails():
    """The both-trees walk is the harness's main defence against unknown drift."""
    diffs = compare({"a": 1}, {"a": 1, "surprise": 2}, LOOSE)
    assert worst(diffs) is Verdict.FAIL
    assert diffs[0].path == "$.surprise"


def test_last_ulp_geometry_is_advisory_not_failure():
    """The known /route divergence: ~1e-15 deg, far below any real difference."""
    ref = {"coordinates": [[10.050849173924503, 9.9]]}
    cand = {"coordinates": [[10.050849173924504, 9.9]]}
    diffs = compare(ref, cand, LOOSE)
    assert worst(diffs) is Verdict.ADVISORY
    assert diffs[0].path == "$.coordinates[0][0]"


def test_within_tolerance_is_still_recorded():
    """Drift stays quantified rather than rounded into silence."""
    diffs = compare({"d": 1.0}, {"d": 1.0 + 1e-13}, LOOSE)
    assert len(diffs) == 1
    assert diffs[0].verdict is Verdict.ADVISORY


def test_real_difference_fails():
    diffs = compare({"distance": 1000.0}, {"distance": 1001.0}, LOOSE)
    assert worst(diffs) is Verdict.FAIL


def test_exact_tolerance_rejects_any_delta():
    """/matrix was byte-identical; keep it that way so a diff stays a signal."""
    diffs = compare({"d": 1.0}, {"d": 1.0 + 1e-15}, EXACT)
    assert worst(diffs) is Verdict.FAIL


def test_negative_zero_differs_from_zero():
    """JSON round-trips -0.0; a port that normalises it has changed the body."""
    diffs = compare({"d": 0.0}, {"d": -0.0}, LOOSE)
    assert worst(diffs) is Verdict.ADVISORY


def test_null_is_not_absent():
    """OSRM uses null for unreachable matrix pairs; dropping the key is a bug."""
    diffs = compare({"d": None}, {}, LOOSE)
    assert worst(diffs) is Verdict.FAIL


def test_null_matches_null():
    assert compare({"d": None}, {"d": None}, LOOSE) == []


def test_bool_is_not_a_number():
    """isinstance(True, int) is True in Python -- screen it or true == 1."""
    diffs = compare({"flag": True}, {"flag": 1}, LOOSE)
    assert worst(diffs) is Verdict.FAIL


def test_type_change_fails():
    diffs = compare({"n": 1}, {"n": "1"}, LOOSE)
    assert worst(diffs) is Verdict.FAIL


def test_list_length_mismatch_fails_without_descending():
    diffs = compare({"xs": [1, 2, 3]}, {"xs": [1, 2]}, LOOSE)
    assert len(diffs) == 1
    assert diffs[0].path == "$.xs"


def test_strings_compare_exactly():
    diffs = compare({"name": "Calle 5"}, {"name": "Calle 6"}, LOOSE)
    assert worst(diffs) is Verdict.FAIL


def test_networkx_edge_key_rename_is_caught():
    """networkx 3.6 emits `edges`; older versions emitted `links`. The response
    shape of /matrix-graph is set by the resolved library version, not by this
    repo's code, so a lockfile refresh can change it silently."""
    diffs = compare({"links": []}, {"edges": []}, LOOSE)
    assert worst(diffs) is Verdict.FAIL
    assert {d.path for d in diffs} == {"$.links", "$.edges"}


def test_error_detail_shapes_are_distinguished():
    """`detail` is polymorphic: a dict from _parse_osrm_error, a plain string
    for internal errors, a list for pydantic 422."""
    structured = {"detail": {"code": "NoRoute", "message": "Impossible route"}}
    plain = {"detail": "Routing service error"}
    assert worst(compare(structured, plain, LOOSE)) is Verdict.FAIL
    assert compare(structured, structured, LOOSE) == []


def test_nested_paths_are_reported_usefully():
    ref = {"routes": [{"legs": [{"distance": 100.0}]}]}
    cand = {"routes": [{"legs": [{"distance": 200.0}]}]}
    diffs = compare(ref, cand, LOOSE)
    assert diffs[0].path == "$.routes[0].legs[0].distance"


def test_ignored_paths_are_skipped():
    tol = Tolerance(ignore_paths=frozenset({"$.hint"}))
    assert compare({"hint": "aaa"}, {"hint": "bbb"}, tol) == []


def test_worst_of_empty_is_ok():
    assert worst([]) is Verdict.OK


def test_worst_picks_the_most_severe():
    diffs = [
        Diff("$.a", "", 1, 1, Verdict.ADVISORY),
        Diff("$.b", "", 1, 2, Verdict.FAIL),
    ]
    assert worst(diffs) is Verdict.FAIL
