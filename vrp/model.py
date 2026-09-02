"""Canonical domain model — SDD §4.1 and §4.2.

Solver-independent by construction: nothing here knows how a route is produced,
only what a legal one looks like. Both the evaluator and the independent
verifier read these types, which is the one thing they are allowed to share.

**Integers throughout.** Instants and durations are whole seconds, quantities
whole units, distances whole metres. Floating-point seconds accumulate error
along a route, and an arrival time out by a microsecond makes `INV-4`
unfalsifiable — the invariant that catches most timeline bugs would silently
stop catching them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from vrp.battery import FULL_PPT
from vrp.hos.rules import DriverState
from vrp.timedependent import arc_class_of

# MTX-5: unreachable pairs are represented explicitly and handled as hard-
# infeasible arcs. Negative because it must be impossible to mistake for a
# cost: a large finite sentinel is "expensive but possible", so a solver with
# nothing better optimises it into a plan and returns a leg nobody can drive.
# Reading one through `duration()`/`distance()` raises rather than returning
# it, so the sentinel cannot reach arithmetic by accident either.
if TYPE_CHECKING:                       # pragma: no cover
    from vrp.battery import ChargingCurve
    from vrp.timedependent import SpeedProfile

UNREACHABLE = -1


class UnreachableArc(LookupError):
    """Raised when travel is read for a pair no route connects."""


class ValidationError(ValueError):
    """A domain object was constructed in a state the specification forbids."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _require_int(value: Any, name: str) -> None:
    # bool is an int in Python and is never a legal quantity or instant.
    _require(isinstance(value, int) and not isinstance(value, bool),
             f"{name} must be a whole number, got {value!r}")


# FR-25's three sources, most binding first. The order is the requirement: a
# legal obligation outranks a contract, and a contract outranks a preference
# somebody paid for. Ranked here rather than in a solver so every consumer --
# the search, the objective, the explanation -- agrees about precedence.
PRIORITY_SOURCES: tuple[str, ...] = ("STATUTORY", "SLA", "COMMERCIAL")


def precedence(order) -> tuple[int, int]:
    """How this order ranks: its tier, then what put it there. FR-13, FR-25.

    `FR-13`'s tiers remain the mechanism and this is what fills them, so the
    tier still decides first and the source only separates orders that would
    otherwise be indistinguishable. Lower is more protected, matching
    `priority_tier`'s own convention and `objective.Tier`'s.
    """
    return (order.priority_tier, PRIORITY_SOURCES.index(order.priority_source))


def sla_window(reported_at: int, respond_within: int,
               opens_at: int = 0) -> TimeWindow:
    """The window an SLA implies. FR-25, `UC-116`.

    `UC-116` breaks on "fixed windows. The window is derived from the fault
    timestamp plus the SLA, so it is computed at intake and differs per order."
    Two faults of the same severity reported an hour apart are due an hour
    apart, and writing one window for both is how a four-hour response target
    becomes a five-hour one for half the estate.
    """
    _require_int(reported_at, "reported_at")
    _require_int(respond_within, "respond_within")
    _require(respond_within > 0, "an SLA response target must be positive")
    return TimeWindow(start=max(opens_at, reported_at),
                      end=reported_at + respond_within)


def _require_set(value: Any, name: str) -> None:
    """A set-typed field must actually be a set. `UC-067`.

    These fields are consumed with set algebra -- `order.incompatible_with &
    carried`, `required_skills - vehicle.skills` -- so a tuple that survives
    construction raises `TypeError` inside the verifier, a long way from the
    caller that built it. A string is rejected separately because `frozenset`
    accepts one and silently yields its characters.
    """
    _require(not isinstance(value, str),
             f"{name} must be a set of strings, not the single string {value!r}")
    _require(isinstance(value, (set, frozenset)),
             f"{name} must be a set, got {type(value).__name__}")


@dataclass(frozen=True)
class TimeWindow:
    """A window a stop may be served in. §4.1."""

    start: int
    end: int
    hardness: str = "HARD"
    earliness_cost_per_sec: int = 0
    lateness_cost_per_sec: int = 0

    def __post_init__(self) -> None:
        _require_int(self.start, "start")
        _require_int(self.end, "end")
        _require(self.end >= self.start, f"end {self.end} precedes start {self.start}")
        _require(self.hardness in ("HARD", "SOFT"), f"unknown hardness {self.hardness!r}")
        for name in ("earliness_cost_per_sec", "lateness_cost_per_sec"):
            cost = getattr(self, name)
            _require_int(cost, name)
            _require(cost >= 0, f"{name} must not be negative")
            # A penalty on a hard window is a contradiction: a hard window
            # cannot be violated, so the penalty could never apply.
            _require(self.hardness == "SOFT" or cost == 0,
                     f"{name} is only meaningful on a SOFT window")

    def contains(self, instant: int) -> bool:
        return self.start <= instant <= self.end


