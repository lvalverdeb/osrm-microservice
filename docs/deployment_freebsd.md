# FreeBSD Jail Deployment

This is the detail page for one of the project's two deployment options. For the
comparison and the Docker path, see [deployment.md](deployment.md).

| Path | Targets | Files | Entry point |
|---|---|---|---|
| **Docker** | A Linux Docker host | `deploy/docker/` | `make compose-up` |
| **FreeBSD jail** | A jail on a FreeBSD host | `deploy/freebsd/` | `make jail-up` |

They share no state and can be used side by side. Nothing in `src/` differs
between them — `OSRM_BASE_URL` in `app/config.py` is the only thing that changes,
and it is already a setting.

## Why the jail path is not "compose, but over there"

**A FreeBSD jail cannot run Docker.** Jails share the FreeBSD kernel; Docker needs
Linux kernel namespaces and cgroups. So the jail path runs the same three services
natively instead:

| `deploy/docker/docker-compose.yml` service | In the jail |
|---|---|
| `osrm` | `www/osrm-backend` package, under `deploy/freebsd/osrm-routed` |
| `redis` | `databases/redis` package, using its own rc.d script |
| `api` | `python313` + `uv` venv, under `deploy/freebsd/osrm-api-gateway` |

The cache runs from its port's own rc.d script, configured via `sysrc`. The
engine and the gateway need scripts of our own — see the constraint below for
why the `osrm` script `www/osrm-backend` ships cannot be used.

## Access route

The `api` jail runs no sshd of its own — the host's sshd binds `*:22` and answers
for the jail's address as well, so SSHing to the jail IP silently lands on the
**host**. Every call therefore takes this route:

```
ssh <JAIL_HOST> -> deploy/freebsd/jailctl.sh -> jexec -> deploy/freebsd/install.sh
```

`jexec(8)` requires real root on the host. Being in `wheel` is not enough.

### One-time host setup

If `make jail-doctor` reports `Escalate : none`, pick one, as root on the host:

```sh
# Option A -- doas (lighter, FreeBSD-native)
pkg install -y doas
echo 'permit nopass :wheel' > /usr/local/etc/doas.conf

# Option B -- sudo
pkg install -y sudo && visudo

# Option C -- deploy as root instead
#   install an SSH key in /root/.ssh/authorized_keys, then:
make jail-up JAIL_HOST=root@10.211.55.33
```

## Usage

```sh
make jail-doctor      # identity, arch, memory, escalation method, package state
make jail-host        # host-level prerequisites: jail.conf loopback, jail resolver
make jail-bootstrap   # packages + service user  (slow: installs the Rust toolchain)
make jail-data        # osrm-extract / partition / customize  (slow, memory-hungry)
make jail-up          # deploy the gateway, (re)start all three services, health-check
make jail-health      # probes /ready (gateway + engine in one check)
make jail-logs        # gateway log + /var/log/messages
make jail-down        # stop all three services

make jail-publish     # expose the gateway on the host's IP (edits the host's pf.conf)
make jail-unpublish   # remove that redirect again
```

First run is `jail-host` → `jail-bootstrap` → `jail-data` → `jail-up`. After that, code changes
only need `jail-up`. The two `publish` targets are host state and stand apart
from that cycle — see "Reaching the gateway from outside the host".

`jail-bootstrap` and `jail-data` are the slow ones: the gateway is compiled from
source and the extract is memory-hungry. Both are one-time,
and `jail-up` refuses to run without them rather than failing halfway.

### After a reboot

Nothing to run. The host has `jail_enable="YES"`, and the jail's `rc.conf` has
`redis_enable`, `osrm_routed_enable` and `osrm_api_gateway_enable` set, so all
three services come back on their own. The pf rules live in the host's
`/etc/pf.conf`, so a published gateway stays published.

### Refreshing the map data

`jail-data` skips when the data is already built — it tests for the
`.osrm.cell_metrics` artifact — so a newer extract needs the old one removed
first:

```sh
ssh <JAIL_HOST> 'doas rm -rf /jails/<JAIL_NAME>/var/db/osrm-backend/<PROFILE>'
make jail-data && make jail-up
```

