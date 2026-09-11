#!/bin/sh
#
# Idempotent installer for the OSRM API Gateway on FreeBSD.
#
# Runs inside the target jail (or on a plain FreeBSD host) and is invoked over
# SSH by the Makefile's jail-* targets. Every phase is safe to re-run.
#
# Usage: install.sh [deps|sync|app|data|services|gateway-services|stop|status|health|logs|all|spike|spike-stop]
#
# Sources are staged into JAIL_STAGE by the Makefile as the login user, then
# placed into JAIL_DIR here with escalated privileges -- the login user has no
# write access to /usr/local/www.
#
# Phases that restart a service go through restart_service(), never a bare
# `service ... restart`: daemon(8) would otherwise start the new child with this
# script's stdout still attached, and over ssh that is the session's pipe, so
# ssh would hang until the daemon exits -- which is never, for a service -- and
# fail with its own 255 long after the work had finished. See restart_service.
#
# Configuration arrives via the environment; see the defaults below.

set -eu

STAGE="${JAIL_STAGE:-/tmp/osrm-api-gateway-stage}"
APP_DIR="${JAIL_DIR:-/usr/local/www/osrm-api-gateway}"
APP_USER="${JAIL_APP_USER:-osrmapi}"
API_HOST="${JAIL_API_HOST:-0.0.0.0}"
API_PORT="${JAIL_API_PORT:-8000}"
API_WORKERS="${JAIL_API_WORKERS:-1}"
FORWARDED_ALLOW_IPS="${JAIL_FORWARDED_ALLOW_IPS:-}"
DATA_DIR="${JAIL_DATA_DIR:-/var/db/osrm-backend}"
PROFILE="${PROFILE:-car}"
OSM_FILE="${OSM_FILE:-costa-rica-latest.osm.pbf}"
OSM_BASE="${OSM_BASE:-costa-rica-latest}"
GEO_URL="${GEO_URL:-}"
REDIS_URL="${JAIL_REDIS_URL:-redis://127.0.0.1:6379/0}"
OSRM_URL="${JAIL_OSRM_URL:-http://127.0.0.1:5000}"
# Filled in after PROFILES is known; see the loop below its definition.
OSRM_PROFILE_URLS=""
SPIKE_DIR="${JAIL_SPIKE_DIR:-/usr/local/www/osrm-gateway-spike}"
SPIKE_HOST="${JAIL_SPIKE_HOST:-0.0.0.0}"
SPIKE_PORT="${JAIL_SPIKE_PORT:-8001}"

# Shared app settings, staged from deploy/env/ alongside deploy/freebsd/.
SHARED_ENV="${STAGE}/deploy/env/app.env"

# MTX-1: one built graph and one engine per routing profile. Each entry is
# "<osrm profile>:<rc.d suffix>:<port>". OSRM's profile names are car, bicycle
# and foot; the gateway's vocabulary is driving, cycling and walking, and the
# OSRM_URL_* settings written into .env below are where the two meet.
#
# Ports are jail-local and consecutive from the engine's usual 5000. The suffix
# is empty for car so the existing `osrm_routed` service, its rc.conf keys and
# anything already watching them keep their names.
PROFILES="${JAIL_PROFILES:-car::5000 bicycle:_cycling:5001 foot:_walking:5002}"

# The gateway's name for each OSRM profile. MTX-1's two vocabularies meet here
# and nowhere else; getting it wrong points a profile at the wrong graph, which
# is the failure the whole change exists to remove.
gateway_profile() {
    case "$1" in
        car) echo driving ;;
        bicycle) echo cycling ;;
        foot) echo walking ;;
        *) echo "" ;;
    esac
}

profile_lua() { echo "/usr/local/share/osrm/profiles/$1.lua"; }
profile_data() { echo "${DATA_DIR}/$1/${OSM_BASE}.osrm"; }

