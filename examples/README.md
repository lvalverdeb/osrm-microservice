# The examples

Seventy-two runnable programs. Each one takes a problem a fleet operator
actually has and shows what the suite does about it — and, because the code is
the point as much as the output, how you would write it yourself.

Run one:

```
uv run --package osrm-api-gateway-examples examples/src/fleet/rich/ride_time.py
```

or `make examples` for a menu. The `--package` matters: the examples are a
separate workspace package, and installing it is what puts `config` on
`sys.path` and `folium` in the environment.

---

## Planning a day's work

The constraints that decide whether a plan can actually be driven.

| | the problem it answers |
|---|---|
| `fleet/tw/multiple_windows.py` | The customer is out between 12 and 2. What does being late actually cost, and is a soft window worth breaking? |
| `fleet/tw/sla_windows.py` | Express, standard and scheduled are three different promises. Which ones did the round keep, and what did keeping them cost? |
| `fleet/tw/envelope_round.py` | Ten minutes a stop while the customer signs. When the signing is 86% of the day, the only decision left is how many couriers. |
| `fleet/rich/multi_capacity.py` | A van is full when *any* dimension runs out — weight, volume, pallets. Totals are the wrong test and pass instances that cannot be loaded. |
| `fleet/rich/heterogeneous_fleet.py` | Six depots and a mixed fleet: the shape most businesses have, rather than one depot and identical vans. |
| `fleet/rich/skills_and_access.py` | Three plans that look perfect and cannot be driven — no tail-lift, no ADR ticket, a low bridge. |
| `fleet/rich/hours_of_service.py` | A long-haul day under EU-561 and US hours-of-service, and what the law costs you in delivered stops. |
| `fleet/rich/departure_scheduling.py` | Leaving at first light wastes two hours of duty waiting at a closed door. Choosing the departure gets them back. |
| `fleet/rich/multi_trip.py` | The van empties at noon and could do a second trip — if a bay is free. Eight bays, forty vans. |
| `fleet/rich/ride_time.py` | How long a shipment may be aboard is not the same as when it may be dropped, and conflating them plans illegal routes. |
| `fleet/rich/synchronisation.py` | The cargo bikes cannot leave before the lorry arrives. Two routes that must meet. |
| `fleet/rich/multi_period.py` | Four inspections a year is not four inspections in January. Spacing visits across a horizon. |
| `fleet/rich/ev_recharging.py` | An electric van that has to stop for electricity, and a plan that knows when — with charging time in the duty, not bolted on after. |
| `fleet/verify_delivery_plan.py` | The whole path end to end: build a plan from real deliveries, cost it, and check it with something that shares no code with the planner. |

## Traffic, and time that moves

| | the problem it answers |
|---|---|
| `fleet/rich/time_dependent.py` | Leaving later must never mean arriving earlier. The property a naive congestion model breaks, and what breaking it does to a plan. |
| `fleet/rich/planning_under_congestion.py` | Knowing about the peak is not the same as planning around it. A plan built at free flow is faster on paper and late in the street. |
| `fleet/rich/arc_class_profiles.py` | Rush hour on the ring road is not rush hour on a lane. One congestion factor for every road says something false about both. |
| `fleet/learn/speed_calibration.py` | Where the traffic numbers come from: fitted from what the vans actually did, against what the routing engine believed. |

## What to serve, and what to decline

Commercial decisions the router should be making rather than a human.

| | the problem it answers |
|---|---|
| `fleet/objective_modes.py` | Is this delivery worth driving to? Asked of the objective, where the answer is auditable, rather than of the solver. |
| `fleet/rich/prizes_and_priority.py` | Work worth declining, and work that is not for sale at any price. No prize large enough inverts a protected tier. |
| `fleet/rich/priority_sources.py` | A legal duty, a contract and a preference are not three weights on one scale, and treating them as one sells the statutory obligation. |
| `fleet/dynamic/prize_collecting_epoch.py` | Letting the router decide what is worth sending this wave, instead of dispatching everything and hoping. |

## How many vans, which, and from where

| | the problem it answers |
|---|---|
| `fleet/alloc/fleet_mix.py` | Which vans go out today, and what the last one is actually worth — the marginal vehicle, priced. |
| `fleet/alloc/fleet_minimisation.py` | The route a distance-minimising search will never remove, because dropping it costs distance and saves a vehicle. |
| `fleet/alloc/tactical_sizing.py` | How many vans to own, decided against thirty days of demand rather than one — including what a failed delivery costs. |
| `fleet/alloc/territories.py` | What it costs to send the same driver to the same street every week, and what you buy with it. Draws the territories, so a wedge and a scattering can be told apart. |
| `fleet/alloc/depot_inventory.py` | A depot is not a spring. Allocating more than the stock on hand is a plan that stops at lunchtime. |
| `fleet/rich/large_instance_decomposition.py` | Two thousand stops, fifty sub-problems, and the constraint none of them can see on its own. |

