"""Phase 1 trust rules (see docs/phase-1.md, section 4).

Kept as pure functions so the scheme can change without touching the ledger code.
"""

from __future__ import annotations

from provenance.models import RunOutcome

INITIAL_TRUST = 0.5
VERIFIED_SUCCESS_TRUST = 0.9
FAILURE_TRUST = 0.1


def trust_for_outcome(outcome: RunOutcome, verified: bool) -> float:
    """Trust score a finished run (and everything it produced) should carry.

    Args:
        outcome: The run's final outcome.
        verified: Whether a task checker confirmed success. The agent's own
            claim of success never counts as verification.

    Returns:
        A score in [0, 1].

    """
    if outcome is RunOutcome.SUCCESS and verified:
        return VERIFIED_SUCCESS_TRUST
    if outcome is RunOutcome.RUNNING:
        return INITIAL_TRUST
    if outcome is RunOutcome.SUCCESS:  # claimed but unverified: no gain, no penalty
        return INITIAL_TRUST
    return FAILURE_TRUST
