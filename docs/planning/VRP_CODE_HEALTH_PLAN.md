# Code health in `vrp/`: where the complexity is, and which of it matters

**Status:** proposed 2026-11-16. Measured against `main` at `c4c7eb5`
(`vrp-platform` 0.4.0) plus the uncommitted `route_distance` consolidation
described in Phase 0, using `spaghetti` 2.6.2 scoped to the `vrp` package. The
raw run is `report.json` at the repository root. The `*_report.json` ignore
pattern does not cover that name, so it should be deleted or ignored rather than
committed; the figures that matter are copied here.

Companion to [`VRP_SDD_FIT_GAP.md`](VRP_SDD_FIT_GAP.md), which measures what the
platform does. This plan measures how the code that does it is shaped, and
proposes the smallest set of changes that would make the shape match the
guarantees the SDD leans on.

## Verdict up front

The package is not spaghetti. It scores a **B (78.2)** and the findings cluster
in a handful of functions whose branch count tracks the number of features the
platform supports. A compiler for a solver model that handles time windows,
reloads, open routes, prizes, eligibility profiles and tier bonuses will branch
once per feature, and no amount of extraction changes how many features there
are.

Two places are a genuine concern, and they are a concern for the same reason:
**the function is the only place its logic lives, and nothing tests it in
isolation.**

1. `compile_problem` in `vrp/solve/pyvrp_adapter.py` translates a `Problem`
   into a PyVRP `Model`. It is 246 lines, nests eleven levels deep at its worst
   point, has changed 25 times in six months, and no test calls it. Its
   correctness is checked only indirectly: the solver's output passes the
   verifier. A branch that compiles a constraint slightly wrong produces a
   slightly worse plan, not a failure, and the verifier accepts a worse plan.
2. The three largest checkers in `vrp/verify/verifier.py` are the oracle for
   everything else, including the adapter above. `_check_route`,
   `_check_one_lock` and `_check_coverage` are each 22 to 30 branches over
   47 to 93 lines. A checker should be boring enough to read whole and be sure
   it is right. If the oracle has a gap, the adapter's gap hides behind it.

Everything else in the report is either a feature count the SDD asked for, or
hygiene. The proposal is to fix the two concerns properly, gate the count so it
cannot climb back, and leave the rest alone until someone is already in the
file.

## Measured state

| | |
|---|---|
| Files / lines / functions | 64 / 18,239 / 626 |
| Score | 78.2 (B), maintainability index 62.1 |
| Errors | 37, all `high-complexity` or `high-cognitive-complexity` |
| Warnings | 95 |
| Info | 109 |

The 37 errors are 25 functions; twelve trip both complexity rules. Below are
the eleven furthest over the thresholds (cyclomatic max 10, cognitive max 15),
with the six-month commit count of their file.

| Function | Cyclomatic | Cognitive | Lines | Commits |
|---|---|---|---|---|
| `pyvrp_adapter.compile_problem` | 55 | 74 | 246 | 25 |
| `pyvrp_adapter.map_solution` | 24 | 53 | 144 | 25 |
| `polish.tsptw_sequence` | 22 | 45 | 96 | 3 |
| `verifier._check_one_lock` | 30 | 32 | 67 | 12 |
| `verifier._check_route` | 28 | 32 | 93 | 12 |
| `matrix.build_large_matrix` | 22 | 33 | 98 | 5 |
| `localsearch.accelerated_search` | 16 | 38 | 106 | 2 |
| `ortools_adapter._map` | 18 | 31 | 89 | 5 |
| `hos.schedule.schedule_route` | 24 | 24 | 90 | 2 |
| `verifier._check_coverage` | 22 | 24 | 47 | 12 |
| `triggers.reoptimise` | 25 | — | 71 | 4 |

