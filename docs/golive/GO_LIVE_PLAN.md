# Go-live plan: deploying the suite and putting it in operation

**Status:** proposed, 2026-09-12. Nothing here is built. Every constraint cited
was confirmed against source, configuration or this project's own measured
results; the durations are planning estimates and are the only part that is not.

> **Versión en español:** [PLAN_PUESTA_EN_MARCHA.md](PLAN_PUESTA_EN_MARCHA.md).
> Both are maintained in parallel; where they diverge, this one is the copy
> written against the code.

Ten weeks, nine teams. Deployment itself is a days-scale exercise -- both paths
are scripted end to end with health gates, and the stack holds no persistent
state, so there is no migration to design and no restore to rehearse. The ten
weeks are almost entirely functional validation, which is where the risk is.

## Is it feasible

Yes, and the answer turns on two things this repository deliberately does not
contain.

**The gateway has no authentication and no TLS.** `gateway/src/openapi.rs`
states it and `the_document_declares_no_authentication` enforces it; SDD §5
lists "no TLS stack" among the dependencies. Nothing here can face a network you
do not fully trust until IT supplies an edge that terminates TLS and
authenticates callers. That edge is not in scope of this codebase, and it
carries its own trap: an L7 proxy without `FORWARDED_ALLOW_IPS` collapses every
client into one rate-limit bucket, and `*` defeats limiting entirely rather than
weakening it.

**`/vrp` is capacity and geography only.** No time windows, no service times, no
driver shifts or vehicle skills, and no time-of-day dimension anywhere -- every
plan assumes free-flow speed (SDD §6). The Python `vrp/` platform does carry
those concepts, but it is a library with no process hosting it, and how the Rust
gateway would reach it is an open design question `vrp/api.py` names rather than
answers. If dispatch needs delivery windows, this is a development project and
not a deployment. That is why the fit-gap is week one, before anything is
provisioned: finding it out in week five costs four weeks of hosts.

Everything else is in the suite's favour. Redis is a cache -- losing it costs
cache hits and nothing else -- so recovery is rebuild and redeploy, and rollback
is redeploying the previous build. Strip the functional program away and a
staging instance on a trusted network is **four to five working days of IT
effort**.

## What actually deploys

| Component | Origin | Notes for planning |
|---|---|---|
| API gateway | built from `gateway/` | the only thing this repo ships; same source on both paths |
| `osrm-routed` | stock OSRM | one engine serves one profile, fixed when the map is built |
| Redis | stock package or image | cache plus shared rate-limit counters; optional |
| Map data | Geofabrik extract, processed | ~180 MB download, then extract/partition/customize |
| Python `vrp/` | this repo, library only | **not a service**; out of scope for go-live |

Pick the deployment path in week one. Docker unless the target is specifically a
FreeBSD jail -- the jail path exists because a jail cannot run Docker, not
because it is preferable. On the jail path `make jail-host` is not optional: it
applies `net.inet.tcp.delayed_ack=0`, worth p50 67 ms -> 9 ms, and without it
the jail simply looks like slower hardware. Nothing errors and nothing logs.
`make jail-doctor` reports whether it is applied *and* persisted, so put that
check in the provisioning checklist rather than in someone's memory.

## Conditions, constraints and traps

| | Finding | Consequence | Closed by | Owner |
|---|---|---|---|---|
| **stop-ship** | no authentication, no TLS | cannot be exposed beyond a trusted network | an edge IT builds | IT-2 |
| **stop-ship** | no time windows, service times, skills or shifts | if dispatch needs windows, the optimiser cannot express it | week-one fit-gap | FN-1 |
| gate | L7 proxy without `FORWARDED_ALLOW_IPS` | every client shares one rate-limit bucket; `*` defeats it | set to the proxy CIDR, prove per-client buckets | IT-2 |
| gate | one engine per routing profile | three profiles triples build time, disk and RAM | confirm profile count, size against it | IT-1 |
| gate | no HA anywhere in the repo | host loss is an outage | accept single-node with a stated RTO, or fund HA | IT-1 |
| bound | matrix capped at 10,000 cells | wider matrices refused; raising it needs `--max-table-size` in **both** deployment files | size against real stop counts | IT-4 |
| bound | `VRP_MAX_STOPS=2000`; one such solve peaked 277 MB, four concurrent 615 MB | peak memory is stops x concurrent solves | set guards from a measured RSS ceiling | IT-5 |
| bound | coordinates travel in the URL; ~720 `/match` breadcrumbs, 24,000-byte ceiling | long traces refused with a 422 naming the limit | client chunks below the cap | IT-4 |
| bound | `process_*` metrics read `/proc`, absent on FreeBSD | CPU, RSS, FD and start-time panels blank on the jail | build dashboards that do not need them | IT-5 |
| bound | `make process-osrm` needs Docker even when targeting the jail | a pure-FreeBSD shop needs one Docker host, or `make jail-data` | decide which | IT-3 |
| bound | `make help` advertises `build-pkg`, `publish`, `clean-pkg` | none exist; they went with the PyPI package | note it in the local runbook | IT-4 |
| bound | docs count settings as 29, 35 and 36 | a config review driven from prose misses settings | review against `gateway/src/config.rs` | IT-4 |

