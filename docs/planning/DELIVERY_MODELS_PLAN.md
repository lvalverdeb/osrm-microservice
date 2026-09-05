# Delivery models: the operation as data, gated before it ships

**Status:** proposed, 2026-09-05. Nothing here is built. The schema below was
spiked and thrown away; the figures are measured, not estimated.

## What a delivery model is

A delivery model is **everything about an operation except today's demand**:
which vehicle serves it, what a stop costs in minutes, what a unit weighs, when
the customer will open the door, whether the vehicle comes home. Envelope
delivery is one model — motorbikes, ten minutes a stop, 200 g a unit, a closed
trip, business hours, the nearest depot. Parcel delivery is another.

It is not a new domain type. `Problem` already expresses all of it; a model is
the *factory* for one, held as JSON so an operation can be described, reviewed
and versioned without a code change.

## Why this shape fits the suite

**`Problem` is the universal interface.** Of 45 modules under `vrp/`, **35 take
`problem: Problem`**. The ten that do not are primitives over matrices, routes
and recurrences -- `matrix`, `lns`, `localsearch`, `battery`, `osrm`,
`policies`, `periodic`, `fleet`, `observe`, `timedependent` -- which a model has
no business configuring. So a model that produces a `Problem` reaches the
decomposer, the verifier, the quoter, the replayer, the scenario sweep, the
hours-of-service scheduler, the consistency measures and pre-flight diagnosis
without any of them knowing models exist. That is not luck; it is what §4.1's
"solver-independent by construction" bought.

**The duplication is already there.** 43 example files construct `Vehicle(` by
hand, 6 define their own `build(depot, deliveries, matrix, ...)`, and at least
eight distinct shift constants are in circulation (`0-12h`, `6-20h`, `0-24h`,
`8-16h`, ...). Every example re-derives corpus to `Problem` with its own
numbers. That translation layer is what does not exist.

**A spike proved the shape.** Three deliberately unalike examples were rebuilt
from one schema through one ~70-line builder:

| Example | Result |
|---|---|
| `tw/envelope_round.py` -- time-bound, disjoint windows, grams | `Problem.to_dict()` **identical** |
| `rich/heterogeneous_fleet.py` -- 6 depots x 3 classes, kilograms, closed **and** open | **identical** |
| `rich/multi_capacity.py` -- two dimensions, `kg` and `dm3` | builds; `dm3` matches `cube_of` exactly |

No special case for any of them. The schema needed four small things: `quantity`
as a list rather than a scalar, capacities on the fleet entry rather than
inherited from the order dimension, costs defaulting to zero, and `route.closed`
overridable per run.

## What a model may not express

Two boundaries, both found by the spike rather than assumed. They are the
difference between configuration and a language nobody meant to write.

**A model names a computation; it never describes one.** `multi_capacity`'s
`cube_of` is a category-conditional formula -- `Apparel` and `Household` divide
by 8, everything else by 60. As data that is `{"divide_by": {"Apparel": 8,
"default": 60}, "scale": 10}`, which is the first line of an expression
language. The model says `{"dimension": "dm3", "derived": "cube"}` and the code
stays code, exactly as `Vehicle.hos_rules` names `EU-561` rather than carrying
its articles.

**A model cannot contain anything that needs a solve to define itself.**
`tw/sla_windows.py` derives its response targets from percentiles of a
calibration run: the windows do not exist until the round has been solved once.
A model describes the shape of a problem. Anything that must solve first is a
procedure, and belongs in code.

## What the spike schema left out

The spike proved a *shape*. It is not a candidate schema, and the measurement
says so plainly:

| Type | Covered | Of | Missing |
|---|---|---|---|
| `Vehicle` | 8 | 28 | `max_duration`, `max_distance`, `skills`, `cost_per_second`, `overtime_cost_per_second`, `cost_per_order`, `profile`, `service_factor_ppt`, `reload_locations`, `max_reloads`, `reload_duration`, `battery_wh`, `consumption_wh_per_km`, `charger_locations`, `charging_curve`, `initial_soc_ppt`, `access_class`, `gross_weight_kg`, `hos_rules`, `initial_state` |
| `Order` | 6 | 13 | `pickup`, `release_time`, `required_skills`, `max_ride_time`, `priority_source`, `order_class`, `incompatible_with` |
| `StopSpec` | 3 | 5 | `service_per_unit`, `service_per_unit_dimension` |
| `Location` | 4 | 9 | `dwell_overhead`, `dock_capacity`, `inventory`, `access_classes`, `max_vehicle_kg` |
| `TimeWindow` | 2 | 5 | `hardness`, `earliness_cost_per_sec`, `lateness_cost_per_sec` |
| **Total** | **23** | **60** | **38% covered** |

