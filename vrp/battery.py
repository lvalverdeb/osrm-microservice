"""Battery arithmetic — FR-20, T-41.

`FR-20`: "Support EV range and en-route recharging with charging-time
functions." A `COULD`, and the only requirement in the backlog with no data
source in this stack: nobody here has charger locations or a manufacturer's
charging curve.

**The curve is the requirement.** A battery that charged at a constant rate
would make `charge_seconds` a division and the whole feature decorative. Real
cells take current fast while they are empty and taper near the top, which is
why a driver charges to eighty percent and drives on rather than waiting for a
hundred. A model without the taper cannot prefer the shorter stop, so it plans
the wrong one and is confidently wrong about when the van gets back.

The walk is the same shape as `vrp.timedependent.travel`: step to the next
band boundary, charge at the rate in force, carry on. Both are piecewise-constant
rates over a quantity that advances, and both stay in integers because CON-4
forbids accumulating floats.

**Rounding goes against the plan in both directions.** Charging rounds up and
consumption rounds up, so the model is pessimistic about time on the plug and
pessimistic about energy off it. A van that arrives with more charge than
promised is a good surprise; the other kind is a recovery.

This module knows nothing about the domain -- no `Problem`, no `Vehicle` --
for the same reason `vrp.timedependent` does not: `vrp.model` imports it, so
the dependency has to run one way.
"""

from __future__ import annotations

from dataclasses import dataclass

# State of charge in parts per thousand of usable capacity, like every other
# ratio in this codebase. CON-4 forbids the float.
FULL_PPT = 1000

SECONDS_PER_HOUR = 3600
METRES_PER_KM = 1000


@dataclass(frozen=True)
class ChargingCurve:
    """Piecewise-constant charging power over state-of-charge bands.

    Attributes:
        bands: `(soc_ceiling_ppt, power_w)` in ascending order of ceiling. The
            last ceiling must be `FULL_PPT`: a curve that stops describing the
            battery before it is full cannot answer the question it exists for.
    """

    bands: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        if not self.bands:
            raise ValueError("a charging curve needs at least one band")
        ceilings = [ceiling for ceiling, _ in self.bands]
        if ceilings != sorted(ceilings) or len(set(ceilings)) != len(ceilings):
            raise ValueError("charging bands must ascend by state of charge")
        if ceilings[-1] != FULL_PPT:
            raise ValueError(
                f"the last band must reach {FULL_PPT} (a full battery); this "
                f"curve stops at {ceilings[-1]}")
        if any(power <= 0 for _, power in self.bands):
            raise ValueError("charging power must be positive; a zero-power "
                             "band is a battery that never fills")

    def power_at(self, soc_ppt: int) -> int:
        """The rate in force at a state of charge."""
        for ceiling, power in self.bands:
            if soc_ppt < ceiling:
                return power
        return self.bands[-1][1]


@dataclass(frozen=True)
class ChargeStop:
    """A visit to a charger, and how full to leave.

    Named rather than a bare tuple because the target is a decision -- a plan
    that always charges to full spends the taper it could have skipped -- and
    an unlabelled integer in a signature invites reading it as the arrival
    charge instead.
    """

    location_id: str
    to_soc_ppt: int = FULL_PPT


def energy_wh(battery_wh: int, soc_ppt: int) -> int:
    """What `soc_ppt` of a battery holds, in watt-hours."""
    return battery_wh * soc_ppt // FULL_PPT


def consumed_ppt(battery_wh: int, wh_per_km: int, metres: int) -> int:
    """How much charge a distance costs, in parts per thousand.

    Args:
        battery_wh: usable capacity.
        wh_per_km: consumption. One number, deliberately: real consumption
            depends on load, gradient and temperature, and none of the three is
            in the domain. Inventing a gradient from coordinates would put an
            unauditable number in a plan a dispatcher has to defend.
        metres: how far.

    Returns:
        The drop in state of charge, rounded up.

    Raises:
        ValueError: on a non-positive battery, which is not an electric
            vehicle with an empty tank but a division by zero.
    """
    if battery_wh <= 0:
        raise ValueError("a battery must have positive capacity")
    if metres <= 0:
        return 0
    used_wh = -(-wh_per_km * metres // METRES_PER_KM)
    return -(-used_wh * FULL_PPT // battery_wh)


def charge_seconds(battery_wh: int, curve: ChargingCurve,
                   from_ppt: int, to_ppt: int) -> int:
    """How long a charge takes. The band walk.

    Args:
        battery_wh: usable capacity.
        curve: the charging function.
        from_ppt: state of charge on arrival.
        to_ppt: state of charge on leaving.

    Returns:
        Seconds on the plug, rounded up.

    Raises:
        ValueError: if `to_ppt` is below `from_ppt`. Discharging into the grid
            is a different feature and this is more likely a swapped argument.
    """
    if to_ppt < from_ppt:
        raise ValueError(
            f"cannot charge from {from_ppt} to {to_ppt}: the target is below "
            "the arrival charge")
    if battery_wh <= 0:
        raise ValueError("a battery must have positive capacity")

    seconds = 0
    soc = max(from_ppt, 0)
    while soc < to_ppt:
        power = curve.power_at(soc)
        ceiling = min(to_ppt, _next_ceiling(curve, soc))
        # Energy for this slice, then time at the rate in force. Both ceiling
        # divisions: a partial second still has to be spent plugged in.
        slice_wh = -(-battery_wh * (ceiling - soc) // FULL_PPT)
        seconds += -(-slice_wh * SECONDS_PER_HOUR // power)
        soc = ceiling
    return seconds


def _next_ceiling(curve: ChargingCurve, soc_ppt: int) -> int:
    for ceiling, _ in curve.bands:
        if soc_ppt < ceiling:
            return ceiling
    return FULL_PPT