@dataclass(frozen=True)
class Location:
    id: str
    lat: float
    lon: float
    matrix_index: int
    dwell_overhead: int = 0
    # FR-11 site access. Empty means unrestricted -- the inverse reading would
    # make every ordinary address unservable. Weight is modelled because it is
    # the limit that collapses bridges; height, emission class and permitted
    # hours are named in §4.1 and are not modelled yet.
    # FR-19 / §6.9: loading bays are finite. None means unconstrained -- most
    # depots are not the bottleneck, and reading an unset value as zero would
    # make every plan fiction.
    dock_capacity: int | None = None
    # FR-31 / §4.1's `inventory_by_dimension`. A depot is not a spring: it holds
    # a finite amount of each thing, and §7.8 makes that a *global* constraint
    # rather than a per-route one. None means unstocked and therefore
    # unconstrained -- the same reading `dock_capacity` has, and for the same
    # reason: treating an unmeasured depot as empty would make every existing
    # plan fiction the moment one depot started counting. A dimension absent
    # from a stocked depot's inventory is likewise unconstrained; a depot that
    # counts kilograms has said nothing about pallets.
    inventory: dict[str, int] | None = None
    access_classes: frozenset[str] = frozenset()
    max_vehicle_kg: int | None = None

    def __post_init__(self) -> None:
        _require(bool(self.id), "location id must not be empty")
        _require(-90.0 <= self.lat <= 90.0, f"latitude {self.lat} out of range")
        _require(-180.0 <= self.lon <= 180.0, f"longitude {self.lon} out of range")
        _require_int(self.matrix_index, "matrix_index")
        _require(self.matrix_index >= 0, "matrix_index must not be negative")
        _require_int(self.dwell_overhead, "dwell_overhead")
        _require(self.dwell_overhead >= 0, "dwell_overhead must not be negative")
        if self.dock_capacity is not None:
            _require_int(self.dock_capacity, "dock_capacity")
            _require(self.dock_capacity >= 0,
                     "dock_capacity must not be negative")
        for dimension, amount in (self.inventory or {}).items():
            _require_int(amount, f"inventory[{dimension}]")
            _require(amount >= 0,
                     f"inventory[{dimension}] must not be negative")
        if self.max_vehicle_kg is not None:
            _require_int(self.max_vehicle_kg, "max_vehicle_kg")
            _require(self.max_vehicle_kg >= 0,
                     "max_vehicle_kg must not be negative")


@dataclass(frozen=True)
class StopSpec:
    """One end of an order: where it happens, when it may, and for how long."""

    location_id: str
    time_windows: tuple[TimeWindow, ...] = ()
    service_fixed: int = 0
    # FR-05: service is fixed + per-unit + per-vehicle-type. The per-unit term
    # is what makes a twenty-parcel drop cost more than a one-parcel drop;
    # without it §6.2's "parking and walking dominate" is modelled as a
    # constant, which is exactly the guessed service time it warns against.
    service_per_unit: int = 0
    # Which quantity the per-unit term counts. A stop handling 400 kg of two
    # parcels takes two parcels' worth of handling, not four hundred
    # kilograms' worth, so the dimension is named rather than inferred.
    service_per_unit_dimension: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.location_id), "location_id must not be empty")
        _require_int(self.service_fixed, "service_fixed")
        _require(self.service_fixed >= 0, "service_fixed must not be negative")
        _require_int(self.service_per_unit, "service_per_unit")
        _require(self.service_per_unit >= 0,
                 "service_per_unit must not be negative")
        # §4.1 says windows are disjoint and sorted. Enforced rather than
        # assumed: an evaluator that trusts the ordering will pick the wrong
        # window when it is violated, and do so silently.
        previous_end: int | None = None
        for w in self.time_windows:
            _require(isinstance(w, TimeWindow), "time_windows must hold TimeWindow")
            if previous_end is not None:
                _require(w.start > previous_end,
                         "time_windows must be sorted and disjoint")
            previous_end = w.end


