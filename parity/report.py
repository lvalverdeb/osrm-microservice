"""Rendering parity results.

Styled after `loadtest.run`'s per-endpoint table so the two read as parts of one
toolchain. The most actionable artifact is the curl line written for each
failing case -- optimise for that over prose.
"""

from __future__ import annotations

import json
from pathlib import Path

from parity.compare import Verdict
from parity.runner import EndpointResult

MAX_DIFFS_SHOWN = 3


def print_table(results: list[EndpointResult]) -> None:
    """Print one row per endpoint: counts by verdict, plus any note."""
    print(f"\n  {'endpoint':<16} {'n':>4} {'ok':>4} {'adv':>4} {'fail':>5}   notes")
    for result in sorted(results, key=lambda r: r.endpoint):
        counts = result.counts()
        # An unproven case compared equal only because both sides failed the
        # same way; surfacing it stops a broken run from reading as a clean one.
        note = result.note
        if counts["unproven"]:
            note = f"{counts['unproven']} case(s) proved nothing (both sides errored). {note}".strip()
        print(f"  {result.endpoint:<16} {len(result.cases):>4} {counts['ok']:>4} "
              f"{counts['advisory']:>4} {counts['fail']:>5}   {note}")


def print_failures(results: list[EndpointResult], report_dir: Path | None,
                   candidate_url: str) -> None:
    """Print each failing case, with a ready-to-paste reproduction."""
    for result in results:
        for case_result in result.cases:
            if case_result.verdict is not Verdict.FAIL:
                continue
            print(f"\n  FAIL {case_result.case.label}")
            failures = [d for d in case_result.diffs if d.verdict is Verdict.FAIL]
            for diff in failures[:MAX_DIFFS_SHOWN]:
                print(f"    {diff.path}  {diff.message}")
            if len(failures) > MAX_DIFFS_SHOWN:
                print(f"    ... and {len(failures) - MAX_DIFFS_SHOWN} more")
            print(f"    repro: {_curl(case_result.case, report_dir, candidate_url)}")


def _curl(case, report_dir: Path | None, base_url: str) -> str:
    """Build a curl line reproducing one case against the candidate."""
    if case.body is None:
        return f"curl -s '{base_url}{case.path}'"
    if report_dir is None:
        return (f"curl -s -X{case.method} '{base_url}{case.path}' "
                f"-H 'content-type: application/json' -d '{json.dumps(case.body)}'")
    return (f"curl -s -X{case.method} '{base_url}{case.path}' "
            f"-H 'content-type: application/json' -d @{report_dir}/{case.label}.request.json")


def write_artifacts(results: list[EndpointResult], report_dir: Path) -> None:
    """Write the request body of every failing case, for replay by hand."""
    report_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        for case_result in result.cases:
            if case_result.verdict is Verdict.FAIL and case_result.case.body is not None:
                path = report_dir / f"{case_result.case.label}.request.json"
                path.write_text(json.dumps(case_result.case.body, indent=2))


def write_json(results: list[EndpointResult], path: Path, meta: dict) -> None:
    """Write the full machine-readable record.

    This is what a later run diffs against to answer whether fidelity improved
    or regressed, rather than someone re-deriving it by eye.
    """
    payload = {
        "meta": meta,
        "endpoints": [
            {
                "endpoint": result.endpoint,
                "verdict": result.verdict.name,
                "note": result.note,
                "counts": result.counts(),
                "cases": [
                    {
                        "label": case_result.case.label,
                        "verdict": case_result.verdict.name,
                        "diffs": [
                            {"path": d.path, "message": d.message, "verdict": d.verdict.name,
                             "reference": _safe(d.reference), "candidate": _safe(d.candidate)}
                            for d in case_result.diffs
                        ],
                    }
                    for case_result in result.cases
                ],
            }
            for result in sorted(results, key=lambda r: r.endpoint)
        ],
    }
    path.write_text(json.dumps(payload, indent=2))


def _safe(value):
    """Reduce a value to something JSON-serialisable and small."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)[:200]


def summarise(results: list[EndpointResult]) -> tuple[int, str]:
    """Return the process exit code and a one-line summary."""
    failures = sum(r.counts()["fail"] for r in results)
    advisories = sum(r.counts()["advisory"] for r in results)
    unproven = sum(r.counts()["unproven"] for r in results)
    plural = "" if failures == 1 else "s"
    summary = f"{failures} failure{plural}, {advisories} advisories"
    if unproven:
        summary += f", {unproven} unproven"
    return (1 if failures else 0), summary
