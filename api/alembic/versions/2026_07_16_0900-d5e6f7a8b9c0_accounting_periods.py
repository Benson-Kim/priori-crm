"""Add accounting_periods (period locking, issue #8)

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-07-16 09:00:00.000000

Accounting periods with an open/closed lifecycle. A closed period locks
every financial write dated inside it (documents and payments) via
app/common/period_guard.py. Overlap is enforced at the service layer
(privileged, low-frequency admin path); the composite index serves the
guard's hot query (closed periods containing a date).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "accounting_periods",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=10),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reopen_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "end_date >= start_date",
            name="ck_accounting_periods_range_valid",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'closed')",
            name="ck_accounting_periods_valid_status",
        ),
    )
    op.create_index(
        "ix_accounting_periods_status_dates",
        "accounting_periods",
        ["status", "start_date", "end_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_accounting_periods_status_dates", table_name="accounting_periods"
    )
    op.drop_table("accounting_periods")
