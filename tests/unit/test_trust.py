"""Trust rules are pure functions; no database needed."""

from __future__ import annotations

import pytest

from provenance.models import RunOutcome
from provenance.trust import (
    FAILURE_TRUST,
    INITIAL_TRUST,
    VERIFIED_SUCCESS_TRUST,
    trust_for_outcome,
)


@pytest.mark.parametrize(
    ("outcome", "verified", "expected"),
    [
        (RunOutcome.RUNNING, False, INITIAL_TRUST),
        (RunOutcome.SUCCESS, True, VERIFIED_SUCCESS_TRUST),
        (RunOutcome.SUCCESS, False, INITIAL_TRUST),  # agent's claim alone earns nothing
        (RunOutcome.FAILURE, False, FAILURE_TRUST),
        (RunOutcome.ABORTED, False, FAILURE_TRUST),
        (RunOutcome.BLOCKED, False, FAILURE_TRUST),
    ],
)
def test_trust_for_outcome(outcome: RunOutcome, verified: bool, expected: float) -> None:
    assert trust_for_outcome(outcome, verified) == expected


def test_scores_are_ordered_and_in_range() -> None:
    assert 0 <= FAILURE_TRUST < INITIAL_TRUST < VERIFIED_SUCCESS_TRUST <= 1
