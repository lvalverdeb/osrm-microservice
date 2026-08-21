#!/bin/sh
#
# Host-side bridge between the Makefile and a jail that has no sshd.
#
# Runs on the FreeBSD *host*, not in the jail. It stages sources into the jail's
# filesystem and then runs install.sh inside via jexec(8). jexec needs real root,
# so the host login must be root or have doas/sudo -- `jailctl.sh doctor` reports
# which, without requiring any of them.
#
# Usage: jailctl.sh <phase>       (install.sh's phases, plus doctor/host/publish/unpublish)

set -eu

JAIL_NAME="${JAIL_NAME:-api}"
HOST_STAGE="${HOST_STAGE:-/tmp/osrm-api-gateway-stage}"
JAIL_STAGE="${JAIL_STAGE:-/tmp/osrm-api-gateway-stage}"
PHASE="${1:-all}"

log() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

# --- privilege escalation -------------------------------------------------
if [ "$(id -u)" = "0" ]; then
    ESC=""; ESC_KIND="root"
elif command -v doas >/dev/null 2>&1; then
    ESC="doas"; ESC_KIND="doas"
elif command -v sudo >/dev/null 2>&1; then
    ESC="sudo"; ESC_KIND="sudo"
else
    ESC=""; ESC_KIND="none"
fi

run() { if [ -n "$ESC" ]; then $ESC "$@"; else "$@"; fi; }

require_esc() {
    if [ "$ESC_KIND" = "none" ]; then
        echo "ERROR: jexec(8) requires root on $(hostname), but this login has" >&2
        echo "       neither doas nor sudo. Fix with one of, as root on the host:" >&2
        echo "         pkg install -y doas" >&2
        echo "         echo 'permit nopass :wheel' > /usr/local/etc/doas.conf" >&2
        echo "       or install an SSH key for root and deploy with JAIL_HOST=root@<host>." >&2
        exit 1
    fi
}

jail_path() {
    # Ask the running jail rather than assuming /jails/<name>.
    jls -j "$JAIL_NAME" path 2>/dev/null || die "jail '${JAIL_NAME}' is not running"
}

# Every install.sh knob the Makefile may have set, forwarded across the jail
# boundary. jexec does not inherit the caller's environment.
jail_env() {
    echo "JAIL_STAGE=${JAIL_STAGE}" \
         "JAIL_DIR=${JAIL_DIR:-/usr/local/www/osrm-api-gateway}" \
         "JAIL_DATA_DIR=${JAIL_DATA_DIR:-/var/db/osrm-backend}" \
         "JAIL_APP_USER=${JAIL_APP_USER:-osrmapi}" \
         "JAIL_API_HOST=${JAIL_API_HOST:-0.0.0.0}" \
         "JAIL_API_PORT=${JAIL_API_PORT:-8000}" \
         "JAIL_API_WORKERS=${JAIL_API_WORKERS:-1}" \
         "JAIL_FORWARDED_ALLOW_IPS=${JAIL_FORWARDED_ALLOW_IPS:-}" \
         "JAIL_SPIKE_DIR=${JAIL_SPIKE_DIR:-/usr/local/www/osrm-gateway-spike}" \
         "JAIL_SPIKE_HOST=${JAIL_SPIKE_HOST:-0.0.0.0}" \
         "JAIL_SPIKE_PORT=${JAIL_SPIKE_PORT:-8001}" \
         "JAIL_REDIS_URL=${JAIL_REDIS_URL:-redis://127.0.0.1:6379/0}" \
         "JAIL_OSRM_URL=${JAIL_OSRM_URL:-http://127.0.0.1:5000}" \
         "PROFILE=${PROFILE:-car}" \
         "OSM_FILE=${OSM_FILE:-costa-rica-latest.osm.pbf}" \
         "OSM_BASE=${OSM_BASE:-costa-rica-latest}" \
         "GEO_URL=${GEO_URL:-}"
}

