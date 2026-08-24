"""Driving the corpus against two gateways and judging the results."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

from parity import quality
from parity.client import Reply, send
from parity.compare import Diff, Verdict, compare, worst
from parity.corpus import Case
from parity.rules import Rule, rule_for


@dataclass
class CaseResult:
    """The verdict on one case, plus what produced it."""

    case: Case
    verdict: Verdict
    diffs: list[Diff] = field(default_factory=list)
    note: str = ""
    # True when both sides errored the same way. The comparison "passed", but it
    # established nothing -- two implementations can be broken identically, and
    # cross-comparison is structurally blind to that.
    unproven: bool = False


@dataclass
class EndpointResult:
    """Every case for one endpoint, and the endpoint's aggregate verdict."""

    endpoint: str
    cases: list[CaseResult] = field(default_factory=list)
    verdict: Verdict = Verdict.OK
    note: str = ""

    def counts(self) -> dict[str, int]:
        """Return case counts by verdict name, plus how many proved nothing."""
        tally = {"ok": 0, "advisory": 0, "fail": 0, "unproven": 0}
        for result in self.cases:
            tally[result.verdict.name.lower()] += 1
            tally["unproven"] += int(result.unproven)
        return tally


def judge_status(reference: Reply, candidate: Reply, rule: Rule) -> list[Diff]:
    """Compare status codes and content types."""
    diffs: list[Diff] = []
    if reference.status != candidate.status:
        verdict = Verdict.FAIL if rule.compare_status else Verdict.ADVISORY
        diffs.append(Diff("$status", f"{reference.status} vs {candidate.status}",
                          reference.status, candidate.status, verdict))
    if reference.content_type != candidate.content_type:
        diffs.append(Diff("$content-type", f"{reference.content_type} vs {candidate.content_type}",
                          reference.content_type, candidate.content_type, Verdict.FAIL))
    return diffs


def judge_bytes(reference: Reply, candidate: Reply) -> list[Diff]:
    """Compare two raw bodies, reporting where they first differ."""
    ref_raw, cand_raw = reference.raw or b"", candidate.raw or b""
    if ref_raw == cand_raw:
        return []
    offset = next((i for i, (a, b) in enumerate(zip(ref_raw, cand_raw)) if a != b),
                  min(len(ref_raw), len(cand_raw)))
    return [Diff("$body", f"{len(ref_raw)} vs {len(cand_raw)} bytes, first differ at {offset}",
                 len(ref_raw), len(cand_raw), Verdict.FAIL)]


def judge_quality(case: Case, reference: Reply, candidate: Reply,
                  chunk_size: int) -> tuple[list[Diff], float | None]:
    """Apply the VRP invariants to both sides and return any distance ratio.

    Returns:
        The violated invariants, and `candidate_total / reference_total` when
        both sides produced a comparable solve.
    """
    body = case.body or {}
    ref_json, cand_json = reference.json or {}, candidate.json or {}

    if case.endpoint == "vrp-allocate":
        diffs = (_side("reference", quality.allocation_invariants(body, ref_json))
                 + _side("candidate", quality.allocation_invariants(body, cand_json)))
        agreement = quality.allocation_agreement(ref_json, cand_json)
        if agreement < 1.0:
            diffs.append(Diff("$.allocations", f"depot agreement {agreement:.1%}",
                              None, None, Verdict.FAIL))
        return diffs, None

    diffs = (_side("reference", quality.vrp_invariants(body, ref_json, chunk_size))
             + _side("candidate", quality.vrp_invariants(body, cand_json, chunk_size)))
    if quality.served_stops(ref_json) != quality.served_stops(cand_json):
        diffs.append(Diff("$.routes", "the two sides served different stops",
                          None, None, Verdict.FAIL))
    return diffs, _ratio(ref_json, cand_json)


def _side(label: str, diffs: list[Diff]) -> list[Diff]:
    """Tag per-side invariant violations with which side broke them."""
    return [Diff(f"{d.path} ({label})", d.message, d.reference, d.candidate, d.verdict)
            for d in diffs]


