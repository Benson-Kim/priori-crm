"""Add document-level VAT columns to invoices and quotes (Sales VAT toggle)

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-07-16 06:00:00.000000

Sales documents (invoices, quotes) gain the same document-level VAT toggle
purchase orders already have (PO-27):

invoices / quotes
  - vat_enabled : bool, NOT NULL, default false. When true, tax_total is
                  computed once on the discounted subtotal at vat_rate and
                  line items persist tax_type=no_tax / tax_amount=0.
  - vat_rate    : Numeric(5,4), nullable. Applied rate as a fraction
                  (e.g. 0.1600). CHECK 0 <= vat_rate <= 1, and required
                  when vat_enabled is true.

No backfill: existing rows keep vat_enabled=false — their tax lives in the
per-line tax_type/tax_amount columns, which remain authoritative whenever
the document-level toggle is off. New documents default the toggle from the
owner profile's VAT settings at create time (service-level).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("invoices", "quotes")


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
            sa.Column("vat_rate", sa.Numeric(precision=5, scale=4), nullable=True),
        )
        op.create_check_constraint(
            f"ck_{table}_vat_rate_range",
            table,
            "vat_rate IS NULL OR (vat_rate >= 0 AND vat_rate <= 1)",
        )
        op.create_check_constraint(
            f"ck_{table}_vat_rate_present_when_enabled",
            table,
            "vat_enabled = false OR vat_rate IS NOT NULL",
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_constraint(
            f"ck_{table}_vat_rate_present_when_enabled", table, type_="check"
        )
        op.drop_constraint(f"ck_{table}_vat_rate_range", table, type_="check")
        op.drop_column(table, "vat_rate")
        op.drop_column(table, "vat_enabled")
