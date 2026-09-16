# 39 — Troubleshooting

*Tier 3: symptoms and their causes, assembled from the failure modes the code
itself documents. Every entry is something that has a named cause in the
source, not a guess.*

Start here: `make compose-doctor` (Docker) or `make jail-doctor` (jail). Both
report what the tooling is actually talking to, which is the answer surprisingly
often.

## HTTP responses that surprise people

| Symptom | Cause | Fix |
|---|---|---|
| **500** while the engine is plainly answering 503 | retries exhausted. Python raised `RetryError`, which its handlers did not recognise as an upstream status error; that is reproduced deliberately | check the engine, not the gateway. Raise `OSRM_RETRY_ATTEMPTS` only if the engine is flaky rather than down |
| **422** `Request needs a N-byte upstream URL, over the M-byte limit` | too many coordinates for one request | send fewer, or split. Only lower `OSRM_MAX_URL_BYTES` if a proxy fronts the engine |
| **422** `This deployment serves no routing graph for profile 'walking'` | `OSRM_URL_WALKING` is unset | stand up the graph, or stop requesting that profile. This refusal replaced silently answering from the car graph |
| **422** `Input should be a valid dictionary or object…` on a request that looks fine | no `Content-Type: application/json` — a bare `curl -d` sends none | set the header |
| **429** with no `Retry-After` | slowapi sent none, so neither does this | back off on your own schedule |
| **503** `Optimization capacity exhausted, retry shortly` **with** `Retry-After` | admission control, not failure | see the solve-queue row below |
| **404/405** with a JSON `detail` body | the fallbacks, deliberately shaped like Starlette's | — |
| a request on the **unversioned** surface gets no `Deprecation` header | the advisory is the innermost layer, so 429s and `Content-Type` 422s bypass it; `/nearest/batch` is missing from the advised list entirely | known gaps, doc 18 |

## "It says it is healthy but nothing works"

| Symptom | Cause |
|---|---|
| `/health` returns 200 while routing fails | **by design** — it always returns 200 and reports `"status":"degraded"`, `"osrm_backend":"down"` in the body. Read the body |
| a container reports healthy while the engine is down | the healthcheck must probe **`/ready`**, not `/health`: `curl -f` only fails on a non-2xx, and `/health` is always 200. The shipped `Dockerfile` gets this right — check any custom probe |
| `/ready` 503 | the engine is unreachable. Expected: it exists so a balancer drains the node |

## Cache and Redis

| Symptom | Cause | Fix |
|---|---|---|
| no `cache_lookups_total{tier="l2"}` series at all | no `REDIS_URL` — an unconfigured tier records nothing rather than a stream of misses | set it, if you wanted L2 |
| L2 configured, everything works, hit rate zero | Redis is unreachable. **Every error is logged and swallowed**; the jail ran this way for a while and served traffic throughout | check the gateway's log for `redis: connect failed` |
| in the jail: Redis reachable but every command DENIED | a jail has no per-jail loopback source address, so Redis sees a non-loopback peer and protected mode refuses. This silently disables both L2 **and** the shared rate-limit counter | `protected-mode no` plus the `ip4.addr += "lo1|127.0.0.1"` that `make jail-host` arranges |
| `REDIS_TTL=0` caches nothing | Redis rejects `EX 0`, and rounding up to 1 s would make "do not cache" mean "cache for a moment" | set a real TTL |
| hit rate looks impossibly high | L1 and L2 series were summed. **They are not independent** — L2 is only consulted after L1 misses | doc 17 |
| one slow first request against a dead Redis | connect and command are capped at 2 s each, with retries set to 0 | expected; the tier is meant to be skipped, not waited on |

## Rate limiting

