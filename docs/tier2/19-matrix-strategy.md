# 19 — Distance-matrix strategy

*Tier 2: authored from `vrp/matrix.py`, `vrp/osrm.py`, `gateway/src/osrm/`,
`gateway/src/handlers.rs`.*

A travel matrix is the input every solver decision rests on, and building one
at operational scale runs into three different walls: the engine's cell cap,
the URL length limit, and n². The work is split across two layers, and the
split is deliberate.

## Who owns what

| Layer | Owns |
|---|---|
| Gateway (`gateway/src/`) | transport: the `/table` call, retries, per-request caching, the cell cap, the URL ceiling |
| Platform (`vrp/matrix.py`, `vrp/osrm.py`) | translation into the domain model, tiling, pair-level caching, snapping, versioning |

`vrp/osrm.py` states the reason it is not all in the gateway: what lives in
Python "constructs `TravelMatrix`, which is a Python type the solver and
verifier both consume, and its rules change with the optimisation model rather
than with transport behaviour. Moving it into the gateway would put the domain
model behind an HTTP boundary for no gain."

## Three limits, three different responses

| Limit | Setting | Default | Crossing it gives |
|---|---|---|---|
| Cells per request | `MATRIX_MAX_CELLS` | 10,000 | 422, refused — never truncated |
| Upstream URL bytes | `OSRM_MAX_URL_BYTES` | 24,000 | 422 naming both figures |
| Stops per solve | `VRP_MAX_STOPS` | 2,000 | 422 |

The cell cap is refused rather than truncated because a silently smaller matrix
produces a confidently wrong plan. The URL ceiling was **measured, not
assumed**: `osrm-routed` 5.x answers up to ~24,750 bytes and drops the
connection from ~25,000, so an over-long `/match` surfaced as a 500 rather than
a refusal. 24,000 leaves margin and sits well above the ~13 KB a default VRP
`/table` batch builds. A proxy in front of the engine changes the answer —
nginx and Apache cap a request line near 8 KB.

## Tiling: how a big matrix gets built anyway

`plan_tiles(size, max_cells)` splits an n×n matrix into **square-ish blocks**,
not whole rows. The reason is stated: a row of a 5,000-location matrix is 5,000
cells, so a row-per-request scheme only works while the cap exceeds the
location count, and fails on exactly the instances that need tiling. Tiles
cover every cell exactly once — gaps leave holes, overlaps pay twice and invite
two answers for one pair.

Tiles are expressed as `sources`/`destinations` index lists over **one upload
of the coordinates**, so the coordinate list is sent once per tile request
rather than the tile's own sub-list being re-derived.

`build_large_matrix` produces a result identical to an unchunked build over the
same locations — same cells, same version — which is the acceptance condition
the examples check rather than assume.

## Pair caching: why a second cache above the gateway's

The gateway's cache keys on the endpoint path plus a digest of the parameters,
and for `/table` **the coordinates are the path**. Change one stop and the key
changes, so the whole matrix is refetched. The requirement is ≥90% pair reuse
on incremental days, and no request-keyed cache can reach that at any hit rate:
the unit of reuse has to be the pair.

`PairCache` is an LRU over `(origin, destination, profile)` → `(duration,
distance)`. Two details carry the design:

- **LRU rather than FIFO**, because the access pattern is not uniform: depot
  rows are touched every single day and are exactly what FIFO would evict after
  enough churn.
- **Coordinates are rounded to 7 decimal places** (~1 cm) before becoming keys.
  Two floats differing in the last bit describe the same doorway, and a cache
  that treats them as different pairs "has a hit rate of approximately zero on
  real geocoded data".

`reset_stats()` zeroes the counters without dropping entries, so one day's
reuse can be measured against a cache warmed by the day before — the shape the
90% target is stated in.

The cache is **in-memory only**. A persistent tier is acknowledged as unbuilt,
with the placement decision (Redis alongside the gateway's L2, or a local file)
explicitly still open: "Reporting an in-memory hit rate honestly is better than
pretending the persistence exists."

## Degradation: a failed tile does not lose the plan

When a tile fetch fails, `build_large_matrix` does not raise. What the cache
already held stands; what it did not stays `UNREACHABLE`, and the matrix is
returned carrying a `degraded` message naming how many tiles were never fetched
and why.

The comment records what the previous behaviour cost: "a provider that dies on
the last of forty tiles threw away thirty-nine good ones and the whole plan
with them". The current posture is that **nothing is invented — the plan simply
covers less ground and says so**.

Both the snap step and the tile fetch are injectable (`snap=`, `fetch=`)
specifically so a caller can exercise the degraded path without a gateway.

## Three translation rules that must not be got wrong

**Unreachable is not expensive.** OSRM reports a pair with no route as `null`.
Writing that as a large finite number — 10⁹ was this repository's own choice in
three examples once — makes it an arc a solver will use when nothing better
exists, and the plan comes back containing a leg no vehicle can drive. It
becomes the `UNREACHABLE` sentinel, which `TravelMatrix.duration()` refuses to
return at all.

**Snapping is data quality.** Every coordinate is snapped whether or not anyone
looks; the only question is whether the distance is recorded. Past
`DEFAULT_SNAP_THRESHOLD_M` (100 m) a `SnapWarning` is issued — a warning rather
than an error, since a far snap is a problem the caller may legitimately
accept. "A stop 2 km from the nearest road still produces a perfectly plausible
matrix; what it does not produce is a plan that serves the address anybody
meant."

**The version pins the profile.** `matrix_version` hashes the locations, the
profile and the OSM extract into `osrm:{profile}:{16 hex}`. A plan pins this
string and invariant INV-4 compares against it, so two profiles sharing a
version would let a van plan be validated against bicycle travel. The profile
appears in the hash *and* in the readable prefix: the hash makes it correct,
the prefix makes a mismatch diagnosable without recomputing anything.

## Snapping is batched, and the numbers say why

`_snap_all` sends `/nearest/batch` in windows of 1,000 — matched to the
gateway's committed `NEAREST_MAX_COORDINATES` so a large round is split into
batches the gateway will accept rather than refused with a 422 it could have
avoided.

Measured against the deployed gateway: **forty coordinates took 0.56 s one at a
time and 0.01 s batched**. Before batching, a 120-stop round opened 121
connections before a single matrix cell was fetched, and running the examples
in sequence saturated the gateway.

Two failures are named rather than worked around. A 404 (a gateway predating
`/nearest/batch`) raises telling you to redeploy, because a silent fallback to
one call per location "would restore the behaviour this exists to remove". A
short answer raises rather than zipping, which "would stop at the shorter
sequence and hand one location's plan another's road".

## Above the stored-matrix ceiling

A stored matrix stops being viable well before the 10,000-stop target: 10,000²
is 100 million cells, measured at **~3.8 GB and roughly twelve minutes just to
build** — a fifth of the hour-long budget spent before any search starts. Above
that, `PlanarMatrix` computes straight-line travel on demand from coordinates
at a fixed 30 km/h, and decomposition (doc 20) keeps the sub-problems inside
the stored regime. A test pins the planar matrix cell-for-cell against a stored
one built by the generator, so the two cannot drift apart.
