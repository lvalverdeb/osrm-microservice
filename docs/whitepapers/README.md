# Whitepapers

Three technical reports on this system, each built around measurements taken
against a live instance rather than around a summary of the other docs. The
scripts that produced every figure are in [`experiments/`](experiments), and
their JSON output is committed alongside.

| | Paper | For | Built on |
|---|---|---|---|
| 01 | [Road Distance Is Not Geometry](01-routing-a-delivery-day.md) | Newcomers | Detour ratios and network asymmetry measured over the Costa Rica corpus |
| 02 | [What the Gateway Costs](02-what-the-gateway-costs.md) | Operators and integrators | `/vrp` and `/matrix` scaling, cache value, and a sweep of the allocation band |
| 03 | [Feasibility Is a Gate](03-feasibility-is-a-gate.md) | Platform engineers and architects | The gateway's routing against a real solver, the anytime curve, and the verifier under mutation |

## Findings that were not previously recorded

- A straight line understates a real Costa Rica drive by ~40% at the median, and
  by 17.6× in the worst pair sampled — 914 m apart, 16.1 km by road (01 §2).
- Two-thirds of GAM address pairs have a different distance out than back
  (01 §3).
- The gateway's `/vrp` costs **8.2% on pure sequencing, rising to 14.8% with six
  vehicles**, against PyVRP on the identical instance and fleet (03 §3).
- At comparable wall-clock — 12 ms — the solver is already 7% ahead (03 §4).
- The gateway's reported `total_distance` and the Python canonical evaluator
  agree to within 0.9 m on 341–482 km plans: INV-9 holding across a language
  boundary, untested in CI (03 §3).
- `hysteresis_m` at its shipped default holds **2 of 400 stops** at national
  depot spacing and **17 of 400** at urban spacing — its effect is a function of
  depot geometry, not of the number (02 §4).
- A cache hit is 11.5× a miss (02 §3).
- The verifier caught all six seeded defects and named the right invariant each
  time (03 §5).

## Corrections these reports make to existing docs

- `SDD.md` §3.6 describes `hysteresis_m` as keeping territories stable *between
  runs*. There is no previous assignment in a `/vrp` request; the band compares a
  Euclidean anchor against the road-cost best within a single run (02 §4).
- `configuration.md` says "all 29 settings" and `SDD.md` says 35; `config.rs`
  declared 36 when measured. Both prose counts are stale (02 §12).
- The spec's §13 status prose says "61 of 65 done … the four that remain" while
  its own table shows 69 of 73 with three `open` rows it does not list (03 §13).
- `planning/VRP_SDD_FIT_GAP.md` (2026-08-25) reports no functional requirement
  met. That was true of the gateway's `/vrp` before most of `vrp/` existed, and
  reads as a verdict on the whole system (03 §13).
