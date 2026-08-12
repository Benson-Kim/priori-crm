"""Link quotes to Sales Desk deals: nullable quotes.deal_id FK + index

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-12 14:00:00.000000

Sales Desk <-> Quotes integration (issue #44):

- quotes.deal_id: nullable FK to deals.id with ON DELETE SET NULL, so a
  quote created from a deal always survives the deal (deal *closure* is a
  status change and never touches quotes; deal *deletion* — only possible
  for open/parked deals — merely unlinks). Standalone quotes keep NULL.
- ix_quotes_deal_id: serves the deal-detail embed ("quotes created from
  this deal") and the reverse lookup from the quote list.

Chained onto b2c3d4e5f6a7 (#41 onboarding) per the agreed wave-2
migration order (#40 -> #41 -> #44) to keep a single Alembic head.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "quotes",
        sa.Column(
            "deal_id",
            UUID(as_uuid=True),
            nullable=True,
            comment="Deal this quote was created from (Sales Desk), if any",
        ),
    )
    op.create_foreign_key(
        "fk_quotes_deal_id_deals",
        "quotes",
        "deals",
        ["deal_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_quotes_deal_id", "quotes", ["deal_id"])


def downgrade() -> None:
    op.drop_index("ix_quotes_deal_id", table_name="quotes")
    op.drop_constraint("fk_quotes_deal_id_deals", "quotes", type_="foreignkey")
    op.drop_column("quotes", "deal_id")