The remaining fourteen sit between 16 and 22 cyclomatic and are listed in the
backlog section. The warnings are mostly size: eleven files over 400 lines
(`model.py` at 1,098 is the largest), 21 functions over 50 lines, seven
functions with more than six positional parameters, and two modules the tool
calls "god modules" for exposing 39 and 17 public functions
(`bench/fixtures.py`, `servicemodel.py`).

The info tier is 60 `duplicate-code` blocks, two thirds of them inside
`bench/fixtures.py` where repeated scenario setup is the point; 34 missing
parameter annotations, nearly all one `scenarios` argument threaded untyped
through `bench/catalogue.py`; and 13 bare `dict` hints at the adapter and OSRM
boundaries.

### Why complexity is the wrong headline and churn is the right one

Spaghetti's own `--plan --hotspots` weights rules by git change frequency and
puts `pyvrp_adapter.py` first in every category: 31 of the 59 complexity
findings, 15 of the 29 cognitive ones, 14 of the 21 long functions. It is also
the most-committed file in the package. A function that is both the most
complex and the most edited is where the next defect lands.

Compare `polish.tsptw_sequence` at cognitive 45: three commits in six months,
a single-purpose sequencer with its own tests. It is hard to read but it is not
moving, and nothing depends on reading it. It stays on the backlog.

## What the proposal is not

- **Not raising the thresholds.** Spaghetti's defaults of 10 and 15 are
  ordinary. Raising them to make the errors disappear hides exactly the
  functions this plan is about.
- **Not a sweep.** Fixing all 37 errors would touch 17 files, most of them
  stable, for no change in what the system guarantees. A change that moves no
  observable behaviour and closes no gap is churn.
- **Not splitting `model.py`.** It is 1,098 lines because the domain has that
  many fields with that many invariants, and `__post_init__` at cyclomatic 17
  is the validation of those invariants in one place. Scattering it would make
  the model harder to check, not easier.

## Phase 0: the one duplicate, done

**Committed as `83a03f8` on 2026-11-16.**

`_length(problem, vehicle_id, order_ids)` existed five times: in `explain.py`,
`triggers.py`, `adherence.py`, `quote.py`, and as the public `route_distance`
in `setpartition.py`. Spaghetti saw two of them because the other three
differed by a docstring or a loop shape. They differed in behaviour too:
only `adherence.py` returned 0 for an empty route, and `setpartition.py` read
`vehicle.end_location_id` without the `ends_at` fallback and would have raised
on an open-ended vehicle.

They are now one function, `vrp.evaluator.route_distance`, next to
`route_metrics` where the matrix is already the source of truth, with two
hand-worked tests in `tests/vrp/test_canonical_evaluator.py`. Each test was
proven by breaking the helper and watching exactly that test fail. The full
`tests/vrp` suite passed (1,178) and the report dropped from 248 findings to
241. This phase is in the working tree, uncommitted, and is the model for the
rest: one owner, a test that would catch the old divergence, no behaviour
change for any closed depot-to-depot route. (The paragraph above was written
before the commit and is kept as written.)

## Phase 1: give `compile_problem` a seam, then split along it

**Done 2026-11-16.** The characterisation test is
`tests/vrp/test_pyvrp_compile.py`: 258 cases over the fixture registry, the
benchmark corpus and one hand-built instance for the branches neither reaches,
every expectation derived from the `Problem` rather than the compiler. It was
proven before the split by breaking the compiler four ways, each caught by
the one test aimed at it. `compile_problem` is now 20 lines over eleven
stage functions, `map_solution` 20 lines over a `_Walk` of the route being
read back, and the full suite of 1,436 passed unchanged. Four errors left the
baseline. One fact nobody had written down came out of the test: a vehicle
with no distance cost compiles to PyVRP's default of 1, not 0.

`map_solution` got no direct test of its own. Every solve-based test reads
its output, the verifier agreement test judges it, and a direct test would
need PyVRP solution objects that only a solve produces; the 1,436 are the
characterisation. The paragraphs below are kept as proposed.