@dataclass(frozen=True)
class Order:
    id: str
    kind: str
    quantities: dict[str, int] = field(default_factory=dict)
    pickup: StopSpec | None = None
    delivery: StopSpec | None = None
    priority_tier: int = 0
    prize: int = 0
    release_time: int = 0
    required_skills: frozenset[str] = frozenset()
    # FR-10 order-to-order incompatibility, as classes rather than order ids:
    # "foodstuff must not share a compartment with hazardous goods" is a
    # statement about kinds, and §6.5 requires incremental class-count tracking
    # per route precisely because pairwise checking is quadratic per move.
    # FR-25: what fills a priority tier, kept apart from the tier itself.
    # §12.2 recommended the split and `UC-117` says why in one line: "Three
    # tiers with different clocks are three different constraints, not three
    # weights on one." They are ordered differently (statutory outranks a
    # contract, which outranks a commercial preference), they expire
    # differently (an SLA clock runs from an intake timestamp; an obligation
    # does not run at all), and only one of them is negotiable.
    # FR-24: how long the goods or the passenger may be aboard, measured from
    # departing the pickup to arriving at the delivery. `UC-092` and `UC-157`
    # both break on the same confusion: "the clock starts at loading, so the
    # constraint is elapsed time since departure, not arrival time at the
    # customer". A delivery window says when the drop may happen; this says how
    # long the journey may take, and an instance can need both.
    max_ride_time: int | None = None
    priority_source: str = "COMMERCIAL"
    order_class: str | None = None
    incompatible_with: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _require(bool(self.id), "order id must not be empty")
        _require(self.kind in ("JOB", "SHIPMENT"), f"unknown kind {self.kind!r}")
        for dimension, amount in self.quantities.items():
            _require_int(amount, f"quantities[{dimension}]")
            _require(amount >= 0, f"quantities[{dimension}] must not be negative")
        if self.kind == "SHIPMENT":
            _require(self.pickup is not None and self.delivery is not None,
                     "a SHIPMENT needs both a pickup and a delivery")
        else:
            _require((self.pickup is None) != (self.delivery is None),
                     "a JOB needs exactly one of pickup or delivery")
        if self.max_ride_time is not None:
            _require_int(self.max_ride_time, "max_ride_time")
            _require(self.max_ride_time > 0,
                     "a maximum ride time must be positive")
            # A ride is a journey between two places, so it needs both. A JOB
            # has one stop and its elapsed time is its service duration, which
            # `FR-05` already models and this would silently duplicate.
            _require(self.kind == "SHIPMENT",
                     "max_ride_time bounds the time between a pickup and its "
                     "delivery, so it belongs on a SHIPMENT; a job has one stop")
        _require(self.priority_source in PRIORITY_SOURCES,
                 f"unknown priority source {self.priority_source!r}; "
                 f"§12.2 names {', '.join(PRIORITY_SOURCES)}")
        # `UC-046`: under a universal service obligation "no address may be
        # declined, so the drop-the-unprofitable-stop behaviour that helps
        # elsewhere is prohibited". A prize is the price at which declining is
        # acceptable, so a statutory order carrying one is a contradiction
        # written down -- and a quiet one, because the solver would simply
        # take the money.
        _require(self.priority_source != "STATUTORY" or self.prize == 0,
                 "a STATUTORY order may not carry a prize: a prize is the "
                 "price at which declining is acceptable, and there is none")
        _require_set(self.required_skills, "required_skills")
        _require_set(self.incompatible_with, "incompatible_with")
        _require(not self.incompatible_with or self.order_class,
                 "an order declaring incompatible_with must have an "
                 "order_class, or nothing can be incompatible with it in turn")
        _require_int(self.priority_tier, "priority_tier")
        _require_int(self.prize, "prize")
        _require_int(self.release_time, "release_time")

    @property
    def stops(self) -> tuple[StopSpec, ...]:
        return tuple(s for s in (self.pickup, self.delivery) if s is not None)