# --- host prerequisites ---------------------------------------------------
# Tweaks that live on the host rather than in the jail, so install.sh cannot
# reach them. Both are idempotent: re-running is a no-op, which is what makes
# the whole deployment reproducible from a bare host.

JAIL_CONF="${JAIL_CONF:-/etc/jail.conf}"
LOOPBACK_IF="${JAIL_LOOPBACK_IF:-lo1}"
HOST_TAG="osrm-api-gateway"
SYSCTL_CONF="${JAIL_SYSCTL_CONF:-/etc/sysctl.conf}"

# Kernel tunables this deployment depends on, as "name=value|why".
#
# The network stack is the host's -- a jail without VNET cannot set net.inet.*
# itself -- so these are host state that install.sh cannot reach, exactly like
# the loopback and resolver fixes below.
#
# delayed_ack: FreeBSD holds an ACK for net.inet.tcp.delacktime (40ms default).
# The gateway pools its connections to osrm-routed and redis, and on a *reused*
# keep-alive connection that interacts with the peer's Nagle: the server waits
# for an ACK the client is sitting on. Measured in the api jail: a reused
# connection cost 54ms against 0.19ms for a fresh one, and every uncached
# request pays it two or three times over (redis GET, OSRM, redis SET). Turning
# it off took the gateway's p50 from 67ms to 9ms -- the same figure the Docker
# path serves. The symptom masquerades as slow hardware, so it is applied here
# rather than left as a note in the docs.
# One entry per line -- the reason text contains spaces.
HOST_SYSCTLS="net.inet.tcp.delayed_ack=0|gateway pools connections, a 40ms delayed ACK stalls every reused one"

phase_host() {
    _host_sysctl
    _host_loopback
    _host_resolver
}

# Apply each tunable to the running kernel and persist it, both idempotently.
# Runtime and boot state are set separately on purpose: a sysctl.conf entry does
# nothing until reboot, and a runtime sysctl is lost at one.
_host_sysctl() {
    printf '%s\n' "$HOST_SYSCTLS" | while IFS= read -r _entry; do
        [ -n "$_entry" ] || continue
        _kv="${_entry%%|*}"; _why="${_entry#*|}"
        _name="${_kv%%=*}"; _want="${_kv#*=}"
        _have="$(sysctl -n "$_name" 2>/dev/null)" || {
            log "sysctl ${_name} does not exist on this host; skipping"
            continue
        }
        if [ "$_have" = "$_want" ]; then
            log "${_name} already ${_want}"
        else
            log "${_name}: ${_have} -> ${_want}  (${_why})"
            run sysctl "${_name}=${_want}" >/dev/null
        fi
        _host_sysctl_persist "$_name" "$_want" "$_why"
    done
}

# Persist one tunable in sysctl.conf without disturbing anyone else's entries.
_host_sysctl_persist() {
    _name="$1"; _want="$2"; _why="$3"
    if [ ! -f "$SYSCTL_CONF" ]; then
        log "creating ${SYSCTL_CONF}"
        run sh -c ": >> '${SYSCTL_CONF}'"
    fi
    if grep -q "^[[:space:]]*${_name}=${_want}[[:space:]]*\$" "$SYSCTL_CONF" 2>/dev/null; then
        log "${SYSCTL_CONF} already sets ${_name}=${_want}"
        return 0
    fi
    _new="$(mktemp -t sysctl.conf)"
    # Drop any previous setting of this name -- ours or a stale one with a
    # different value -- so the file never ends up with two lines for it.
    grep -v "^[[:space:]]*${_name}=" "$SYSCTL_CONF" > "$_new" 2>/dev/null || :
    {
        echo ""
        echo "# ${HOST_TAG}: ${_why}"
        echo "${_name}=${_want}"
    } >> "$_new"
    run cp -p "$SYSCTL_CONF" "${SYSCTL_CONF}.bak-$(date +%Y%m%d-%H%M%S)"
    run install -m 644 -o root -g wheel "$_new" "$SYSCTL_CONF"
    rm -f "$_new"
    log "persisted ${_name}=${_want} in ${SYSCTL_CONF}"
}

