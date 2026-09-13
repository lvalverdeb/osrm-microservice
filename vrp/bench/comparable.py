"""Which public instance each variant is measured against — `T-100`, §13.3.

§13.3 asks that "each variant section contributes at least one
benchmark-comparable fixture so public benchmark performance and production
performance can be related". That relation existed, but only in
`benchmarks/instances/README.md` -- as prose, where deleting an instance,
renaming one or adding a variant broke nothing and told nobody.

**An anchor must exhibit its variant.** The registry is checked against the
crossed `Problem`, never against the file's own `TYPE` line, because the defect
that prompted this was a file whose `TYPE` said `PDPTW` while its problem said
102 independent jobs -- `read_benchmark` did not read
`PICKUP_AND_DELIVERY_SECTION`, so Li & Lim's precedence and same-vehicle rule
were silently absent and every gap against its published best-known would have
measured a strictly easier problem while looking fine.

**Four variants are beyond the advertised five.** §2 advertises `TSP`, `CVRP`,
`VRPTW`, `MDHVRPTW` and `PDPTW`; §10.5 puts `IRP`, `CARP` and `LRP`
"deliberately partial", and `DARP` is marked thin. Those may record a reason
instead of an anchor -- but the reason is read, and it has to say something a
reader can act on rather than hold the place.

Placement: **Python**, in `vrp/bench/` beside the catalogue reader it queries
and the fixtures it sits alongside. It joins a documentation artefact to a test
corpus and is nowhere near a request path.
"""

from __future__ import annotations

from pathlib import Path

from vrp.bench.catalogue import PATHOLOGICAL, load
from vrp.benchmarks import Benchmark

INSTANCES = Path(__file__).resolve().parents[2] / "benchmarks" / "instances"

# §2: "the five variants the engine advertises".
ADVERTISED = ("TSP", "CVRP", "VRPTW", "MDHVRPTW", "PDPTW")

ANCHOR: dict[str, str] = {
    "TSP": "pr107.tsp",                      # TSPLIB, Padberg & Rinaldi
    "CVRP": "E-n22-k4.txt",                  # Christofides & Eilon, BKS in the file
    "VRPTW": "RC208.vrp",                    # Solomon, BKS in the sibling .sol
    "MDHVRPTW": "OkSmallMultipleDepots.txt",  # multi-depot; correctness anchor only
    "PDPTW": "lrc206.vrp",                   # Li & Lim
}

NO_ANCHOR: dict[str, str] = {
    "DARP": (
        "Cordeau & Laporte's DARP instances are public and are not vendored. "
        "This is the one entry here that is a to-do rather than a position: "
        "the catalogue marks DARP thin (3 scenarios, none P0) and it is outside "
        "the five variants §2 advertises, so nobody has been asked for an "
        "anchor -- but `max_ride_time` exists in the model (FR-24), so unlike "
        "the three below there is something on our side to compare."),
    "IRP": (
        "§10.5 places inventory routing deliberately partial: the replenishment "
        "decision -- when and how much -- is the problem, and this engine is "
        "given the quantities. A public IRP gap would score a decision we do "
        "not make."),
    "CARP": (
        "§10.5 places arc routing deliberately partial. Demand on arcs rather "
        "than nodes is a different model, not a harder instance of this one, "
        "and the Golden-DeArmon sets would have to be transformed to node "
        "routing first -- at which point the number measures the transform."),
    "LRP": (
        "§10.5 places location routing deliberately partial. Choosing which "
        "depots to open is a strategic decision this engine takes as input, "
        "so a Prodhon-set gap would be measuring a choice already made for it."),
}

# What the crossed problem must actually show. Facts, not file labels.
DEFINING: dict[str, frozenset[str]] = {
    "TSP": frozenset({"one-vehicle"}),
    "CVRP": frozenset({"capacitated", "many-vehicles"}),
    "VRPTW": frozenset({"binding-windows", "many-vehicles"}),
    "MDHVRPTW": frozenset({"many-depots"}),
    "PDPTW": frozenset({"shipments"}),
}


def variants() -> tuple[str, ...]:
    """Every operational variant the catalogue defines, sorted.

    `PATHOLOGICAL` is excluded: §11's adversarial instances are not a variant
    section and are already gated by `test_every_adversarial_instance_has_a_
    fixture_or_a_written_reason`.
    """
    return tuple(sorted({s.variant for s in load()
                         if s.variant != PATHOLOGICAL}))


def anchor_path(variant: str) -> Path:
    """Where `variant`'s public instance lives.

    Raises:
        KeyError: if the variant has no anchor. Callers that may be handed an
            excused variant should consult `NO_ANCHOR` first.
    """
    return INSTANCES / ANCHOR[variant]


def structure(benchmark: Benchmark) -> frozenset[str]:
    """The structural facts a crossed instance exhibits.

    Read from the `Problem`, so a file cannot claim a variant it does not
    demonstrate. Each fact is the thing its variant is *about*: a tour rather
    than a fleet, a binding window rather than a horizon-wide one.
    """
    problem = benchmark.problem
    facts = set()
    if len(problem.vehicles) == 1:
        facts.add("one-vehicle")
    else:
        facts.add("many-vehicles")
    if len({v.start_location_id for v in problem.vehicles}) > 1:
        facts.add("many-depots")
    if any(order.kind == "SHIPMENT" for order in problem.orders):
        facts.add("shipments")
    if any(quantity > 0 for order in problem.orders
           for quantity in order.quantities.values()):
        facts.add("capacitated")

    shift = problem.vehicles[0].shift
    for order in problem.orders:
        for stop in (order.pickup, order.delivery):
            for window in (stop.time_windows if stop else ()):
                if window.start > shift.start or window.end < shift.end:
                    facts.add("binding-windows")
    return frozenset(facts)
