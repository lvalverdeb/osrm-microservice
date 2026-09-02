"""Speed-profile calibration — §12.2, T-63.

§12.2: "Fit per-arc-class, per-bucket speed multipliers from observed GPS
traces against the routing engine's free-flow assumptions. Maintain FIFO by
construction (§6.3). Re-fit weekly; hold out one week for validation."

The fit is grouped medians, for the reasons §12.1's sibling pipeline gives at
length and which apply unchanged here: a median over a named group is a number
somebody can go and count, and a driver stuck behind an accident produces an
arc that is genuinely slow and must not drag an hour's multiplier with it.

**Against the engine's assumptions, not against distance.** The multiplier is
the ratio of what the matrix promised to what the road delivered, so the output
is a correction to a specific engine's specific guess. Re-fitting after the
engine's map changes is therefore mandatory rather than housekeeping.

**An arc that crossed a bucket boundary attests to neither bucket.** It was
driven at two speeds and reports one duration; attributing it to the bucket it
departed in produces a plausible number that is wrong, and nothing downstream
could tell it from a real one. Those traversals are excluded and counted, which
is the same choice §12.1 makes for a stop whose departure was never recorded.
Recovering them would mean inverting the Ichoua-Gendreau-Potvin construction
across coupled buckets -- a regression, and §12.1's instruction to reach for
grouped medians first is about exactly that temptation.

**FIFO is inherited, not re-established.** The output is a `SpeedProfile`, and
§6.3's no-passing property holds for any positive multiplier because
`vrp.timedependent` buckets speed rather than travel time. The one thing the
fit owes that guarantee is never emitting a zero: a van stalled for an hour on
a four-minute arc implies a multiplier that rounds to nothing, which is an
unreachable road (MTX-5) rather than slow traffic. It is floored at `SLOWEST_PPT`
and left visible, because a number an operator can see is absurd is worth more
than a pipeline that raises at 03:00 on a Sunday.

**§12.2 fits per arc class; the model carries one profile per instance.**
`Problem.speed_profile` is a single `SpeedProfile`, while §6.3 specifies
per-arc (or per-zone) profiles and `MTX-9` sizes their storage at `O(nnz · T)`.
So a per-class fit has nowhere to be applied wholesale. The classes are kept
apart and the caller names the one it wants: collapsing them would produce a
profile describing no road in particular, and that is the failure this
pipeline exists to prevent rather than commit. Closing the gap is `T-83`.

Placement: **Python**, per criterion 2. It reads executed routes and the domain
model, and it changes whenever either does.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from vrp.adherence import ExecutedRoute
from vrp.model import Problem
from vrp.timedependent import PPT, SpeedProfile, travel

FREE_FLOW_PPT = PPT

# The slowest a road is allowed to be fitted at: one part per thousand of free
# flow, an arc a thousand times its estimate. Below this `SpeedProfile` refuses
# the value outright, so this is the last multiplier that still describes a
# road rather than a wall.
SLOWEST_PPT = 1

# Arc classes by free-flow duration, in the order they are tested. §12.2 says
# per-arc-class without naming the classes; duration bands are the classification
# the matrix already supports, and a dispatcher can check which band an arc is
# in by reading one number. Road category would be better and is not in the
# domain: inventing it from coordinates would put an unauditable label in an
# auditable pipeline, which is the objection §12.1 raises to driver experience.
ARC_CLASSES = ((300, "local"), (1_200, "arterial"))


def arc_class_of(free_flow_seconds: int) -> str:
    """Which class an arc belongs to, by what the engine thinks it costs."""
    for ceiling, name in ARC_CLASSES:
        if free_flow_seconds <= ceiling:
            return name
    return "trunk"


@dataclass(frozen=True)
class ArcKey:
    """§12.2's grouping key: a class of road at an hour of the day."""

    arc_class: str
    bucket: int