# A jail without its own 127.0.0.1 remaps every loopback bind and connect to
# ip4.addr. Redis then sees non-loopback clients and protected mode answers
# DENIED to everything, silently disabling the L2 cache and the rate limiter's
# shared storage; osrm-routed's --ip 127.0.0.1 likewise ends up on the jail
# address, reachable by sibling jails.
_host_loopback() {
    [ -f "$JAIL_CONF" ] || die "${JAIL_CONF} not found"
    # Look for the address itself, not a marker: the block may already have been
    # edited by hand, and a duplicate ip4.addr breaks the next jail start.
    if awk -v jail="$JAIL_NAME" '
        $0 ~ "^" jail " *\\{" { inblock = 1 }
        inblock && /^ *\}/ { inblock = 0 }
        inblock && /ip4\.addr/ && /127\.0\.0\.1/ { found = 1 }
        END { exit found ? 0 : 1 }
    ' "$JAIL_CONF"; then
        log "jail.conf already grants ${JAIL_NAME} its own loopback"
    else
        log "adding a jail-local 127.0.0.1 on ${LOOPBACK_IF} to ${JAIL_NAME}"
        _new="$(mktemp -t jail.conf)"
        awk -v jail="$JAIL_NAME" -v iface="$LOOPBACK_IF" -v tag="$HOST_TAG" '
            $0 ~ "^" jail " *\\{" { inblock = 1 }
            inblock && /ip4\.addr *=/ && !done {
                print
                print "    # " tag ": loopback so 127.0.0.1 binds stay jail-local"
                print "    ip4.addr += \"" iface "|127.0.0.1\";"
                done = 1
                next
            }
            { print }
            END { if (!done) exit 3 }
        ' "$JAIL_CONF" > "$_new" || { rm -f "$_new"; die "no '${JAIL_NAME} {' block with an ip4.addr in ${JAIL_CONF}"; }
        _bak="${JAIL_CONF}.bak-$(date +%Y%m%d-%H%M%S)"
        run cp -p "$JAIL_CONF" "$_bak"
        run install -m 644 -o root -g wheel "$_new" "$JAIL_CONF"
        rm -f "$_new"
        if ! run jail -f "$JAIL_CONF" -e ";" >/dev/null 2>&1; then
            run cp -p "$_bak" "$JAIL_CONF"
            die "${JAIL_CONF} would not parse; restored from ${_bak}"
        fi
        log "jail.conf updated; previous copy at ${_bak}"
    fi

    if jls -j "$JAIL_NAME" ip4.addr 2>/dev/null | grep -q '127\.0\.0\.1'; then
        log "jail ${JAIL_NAME} already has its loopback"
        return 0
    fi
    log "restarting jail ${JAIL_NAME} to pick up the address (services restart with it)"
    run service jail restart "$JAIL_NAME"
    jls -j "$JAIL_NAME" ip4.addr 2>/dev/null | grep -q '127\.0\.0\.1' || \
        die "jail ${JAIL_NAME} came back without 127.0.0.1; check ${JAIL_CONF}"
}

# Once the jail has a real loopback, a stale "nameserver 127.0.0.1" pointing at
# the host's resolver stops failing fast and starts costing a full DNS timeout
# per lookup -- which is slow enough to break uv installing from PyPI.
_host_resolver() {
    _resolv="$(jail_path)/etc/resolv.conf"
    [ -f "$_resolv" ] || { log "no ${_resolv}; skipping resolver check"; return 0; }
    grep -q '^nameserver 127\.0\.0\.1$' "$_resolv" || {
        log "jail resolver has no loopback nameserver; nothing to do"
        return 0
    }
    if run jexec "$JAIL_NAME" sockstat -4 -l 2>/dev/null | grep -q '127\.0\.0\.1:53'; then
        log "jail runs its own resolver on 127.0.0.1:53; leaving resolv.conf alone"
        return 0
    fi
    log "removing unreachable 'nameserver 127.0.0.1' from the jail's resolv.conf"
    _new="$(mktemp -t resolv.conf)"
    grep -v '^nameserver 127\.0\.0\.1$' "$_resolv" > "$_new"
    run cp -p "$_resolv" "${_resolv}.bak-$(date +%Y%m%d-%H%M%S)"
    run install -m 644 -o root -g wheel "$_new" "$_resolv"
    rm -f "$_new"
}

