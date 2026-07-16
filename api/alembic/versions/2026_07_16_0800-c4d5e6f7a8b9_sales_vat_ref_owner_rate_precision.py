"""Sales VAT compliance ref columns + owner vat_rate precision

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-07-16 08:00:00.000000

Two follow-ups from the sales VAT work (!19):

1. invoices / quotes gain ``vat_compliance_ref`` (String(50), nullable) -
   the tax/VAT registration reference printed on the issued document. The
   sales editor already sent ``vatComplianceRef``; the server now persists
   it, mirroring the PO-27 Option A contract (explicit value wins; when
   omitted and VAT is enabled it defaults from the owner tax PIN at the
   service layer).

2. owner_profiles / owner_profile_snapshots ``vat_rate`` widens from
   Numeric(5,2) to Numeric(5,4) so fractional defaults like 16.5%
   (0.1650) are representable, matching the invoice/quote/PO columns.
   Widening precision is lossless, so no data backfill is required.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DOCUMENT_TABLES = ("invoices", "quotes")
_OWNER_TABLES = ("owner_profiles", "owner_profile_snapshots")


def upgrade() -> None:
    for table in _DOCUMENT_TABLES:
        op.add_column(
            table,
            sa.Column("vat_compliance_ref", sa.String(length=50), nullable=True),
        )
    for table in _OWNER_TABLES:
        op.alter_column(
            table,
            "vat_rate",
            type_=sa.Numeric(precision=5, scale=4),
            existing_type=sa.Numeric(precision=5, scale=2),
            existing_nullable=True,
        )


def downgrade() -> None:
    for table in _OWNER_TABLES:
        op.alter_column(
            table,
            "vat_rate",
            type_=sa.Numeric(precision=5, scale=2),
            existing_type=sa.Numeric(precision=5, scale=4),
            existing_nullable=True,
        )
    for table in _DOCUMENT_TABLES:
        op.drop_column(table, "vat_compliance_ref")
