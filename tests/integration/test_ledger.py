"""Ledger repository against a real, migrated Postgres."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from provenance.ledger import Ledger, LedgerError
from provenance.models import (
    EvidenceKind,
    ItemStatus,
    ItemType,
    LedgerEdge,
    LedgerItem,
    Relation,
    Run,
    RunOutcome,
    Step,
    StepResult,
)
from provenance.trust import FAILURE_TRUST, INITIAL_TRUST, VERIFIED_SUCCESS_TRUST

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]


async def _run_with_steps(
    ledger: Ledger, tmp_path: Path, n_steps: int = 2
) -> tuple[Run, list[Step]]:
    run = await ledger.start_run(
        goal="find the cheapest kettle",
        start_url="http://127.0.0.1:8001/",
        channel="chrome",
        model="fake",
        task_id="nav-01",
    )
    steps = []
    for i in range(n_steps):
        step = await ledger.record_step(
            run,
            action={"type": "click", "ref": f"e{i}"},
            result=StepResult.OK,
            url=f"http://127.0.0.1:8001/p/{i}",
            tokens_in=100,
            tokens_out=10,
        )
        shot = tmp_path / f"step{i}.png"
        shot.write_bytes(b"fake-png-%d" % i)
        await ledger.record_evidence(step, kind=EvidenceKind.SCREENSHOT, path=shot)
        steps.append(step)
    return run, steps


async def test_run_steps_evidence_are_fully_covered(session: AsyncSession, tmp_path: Path) -> None:
    ledger = Ledger(session)
    run, steps = await _run_with_steps(ledger, tmp_path)
    await session.commit()

    items = await ledger.run_items(run.ledger_item_id)
    assert [i.item_type for i in items].count(ItemType.RUN) == 1
    assert [i.item_type for i in items].count(ItemType.STEP) == 2
    assert [i.item_type for i in items].count(ItemType.EVIDENCE) == 2
    assert all(i.source_run_id == run.ledger_item_id for i in items)
    assert await ledger.orphans() == []

    assert [s.idx for s in steps] == [0, 1]
    assert (run.step_count, run.tokens_in, run.tokens_out) == (2, 200, 20)

    edges = (await session.execute(select(LedgerEdge))).scalars().all()
    assert {e.relation for e in edges} == {Relation.PRODUCED, Relation.EVIDENCE_FOR}
    assert len(edges) == 4


async def test_evidence_hash_and_size(session: AsyncSession, tmp_path: Path) -> None:
    ledger = Ledger(session)
    run = await ledger.start_run(
        goal="g", start_url="http://localhost/", channel="msedge", model="m"
    )
    step = await ledger.record_step(run, action={"type": "wait"}, result=StepResult.OK, url=None)
    f = tmp_path / "snap.yml"
    f.write_text("- button 'Go' [ref=e1]")
    ev = await ledger.record_evidence(step, kind=EvidenceKind.ARIA_SNAPSHOT, path=f)
    assert ev.sha256 == hashlib.sha256(f.read_bytes()).hexdigest()
    assert ev.bytes == f.stat().st_size


@pytest.mark.parametrize(
    ("outcome", "verified", "expected"),
    [
        (RunOutcome.SUCCESS, True, VERIFIED_SUCCESS_TRUST),
        (RunOutcome.SUCCESS, False, INITIAL_TRUST),
        (RunOutcome.FAILURE, False, FAILURE_TRUST),
    ],
)
async def test_finish_run_sets_trust_on_everything(
    session: AsyncSession, tmp_path: Path, outcome: RunOutcome, verified: bool, expected: float
) -> None:
    ledger = Ledger(session)
    run, _ = await _run_with_steps(ledger, tmp_path)
    await ledger.finish_run(run, outcome=outcome, verified=verified, cost_usd=0.01)
    await session.commit()
    items = await ledger.run_items(run.ledger_item_id)
    assert {i.trust_score for i in items} == {expected}
    assert run.ended_at is not None


async def test_finished_run_rejects_more_steps(session: AsyncSession) -> None:
    ledger = Ledger(session)
    run = await ledger.start_run(
        goal="g", start_url="http://localhost/", channel="chrome", model="m"
    )
    await ledger.finish_run(run, outcome=RunOutcome.FAILURE, verified=False)
    with pytest.raises(LedgerError):
        await ledger.record_step(run, action={"type": "wait"}, result=StepResult.OK, url=None)
    with pytest.raises(LedgerError):
        await ledger.finish_run(run, outcome=RunOutcome.SUCCESS, verified=True)


async def test_cannot_verify_a_failed_run(session: AsyncSession) -> None:
    ledger = Ledger(session)
    run = await ledger.start_run(
        goal="g", start_url="http://localhost/", channel="chrome", model="m"
    )
    with pytest.raises(LedgerError, match="only a successful run"):
        await ledger.finish_run(run, outcome=RunOutcome.FAILURE, verified=True)


async def test_retracting_run_flags_all_descendants(session: AsyncSession, tmp_path: Path) -> None:
    ledger = Ledger(session)
    run, _ = await _run_with_steps(ledger, tmp_path, n_steps=3)
    other, _ = await _run_with_steps(ledger, tmp_path, n_steps=1)
    await session.commit()

    result = await ledger.retract(run.ledger_item_id, "injected page content found in step 2")
    await session.commit()

    assert len(result.flagged) == 6  # 3 steps + 3 evidence
    by_status: dict[ItemStatus, int] = {}
    for item in await ledger.run_items(run.ledger_item_id):
        by_status[item.status] = by_status.get(item.status, 0) + 1
    assert by_status == {ItemStatus.RETRACTED: 1, ItemStatus.FLAGGED: 6}

    # An unrelated run is untouched.
    assert {i.status for i in await ledger.run_items(other.ledger_item_id)} == {ItemStatus.ACTIVE}

    retracted = await session.get(LedgerItem, run.ledger_item_id)
    assert retracted is not None and retracted.retracted_at is not None
    assert retracted.retraction_reason == "injected page content found in step 2"


async def test_retracting_a_step_flags_only_its_evidence(
    session: AsyncSession, tmp_path: Path
) -> None:
    ledger = Ledger(session)
    run, steps = await _run_with_steps(ledger, tmp_path, n_steps=2)
    result = await ledger.retract(steps[0].ledger_item_id, "bad screenshot")
    assert len(result.flagged) == 1
    run_item = await session.get(LedgerItem, run.ledger_item_id)
    assert run_item is not None and run_item.status is ItemStatus.ACTIVE


async def test_retraction_is_idempotent_and_needs_reason(
    session: AsyncSession, tmp_path: Path
) -> None:
    ledger = Ledger(session)
    run, _ = await _run_with_steps(ledger, tmp_path, n_steps=1)
    with pytest.raises(LedgerError):
        await ledger.retract(run.ledger_item_id, "   ")
    first = await ledger.retract(run.ledger_item_id, "why")
    second = await ledger.retract(run.ledger_item_id, "why again")
    assert len(first.flagged) == 2 and second.flagged == []
    with pytest.raises(LedgerError):
        await ledger.retract(uuid.uuid4(), "missing")


async def test_descendants_terminates_on_cycles(session: AsyncSession) -> None:
    ledger = Ledger(session)
    a = await ledger.start_run(goal="a", start_url="http://localhost/", channel="chrome", model="m")
    b = await ledger.start_run(goal="b", start_url="http://localhost/", channel="chrome", model="m")
    session.add_all(
        [
            LedgerEdge(
                parent_id=a.ledger_item_id,
                child_id=b.ledger_item_id,
                relation=Relation.DERIVED_FROM,
            ),
            LedgerEdge(
                parent_id=b.ledger_item_id,
                child_id=a.ledger_item_id,
                relation=Relation.DERIVED_FROM,
            ),
        ]
    )
    await session.flush()
    assert await ledger.descendants(a.ledger_item_id) == [b.ledger_item_id]


async def test_db_rejects_out_of_range_trust(session: AsyncSession) -> None:
    session.add(
        LedgerItem(item_type=ItemType.RUN, origin_type="human", trust_score=1.5, status="active")
    )
    with pytest.raises(IntegrityError):
        await session.flush()


def test_downgrade_then_upgrade_is_clean(migrated_db_url: str) -> None:
    cfg = Config(str(REPO / "alembic.ini"))
    cfg.attributes["url"] = migrated_db_url
    cfg.attributes["skip_logging"] = True
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
