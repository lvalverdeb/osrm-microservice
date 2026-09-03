# Making the examples do their two jobs

**Status:** proposed, 2026-09-03. Nothing here is built yet.

## What examples are for

Two purposes, and every decision below follows from them:

1. **Showcase the suite's functionality** — what it does, on work that looks
   like a customer's.
2. **Provide implementation clues** — an example should read as the code
   somebody integrating the suite would copy.

A third thing they must *not* be is **proof**. Proof is a test's job, and the
difference is not cosmetic: proving a property invites reaching into internals,
and an example that monkeypatches a private function shows a reader nothing
they could write. Two examples were doing exactly that and were corrected in
`2c5a633`; this plan is about the rest.

## Measured state, 2026-09-03

Every figure below was measured, not estimated.

| | |
|---|---|
| runnable examples under `examples/src` | 72 |
| run standalone without error | **65** |
| fail because they need a gateway while claiming not to | **5** |
| fail on API drift — the library moved and they did not | **2** |
| reach into private APIs | 8 |
| have no `E-nn` row in `VRP_TDD_EXAMPLES.md` | **31** |
| draw instances from `dataset.py` | 18 |
| claim "Runs offline" | 47 |

**Nothing runs them.** `make examples` is an interactive menu; no test and no
CI job executes an example. That is why two are broken:

- `fleet/dynamic/dispatch_waves.py` calls `decide(..., postponed_to=...)`; the
  parameter no longer exists.
- `fleet/rich/prizes_and_priority.py` does `bonuses[1]`; `tier_bonuses` became
  keyed by `precedence()` — a `(tier, source)` tuple — during `T-74`. **That
  break is mine**, made this session, and went unnoticed for exactly the reason
  this plan exists.

The five "offline" failures are a false claim rather than a missing service:
`territories`, `fleet_mix`, `fleet_minimisation`, `tactical_sizing` and
`depot_inventory` each say "Runs offline. No gateway required" and then call
`build_matrix(GATEWAY, …)` with no fallback.

## Phase 0 — Stop the rot — **DONE**

Everything else re-rots without this, and it is the cheapest item here.

Landed as `tests/test_examples_run.py`, `make examples-check` and a CI step of
its own. The whole sweep is **170 s** measured, against a suite of ~80 s, so it
carries the `slow` marker and `make test` still filters it out; CI runs it
separately, which is the difference between a gate and a good intention.

Three examples are 60% of that 170 s -- `set_partitioning_polish` at 52 s,
`process_portfolio` at 28 s, `portfolio_parallelism` at 21 s. The last two are
slow because they make a pure-Python engine do real work to show the GIL, which
is the demonstration rather than waste.

What it does, and one thing the plan got wrong. The original sketch said to run
"every example marked offline" and skip the rest by static analysis. Measuring
first: 25 examples reference a gateway and **20 of them run anyway** -- optional
enrichment, or a path that degrades. Skipping by what the source mentions would
have given up two-thirds of the coverage for the five that genuinely need a
service. So the rule is about what happened, not what the file says: run
everything, treat a refused connection as a skip and anything else as a
failure.

The two broken examples are pinned as strict `xfail`s naming the drift, so the
suite is green and the breakage is recorded rather than hidden. Phase 1 removes
the pins by fixing them -- and cannot fix one quietly, because a passing xfail
fails the file.

The five false "Runs offline" claims have their own test, asserting the exact
set. Fixing one means removing it from that list; a sixth appearing fails.

## Phase 1 — Repair what is already broken — **DONE**

Both drifts were deeper than the one-line traceback suggested.

- `dispatch_waves` — `decide` had **two** changes. It takes the `Epoch` rather
  than a `postponed_to` instant, and `Policy` became `(open_ids,
  classification, epoch)` rather than two arguments. Fixing the first surfaced
  the second.
- `prizes_and_priority` — the fix is not `bonuses[(1, 2)]` but
  `bonuses[precedence(order)]`. Keying with the same function the adapter keys
  with is what stops a third drift.

The five false offline claims are **made true rather than deleted**. They go
through `dataset.road_matrix_or_planar`: a road matrix when a gateway answers,
planar over the same coordinates when none does, and it prints which it got.
Straight-line distances are shorter than roads everywhere and by different
amounts in different places, so a number from a planar matrix is not the number
the road gives; an example that swapped them silently would be a worse lie than
the one it replaced.

**Result: 74 passed locally. 52 passed and 22 skipped under CI's conditions**,
which is the honest figure and was not the one first reported here.

`examples/.env` is gitignored and points at the FreeBSD jail, which is live from
the machine this was written on. Twenty-two examples were reaching a real
gateway, or the local 12 MB delivery corpus, or both. "Runs with no
infrastructure" was measured on a machine that had the infrastructure. The gate
is now verified by hiding `data/deliveries_cr.json` and `examples/.env` and
running it again -- reproducing CI rather than guessing at it.

### What Phase 0's gate got wrong, and CI caught

The gate ran examples with a bare interpreter. Every example's usage line says
`uv run --package osrm-api-gateway-examples`, and that package is what puts
`config` on `sys.path` and installs `folium`. It passed on a laptop whose venv
had been synced by an earlier `make examples` and failed on CI's clean checkout.

A gate that runs the thing differently from the way it is documented tests a
configuration nobody has -- a sharper version of the error this whole plan is
about. The runner is now the documented command, at 0.11 s of overhead each.