@dataclass(frozen=True)
class Traversal:
    """One arc between two stops, as telematics saw it."""

    origin_id: str
    destination_id: str
    key: ArcKey
    departed: int
    free_flow_seconds: int
    observed_seconds: int

    @property
    def multiplier_ppt(self) -> int:
        """What speed the road ran at, in parts per thousand of free flow.

        Floored rather than allowed to reach zero: see the module docstring.
        """
        return max(SLOWEST_PPT,
                   self.free_flow_seconds * PPT // self.observed_seconds)


@dataclass(frozen=True)
class Traversals:
    """What a batch of executed routes yielded, and what it did not.

    Attributes:
        kept: traversals that stayed inside one bucket.
        straddled: how many crossed a boundary and were excluded.
        unrecorded: how many lacked an arrival or a departure at one end.
    """

    kept: tuple[Traversal, ...]
    straddled: int
    unrecorded: int


@dataclass(frozen=True)
class SpeedCalibration:
    """One week's fit, per arc class and bucket."""

    bucket_seconds: int
    buckets: int
    by_key: dict[ArcKey, int]
    thin: dict[ArcKey, int]
    straddled: int

    @property
    def classes(self) -> tuple[str, ...]:
        return tuple(sorted({key.arc_class for key in self.by_key}))

    def profile(self, arc_class: str) -> SpeedProfile:
        """The fitted profile for one class of road.

        Args:
            arc_class: which class, from `classes`.

        Returns:
            A `SpeedProfile` whose unfitted buckets are free flow.

        Raises:
            KeyError: if nothing was fitted for that class. A profile of
                twenty-four free-flow buckets is not an answer to "what is the
                traffic on the motorway": it is the absence of one, and
                returning it would be indistinguishable from success.
        """
        if arc_class not in self.classes:
            raise KeyError(
                f"nothing was fitted for arc class {arc_class!r}; "
                f"classes with observations: {self.classes or '(none)'}")
        return SpeedProfile(
            bucket_seconds=self.bucket_seconds,
            multipliers_ppt=tuple(
                self.by_key.get(ArcKey(arc_class, bucket), FREE_FLOW_PPT)
                for bucket in range(self.buckets)))

    def defaulted(self, arc_class: str) -> tuple[int, ...]:
        """Which buckets fell back to free flow because nothing was seen."""
        return tuple(bucket for bucket in range(self.buckets)
                     if ArcKey(arc_class, bucket) not in self.by_key)


@dataclass(frozen=True)
class Validation:
    """A held-out week, predicted against what the road actually did."""

    arcs: int
    median_error_seconds: int
    worst_error_seconds: int
    unfitted_arcs: int


def traversals(problem: Problem, executed: Sequence[ExecutedRoute],
               bucket_seconds: int) -> Traversals:
    """Arcs between consecutive stops, as telematics recorded them.

    Args:
        problem: the instance, for its free-flow matrix.
        executed: what the vehicles did.
        bucket_seconds: the bucket width the profile will use.

    Returns:
        The usable traversals, with counts of what was dropped and why.

    Raises:
        ValueError: on an arrival that precedes the departure before it. That
            is a clock fault, and a negative duration would invert a multiplier.

    The leg out of the depot is not among them: `ExecutedRoute` keys its times
    by order, so the moment a vehicle left the yard is not recorded. That is a
    limit of §12.4's shape rather than a choice here, and it costs one arc per
    route out of many.
    """
    kept: list[Traversal] = []
    straddled = unrecorded = 0
    for route in executed:
        for origin_id, destination_id in zip(route.sequence,
                                             route.sequence[1:]):
            departed = route.departures.get(origin_id)
            arrived = route.arrivals.get(destination_id)
            if departed is None or arrived is None:
                unrecorded += 1
                continue
            if arrived < departed:
                raise ValueError(
                    f"{route.vehicle_id} arrived at {destination_id} "
                    f"({arrived}) before leaving {origin_id} ({departed})")
            observed = arrived - departed
            if observed <= 0 or departed % bucket_seconds + observed > bucket_seconds:
                straddled += 1
                continue
            free_flow = _free_flow(problem, origin_id, destination_id)
            if free_flow <= 0:
                unrecorded += 1
                continue
            kept.append(Traversal(
                origin_id=origin_id, destination_id=destination_id,
                key=ArcKey(arc_class_of(free_flow),
                           departed % (bucket_seconds * 24) // bucket_seconds),
                departed=departed, free_flow_seconds=free_flow,
                observed_seconds=observed))
    return Traversals(kept=tuple(kept), straddled=straddled,
                      unrecorded=unrecorded)


def _free_flow(problem: Problem, origin_id: str, destination_id: str) -> int:
    index = {location.id: location.matrix_index
             for location in problem.locations}
    origin = problem.order(origin_id).delivery or problem.order(origin_id).pickup
    target = (problem.order(destination_id).delivery
              or problem.order(destination_id).pickup)
    return problem.matrix.duration(index[origin.location_id],
                                   index[target.location_id])


def fit(seen: Traversals, minimum: int, bucket_seconds: int,
        buckets: int) -> SpeedCalibration:
    """Grouped medians of observed speed, per arc class and bucket.

    Args:
        seen: traversals from `traversals`.
        minimum: how many traversals a group needs before it is fitted.
        bucket_seconds: bucket width.
        buckets: how many buckets a day has.

    Returns:
        The fit, with thin groups reported separately and the straddle count
        carried through so a report can say how much of the week was usable.
    """
    grouped: dict[ArcKey, list[int]] = {}
    for traversal in seen.kept:
        grouped.setdefault(traversal.key, []).append(traversal.multiplier_ppt)

    fitted, thin = {}, {}
    for key, multipliers in grouped.items():
        if len(multipliers) < minimum:
            thin[key] = len(multipliers)
            continue
        fitted[key] = _median(multipliers)
    return SpeedCalibration(bucket_seconds=bucket_seconds, buckets=buckets,
                            by_key=fitted, thin=thin, straddled=seen.straddled)


def _median(values: Sequence[int]) -> int:
    """The lower of the two middle values on an even count.

    The same choice `vrp.calibrate` makes and for the same reason: CON-4
    forbids the float, and the lower middle is deterministic and replayable.
    """
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def validate(calibration: SpeedCalibration, held_out: Traversals) -> Validation:
    """Predict the held-out week with the fit, and report the error. §12.2.

    Args:
        calibration: the fit, trained on earlier weeks.
        held_out: traversals from the week that was not trained on.

    Returns:
        Median and worst absolute error in seconds, and how many arcs the fit
        had nothing to say about.

    An arc whose class was never fitted is counted rather than predicted at
    free flow. Scoring it against the engine's own guess would make a pipeline
    that fitted nothing look accurate on exactly the roads it knows least.
    """
    errors: list[int] = []
    unfitted = 0
    for traversal in held_out.kept:
        multiplier = calibration.by_key.get(traversal.key)
        if multiplier is None:
            unfitted += 1
            continue
        predicted = travel(
            traversal.free_flow_seconds, traversal.departed,
            SpeedProfile(bucket_seconds=calibration.bucket_seconds,
                         multipliers_ppt=(multiplier,)))
        errors.append(abs(predicted - traversal.observed_seconds))
    return Validation(
        arcs=len(errors),
        median_error_seconds=_median(errors) if errors else 0,
        worst_error_seconds=max(errors) if errors else 0,
        unfitted_arcs=unfitted)


def recalibrate(problem: Problem, weeks: Sequence[Sequence[ExecutedRoute]],
                minimum: int, bucket_seconds: int,
                buckets: int) -> tuple[SpeedCalibration, Validation]:
    """The weekly job: fit on every week but the last, validate on it. §12.2.

    Args:
        problem: the instance the routes were planned against.
        weeks: executed routes, oldest first. The last is held out.
        minimum: how many traversals a group needs before it is fitted.
        bucket_seconds: bucket width.
        buckets: how many buckets a day has.

    Returns:
        The fit and its held-out validation report.

    Raises:
        ValueError: with fewer than two weeks. A single week can be fitted or
            it can be validated and it cannot be both, and reporting the error
            of a fit against its own training data is the specific thing
            holding a week out exists to prevent.
    """
    if len(weeks) < 2:
        raise ValueError(
            f"a weekly re-fit needs at least two weeks so one can be held "
            f"out for validation; got {len(weeks)}")
    training = [route for week in weeks[:-1] for route in week]
    calibration = fit(traversals(problem, training, bucket_seconds),
                      minimum=minimum, bucket_seconds=bucket_seconds,
                      buckets=buckets)
    report = validate(calibration,
                      traversals(problem, weeks[-1], bucket_seconds))
    return calibration, report