`Problem` itself contributes five more the schema never touched: `horizon`,
`locks`, `synchronisations`, `speed_profile`, `speed_profiles`.

**The spike reproduced the exact defect this plan warns about.** It covers 8 of
`Vehicle`'s 28 fields. `_vehicle_from_dict` covered 9 before `T-89`. A loader
written from the spike would ship the same bug the snapshot work removed, which
is the strongest possible argument for the reflection test below.

**Omissions that matter to the models already discussed**, not hypotheticals:

- **`reload_locations` / `max_reloads` / `reload_duration` (FR-09, §6.9).** A
  25 kg motorbike box on a 38-stop day *obviously* returns to reload. The
  envelope model as spiked cannot say so, and silently plans a round no
  motorbike could carry.
- **`access_class` / `access_classes` / `max_vehicle_kg` (FR-11).** The
  motorbike's real operational advantage -- reaching sites a van cannot -- and
  the one part of it the platform *does* enforce today.
- **`max_duration` (FR-16).** "Shift windows (earliest start, latest end, **max
  duty duration**) independent of vehicle availability" is three components; the
  spike expressed one. "Shifts cannot exceed eight hours" is a duty cap, and a
  `TimeWindow(08:00, 16:00)` is not the same statement.
- **`hardness` and the soft-window cost rates.** Every window in the spike is
  hard. `E-23` and `E-93` both turn on soft windows with asymmetric earliness
  and lateness pricing; no model can express either.
- **`dwell_overhead` (§6.2).** Parking and walking, "independent of the order".
  It is the term that would let a motorbike be faster at the kerb -- and, as
  `E-21` records, the model cannot vary it per vehicle anyway.
- **`priority_source` (FR-25) and `release_time`.** The SLA work's ranking, and
  when goods become available.
- **`pickup` / `kind: SHIPMENT`.** `rich/multi_capacity.py` already plans
  collections; the spike models deliveries only.
- **`speed_profiles` (FR-14).** A metro operation's congestion is a model-level
  fact, not a daily one.

None of these is an argument against the design. They are the difference
between a spike and `T-94`, and they set its real size: the builder is small,
the *coverage* is the work.

## Model architecture

Sixty settable fields, most of them optional, is exactly the shape that becomes
repetitive to write and hard to debug. Five rules, each answering a specific
failure.

**1. Composition by named fragments, never inheritance.** Business hours, a
Costa Rican daytime shift and a depot strategy will repeat in every model. A
model *references* a fragment -- `"windows": "cr-business-hours"` -- resolved
from `models/fragments/`. This is the same rule as naming a computation:
a fragment is used whole, by name.

**2. No partial override.** The moment a model may say "this fragment but with
`end` changed", the design has inheritance, and with it the question "where did
this value actually come from" that makes deep hierarchies undebuggable. A model
needing different hours names a different fragment. Fragments do not reference
fragments.

**3. Load-bearing fields are explicit; only cosmetic ones default.** Thirty-seven
unstated fields silently taking dataclass defaults is how a model means
something nobody wrote. Anything that changes feasibility -- capacities, shift,
`max_duration`, service, windows, route closure -- must be stated, and silence
is an error. `Location.dock_capacity = None` meaning "unconstrained" is a
deliberate, documented reading; a model inheriting that reading by accident is
not.

**4. Every resolved field carries its provenance.** The resolved model records,
per field, whether the value came from this file, a named fragment, or a
default. It is cheap -- recorded during resolution -- and it is the difference
between answering "why is this van capped at eight hours?" and reading three
files to guess.

**5. The snapshot hashes the *resolved* model, not the file.** Two textually
different files meaning the same thing must hash the same, and one file whose
fragment changed underneath it must not. Hashing source text gets both
backwards, and `NFR-08`'s replayability depends on getting it right.

