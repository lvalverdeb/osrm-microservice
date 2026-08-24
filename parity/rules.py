"""Per-endpoint comparison rules.

Tolerances are set from what the two implementations actually do, not from a
generic notion of "close enough":

* `/matrix` was observed byte-identical between the gateways, so it is compared
  exactly -- a delta there is a real signal rather than float noise.
* `/route` and the other pass-throughs re-serialise the same upstream JSON, and
  float64 parse/format differences move the last ULP on some coordinates. They
  get a tolerance six orders of magnitude above that drift and six below any
  routing-relevant difference.
* `/tile` proxies raw protobuf, so it is compared as bytes.
* `/vrp` and `/vrp/allocate` are judged on solution quality, not equality --
  clustering ties and float details legitimately differ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from parity.compare import Tolerance

# 1e-9 degrees is ~0.1 mm: far above the ~1e-15 float re-formatting drift and
# far below any difference a router could mean.
COORDINATE_ABS_TOL = 1e-9

BodyKind = Literal["json", "bytes", "quality", "probe"]


@dataclass(frozen=True)
class Rule:
    """How one endpoint's responses are compared.

    Args:
        body: Which comparison strategy applies to the response body.
        tolerance: Numeric tolerance, for `json` bodies.
        compare_status: Whether a status-code mismatch is a failure. False for
            the probes, which query the engine independently and may legitimately
            disagree while it flaps.
    """

    body: BodyKind
    tolerance: Tolerance = field(default_factory=Tolerance)
    compare_status: bool = True


_PASS_THROUGH = Rule("json", Tolerance(abs_tol=COORDINATE_ABS_TOL, rel_tol=1e-12))

RULES: dict[str, Rule] = {
    # Pure proxies of the same upstream JSON.
    "route": _PASS_THROUGH,
    "trip": _PASS_THROUGH,
    "match": _PASS_THROUGH,
    "nearest": _PASS_THROUGH,
    # Observed byte-identical; hold it there.
    "matrix": Rule("json", Tolerance(exact=True)),
    # Built locally from the matrix, so it re-serialises floats a second time.
    # The envelope key name is the thing to watch: networkx 3.6 emits "edges"
    # where older versions emitted "links", and the handler passes node_link_data
    # through unchanged, so the response shape follows the resolved library
    # version rather than this repo's code.
    "matrix-graph": _PASS_THROUGH,
    # Raw protobuf.
    "tile": Rule("bytes"),
    # Judged on quality; see parity.quality.
    "vrp": Rule("quality"),
    "vrp-allocate": Rule("quality"),
    # Probes: each side asks the engine independently, so a status mismatch is
    # advisory rather than a failure, but the body shape is not.
    "health": Rule("json", compare_status=False),
    # Prometheus text carries process-level and scrape-time-dependent series;
    # comparing it across implementations is noise. The metrics contract needs
    # its own test -- scrape, issue N requests, scrape, assert the delta.
    "metrics": Rule("probe", compare_status=False),
}


def rule_for(endpoint: str) -> Rule:
    """Return the rule for `endpoint`, defaulting to float-tolerant JSON."""
    return RULES.get(endpoint, _PASS_THROUGH)