## When the day goes wrong

| | the problem it answers |
|---|---|
| `fleet/dynamic/breakdown_at_eleven.py` | A van goes down at 11:00. Most of the day should not move, and the part that must should be the smallest part. |
| `fleet/dynamic/committed_state.py` | Eleven o'clock, and the morning is not up for discussion. What is already delivered cannot be re-planned. |
| `fleet/dynamic/preemption.py` | A gas escape does not queue behind six routine jobs, and what it displaces has to be reported by name. |
| `fleet/dynamic/dispatch_waves.py` | Send it now, or wait and send it with the next three? And what may never wait. |
| `fleet/dynamic/insertion_quote.py` | Pricing a stop while the customer is still on the phone — without replanning a day that is half driven. |
| `fleet/dynamic/churn_tradeoff.py` | What a quiet afternoon is worth, in money. Every re-optimisation moves work between drivers; some of that is not worth having. |
| `fleet/dynamic/sample_scenario_policy.py` | Guessing at tomorrow to decide about this afternoon: holding capacity back for orders that have not arrived. |
| `fleet/dynamic/replay_policies.py` | Ninety days, three dispatch policies, one number each. Which one you should actually run. |

## "Why can't you do that?"

A refusal a dispatcher cannot act on is worse than a bad plan.

| | the problem it answers |
|---|---|
| `fleet/explain/preflight_diagnosis.py` | Why can't this stop be served? Answered before solving, and specifically enough to fix. |
| `fleet/explain/why_unassigned.py` | The other half: the plan came back and left three stops out. Which, and why each one. |
| `fleet/rich/locks_and_overrides.py` | Which two of your twelve manual instructions contradict each other, named rather than silently dropped. |
| `fleet/adversarial/pathological_instances.py` | Fifteen instances that break routing engines — the order nobody can carry, the window that closed, the pair that must not share a van. |
| `fleet/p0/must_work_at_v1.py` | The fourteen operations that must work before this is worth shipping, each with the obvious wrong answer it has to avoid. |

## Trusting the answer

| | the problem it answers |
|---|---|
| `fleet/verify/external_plan.py` | Checking a plan we did not make — a customer's, or last year's system's. |
| `fleet/infra/plan_snapshots.py` | Proving a plan came from the inputs somebody says it came from, months later, to somebody who is not asking politely. |
| `fleet/infra/run_record.py` | What a run has to be able to tell you afterwards: what it tried, when it improved, what it violated, what it was seeded with. |
| `fleet/rich/engine_portfolio.py` | Two solvers, one scoreboard, and why a portfolio must never believe an engine's own score. |
| `fleet/rich/set_partitioning_polish.py` | What a search throws away, and whether recombining the discards is worth the arithmetic. |

## Learning from what actually happened

| | the problem it answers |
|---|---|
| `fleet/learn/service_time_calibration.py` | Five minutes on paper, seven in the street. Fitting service times from telematics instead of guessing. |
| `fleet/learn/plan_adherence.py` | When three drivers reverse the same route, the map is wrong — measuring where the plan and the street disagree. |
| `fleet/learn/zone_sequence_prior.py` | Twenty days of drivers going south first. Learning the sequence they prefer and warm-starting from it. |
| `fleet/learn/canary_rollout.py` | Deciding in advance what would make you stop a rollout, rather than arguing about it afterwards. |

## Running it in production

| | the problem it answers |
|---|---|
| `fleet/infra/degraded_matrix.py` | The matrix provider stops answering mid-build. That should cost you the arcs it did not deliver, not the day. |
| `fleet/infra/portfolio_parallelism.py` | Running the solver portfolio wide — and measuring whether it helped, which for half the engines it does not. |
| `fleet/infra/process_portfolio.py` | Giving a pure-Python engine a core of its own, when threads cannot. |
| `fleet/infra/decomposition_queue.py` | Solving the clusters at once, and finding out at what size that starts being worth it. |
| `fleet/infra/accelerator_profile.py` | Proving a GPU is optional — demonstrated on a machine that has none, which is the only place that claim can be tested. |

## The HTTP surface

These call the gateway rather than the library, and need one running.
`examples/.env` points at it.

