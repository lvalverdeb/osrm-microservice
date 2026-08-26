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

**Best-known values are never transcribed into code.** `vrp/benchmarks.py` reads
them from the instance `COMMENT` or from the sibling `.sol`, and reports
`best_known=None` when neither says. A hand-typed registry is a registry of
typos, and every gap computed against a wrong one is wrong while looking fine.

## Measured, at 20,000 iterations, verified by `vrp.verify`

| Instance | Ours | Published | Gap |
|---|---|---|---|
| `E-n22-k4` | 375 | 375 | +0.00% |
| `RC208` | 776 | 776 | +0.00% |

Both match the published vehicle count too (four each). These are small
instances and matching them is not evidence about the large ones — §11.3's
thresholds are stated over the full sets, which are not vendored here.

## Adding more

Drop any VRPLIB or Solomon file in this directory and the reader tests pick it
up automatically. Large sets should not be committed; point `read_benchmark` at
a path outside the repository instead.