PROFILE_LUA="/usr/local/share/osrm/profiles/${PROFILE}.lua"
# OSRM_DATA is a base path, not a file: osrm-extract/partition/customize strip
# the .osrm suffix and read and write ${OSRM_DATA}.<suffix> siblings. Nothing in
# OSRM 6 ever creates the bare .osrm file. OSRM_BUILT is the last artifact
# osrm-customize writes, so it is what "the data is built" actually means.
OSRM_DATA="${DATA_DIR}/${PROFILE}/${OSM_BASE}.osrm"
OSRM_BUILT="${OSRM_DATA}.cell_metrics"

# PROFILES is positional and hand-edited, so it is checked before any phase does
# work. Every rejection below is something that would otherwise half-succeed: a
# duplicate port leaves the second engine dead while the first looks fine, a
# duplicate suffix installs two profiles over one rc.d name, and a profile the
# gateway has no word for builds a graph nothing will ever route on.
validate_profiles() {
    _ports=""
    _suffixes=""
    for _entry in $PROFILES; do
        case "$_entry" in
            *:*:*:*) die "PROFILES entry '${_entry}' has too many fields; \
expected <osrm-profile>:<rc.d-suffix>:<port>" ;;
            *:*:*) : ;;
            *) die "PROFILES entry '${_entry}' is not \
<osrm-profile>:<rc.d-suffix>:<port>" ;;
        esac

        _p="${_entry%%:*}"
        _rest="${_entry#*:}"
        _sfx="${_rest%%:*}"
        _prt="${_rest#*:}"

        case "$_p" in
            "") die "PROFILES entry '${_entry}' names no OSRM profile" ;;
            *[!a-z]*) die "PROFILES profile '${_p}' is not a lowercase OSRM \
profile name such as car, bicycle or foot" ;;
        esac
        [ -n "$(gateway_profile "$_p")" ] || die "PROFILES profile '${_p}' has \
no gateway name; add it to gateway_profile() or the graph is built and never routed on"

        case "$_prt" in
            "" | *[!0-9]*) die "PROFILES port '${_prt}' for ${_p} is not a number" ;;
        esac
        [ "$_prt" -ge 1024 ] && [ "$_prt" -le 65535 ] || \
            die "PROFILES port ${_prt} for ${_p} is outside 1024-65535"

        case " ${_ports} " in
            *" ${_prt} "*) die "PROFILES gives port ${_prt} to more than one \
profile; the second engine would fail to bind while the first looked healthy" ;;
        esac
        # The car suffix is empty on purpose, so an empty string has to be a
        # value the seen-list can hold rather than one that matches any gap in
        # it: comparing "" against a space-padded list matches on the first
        # entry and rejects the shipped default.
        _key="${_sfx:-(none)}"
        case " ${_suffixes} " in
            *" ${_key} "*) die "PROFILES gives rc.d suffix '${_sfx}' to more \
than one profile; they would install over one another as \
osrm_routed${_sfx}" ;;
        esac
        _ports="${_ports} ${_prt}"
        _suffixes="${_suffixes} ${_key}"
    done
}

validate_profiles

# One OSRM_URL_<PROFILE> per engine, for the gateway's .env. A profile with no
# line here is refused by name rather than answered from the driving graph.
for _entry in $PROFILES; do
    _p="${_entry%%:*}"
    _name="$(gateway_profile "$_p")"
    [ -n "$_name" ] || continue
    _upper=$(echo "$_name" | tr '[:lower:]' '[:upper:]')
    OSRM_PROFILE_URLS="${OSRM_PROFILE_URLS}OSRM_URL_${_upper}=http://127.0.0.1:${_entry##*:}
"
done

log() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

# --- privilege escalation -------------------------------------------------
# Neither doas nor sudo is guaranteed to exist, so resolve at runtime and fail
# with a remediation message rather than half-installing.
if [ "$(id -u)" = "0" ]; then
    SUDO=""
elif command -v doas >/dev/null 2>&1; then
    SUDO="doas"