def _ratio(reference: dict, candidate: dict) -> float | None:
    """Return the candidate's total distance relative to the reference's."""
    ref_total = reference.get("total_distance", 0.0)
    if not ref_total:
        return None
    return candidate.get("total_distance", 0.0) / ref_total


async def run_case(case: Case, ref_client: httpx.AsyncClient, cand_client: httpx.AsyncClient,
                   chunk_size: int) -> tuple[CaseResult, float | None]:
    """Send one case to both gateways and judge the pair."""
    rule = rule_for(case.endpoint)
    want_bytes = rule.body == "bytes"
    reference = await send(ref_client, case, want_bytes)
    candidate = await send(cand_client, case, want_bytes)

    for label, reply in (("reference", reference), ("candidate", candidate)):
        if reply.is_precondition_failure:
            detail = reply.transport_error or f"HTTP {reply.status}"
            raise PreconditionError(f"{label} returned {detail} for {case.label}")

    diffs = judge_status(reference, candidate, rule)
    ratio: float | None = None
    if rule.body == "bytes":
        diffs += judge_bytes(reference, candidate)
    elif rule.body == "quality":
        quality_diffs, ratio = judge_quality(case, reference, candidate, chunk_size)
        diffs += quality_diffs
    elif rule.body == "json":
        diffs += compare(reference.json, candidate.json, rule.tolerance)

    unproven = reference.status >= 500 and candidate.status >= 500
    if unproven:
        diffs.append(Diff("$status", f"both sides returned {reference.status}; proves nothing",
                          reference.status, candidate.status, Verdict.ADVISORY))
    return CaseResult(case, worst(diffs), diffs, unproven=unproven), ratio


class PreconditionError(RuntimeError):
    """The run's environment is wrong, rather than the port.

    Kept distinct from a parity failure so a rate-limited or half-down run does
    not read as catastrophic divergence -- which is how a harness earns a
    reputation for crying wolf and stops being run.
    """


async def run_corpus(cases: list[Case], ref_client: httpx.AsyncClient,
                     cand_client: httpx.AsyncClient, chunk_size: int,
                     pace: float = 0.0) -> list[EndpointResult]:
    """Run every case against two already-built clients.

    Taking clients rather than URLs is what lets the harness be validated
    against the Python gateway in-process, with no engine and no sockets --
    see `tests/test_parity_selfdiff.py`.

    Args:
        cases: The corpus, already built.
        ref_client: Client bound to the incumbent gateway.
        cand_client: Client bound to the gateway under test.
        chunk_size: `VRP_CHUNK_SIZE`, for the capacity invariant.
        pace: Seconds between cases, to stay under the rate limiter.

    Returns:
        One `EndpointResult` per endpoint, in corpus order.
    """
    grouped: dict[str, EndpointResult] = {}
    ratios: dict[str, list[float]] = {}

    for case in cases:
        result, ratio = await run_case(case, ref_client, cand_client, chunk_size)
        group = grouped.setdefault(case.endpoint, EndpointResult(case.endpoint))
        group.cases.append(result)
        if ratio is not None:
            ratios.setdefault(case.endpoint, []).append(ratio)
        if pace:
            await asyncio.sleep(pace)

    for endpoint, group in grouped.items():
        group.verdict = worst([Diff("", "", None, None, c.verdict) for c in group.cases])
        if endpoint in ratios:
            ratio_verdict, summary = quality.ratio_verdict(ratios[endpoint])
            group.verdict = max(group.verdict, ratio_verdict)
            group.note = summary
    return list(grouped.values())


async def run_urls(cases: list[Case], reference_url: str, candidate_url: str,
                   chunk_size: int, pace: float, timeout: float) -> list[EndpointResult]:
    """Run the corpus against two gateways addressed by URL."""
    async with (httpx.AsyncClient(base_url=reference_url, timeout=timeout) as ref_client,
                httpx.AsyncClient(base_url=candidate_url, timeout=timeout) as cand_client):
        return await run_corpus(cases, ref_client, cand_client, chunk_size, pace)
