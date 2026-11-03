"""ORM models for the provenance ledger (Phase 1: runs, steps, evidence).

Every learned or recorded item has exactly one row in ``ledger_items`` holding its
provenance (origin, source run, URL, evidence, trust, status). Typed tables
(``runs``, ``steps``, ``evidence``) hold the payload and reference that row 1:1.
``ledger_edges`` records where each item came from, so retraction can walk the graph.
Later phases add memories, skills and training examples the same way.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Stable constraint names so Alembic autogenerate diffs stay clean.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Timezone-aware current time."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base with a consistent constraint naming convention."""

    metadata = MetaData(naming_convention=NAMING)


class ItemType(enum.StrEnum):
    """Kind of item a ledger row describes."""

    RUN = "run"
    STEP = "step"
    EVIDENCE = "evidence"


class OriginType(enum.StrEnum):
    """Where an item originally came from."""

    AGENT_RUN = "agent_run"
    HUMAN = "human"
    BENCHMARK = "benchmark"
    IMPORT = "import"


class ItemStatus(enum.StrEnum):
    """Lifecycle state. Only ``active`` items may feed memory, skills or training."""

    ACTIVE = "active"
    FLAGGED = "flagged"  # an ancestor was retracted; needs review
    RETRACTED = "retracted"


class Relation(enum.StrEnum):
    """How a child item relates to its parent."""

    PRODUCED = "produced"  # run -> step
    EVIDENCE_FOR = "evidence_for"  # step -> evidence
    DERIVED_FROM = "derived_from"  # later: memory/skill/example -> source item


class RunOutcome(enum.StrEnum):
    """Final state of a run."""

    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    ABORTED = "aborted"  # budget or error
    BLOCKED = "blocked"  # CAPTCHA or site protection: stopped by rule


class StepResult(enum.StrEnum):
    """What happened when a step's action was attempted."""

    OK = "ok"
    ERROR = "error"
    GATED = "gated"  # awaiting / received human confirmation
    DENIED = "denied"  # blocked by allowlist or gate


class EvidenceKind(enum.StrEnum):
    """Type of stored evidence file."""

    SCREENSHOT = "screenshot"
    ARIA_SNAPSHOT = "aria_snapshot"
    CONFIRMATION = "confirmation"  # gate decision (who approved/denied what)


def _enum(e: type[enum.Enum], name: str) -> Enum:
    """Postgres enum stored by value (lowercase strings)."""
    return Enum(e, name=name, values_callable=lambda cls: [m.value for m in cls])


class LedgerItem(Base):
    """Provenance record shared by every item type."""

    __tablename__ = "ledger_items"
    __table_args__ = (
        CheckConstraint("trust_score >= 0 AND trust_score <= 1", name="trust_range"),
        CheckConstraint(
            "(status = 'retracted') = (retracted_at IS NOT NULL)", name="retracted_has_time"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    item_type: Mapped[ItemType] = mapped_column(_enum(ItemType, "item_type"), index=True)
    origin_type: Mapped[OriginType] = mapped_column(_enum(OriginType, "origin_type"))
    # The run this item traces back to. A run row points at itself.
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ledger_items.id", ondelete="RESTRICT"), index=True
    )
    url: Mapped[str | None] = mapped_column(Text)
    evidence_ref: Mapped[str | None] = mapped_column(Text)
    trust_score: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[ItemStatus] = mapped_column(
        _enum(ItemStatus, "item_status"), default=ItemStatus.ACTIVE, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retraction_reason: Mapped[str | None] = mapped_column(Text)


class LedgerEdge(Base):
    """Directed dependency: ``child`` was produced by or derived from ``parent``."""

    __tablename__ = "ledger_edges"

    parent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_items.id", ondelete="RESTRICT"), primary_key=True
    )
    child_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_items.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    relation: Mapped[Relation] = mapped_column(_enum(Relation, "relation"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class Run(Base):
    """One attempt by the agent at one goal."""

    __tablename__ = "runs"

    ledger_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_items.id", ondelete="RESTRICT"), primary_key=True
    )
    goal: Mapped[str] = mapped_column(Text)
    start_url: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(128))
    task_id: Mapped[str | None] = mapped_column(String(128), index=True)
    outcome: Mapped[RunOutcome] = mapped_column(
        _enum(RunOutcome, "run_outcome"), default=RunOutcome.RUNNING
    )
    verified: Mapped[bool] = mapped_column(default=False)
    agent_answer: Mapped[str | None] = mapped_column(Text)
    # Why the run stopped, e.g. 'agent finished', 'step budget (25) reached', 'captcha'.
    end_reason: Mapped[str | None] = mapped_column(Text)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    item: Mapped[LedgerItem] = relationship(lazy="joined")


class Step(Base):
    """One observe -> act cycle inside a run."""

    __tablename__ = "steps"
    __table_args__ = (UniqueConstraint("run_id", "idx", name="uq_steps_run_id_idx"),)

    ledger_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_items.id", ondelete="RESTRICT"), primary_key=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.ledger_item_id", ondelete="RESTRICT"), index=True
    )
    idx: Mapped[int] = mapped_column(Integer)
    observation_ref: Mapped[str | None] = mapped_column(Text)
    action: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[StepResult] = mapped_column(_enum(StepResult, "step_result"))
    error: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)

    item: Mapped[LedgerItem] = relationship(lazy="joined")


class Evidence(Base):
    """A file on disk supporting a step; the DB keeps path and hash, not bytes."""

    __tablename__ = "evidence"

    ledger_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_items.id", ondelete="RESTRICT"), primary_key=True
    )
    kind: Mapped[EvidenceKind] = mapped_column(_enum(EvidenceKind, "evidence_kind"))
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    bytes: Mapped[int] = mapped_column(Integer)

    item: Mapped[LedgerItem] = relationship(lazy="joined")