Drop a fresh `data/*.osm.pbf` in place first if you want a newer region file;
`make jail-stage` uploads it, otherwise the jail fetches `GEO_URL` itself.

### Overridable variables

Defaults live at the bottom of the `Makefile`; every one is `?=`.

| Variable | Default | Notes |
|---|---|---|
| `JAIL_HOST` | `developer@10.211.55.33` | SSH target — the **host**, not the jail |
| `JAIL_NAME` | `api` | Jail name passed to `jexec` |
| `JAIL_DIR` | `/usr/local/www/osrm-api-gateway` | App directory inside the jail |
| `JAIL_DATA_DIR` | `/var/db/osrm-backend` | Where `.osrm` data lands |
| `JAIL_API_PORT` | `8000` | Gateway bind port |
| `JAIL_OSRM_URL` | `http://127.0.0.1:5000` | Engine URL as seen from the gateway |
| `JAIL_REDIS_URL` | `redis://127.0.0.1:6379/0` | Empty string = L1-only cache |
| `PROFILE` | `car` | Shared with the Docker path |

All jail variables are `JAIL_`-prefixed on purpose: the `Makefile` does
`-include .env` + `export`, so an unprefixed `OSRM_BASE_URL` would leak a
development value into the jail.

## Reproducing this deployment

Every change this deployment needs is scripted and idempotent, so a rebuilt host
reaches the same state by re-running the targets. Nothing has to be remembered
or repeated by hand.

The configuration lives in `.env` at the repository root, which the `Makefile`
pulls in with `-include .env` + `export`. Only the settings that differ from the
defaults need to be there:

```sh
JAIL_HOST=developer@10.211.55.33   # SSH target: the host, not the jail
JAIL_NAME=api                      # jail to deploy into
JAIL_API_PORT=8000
JAIL_API_WORKERS=1                 # raise once metrics and limits are shared
PROFILE=car
```

From a bare host with the jail already created and running:

```sh
make jail-host        # jail.conf loopback + resolver fix   (restarts the jail if needed)
make jail-bootstrap   # packages, build toolchain, service user
make jail-data        # osrm-extract / partition / customize
make jail-up          # sources, venv, rc.d scripts, services, health check
make jail-publish     # optional: expose the gateway on the host's IP via pf
```

Each target may be re-run safely: `jail-host` checks the jail's address list
before touching `jail.conf`, `jail-bootstrap` installs only missing packages,
`jail-data` skips when the routing data is already built, `jail-publish` refuses
to duplicate a redirect. The only host state outside these targets is the jail's
own existence in `/etc/jail.conf` and whatever created it.

### What changes where: box level vs jail level

The split follows the two scripts: `jailctl.sh` runs on the **host**,
`install.sh` runs **inside the jail**. It matters mainly for blast radius —
every box-level change is shared with sibling jails, every jail-level change is
contained to this one.

**Box (host) level** — `jailctl.sh`, via `make jail-host` and `make jail-publish`:

| Change | Lands in | Why it cannot be done from inside |
|---|---|---|
| `net.inet.tcp.delayed_ack=0` | runtime `sysctl` **and** `/etc/sysctl.conf` | Without VNET the jail shares the host's network stack; `net.inet.*` is not writable from a jail |
| `ip4.addr += "lo1\|127.0.0.1"` | `/etc/jail.conf`, then `service jail restart` | It defines the jail; only the host can set it |
| pf `rdr` for `JAIL_API_PORT` | `/etc/pf.conf`, then `pfctl -f` | pf runs in the host kernel; a jail has no ruleset of its own |

All three are idempotent and back up the file they touch before editing it.

Of these, only the sysctl is performance tuning — the other two are connectivity
and isolation plumbing. Further optimisation tunables belong in `HOST_SYSCTLS`
in `deploy/freebsd/jailctl.sh`, which is a table for exactly that purpose.

**Jail level** — `install.sh`, via `make jail-bootstrap`, `jail-data`, `jail-up`:

| Change | Lands in |
|---|---|
| Packages `osrm-backend`, `redis`, `python313`, `uv` | the jail's pkg database |
| Service user `osrmapi` | the jail's `/etc/passwd` |
| App tree, venv, `pyproject.toml` | `/usr/local/www/osrm-api-gateway`, `root:osrmapi` 750 |
| `.env` — shared `deploy/env/app.env` plus the generated overlay | `${JAIL_DIR}/.env`, `root:osrmapi` 640 |
| rc.d scripts `osrm-api-gateway`, `osrm-routed` | `/usr/local/etc/rc.d/`, mode 555 |
| `redis-cache.conf` (`save ""`, `appendonly no`, `protected-mode no`) | `/usr/local/etc/redis-osrm-cache.conf`, mode 644 |
| `sysrc osrm_enable=NO`, `sysrc -x osrm_file osrm_flags` | the jail's `/etc/rc.conf` — disables the port's stock service |
| `sysrc osrm_routed_enable`, `osrm_routed_base` | the jail's `/etc/rc.conf` |
| `sysrc redis_enable`, `redis_config` | the jail's `/etc/rc.conf` |
| `sysrc osrm_api_gateway_{enable,dir,user,host,port,workers}` | the jail's `/etc/rc.conf` |
| OSRM dataset, `chown osrm:osrm` | `/var/db/osrm-backend/<profile>` |
| `PROMETHEUS_MULTIPROC_DIR` | the rc.d process environment, only when `workers > 1` |

Nothing inside the jail touches `sysctl`, `loader.conf` or `login.conf`: the one
kernel-level change in the whole deployment is the single host sysctl above.

**The one that straddles the line.** The jail's `/etc/resolv.conf` is jail state
but is edited from the host, at `$(jail_path)/etc/resolv.conf`. That is
deliberate: `install.sh`'s first phase is `pkg install`, which needs working DNS,
so the resolver has to be correct *before* anything runs inside — and deciding
whether the jail runs its own resolver needs `jls`/`sockstat` from outside.

**Not scripted.** The deployment assumes, but does not create: `jail_enable="YES"`,
the jail's own entry in `/etc/jail.conf`, the host's `sshd` (the jail runs none),
and root or `doas`/`sudo` for `jexec(8)`. `make jail-doctor` reports the
escalation method; the rest is yours to provide.

### What `jail-host` fixes, and why it is not part of `jail-up`

Three things live on the host and cannot be reached from inside the jail:

0. **Kernel tunables** (`/etc/sysctl.conf`). A jail without VNET shares the
   host's network stack, so `net.inet.*` is host state. Currently one entry:

   | Tunable | Value | Why |
   |---|---|---|
   | `net.inet.tcp.delayed_ack` | `0` | FreeBSD holds an ACK for `net.inet.tcp.delacktime` (40 ms). The gateway pools its connections to `osrm-routed` and Redis, and on a **reused** keep-alive connection that meets the peer's Nagle: the server waits for an ACK the client is sitting on. |

   Measured in the `api` jail: a reused connection cost **54 ms** against
   **0.19 ms** for a fresh one, and every uncached request pays it two or three
   times over (Redis `GET`, OSRM, Redis `SET`). Turning it off took the
   gateway's p50 from **67 ms to 9 ms** — the same figure the Docker path
   serves, on a host with half the cores and a sixth of the RAM.

   This one matters disproportionately because of how it presents: the jail
   simply looks like slower hardware. Nothing errors, nothing logs, and the
   per-endpoint profile looks plausible. `make jail-doctor` reports the tunable
   and whether it is persisted, so a host that has drifted says so.

   Both the running kernel and `/etc/sysctl.conf` are set, separately: a
   `sysctl.conf` entry does nothing until reboot, and a runtime `sysctl` is lost
   at one. Existing entries in the file are preserved and the edit is
   idempotent; a timestamped backup is written before any change.

1. **A jail-local `127.0.0.1`** (`ip4.addr += "lo1|127.0.0.1"`). Without it the
   kernel remaps every loopback bind to `ip4.addr`, so Redis sees non-loopback
   clients and protected mode answers `DENIED` to every command — silently
   disabling the L2 cache and the rate limiter's shared storage — while
   `osrm-routed --ip 127.0.0.1` ends up on the jail address where sibling jails
   can reach it.
2. **The jail's resolver.** A stale `nameserver 127.0.0.1` pointing at the
   host's resolver fails instantly while loopback is remapped, but costs a full
   DNS timeout per lookup once the jail has its own — slow enough to break `uv`
   installing from PyPI. `jail-host` drops it unless the jail runs a resolver of
   its own.