@dataclass(frozen=True)
class Vehicle:
    id: str
    capacities: dict[str, int]
    shift: TimeWindow
    start_location_id: str
    end_location_id: str | None = None
    max_duration: int | None = None
    max_distance: int | None = None
    skills: frozenset[str] = frozenset()
    # Hours-of-service rule set name (§6.4), e.g. "EU-561". None means the
    # vehicle is not subject to driving-hours law, which is a claim about the
    # operation rather than a default -- INV-7 reports "not applicable" only
    # when no vehicle in the problem declares a rule set.
    # FR-07: cost belongs to the vehicle, not the fleet. These lived on
    # ObjectiveSpec as one set for everybody, which made a 3.5-tonne van and an
    # artic cost the same per kilometre -- the "H" in MDHVRPTW, decorative.
    fixed_cost: int = 0
    cost_per_metre: int = 0
    cost_per_second: int = 0
    overtime_cost_per_second: int = 0
    # FR-33 names three hire structures -- "per-job, per-day, per-km". Per-day
    # is `fixed_cost` and per-km is `cost_per_metre`; this is the third. A
    # contractor paid per drop is not one paid per kilometre, and folding the
    # first into the second is the amortisation OBJ-4 forbids.
    cost_per_order: int = 0
    # FR-07's routing profile. A profile is a *matrix*, and a Problem pins one,
    # so the adapter refuses a fleet that mixes them rather than routing a
    # bicycle as a lorry. See `_single_profile`.
    profile: str = "driving"
    # FR-05's per-vehicle-type component, in parts per thousand because CON-4
    # forbids floating-point accumulation. 1000 leaves handling unchanged; 600
    # is a tail-lift van at 60% of manual handling. Scales the handling only --
    # see `service_time`.
    service_factor_ppt: int = 1000
    # FR-11's other side: what this vehicle is, for site access purposes.
    # FR-09 / §6.8: a van that empties before its shift ends returns, reloads
    # and goes again. Reload is only possible where stock is, so the locations
    # are named rather than assumed to be the start depot.
    reload_locations: frozenset[str] = frozenset()
    max_reloads: int = 0
    reload_duration: int = 0
    # FR-20: EV range and en-route recharging. `battery_wh` is the switch --
    # None is a vehicle that burns diesel and is untouched by any of this, so
    # a mixed fleet needs no flag and every existing instance keeps its
    # behaviour. Charging is a reload of a different dimension, and the fields
    # deliberately echo the ones above: energy is only available where a
    # charger is, so the places are named rather than assumed.
    battery_wh: int | None = None
    consumption_wh_per_km: int = 0
    charger_locations: frozenset[str] = frozenset()
    charging_curve: ChargingCurve | None = None
    # Vans start their shift plugged in overnight. Stated rather than assumed
    # so a second round on the same battery can say what it inherited.
    initial_soc_ppt: int = FULL_PPT
    access_class: str | None = None
    gross_weight_kg: int | None = None
    # FR-08's "end-anywhere". Deliberately not spelled `end_location_id=None`:
    # that already means "end where you started" and much of the codebase reads
    # it that way, so reusing it would change the meaning of existing fleets
    # silently rather than adding a new capability.
    open_route: bool = False
    hos_rules: str | None = None
    # Hours already consumed before this duty. §6.4 makes this mandatory input
    # where tachograph or ELD data exists: planning a fresh nine-hour day for a
    # driver who already drove six is a compliance incident.
    initial_state: DriverState | None = None

    def __post_init__(self) -> None:
        _require(bool(self.id), "vehicle id must not be empty")
        _require_set(self.skills, "skills")
        for dimension, limit in self.capacities.items():
            _require_int(limit, f"capacities[{dimension}]")
            _require(limit >= 0, f"capacities[{dimension}] must not be negative")
        _require(isinstance(self.shift, TimeWindow), "shift must be a TimeWindow")
        _require(bool(self.start_location_id), "start_location_id must not be empty")
        for name in ("max_duration", "max_distance"):
            limit = getattr(self, name)
            if limit is not None:
                _require_int(limit, name)
                _require(limit >= 0, f"{name} must not be negative")
        for name in ("fixed_cost", "cost_per_metre", "cost_per_second",
                     "overtime_cost_per_second", "cost_per_order"):
            cost = getattr(self, name)
            _require_int(cost, name)
            # A negative cost is a vehicle that is paid to drive, and a solver
            # finds that arbitrage on the first iteration.
            _require(cost >= 0, f"{name} must not be negative")
        _require(bool(self.profile), "profile must not be empty")
        _require_int(self.max_reloads, "max_reloads")
        _require(self.max_reloads >= 0, "max_reloads must not be negative")
        _require_int(self.reload_duration, "reload_duration")
        _require(self.reload_duration >= 0,
                 "reload_duration must not be negative")
        _require(not self.max_reloads or self.reload_locations,
                 "max_reloads without reload_locations permits reloading "
                 "nowhere; name the depots or satellites that hold stock")
        _require_int(self.service_factor_ppt, "service_factor_ppt")
        # Zero would service every stop instantly, which is never meant and is
        # precisely what a solver would exploit.
        _require(self.service_factor_ppt > 0,
                 "service_factor_ppt must be positive")
        self._require_coherent_battery()

    def _require_coherent_battery(self) -> None:
        """FR-20's fields describe one vehicle or none of it.

        Half a specification is the dangerous state: a battery with no
        consumption is a van with infinite range, and chargers named on a
        vehicle that has no battery is a claim nobody can act on. Both are
        refused here rather than discovered as a plan that drives forever.
        """
        _require_set(self.charger_locations, "charger_locations")
        if self.battery_wh is None:
            _require(not self.charger_locations and self.charging_curve is None
                     and self.consumption_wh_per_km == 0,
                     f"{self.id} declares charging but no battery_wh; a "
                     "vehicle with no battery does not have a range")
            return
        _require_int(self.battery_wh, "battery_wh")
        _require(self.battery_wh > 0, "battery_wh must be positive")
        _require_int(self.consumption_wh_per_km, "consumption_wh_per_km")
        _require(self.consumption_wh_per_km > 0,
                 f"{self.id} has a battery and no consumption, which is a "
                 "vehicle with unlimited range wearing FR-20's fields")
        _require(self.charging_curve is not None,
                 f"{self.id} has a battery and no charging curve, so how long "
                 "it spends on a charger is unanswerable")
        _require_int(self.initial_soc_ppt, "initial_soc_ppt")
        _require(0 <= self.initial_soc_ppt <= FULL_PPT,
                 f"initial_soc_ppt must be between 0 and {FULL_PPT}")

    @property
    def is_electric(self) -> bool:
        return self.battery_wh is not None

    @property
    def ends_at(self) -> str:
        """Where a *closed* route finishes; the start when none is given.

        Meaningless for an open route, which finishes at its last stop and has
        no fixed end. Check `open_route` before relying on this.
        """
        return self.end_location_id or self.start_location_id


