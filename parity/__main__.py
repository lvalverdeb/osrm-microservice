"""Differential parity runner: replay a seeded corpus against two gateways.

Both gateways must be pointed at the *same* `osrm-routed`, or the comparison
measures the map data rather than the port.

They must **not** share a warm Redis L2, for two reasons. A response served from
cache never exercises the gateway's upstream URL construction, so a run against
a warm shared cache can pass while the two build entirely different queries. And
because the two implementations store the same JSON with different whitespace --
Python writes `json.dumps` of the decoded body, this port writes the engine's
bytes -- a cross-populated entry makes the reader's response bytes depend on
which gateway happened to populate it. Give each side its own `REDIS_URL`
database, or leave `REDIS_URL` empty on both.

Usage:
    uv run python -m parity --reference http://127.0.0.1:8000 \
        --candidate http://127.0.0.1:8001 --seed 20260822 --cases 10

The first run worth doing is a self-diff -- reference and candidate both set to
the Python gateway. It must come back completely clean; anything else means the
harness is broken, not the port.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from parity.corpus import DEFAULT_ENDPOINTS, ENDPOINTS, build_all
from parity.quality import DEFAULT_VRP_CHUNK_SIZE
from parity.report import (
    print_failures,
    print_table,
    summarise,
    write_artifacts,
    write_json,
)
from parity.runner import PreconditionError, run_urls

# The deployed /route limit is 600/minute (~10/s), so an unpaced run trips the
# limiter and every case comes back 429 -- which looks like total divergence.
DEFAULT_PACE = 0.15

EXIT_PRECONDITION = 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reference", default="http://127.0.0.1:8000",
                        help="base URL of the incumbent gateway")
    parser.add_argument("--candidate", default="http://127.0.0.1:8001",
                        help="base URL of the gateway under test")
    parser.add_argument("--seed", type=int, default=20260822,
                        help="corpus seed; the same seed always builds the same requests")
    parser.add_argument("--cases", type=int, default=10, help="requests per endpoint")
    parser.add_argument("--endpoints", default=",".join(DEFAULT_ENDPOINTS),
                        help=f"comma-separated subset of: {', '.join(ENDPOINTS)}")
    parser.add_argument("--pace", type=float, default=DEFAULT_PACE,
                        help="seconds between cases, to stay under the rate limiter")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--vrp-chunk-size", type=int, default=DEFAULT_VRP_CHUNK_SIZE,
                        help="VRP_CHUNK_SIZE, which bounds a route jointly with capacity; "
                             "no endpoint exposes it, so the harness has to be told")
    parser.add_argument("--report-json", type=Path, default=None,
                        help="write the full machine-readable record here")
    parser.add_argument("--report-dir", type=Path, default=None,
                        help="write failing cases' request bodies here")
    return parser.parse_args(argv)


def main() -> int:
    """Run the corpus against both gateways and report."""
    args = parse_args()
    endpoints = tuple(e.strip() for e in args.endpoints.split(",") if e.strip())
    cases = build_all(args.seed, args.cases, endpoints)

    print(f"parity  seed={args.seed}  cases={args.cases}  endpoints={len(endpoints)}")
    print(f"  reference  {args.reference}")
    print(f"  candidate  {args.candidate}")

    try:
        results = asyncio.run(run_urls(cases, args.reference, args.candidate,
                                       args.vrp_chunk_size, args.pace, args.timeout))
    except PreconditionError as exc:
        # Exit 2, not 1: the environment is wrong, not the port. Collapsing the
        # two is how a harness stops being believed.
        print(f"\n  PRECONDITION  {exc}", file=sys.stderr)
        return EXIT_PRECONDITION

    print_table(results)
    print_failures(results, args.report_dir, args.candidate)
    if args.report_dir:
        write_artifacts(results, args.report_dir)

    exit_code, summary = summarise(results)
    if args.report_json:
        write_json(results, args.report_json,
                   {"seed": args.seed, "cases": args.cases,
                    "reference": args.reference, "candidate": args.candidate})
    print(f"\n  {summary}.  exit {exit_code}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
