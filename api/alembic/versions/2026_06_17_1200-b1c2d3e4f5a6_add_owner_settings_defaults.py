"""add owner settings defaults (PO-11): T&C, send message, jurisdiction

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f7
Create Date: 2026-06-17 12:00:00.000000

Persists the org-scoped Purchase Order defaults (PO-11) on the existing
owner_profiles singleton:
- default_terms_and_conditions: pre-filled on new POs (<= 2000 chars,
  enforced in the schema layer).
- default_send_message: pre-filled in the PO Send modal body.
- jurisdiction: ISO 3166-1 alpha-2 driving the jurisdiction-aware
  compliance-reference label (PO-10).

Also snapshots jurisdiction on owner_profile_snapshots so the issued
document's compliance label is frozen at issue time and a later Settings
change cannot re-label an already-sent document.

All columns are nullable with NO backfill: a NULL resolves to the built-in
default at read time (app.constants.settings_defaults), so the single live
profile row and any existing snapshots upgrade cleanly without a data step.

Chained after the re-keyed purchase-orders migration (a1b2c3d4e5f7) to keep
a single linear history.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "owner_profiles",
        sa.Column("default_terms_and_conditions", sa.Text(), nullable=True),
    )
    op.add_column(
        "owner_profiles",
        sa.Column("default_send_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "owner_profiles",
        sa.Column("jurisdiction", sa.String(length=2), nullable=True),
    )
    op.add_column(
        "owner_profile_snapshots",
        sa.Column("jurisdiction", sa.String(length=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("owner_profile_snapshots", "jurisdiction")
    op.drop_column("owner_profiles", "jurisdiction")
    op.drop_column("owner_profiles", "default_send_message")
    op.drop_column("owner_profiles", "default_terms_and_conditions")