elif command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
else
    echo "ERROR: this script needs root, but neither doas nor sudo is available." >&2
    echo "       Fix with one of (as root in the jail):" >&2
    echo "         pkg install -y doas && echo 'permit nopass :wheel' > /usr/local/etc/doas.conf" >&2
    echo "         pkg install -y sudo && visudo" >&2
    echo "       Or deploy with JAIL_USER=root after installing an SSH key for root." >&2
    exit 1
fi

run() { if [ -n "$SUDO" ]; then $SUDO "$@"; else "$@"; fi; }

# Restart a service without handing the daemon our stdout and stderr.
#
# rc.d starts the new process through daemon(8), which lets it inherit whatever
# fds this script holds. Over ssh those are the session's pipes, so sshd never
# sees EOF and ssh does not return until the daemon exits -- i.e. never, and the
# caller eventually gets ssh's 255 long after the work finished. Capturing to a
# file gives the daemon that file to hold instead; the output is replayed here
# so the operator still sees what service(8) said.
restart_service() {
    _out=$(mktemp -t osrm-restart) || die "mktemp failed"
    run service "$1" restart >"$_out" 2>&1 || {
        cat "$_out" >&2
        rm -f "$_out"
        die "service $1 restart failed"
    }
    cat "$_out"
    rm -f "$_out"
}

# --- phases ---------------------------------------------------------------

install_pkgs() {
    missing=""
    for p in "$@"; do
        if ! pkg info -e "$p" >/dev/null 2>&1; then
            missing="${missing} ${p}"
        fi
    done
    if [ -n "$missing" ]; then
        log "installing packages:${missing}"
        # shellcheck disable=SC2086
        run pkg install -y ${missing}
    else
        log "packages already present: $*"
    fi
}

phase_deps() {
    # osrm-backend brings the engine, its Lua profiles and its own rc.d script.
    # rust builds the gateway itself.
    #
    # The Python toolchain that used to be here -- python313, uv, pkgconf,
    # openblas, ninja -- is gone with the FastAPI gateway, and with it the slow
    # part of a bootstrap: numpy and pydantic-core were compiled from source
    # every time because PyPI publishes no FreeBSD wheels. The rollback path
    # still needs them, so `install.sh python-deps` installs them on demand.
    install_pkgs osrm-backend redis rust

    if ! pw usershow "$APP_USER" >/dev/null 2>&1; then
        log "creating service user ${APP_USER}"
        run pw useradd -n "$APP_USER" -c "OSRM API Gateway" \
            -d /nonexistent -s /usr/sbin/nologin
    fi

    run mkdir -p "$APP_DIR" "$DATA_DIR"
}

phase_sync() {
    [ -d "${STAGE}/gateway/src" ] || die "no staged gateway sources at ${STAGE}; run 'make jail-stage' first."
    log "placing sources into ${APP_DIR}"
    run mkdir -p "$APP_DIR"
    # Replace outright rather than merge, so deleted modules do not linger.
    run rm -rf "${APP_DIR}/deploy"
    run cp -R "${STAGE}/deploy" "${APP_DIR}/deploy"
    if [ -f "${STAGE}/README.md" ]; then
        run cp "${STAGE}/README.md" "${APP_DIR}/README.md"
    fi
}