| | shows |
|---|---|
| `routing/route_advanced_options.py` | `/route` and its full option surface |
| `routing/matrix_example.py` | `/matrix` — the travel-time table every planner starts from, and how to ask for one |
| `routing/matrix_graph_example.py` | `/matrix-graph`, and the graph shape it returns |
| `routing/match_example.py` | `/match`, snapping a GPS trace to the network |
| `routing/nearest_example.py` | `/nearest` — snapping a customer's pin to a road a van can actually stop on |
| `routing/tile_example.py` | `/tile`, and the protobuf it answers with |
| `routing/error_handling_demo.py` | what every failure mode looks like to a client |
| `infra/health_and_metrics.py` | `/health`, `/ready` and the Prometheus scrape |
| `fleet/clustering_mode_comparison.py` | the clustering modes, side by side |
| `fleet/hysteresis_demo.py` | the hysteresis band that stops a stop flapping between depots |
| `clustering/run_clustering_workflow.py` | road distance against travel time, on the 2,000 deliveries two depots could each claim |
| `clustering/simple_id_example.py` | custom stop identifiers through the API, using the corpus's own delivery ids |

## Benchmarks and tools

`benchmarking/published_instances.py` scores the engine on problems other
people have already solved, which is the only honest way to say it is any good.

Tools are utilities rather than demonstrations. They are listed apart so a
reader looking for "how do I use this" does not have to sift them out.

| tool | does |
|---|---|
| `fleet/generate_delivery_dataset.py` | builds `data/deliveries_cr.json`, snapping every point through OSRM |
| `fleet/stress_test_vrp.py` | drives the VRP endpoint at volume |
| `fleet/visualize_vrp.py` | renders a plan to an HTML map |
| `routing/visualize_routes.py` | renders routes to an HTML map |
| `clustering/generate_payload.py` | builds a clustering payload from the corpus — deterministic, so it is an artifact rather than a commit |
| `benchmarking/compare_tsp.py` | compares TSP results across engines |

---

## Writing one

Two purposes, and everything below follows from them:

1. **Showcase the suite's functionality** — on work that looks like a
   customer's, not a toy grid.
2. **Provide implementation clues** — an example should read as the code
   somebody integrating the suite would copy.

A third thing they must **not** be is *proof*. Proof is a test's job, and the
distinction is not cosmetic: proving a property tempts you into reaching for
internals, and an example that monkeypatches a private function shows a reader
a technique they cannot use.

- **Public API only.** If a demonstration needs a private hook, it is a test.
  Where an example legitimately needed something private, the library changed —
  `must_be_served` moved to `vrp.model`, `build_large_matrix` gained
  `snap=`/`fetch=` seams — rather than the example reaching in.
- **Offline by default.** An example that needs infrastructure is one most
  readers never see work. Where a road matrix is wanted and no gateway is
  there, `dataset.road_matrix_or_planar` falls back to straight-line distances
  **and says so**, because a planar number is not the road's number.
- **Real work.** `dataset.py` slices the Costa Rica corpus. Take a slice rather
  than inventing coordinates, unless the instance itself is the subject — a
  maximal record for serialisation, an adversarial fixture, a known speed
  profile whose ground truth is the point.
- **The corpus is here, in miniature.** The full 50,000-delivery
  `data/deliveries_cr.json` is 12 MB and needs a live OSRM to build, so it is
  not committed. `examples/data/deliveries_sample.json` is, and `dataset.load`
  falls back to it. It is not a sample: it is the union of the slices the
  examples take, built by
  [`tools/build_sample_slice.py`](tools/build_sample_slice.py) so that every
  selection returns the *same deliveries* it returns from the full corpus, and
  an example prints the same numbers on a fresh clone as on a machine with the
  generated dataset. `tests/test_sample_slice.py` holds that contract.
  An example reaching for a selection the slice does not underwrite — a
  `spread` pool past 2,000, a `by_province` restriction — should add it to that
  file's `MANIFEST` and rebuild, rather than quietly print different numbers
  depending on who is running it.
- **The first docstring line is the title.** It is what the menu shows and what
  the tables above quote, so make it the problem, not the mechanism.
- **Run it.** `make examples-check` executes every one and is a CI step.
  Nothing ran them before, and two had rotted against a fully green suite.

Each VRP example also carries an `E-nn` row in
[`docs/planning/VRP_TDD_EXAMPLES.md`](../docs/planning/VRP_TDD_EXAMPLES.md)
tracing it to the requirement and task it demonstrates.
