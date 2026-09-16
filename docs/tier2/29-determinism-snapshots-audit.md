# 29 — Determinism, snapshots and the audit trail

*Tier 2: authored from `vrp/snapshot.py`, `vrp/observe.py`,
`vrp/portfolio.py`, `tests/vrp/test_determinism.py`.*

Three properties that only mean something together: a run must be
**reproducible**, its inputs must be **retained**, and what it did must be
**recorded**.

## Determinism

The constraint is that any run be replayable, which shapes decisions across the
whole platform:

| Decision | Where | Consequence |
|---|---|---|
| budgets are **iteration counts, never wall-clock** | LNS, route pool, portfolio | the same seed gives the same plan |
| portfolio results collected in **engine order** | `portfolio.py` | two runs agreeing on every number serialise to the same bytes |
| `workers=1` is the default | `portfolio.py` | "a library that parallelised unasked would make every existing caller's run non-reproducible" |
| integers, never accumulated floats | `battery.py`, `timedependent.py` | no drift between runs or platforms |
| canonical JSON: sorted keys, sorted sets, no insignificant whitespace | `snapshot.py` | two processes that agree about content agree about bytes |

The single-threaded, iteration-limited mode is what all regression tests use.

## Snapshots

> Input snapshot, solver configuration, and output plan are immutable and
> retained for the regulatory retention period; a plan is replayable from its
> snapshot.

The module is precise about the division of labour: **where the bytes live for
a retention period is a deployment decision**; this module writes a file and
reads it back. What a library *can* own is that the thing retained is
**sufficient** to re-derive the plan and **tamper-evident**.

### Sufficiency was the hard part, and it was missing

This is the most instructive defect in the repository. `Problem.from_dict`
reconstructed:

- 9 of `Vehicle`'s 28 fields
- 4 of `Order`'s 13
- 3 of `StopSpec`'s 5
- none of the problem's locks, synchronisations or speed profiles

**Thirty fields, dropped in silence.** A snapshot built on that codec would
have replayed a problem with no vehicle costs, no hours-of-service rules, no
site access and no incompatibilities: "cheaper to serve, legal in more ways,
and reported as a faithful replay". That is precisely the audit failure the
requirement exists to prevent, so the codec was fixed **before** anything was
built on it.

The same class of defect is now machine-checked elsewhere: the delivery-model
contract is verified against `dataclasses.fields` itself, so a field added to
`Vehicle` fails the suite until somebody decides about it (doc 06).

### Tamper-evidence

A sha256 **over the canonical bytes**, not a checksum somebody remembers to
update. Content addressing means a snapshot cannot be edited and still be
itself: any change to the problem or the configuration changes the digest, and
`read` raises `SnapshotTampered` rather than replaying something that is no
longer what was planned.

The configuration — solver, seed, iteration budget, matrix version — is sealed
**alongside** the problem, "because a plan is only reproducible from both: the
same instance at a different seed is a different plan and an honest audit says
so".

## The run record

Seven things every run emits: objective trajectory over time, incumbent
timestamps, constraint-violation counts, matrix cache hit rate, seed, solver
version, deterministic iteration count.

Three were already carried by `Solution.solver`; the other four "existed in
pieces nothing collected" — the pair cache counts its own hits, the verifier
returns violations, and the trajectory was not recorded at all.

### The risk is a decorative record

> Every field is easy to emit and hard to make true: a trajectory that is
> always empty, a hit rate that is always zero, a violation count nobody
> increments. Such a record passes any test that asks whether the field exists,
> and tells an operator nothing on the day they need it.

So the tests **run the same thing twice with one dimension changed and assert
the field moved**. That is the testing pattern worth copying anywhere a
requirement is a list of fields.

### Two clocks, and only one of them replays

The requirement asks for a trajectory "over time" and for incumbent timestamps;
reproducibility asks that any run be replayable. Wall-clock nanoseconds satisfy
the first and destroy the second — "two runs of the same seed agree about
everything except when they happened". Recording only iterations would lose
what was asked for.

Both are kept, and `replayable()` names the half that is a function of the
seed. A consumer comparing two runs uses that half; a consumer showing an
operator what happened uses the other.

### Observation cannot change the plan

The LNS search takes an optional recorder and tells it about each new
incumbent. It is "optional and passive: the search reports what it found and
knows nothing about what is done with it, so observability cannot change the
plan".

## What this gives an auditor

1. The snapshot: the exact instance and configuration, digest-sealed.
2. The run record: what the search did, including the trajectory and the
   violation counts.
3. The plan, with the verifier's independent report over it.
4. A replay from (1) that reproduces (3).

## What is still a deployment decision

Object storage, the retention period itself, and access control over retained
snapshots. The module writes a file; "the format is the part that has to be
right either way".
