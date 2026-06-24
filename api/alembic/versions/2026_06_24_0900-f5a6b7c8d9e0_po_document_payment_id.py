"""Add payment_id to purchase_order_documents

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-06-24 09:00:00.000000

Proof-of-payment documents uploaded in the Record Payment / payment-detail
modal are grouped under their payment via a new nullable
``purchase_order_documents.payment_id`` FK (ON DELETE SET NULL), so a single
payment can carry any number of documents. PO-level (form / view) attachments
leave the column NULL. A supporting index backs the per-payment lookup.

Chained off e4f5a6b7c8d9 (PO status drop billed/canceled). Clean
upgrade/downgrade on PostgreSQL 16.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "purchase_order_documents"
_COLUMN = "payment_id"
_INDEX = "ix_purchase_order_documents_payment_id"
_FK = "fk_po_documents_payment_id"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        _FK,
        _TABLE,
        "purchase_order_payments",
        [_COLUMN],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(_INDEX, _TABLE, [_COLUMN])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_constraint(_FK, _TABLE, type_="foreignkey")
    op.drop_column(_TABLE, _COLUMN)
