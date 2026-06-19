"""PO-19: purchase-order payments table + balance columns

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-06-18 13:00:00.000000

Adds the payment/balance surface to the Purchase Orders module (work item
#20), mirroring the expenses module:
- purchase_orders gains amount_paid, balance_due (Numeric(15,2), default
  0.00, CHECK >= 0) and paid_at (timestamptz, nullable). Existing rows
  backfill balance_due = total via the server_default + an explicit UPDATE.
- purchase_order_payments: auditable payment records (amount CHECK > 0,
  optional proof-of-payment document_id FK -> purchase_order_documents with
  ON DELETE SET NULL); indexed on po_id and payment_date.
- purchase_order_documents.source CHECK widened to include 'payment_modal'.

Chained off c2d3e4f5a6b7 (PO-18 status CHECK). Clean upgrade/downgrade on
PostgreSQL 16.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DOC_SOURCE_CK = "ck_po_documents_valid_source"


def upgrade() -> None:
    # Balance columns on purchase_orders.
    op.add_column(
        "purchase_orders",
        sa.Column(
            "amount_paid",
            sa.Numeric(precision=15, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
    )
    op.add_column(
        "purchase_orders",
        sa.Column(
            "balance_due",
            sa.Numeric(precision=15, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
    )
    op.add_column(
        "purchase_orders",
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill balance_due for existing rows (no payments yet -> full total).
    op.execute("UPDATE purchase_orders SET balance_due = total")

    op.create_check_constraint(
        "ck_purchase_orders_amount_paid_non_negative",
        "purchase_orders",
        "amount_paid >= 0",
    )
    op.create_check_constraint(
        "ck_purchase_orders_balance_due_non_negative",
        "purchase_orders",
        "balance_due >= 0",
    )

    # Widen the document source CHECK to allow payment_modal.
    op.drop_constraint(_DOC_SOURCE_CK, "purchase_order_documents", type_="check")
    op.create_check_constraint(
        _DOC_SOURCE_CK,
        "purchase_order_documents",
        "source IN ('form', 'view', 'payment_modal')",
    )

    # Payments table.
    op.create_table(
        "purchase_order_payments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("po_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("reference", sa.String(length=200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("recorded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("amount > 0", name="ck_po_payments_amount_positive"),
        sa.ForeignKeyConstraint(["po_id"], ["purchase_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["purchase_order_documents.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_purchase_order_payments_po_id",
        "purchase_order_payments",
        ["po_id"],
    )
    op.create_index(
        "ix_purchase_order_payments_payment_date",
        "purchase_order_payments",
        ["payment_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_purchase_order_payments_payment_date",
        table_name="purchase_order_payments",
    )
    op.drop_index(
        "ix_purchase_order_payments_po_id",
        table_name="purchase_order_payments",
    )
    op.drop_table("purchase_order_payments")

    # Restore the original document source CHECK (form | view).
    op.drop_constraint(_DOC_SOURCE_CK, "purchase_order_documents", type_="check")
    op.create_check_constraint(
        _DOC_SOURCE_CK,
        "purchase_order_documents",
        "source IN ('form', 'view')",
    )

    op.drop_constraint(
        "ck_purchase_orders_balance_due_non_negative",
        "purchase_orders",
        type_="check",
    )
    op.drop_constraint(
        "ck_purchase_orders_amount_paid_non_negative",
        "purchase_orders",
        type_="check",
    )
    op.drop_column("purchase_orders", "paid_at")
    op.drop_column("purchase_orders", "balance_due")
    op.drop_column("purchase_orders", "amount_paid")
