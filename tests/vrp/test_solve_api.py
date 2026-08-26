"""Solve jobs: idempotency and the anytime incumbent — §9.4, NFR-03, T-15, E-15.

Two properties, both of which are easy to fake and worth testing accordingly.

**Idempotency.** §9.4 makes `/solve` asynchronous, which means a client that
does not hear back cannot tell a lost response from a lost request, and will
retry. Without an idempotency key that retry starts a second solve of the same
problem — burning the budget twice and, worse, possibly returning a different
plan from the one already sent downstream.

**Anytime (NFR-03).** "The solver MUST hold a best-known feasible incumbent from
the first construction onward and return it on any timeout or cancellation."
The trap is that a job which simply blocks until finished and *then* publishes
satisfies every naive test of this: ask for the incumbent after it is done and
there it is. So the tests here read the incumbent while the solve is
demonstrably still running, and check that cancelling early returns a usable
plan rather than nothing.

The incumbent is real rather than simulated: PyVRP's `Model.solve` accepts an
`initial_solution`, so the job solves in chunks and warm-starts each from the
last. Note what that buys, since the first draft of this file got it wrong:
chunks run at a fixed seed, so a cold restart is *deterministic* and returns
the same plan forever. Both warm and cold are monotone. The warm start is what
makes the incumbent **improve**, and a test asserting only "never gets worse"
passes with it removed entirely.
"""

from __future__ import annotations

import time

import pytest

from vrp.generate import Shape, generate_instance
from vrp.jobs import JobStatus, SolveService
from vrp.verify import verify


@pytest.fixture
def service() -> SolveService:
    return SolveService()


@pytest.fixture
def problem():
    return generate_instance(21, shape=Shape.SLACK)


# --------------------------------------------------------------------------
# Problem registration (§9.4's POST /v1/problems)
# --------------------------------------------------------------------------

def test_registering_a_problem_returns_an_id_and_its_diagnostics(service, problem):
    """§9.4: "create + validate, returns problem_id + diagnostics". The
    diagnostics are E-14's pre-flight pass, run once at registration rather
    than left for the solver to discover."""
    registered = service.register(problem)

    assert registered.problem_id
    assert registered.diagnostics == {}, "this instance is servable"


def test_registration_reports_orders_no_vehicle_can_serve(service):
    """An unservable order is worth knowing before spending a solve budget."""
    from vrp.model import Order, StopSpec

    base = generate_instance(22, shape=Shape.SLACK)
    impossible = Order(id="IMPOSSIBLE", kind="JOB", quantities={"units": 10**9},
                       delivery=StopSpec(location_id=base.locations[1].id,
                                         time_windows=(base.vehicles[0].shift,),
                                         service_fixed=60))
    problem = base.__class__(id=base.id, locations=base.locations,
                             orders=(*base.orders, impossible),
                             vehicles=base.vehicles, matrix=base.matrix)

    registered = service.register(problem)
    assert registered.diagnostics["IMPOSSIBLE"].code == "CAPACITY_EXCEEDED"


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------

def test_the_same_idempotency_key_returns_the_same_job(service, problem):
    """A retried request must join the original solve, not start a second."""
    registered = service.register(problem)
    first = service.solve(registered.problem_id, iterations=400,
                          idempotency_key="abc-123")
    second = service.solve(registered.problem_id, iterations=400,
                           idempotency_key="abc-123")

    assert first.job_id == second.job_id
    assert service.job_count() == 1, "the retry started a second solve"


def test_a_different_key_starts_a_different_job(service, problem):
    """Otherwise "idempotent" would mean "only ever solves once", and a second
    genuine request would silently receive the first one's answer."""
    registered = service.register(problem)
    first = service.solve(registered.problem_id, iterations=200,
                          idempotency_key="one")
    second = service.solve(registered.problem_id, iterations=200,
                           idempotency_key="two")

    assert first.job_id != second.job_id
    assert service.job_count() == 2


def test_no_key_means_no_deduplication(service, problem):
    """Idempotency is something a client opts into by supplying a key. Guessing
    that two keyless requests are "the same" would collapse legitimately
    repeated solves."""
    registered = service.register(problem)
    first = service.solve(registered.problem_id, iterations=200)
    second = service.solve(registered.problem_id, iterations=200)

    assert first.job_id != second.job_id


def test_a_key_is_scoped_to_its_problem(service):
    """The same key against a different problem is a different request, and
    returning the first problem's plan for the second would be a serious
    confusion of one customer's day with another's."""
    one = service.register(generate_instance(23, shape=Shape.SLACK))
    two = service.register(generate_instance(24, shape=Shape.SLACK))

    job_one = service.solve(one.problem_id, iterations=200, idempotency_key="k")
    job_two = service.solve(two.problem_id, iterations=200, idempotency_key="k")
    assert job_one.job_id != job_two.job_id


