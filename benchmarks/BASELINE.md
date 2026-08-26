# Benchmark baseline

Recorded by `python -m vrp.bench.runner --record`. **Do not edit by hand** --
these numbers are only meaningful as the output of a run, and a hand-edited
baseline silently redefines what every later comparison means.

| Field | Value |
|---|---|
| Recorded | 2026-08-26 |
| Solver | pyvrp |
| Budget | 300 iterations, seed 0 |
| Hardware | Darwin arm64 |

| Instance | Total distance | Vehicles |
|---|---|---|
| `c20-clustered-slack` | 182,174 m | 2 |
| `c20-scattered-slack` | 196,245 m | 2 |
| `c30-clustered-tight` | 293,474 m | 5 |
| `c30-scattered-tight` | 389,058 m | 5 |
| `c50-clustered-pressure` | 416,967 m | 7 |
| **Total** | **1,477,918 m** | |

## What these numbers are, and are not

They are a **regression baseline**: the gate in `vrp/bench/runner.py` fails a
change that makes the mean worse by more than 0.25 percentage
points, or any single instance worse by more than 2.0%.

They are **not** a quality claim. Gap against published best-known solutions
needs the public instance readers and the BKS registry (`T-06`), and until those
land there is nothing here to compare against but ourselves. SDD §11.3 is
explicit that its initial targets are targets rather than claims; this file does
not upgrade them.

The budget is **iterations, not seconds**. Wall-clock varies with the machine,
which would make the gate fail on a busy CI runner rather than on a real
regression. Absolute-quality reporting against published BKS does need a time
budget on declared hardware, and will get one when it has something to report.

## Re-recording

Changing `vrp/bench/corpus.py` changes what every number above means, so the
corpus and this file move together:

```sh
python -m vrp.bench.runner --record --iterations 300
```

Commit both, and say in the commit why the corpus changed.
