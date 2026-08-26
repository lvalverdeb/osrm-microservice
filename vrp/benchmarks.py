"""Reading public benchmark instances — §11.3, FR/T-06, E-05.

§11.3 wants quality argued with evidence rather than assertion, and that needs
instances other people have already solved. This reads the standard formats into
our own `Problem`, so the same evaluator and the same independent verifier judge
a Solomon instance and a Costa Rica delivery round.

Parsing is delegated to `vrplib`, the reference implementation of the VRPLIB and
Solomon formats and already a PyVRP dependency. Writing a fourth parser for a
format with this many dialects would be a way to acquire bugs, not capability.
What is here is the *mapping*, which is the part with judgement in it.

**Best-known values are read from the files, never transcribed.** Every
published optimum this module reports comes from the instance's own COMMENT line
or from an accompanying `.sol`. A registry of hand-typed numbers is a registry
of typos that will silently redefine what "1.2% above BKS" means, and nobody
checks it because it looks like data.

Conventions worth stating, because they are conventions and not facts:

* **Travel time equals distance.** Solomon and CVRPLIB give one number per arc
  and the literature treats it as both. A benchmark comparison that invented a
  speed would not be comparable with anything.
* **Distances are rounded to integers.** EUC_2D in VRPLIB is defined as rounded,
  and our model is integer-only in any case. `vrplib` returns floats; the
  rounding happens once, here.

Placement: Python. Benchmark ingestion feeds the solver and the verifier and is
nowhere near a request path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import vrplib

from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)

# Solomon instances state a horizon on the depot; CVRP instances have none, and
# a day long enough to never bind is the honest stand-in.
UNBOUNDED_DAY = 10 ** 7


@dataclass(frozen=True)
class Benchmark:
    """One public instance, in our model, with whatever is known about it."""

    problem: Problem
    name: str
    kind: str                       # CVRP, CVRPTW, PDPTW as the file declares
    best_known: int | None          # from the file; None when it does not say
    best_known_source: str          # where that number came from, or "unknown"
    vehicles_available: int
    # Benchmark coordinates are planar, not geographic: E-n22-k4 has a point at
    # (145, 215), which is not a latitude. They are kept here rather than
    # forced into `Location`, whose range validation is worth keeping honest --
    # and nothing in a solve reads them, because every distance comes from the
    # pinned matrix.
    coordinates: tuple[tuple[float, float], ...] = ()


def _optimum_from_comment(comment: str) -> int | None:
    """Pull a published optimum out of a VRPLIB COMMENT line.

    CVRPLIB writes them as free text -- "(Christophides and Eilon, Min no of
    trucks: 4, Optimal value: 375)" -- so this is a regex over prose, which is
    fragile by nature. It returns None rather than guessing when the phrasing
    is unfamiliar, because a wrong optimum is worse than no optimum: every gap
    computed against it would be wrong and nothing would look broken.
    """
    match = re.search(r"Optimal value\s*:\s*(\d+)", comment, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"Best (?:known )?(?:value|solution)\s*:\s*(\d+)",
                      comment, re.IGNORECASE)
    return int(match.group(1)) if match else None


def read_solution_cost(path: Path) -> float | None:
    """The cost recorded in a `.sol` file, if it has one."""
    if not path.exists():
        return None
    match = re.search(r"^Cost\s+([\d.]+)", path.read_text(), re.MULTILINE)
    return float(match.group(1)) if match else None


def read_benchmark(path: str | Path, *, vehicles: int | None = None) -> Benchmark:
    """Read a VRPLIB or Solomon instance into a `Problem`.

    Args:
        path: the instance file. A sibling `.sol` is used for the best-known
            cost when the instance itself does not state one.
        vehicles: override the fleet size. Solomon files declare one; CVRP files
            often encode it only in the name (`E-n22-k4` means four), and
            guessing from a filename is not something this should do silently.

    Returns:
        The instance, its declared kind, and its best-known cost with the
        provenance of that number.

    Raises:
        NotImplementedError: the file uses a feature this mapping does not
            cover, named explicitly rather than approximated.
    """
    path = Path(path)
    raw = vrplib.read_instance(str(path))

    if "edge_weight" not in raw:
        raise NotImplementedError(
            f"{path.name} gives no edge weights; only explicit or EUC_2D "
            f"matrices are mapped, not geographic or lower-triangular forms")

    matrix_values = [[round(float(cell)) for cell in row] for row in raw["edge_weight"]]
    size = len(matrix_values)
    depot_index = int(raw["depot"][0]) if "depot" in raw else 0

    coordinates = raw.get("node_coord")
    locations = tuple(
        Location(id="DEPOT" if i == depot_index else f"C{i}",
                 lat=0.0, lon=0.0, matrix_index=i)
        for i in range(size)
    )
    planar = tuple((float(x), float(y)) for x, y in coordinates) \
        if coordinates is not None else ()

    windows = raw.get("time_window")
    horizon = (int(windows[depot_index][1]) if windows is not None
               else UNBOUNDED_DAY)
    shift = TimeWindow(start=0, end=horizon)

    service = raw.get("service_time", 0)
    demands = raw.get("demand")

    orders = []
    for i in range(size):
        if i == depot_index:
            continue
        window = (TimeWindow(start=int(windows[i][0]), end=int(windows[i][1]))
                  if windows is not None else shift)
        per_stop = int(service[i]) if hasattr(service, "__len__") else int(service)
        orders.append(Order(
            id=f"O{i}", kind="JOB",
            quantities={"demand": int(demands[i]) if demands is not None else 0},
            delivery=StopSpec(location_id=locations[i].id,
                              time_windows=(window,),
                              service_fixed=per_stop)))

    fleet_size = vehicles or int(raw.get("vehicles", 0)) or len(orders)
    capacity = int(raw.get("capacity", 0))
    fleet = tuple(
        Vehicle(id=f"V{n}", capacities={"demand": capacity}, shift=shift,
                start_location_id="DEPOT", end_location_id="DEPOT")
        for n in range(1, fleet_size + 1)
    )

    best = _optimum_from_comment(str(raw.get("comment", "")))
    source = "instance COMMENT" if best is not None else "unknown"
    if best is None:
        cost = read_solution_cost(path.with_suffix(".sol"))
        if cost is not None:
            best, source = round(cost), f"{path.with_suffix('.sol').name}"

    return Benchmark(
        problem=Problem(
            id=str(raw.get("name", path.stem)), locations=locations,
            orders=tuple(orders), vehicles=fleet,
            matrix=TravelMatrix(version=f"benchmark:{path.stem}",
                                durations=tuple(tuple(r) for r in matrix_values),
                                distances=tuple(tuple(r) for r in matrix_values))),
        name=str(raw.get("name", path.stem)),
        kind=str(raw.get("type", "UNKNOWN")),
        best_known=best,
        best_known_source=source,
        vehicles_available=fleet_size,
        coordinates=planar,
    )


def gap_percent(cost: int, best_known: int) -> float:
    """How far above the best known a plan sits, as §11.3 reports it."""
    if best_known <= 0:
        raise ValueError("best-known cost must be positive to compare against")
    return (cost - best_known) / best_known * 100
