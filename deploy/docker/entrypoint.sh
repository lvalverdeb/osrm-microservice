#!/bin/sh
#
# Container entrypoint for the OSRM API Gateway.
#
# The FreeBSD path solves the same problems in deploy/freebsd/osrm-api-gateway;
# this is a port of that script's worker handling, not a second design. Keep the
# two in step -- docs/deployment.md, "Keeping the two in step".
#
# Configuration arrives via the environment; see the defaults below and
# deploy/docker/.env.example.

set -eu

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-1}"
METRICS_DIR="${METRICS_DIR:-/tmp/prometheus-multiproc}"
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-}"

warn() { echo "entrypoint: $*" >&2; }

# --- workers --------------------------------------------------------------
# Extra workers need a shared directory for metrics: prometheus_client counts in
# process memory, so without it each worker reports only its own share of the
# traffic and a scrape shows roughly 1/N. The library picks its value class when
# it is imported, so PROMETHEUS_MULTIPROC_DIR has to be exported before uvicorn
# starts, not set from application config.
_workers_flag=""
case "$WORKERS" in
    ''|*[!0-9]*)
        warn "WORKERS=\"${WORKERS}\" is not a number; using 1"
        WORKERS=1
        ;;
    0|1) ;;
    *)
        _workers_flag="--workers ${WORKERS}"
        # Files left by a previous run would be counted again by the collector.
        # A fresh container starts with an empty filesystem, but `docker restart`
        # does not -- so the wipe is required, not belt-and-braces.
        rm -rf "$METRICS_DIR"
        mkdir -p "$METRICS_DIR"
        PROMETHEUS_MULTIPROC_DIR="$METRICS_DIR"
        export PROMETHEUS_MULTIPROC_DIR
        ;;
esac

# --- trusted proxies ------------------------------------------------------
# uvicorn installs ProxyHeadersMiddleware by default but trusts only 127.0.0.1,
# so X-Forwarded-For from a load balancer is ignored and every client collapses
# into one rate-limit bucket keyed on the balancer's address. Naming the proxies
# here makes the limiter key the real client again.
_proxy_flag=""
if [ -n "$FORWARDED_ALLOW_IPS" ]; then
    if [ "$FORWARDED_ALLOW_IPS" = "*" ]; then
        # With "*" uvicorn trusts every hop and takes X-Forwarded-For[0], which
        # the client controls. That does not weaken rate limiting, it defeats it:
        # any caller can mint a fresh bucket per request by varying the header.
        warn "FORWARDED_ALLOW_IPS=* trusts any client-supplied X-Forwarded-For;"
        warn "rate limiting can be bypassed by spoofing it. Name your proxies instead."
    fi
    _proxy_flag="--forwarded-allow-ips ${FORWARDED_ALLOW_IPS}"
fi

# exec so uvicorn is PID 1 and receives SIGTERM from `docker stop` directly.
# shellcheck disable=SC2086  # both flags are deliberately word-split or empty
exec uvicorn app.main:app --host "$HOST" --port "$PORT" $_workers_flag $_proxy_flag