**The completeness test is written by reflection, not by hand.** It walks
`dataclasses.fields(Vehicle)` and friends and asserts that every field is either
expressible in the schema or on an explicit, justified exclusion list. A
hand-written list is how a loader silently lags the model; a reflective one
fails the moment somebody adds a field, and makes them decide. This is the test
that would have caught `_vehicle_from_dict` at 9 of 28, and it is the only
reason to believe a schema at 23 of 60 will not ship as it stands.

### Master models and deployment tweaks

An operation running in six cities differs slightly in each. Six near-identical
model files is the repetition this design exists to remove, so a master model
assembled once and adjusted per deployment is the right ambition. It is also
where configuration schemes usually become undebuggable, so it needs three
constraints rather than a merge rule.

The repository already runs this pattern successfully. `gateway/src/config.rs`
declares every setting as `(field, env name, committed default)`, exports
`DEFAULTS`, and its tests enforce **both** directions -- every key in
`deploy/env/app.env` resolves to a setting, and every default equals the
committed value. One level, a declared surface, and a test in each direction.
Its own docstring calls it "the cheapest correctness gate this port has".

**1. Composition is disjoint, so there is no precedence to reason about.** A
master assembles *sections* -- windows, fleet, service, assignment -- each
claimed by exactly one named source. Two sources offering the same section is a
conflict the loader refuses, not something it resolves by order. This is what
removes the diamond problem outright: there is no "last wins" rule, because
overlap is illegal rather than ordered.

**2. Exactly one level of adjustment.** A model either declares a `base` -- in
which case it is a deployment variant -- or it may be used as a base. Never
both. Chains are what make "where did this value come from" unanswerable; depth
one means the answer is always "the base, or the overlay", which is two files.
It is checkable at load and cheap to enforce.

**3. The master declares what a deployment may change.** A `tunable` list names
the adjustable fields; everything else is frozen. This is the rule that turns a
master model into a *contract*: a city may raise its courier count and shift its
hours, and may not quietly change the capacity its feasibility depends on. An
overlay touching a field outside the tunable surface is an error, and so is an
overlay introducing a key the base does not have -- otherwise a typo silently
becomes a new field rather than a failed override.

**Lists are addressed by identity, never by position.** `fleet.MOTO.count`, not
`fleet[0].count`, and never an element-wise merge of two lists. Positional
merging of fleet entries is a defect waiting for somebody to reorder a file.

**The gate runs per deployment variant, not per master.** A validated master
that ships six unvalidated variants is the failure this whole design is meant to
prevent -- each resolved model faces `T-96` on its own demand, because that is
the only artefact that will actually plan a day.

**Hashing needs no special case.** A master and its overlay resolve to one flat
model, and rule 5 already hashes the resolved form. Two deployments of one
master hash differently because they *are* different; a master edit changes
every variant's hash, which is correct and is what `NFR-08` requires.

**Three scopes, kept physically apart.** A model is reusable; an instance is
today; a run is how it was solved. `locks` and `synchronisations` are
instance-level -- they are facts about one day -- and a model that can carry
them will accumulate yesterday's exceptions. `ObjectiveSpec` and solver settings
are run-level, which is `T-95`.

## `T-94` -- the service model

**Deliverable.** `models/*.json`, one file per model, plus
`models/categories.json` holding the global category-to-model map. The corpus
carries seven categories and `multi_capacity.BULKY_CATEGORIES` already hardcodes
a category-to-behaviour map, so this generalises something that exists in
miniature. `vrp/servicemodel.py` holds a frozen `ServiceModel`, `model_for(name)`
and `model_for_category(category)`, mirroring `hos.rules_for` and its registry.
Named strategies only: `{"depot": "nearest"}` resolves through a table to
`dataset.nearest`.

Named `servicemodel` because `vrp/model.py` is the domain model and the
collision would be cruel. It is a builder *beside* the model, not inside it:
§4.1's rule is that nothing there knows how a route is produced.

**The thing to get right.** This repository has been bitten three times by
partial deserialisation that succeeds rather than fails:

| | Reconstructed | Of |
|---|---|---|
| `_vehicle_from_dict` before `T-89` | 9 | 28 fields |
| `_order_from_dict` before `T-89` | 4 | 13 fields |
| `_stop_from_dict` before `T-89` | 3 | 5 fields |
| `api._window` **today** | 3 | 5 fields -- both soft-window cost rates dropped |

A JSON loader is that hazard with a new surface. Unknown keys are an error, not
a shrug. Every field round-trips. Contradictions refuse at load, the way `Order`
refuses a `STATUTORY` order carrying a prize -- a 25 kg vehicle declared against
40 kg items is the same kind of contradiction written down.

**Snapshot integration is not optional.** `snapshot.capture(problem, config)`
seals a problem and its configuration so a plan is replayable (NFR-08, CON-4).
The model's name *and content hash* belong in `config`, or every plan stops
being reproducible the moment somebody edits a model file.

**Passes when.** Round-trip completeness holds for every field; an unknown key
is rejected; a contradictory model is rejected at load; `E-94` and `E-21` built
from JSON produce byte-identical problems to the hand-rolled versions; a plan's
snapshot names the model it was built from and refuses a changed one.

## `T-95` -- the run configuration

Split out of `T-94` because it is a different subject, and conflating them is
how a model grows until it can express a replan policy.

**What is not in `Problem`:** `ObjectiveSpec` (mode, tiers, rates), solver
iterations, seed, engine choice. A model that fixes the fleet but not the
objective has not pinned the plan -- two runs of the same model can order two
plans differently and both be right.

**Deliverable.** A declared sibling section in the model file, resolved into
`ObjectiveSpec` and a solver configuration, sealed into the snapshot alongside
the model hash. Not smuggled into problem-building, and not merged with the
model's own fields.

**Explicitly out of scope, and named so nobody tries.** Anything dynamic --
`epochs`, `preemption`, `committed`, replan triggers, `pcdispatch` -- operates
on a *sequence* of problems with state carried between them. One model produces
one `Problem`. A day that evolves is a second concept and needs its own task.

**Passes when.** Two models differing only in objective mode produce
demonstrably different plans from identical demand; the snapshot pins both the
model and the run configuration; replaying a sealed plan reproduces it.

## `T-96` -- the model evaluator, which is the gate

The evaluator is what makes the rest of this safe rather than merely
convenient. Today a bad constant needs a code change, a test and a review. As
JSON it is an edit, so this design **moves a class of failure from review time
to run time**, and the gate is what buys it back.

The failure it must catch: a model can be valid JSON, load cleanly, build a
legal `Problem`, and still be operationally wrong. A 25 kg motorbike against
40 kg parcels is INFEASIBLE every day and nothing upstream of a solve can say so.

**Deliverable.** `vrp/compare.py` -- `compare(models, scenarios, solve)` over
identical demand, reusing `scenarios.sweep`'s shape and `pareto()` rather than
re-deriving them. Per model it reports:

- cost, from **verifier-accepted plans only**
- `evaluator.window_attainment` (`T-93`), not `MixResult.service_level`, which
  is an assignment rate and would score a model that serves everything late as
  perfect
- vehicles used and orders unassigned
- **which constraint bound** -- the new capability, and the reason this is its
  own task rather than assembly

**Why the binding constraint matters more than the cost.** "Three couriers" is
useless without "because the shift ran out, not the satchel": the binding
constraint is the number that says *which model parameter to adjust*.
`diagnose.py` explains why an order was rejected pre-flight (`T-14`); nothing
today reports what bound in a plan that succeeded.

**Why verifier-accepted only.** Measured during `E-94`: asked to serve 120
envelopes with one courier, PyVRP returns its best infeasible attempt with every
arrival clamped to noon, and a naive count reports 120 letters delivered by a
rider who could not have managed 40. A comparison that scores that ranks
fantasies. `E-23` states the same warning: a solver's stop count is what it
attempted, not what is achievable.

### Validating and debugging composite models

Composition adds a second class of failure, and it fails at a different speed
from the first. `T-96` must do both and must not conflate them.

**Structural validation -- no solve, runs on every edit.** Milliseconds, so it
belongs in the pre-commit gate rather than the deployment one:

- no two sources claim the same section (composition is disjoint)
- no chains: a model declaring `base` is never itself a base
- the overlay touches only fields the master declared `tunable`
- the overlay introduces no key the base does not have
- after resolution, every load-bearing field is stated rather than defaulted
- no contradiction survives -- a 25 kg vehicle against 40 kg items
- every resolved field resolves to exactly one source

**Operational validation -- needs a solve, runs before deployment.** Feasible
across a scenario set, verifier-accepted, attainment met, and the binding
constraint the intended one.

A model can pass every structural check and be operationally impossible; that is
the whole reason `T-96` exists. It can equally be operationally fine and
structurally incoherent -- two fragments both defining `windows`, resolved by
luck of ordering -- which is the failure that only shows up when somebody edits
the other fragment months later.

**Three debugging outputs, and the third is the one that earns the
architecture.**

1. **The resolved model, flat, with provenance per field.** Rule 4's record,
   printed: base, fragment name, overlay, or default. It answers "why is this
   van capped at eight hours" without opening three files.

2. **A diff across the variants of one master.** Six cities sharing a master
   should differ in a handful of tunables; a report that lists exactly what
   differs is where drift is caught, because an unintended difference looks
   identical to an intended one in a file listing.

3. **Attribution when a variant fails a gate its master passes.** With depth one
   and a declared tunable surface, the evaluator can revert each overlay field
   in turn and name the one that broke it. That is a small, bounded search --
   the tunable list is short by construction -- and it is only possible *because*
   of the depth-one and declared-surface rules. Under deep chains the same
   question is combinatorial and nobody asks it twice.

The third point is worth stating as the argument for the constraints rather than
a consequence of them: the rules in the architecture section are not there to
make composition tidy, they are there so that a failing composite model can be
explained by a machine instead of by a person reading files.

**Deployment.** `rollout.py` already ships the other half -- `shadow()`,
`divergences()`, `decide(canary, days)` with typed `Criterion` and `Decision`.
A model change is a canary candidate. No model reaches `models/` without passing
the gate, and that is a CI job rather than a convention.

**Passes when.** A model whose shift binds and one whose capacity binds are
correctly distinguished on the same demand; an infeasible plan never scores; a
model serving everything two hours late loses to one serving on time; the gate
fails a model that is valid JSON and operationally impossible.

## `E-95` -- the example

Envelopes against parcels on the same demand, with the binding constraint named
and a sensitivity line -- what ten minutes to nine buys, in stops. It is the
artefact that makes the feature usable by somebody who did not write it.

## Sequencing

1. **`T-94`**, then **`T-95`**, then **`T-96`**: the evaluator needs models to
   compare and a configuration to hold fixed while comparing them.
2. Convert **two** examples, not 43. `tw/envelope_round.py` and
   `rich/heterogeneous_fleet.py` are the pair the spike used and the pair that
   proves the schema. The rest migrate when touched.

## Risks, stated rather than discovered

**Indirection in a repository whose examples are read as much as run.**
`VRP_EXAMPLES_PLAN.md` sets the two jobs: showcase, and implementation clue.
Reading an example becomes reading an example *plus* a JSON file *plus* a
registry. `envelope_round.py`'s docstring currently teaches why grams beat
kilograms; that teaching is worth more than its reuse. **Convert where a model
is genuinely shared across categories; leave the teaching examples explicit.** A
model layer that needs 43 conversions to justify itself has not justified itself.

**A model is a hypothesis, and hypotheses rot.** Ten minutes a stop and 200 g an
envelope are claims about an operation that drift. `calibrate.py` already fits
service times from telematics by `Archetype` and reports `Drift` with `was`/`now`
and thin evidence. Without that loop closed, `models/` becomes a confident
description of last year's business. The feedback path should be designed in
now even if it is built later.

**The expression-language slope.** Already met once, at `cube_of`. The rule --
name a computation, never describe one -- has to be written into the schema
documentation, because the second and third cases will each look like a
reasonable exception.

## What this plan does not do

No plugin system, no inheritance between models, no per-model Python hooks, no
runtime fetching. Models are in-repo JSON, versioned with the code, and differ
in numbers and in which shipped strategy they name. The moment one needs custom
code, it is a code change with a test -- which is the same rule `hos/rules.py`
already states for a rule set whose *shape* differs.
