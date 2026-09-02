"""Immutable snapshots, and plans replayable from them — NFR-08, CON-4, T-89.

`NFR-08`: "Input snapshot, solver configuration, and output plan are immutable
and retained for the regulatory retention period; a plan is replayable from its
snapshot."

**What this delivers and what it does not.** The snapshot and the replay are
here. Where the bytes live for a retention period is §10.1's object store and a
deployment decision; this module writes a file and reads it back. Retention is
worth nothing unless the thing retained is *sufficient* to re-derive the plan
and *tamper-evident*, and those are the two properties a library can own.

**Sufficiency was the hard part, and it was missing.** `Problem.to_dict` /
`from_dict` are the model's round trip, and `from_dict` reconstructed nine of
Vehicle's twenty-eight fields, four of Order's thirteen, three of StopSpec's
five, and none of the problem's locks, synchronisations or speed profiles --
thirty fields in all, dropped in silence. A snapshot built on that codec would
have replayed a problem with no vehicle costs, no hours-of-service rules, no
site access and no incompatibilities: cheaper to serve, legal in more ways, and
reported as a faithful replay. That is the precise audit failure NFR-08 exists
to prevent, so `T-89` fixed the codec before building anything on it.

**Tamper-evidence is a digest over the canonical bytes**, not a checksum
somebody remembers to update. Content addressing means a snapshot cannot be
edited and still be itself: any change to the problem or the configuration
changes the digest, and `read` refuses rather than replaying something that is
no longer what was planned.

**Canonical means byte-stable across processes.** Sorted keys, sorted sets, no
insignificant whitespace. `to_dict` yields frozensets and tuples, which JSON has
no spelling for; a snapshot that cannot be written as bytes cannot be retained,
so those are normalised on the way out and rebuilt on the way in.

Placement: **Python**, per criterion 2. It serialises the domain model and
changes whenever that does -- which is the whole point of the guard test that
comes with it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vrp.model import Problem

FORMAT = 1


class SnapshotTampered(Exception):
    """A snapshot's contents no longer match the digest recorded with them."""


@dataclass(frozen=True)
class Snapshot:
    """A problem and the configuration it was solved with, sealed together.

    Attributes:
        payload: the JSON-ready record -- `problem`, `config`, `format`.
        digest: sha256 over the canonical encoding of `payload`.
    """

    payload: dict[str, Any]
    digest: str

    def problem(self) -> Problem:
        """The instance, rebuilt from the record rather than from memory."""
        return Problem.from_dict(self.payload["problem"])

    @property
    def config(self) -> dict[str, Any]:
        return self.payload["config"]


def canonical(payload: Any) -> str:
    """The one encoding a digest may be taken over.

    Sorted keys and no insignificant whitespace, so two processes that agree
    about the content agree about the bytes. CON-4 asks for replayability, and
    a digest that moved with dictionary ordering would make every snapshot
    non-reproducible for a reason that has nothing to do with the plan.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _jsonable(value: Any) -> Any:
    """Normalise `to_dict`'s output into something JSON can hold.

    Sets become sorted lists: JSON has no set, and an arbitrary order would
    make the digest depend on a hash seed rather than on the contents.
    """
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def capture(problem: Problem, config: dict[str, Any]) -> Snapshot:
    """Seal a problem and the configuration it is about to be solved with.

    Args:
        problem: the instance, exactly as the solver will receive it.
        config: solver, seed, iteration budget, matrix version -- whatever
            `Solution.solver` records. Sealed alongside the problem because a
            plan is only reproducible from both: the same instance at a
            different seed is a different plan and an honest audit says so.

    Returns:
        The snapshot, with its digest.
    """
    payload = {
        "format": FORMAT,
        "problem": _jsonable(problem.to_dict()),
        "config": _jsonable(config),
    }
    return Snapshot(payload=payload,
                    digest=hashlib.sha256(
                        canonical(payload).encode("utf-8")).hexdigest())


def write(snapshot: Snapshot, path: Path | str) -> Path:
    """Write a snapshot to disk, digest and all.

    Args:
        snapshot: what to retain.
        path: where. §10.1 wants an object store and this writes a file; the
            format is the part that has to be right either way.

    Returns:
        The path written.
    """
    path = Path(path)
    path.write_text(canonical({"digest": snapshot.digest,
                               "payload": snapshot.payload}),
                    encoding="utf-8")
    return path


def read(path: Path | str) -> Snapshot:
    """Read a snapshot back, refusing one that has been edited.

    Args:
        path: what to read.

    Returns:
        The snapshot.

    Raises:
        SnapshotTampered: if the contents no longer hash to the recorded
            digest. Refused rather than repaired: a record that does not match
            its own digest is not a record of anything, and replaying it would
            produce a plan attributed to inputs nobody can show were used.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    payload = raw["payload"]
    found = hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()
    if found != raw["digest"]:
        raise SnapshotTampered(
            f"{path} does not match its digest: recorded {raw['digest'][:12]}, "
            f"contents hash to {found[:12]}. The snapshot has been edited "
            "since it was written, so nothing derived from it can be "
            "attributed to the plan it claims to describe")
    return Snapshot(payload=payload, digest=raw["digest"])


def replay(snapshot: Snapshot,
           solve: Callable[[Problem, dict[str, Any]], Any]) -> Any:
    """Re-derive a plan from a snapshot alone. NFR-08's sentence.

    Args:
        snapshot: the sealed record.
        solve: what to run, given the rebuilt problem and the sealed config.

    Returns:
        Whatever `solve` returns.

    The problem is rebuilt from the payload rather than passed in, which is the
    only version of this that proves anything: replaying against the objects
    that happen to be in memory tests the solver, not the record.
    """
    return solve(snapshot.problem(), snapshot.config)
