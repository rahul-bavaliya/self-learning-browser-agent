"""Gate decisions as evidence ('confirmation' kind) and runs.end_reason.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("screenshot", "aria_snapshot")


def upgrade() -> None:
    """Add the enum value (allowed inside a transaction on Postgres 12+)."""
    op.execute("ALTER TYPE evidence_kind ADD VALUE IF NOT EXISTS 'confirmation'")
    op.add_column("runs", sa.Column("end_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    """Postgres can't drop an enum value, so rebuild the type.

    Refuses if confirmation evidence exists: dropping it would silently erase
    provenance. Retract or export it first.
    """
    op.drop_column("runs", "end_reason")
    bind = op.get_bind()
    count = bind.exec_driver_sql(
        "SELECT count(*) FROM evidence WHERE kind = 'confirmation'"
    ).scalar_one()
    if count:
        raise RuntimeError(f"{count} confirmation evidence rows exist; refusing to drop them")
    values = ", ".join(f"'{v}'" for v in OLD)
    op.execute("ALTER TYPE evidence_kind RENAME TO evidence_kind_old")
    op.execute(f"CREATE TYPE evidence_kind AS ENUM ({values})")
    op.execute(
        "ALTER TABLE evidence ALTER COLUMN kind TYPE evidence_kind "
        "USING kind::text::evidence_kind"
    )
    op.execute("DROP TYPE evidence_kind_old")
