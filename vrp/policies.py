"""Baseline dispatch policies — §8.2 step 1, T-52.

§8.2 names three and says why they exist: "*Greedy* -- dispatch everything known
now. *Lazy* -- dispatch only must-go requests. *Random* -- dispatch must-go plus
each other request with probability p. These are the competition-standard
baselines and MUST be retained permanently as the denominator for every policy
claim."

*Denominator* is the load-bearing word. A dispatch policy that beats nothing in
particular has not been shown to be any good, and §8.2 orders the whole slice
around that: baselines first, then prize-collecting (T-55), then ICD (T-54),
each measured against these. So `BASELINES` is a registry rather than three
loose functions -- T-53's replayer enumerates it, and a baseline that quietly
stopped being compared would take every claim made against it with it.

**These are allowed to be bad, and two of them are.** Lazy postpones everything
it legally can, which is dreadful consolidation and exactly the point: it is the
floor. Greedy is the ceiling on service and the floor on consolidation. Random
spans them, and at `p = 0` and `p = 1000` it *is* the other two -- a useful check
that `p` means what it says rather than merely varying something.

**None of them can cause a service failure.** AC-3.1 is enforced in
`vrp.epochs.decide`, not trusted to the policy, which is what makes it safe to
ship a baseline that is literally random.

Placement: **Python**, per criterion 2. These are policies over the domain
model, consumed by the epoch controller.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from vrp.epochs import Classification, Epoch, Policy

# Probability in parts per thousand, per CON-4: this project does not
# accumulate floats, and a comparison against a baseline has to reproduce
# exactly or it is a comparison against a different number.
FULL = 1000


def greedy(open_ids: Sequence[str], split: Classification,
           epoch: Epoch) -> Sequence[str]:
    """Dispatch everything known now. §8.2's ceiling on service.

    Ignores the epoch, and that is the point of a baseline: it has no view
    about how much day is left.
    """
    return tuple(open_ids)


def lazy(open_ids: Sequence[str], split: Classification,
         epoch: Epoch) -> Sequence[str]:
    """Dispatch only what must go. §8.2's floor on service.

    It looks bad because it is meant to: a baseline that behaved sensibly would
    flatter every policy measured against it.
    """
    return split.must_go


def random_policy(probability: int, seed: int) -> Policy:
    """Dispatch must-go work plus each other request with probability `p`.

    Args:
        probability: parts per thousand, 0 to 1000 inclusive.
        seed: same seed, same draw. A baseline nobody can reproduce is not a
            denominator -- every comparison against it would be against a
            different number.

    Returns:
        A policy closing over its own generator. The generator is created once
        and advances across calls, so `p` is a rate per epoch rather than a
        single coin toss -- which is what §8.2's "each other request with
        probability p" means, once the policy is asked more than once.

        A first version re-seeded on every call. The same open set then drew
        the same set forever: at p = 0.5 over four requests, two of them were
        never dispatched until must-go forced them, and an empty first draw
        would have dispatched nothing at all. A baseline that gets stuck is not
        a denominator. Reproducibility is preserved by building the policy
        fresh per run, which is what a replayer does anyway.

    Raises:
        ValueError: if `probability` is outside the scale. Silently clamping it
            would redefine the baseline without saying so.
    """
    if not 0 <= probability <= FULL:
        raise ValueError(f"probability must be 0..{FULL} parts per thousand, "
                         f"got {probability}")
    rng = random.Random(seed)

    def policy(open_ids: Sequence[str], split: Classification,
               epoch: Epoch) -> Sequence[str]:
        chosen = list(split.must_go)
        for order_id in open_ids:
            if order_id in split.must_go:
                continue
            if rng.randrange(FULL) < probability:
                chosen.append(order_id)
        return tuple(chosen)

    return policy


# §8.2: "MUST be retained permanently as the denominator for every policy
# claim." A registry rather than three names a caller has to remember, so
# T-53's replayer iterates one thing and a baseline cannot fall out of the
# comparison by being forgotten.
BASELINES: dict[str, Policy] = {
    "greedy": greedy,
    "lazy": lazy,
    "random": random_policy(probability=FULL // 2, seed=0),
}
