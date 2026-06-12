"""add customers.version for optimistic locking

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-06-11 15:00:00.000000

Customer master data (including currency, which every invoice and quote is
pinned to) was the only mutable entity without an optimistic-locking version
column, so concurrent edits were silent last-write-wins. The
column is NOT NULL with a server default of 1 so existing rows backfill
cleanly, mirroring the vendors/invoices/quotes/expenses pattern.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("customers", "version")
