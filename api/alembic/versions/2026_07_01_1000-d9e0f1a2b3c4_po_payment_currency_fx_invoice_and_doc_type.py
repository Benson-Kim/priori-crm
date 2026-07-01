"""Add payment currency/FX/invoice fields and document_type to PO payments/docs

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-07-01 10:00:00.000000

Record Payment v2 (PO-28) captures more per-payment data and splits proof
attachments into Invoice vs POP:

purchase_order_payments
  - invoice_number : free-text reference of the bill(s) paid against (nullable)
  - currency       : ISO 4217 currency the payment was made in (NOT NULL).
                     May differ from the PO currency. Backfilled from the
                     parent PO's currency for existing rows.
  - exchange_rate  : Numeric(18,8) rate converting payment currency -> PO
                     currency (NOT NULL, default 1, CHECK > 0). Existing rows
                     were recorded in the PO currency, so 1 is correct.

purchase_order_documents
  - document_type  : 'invoice' | 'pop' | 'other' (NOT NULL, default 'other',
                     CHECK). Existing payment-modal proof docs are backfilled
                     to 'pop'; all other rows stay 'other'.

Forward/backward-safe on PostgreSQL 16. The new NOT NULL columns are added
with server defaults so the ALTER never fails on existing data; backfills run
after the column is present.

NOTE: This file is a verbatim copy of the migration authored on
feat/po-record-payment-v2 (!56). It is carried here so PO-27's migration can
chain linearly behind it (c8d9e0f1a2b3 -> d9e0f1a2b3c4 -> e0f1a2b3c4d5),
avoiding a multiple-heads Alembic branch when both land on develop. Keep it
byte-identical to !56's copy so the merge stays conflict-free.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PAYMENTS = "purchase_order_payments"
_DOCUMENTS = "purchase_order_documents"


def upgrade() -> None:
    # --- purchase_order_payments -------------------------------------
    op.add_column(
        _PAYMENTS,
        sa.Column("invoice_number", sa.String(length=200), nullable=True),
    )
    # Add currency with a temporary server_default so the NOT NULL add
    # succeeds on existing rows, then backfill from the parent PO.
    op.add_column(
        _PAYMENTS,
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            server_default="KES",
        ),
    )
    op.add_column(
        _PAYMENTS,
        sa.Column(
            "exchange_rate",
            sa.Numeric(precision=18, scale=8),
            nullable=False,
            server_default="1",
        ),
    )

    # Backfill existing payments' currency from their parent PO. Historical
    # payments were recorded in the PO currency, so exchange_rate stays 1.
    op.execute(
        f"UPDATE {_PAYMENTS} AS p SET currency = po.currency FROM purchase_orders AS po WHERE p.po_id = po.id"
    )

    op.create_check_constraint(
        "ck_po_payments_valid_currency",
        _PAYMENTS,
        "currency IN ('KES', 'USD', 'EUR', 'GBP')",
    )
    op.create_check_constraint(
        "ck_po_payments_exchange_rate_positive",
        _PAYMENTS,
        "exchange_rate > 0",
    )

    # --- purchase_order_documents ------------------------------------
    op.add_column(
        _DOCUMENTS,
        sa.Column(
            "document_type",
            sa.String(length=20),
            nullable=False,
            server_default="other",
        ),
    )
    # Existing proof-of-payment uploads (payment-modal, grouped under a
    # payment) become 'pop'; everything else stays 'other'.
    op.execute(
        f"UPDATE {_DOCUMENTS} SET document_type = 'pop' WHERE source = 'payment_modal' AND payment_id IS NOT NULL"
    )
    op.create_check_constraint(
        "ck_po_documents_valid_document_type",
        _DOCUMENTS,
        "document_type IN ('invoice', 'pop', 'other')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_po_documents_valid_document_type", _DOCUMENTS, type_="check")
    op.drop_column(_DOCUMENTS, "document_type")

    op.drop_constraint(
        "ck_po_payments_exchange_rate_positive", _PAYMENTS, type_="check"
    )
    op.drop_constraint("ck_po_payments_valid_currency", _PAYMENTS, type_="check")
    op.drop_column(_PAYMENTS, "exchange_rate")
    op.drop_column(_PAYMENTS, "currency")
    op.drop_column(_PAYMENTS, "invoice_number")
