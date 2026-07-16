"""Add owner-profile VAT settings columns (owner VAT defaults)

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-07-16 07:00:00.000000

The ORM models for owner_profiles / owner_profile_snapshots gained
vat_enabled / vat_rate (the owner VAT defaults that seed new sales
documents and purchase orders), but no migration shipped alongside
them: CI creates the schema from the models, so tests passed while
``alembic upgrade head`` would leave a migrated database without the
columns. This migration closes that gap.

owner_profiles / owner_profile_snapshots
  - vat_enabled : bool, NOT NULL, default false
  - vat_rate    : Numeric(5,2), nullable. Rate as a fraction (e.g. 0.16)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("owner_profiles", "owner_profile_snapshots")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "vat_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
        op.add_column(
            table,
            sa.Column("vat_rate", sa.Numeric(precision=5, scale=2), nullable=True),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "vat_rate")
        op.drop_column(table, "vat_enabled")
