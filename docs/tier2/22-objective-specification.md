# 22 — The objective: what "better" means

*Tier 2: authored from `vrp/objective.py`, `vrp/evaluator.py`,
`vrp/portfolio.py`.*

## Why not a weighted sum

The specification opens by naming naive weighted sums "the most common
modelling error in production routing". The failure mode is specific and worth
restating, because it is not obvious:

> weights tuned on a 200-stop day **silently invert** on a 2,000-stop day.
> Nothing fails; the solver simply starts preferring a different thing, and the
> first sign is a dispatcher saying the plans have got worse.

## Lexicographic tiers, scaled from the instance

Tier *n* strictly dominates the sum of the maximum attainable values of every
tier beneath it. That maximum is computed **from the instance**, never
hard-coded, "because a constant that dominates on one instance will not on a
larger one".

`tier_scales` builds bottom-up: the lowest tier scales by one, each tier above
by one more than everything below it can reach. **That "one more" is what makes
domination strict rather than merely likely.**

| Tier | Carries |
|---|---|
| 1 | unserved required orders / priority obligations |
| 2 | orders served (droppable ones) |
| 3–4 | fleet size and operating cost |
| 5 | soft violations (lateness, earliness) |
| 6 | workload imbalance, and churn against a previous plan |

Tier 5 is **taken from the canonical evaluator rather than recomputed** here:
"Two implementations of 'how late is this' would be two chances to disagree,
and there is no independence argument for separating them — that argument
applies to the verifier, which shares nothing with either."

Tier 6 holds the spread of duration, distance and stop count across the drivers
who actually worked, plus churn when a previous plan exists. It stays at the
bottom: consistency is a tie-breaker, "never a reason to drive further, so no
amount of imbalance can outrank a metre of operating cost". Without a previous
plan there is no churn to measure — "inventing a baseline would rewrite the
objective for every static solve in the system".

## Compare, do not total

`compare` walks levels in precedence order and returns at the first difference.
It is **exact whatever the magnitudes**, where comparing scaled totals depends
on the scaling being right. `total` exists only for engines that need a single
number.

`compare` takes the spec rather than the scales, deliberately. The first
version took both, "which let a caller pass scales built for one mode and a
spec for another and get a silently wrong ordering — and `compare` does not
need the scales at all".

## The modes, and where they actually live

The modes are not weights. They are **groupings of tiers into levels**, and
tiers inside a level trade against each other while levels are strictly
ordered.

| Mode | Arrangement |
|---|---|
| `MIN_VEHICLES` | fleet is its own level: vehicle count strictly dominates distance |
| `MIN_COST` | fleet and operating **share a level**, compared in money: a vehicle is deployed iff its fixed cost is repaid by savings |
| `PRIZE_COLLECTING` | tier 2 merges into the money level — "freely droppable". Tier 1 stays above it: a priority-0 order is a promise, not a bid |
| `MAX_SERVICE` | deliberately not a case: it is already the default arrangement. What distinguishes it is that orders stay required rather than droppable, which lives on the order |

The `MIN_COST` grouping is the one that bit. Treating both tiers as
lexicographic was the first implementation, "and it silently made `MIN_COST`
behave as `MIN_VEHICLES`: no vehicle could ever pay for itself, because one
fewer vehicle always won".

## One scoring authority

`vrp/portfolio.py` never reads an engine's `objective_breakdown`. Every
incumbent is re-scored from its routes by the canonical evaluator, on one
scale. This is INV-9's argument one level up: a solver's own cost figure is not
evidence about the solver, and comparing several engines' self-reported numbers
"picks whichever engine is most generous to itself".

The same rule reaches into the route pool, where columns come from several
engines: cost is read from the matrix, not from whatever produced the route.

## Two consequences to know about

**The totals are large.** A six-customer instance produces scale factors in the
billions. Arithmetically fine in Python's unbounded integers, and exactly the
overflow risk the specification warns about above 10,000 stops.

**Staged optimisation — the specified answer to that — is not implemented.** It
belongs with the solver driver rather than with the objective, and the module
says so. Until then, prefer `compare` over `total` at scale.
