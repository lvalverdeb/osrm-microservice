# 16 — Rate limiting and admission control

*Tier 2: authored from `gateway/src/ratelimit.rs`, `admission.rs`, `main.rs`.*

Two unrelated mechanisms protect two different resources. Rate limiting bounds
how often **one client** may call an endpoint. Admission control bounds how many
**solves** run at once, regardless of who asked. A request can pass one and be
refused by the other.

## Rate limiting

### Fixed windows, because that is what slowapi did

`600/minute` means 600 requests per wall-clock minute, not a rolling average —
the fixed window of slowapi's underlying `limits` library. A client can
therefore send 1,200 requests across a window boundary; that is inherited
behaviour, not an oversight.

`Limit::parse` accepts the same spellings: `600/minute`, `5/2minutes`, and the
`second`/`minute`/`hour`/`day` granularities. An unparseable value is a
**startup failure** for a configured endpoint, because the alternative is an
endpoint that comes up silently unlimited; an empty value means "unset", as it
does for every other setting.

The 429 body is slowapi's, verbatim: `{"error":"Rate limit exceeded: 2 per 1
minute"}`. Note it carries **no `Retry-After`**, because slowapi sent none.

### Which endpoints have buckets

The seven `RATE_LIMIT_*` settings in doc 04 cover `/route`, `/matrix`,
`/match`, `/trip`, `/vrp`, `/nearest`, `/nearest/batch` and `/tile`.
`/health`, `/ready` and the metrics scrape are deliberately unlimited so probes
and scrapes are never shed.

`/nearest/batch` is limited an order of magnitude lower than `/nearest` (60 vs
600 a minute) because one request is many engine calls: at the
1,000-coordinate cap, 60 a minute is already 60,000 upstream snaps.

The versioned and unversioned spellings of an endpoint **share one bucket**.
`version.rs` explains why this has to be true: otherwise a client doubles its
quota by alternating, and an unstripped prefix would have no bucket at all. A
test pins that every path advised for migration also has a bucket.

### Keying: the part that is easy to get wrong

Behind a reverse proxy the TCP peer is the proxy, so every client would share
one bucket. `X-Forwarded-For` fixes that, but trusting it unconditionally lets
any client mint a fresh allowance per request by spoofing a header.

`client_key` resolves this the way uvicorn's `ProxyHeadersMiddleware` does:

1. If nothing is trusted, or the peer itself is not trusted, **use the peer**
   and ignore the header entirely.
2. Otherwise walk the forwarded chain **from the right**, and key on the first
   hop outside the trusted set — anything further left was supplied by that
   client.
3. If every hop is trusted, key on the leftmost.

`FORWARDED_ALLOW_IPS` accepts bare addresses, CIDR blocks, and `*`. `*` trusts
every peer, which means any client can set its own limiter key; the entrypoint
warns about it.

### Shared counting, and what happens when Redis goes

`check_shared` increments a Redis counter when Redis is configured, and falls
back to the in-process counter when Redis does not answer — slowapi's
`swallow_errors` posture. This matters more than it sounds: counting only in
process means N instances behind a balancer allow N times the configured limit.
The limit silently becomes a per-instance one.

The Redis key is `ratelimit:{client}:{endpoint}:{window_id}` where `window_id`
is `now / window_seconds` — **the same window identity as the local path**, so
an instance that falls back mid-window keeps counting in the same bucket. Only
the request that creates a window sets its expiry, so the window does not slide
forward with every hit inside it.

The in-process map prunes stale windows on write once it exceeds
`PRUNE_THRESHOLD` (10,000 buckets). A poisoned mutex is recovered rather than
propagated: a thread that panicked holding the lock would otherwise turn one
failure into a panic on every subsequent request.

## Admission control for solves

`/vrp` and `/vrp/allocate` pass through an `AdmissionGate` with three bounds:

| Setting | Committed default | Bounds |
|---|---|---|
| `VRP_MAX_CONCURRENCY` | 1 | solves running at once |
| `VRP_QUEUE_TIMEOUT` | 10.0 s | how long a request waits for a slot |
| `VRP_MAX_QUEUE_DEPTH` | **0 — disabled** | how many may wait at all |

Concurrency is capped because **peak memory is stops × concurrent solves**.

The queue timeout alone was not enough, and the module records the measurement
that proved it: under sustained overload every rejected caller paid the full
timeout before hearing no — **p50 10,008 ms against a 10-second timeout** on
the jail deployment. "That is the worst of both: the caller waits, and still
fails."

Hence the depth bound. Past `VRP_MAX_QUEUE_DEPTH` waiters, a request is
refused in microseconds instead of joining a queue that is not draining. A
modest queue still waits, because a solve usually takes well under a second and
a queued request is often served.

**But `deploy/env/app.env` ships it at 0, which disables it.** Zero is the
documented way to "restore the wait-only behaviour", so both deployments
currently run with the queue timeout as the only bound — the configuration whose
measured behaviour is the p50 10,008 ms above. Whether that is deliberate (the
bound was added as an opt-in) or an unset knob is not recorded. Setting it to a
small multiple of `VRP_MAX_CONCURRENCY` is what turns the mitigation on.

Refusal is `503` with `Retry-After` and
`{"detail":"Optimization capacity exhausted, retry shortly"}` — unlike the 429,
this one does tell the client when to come back.

The waiter count is maintained by a `WaitingGuard` whose `Drop` decrements it.
The reason is stated: an axum handler whose client disconnects is dropped
mid-await, and a hand-rolled decrement would leak a waiter every time that
happened.

## Interaction worth knowing

`rate_limit` sits **outside** `require_json_body` in the middleware stack, so a
request with a wrong `Content-Type` still consumes rate-limit quota before it
is rejected. It sits **inside** `observe`, so a shed 429 is counted.

`announce_deprecation` sits inside both, so **a 429 on the deprecated
unversioned surface carries no `Deprecation` header** — confirmed by request.
A client that only ever sees 429s would never be told to migrate.
