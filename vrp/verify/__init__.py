"""Independent verification — SDD §11.2.

A separate package on purpose: the verifier must share no code with any solver
or with the canonical evaluator. See `verifier.py` for why that boundary is the
whole point, and `tests/vrp/test_independent_verifier.py` for the test that
keeps it honest.
"""

from vrp.verify.verifier import NOT_APPLICABLE, Report, Violation, verify

__all__ = ["NOT_APPLICABLE", "Report", "Violation", "verify"]
