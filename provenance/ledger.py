"""Provenance ledger repository: the only way runs, steps and evidence get written.

Every write creates the typed row, its ``ledger_items`` row and the edge to its
parent in one call, so nothing can be recorded without provenance.

Transactions belong to the caller: methods ``flush()`` but never ``commit()``.
The agent loop commits after each step so a crash keeps the partial run.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from provenance.models import (
    Evidence,
    EvidenceKind,
    ItemStatus,
    ItemType,
    LedgerEdge,
    LedgerItem,
    OriginType,
    Relation,
    Run,
    RunOutcome,
    Step,
    StepResult,
    utcnow,
)
from provenance.trust import INITIAL_TRUST, trust_for_outcome


class LedgerError(Exception):
    """Raised when a write would break ledger invariants."""


@dataclass(frozen=True)
class RetractionResult:
    """Outcome of :meth:`Ledger.retract`."""

    retracted: uuid.UUID
    flagged: list[uuid.UUID]


def sha256_file(path: Path) -> tuple[str, int]:
    """Return the SHA-256 hex digest and size of a file, read in chunks."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class Ledger:
    """Repository over one :class:`AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to a session; the caller owns commit/rollback."""
        self.session = session

    # ------------------------------------------------------------------ writes
    async def start_run(
        self,
        *,
        goal: str,
        start_url: str,
        channel: str,
        model: str,
        task_id: str | None = None,
        origin: OriginType = OriginType.AGENT_RUN,
    ) -> Run:
        """Create a run and its ledger item. The run's ``source_run_id`` is itself."""
        item_id = uuid.uuid4()
        item = LedgerItem(
            id=item_id,
            item_type=ItemType.RUN,
            origin_type=origin,
            source_run_id=item_id,
            url=start_url,
            trust_score=INITIAL_TRUST,
            status=ItemStatus.ACTIVE,
        )
        run = Run(
            ledger_item_id=item_id,
            item=item,
            goal=goal,
            start_url=start_url,
            channel=channel,
            model=model,
            task_id=task_id,
            outcome=RunOutcome.RUNNING,
            verified=False,
            step_count=0,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            started_at=utcnow(),
        )
        self.session.add_all([item, run])
        await self.session.flush()
        return run

    async def record_step(
        self,
        run: Run,
        *,
        action: dict[str, Any],
        result: StepResult,
        url: str | None,
        observation_ref: str | None = None,
        error: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int = 0,
    ) -> Step:
        """Append the next step to a running run, with ledger item and edge.

        Raises:
            LedgerError: If the run has already finished or is not active.

        """
        self._require_open(run)
        item = LedgerItem(
            id=uuid.uuid4(),
            item_type=ItemType.STEP,
            origin_type=run.item.origin_type,
            source_run_id=run.ledger_item_id,
            url=url,
            evidence_ref=observation_ref,
            trust_score=run.item.trust_score,
            status=ItemStatus.ACTIVE,
        )
        step = Step(
            ledger_item_id=item.id,
            item=item,
            run_id=run.ledger_item_id,
            idx=run.step_count,
            observation_ref=observation_ref,
            action=action,
            result=result,
            error=error,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
        edge = LedgerEdge(
            parent_id=run.ledger_item_id, child_id=item.id, relation=Relation.PRODUCED
        )
        run.step_count += 1
        run.tokens_in += tokens_in
        run.tokens_out += tokens_out
        await self._add_with_edge([item, step], edge)
        return step

    async def record_evidence(self, step: Step, *, kind: EvidenceKind, path: Path) -> Evidence:
        """Hash a file already written to disk and attach it to a step.

        Raises:
            FileNotFoundError: If ``path`` does not exist.

        """
        sha, size = sha256_file(path)
        item = LedgerItem(
            id=uuid.uuid4(),
            item_type=ItemType.EVIDENCE,
            origin_type=step.item.origin_type,
            source_run_id=step.item.source_run_id,
            url=step.item.url,
            evidence_ref=str(path),
            trust_score=step.item.trust_score,
            status=ItemStatus.ACTIVE,
        )
        evidence = Evidence(
            ledger_item_id=item.id, item=item, kind=kind, path=str(path), sha256=sha, bytes=size
        )
        edge = LedgerEdge(
            parent_id=step.ledger_item_id, child_id=item.id, relation=Relation.EVIDENCE_FOR
        )
        await self._add_with_edge([item, evidence], edge)
        return evidence

    async def finish_run(
        self,
        run: Run,
        *,
        outcome: RunOutcome,
        verified: bool,
        agent_answer: str | None = None,
        cost_usd: float = 0.0,
        end_reason: str | None = None,
    ) -> Run:
        """Close a run and set trust on it and everything it produced.

        ``verified`` must come from a task checker, never from the agent.

        Raises:
            LedgerError: If the run is already finished, or ``verified`` is set on
                a non-successful outcome.

        """
        self._require_open(run)
        if outcome is RunOutcome.RUNNING:
            raise LedgerError("finish_run needs a final outcome")
        if verified and outcome is not RunOutcome.SUCCESS:
            raise LedgerError("only a successful run can be verified")
        run.outcome = outcome
        run.verified = verified
        run.agent_answer = agent_answer
        run.end_reason = end_reason
        run.cost_usd = cost_usd
        run.ended_at = utcnow()
        score = trust_for_outcome(outcome, verified)
        await self.session.execute(
            update(LedgerItem)
            .where(LedgerItem.source_run_id == run.ledger_item_id)
            .values(trust_score=score)
        )
        await self.session.flush()
        await self.session.refresh(run.item)
        return run

    async def retract(self, item_id: uuid.UUID, reason: str) -> RetractionResult:
        """Retract an item and flag every active item that depends on it.

        Nothing is deleted. Retracting an already-retracted item is a no-op
        apart from re-flagging any descendants added since.

        Raises:
            LedgerError: If the item doesn't exist or ``reason`` is empty.

        """
        if not reason.strip():
            raise LedgerError("a retraction needs a reason")
        item = await self.session.get(LedgerItem, item_id)
        if item is None:
            raise LedgerError(f"no ledger item {item_id}")
        if item.status is not ItemStatus.RETRACTED:
            item.status = ItemStatus.RETRACTED
            item.retracted_at = utcnow()
            item.retraction_reason = reason

        descendants = await self.descendants(item_id)
        flagged: list[uuid.UUID] = []
        if descendants:
            res = await self.session.execute(
                update(LedgerItem)
                .where(LedgerItem.id.in_(descendants), LedgerItem.status == ItemStatus.ACTIVE)
                .values(status=ItemStatus.FLAGGED)
                .returning(LedgerItem.id)
            )
            flagged = list(res.scalars())
        await self.session.flush()
        return RetractionResult(retracted=item_id, flagged=flagged)

    # ------------------------------------------------------------------- reads
    async def descendants(self, item_id: uuid.UUID) -> list[uuid.UUID]:
        """All items reachable from ``item_id`` through edges (excluding itself).

        Uses ``UNION`` (not ``UNION ALL``) so a cycle in the graph still terminates.
        """
        edges = LedgerEdge.__table__
        tree = select(edges.c.child_id).where(edges.c.parent_id == item_id).cte(recursive=True)
        tree = tree.union(select(edges.c.child_id).join(tree, edges.c.parent_id == tree.c.child_id))
        res = await self.session.execute(select(tree.c.child_id).where(tree.c.child_id != item_id))
        return list(res.scalars())

    async def orphans(self) -> list[uuid.UUID]:
        """Non-root items with no parent edge. Must be empty (ledger coverage gate)."""
        edge = aliased(LedgerEdge)
        res = await self.session.execute(
            select(LedgerItem.id).where(
                LedgerItem.item_type != ItemType.RUN,
                ~exists().where(edge.child_id == LedgerItem.id),
            )
        )
        return list(res.scalars())

    async def run_items(self, run_id: uuid.UUID) -> list[LedgerItem]:
        """Every ledger item that traces back to a run, run first."""
        res = await self.session.execute(
            select(LedgerItem)
            .where(LedgerItem.source_run_id == run_id)
            .order_by((LedgerItem.item_type != ItemType.RUN), LedgerItem.created_at)
        )
        return list(res.scalars())

    # ----------------------------------------------------------------- helpers
    async def _add_with_edge(self, rows: list[object], edge: LedgerEdge) -> None:
        """Insert rows, then the edge that references them.

        Edges have foreign keys but no ORM relationship, so the unit of work can't
        order them; flushing in two steps guarantees the items exist first.
        """
        self.session.add_all(rows)
        await self.session.flush()
        self.session.add(edge)
        await self.session.flush()

    @staticmethod
    def _require_open(run: Run) -> None:
        if run.outcome is not RunOutcome.RUNNING:
            raise LedgerError(f"run {run.ledger_item_id} already finished ({run.outcome})")
        if run.item.status is not ItemStatus.ACTIVE:
            raise LedgerError(f"run {run.ledger_item_id} is {run.item.status}")