**Order matters: the test comes first.** There is no direct test today, so a
refactor would be checked only by the solver's end-to-end output, which is the
weak oracle this plan exists to strengthen.

1. **Characterisation test.** `compile_problem` returns a `_Compiled` holding
   the PyVRP `Model`. A test can compile each scenario in the P0 corpus and
   assert on the model's observable shape: location count and order, the depot
   set including reload locations, the sink depot present only when a vehicle
   has `open_route`, one vehicle type per eligibility profile, and for each
   client its time window, service duration, prize and required flag. These
   are the facts the function's own comments say it establishes; today nothing
   reads them back. Write it against the current function and commit it green.
2. **Split along the function's own section comments.** The body already
   narrates six stages at four-space indentation: locations in matrix order;
   depots including reloads; the zero-cost sink for FR-08 open routes; the
   per-order client loop (lines 492 to 582, where the nesting reaches its deepest point at line 566); eligibility
   profiles built before the fleet (FR-10, FR-11); vehicle types and forbidden
   edges. Each becomes a function taking `problem`, `model` and whatever
   handles the previous stage produced, in that order, and `compile_problem`
   becomes the sequence. The characterisation test does not change.
3. **`map_solution` the same way**, in the opposite direction. It is one
   130-line loop over PyVRP routes with three stages: reconstructing the depot
   load per trip (the comments from line 715 record two bugs that explain why
   it is rebuilt rather than read back), the per-activity step loop from line
   737, and the unassigned list at the end. Each stage becomes a function. The
   bare `dict` at line 696 is the `solver` provenance parameter; it becomes a
   small typed record while the signature is open.

Exit criterion: `compile_problem` and `map_solution` each under 50 lines, no
helper over the complexity threshold, the characterisation test unchanged and
green, and `tests/vrp` green including `test_evaluator_verifier_agreement.py`.

## Phase 2: make each verifier checker readable whole

**Done 2026-11-16.** Before the split, every `report.fail` site in the three
checkers was neutralised in turn to see whether a test noticed: 19 of 25 did.
Two INV-3 sites masked each other (a bad `start_service` also makes a bad
departure, and the test asserted only on the code), one INV-1 site, one INV-6
site and one PIN_DEPOT site had no test at all. Five tests were added, each
asserting on the detail text so sibling checks cannot cover for one another.
The sixth site is unreachable, see the finding below. Then the split: INV-3 to
INV-6 as four functions over one break-collapsing `_legs` walk, INV-8 as a
dispatch table of eight rules over a `_Placement`, INV-1 and INV-2 as their
own functions. Every message byte-identical, every invariant emitted from one
function, 1,442 tests green, six errors out of the baseline.

**Finding.** A plan that names an order the problem does not contain never
gets the INV-1 verdict `_check_coverage` records for it: `_check_route` then
asks the problem for that order and the lookup raises `ValidationError`, so
`verify()` raises instead of returning the report. Pinned as a refusal in
`test_a_plan_naming_an_order_the_problem_lacks_is_refused_not_reported`.
Turning it into a reported violation is a behaviour change: the route
checkers would have to tolerate an unknown order id. Worth doing, not here.

The paragraphs below are kept as proposed.

The verifier is INV-1 to INV-9 made executable, and the SDD calls INV-9 "the
single most valuable test in the system". Its checkers should each state one
invariant.

1. **`_check_route` is four invariants in one loop.** Its comments already
   label them: INV-3 (service timing) and INV-5 (load) inside the per-step
   loop, INV-4 (arrival follows from departure plus matrix plus breaks) and
   INV-6 (duration, distance, shift) over the collapsed stop sequence. Split
   into `_check_service_timing`, `_check_load`, `_check_arrivals` and
   `_check_shift_and_distance`. The break-collapsing walk that INV-4 and INV-6
   share becomes one helper that yields consecutive stops with en-route break
   time, so the subtlety the comment at line 773 describes is written once.
