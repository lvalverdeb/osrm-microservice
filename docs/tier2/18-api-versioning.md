# 18 — API versioning and the deprecation window

*Tier 2: authored from `gateway/src/version.rs`, `main.rs`, `metrics.rs`, and
confirmed against a running gateway.*

`NFR-10` requires that the public API be versioned and that breaking changes
get a deprecation window. The gateway served everything unversioned, so there
was no way to make a breaking change and keep that promise. The fix is
structural rather than cosmetic, and its consequences reach into rate limiting,
metrics and the response headers.

## Every data-plane endpoint is served twice

`main.rs` mounts one `data_plane()` router **twice** — nested under `/v1` and
merged at the root — rather than declaring the routes a second time, so the two
spellings cannot drift:

```rust
.nest(version::PREFIX, data_plane())
.merge(data_plane())
```

`/v1/...` is the surface to integrate against. The root spelling exists only
for the deprecation window.

**`/health`, `/ready`, the metrics scrape and the docs endpoints are not
versioned, and never will be.** They are an operator's contract with its own
orchestrator, not a client integration; versioning them "would break every
liveness probe and Prometheus job for a promise nobody asked for".

## Only this version's prefix is stripped

`strip_served_version` removes `/v1` and nothing else. `/v2/route` and `/v10/route`
are returned unchanged, and so is the bare `/v1`, which is a prefix rather than
an endpoint.

The reasoning is worth preserving: treating any `/vN` as strippable "would give
a future version's traffic this version's behaviour — silently, and in the
direction that breaks the promise the version number exists to make". A
`/v2/route` request against a v1 build should 404, not be quietly served.

## Path-keyed decisions have to agree — except where they must not

Serving one handler under two paths forces every path-keyed decision in the
gateway to take a position:

| Decision | Folds the versions together? | Why |
|---|---|---|
| Rate limiting | **yes** | otherwise a client doubles its quota by alternating, and an unstripped prefix has no bucket at all |
| Metrics `handler` label | **no** | the point of the window is watching unversioned traffic fall to zero; a dashboard that could not tell them apart could not say when it was safe |
| Deprecation advisory | applies to the root spelling only | a client that migrated must not keep being told to migrate |

A test in `version.rs` pins the first row from the other direction: every path
advised for migration must have a rate-limit bucket, "or one of them has been
extended and the other forgotten".

## The advisory

On an unversioned response whose path has a `/v1` successor,
`announce_deprecation` adds:

- `Deprecation: true` — RFC 9745.
- `Link: </v1/{path}>; rel="successor-version"` — RFC 8288.
- `Sunset: {API_SUNSET}` — RFC 8594, **only when `API_SUNSET` names a date**.
  Committing an operator to a date they did not choose was judged worse than
  sending none.

Header values are built from a routed path and an operator-supplied setting; a
malformed one is dropped rather than made fatal, "since a working response with
no advisory beats a 500 with one".

The advisory fires only where the successor exists. A 404 on a path the gateway
never served is a client mistake, and answering it with a migration notice
would invent an endpoint.

## Two gaps, both confirmed against a running gateway

**1. `/nearest/batch` is served on both spellings but is not advised.** The
`VERSIONED` list in `version.rs` contains eight paths and `has_versioned_successor`
matches them exactly, plus a `/tile/` prefix. `/nearest/batch` is absent, so an
unversioned call to it carries no `Deprecation` header — verified by request. It
is rate limited (`RATE_LIMIT_NEAREST_BATCH`) and it does have a `/v1`
counterpart, so the omission looks like an oversight rather than a decision.
The existing test cannot catch it: it checks that everything advised is
limited, not that everything served is advised.

**2. Responses produced by an outer middleware carry no advisory.**
`announce_deprecation` is the innermost layer, so it only sees responses from
the router. Confirmed by request:

| Unversioned request | Status | `Deprecation`? |
|---|---|---|
| valid body, reaches the handler | 422 | **yes** |
| no `Content-Type` | 422 | no |
| over the rate limit | 429 | no |
| wrong method | 405 | **yes** |

A client that only ever gets rate limited is never told to migrate.

## Migrating a client

1. Change the base path from `/` to `/v1/`. Nothing else changes: the same
   handler, the same request and response shapes, the same rate-limit bucket.
2. Watch `http_requests_total{handler="/route"}` (unversioned) fall against
   `handler="/v1/route"`. That is the signal the alias can be removed.
3. Set `API_SUNSET` when a date is chosen, so clients are told.
