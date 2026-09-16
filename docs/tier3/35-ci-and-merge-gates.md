# 35 — CI and merge gates

*Tier 3: authored from `.github/workflows/tests.yml` and the make targets it
calls.*

One workflow, `Tests`, on pushes to `main` and pull requests against it. Two
jobs.

## Concurrency

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

One run per branch; pushing again supersedes whatever is in flight. The comment
records why it was needed — "three were once in flight together here, and four
of eleven finished after they had been overtaken" — and why it is safe here:
nothing this workflow does is published, so a cancelled run leaves nothing
half-done.

## Job `test` — Python, examples, lint

| Step | What it proves |
|---|---|
| `uv sync --extra dev` | the dev extra installs, including PyVRP and OR-Tools |
| cache cargo | shared key with the `rust` job; whichever runs second reuses it |
| **build the gateway** | so binary-dependent tests run instead of skipping |
| pull the OSRM image | keeps the pull out of the first test's timeout |
| `make test` | the pytest suite, `-m "not slow"` |
| `make examples-check` | every example still runs (~170 s) |
| `make lint` | `ruff check .` |

Two of those steps exist because of specific incidents, and both are worth
knowing:

**Building the gateway is a test-correctness step, not a build step.** The
parity harness's acceptance test and its replay regression gate drive the
gateway binary as a subprocess. Without this step they skip, "and CI goes green
having never run the two most valuable gates in the repo — which is exactly
what happened between #5 and this change".

**`make examples-check` is separate from `make test`** because the suite
filters `-m "not slow"` and this sweep takes ~170 s. "Without it nothing
executes an example, which is how two of them came to be broken against a fully
green suite."

## Job `rust` — the gateway itself

| Step | What it proves |
|---|---|
| cache cargo | ~160 crates; a cold build dominates the job |
| `cargo test --locked --manifest-path gateway/Cargo.toml` | 176 unit tests |
| `cargo check --locked --manifest-path rust-spike/Cargo.toml` | the spike still compiles |

The job exists because "a change to `gateway/` can merge with a compile error
and nothing notices — the Python job passes regardless, because it never
touches the crate".

`--locked` throughout, so CI builds the dependency versions that were tested
rather than resolving newer ones "and failing somewhere unrelated".

The spike has no tests — it is a benchmark target — but it must keep compiling
"or the comparison it exists to reproduce quietly rots".

## What actually gates a merge

Ranked by what each would catch, not by runtime:

1. **Parity replay regression** — both gateways over a recorded corpus, offline
   (doc 36). Needs the built binary.
2. **The independent verifier** across the property suite — INV-1…INV-9 over
   generated instances.
3. **Benchmark regression gate** — solution quality against
   `benchmarks/baseline.json` (doc 13).
4. **Traceability** — dangling identifiers, generated files out of step with
   their sources, a module claiming an unlanded task, a specification
   understating what the verifier checks.
5. **Tier 1 doc freshness** — the committed generated docs match a fresh build.
6. **`cargo test`** — 176 gateway unit tests, including the cross-language cache
   key digests.
7. **Examples** — 79 scripts actually run.
8. **Lint** — `ruff`.

Note what is *not* gated: `cargo clippy` is documented as a local command but no
CI step runs it, and there is no coverage gate.

## Tests that skip rather than fail — except in CI

`tests/conftest_gateway.py` exposes `requires_binary`, a skipif that is
**disabled under CI**: locally the binary may be missing and the test skips;
in CI the same absence is an assertion failure naming the fix. That is the
mechanism that makes step 3 of the `test` job load-bearing rather than
decorative.

`require_binary_built()` spells the reason out: "In CI these tests must run,
not skip."

## Running the gates locally

```sh
make test              # the suite
make examples-check    # the example sweep CI runs separately
make lint
cargo test --manifest-path gateway/Cargo.toml
make parity-selfcheck  # the harness's own tests, offline, no engine
make property-soak     # 10^5 generated instances against INV-1..INV-9
make tier1             # regenerate the derived docs before committing
```

`make property-soak` and `make corpus` are **not** in CI — they are slow sweeps
run deliberately.

## The one Linux check

CI is the only place this repository is exercised on Linux; development happens
on macOS and the jail deployment is FreeBSD. The `rust` job is therefore the
check that the deployed target still builds, and `process_*` metrics only
appear on the two Linux paths (doc 17).