It also means the survey's "65 of 72 pass" was measured wrongly and was
flattering: `folium` and `config` failures would have counted as failures on a
clean machine. The conclusions about *which* examples were broken still hold,
because those two failed for their own reasons.

CI then taught two more things, and both were invisible here:

- **The delivery corpus is absent.** 12 MB, generated, not committed -- and
  generating it snaps fifty thousand points through OSRM, so CI cannot make one
  either. Skipped, not generated. `preflight_diagnosis.py` also read the file
  directly *as well as* through `dataset.load`, so an absent corpus surfaced as
  a bare `FileNotFoundError` instead of the guard's message naming the build
  command. The guard existed and the example walked around it.
- **`error_handling_demo.py` handles a missing gateway** and exits non-zero
  with a sentence about it, which is better behaviour than a traceback and read
  as a failure to the first version of the skip rule.

The rule now recognises three kinds of missing prerequisite -- no service, a
handled absence, no corpus -- rather than only a refused connection, and is
named `MISSING_PREREQUISITE` to say so.

## Phase 2 — Purpose 2: only the public API — **DONE**

Eight examples reach into privates. They are three different problems and the
fix differs:

**Promote — the function is a domain concept wearing an underscore.**

- `pyvrp_adapter._is_required` → **move to `vrp/model.py`**. It answers
  `FR-12`/`FR-25`'s "may the solver decline this order?", depends on nothing
  PyVRP-specific, and belongs beside `precedence()`, `may_enter()` and
  `has_skills_for()`. Renaming it in place would leave a model rule living in
  an adapter. Used by `prizes_and_priority` and `prize_collecting_epoch`.
- `scenarios._recovery_cost` → `recovery_cost`. Already documented as an
  injectable default; the underscore is the only thing making it private.
  Used by `tactical_sizing`.

**Do not promote — the example needs a seam, not a name.**

- `osrm._snap_all` and `matrix._fetch_tile` are patched by `degraded_matrix.py`
  to simulate a provider failing mid-build. Renaming them would bless
  monkeypatching as the public interface. Give `build_matrix` an injectable
  fetcher, or move the fault-injection to `tests/` and have the example show
  what a `DEGRADED` plan looks like to a caller.

**A fourth turned up that the survey missed.** `ride_time.py` imported
`pyvrp_adapter._delivery_deadline`, and it wanted a *fourth* answer again: made
public, but deliberately **left in the adapter** rather than moved to the model
like `must_be_served`. Its own docstring says `INV-14` is the exact check and
this is "the search's safe approximation" -- it exists because `add_shipment`
cannot state a ride bound. Among the model's rules a reader would take it for
the authoritative deadline, which it is not.

`must_be_served` is the positive form on purpose: the three examples that drove
this all said `not _is_required(order)`, and now say `not must_be_served(order)`
-- the domain statement, negated where the question is whether work can be
declined.

- **Done:** no example reaches into `vrp` internals. Verified with
  `grep -rn "from vrp[a-z_.]* import.*\b_[a-z]\|vrp\.[a-z_.]*\._[a-z]" examples/src`,
  which is narrower than the plan's original pattern -- that one also matched
  `import os as _os`, and a check with false positives is one people learn to
  ignore.

## Phase 3 — Purpose 1: coverage somebody can navigate

31 examples have no catalogue row. They are not all the same kind of thing, and
the first job is to say which:

- **Feature examples** — belong in `VRP_TDD_EXAMPLES.md` with an `E-nn` row
  tracing to a requirement, like the other 43.
- **Tools** — `generate_delivery_dataset.py`, `stress_test_vrp.py`,
  `visualize_vrp.py`, `clustering/generate_payload.py`. These are utilities,
  not demonstrations; move them under `examples/tools/` so the example tree is
  all examples.
- **Gateway/API examples** — the eight under `routing/` demonstrate the HTTP
  surface rather than the VRP library. They are legitimate and uncatalogued
  because `VRP_TDD_EXAMPLES.md` is a VRP document; give them their own short
  index rather than forcing them into it.

- **Done when** every file under `examples/src` is either catalogued, indexed
  as a gateway example, or moved out of the example tree, and a traceability
  check enforces it the way it already enforces missing example files.

## Phase 4 — Say what an example is

There is no `examples/README.md`. Write one, short:

- the two purposes, stated;
- public API only, and why proof belongs in tests;
- offline by default, `dataset.py` for instances, `PlanarMatrix` when a road
  matrix is not the subject;
- the docstring shape the good ones already share — what it demonstrates, which
  requirement, numbered sections, "Runs offline", a usage line.

- **Done when** a new example can be written from the README without reading
  another example, and the claim is checked by doing it.

## What this plan does not do

- It does not rewrite the 65 working examples. Most are fine; the ones needing
  real data got it in `da65eff`, and the rest build instances deliberately —
  `maximal_problem` for serialisation, adversarial fixtures for pathological
  cases, known profiles where ground truth is the point.
- It does not add a gateway to CI. The 25 examples that genuinely need one stay
  behind a marker; the parity harness already shows how (`conftest_gateway.py`).

## Order, and why

Phase 0 first because two examples are broken *now* and nothing would tell us
about the third. Phase 1 is what Phase 0 turns red. Phase 2 is the purpose-2
work you asked for and is independent. Phase 3 is the largest and the least
urgent. Phase 4 is cheap and makes the rest stick.