## Teams

`IT-*` build and run the platform; `FN-*` define what a good plan is and make it
part of the working day. The codes group ownership; they are not an order of
work.

| Code | Team | Owns | Decides |
|---|---|---|---|
| IT-1 | Platform & Infrastructure | hosts, OS, daemon or jail, network, DNS, firewall | deployment path, sizing, HA posture |
| IT-2 | Security & Access | TLS, authentication, secrets, trusted-proxy config | how callers prove identity |
| IT-3 | Geospatial Data | extract, profile builds, refresh cadence, snapping | map currency, which profiles exist |
| IT-4 | Application & Release | build, config, `/v1` deprecation, release and rollback | release contents and cadence |
| IT-5 | SRE & Observability | metrics, alerts, capacity, runbook, on-call | alert thresholds, capacity guards |
| IT-6 | QA & Validation | test suite, parity gate, load runs, acceptance evidence | whether a build may be promoted |
| FN-1 | Business Analysis | fit-gap, requirements, KPIs, acceptance criteria | what the system is required to do |
| FN-2 | Fleet Operations & Dispatch | depots, vehicles, stop data, daily use, exceptions | whether a plan is drivable |
| FN-3 | Integration & Client Apps | the calling application, error handling, contract | how the API is consumed |
| FN-4 | Training & Change | SOPs, training, go-live support, feedback | when dispatchers are ready |

## Schedule

Calendar weeks, teams working part-time alongside existing duties -- which is
why elapsed weeks exceed the sum of working days. Infrastructure finishes early.

| Week | IT | Functional | Gate |
|---|---|---|---|
| 1 | IT-1 plan, IT-2 edge design | **FN-1 fit-gap and sizing** | G0 |
| 2 | IT-1 staging, IT-2 build, IT-3 map build | | |
| 3 | IT-3 validate, IT-4 deploy and config | FN-2 depots | G1 |
| 4 | IT-2 review, IT-4 tune, IT-5 metrics, IT-6 suite | FN-2 stops, FN-3 build | |
| 5 | IT-5 alerts, IT-6 acceptance | FN-2 criteria, FN-3 build | G2 |
| 6 | IT-5 capacity, IT-6 load | FN-3 error paths, FN-4 SOPs | G3 |
| 7 | IT-1/2/3 production, IT-4 release | **FN-2 parallel run**, FN-3 pilot, FN-4 train | |
| 8 | | FN-2 parallel run, FN-4 train | G4 |
| 9 | IT-4 cutover, IT-5 on-call, IT-6 gate | FN-2 live, FN-3 cutover, FN-4 support | G5 |
| 10 | IT-5 hypercare | FN-2 live, FN-4 support | |

## Team plans

Durations are working days of elapsed time for the activity, not headcount-days.
Where an activity is mostly machine time -- a map build, a Rust compile in a
2 GB jail -- it needs watching rather than staffing.

### IT-1 Platform & Infrastructure -- 9 days, weeks 1-2 and 7

| # | Activity | Days | Depends | Exit criteria |
|---|---|---|---|---|
| 1.1 | choose the deployment path, size the host against profiles, peak stops and concurrency | 1 | FN-1 sizing | written decision, host spec signed |
| 1.2 | provision staging -- Linux Docker host, or FreeBSD host plus jail | 2 | 1.1 | reachable, disk sized for extract plus graph |
| 1.3 | network, DNS, firewall: inbound only to the edge | 1 | 1.2 | engine unreachable from outside the host |
| 1.4 | **jail only:** `make jail-host`, then `make jail-doctor` | 1 | 1.2 | `delayed_ack=0` set *and* persisted; Redis not answering `DENIED` |
| 1.5 | baseline the target with `compose-doctor` / `jail-doctor`, recorded | 0.5 | 1.3, 1.4 | arch and memory recorded before any build |
| 1.6 | decide HA posture: single node with a stated RTO, or fund two | 1 | 1.1 | decision recorded with the accepted outage window |
| 1.7 | provision production mirroring staging exactly | 2 | G2 | doctor output matches staging |
| 1.8 | fold patching, access and change control into existing process | 0.5 | 1.7 | hosts in the standard inventory |