phase_app() {
    [ -d "${STAGE}/gateway/src" ] || \
        die "no staged gateway sources at ${STAGE}; run 'make jail-stage' first."
    # cargo arrives with the deps phase.
    command -v cargo >/dev/null 2>&1 || \
        die "cargo missing; run the deps phase first ('make jail-bootstrap')."

    # The `jail` profile uses thin LTO across 16 codegen units. The default
    # release profile is fat LTO in a single unit, which is the most
    # memory-hungry way to build and can meet the OOM killer on a 2 GB box
    # shared with two other jails. Fetching crates needs working DNS in the
    # jail -- that is what 'make jail-host' arranges.
    log "building the gateway (cargo, jail profile; first build fetches crates and is slow)"
    run sh -c "cd '${STAGE}/gateway' && cargo build --profile jail --locked"

    _built="${STAGE}/gateway/target/jail/osrm-api-gateway"
    [ -x "${_built}" ] || die "build produced no binary at ${_built}"

    log "installing the gateway into ${APP_DIR}"
    run mkdir -p "$APP_DIR"
    run install -m 755 "${_built}" "${APP_DIR}/osrm-api-gateway"

    log "writing ${APP_DIR}/.env"
    # Two tiers in one file. The shared block is deploy/env/app.env verbatim --
    # the same file the Docker path loads via env_file -- so app settings are
    # maintained in one place for both deployments. The overlay appended after it
    # carries the values only this deployment can know; the binary's .env parser
    # takes the last occurrence of a duplicated key, matching pydantic-settings,
    # so the overlay wins.
    #
    # OSRM_API_URL stays at the shared value on purpose: it is a client-side
    # setting (examples/ reads it to find the gateway), not a server one. The bind
    # address is not written either -- it is not a URL a client could use, and the
    # rc.d script passes it through ${name}_env from osrm_api_gateway_host.
    #
    # FORWARDED_ALLOW_IPS lives here rather than in a sysrc knob: rc.subr expands
    # ${name}_env unquoted, and the value `*` would glob against the working
    # directory. Change it by editing this file and restarting the service.
    [ -f "${SHARED_ENV}" ] || \
        die "missing ${SHARED_ENV}; run 'make jail-stage' from a checkout that has deploy/env/app.env"
    run sh -c "cat '${SHARED_ENV}' > ${APP_DIR}/.env"
    run sh -c "cat >> ${APP_DIR}/.env <<ENVEOF

# --- generated by deploy/freebsd/install.sh; overrides the shared block above ---
OSRM_BASE_URL=${OSRM_URL}
${OSRM_PROFILE_URLS}
REDIS_URL=${REDIS_URL}
FORWARDED_ALLOW_IPS=${FORWARDED_ALLOW_IPS}
ENVEOF"

    # Root owns the code, the service user only reads it: the gateway cannot
    # rewrite its own binary or configuration at runtime.
    run chown -R "root:${APP_USER}" "$APP_DIR"
    run chmod 750 "$APP_DIR"
    run chmod 640 "${APP_DIR}/.env"
}

phase_data() {
    _missing=""
    for _entry in $PROFILES; do
        _p="${_entry%%:*}"
        [ -f "$(profile_data "$_p").cell_metrics" ] || _missing="${_missing} ${_p}"
    done
    if [ -z "$_missing" ]; then
        log "OSRM data already built for every profile (${PROFILES}); skipping"
        return 0
    fi
    for _p in $_missing; do
        _lua="$(profile_lua "$_p")"
        [ -f "$_lua" ] || die "profile ${_lua} missing; is osrm-backend installed?"
    done

    if [ ! -f "${DATA_DIR}/${OSM_FILE}" ]; then
        if [ -f "${STAGE}/data/${OSM_FILE}" ]; then
            log "using staged ${OSM_FILE} uploaded from the workstation"
            run cp "${STAGE}/data/${OSM_FILE}" "${DATA_DIR}/${OSM_FILE}"
        elif [ -n "$GEO_URL" ]; then
            log "fetching ${OSM_FILE}"
            run fetch -o "${DATA_DIR}/${OSM_FILE}" "$GEO_URL"
        else
            die "${DATA_DIR}/${OSM_FILE} missing, nothing staged, and GEO_URL unset."
        fi
    fi

    # Mirrors Dockerfile.builder so both deployment paths produce identical data.
    # One profile at a time and only the ones missing, so an extract killed for
    # memory -- this box has 2 GB and the docs warn about it -- costs that
    # profile rather than all three, and re-running resumes where it stopped.
    for _p in $_missing; do
        _data="$(profile_data "$_p")"
        log "osrm-extract ${_p} (memory-hungry; see docs/deployment_freebsd.md if killed)"
        run sh -c "cd ${DATA_DIR} && osrm-extract -p $(profile_lua "$_p") ${DATA_DIR}/${OSM_FILE}"
        run mkdir -p "${DATA_DIR}/${_p}"
        run sh -c "mv ${DATA_DIR}/${OSM_BASE}.osrm* ${DATA_DIR}/${_p}/"
        log "osrm-partition ${_p}"
        run osrm-partition "$_data"
        log "osrm-customize ${_p}"
        run osrm-customize "$_data"
        run chown -R osrm:osrm "${DATA_DIR}/${_p}"
    done
}