@dataclass(frozen=True)
class TravelMatrix:
    """Pinned travel data. `version` is what INV-4 checks a solution against."""

    version: str
    durations: tuple[tuple[int, ...], ...]
    distances: tuple[tuple[int, ...], ...]
    # NFR-04 and MTX-11: why this matrix is not what was asked for, or None.
    # A sentence rather than a flag, because an operator deciding whether to
    # dispatch a `DEGRADED` plan needs to know which arcs are guesses and why --
    # "true" tells them the plan is suspect and nothing about what to do.
    degraded: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.version), "matrix version must not be empty")
        _require(self.degraded is None or bool(self.degraded.strip()),
                 "a degraded matrix must say why; an empty reason is a flag "
                 "pretending to be an explanation")
        size = len(self.durations)
        for name, grid in (("durations", self.durations), ("distances", self.distances)):
            _require(len(grid) == size, f"{name} must have {size} rows")
            for row in grid:
                _require(len(row) == size, f"{name} must be square ({size}x{size})")
                for cell in row:
                    _require_int(cell, f"{name} cell")
                    _require(cell >= 0 or cell == UNREACHABLE,
                             f"{name} cell {cell} is negative and is not "
                             f"UNREACHABLE ({UNREACHABLE})")

    @property
    def size(self) -> int:
        """How many locations this matrix covers.

        A property rather than `len(matrix.durations)` at four call sites,
        because a matrix that computes its cells on demand has no `durations`
        to take the length of -- and §7.6's instances are too large to store
        one. Callers want the count, not the storage.
        """
        return len(self.durations)

    def extremes(self) -> tuple[int, int]:
        """The longest leg and the slowest leg. §5.1's instance-derived bounds.

        Owned here for the same reason as `size`: the objective needs a bound,
        not the grids. A matrix that computes cells on demand can bound itself
        far more cheaply than by enumerating n^2 of them, and §5.1 asks only
        that the bound be real -- "deliberately loose rather than tight".
        """
        longest = max((max(row) for row in self.distances), default=0)
        slowest = max((max(row) for row in self.durations), default=0)
        return longest, slowest

    def is_reachable(self, origin: int, destination: int) -> bool:
        """Whether any route connects the pair. Ask before reading travel."""
        return self.durations[origin][destination] != UNREACHABLE

    def duration(self, origin: int, destination: int) -> int:
        return self._cell(self.durations, origin, destination, "duration")

    def distance(self, origin: int, destination: int) -> int:
        return self._cell(self.distances, origin, destination, "distance")

    @staticmethod
    def _cell(grid: tuple[tuple[int, ...], ...], origin: int,
              destination: int, what: str) -> int:
        value = grid[origin][destination]
        if value == UNREACHABLE:
            raise UnreachableArc(
                f"no route from {origin} to {destination}; "
                f"{what} is undefined, not large")
        return value


# §6.6's lock kinds, and what each one needs to mean anything. A lock missing
# its subject constrains nothing while looking like an instruction, which is
# the worst of both: the operator believes they have pinned something.
LOCK_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "PIN_ORDER_TO_VEHICLE": ("order_id", "vehicle_id"),
    "FORBID_ORDER_ON_VEHICLE": ("order_id", "vehicle_id"),
    "FIX_ROUTE_PREFIX": ("vehicle_id", "order_ids"),
    "FIX_SEQUENCE": ("vehicle_id", "order_ids"),
    "FORCE_DEPLOY": ("vehicle_id",),
    "FORBID_DEPLOY": ("vehicle_id",),
    "PIN_DEPOT": ("order_id", "depot_id"),
    "FREEZE_UNTIL": ("instant",),
}


@dataclass(frozen=True)
class Lock:
    """An operator instruction the plan must honour exactly. §6.6, FR-21.

    Locks are hard constraints, not preferences. §6.6 is explicit that a lock
    set which makes the instance infeasible must be reported with the minimal
    conflicting subset rather than quietly relaxed -- because a dropped lock is
    a dispatcher's decision being overruled without anyone being told.
    """

    kind: str
    order_id: str | None = None
    vehicle_id: str | None = None
    order_ids: tuple[str, ...] = ()
    depot_id: str | None = None
    instant: int | None = None

    def __post_init__(self) -> None:
        _require(self.kind in LOCK_REQUIREMENTS,
                 f"unknown lock kind {self.kind!r}; "
                 f"§6.6 defines {', '.join(sorted(LOCK_REQUIREMENTS))}")
        for field_name in LOCK_REQUIREMENTS[self.kind]:
            value = getattr(self, field_name)
            _require(bool(value) if field_name != "instant" else value is not None,
                     f"{self.kind} needs {field_name}")
        if self.instant is not None:
            _require_int(self.instant, "instant")


SYNCHRONISATION_KINDS: tuple[str, ...] = ("TRANSFER", "CONVOY")


