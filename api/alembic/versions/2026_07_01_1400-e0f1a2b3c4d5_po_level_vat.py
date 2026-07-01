"""Add PO-level VAT columns (vat_enabled / vat_rate / vat_compliance_ref)

Revision ID: e0f1a2b3c4d5
Revises: c8d9e0f1a2b3
Create Date: 2026-07-01 14:00:00.000000

PO-27 moves VAT from per-line to a PO-level toggle computed on the subtotal:

purchase_orders
  - vat_enabled        : bool, NOT NULL, default false. When true, tax_total
                         is computed on the subtotal at vat_rate.
  - vat_rate           : Numeric(5,4), nullable. Applied rate as a fraction
                         (e.g. 0.1600). CHECK 0 <= vat_rate <= 1, and required
                         when vat_enabled is true.
  - vat_compliance_ref : String(255), nullable. VAT/compliance reference
                         printed on the VAT line; defaults from the owner
                         profile's tax_pin at create time (editable per PO).

Backfill for existing rows (deterministic):
  - vat_enabled := (tax_total > 0)
  - vat_rate    := round(tax_total / subtotal, 4) when subtotal > 0 and
                   tax_total > 0, else NULL (historical blended effective rate)
  - vat_compliance_ref stays NULL (create-time default going forward)

The columns are added with a server_default for vat_enabled so the NOT NULL
add succeeds on existing data; the CHECK constraints are created only after
the backfill has populated vat_rate for enabled rows. Forward/backward-safe
on PostgreSQL 16.

NOTE: linearised behind the PO-payments migration d9e0f1a2b3c4 (MR !56) to
avoid a multiple-heads Alembic branch. The two migrations touch disjoint
tables (this one: purchase_orders; !56: purchase_order_payments /
purchase_order_documents), so applying them in series is order-safe. A copy of
d9e0f1a2b3c4 is carried on this branch purely so `alembic upgrade head`
resolves a single linear chain c8d9e0f1a2b3 -> d9e0f1a2b3c4 -> e0f1a2b3c4d5
here and after !56 merges.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e0f1a2b3c4d5"
down_revision: Union[str, None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "purchase_orders"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "vat_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column("vat_rate", sa.Numeric(precision=5, scale=4), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column("vat_compliance_ref", sa.String(length=255), nullable=True),
    )

    # Backfill from the historical per-line tax totals. A row that carried any
    # tax becomes VAT-enabled; its rate is the blended effective rate
    # (tax_total / subtotal) rounded to 4 dp. Rows with no tax stay disabled
    # with a NULL rate.
    op.execute(
        "UPDATE purchase_orders SET vat_enabled = (tax_total > 0), vat_rate = CASE WHEN subtotal > 0 AND tax_total > 0 THEN round(tax_total / subtotal, 4) ELSE NULL END"
    )

    op.create_check_constraint(
        "ck_purchase_orders_vat_rate_range",
        _TABLE,
        "vat_rate IS NULL OR (vat_rate >= 0 AND vat_rate <= 1)",
    )
    op.create_check_constraint(
        "ck_purchase_orders_vat_rate_present_when_enabled",
        _TABLE,
        "vat_enabled = false OR vat_rate IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_purchase_orders_vat_rate_present_when_enabled", _TABLE, type_="check"
    )
    op.drop_constraint("ck_purchase_orders_vat_rate_range", _TABLE, type_="check")
    op.drop_column(_TABLE, "vat_compliance_ref")
    op.drop_column(_TABLE, "vat_rate")
    op.drop_column(_TABLE, "vat_enabled")
