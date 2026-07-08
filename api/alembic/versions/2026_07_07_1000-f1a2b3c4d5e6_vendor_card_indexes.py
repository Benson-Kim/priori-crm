"""composite indexes for the vendor detail cards

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-07-07 10:00:00.000000

The three vendor detail cards (Total POs / Total Payments / Total Bills) filter
by vendor_id AND a date, ordered newest-first:

  - POs   : WHERE vendor_id = ? AND status IN (...) AND order_date BETWEEN ?
            ORDER BY order_date DESC
  - Bills : WHERE vendor_id = ? AND status IN (...) AND expense_date BETWEEN ?
            ORDER BY expense_date DESC

The existing single-column indexes (ix_purchase_orders_order_date,
ix_expenses_expense_date) and the (vendor_id, status) composites each serve
only half the predicate, forcing a bitmap-AND or a sort. These composite
(vendor_id, <date> DESC) indexes let the planner satisfy the filter AND the
sort from one index, keeping the card queries inside the <1s budget as row
counts grow (WI-10 / ADR-0003 / ADR-0006).

Created IF NOT EXISTS and PostgreSQL-guarded so re-runs and the SQLite unit
fallback are safe.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e0f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index_name, table, columns-with-order) for each vendor-scoped, date-ordered
# card query.
_CARD_INDEXES: list[tuple[str, str, str]] = [
    (
        "ix_purchase_orders_vendor_order_date",
        "purchase_orders",
        "vendor_id, order_date DESC",
    ),
    (
        "ix_expenses_vendor_expense_date",
        "expenses",
        "vendor_id, expense_date DESC",
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for index_name, table, columns in _CARD_INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({columns})")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for index_name, _table, _columns in _CARD_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