### IT-2 Security & Access -- 12 days, weeks 1-4 and 7

This team builds what the repository deliberately omits. None of it is gateway
configuration; it is a component in front of the gateway. It is the critical
path.

| # | Activity | Days | Depends | Exit criteria |
|---|---|---|---|---|
| 2.1 | design the edge: proxy, TLS termination, authentication method, per-endpoint authorisation if any | 2 | -- | design approved, certificate source identified |
| 2.2 | stand up the proxy with a valid certificate and automated renewal | 2 | 1.2, 2.1 | handshake clean, renewal *proven* not assumed |
| 2.3 | implement authentication, issue credentials per calling application | 3 | 2.2 | unauthenticated request refused at the edge |
| 2.4 | set `FORWARDED_ALLOW_IPS` to the proxy CIDR -- never `*` | 1 | 2.3 | two clients through the proxy each get their own allowance |
| 2.5 | confirm the gateway port is reachable only through the edge, the engine not at all | 1 | 2.4 | port scan from outside shows only the edge |
| 2.6 | secrets: nothing sensitive in `deploy/env/app.env`, which is committed | 0.5 | -- | no credential in any committed file |
| 2.7 | exposure review and sign-off | 2 | 2.5 | security sign-off recorded |
| 2.8 | replicate the whole edge in production | 1 | 1.7 | production edge independently verified against staging |

### IT-3 Geospatial Data -- 6 days, weeks 2-3 and 7

| # | Activity | Days | Depends | Exit criteria |
|---|---|---|---|---|
| 3.1 | `make download-data` | 0.5 | 1.2 | extract present, checksum recorded |
| 3.2 | `make process-osrm PROFILE=car`, or `make jail-data`; machine time, OOM-prone on a small host | 1 | 3.1 | graph built, peak memory recorded |
| 3.3 | each additional profile: a second pass and a second engine process | 1 each | 3.2 | one engine per profile, each with its own URL setting |
| 3.4 | **snapping validation** -- real depot and customer addresses through `/nearest` | 2 | 3.2 | snap-distance distribution reviewed with FN-2, outliers explained |
| 3.5 | agree refresh cadence and who triggers it | 0.5 | 3.4 | cadence documented with a named owner |
| 3.6 | rebuild for production, verified against the staging graph | 1 | 1.7 | same source extract, same profiles |

Worth telling dispatch early, from `docs/whitepapers/01`: a straight line
understates a real Costa Rica drive by ~40% at the median and by 17.6x in the
worst sampled pair -- 914 m apart, 16.1 km by road -- and two-thirds of GAM
address pairs differ out versus back. Plans will feel counter-intuitive at
first, and will usually be right.

### IT-4 Application & Release -- 8 days, weeks 3-4, 7, 9

| # | Activity | Days | Depends | Exit criteria |
|---|---|---|---|---|
| 4.1 | tag the release baseline from a green tree | 0.5 | -- | tag exists, CI green on it |
| 4.2 | review every setting against `gateway/src/config.rs` | 1 | 4.1 | config baseline stored with the release |
| 4.3 | deploy to staging: `make compose-up` / `make jail-up`, then `*-health` | 0.5 | 2.2, 3.2 | `/ready` passes, covering gateway and engine |
| 4.4 | smoke `/health`, `/ready`, `/metrics`, `/docs`, plus one real call per endpoint | 0.5 | 4.3 | every endpoint answers with real map data |
| 4.5 | apply capacity guards from IT-5's measurement | 1 | 5.4 | guards set from measurement, not defaults |
| 4.6 | versioning posture; `API_SUNSET` commits to a date | 0.5 | FN-3 | integrators on `/v1`, sunset set or deliberately empty |
| 4.7 | write and rehearse release and rollback | 1 | 4.3 | rollback rehearsed on staging, timed |
| 4.8 | build and deploy production | 1 | G3 | production answering behind the edge |
| 4.9 | cutover execution | 0.5 | G5 | traffic on the new path, rollback still available |
| 4.10 | convention: engine flags change in **both** deployment files, same commit | 0.5 | -- | recorded in the team's review checklist |