phase_services() {
    for _rc in osrm-api-gateway osrm-routed redis-cache.conf; do
        [ -f "${APP_DIR}/deploy/freebsd/${_rc}" ] || \
            die "rc.d script ${_rc} missing; run the sync phase first."
    done
    for _entry in $PROFILES; do
        _p="${_entry%%:*}"
        [ -f "$(profile_data "$_p").cell_metrics" ] || \
            die "no routing data for ${_p}; run the data phase first ('make jail-data')."
    done
    log "installing rc.d scripts and configuring services"
    # One script, installed once per profile. It reads its own filename, so the
    # cycling instance is configured through osrm_routed_cycling_* and stops and
    # starts independently of the others.
    for _entry in $PROFILES; do
        _suffix="${_entry#*:}"; _suffix="${_suffix%%:*}"
        run install -m 555 "${APP_DIR}/deploy/freebsd/osrm-routed" \
            "/usr/local/etc/rc.d/osrm_routed${_suffix}"
    done
    run install -m 644 "${APP_DIR}/deploy/freebsd/redis-cache.conf" \
        /usr/local/etc/redis-osrm-cache.conf

    # www/osrm-backend's own `osrm` service cannot pass flags to osrm-routed
    # -- see the header of deploy/freebsd/osrm-routed -- so it is disabled and
    # replaced. Its settings are removed rather than left to rot in rc.conf.
    run service osrm onestop >/dev/null 2>&1 || true
    run sysrc osrm_enable=NO >/dev/null
    run sysrc -x osrm_file osrm_flags >/dev/null 2>&1 || true

    # Engine and gateway run from the rc.d scripts in deploy/freebsd; redis
    # uses its own port's script unchanged. Mirrors docker-compose.yml.
    for _entry in $PROFILES; do
        _p="${_entry%%:*}"
        _suffix="${_entry#*:}"; _suffix="${_suffix%%:*}"
        _port="${_entry##*:}"
        run sysrc "osrm_routed${_suffix}_enable=YES" >/dev/null
        run sysrc "osrm_routed${_suffix}_base=$(profile_data "$_p")" >/dev/null
        run sysrc "osrm_routed${_suffix}_port=${_port}" >/dev/null
    done
    run sysrc redis_enable=YES >/dev/null
    run sysrc redis_config=/usr/local/etc/redis-osrm-cache.conf >/dev/null

    _engines=""
    for _entry in $PROFILES; do
        _suffix="${_entry#*:}"; _suffix="${_suffix%%:*}"
        _engines="${_engines} osrm_routed${_suffix}"
    done
    for svc in redis $_engines; do
        log "restarting ${svc}"
        restart_service "$svc"
    done

    phase_gateway_services
}

