# Public benchmark instances

Real published instances, vendored so `tests/vrp/test_benchmark_readers.py` can
run without network access. Taken from [PyVRP's test
corpus](https://github.com/PyVRP/PyVRP/tree/main/tests/data) (MIT), which in
turn takes them from the original public sets.

| File | Class | Origin | Best known |
|---|---|---|---|
| `E-n22-k4.txt` | CVRP | Christofides & Eilon | 375, stated in the file's own `COMMENT` |
| `X-n101-50-k13.vrp` | CVRP | Uchoa X-set | not stated in the file |
| `RC208.vrp` | VRPTW | Solomon | 776.1, from `RC208.sol` alongside it |
| `lrc206.vrp` | PDPTW | Li & Lim | not stated in the file |
| `SmallVRPSPD.vrp` | VRPSPD | PyVRP fixture | not stated in the file |
| `pr107.tsp` | TSP | TSPLIB, Padberg & Rinaldi | not stated in the file |
| `OkSmallMultipleDepots.txt` | multi-depot VRPTW | PyVRP fixture, with a `.sol` | not stated in the file |
| `PR01.vrp` | SDVRPTW | Cordeau, via Vidal et al. (2013) | not read: the mapping refuses it, see below |

**Best-known values are never transcribed into code.** `vrp/benchmarks.py` reads
them from the instance `COMMENT` or from the sibling `.sol`, and reports
`best_known=None` when neither says. A hand-typed registry is a registry of
typos, and every gap computed against a wrong one is wrong while looking fine.

## Why these four variants

`CAT-VRP-003` §13.3 asks that "each variant section contributes at least one
benchmark-comparable fixture so public benchmark performance and production
performance can be related". Two of the catalogue's sections had no anchor at
all: §5 (TSP, sixteen scenarios) and the multi-depot half of §8 (MDHVRPTW,
twenty-nine scenarios, the second-largest). `pr107.tsp` and
`OkSmallMultipleDepots.txt` are those two anchors.

`PR01.vrp` is here to be refused. It is site-dependent -- its
`VEHICLES_ALLOWED_CLIENTS_SECTION` says which vehicle may serve which customer
-- and `read_benchmark` raises `NotImplementedError` naming that section rather
than dropping it. An instance that parses cleanly into a different problem is
the failure mode this file exists to keep tested.

## Measured, at 20,000 iterations, verified by `vrp.verify`

| Instance | Ours | Published | Gap |
|---|---|---|---|
| `E-n22-k4` | 375 | 375 | +0.00% |
| `RC208` | 776 | 776 | +0.00% |
| `pr107` | 44,303 | not in the file | — |

`pr107` reached 44,303 at 5,000 iterations in 0.7 s, single-threaded. TSPLIB
publishes 44,303 as this instance's optimum, so that is a match -- but the
number is written here, in prose, with its source, and **not** in code:
`read_benchmark` reports `best_known=None` for pr107 because the file does not
state one, and `test_a_tsp_instance_reads_as_one_tour_over_every_city` pins
that. Putting 44,303 into a registry would be the first entry in exactly the
table of typos this project refuses to keep.

`OkSmallMultipleDepots` solves to a verified two-route plan with each vehicle
starting and ending at its own depot. It is a correctness anchor, not a quality
one: it is a three-customer fixture with no published optimum, and the Cordeau
MDVRPTW set proper is too large to vendor -- point `read_benchmark` at a copy
outside the repository to measure against it.

Both match the published vehicle count too (four each). These are small
instances and matching them is not evidence about the large ones — §11.3's
thresholds are stated over the full sets, which are not vendored here.

## Adding more

Drop any VRPLIB, Solomon or TSPLIB file in this directory and the reader tests
pick it up automatically -- `.vrp`, `.txt` and `.tsp` are all swept. Large sets should not be committed; point `read_benchmark` at
a path outside the repository instead.