### IT-5 SRE & Observability -- 12 days, weeks 4-6 and 9-10

| # | Activity | Days | Depends | Exit criteria |
|---|---|---|---|---|
| 5.1 | scrape `/metrics` into Prometheus | 1 | 4.3 | series arriving and retained |
| 5.2 | dashboards: latency percentiles, error rate, cache hit ratio, upstream behaviour | 2 | 5.1 | renders correctly; no `process_*` dependence on the jail |
| 5.3 | alerts: 5xx rate, p95, `/ready` failing, engine unreachable, Redis down, rate-limit rejections climbing | 2 | 5.2 | each fired deliberately once and seen |
| 5.4 | **capacity**: `make loadtest` at expected rate, then `make capacity` stepped with the OOM guard, using `--forwarded-for-pool` | 2 | 5.1 | measured RSS ceiling and safe concurrency handed to IT-4 |
| 5.5 | log shipping and retention | 1 | 4.3 | logs searchable, retention agreed |
| 5.6 | adapt `docs/RUNBOOK.md` into the local operations runbook | 2 | 5.3 | a second engineer can restart the stack from it unaided |
| 5.7 | document RTO: no persistent state, so recovery is rebuild and redeploy | 1 | 3.2 | recovery timed once, end to end |
| 5.8 | on-call rota, escalation path, hypercare staffing | 1 | 5.6 | rota published before cutover |

Redis unavailability deserves its own alert: the limiter falls back to
per-process in-memory counting rather than failing requests, so the effective
limit silently multiplies by the worker count. It is available, and wrong.

### IT-6 QA & Validation -- 9 days, weeks 4-6 and 9

| # | Activity | Days | Depends | Exit criteria |
|---|---|---|---|---|
| 6.1 | `make test`, `make lint`, `cargo test` on the release commit | 0.5 | 4.1 | green, result attached to the tag |
| 6.2 | wire the parity replay gate into CI; `make parity-selfcheck` validates the harness offline | 1 | 6.1 | regression gate blocking merges |
| 6.3 | `make examples-check` against staging | 1 | 4.4 | all examples pass against the deployed instance |
| 6.4 | endpoint acceptance against `docs/API_REFERENCE.md` | 3 | 4.4 | signed evidence per endpoint |
| 6.5 | negative testing: oversized matrix, over-long trace, stops above the cap, queue saturation | 2 | 6.4 | refusals are diagnosable, not 500s |
| 6.6 | assemble the acceptance pack for the gate | 1 | 6.5, 5.4 | evidence complete, recommendation issued |
| 6.7 | production verification after cutover | 0.5 | 4.9 | same smoke pack green in production |

A run reporting 100% transport errors at ~2 ms on every endpoint, `/health` and
`/metrics` included, is pointed at nothing. That is connection-refused, not
stress. `LOADTEST_URL` defaults to the jail's port; the Docker path needs it
passed explicitly.

### FN-1 Business Analysis -- 8 days, week 1 and week 5

| # | Activity | Days | Depends | Exit criteria |
|---|---|---|---|---|
| 7.1 | **fit-gap against what `/vrp` does**: allocation to depots and per-vehicle sequencing by capacity and geography, and nothing about time | 3 | -- | every requirement marked met, worked around or out of scope -- signed |
| 7.2 | sizing: stops/day, stops/solve, vehicles, depots, profiles, peak concurrent solves, planning window | 2 | 7.1 | numbers handed to IT-1 |
| 7.3 | define what makes a plan acceptable, in dispatch's language | 2 | 7.1 | criteria FN-2 can apply daily |
| 7.4 | baseline today's KPIs before anything changes | 1 | -- | recorded; there is no second chance at this |

7.1 comes before everything. If the answer is that dispatch needs delivery
windows, the program stops and becomes a development project.

### FN-2 Fleet Operations & Dispatch -- 22 days, weeks 3-5 and 7-10

The largest functional effort.