| Symptom | Cause | Fix |
|---|---|---|
| every client shares one allowance | a proxy fronts the gateway and `FORWARDED_ALLOW_IPS` is empty, so the limiter keys on the balancer | set it to the proxy's address or CIDR |
| a client bypasses limits entirely | `FORWARDED_ALLOW_IPS=*` trusts the client-supplied header | never use `*` in front of untrusted clients |
| N instances allow N× the limit | no Redis, so each counts in process | give every instance the same `REDIS_URL` |
| a client sends double the limit in a few seconds | fixed windows, not rolling — inherited from slowapi | expected |
| the gateway **refuses to start**, naming a rate limit | a configured `RATE_LIMIT_*` did not parse. Deliberately fatal, because the alternative is an endpoint that comes up silently unlimited | fix the spelling: `600/minute`, `5/2minutes` |

## Solves

| Symptom | Cause | Fix |
|---|---|---|
| `/vrp` p95 pinned just above 10 s under load, then 503 | `VRP_MAX_QUEUE_DEPTH` ships at **0**, which disables the depth bound, so every rejected caller waits the full `VRP_QUEUE_TIMEOUT` first. Measured p50 10,008 ms | set the depth to a small multiple of `VRP_MAX_CONCURRENCY` |
| memory climbs with load until the box suffers | peak memory is stops × concurrent solves | lower `VRP_MAX_CONCURRENCY`, or assess with `make capacity`, which aborts before the OOM killer |
| the portfolio returns no winner | no engine produced a **legal** plan. Returning something regardless "would be the portfolio inventing one" | read the per-engine rejection reasons in the outcome |
| an engine is rejected on every instance | adapters decline what they cannot model — the OR-Tools one declines shipments | expected; that is why several engines run |
| set-partitioning polish returns `None` | the pool admits no exact cover. "A partial cover is not a cheap plan, it is undelivered freight" | more or more diverse pool runs |
| `NoChargerReachable` | no charger is reachable before the battery would go flat | the refusal is the answer: "a dispatcher can hire a diesel van in ten minutes and cannot un-strand a driver" |
| `INFEASIBLE` plus a lock set | operator locks conflict. The minimal conflicting set is returned rather than a lock being dropped | resolve the named locks |
| an order is unassigned but passes pre-flight | **different failure, different fix.** Pre-flight asks whether *any* vehicle could serve it in isolation | doc 28 |

## Matrices

| Symptom | Cause |
|---|---|
| `SnapWarning: N location(s) snapped more than 100 m…` | geocodes landed away from the road network. "A plan built on these serves the road, not the address" — a warning, because a caller may legitimately accept it |
| a matrix comes back with a `degraded` message | one or more tiles were never fetched. What the cache held stands; the rest is `UNREACHABLE`, not zero. "Nothing is invented — the plan simply covers less ground and says so" |
| `RuntimeError: … has no /nearest/batch` | the gateway predates the endpoint. Named rather than worked around, because falling back to one call per location restores the behaviour batching exists to remove |
| `ValueError: /nearest/batch answered N of M coordinates` | a partial answer. Zipping would "hand one location's plan another's road" |
| pair-cache hit rate near zero | coordinates are not being rounded, or the profile differs. Keys round to 7 dp (~1 cm) precisely so float noise cannot split one doorway |

## Docker

| Symptom | Cause | Fix |
|---|---|---|
| `docker compose up` finds no services | the compose file is not at the repository root | `make compose-up`, or pass `-f deploy/docker/docker-compose.yml -p osrm-microservice` |
| containers land on an unexpected network, or a second stack appears | `-p` omitted, so the project name defaulted to the directory (`docker`) | always pass `-p osrm-microservice` |
| `Cannot connect to the Docker daemon` with a `tcp://` host | the remote daemon publishes no TCP socket | use `ssh://user@host` |
| the wrong daemon entirely | `DOCKER_HOST` in the environment outranks what you expect | `make compose-doctor` reports the active host and its architecture |
| exec-format errors, or an unreasonably slow build | images pin `linux/arm64`; the host is not arm64 | let the binfmt step run (`docker run --privileged --rm tonistiigi/binfmt --install all`); it is allowed to fail, so check it actually succeeded |
| all three engines answer with car routing | `DATA_IMAGE` not varied per profile | use the shipped compose file; it tags `osrm-data-builder:car|bicycle|foot` |

## FreeBSD jail

