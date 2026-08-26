"""Solve jobs: registration, idempotency, and the anytime incumbent — §9.4, T-15.

§9.4 makes solving asynchronous, and two consequences follow that this module
exists to handle.

**A client that does not hear back will retry.** It cannot distinguish a lost
response from a lost request, so without an idempotency key the retry starts a
second solve of the same problem: the budget is spent twice and, worse, the
second plan may differ from the one already sent to drivers. A key makes the
retry join the original job.

**A long solve must be useful before it ends** (NFR-03): "The solver MUST hold a
best-known feasible incumbent from the first construction onward and return it
on any timeout or cancellation."

The incumbent here is real rather than staged. PyVRP's `Model.solve` accepts an
`initial_solution`, so a job runs the search in chunks and warm-starts each
chunk from the previous best, publishing after every one.

The warm start is what makes the incumbent *improve*, not what keeps it from
getting worse -- a distinction worth stating because the first version of this
docstring had it wrong. Chunks run at a fixed seed, so a cold restart is
deterministic and simply returns the same plan every time: measured on RC208,
six cold chunks give 803 six times, while six warm ones give 803, 791, 788,
788, 788, 788. Both are monotone. Only one is anytime in any useful sense.

**Placement, and what is deliberately not here.** This is the job layer, in
Python, next to the solver it drives. §9.4's HTTP surface is *not* implemented,
because how the Rust gateway reaches a Python solver is an open architectural
question the SDD does not answer -- subprocess, sidecar service, or embedding --
and each has different failure, deployment and back-pressure characteristics.
Inventing one here would bury that decision in an example. What this module
gives that decision is a transport-agnostic core: register, solve, poll, read
the incumbent, cancel.

In-memory only. §10.1 puts problems, jobs and solutions in a relational store
with a regulatory retention period; a process-local dict is not that, and
pretending otherwise would be worse than saying so.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum

from vrp.diagnose import Finding, preflight
from vrp.model import Problem, Solution

# How much search happens between two publications of the incumbent. Small
# enough that a caller polling a long solve sees progress; large enough that the
# per-chunk compile overhead stays a rounding error. Measured at ~17 ms to
# compile a 200-stop model against ~640 ms of search, so 250 iterations keeps
# that ratio comfortable.
CHUNK_ITERATIONS = 250


class JobStatus(Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RegisteredProblem:
    """§9.4's POST /v1/problems response: an id and what pre-flight found."""

    problem_id: str
    diagnostics: dict[str, Finding]


@dataclass(frozen=True)
class JobHandle:
    """§9.4's 202: enough to poll with, and nothing else."""

    job_id: str
    problem_id: str