# --- host firewall --------------------------------------------------------
# pf runs on the host, not in the jail, so publishing the gateway is host state
# that install.sh cannot reach. It is deliberately its own phase and never part
# of `make jail-up`: whether to expose an unauthenticated API is a policy call,
# and a bad ruleset loaded over SSH locks the host out.

PF_CONF="/etc/pf.conf"
PF_TAG="osrm-api-gateway"
PUBLISH_PORT="${JAIL_API_PORT:-8000}"

jail_ip() {
    jls -j "$JAIL_NAME" ip4.addr 2>/dev/null | cut -d, -f1
}

pf_ext_if() {
    # Reuse the ruleset's own macro when it defines one, so the added rules read
    # like their neighbours; otherwise name the default route's interface.
    if grep -Eq '^[[:space:]]*ext_if[[:space:]]*=' "$PF_CONF"; then
        echo '$ext_if'
    else
        route -n get default 2>/dev/null | awk '/interface:/ { print $2; exit }'
    fi
}

# Validate first, keep a backup, then load. Never edit $PF_CONF in place.
pf_apply() {
    _new="$1"
    if ! run pfctl -nf "$_new"; then
        rm -f "$_new"
        die "generated ruleset does not parse; ${PF_CONF} left untouched"
    fi
    _bak="${PF_CONF}.bak-$(date +%Y%m%d-%H%M%S)"
    run cp -p "$PF_CONF" "$_bak"
    run install -m 644 -o root -g wheel "$_new" "$PF_CONF"
    rm -f "$_new"
    run pfctl -f "$PF_CONF"
    log "pf reloaded; previous ruleset saved at ${_bak}"
    run pfctl -si 2>/dev/null | grep -q "Status: Enabled" || \
        log "WARNING: pf is not enabled -- sysrc pf_enable=YES && service pf start"
}

phase_publish() {
    [ -f "$PF_CONF" ] || die "${PF_CONF} not found; is pf configured on this host?"
    _ip="$(jail_ip)"
    [ -n "$_ip" ] || die "cannot read ip4.addr of jail '${JAIL_NAME}'; is it running?"
    _if="$(pf_ext_if)"
    [ -n "$_if" ] || die "cannot determine the external interface for ${PF_CONF}"

    if grep -Eq "^[[:space:]]*rdr .*port ${PUBLISH_PORT} ->" "$PF_CONF"; then
        if grep -q "# ${PF_TAG}\$" "$PF_CONF"; then
            log "port ${PUBLISH_PORT} is already published; nothing to do"
        else
            log "${PF_CONF} already redirects port ${PUBLISH_PORT} without the"
            log "'# ${PF_TAG}' marker, so it is left alone. Remove that rule by hand"
            log "if you want this target to own it."
        fi
        return 0
    fi

    log "publishing ${_ip}:${PUBLISH_PORT} on ${_if}"
    _new="$(mktemp -t pf.conf)"
    # pf rejects a ruleset whose sections are out of order: translation rules
    # must precede filter rules. Inserting the rdr immediately before the first
    # filter rule is valid whatever the file looks like; the pass rule appends.
    awk -v rdr="rdr on ${_if} proto tcp from any to any port ${PUBLISH_PORT} -> ${_ip} port ${PUBLISH_PORT} # ${PF_TAG}" '
        !done && ($1 == "block" || $1 == "pass" || $1 == "antispoof") { print rdr; done = 1 }
        { print }
        END { if (!done) print rdr }
    ' "$PF_CONF" > "$_new"
    printf 'pass in on %s proto tcp from any to any port %s flags S/SA keep state # %s\n' \
        "$_if" "$PUBLISH_PORT" "$PF_TAG" >> "$_new"

    pf_apply "$_new"
    run pfctl -sn | grep -F "port = ${PUBLISH_PORT}" || true
}

