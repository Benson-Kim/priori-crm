"""Persist discounted taxable bases for invoice line tax.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-07-22 12:00:00.000000

Undiscounted historical line-tax invoices can be backfilled exactly. Discounted
issued invoices remain NULL because their stored VAT is immutable and no
authoritative discounted line base was persisted when they were issued.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "invoice_line_items",
        sa.Column("taxable_value", sa.Numeric(precision=15, scale=2), nullable=True),
    )
    op.create_check_constraint(
        "ck_line_items_taxable_value_non_negative",
        "invoice_line_items",
        "taxable_value IS NULL OR taxable_value >= 0",
    )
    op.execute(
        """
        UPDATE invoice_line_items AS line
        SET taxable_value = line.line_total
        FROM invoices AS invoice
        WHERE line.invoice_id = invoice.id
          AND invoice.vat_enabled = false
          AND (
            invoice.discount_type IS NULL
            OR (
              invoice.discount_type = 'amount'
              AND COALESCE(invoice.discount_amount, 0) = 0
            )
            OR (
              invoice.discount_type = 'percentage'
              AND COALESCE(invoice.discount_percentage, 0) = 0
            )
          )
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_line_items_taxable_value_non_negative",
        "invoice_line_items",
        type_="check",
    )
    op.drop_column("invoice_line_items", "taxable_value")