@dataclass(frozen=True)
class Synchronisation:
    """Two routes meeting at a place and a time. FR-26, §12.2.

    Every other constraint in this model belongs to something: a capacity to a
    vehicle, a window to a stop, an obligation to an order. This one belongs to
    a *pair*, and that is what makes it different in kind -- each route can be
    beyond reproach on its own while the plan is fiction.

    `TRANSFER` is `UC-131`'s two-echelon satellite: "the second-echelon
    departure depends on the first echelon's arrival". The second order may not
    be served until the first has been, plus whatever the handover takes.

    `CONVOY` is `UC-147`: "vehicles must travel in convoy, which is a
    synchronisation constraint forcing several routes to share a path and a
    schedule". The two are served together, within `max_gap` of each other.

    Attributes:
        kind: `TRANSFER` or `CONVOY`.
        first: the order that must happen first, or either of a convoy pair.
        second: the order that follows, or the other half of the convoy.
        min_gap: seconds the second must wait after the first departs. The
            handover itself -- craning a container across, breaking a pallet
            down for cargo bikes.
        max_gap: the longest the coupling tolerates. Required for a convoy,
            where it is how far apart "together" may be; optional for a
            transfer, where it bounds how long goods may sit at the satellite.
    """

    kind: str
    first: str
    second: str
    min_gap: int = 0
    max_gap: int | None = None

    def __post_init__(self) -> None:
        _require(self.kind in SYNCHRONISATION_KINDS,
                 f"unknown synchronisation {self.kind!r}; FR-26 defines "
                 f"{', '.join(SYNCHRONISATION_KINDS)}")
        _require(self.first != self.second,
                 "a synchronisation couples two orders; one order is a sequence")
        _require_int(self.min_gap, "min_gap")
        _require(self.min_gap >= 0, "a handover cannot take negative time")
        if self.max_gap is not None:
            _require_int(self.max_gap, "max_gap")
            _require(self.max_gap >= self.min_gap,
                     "max_gap must not be shorter than the handover it allows")
        _require(self.kind != "CONVOY" or self.max_gap is not None,
                 "a convoy needs a max_gap: 'together' with no bound is not a "
                 "constraint, and zero is a legitimate value for it")


@dataclass(frozen=True)
class Problem:
    id: str
    locations: tuple[Location, ...]
    orders: tuple[Order, ...]
    vehicles: tuple[Vehicle, ...]
    matrix: TravelMatrix
    horizon: TimeWindow | None = None
    locks: tuple[Lock, ...] = ()
    # FR-26: couplings between two routes at a place and time. On the problem
    # rather than on an order, because the constraint is a relation and belongs
    # to neither side of it -- putting it on one order would make the other's
    # copy either redundant or, worse, separately editable.
    synchronisations: tuple[Synchronisation, ...] = ()
    # FR-14: one profile for the instance. §6.3 allows per-arc or per-zone and
    # this is neither -- a refinement worth making when there is data to fit
    # per-arc multipliers from, which is `T-63`. An instance-wide profile is
    # enough to make afternoon travel cost what it costs, and enough for the
    # FIFO property to bind on every arc.
    speed_profile: SpeedProfile | None = None
    # §6.3: "per-arc (or per-zone)" profiles; §12.2 fits per arc class. One
    # profile for a whole instance says congestion slows a motorway exactly as
    # it slows a residential street, which sixteen of the twenty-seven
    # `vrp.bench` fixtures are in a position to contradict -- they span two or
    # three classes each. Keyed by `vrp.timedependent.arc_class_of`, so the
    # storage is O(classes · buckets) and the classification is derived from
    # the matrix rather than stored per arc.
    speed_profiles: Mapping[str, SpeedProfile] | None = None

    def __post_init__(self) -> None:
        _require(bool(self.id), "problem id must not be empty")
        by_id = {location.id: location for location in self.locations}
        _require(len(by_id) == len(self.locations), "duplicate location id")
        indices = {location.matrix_index for location in self.locations}
        _require(len(indices) == len(self.locations), "duplicate matrix_index")
        _require(all(i < self.matrix.size for i in indices),
                 "matrix_index outside the matrix")

        self._require_coherent_profiles()

        order_ids = {order.id for order in self.orders}
        _require(len(order_ids) == len(self.orders), "duplicate order id")
        for order in self.orders:
            for stop in order.stops:
                _require(stop.location_id in by_id,
                         f"order {order.id} references unknown location "
                         f"{stop.location_id!r}")
        for sync in self.synchronisations:
            for side in (sync.first, sync.second):
                _require(side in order_ids,
                         f"synchronisation references unknown order {side!r}")
        for vehicle in self.vehicles:
            for name in ("start_location_id", "end_location_id"):
                location_id = getattr(vehicle, name)
                _require(location_id is None or location_id in by_id,
                         f"vehicle {vehicle.id} references unknown location "
                         f"{location_id!r}")

        # A lock naming something absent is a typo, and a typo that is silently
        # ignored is an operator's instruction disappearing without a word.
        vehicle_ids = {vehicle.id for vehicle in self.vehicles}
        for lock in self.locks:
            for named in (lock.order_id, *lock.order_ids):
                _require(named is None or named in order_ids,
                         f"lock {lock.kind} references unknown order {named!r}")
            _require(lock.vehicle_id is None or lock.vehicle_id in vehicle_ids,
                     f"lock {lock.kind} references unknown vehicle "
                     f"{lock.vehicle_id!r}")


    def _require_coherent_profiles(self) -> None:
        """§6.3's two forms are alternatives, and the per-class one is total.

        Declaring both is two answers to the same question, and whichever the
        lookup happened to prefer would decide it without anybody choosing.

        A mapping that omits a class the matrix contains is refused rather
        than defaulted to free flow. The default is the dangerous version: the
        motorway becomes the one road nobody modelled, and the plan still
        looks fully time-aware to everything downstream. Naming the missing
        class is something an operator can act on -- fit it, or say the
        instance is single-profile and mean it.
        """
        if self.speed_profiles is None:
            return
        _require(self.speed_profile is None,
                 "the instance declares both a single speed_profile and a "
                 "per-class speed_profiles mapping; they are alternatives")
        _require(bool(self.speed_profiles),
                 "speed_profiles is empty; omit it rather than declaring no "
                 "profiles, which reads as free flow but is not stated")
        present = {arc_class_of(self.matrix.duration(origin, destination))
                   for origin in range(self.matrix.size)
                   for destination in range(self.matrix.size)
                   if origin != destination
                   and self.matrix.is_reachable(origin, destination)
                   and self.matrix.duration(origin, destination) > 0}
        missing = sorted(present - set(self.speed_profiles))
        _require(not missing,
                 f"the matrix contains {', '.join(missing)} arcs and "
                 f"speed_profiles has no profile for them; classes present: "
                 f"{sorted(present)}")

    def location(self, location_id: str) -> Location:
        for candidate in self.locations:
            if candidate.id == location_id:
                return candidate
        raise ValidationError(f"unknown location {location_id!r}")

    def order(self, order_id: str) -> Order:
        for candidate in self.orders:
            if candidate.id == order_id:
                return candidate
        raise ValidationError(f"unknown order {order_id!r}")

    def vehicle(self, vehicle_id: str) -> Vehicle:
        for candidate in self.vehicles:
            if candidate.id == vehicle_id:
                return candidate
        raise ValidationError(f"unknown vehicle {vehicle_id!r}")

    # --- serialisation ---------------------------------------------------
    # A problem arrives as JSON, so the model has to survive the round trip.

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Problem:
        return cls(
            id=raw["id"],
            locations=tuple(Location(**location) for location in raw["locations"]),
            orders=tuple(_order_from_dict(order) for order in raw["orders"]),
            vehicles=tuple(_vehicle_from_dict(vehicle) for vehicle in raw["vehicles"]),
            matrix=TravelMatrix(
                version=raw["matrix"]["version"],
                durations=tuple(tuple(row) for row in raw["matrix"]["durations"]),
                distances=tuple(tuple(row) for row in raw["matrix"]["distances"]),
            ),
            horizon=TimeWindow(**raw["horizon"]) if raw.get("horizon") else None,
        )