It is a separate target because applying it can restart the jail, and because
`jail-up` runs on every code change while this runs once per host.

**The tunables are host-wide.** Sibling jails on the same host run under them
too. That is inherent — a shared network stack cannot be tuned per jail without
VNET — so `jail-host` stays an explicit, separately-invoked step rather than
something `jail-up` does behind your back.

## Service configuration

`install.sh` writes these with `sysrc`, mirroring `deploy/docker/docker-compose.yml`:

```sh
osrm_routed_enable="YES"
osrm_routed_base="/var/db/osrm-backend/car/costa-rica-latest.osrm"
redis_enable="YES"
redis_config="/usr/local/etc/redis-osrm-cache.conf"
osrm_api_gateway_enable="YES"
osrm_api_gateway_dir="/usr/local/www/osrm-api-gateway"
osrm_api_gateway_user="osrmapi"
osrm_api_gateway_port="8000"
osrm_api_gateway_workers="1"
```

`osrm_api_gateway_workers` above 1 also makes the rc.d script export
`PROMETHEUS_MULTIPROC_DIR`, without which each worker would report only its own
share of the traffic on `/metrics`.

Two of the three services run from rc.d scripts in `deploy/freebsd/`:
`osrm-routed` and `osrm-api-gateway`. Both run under `daemon(8)` with `-r`, so
they restart if they exit. The engine binds `127.0.0.1:5000` and serves
`--algorithm mld --max-trip-size 200`, matching `deploy/docker/docker-compose.yml`; only the
gateway is exposed. Root owns the app directory and the service user only reads
it — the gateway cannot rewrite its own source or `.env` at runtime.

Redis uses its own port's rc.d script unchanged.

## Reaching the gateway from outside the host

The jails live on `lo1`, a cloned **loopback** interface, so `10.0.0.0/24` exists
only inside the FreeBSD host:

```sh
cloned_interfaces="lo1"
ifconfig_lo1="inet 10.0.0.1 netmask 255.255.255.0"
```

Nothing off that box has a route to the jail address, and `pf.conf` ends with
`block in all`, so a workstation hitting `10.211.55.33:8000` gets a silent drop —
a connect timeout rather than a refusal. `nat on $ext_if from $jail_net to any`
covers outbound traffic only.

### Option A — SSH tunnel (no host changes)

```sh
ssh -N -L 8000:10.0.0.12:8000 developer@10.211.55.33
# http://localhost:8000/docs
```

### Option B — publish it with pf

```sh
make jail-publish     # host port 8000 -> the jail's gateway
make jail-unpublish   # remove it again
```

`jail-publish` reads the jail's address from `jls`, reuses the ruleset's own
`ext_if` macro when it defines one, and adds two rules to the host's
`/etc/pf.conf`, each tagged so `jail-unpublish` can remove exactly those lines:

```
rdr on $ext_if proto tcp from any to any port 8000 -> 10.0.0.12 port 8000 # osrm-api-gateway
pass in on $ext_if proto tcp from any to any port 8000 flags S/SA keep state # osrm-api-gateway
```

Both are needed: pf filters **after** translation, so the `rdr` alone still hits
`block in all`. The `rdr` is inserted before the first filter rule because pf
rejects a ruleset whose translation rules come after filtering.

Every run validates with `pfctl -nf` before loading and copies the live ruleset
to `/etc/pf.conf.bak-<timestamp>` first, so a rejected ruleset leaves the host
untouched and a bad load is one `cp` away from reverting. Existing states are
not flushed — your SSH session and in-flight requests survive the reload. Both
targets are idempotent, and `jail-publish` refuses to touch a redirect for that
port it did not create.

`JAIL_API_PORT` selects the port on both sides.

Then the gateway answers on `http://10.211.55.33:8000/docs`.

Two things this route implies. The gateway has **no authentication** — `/docs`,
`/metrics` and every routing endpoint become reachable by anything on the host's
network, with only the gateway's per-IP rate limits in front. That is why publishing
is its own target and not part of `make jail-up`: exposing an unauthenticated
API is a policy decision, and `/etc/pf.conf` is host state shared with every
other jail.