# --------------------------------------------------------------------------
# Anytime behaviour (NFR-03)
# --------------------------------------------------------------------------

def test_an_incumbent_is_readable_before_the_job_finishes(service, problem):
    """NFR-03's actual claim, tested against a job that is still running.

    A job that blocks and publishes at the end satisfies a lazier version of
    this test perfectly, so the assertion is made while `status` is RUNNING.
    """
    registered = service.register(problem)
    job = service.solve(registered.problem_id, iterations=200_000)

    incumbent = None
    for _ in range(200):                       # up to ~10s
        if service.status(job.job_id) is JobStatus.RUNNING:
            incumbent = service.incumbent(job.job_id)
            if incumbent is not None:
                break
        time.sleep(0.05)

    assert incumbent is not None, "no incumbent while the job was running"
    assert service.status(job.job_id) is JobStatus.RUNNING, \
        "the job finished before an incumbent could be read mid-solve"
    assert verify(problem, incumbent).ok, "the incumbent is not a legal plan"
    service.cancel(job.job_id)


def test_cancelling_returns_the_incumbent_rather_than_nothing(service, problem):
    """§9.4: "POST /v1/jobs/{job_id}/cancel -> stop, return incumbent". A
    cancellation that discards the work is the failure NFR-03 exists to
    prevent -- the whole point is that partial progress is still useful."""
    registered = service.register(problem)
    job = service.solve(registered.problem_id, iterations=200_000)

    for _ in range(200):
        if service.incumbent(job.job_id) is not None:
            break
        time.sleep(0.05)

    returned = service.cancel(job.job_id)
    assert returned is not None
    assert verify(problem, returned).ok
    assert service.status(job.job_id) is JobStatus.CANCELLED


def test_the_incumbent_improves_rather_than_merely_repeating(service):
    """The property that actually distinguishes an anytime job from a chunked one.

    "Never gets worse" is too weak to test anything: chunks run at a fixed
    seed, so a cold restart is deterministic and returns the identical plan
    forever -- monotone, and static. Perturbation proved it, passing with the
    warm start removed entirely.

    Measured on RC208, six chunks warm give 803, 791, 788, 788, 788, 788 and
    six cold give 803 six times. So the assertion is that the incumbent gets
    *better*, on an instance large enough to have somewhere to go. The
    generated instances are 7-14 orders and are solved outright in the first
    chunk, which is why this one reaches for a real benchmark.
    """
    from pathlib import Path

    from vrp.benchmarks import read_benchmark

    instance = Path("benchmarks/instances/RC208.vrp")
    if not instance.exists():
        pytest.skip("benchmark instances not present")

    problem = read_benchmark(instance).problem
    registered = service.register(problem)
    job = service.solve(registered.problem_id, iterations=200_000)

    costs = []
    for _ in range(200):
        incumbent = service.incumbent(job.job_id)
        if incumbent is not None:
            cost = _cost(problem, incumbent)
            if not costs or cost != costs[-1]:
                costs.append(cost)
        if len(costs) >= 3 or service.status(job.job_id) is not JobStatus.RUNNING:
            break
        time.sleep(0.05)
    service.cancel(job.job_id)

    assert len(costs) >= 2, f"the incumbent never changed: {costs}"
    assert costs == sorted(costs, reverse=True), f"the incumbent got worse: {costs}"


def test_a_finished_job_reports_done_and_keeps_its_solution(service, problem):
    registered = service.register(problem)
    job = service.solve(registered.problem_id, iterations=300)

    solution = service.wait(job.job_id, timeout=60)
    assert service.status(job.job_id) is JobStatus.DONE
    assert verify(problem, solution).ok
    assert service.incumbent(job.job_id) is not None


def test_the_finished_solution_carries_its_replay_record(service, problem):
    """E-17's CON-4 record has to survive the job layer, or a plan returned by
    the API cannot be reproduced even though the solver recorded how."""
    registered = service.register(problem)
    job = service.solve(registered.problem_id, iterations=300, seed=5)
    solution = service.wait(job.job_id, timeout=60)

    assert solution.solver["seed"] == 5
    assert solution.solver["iterations"] >= 300


def _cost(problem, solution) -> int:
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    return sum(problem.matrix.distance(index[a.location_id], index[b.location_id])
               for route in solution.routes
               for a, b in zip(route.steps, route.steps[1:], strict=False))