| Symptom | Cause | Fix |
|---|---|---|
| `jexec` permission denied | it requires **real root** on the host | `make jail-doctor` names the fix: `doas`, `sudo`, or `JAIL_HOST=root@…` |
| `make jail-*` hangs, then fails with 255 long after the work finished | `daemon(8)` started the child with the ssh session's pipe still attached | already handled by `restart_service()`; never call `service … restart` directly over ssh |
| cargo build is killed | fat LTO in one codegen unit on a 2 GB box shared with two other jails | build with `--profile jail` (thin LTO, 16 units), which `install.sh` does |
| cargo cannot fetch crates | the jail has no resolver | `make jail-host` |
| the service will not start, complaining about `setusercontext` | `-u` passed to `daemon(8)` as well as `${name}_user`; rc.subr already wraps the command in `su -m` | do not add `-u` |
| the service dies immediately after an rc.conf edit | a value containing `;` reached `${name}_env`, which rc.subr expands unquoted — `HEALTH_CHECK_COORDS` is `0,0;0,0` | only `HOST`, `PORT` and `WORKERS` go through `${name}_env`; everything else belongs in `.env` |
| `FORWARDED_ALLOW_IPS=*` behaves strangely | rc.subr expansion globs `*` against the working directory | set it in `${JAIL_DIR}/.env` and restart |
| the log fills with escape sequences | ANSI colour | already disabled unconditionally; check for a custom build |
| no `process_*` metrics | they read `/proc`, which FreeBSD does not mount | expected. Use `make capacity`'s SSH probe for memory |
| a setting change has no effect | the overlay at the end of `${JAIL_DIR}/.env` wins — the parser takes the **last** occurrence of a duplicated key | edit the overlay, or the shared `deploy/env/app.env` for both deployments |
| changing `VRP_HYSTERESIS_M`, `REDIS_MAXSIZE` or `OSRM_API_URL` does nothing | all three are carried by `app.env` and **read by nothing** — `config.rs` lists them in `INTENTIONALLY_UNUSED` with reasons | `VRP_HYSTERESIS_M` is shadowed by the request field's own default; size Redis with `maxmemory-policy` |

## Logging and tracing

| Symptom | Cause |
|---|---|
| `DEBUG=true` yields info-level logs | a stray `RUST_LOG` used to override it. `DEBUG` is now authoritative for this crate; `RUST_LOG` still tunes dependencies |
| OTLP is configured and connected but traces are empty | historically, the layer was installed over no spans at all. If it recurs, check that the `http.server` span is being created |
| a caller's trace ends at the gateway | inbound extraction missing. Both halves are wired now; "only fixing the outbound half looks like it works right up until someone traces end to end" |
| SIGTERM takes a minute | span flush against an unreachable collector. Bounded to 2 s; beyond that it logs "collector unreachable, exiting without flushing spans" |

## Tests and tooling

| Symptom | Cause | Fix |
|---|---|---|
| binary-dependent tests skip locally | the gateway is not built | `cargo build --manifest-path gateway/Cargo.toml`. In CI the same absence **fails** rather than skipping |
| `differ from a fresh build; run make tier1` | the generated docs no longer match the code | `make tier1` |
| the catalogue test fails | `docs/TDD/vrp-catalogue-v2.1.md` was hand-edited; it is generated | edit the `.src.md` and run `make catalogue` |
| a corpus fix does not survive | the corpus is regenerated by its pipeline | change the generator |
| an example behaves differently on a fresh clone | it fell back to `deliveries_sample.json`, and its selection is outside `MANIFEST` | add the selection and `make examples-slice` |
| the suite is green but an example is broken | `make test` filters `-m "not slow"` | `make examples-check` — CI runs it as a separate step for this reason |
| a parity run reports divergence that is really misconfiguration | exit code 2 means misconfigured (rate-limited, half-down, unreachable); 1 means the candidate genuinely diverged | "a harness that cries wolf stops being run" — read the exit code |
| parity measures the map rather than the port | the two gateways point at different engines | both must use the same `osrm-routed` |
