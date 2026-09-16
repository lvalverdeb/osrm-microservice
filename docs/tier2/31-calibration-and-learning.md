# 31 — Calibration, adherence and rollout

*Tier 2: authored from `vrp/calibrate.py`, `vrp/speedfit.py`, `vrp/zones.py`,
`vrp/adherence.py`, `vrp/rollout.py`.*

Four pipelines that close the loop between what was planned and what happened,
plus the rollout discipline that decides whether a model change ships.

## One guardrail governs all of it

> Learned components MUST be advisory: they may bias search and warm starts,
> they MUST NOT be able to produce a plan that violates a hard constraint. The
> verifier is downstream of them.

Every module below is built to sit on the safe side of that line. Nothing
learned can make an illegal plan legal, because the independent verifier does
not know or care where a plan's structure came from (doc 21).

## Service-time calibration

Fit service duration from telematics as a function of order archetype,
quantity, location archetype, vehicle type, time of day and driver experience —
**starting with grouped medians per archetype, before any regression model**.

The instruction is taken literally, and the reason is explainability:

> A regression fits everything and explains nothing: a dispatcher told "the
> model says 412 seconds" cannot check it, and nobody can separate a genuine
> shift from an artefact of the fit. A median over a named group is a number
> somebody can go and count, which is what makes it safe to ship to every van
> in a monthly job.

**Median, not mean**, for a reason that cannot be fixed by filtering:

> A driver who takes a phone call mid-stop produces a forty-minute service on a
> four-minute job. The van really was stationary, so the observation is not
> wrong and cannot be filtered on principle — the statistic simply has to be
> the one that survives it.

Re-fit monthly; alert on drift.

## Speed-profile calibration

Per-arc-class, per-bucket speed multipliers fitted from observed GPS traces
**against the routing engine's free-flow assumptions**, maintaining FIFO by
construction (doc 27). Re-fit weekly, holding out one week for validation.

Grouped medians again, and for the same reason — "a driver stuck behind an
accident produces an arc that is genuinely slow and must not drag an hour's
multiplier with it".

Two properties worth knowing:

- **The multiplier is a correction to a specific engine's specific guess**, not
  a speed. Re-fitting after the engine's map changes is therefore mandatory
  rather than housekeeping.
- **An arc that crossed a bucket boundary attests to neither bucket.** It was
  driven partly in each, so it is excluded rather than attributed.

## Zone-sequence priors

The order of preference is explicit, and learning is the *second* choice:

1. **Extract the deviation into an explicit model feature** — "always
   preferable — it is explainable and auditable".
2. Only *where the pattern resists formalisation*, learn a sequencing prior at
   the zone level and add it as a soft objective or warm-start structure.
3. **"Never simply penalise drivers into compliance with a plan the model got
   wrong."**

Zone-sequence learning from historical routes is the approach that performed
best in the Amazon challenge, where a probabilistic model of zone ordering
learned from drivers outperformed hand-coded zone constraints.

## Plan adherence: the metric that judges the model

> Trust the plan only as far as it survives contact with reality. Plan quality
> MUST be measured against executed reality (GPS/telematics), not against the
> solver's own objective.

For each executed route: a sequence-dissimilarity score between planned and
actual stop order, plus the realised-cost delta, aggregated by depot, driver,
territory and time of day.

And then the sentence that decides what the metric is *for*:

> Systematic, repeated deviation is a **model defect**, not driver
> misbehaviour.

The justification is that drivers hold tacit knowledge that is hard or
impossible to formalise — roads that are awkward, when traffic is bad, where
parking is findable, which stops are conveniently served together. "Which is
exactly why drivers deviate from planned sequences." A system that read
adherence as a compliance score would be reading its own defect list as
somebody else's performance review.

This is the same premise that makes territory consistency a cost saver rather
than a concession (doc 23).

## Rollout

> Benchmarks validate the algorithm; only production validates the model.

Three stages:

1. **Shadow mode** — produce plans daily without executing them; measure the
   gap between shadow and executed plans, and interrogate every large
   divergence.
2. **Canary** — one depot, one month, with explicit rollback criteria **agreed
   in advance**.
3. **Plan adherence** — reused from above rather than reinvented; "this is the
   metric that tells you whether the model is right".

### Why the module contains a fingerprint

> "Agreed in advance" is the phrase a tool can actually enforce.

Criteria chosen after seeing the results are not criteria, they are a
rationalisation, and the failure mode is completely ordinary: the run lands 4%
down, somebody observes that 5% was always the real threshold, and the canary
passes. So the criteria are fingerprinted when they are set, and the
fingerprint is checked when they are evaluated.

## What is real and what is waiting on data

| Pipeline | Mechanism | Input status |
|---|---|---|
| service-time calibration | built | needs telematics |
| speed-profile fitting | built | needs GPS traces |
| zone priors | built | needs historical routes |
| adherence scoring | built | needs executed routes |
| rollout gating | built | needs a production deployment |

The mechanisms exist and are tested; the data sources are not in this stack.
That separation was deliberate — doc 27 records the same lesson in its own
words: the correctness properties of a construction can be tested long before
anyone has production data for it.