def _stop_from_dict(raw: dict[str, Any] | None) -> StopSpec | None:
    if raw is None:
        return None
    return StopSpec(
        location_id=raw["location_id"],
        time_windows=tuple(TimeWindow(**w) for w in raw.get("time_windows", ())),
        service_fixed=raw.get("service_fixed", 0),
    )


def _order_from_dict(raw: dict[str, Any]) -> Order:
    return Order(
        id=raw["id"],
        kind=raw["kind"],
        quantities=dict(raw.get("quantities", {})),
        pickup=_stop_from_dict(raw.get("pickup")),
        delivery=_stop_from_dict(raw.get("delivery")),
        priority_tier=raw.get("priority_tier", 0),
        prize=raw.get("prize", 0),
        release_time=raw.get("release_time", 0),
        required_skills=frozenset(raw.get("required_skills", ())),
    )


def _vehicle_from_dict(raw: dict[str, Any]) -> Vehicle:
    return Vehicle(
        id=raw["id"],
        capacities=dict(raw["capacities"]),
        shift=TimeWindow(**raw["shift"]),
        start_location_id=raw["start_location_id"],
        end_location_id=raw.get("end_location_id"),
        max_duration=raw.get("max_duration"),
        max_distance=raw.get("max_distance"),
        skills=frozenset(raw.get("skills", ())),
    )


# --- Solution side (§4.2) -------------------------------------------------


@dataclass(frozen=True)
class Step:
    type: str
    location_id: str
    arrival: int
    start_service: int
    departure: int
    order_id: str | None = None
    load_after: dict[str, int] = field(default_factory=dict)
    # Set on BREAK steps only. §9.3 reports both, and AC-5.1 requires every
    # break to name the rule that compelled it -- a break a compliance officer
    # cannot trace to an article is not evidence of anything.
    rule_ref: str | None = None
    placement: str | None = None
    # FR-20. None on a route that is not electric, so a diesel timeline reads
    # exactly as it always did rather than carrying a column of 1000s that
    # mean nothing.
    soc_after_ppt: int | None = None

    def __post_init__(self) -> None:
        _require(self.type in ("START", "PICKUP", "DELIVERY", "BREAK", "RELOAD",
                              "CHARGE", "END"),
                 f"unknown step type {self.type!r}")
        _require(self.rule_ref is None or self.type == "BREAK",
                 "rule_ref belongs on a BREAK step")
        for name in ("arrival", "start_service", "departure"):
            _require_int(getattr(self, name), name)

    @property
    def waiting(self) -> int:
        return self.start_service - self.arrival