phase_unpublish() {
    [ -f "$PF_CONF" ] || die "${PF_CONF} not found; is pf configured on this host?"
    if ! grep -q "# ${PF_TAG}\$" "$PF_CONF"; then
        log "no '# ${PF_TAG}' rules in ${PF_CONF}; nothing to do"
        return 0
    fi
    log "removing '# ${PF_TAG}' rules from ${PF_CONF}"
    _new="$(mktemp -t pf.conf)"
    grep -v "# ${PF_TAG}\$" "$PF_CONF" > "$_new"
    pf_apply "$_new"
}

phase_doctor() {
    echo "Host     : $(hostname)"
    echo "Release  : $(uname -mr)"
    echo "Escalate : ${ESC_KIND}"
    echo "Jail     : ${JAIL_NAME}"
    printf '%s\n' "$HOST_SYSCTLS" | while IFS= read -r _entry; do
        [ -n "$_entry" ] || continue
        _kv="${_entry%%|*}"; _name="${_kv%%=*}"; _want="${_kv#*=}"
        _have="$(sysctl -n "$_name" 2>/dev/null || echo "n/a")"
        if grep -q "^[[:space:]]*${_name}=${_want}[[:space:]]*$" "$SYSCTL_CONF" 2>/dev/null; then
            _boot="persisted"
        else
            _boot="NOT PERSISTED -- reverts on reboot; run 'make jail-host'"
        fi
        if [ "$_have" = "$_want" ]; then
            echo "Tunable  : ${_name}=${_have} (want ${_want}) [${_boot}]"
        else
            echo "Tunable  : ${_name}=${_have} -- WANT ${_want}; run 'make jail-host' [${_boot}]"
        fi
    done
    if ! jls -j "$JAIL_NAME" jid >/dev/null 2>&1; then
        echo "Status   : NOT RUNNING"
        return 0
    fi
    echo "Status   : running (jid $(jls -j "$JAIL_NAME" jid), path $(jail_path))"
    if [ "$ESC_KIND" = "none" ]; then
        echo "Inside   : unavailable -- no way to jexec; see the message from any deploy target"
        return 0
    fi
    run jexec "$JAIL_NAME" sh -c '
        echo "Jailed   : $(sysctl -n security.jail.jailed) (1 = a jail)"
        echo "Memory   : $(($(sysctl -n hw.physmem) / 1048576)) MB"
        printf "Packages : "
        for p in osrm-backend redis python313 uv; do
            if pkg info -e "$p" 2>/dev/null; then printf "%s " "$p"; fi
        done
        echo ""'
}

stage_into_jail() {
    [ -d "${HOST_STAGE}/deploy/freebsd" ] || die "nothing staged at ${HOST_STAGE}; run 'make jail-stage'"
    _jp="$(jail_path)"
    log "copying ${HOST_STAGE} -> ${_jp}${JAIL_STAGE}"
    run rm -rf "${_jp}${JAIL_STAGE}"
    run mkdir -p "${_jp}${JAIL_STAGE}"
    run cp -R "${HOST_STAGE}/." "${_jp}${JAIL_STAGE}/"
}

case "$PHASE" in
    doctor)
        phase_doctor
        ;;
    host)
        require_esc
        phase_host
        ;;
    publish)
        require_esc
        phase_publish
        ;;
    unpublish)
        require_esc
        phase_unpublish
        ;;
    stop|status|health|logs)
        # Read-only or service-only: no staging needed.
        require_esc
        # shellcheck disable=SC2046
        run jexec "$JAIL_NAME" env $(jail_env) \
            sh "${JAIL_STAGE}/deploy/freebsd/install.sh" "$PHASE"
        ;;
    *)
        require_esc
        stage_into_jail
        # shellcheck disable=SC2046
        run jexec "$JAIL_NAME" env $(jail_env) \
            sh "${JAIL_STAGE}/deploy/freebsd/install.sh" "$PHASE"
        ;;
esac