@dataclass
class _Job:
    job_id: str
    problem: Problem
    iterations: int
    seed: int
    status: JobStatus = JobStatus.QUEUED
    incumbent: Solution | None = None
    error: str | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    future: Future | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class SolveService:
    """Registration, idempotent job submission, and anytime reads.

    Thread-safe by a single coarse lock over the registries. The solves
    themselves run on a pool and touch only their own `_Job`, so the lock is
    never held across one.
    """

    def __init__(self, workers: int = 4) -> None:
        self._problems: dict[str, Problem] = {}
        self._jobs: dict[str, _Job] = {}
        # (problem_id, key) rather than key alone: the same key against a
        # different problem is a different request, and conflating them would
        # return one customer's plan for another's day.
        self._by_key: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="vrp-solve")

    # -- registration ------------------------------------------------------

    def register(self, problem: Problem) -> RegisteredProblem:
        """Validate, diagnose, and store. §9.4's create endpoint.

        Pre-flight runs here rather than at solve time so a caller learns that
        an order is unservable before committing a budget to discovering it.
        """
        problem_id = f"prb_{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._problems[problem_id] = problem
        return RegisteredProblem(problem_id=problem_id,
                                 diagnostics=preflight(problem))

    def problem(self, problem_id: str) -> Problem:
        with self._lock:
            return self._problems[problem_id]

    # -- submission --------------------------------------------------------

    def solve(self, problem_id: str, iterations: int = 2_000, seed: int = 0,
              idempotency_key: str | None = None) -> JobHandle:
        """Start a solve, or return the job an identical request already started.

        Args:
            problem_id: from `register`.
            iterations: deterministic budget (CON-4 -- not wall-clock).
            seed: the run's seed, recorded on the solution for replay.
            idempotency_key: supplied by the caller. Absent means no
                deduplication: two keyless requests are two solves, because
                guessing they are "the same" would collapse a legitimately
                repeated one.

        Returns:
            A handle to poll. Identical to the earlier handle when the key
            matches one already seen for this problem.
        """
        with self._lock:
            if problem_id not in self._problems:
                raise KeyError(f"unknown problem {problem_id!r}")
            if idempotency_key is not None:
                existing = self._by_key.get((problem_id, idempotency_key))
                if existing is not None:
                    return JobHandle(job_id=existing, problem_id=problem_id)

            job = _Job(job_id=f"job_{uuid.uuid4().hex[:12]}",
                       problem=self._problems[problem_id],
                       iterations=iterations, seed=seed)
            self._jobs[job.job_id] = job
            if idempotency_key is not None:
                self._by_key[(problem_id, idempotency_key)] = job.job_id

        job.future = self._pool.submit(self._run, job)
        return JobHandle(job_id=job.job_id, problem_id=problem_id)

    # -- polling -----------------------------------------------------------

    def status(self, job_id: str) -> JobStatus:
        return self._job(job_id).status

    def incumbent(self, job_id: str) -> Solution | None:
        """The best plan so far, or None before the first chunk completes."""
        job = self._job(job_id)
        with job.lock:
            return job.incumbent

    def cancel(self, job_id: str) -> Solution | None:
        """Stop, and hand back whatever had been found. §9.4, NFR-03."""
        job = self._job(job_id)
        job.cancelled.set()
        if job.future is not None:
            job.future.result(timeout=60)
        with job.lock:
            if job.status is not JobStatus.DONE:
                job.status = JobStatus.CANCELLED
            return job.incumbent

    def wait(self, job_id: str, timeout: float = 300) -> Solution | None:
        job = self._job(job_id)
        if job.future is not None:
            job.future.result(timeout=timeout)
        with job.lock:
            return job.incumbent

    def job_count(self) -> int:
        with self._lock:
            return len(self._jobs)

    def _job(self, job_id: str) -> _Job:
        with self._lock:
            return self._jobs[job_id]

    # -- the search --------------------------------------------------------

    def _run(self, job: _Job) -> None:
        """Solve in warm-started chunks, publishing after each.

        The warm start is what makes this anytime rather than merely chunked:
        each chunk resumes from the previous best, so the search continues
        instead of starting over. At a fixed seed a cold chunk is deterministic
        and returns the identical plan every time -- monotone, and static.
        """
        from pyvrp.stop import MaxIterations

        from vrp.solve.pyvrp_adapter import (
            _pyvrp_version,
            compile_problem,
            map_solution,
        )

        job.status = JobStatus.RUNNING
        try:
            compiled = compile_problem(job.problem)
            best = None
            done = 0
            while done < job.iterations and not job.cancelled.is_set():
                step = min(CHUNK_ITERATIONS, job.iterations - done)
                result = compiled.model.solve(
                    stop=MaxIterations(step), seed=job.seed, display=False,
                    initial_solution=best)
                best = result.best
                done += step

                solution = map_solution(
                    job.problem, compiled, best,
                    feasible=result.is_feasible(),
                    solver={"solver": f"pyvrp:{_pyvrp_version()}",
                            "seed": job.seed, "iterations": done,
                            "matrix_version": job.problem.matrix.version})
                with job.lock:
                    job.incumbent = solution

            with job.lock:
                if not job.cancelled.is_set():
                    job.status = JobStatus.DONE
        except Exception as failure:
            with job.lock:
                job.status = JobStatus.FAILED
                job.error = f"{type(failure).__name__}: {failure}"