@dataclass(frozen=True)
class Route:
    vehicle_id: str
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class Solution:
    problem_id: str
    routes: tuple[Route, ...]
    unassigned: tuple[dict[str, Any], ...] = ()
    objective_breakdown: dict[str, int] = field(default_factory=dict)
    status: str = "FEASIBLE"
    # NFR-04: "mark the plan `DEGRADED`". Separate from `status` because it is
    # a separate fact -- a plan can be perfectly feasible and costed against
    # arcs nobody measured, and collapsing the two would make an operator
    # choose between hearing that it works and hearing what it is built on.
    degraded: str | None = None
    # CON-4: wall-clock runs are permitted "but MUST record the deterministic
    # iteration count actually achieved so that any run can be replayed". Until
    # this existed the seed and the budget were arguments somebody happened to
    # pass, surviving nowhere -- so a production plan could not be reproduced
    # even in principle. Keys: solver, seed, iterations, matrix_version.
    solver: dict[str, Any] | None = None


def travel_between(problem: Problem, origin: int, destination: int,
                   depart: int) -> int:
    """How long an arc takes, leaving at `depart`. FR-14, §6.3.

    Free flow when the instance declares no speed profile, which is every
    instance that existed before `T-80` and every one whose matrix already
    reflects the traffic it cares about. Where a profile is present the matrix
    supplies free flow and the profile scales it, which is §12.2's construction
    -- multipliers fitted *against* the routing engine's assumptions rather
    than replacing them.

    Shared by the evaluator and the independent verifier, on the same footing
    as `service_time`, which the verifier has always imported from here. CON-1
    forbids the verifier sharing code with a *solver*; a domain primitive both
    of them compute from is the model, not a solver, and duplicating this one
    would give two IGP implementations to keep in step and no way to tell which
    was right.
    """
    free_flow = problem.matrix.duration(origin, destination)
    profile = profile_for_arc(problem, free_flow)
    if profile is None or free_flow <= 0:
        return free_flow
    from vrp.timedependent import travel
    return travel(free_flow, depart, profile)


def profile_for_arc(problem: Problem, free_flow_seconds: int):
    """The speed profile governing an arc, or None when nothing does.

    Args:
        problem: the instance.
        free_flow_seconds: what the matrix says the arc costs, which is both
            the thing the profile scales and the thing that classifies it.

    Returns:
        The per-class profile if the instance declares them, the single
        instance-wide profile if it declares one, otherwise None.

    The class is derived rather than stored. `MTX-9` sizes per-arc profile
    storage at `O(nnz · T)`; per *class* is `O(C · T)` with the classification
    recomputed in constant time from a number the matrix already holds, which
    is what §12.2 can actually fit -- an individual arc is driven too rarely
    for a profile of its own to be estimated from traces.
    """
    if problem.speed_profiles is not None:
        return problem.speed_profiles.get(arc_class_of(free_flow_seconds))
    return problem.speed_profile


def may_enter(vehicle: Vehicle, site: Location) -> bool:
    """Whether this vehicle is allowed at this site at all. FR-11, §6.5.

    Empty `access_classes` means unrestricted, not "admits nothing": the
    inverse reading would make every ordinary address unservable.

    **The independent verifier deliberately does not call this.** CON-1 requires
    it to share no code with any solver, so `vrp/verify/verifier.py` states the
    same rule again in its own words. That duplication is the point: a mistake
    here is caught there precisely because the two were written separately, and
    consolidating them would delete the check rather than tidy it.
    """
    if site.access_classes and vehicle.access_class not in site.access_classes:
        return False
    return not (site.max_vehicle_kg is not None
                and vehicle.gross_weight_kg is not None
                and vehicle.gross_weight_kg > site.max_vehicle_kg)


def has_skills_for(vehicle: Vehicle, order: Order) -> bool:
    """Whether this vehicle carries the qualifications the order needs. FR-10."""
    return order.required_skills <= vehicle.skills


def service_time(order: Order, vehicle: Vehicle, location: Location) -> int:
    """How long this vehicle takes to serve this order here. FR-05, §6.2.

        (service_fixed + service_per_unit x quantity) x factor + dwell_overhead

    The composition order is a decision, not an accident. The vehicle factor
    scales the *handling* -- the part a tail lift or a second crew member
    actually changes -- while the dwell overhead is added afterwards, because
    §6.2 defines it as "parking/walking overhead independent of the order" and
    a tail lift does not move the parking space closer.

    Integer throughout (CON-4). The factor is parts per thousand and the
    division truncates, which is deterministic and therefore replayable; a
    float here is how two machines disagree about when a driver left.
    """
    stop = order.delivery or order.pickup
    quantity = 0
    if stop.service_per_unit:
        dimension = stop.service_per_unit_dimension
        quantity = (order.quantities.get(dimension, 0) if dimension
                    else sum(order.quantities.values()))
    handling = stop.service_fixed + stop.service_per_unit * quantity
    return handling * vehicle.service_factor_ppt // 1000 + location.dwell_overhead