# The gateway alone: no engine, no redis, no data check.
#
# Split out of phase_services because returning from a rollback only needs the
# gateway swapped, and going through the full phase restarts osrm_routed --
# which reloads the map data and turns a seconds-long swap into minutes, during
# an incident, for no reason. `install.sh python-services` is the mirror of this
# one and has always been gateway-only.
phase_gateway_services() {
    [ -f "${APP_DIR}/deploy/freebsd/osrm-api-gateway" ] || \
        die "rc.d script osrm-api-gateway missing; run the sync phase first."
    [ -x "${APP_DIR}/osrm-api-gateway" ] || \
        die "no gateway binary at ${APP_DIR}; run the app phase first ('make jail-up')."

    log "installing the gateway rc.d script"
    # Installed with underscores: service(8) resolves by filename, so
    # /usr/local/etc/rc.d/osrm-api-gateway would be invisible to
    # `service osrm_api_gateway`.
    run install -m 555 "${APP_DIR}/deploy/freebsd/osrm-api-gateway" \
        /usr/local/etc/rc.d/osrm_api_gateway

    run sysrc osrm_api_gateway_enable=YES >/dev/null
    run sysrc osrm_api_gateway_dir="$APP_DIR" >/dev/null
    run sysrc osrm_api_gateway_user="$APP_USER" >/dev/null
    run sysrc osrm_api_gateway_host="$API_HOST" >/dev/null
    run sysrc osrm_api_gateway_port="$API_PORT" >/dev/null
    run sysrc osrm_api_gateway_workers="$API_WORKERS" >/dev/null
    # Neither knob belongs to this implementation: the binary reads
    # FORWARDED_ALLOW_IPS from .env (a `*` would glob through ${name}_env), and
    # workers are threads in one process, so there is no multiprocess metrics
    # directory. Cleared rather than left behind, because `python-services`
    # sets forwarded_allow_ips and a rollback cycle would otherwise leave it
    # stale in rc.conf.
    run sysrc -x osrm_api_gateway_metrics_dir >/dev/null 2>&1 || true
    run sysrc -x osrm_api_gateway_forwarded_allow_ips >/dev/null 2>&1 || true

    log "restarting osrm_api_gateway"
    restart_service osrm_api_gateway
}

phase_health() {
    # /ready covers both processes: it answers 503 while the engine is
    # unreachable, so a single probe replaces the old gateway + OSRM pair.
    i=0
    until fetch -qo /dev/null "http://127.0.0.1:${API_PORT}/ready"; do
        i=$((i + 1))
        if [ "$i" -ge 30 ]; then
            die "readiness check failed after 30 attempts (gateway or OSRM engine down)"
        fi
        sleep 1
    done
    log "health checks passed"
    fetch -qo - "http://127.0.0.1:${API_PORT}/health"
    echo
}

phase_logs() {
    tail -n 100 /var/log/osrm-api-gateway.log 2>/dev/null || echo "(no gateway log yet)"
    echo "--- /var/log/messages ---"
    tail -n 50 /var/log/messages 2>/dev/null || echo "(unreadable)"
}

# --- evaluation spike -------------------------------------------------------
# Optional and self-contained: nothing in the phases above depends on it, and
# `all` does not run it. It exists so the Python-vs-Rust comparison in
# rust-spike/README.md can be re-run on this hardware rather than a laptop.