| # | Activity | Days | Depends | Exit criteria |
|---|---|---|---|---|
| 8.1 | depot master data, each checked against the road vehicles actually leave from | 2 | 3.2 | every depot verified on a map, not in a spreadsheet |
| 8.2 | vehicle capacities, in the same unit demand is expressed in | 2 | 7.2 | fleet list complete and unit-consistent |
| 8.3 | **stop geocoding quality** -- the largest functional risk | 5 | 3.4 | snap outliers corrected or accepted |
| 8.4 | learn to read a plan; drive `make examples` against staging with real data | 3 | 4.4 | dispatchers interpret a plan unaided |
| 8.5 | apply the acceptance criteria to produced plans, recording disagreements | 2 | 7.3, 8.4 | a reviewed sample with recorded verdicts |
| 8.6 | **parallel run** -- plan the day both ways and compare before committing | 8 | G3 | two weeks side by side, variance explained |
| 8.7 | exception handling: refused plan, unsnappable stop, service unavailable | 2 | 8.6 | a fallback that does not need IT to execute |
| 8.8 | live operation with daily review during hypercare | 10 | G5 | KPIs tracking against the 7.4 baseline |

### FN-3 Integration & Client Applications -- 14 days, weeks 4-7 and 9

| # | Activity | Days | Depends | Exit criteria |
|---|---|---|---|---|
| 9.1 | agree the endpoints the client will call, from `API_REFERENCE.md` and live `/docs` | 1 | 4.4 | endpoint list agreed with FN-1 |
| 9.2 | build against **`/v1`**, not the unversioned root | 5 | 9.1, 2.3 | works end to end through the authenticated edge |
| 9.3 | handle 422 (over a documented cap), 429 (rate limit) and 503 (solve queue shedding) distinctly | 2 | 9.2 | each path exercised deliberately against staging |
| 9.4 | respect the caps client-side; chunk before sending rather than being refused | 2 | 9.3 | no avoidable 422s in a normal day |
| 9.5 | credential handling and rotation | 1 | 2.3 | rotation possible without a code change |
| 9.6 | pilot support and defect turnaround | 2 | 8.6 | pilot defects closed or accepted |
| 9.7 | repoint to production at cutover | 1 | 4.8 | client on production, staging still available |

### FN-4 Training & Change Management -- 9 days, weeks 6-10

| # | Activity | Days | Depends | Exit criteria |
|---|---|---|---|---|
| 10.1 | write the dispatcher SOP from plans FN-2 actually produced, not from API docs | 3 | 8.5 | reviewed by a dispatcher who did not write it |
| 10.2 | train dispatchers, including why road distance disagrees with the map in their head | 2 | 10.1 | every dispatcher has planned a day on the system |
| 10.3 | brief drivers and supervisors on what changes for them | 1 | 10.1 | delivered before the first live day |
| 10.4 | floor support through the first live weeks | 2 | G5 | questions answered on the floor, not by ticket |
| 10.5 | route genuine gaps back to FN-1 as change requests | 1 | 10.4 | backlog owned and prioritised |

## Checklists

Sequenced for the day the work is done. Each line is one verifiable outcome.

**IT-1 Platform.** Deployment path written down. Host sized against profile
count and peak concurrent solves. Staging provisioned and reachable. Disk sized
for extract plus graph, per profile. Engine port unreachable from outside the
host. *Jail:* `make jail-host` run; `jail-doctor` confirms `delayed_ack=0` set
and persisted. Doctor baseline recorded. HA posture decided and the outage
window accepted in writing. Production mirrors staging, doctor outputs compared.

**IT-2 Security.** Edge design approved. Certificate installed, renewal proven.
Authentication enforced -- an unauthenticated call is refused. Credentials
issued per calling application. `FORWARDED_ALLOW_IPS` set to the proxy CIDR.
Confirmed it is **not** `*`. Per-client buckets proven with two clients. Gateway
reachable only through the edge, with port-scan evidence. No secret in any
committed file. Exposure review signed off.

**IT-3 Geodata.** Extract downloaded, checksum recorded. Graph built for every
required profile. Peak build memory recorded. One engine per profile. Real
addresses run through `/nearest`. Snap outliers reviewed with dispatch. Refresh
cadence agreed with a named owner. Built graph or image archived so recovery
need not rebuild.

**IT-4 Release.** Release commit tagged from a green tree. Every setting
reviewed against `config.rs`. Deployed and `*-health` green. One real call
against every endpoint. Capacity guards set from IT-5's measurement. Integrators
on `/v1`, `API_SUNSET` decided. Rollback rehearsed and timed. Both-files
convention recorded for engine flags.

**IT-5 SRE.** `/metrics` scraped and retained. Dashboards independent of
`process_*` on FreeBSD. Alerts on 5xx, p95, `/ready`, engine unreachable and
Redis unavailability. Every alert fired once deliberately. Load test run with a
forwarded-for pool. Capacity assessment complete, RSS ceiling handed to IT-4.
Logs shipped with agreed retention. Local runbook proven by a second engineer.
Recovery timed, RTO published. On-call rota live before cutover.

