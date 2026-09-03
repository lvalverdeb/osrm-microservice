# Whitepaper experiments

Every figure quoted in the whitepapers is produced by a script here and written
to `results/` as JSON. Nothing is transcribed by hand: each paper cites the
script and the result file, so any number can be re-run rather than trusted.

## Running them

The gateway must be reachable and its engine up.

```sh
export WHITEPAPER_GATEWAY=http://10.211.55.33:8000   # default if unset
cd docs/whitepapers/experiments
PYTHONPATH=../../.. uv run python e01_geometry.py
```

`PYTHONPATH` is needed because the scripts import both `common` (this
directory) and `vrp` (the repository root).

| Script | Measures | Used by |
|---|---|---|
| `e01_geometry.py` | Detour ratio and network asymmetry over the corpus | 01 §2, §3 |
| `e02_heuristic_vs_solver.py` | Gateway `/vrp` against PyVRP, both scored by `vrp.evaluator` | 03 §3 |
| `e03_anytime.py` | Solution quality against search budget; crossover with the gateway | 03 §4 |
| `e04_scaling.py` | `/vrp` and `/matrix` latency by size; cold vs warm cache | 02 §2, §3 |
| `e05_hysteresis.py` | What the allocation band holds, and what it costs, at two depot spacings | 02 §4 |
| `e06_mutation.py` | The independent verifier against six seeded defects | 03 §5 |

## Reproducibility

Sampling is seeded (`seed = 20260902`) and every matrix is pinned, so a re-run
against the same corpus and the same OSRM extract reproduces the tables. A
different extract moves the road distances and therefore every derived figure —
which is why each result file records the gateway it was measured against.

`results/` is committed. These are measurements, not build output: the papers
cite specific numbers, and a reader who cannot see what was measured cannot
check the claim. Re-run and commit when the corpus or the extract changes.