## Known constraints

### The gateway is compiled in the jail

`install.sh` installs exactly three packages: `osrm-backend`, `redis` and
`rust`. There is no Python in the jail any more -- the gateway is a single
static binary built from `gateway/` on the box.

That build is the slow part of `jail-bootstrap`, and it is memory-hungry: the
`jail` cargo profile exists for this, trading fat LTO and a single codegen unit
for thin LTO and sixteen, because the release profile risks the OOM killer on a
2 GB box shared with two other jails. Runtime difference is immaterial for an
I/O-bound proxy.

### The port's osrm service is replaced, not configured

`www/osrm-backend` ships an `osrm` rc.d script, and it cannot start the engine
with any flags set. `rc.subr` expands `${name}_flags` between the command and
its arguments:

```sh
_doit="$_cpusetcmd $command $rc_flags $command_args"
```

`command` is `/usr/sbin/daemon`, so a non-empty `osrm_flags` is handed to
`daemon(8)` rather than to `osrm-routed`:

```
daemon: unrecognized option `--algorithm'
/usr/local/etc/rc.d/osrm: WARNING: failed to start osrm
```

The script interpolates `${osrm_flags}` into `command_args` as well, so there
is no value that both reaches the engine and leaves `daemon` alone. Since MLD
data cannot be served without `--algorithm mld`, `deploy/freebsd/osrm-routed`
replaces the service: flags live in `command_args`, and no
`osrm_routed_flags` variable exists to be expanded. The `services` phase sets
`osrm_enable=NO` and removes the port's `osrm_file`/`osrm_flags` from
`rc.conf`.

That script also drops the port's other constraint. Its start check required
`osrm_file` to be a regular file, but `.osrm` is a base path, not a file:
`osrm-extract`, `osrm-partition` and `osrm-customize` read and write
`<base>.osrm.<suffix>` siblings only, and OSRM 6 writes no bare `.osrm` file.
The replacement checks `<base>.osrm.cell_metrics` — the last artifact
`osrm-customize` writes — which is also what the `data` and `services` phases
use to answer "is the data built?".

### Redis needs the jail to have a real loopback

The port's `redis.conf` binds `127.0.0.1` with `protected-mode yes`. In a jail
without its own loopback that bind is remapped to `ip4.addr`, so every client --
including one inside the same jail -- arrives from a non-loopback address and
Redis answers:

```
DENIED Redis is running in protected mode because protected mode is enabled
and no password is set for the default user.
```

Nothing fails loudly when this happens: `RedisCache` logs a warning per
operation and serves from L1, and the rate limiter silently falls back to per-process
limits. This deployment ran that way until it was measured.

`make jail-host` fixes the addressing. `deploy/freebsd/redis-cache.conf` then
covers what is left: a jail still has no per-jail loopback *source* address, so
a client connecting to `127.0.0.1` leaves with a source of `ip4.addr` and
protected mode would keep rejecting it. Turning protected mode off is safe only
because the listener itself is now on the jail's real `127.0.0.1` -- sibling
jails cannot reach it on either address, and host root can `jexec` in regardless.

That file also sets `save ""`, matching `deploy/docker/docker-compose.yml`: this is a cache,
and with the port's `stop-writes-on-bgsave-error yes` a failed snapshot fork on
a small box would make Redis start refusing writes.

### Memory

`osrm-extract` and the Rust build are both memory-hungry, and the host has under
2 GB shared across all jails. If either is OOM-killed, in order of preference:

1. Add swap on the host.
2. Stop the other jails for the duration of the build.
3. Build the data elsewhere and copy it into `JAIL_DATA_DIR`. This only works if
   the producing OSRM version matches the port's (`6.0.0.g20250916`) — the engine
   rejects `.osrm` files written by a different version, so data from the Docker
   image is not automatically compatible.

### Data is built in the jail

`jail-data` mirrors `deploy/docker/Dockerfile.builder` step for step, so both paths produce
equivalent data. Building in place guarantees the `.osrm` format matches the
installed engine. `make jail-stage` uploads `data/*.osm.pbf` if you already have
it locally, so only the extract runs remotely, not a fresh 38 MB download.