2. **`_check_one_lock` promised this and did not finish.** Its docstring reads
   "split out so each kind reads as its own rule", then dispatches on
   `lock.kind` through a 60-line if-chain with seven parameters. One function
   per lock kind, dispatched from a dict, each taking the lock and the three
   lookup tables it needs. The seven-parameter signature goes away because no
   kind needs all seven.
3. **`_check_coverage` is two invariants.** INV-1: every order is served
   exactly as many times as expected or listed unassigned, never both, and
   nothing unknown is served. INV-2: both ends of a shipment sit on one route
   with the pickup first. Two functions, each named for the invariant it
   emits.

The agreement test between evaluator and verifier, and `test_independent_verifier.py`,
are the safety net. Before each split, perturb the checker (drop one invariant)
and confirm a test fails; a checker nothing fails against is not a checker.

Exit criterion: no function in `verifier.py` over the complexity threshold,
no `Report` consumer changed, every `INV-n` string still emitted from exactly
one place.

### What the examples sweep could and could not show

`make examples-check` after both refactors: 37 passed, 6 skipped, 38 failed,
every failure the same `RuntimeError: No road matrix` because the FreeBSD
jail gateway was unreachable from this machine on 2026-11-16, and the
examples refuse to fall back to straight lines by design. The 37 that passed
are the offline ones and they do drive the compiler, the mapper and the
verifier. The 38 are unverified against this change until the gateway is up;
the run takes about 55 minutes when it is down, because each example waits
out a TCP timeout.

## Phase 3: hold the line

**Steps 1 and 2 done 2026-11-16**, gate proven: with the baseline in place
`make spaghetti` exits 0 with 37 findings hidden; removing one accepted
fingerprint from the file made the same run exit 2 naming `compile_problem`.

None of the above holds without a gate, and today there is none. `make` has no
`spaghetti` target although `.claude/settings.local.json` already carries a
permission for one, which says somebody expected it.

1. **Configure the tool in `pyproject.toml`** under `[tool.spaghetti]`: packages
   limited to `vrp`, `fail-on = "error"`, a baseline file committed at
   `.spaghetti-baseline.json` in the repository root, where the retired
   `.fenceline-baseline.json` lived and where the tool's README puts it. The
   baseline accepts the 37 errors that exist today by fingerprint. New errors
   fail; removing one is a one-line baseline edit in the same commit. The
   baseline is edited by hand, removals only: `--update-baseline` accepts
   every current finding at every severity, which is the opposite of a gate.
2. **`make spaghetti`** runs it. It is deliberately **not** called from
   `make lint`, because CI runs `make lint` and the runner has no `spaghetti`
   binary; a target that skipped when the tool was missing would look like a
   gate without being one. The CI job already restores a cargo cache and
   builds the gateway, so `cargo install spaghetti --locked` in that job is
   the obvious next step, but it adds a compile to every run and has not been
   measured. That is a decision, not a task, and it is left open here. Keep
   the HTML and JSON outputs gitignored, as `*_report.json` already is, plus
   the bare `report.json` the CLI tends to be redirected into.
3. **Shrink the baseline as Phases 1 and 2 land.** The baseline is the burn-down
   chart. When it is empty, drop `fail-on` to `warning` and repeat for size.

One measured caveat. `duplicate-function-body` fingerprints are not stable
across runs: the finding's message embeds the twin's path, and between two
identical runs it flipped from absolute to repository-relative, changing eleven
fingerprints. A baseline for that rule would churn. Scope the baseline to the
two complexity rules, where fingerprints held across three runs, until the tool
is fixed.

## Backlog: fix when already in the file

Errors not covered above, in descending cyclomatic order, each with the reason
it can wait:

| Function | Cyc | Why it waits |
|---|---|---|
| `polish.tsptw_sequence` | 22 | Stable, tested, single purpose |
| `matrix.build_large_matrix` | 22 | Chunking and retry logic; branches are failure modes, each tested |
| `benchmarks.read_benchmark` | 22 | A file-format parser; one branch per format quirk |
| `diagnose.preflight` | 21 | A list of independent checks in sequence; split is mechanical when wanted |
| `explain._remedy` | 20 | Ten returns, one per remedy kind; a dispatch table when touched |
| `diagnose._eligible` | 19 | Answers the same question as `pyvrp_adapter._may_serve`, as that docstring says; one should call the other |
| `decompose.repair_boundaries` | 18 | Algorithmic; cognitive 17 says the nesting is shallow |
| `ortools_adapter._refuse`, `_map` | 18, 18 | Secondary adapter; mirror the Phase 1 shape when it is next changed |
| `objective.score` | 18 | One term per objective mode |
| `decompose.partition_quality` | 17 | Metric computation |
| `model.__post_init__` | 17 | Deliberate, see above |
| `lns.sisr_ruin` | 17 | Published algorithm transcribed; splitting obscures the paper |
| `localsearch.accelerated_search`, `naive_search` | 16, 12 | Inner loops of a search; the cognitive figure is nesting, not branching |
| `evaluator.build_timeline` | 16 | Ground-truth evaluator; written to be obviously right, and it is long rather than tangled |
| `modelcheck.check`, `pyvrp_adapter._refuse_unreachable` | 16, 16 | Validation lists |
| `hos.schedule.schedule_route`, `triggers.reoptimise` | 24, 25 | Each is one policy loop; revisit if either grows |

Warnings worth a single hygiene commit, together: the 34 untyped `scenarios`
parameters in `bench/catalogue.py` (one type alias), the three missing return
types, and the nine-parameter `_drive` in `hos/schedule.py`, five of whose
parameters are the running state (clock, driver state, steps, violation,
on-board load) that `schedule_route` threads through every call and which want
to be one object.

Not worth a commit: the 60 `duplicate-code` blocks in `bench/fixtures.py`.
Fixtures that share text are fixtures that can be read side by side, which is
what they are for.

## Risks

- **Refactoring the compiler without the characterisation test.** This is the
  one ordering constraint in the plan. If Phase 1 step 2 starts before step 1
  is committed, the only check is the solver's output. The solver is
  deterministic for a seed (CON-4), so a compile error does not show up as
  noise: it shifts the plan to a different one that still verifies, and
  nothing reports that the plan should have been better.
- **A split that changes evaluation order.** `compile_problem` builds profiles
  before vehicle types because a type names its profile. The staged functions
  must keep that dependency explicit through their arguments, not through a
  shared mutable `model` reached from several places.
- **Stale bytecode during perturbation.** A perturb-and-restore cycle inside
  one second with a same-size edit left Python running the broken `.pyc` after
  the source was restored, and a test failed against a file `diff` called
  identical. Run perturbation checks with `python -B` and clear the module's
  `__pycache__` entry first.
- **Shrinking the baseline from the wrong scan.** A scan run with the
  pyproject baseline active omits the accepted findings from its output, so a
  script that computes "fixed = accepted minus current" against it empties
  the file. This happened once and was caught by the gate reporting 35 errors
  with 0 hidden. Shrink from an unbaselined run: `spaghetti --no-pyproject
  --package vrp=vrp --json`, then keep only the fingerprints still present.
- **The baseline becoming a graveyard.** A baseline that only grows is worse
  than no gate because it looks like one. Phase 3 step 3 is the mitigation:
  the baseline shrinks or the plan has stalled, and either is visible.

## Sequencing

Phase 0 is done and should be committed first on its own. Phase 3 steps 1 and
2 are small and go next, so Phases 1 and 2 land against a gate that already
exists. Phase 1 before Phase 2: strengthening the oracle first is tempting but
the adapter is where the commits are, and a stronger oracle with the same
untested compiler would catch defects a week after they ship rather than before.