**IT-6 QA.** Suite, lint and cargo tests green on the release commit. Parity
replay gate blocking in CI. `make examples-check` passes against staging. Every
endpoint accepted against the API reference. Oversized matrix refused cleanly.
Over-long trace refused with 422, not 500. Stops above the cap refused; queue
saturation sheds with 503. Load-test result sanity-checked. Acceptance pack
assembled.

**FN-1 Analysis.** Fit-gap signed *before any provisioning starts*. Confirmed
whether time windows are required. Confirmed whether service times, shifts or
skills are required. Accepted that plans assume free-flow speed. Sizing
delivered to IT-1. Acceptance criteria written in dispatch's language. KPI
baseline captured before anything changes.

**FN-2 Dispatch.** Every depot verified on a map. Capacities in the same unit as
demand. Stop geocodes reviewed, far snaps corrected or accepted. Dispatchers can
read an allocation and a sequence unaided. Criteria applied to a real sample,
verdicts recorded. Two weeks of parallel running complete. Every material
variance explained, not just noted. Fallback written that does not need IT.
Daily KPI review running.

**FN-3 Integration.** Endpoint list agreed. Client calls `/v1`, not the
deprecated root. Authenticated through the edge. 422 handled as fix-the-request,
not retry. 429 handled with backoff. 503 from queue shedding distinguished from
an outage. Client chunks below the caps. Credential rotation without a code
change.

**FN-4 Training.** SOP written from real plans. SOP reviewed by a dispatcher who
did not write it. Every dispatcher has planned a full day. Road-distance
intuition covered. Drivers and supervisors briefed. Floor support staffed.
Feedback routed to FN-1.

## Gates

| Gate | Week | Question | Evidence | Chair |
|---|---|---|---|---|
| G0 | 1 | can the shipped optimiser express what dispatch needs? | signed fit-gap, sizing, path chosen | FN-1 |
| G1 | 3 | is staging standing and answering with real map data? | `*-health` green, one real call per endpoint, config baseline | IT-4 |
| G2 | 5 | is the edge real and is the contract met? | unauthenticated call refused, per-client buckets proven, acceptance signed | IT-2 + IT-6 |
| G3 | 6 | does it hold under load, and can we see it? | capacity run, guards set from measurement, every alert fired | IT-5 |
| G4 | 8 | are the plans drivable? | two weeks parallel, variance explained, dispatch sign-off | FN-2 |
| G5 | 9 | go live? | all of the above, rollback rehearsed, on-call live, training delivered | program |

## After go-live

| Cadence | Activity | Owner |
|---|---|---|
| daily | dispatch reviews plans against the acceptance criteria, exceptions logged | FN-2 |
| weekly | KPI review against the pre-program baseline | FN-1, FN-2 |
| monthly | map refresh and redeploy, then re-run the snapping sample | IT-3 |
| monthly | capacity review against measured traffic | IT-5 |
| quarterly | dependency and base-image patching, rebuild, re-run acceptance | IT-4 |
| quarterly | certificate and credential rotation drill | IT-2 |
| on change | engine flags and resource limits in **both** deployment files, same commit | IT-4 |
| once | retire the unversioned root paths; set `API_SUNSET` to commit to the date | IT-4 |

## Assumptions, and what would change this

Assumed: one production environment plus staging, one region, one deployment
path; one routing profile at go-live; an existing dispatch or order application
to integrate with, so no new user interface; teams part-time, which is why
elapsed weeks exceed the sum of working days; IT can supply a reverse proxy;
single-node operation acceptable with a documented recovery time.

What would change the plan materially:

- **Delivery time windows are required.** The program becomes a development
  project. Rescope at G0 rather than working around it in dispatch procedure.
- **High availability is required.** Two nodes, a load balancer, shared Redis
  for the rate-limit counters, and the testing to prove failover. Nothing in the
  repository does this today; budget three to four weeks.
- **More than one region or extract.** Multiplies the data workstream and the
  host footprint. The gateway itself is unaffected.
- **Matrices wider than 100 coordinates.** An engine flag in both deployment
  definitions, and re-measuring memory afterwards.
- **The jail is the target and it is memory-constrained.** Build times lengthen
  and the extract can meet the OOM killer. Add a week to the data and build
  workstreams.
