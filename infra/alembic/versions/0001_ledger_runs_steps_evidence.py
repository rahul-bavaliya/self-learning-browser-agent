"""Phase 1 provenance ledger: ledger_items, ledger_edges, runs, steps, evidence.

Revision ID: 0001
Revises:
Create Date: 2026-11-03 13:53:29.421969

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('ledger_items',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('item_type', sa.Enum('run', 'step', 'evidence', name='item_type'), nullable=False),
    sa.Column('origin_type', sa.Enum('agent_run', 'human', 'benchmark', 'import', name='origin_type'), nullable=False),
    sa.Column('source_run_id', sa.UUID(), nullable=True),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('evidence_ref', sa.Text(), nullable=True),
    sa.Column('trust_score', sa.Float(), nullable=False),
    sa.Column('status', sa.Enum('active', 'flagged', 'retracted', name='item_status'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('retracted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('retraction_reason', sa.Text(), nullable=True),
    sa.CheckConstraint("(status = 'retracted') = (retracted_at IS NOT NULL)", name=op.f('ck_ledger_items_retracted_has_time')),
    sa.CheckConstraint('trust_score >= 0 AND trust_score <= 1', name=op.f('ck_ledger_items_trust_range')),
    sa.ForeignKeyConstraint(['source_run_id'], ['ledger_items.id'], name=op.f('fk_ledger_items_source_run_id_ledger_items'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ledger_items'))
    )
    op.create_index(op.f('ix_ledger_items_item_type'), 'ledger_items', ['item_type'], unique=False)
    op.create_index(op.f('ix_ledger_items_source_run_id'), 'ledger_items', ['source_run_id'], unique=False)
    op.create_index(op.f('ix_ledger_items_status'), 'ledger_items', ['status'], unique=False)
    op.create_table('evidence',
    sa.Column('ledger_item_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.Enum('screenshot', 'aria_snapshot', name='evidence_kind'), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('bytes', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['ledger_item_id'], ['ledger_items.id'], name=op.f('fk_evidence_ledger_item_id_ledger_items'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('ledger_item_id', name=op.f('pk_evidence'))
    )
    op.create_index(op.f('ix_evidence_sha256'), 'evidence', ['sha256'], unique=False)
    op.create_table('ledger_edges',
    sa.Column('parent_id', sa.UUID(), nullable=False),
    sa.Column('child_id', sa.UUID(), nullable=False),
    sa.Column('relation', sa.Enum('produced', 'evidence_for', 'derived_from', name='relation'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['child_id'], ['ledger_items.id'], name=op.f('fk_ledger_edges_child_id_ledger_items'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['parent_id'], ['ledger_items.id'], name=op.f('fk_ledger_edges_parent_id_ledger_items'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('parent_id', 'child_id', 'relation', name=op.f('pk_ledger_edges'))
    )
    op.create_index(op.f('ix_ledger_edges_child_id'), 'ledger_edges', ['child_id'], unique=False)
    op.create_table('runs',
    sa.Column('ledger_item_id', sa.UUID(), nullable=False),
    sa.Column('goal', sa.Text(), nullable=False),
    sa.Column('start_url', sa.Text(), nullable=False),
    sa.Column('channel', sa.String(length=16), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('task_id', sa.String(length=128), nullable=True),
    sa.Column('outcome', sa.Enum('running', 'success', 'failure', 'aborted', 'blocked', name='run_outcome'), nullable=False),
    sa.Column('verified', sa.Boolean(), nullable=False),
    sa.Column('agent_answer', sa.Text(), nullable=True),
    sa.Column('step_count', sa.Integer(), nullable=False),
    sa.Column('tokens_in', sa.Integer(), nullable=False),
    sa.Column('tokens_out', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['ledger_item_id'], ['ledger_items.id'], name=op.f('fk_runs_ledger_item_id_ledger_items'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('ledger_item_id', name=op.f('pk_runs'))
    )
    op.create_index(op.f('ix_runs_task_id'), 'runs', ['task_id'], unique=False)
    op.create_table('steps',
    sa.Column('ledger_item_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('idx', sa.Integer(), nullable=False),
    sa.Column('observation_ref', sa.Text(), nullable=True),
    sa.Column('action', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('result', sa.Enum('ok', 'error', 'gated', 'denied', name='step_result'), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('tokens_in', sa.Integer(), nullable=False),
    sa.Column('tokens_out', sa.Integer(), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['ledger_item_id'], ['ledger_items.id'], name=op.f('fk_steps_ledger_item_id_ledger_items'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['run_id'], ['runs.ledger_item_id'], name=op.f('fk_steps_run_id_runs'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('ledger_item_id', name=op.f('pk_steps')),
    sa.UniqueConstraint('run_id', 'idx', name='uq_steps_run_id_idx')
    )
    op.create_index(op.f('ix_steps_run_id'), 'steps', ['run_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_steps_run_id'), table_name='steps')
    op.drop_table('steps')
    op.drop_index(op.f('ix_runs_task_id'), table_name='runs')
    op.drop_table('runs')
    op.drop_index(op.f('ix_ledger_edges_child_id'), table_name='ledger_edges')
    op.drop_table('ledger_edges')
    op.drop_index(op.f('ix_evidence_sha256'), table_name='evidence')
    op.drop_table('evidence')
    op.drop_index(op.f('ix_ledger_items_status'), table_name='ledger_items')
    op.drop_index(op.f('ix_ledger_items_source_run_id'), table_name='ledger_items')
    op.drop_index(op.f('ix_ledger_items_item_type'), table_name='ledger_items')
    op.drop_table('ledger_items')
    # Postgres enum types outlive their tables; drop them so upgrade can run again.
    sa.Enum(name="step_result").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="run_outcome").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="relation").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="evidence_kind").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="item_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="origin_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="item_type").drop(op.get_bind(), checkfirst=True)