phase_spike() {
    [ -d "${STAGE}/rust-spike/src" ] || \
        die "no staged rust-spike at ${STAGE}; run 'make jail-stage' from a checkout that has it."
    # cargo arrives with the deps phase -- it is already required there, because
    # pydantic-core is a Rust extension with no FreeBSD wheel.
    command -v cargo >/dev/null 2>&1 || \
        die "cargo missing; run the deps phase first ('make jail-bootstrap')."

    # The `jail` profile uses thin LTO across 16 codegen units. The default
    # release profile is fat LTO in a single unit, which is the most
    # memory-hungry way to build and can meet the OOM killer on a 2 GB box
    # shared with two other jails. Fetching crates needs working DNS in the
    # jail -- that is what 'make jail-host' arranges.
    log "building the spike (cargo, jail profile; first build fetches crates and is slow)"
    run sh -c "cd '${STAGE}/rust-spike' && cargo build --profile jail --locked"

    _built="${STAGE}/rust-spike/target/jail/osrm-gateway-spike"
    [ -x "${_built}" ] || die "build produced no binary at ${_built}"

    log "installing the spike into ${SPIKE_DIR}"
    run mkdir -p "$SPIKE_DIR"
    run install -m 755 "${_built}" "${SPIKE_DIR}/osrm-gateway-spike"

    # Same two tiers as phase_app: the shared block verbatim, then an overlay
    # this deployment can know. Later keys win, which the binary's own .env
    # parser implements to match pydantic-settings.
    [ -f "${SHARED_ENV}" ] || \
        die "missing ${SHARED_ENV}; run 'make jail-stage' from a checkout that has deploy/env/app.env"
    run sh -c "cat '${SHARED_ENV}' > ${SPIKE_DIR}/.env"
    run sh -c "cat >> ${SPIKE_DIR}/.env <<ENVEOF

# --- generated by deploy/freebsd/install.sh; overrides the shared block above ---
OSRM_BASE_URL=${OSRM_URL}
ENVEOF"

    # Installed name uses underscores like the other two: service(8) resolves
    # by filename, so /usr/local/etc/rc.d/osrm-gateway-spike would be invisible
    # to `service osrm_gateway_spike`.
    run install -m 555 "${STAGE}/deploy/freebsd/osrm-gateway-spike" \
        /usr/local/etc/rc.d/osrm_gateway_spike

    run chown -R "root:${APP_USER}" "$SPIKE_DIR"
    run chmod 750 "$SPIKE_DIR"
    run chmod 640 "${SPIKE_DIR}/.env"

    run sysrc osrm_gateway_spike_enable=YES >/dev/null
    run sysrc osrm_gateway_spike_dir="${SPIKE_DIR}" >/dev/null
    run sysrc osrm_gateway_spike_user="${APP_USER}" >/dev/null
    run sysrc osrm_gateway_spike_host="${SPIKE_HOST}" >/dev/null
    run sysrc osrm_gateway_spike_port="${SPIKE_PORT}" >/dev/null
    # Deliberately mirrors the Python gateway's worker count: comparing a
    # 1-worker uvicorn against a 4-thread tokio measures the configuration.
    run sysrc osrm_gateway_spike_workers="${API_WORKERS}" >/dev/null

    log "restarting osrm_gateway_spike"
    restart_service osrm_gateway_spike

    _i=0
    until fetch -qo /dev/null "http://127.0.0.1:${SPIKE_PORT}/ready" 2>/dev/null; do
        _i=$((_i + 1))
        [ "$_i" -ge 20 ] && die "spike did not become ready on port ${SPIKE_PORT}; see /var/log/osrm-gateway-spike.log"
        sleep 1
    done
    log "spike ready on ${SPIKE_HOST}:${SPIKE_PORT} (gateway is on ${API_PORT})"
}

phase_spike_stop() {
    log "stopping osrm_gateway_spike"
    run service osrm_gateway_spike stop || true
    run sysrc osrm_gateway_spike_enable=NO >/dev/null || true
}

phase_stop() {
    for svc in osrm_api_gateway osrm_routed redis; do
        log "stopping ${svc}"
        run service "$svc" stop || true
    done
}

phase_status() {
    for svc in osrm_api_gateway osrm_routed redis; do
        run service "$svc" status || true
    done
}

case "${1:-all}" in
    deps)     phase_deps ;;
    sync)     phase_sync ;;
    app)      phase_app ;;
    gateway-services) phase_gateway_services ;;
    data)     phase_data ;;
    services) phase_services ;;
    stop)     phase_stop ;;
    status)   phase_status ;;
    health)   phase_health ;;
    logs)     phase_logs ;;
    all)      phase_deps; phase_sync; phase_app; phase_data; phase_services ;;
    spike)      phase_spike ;;
    spike-stop) phase_spike_stop ;;
    *)        die "usage: $0 [deps|sync|app|data|services|gateway-services|stop|status|health|logs|all|spike|spike-stop]" ;;
esac

log "done: ${1:-all}"
