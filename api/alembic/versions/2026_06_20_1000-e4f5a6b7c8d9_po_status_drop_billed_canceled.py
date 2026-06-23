"""Remove 'billed' and 'canceled' purchase-order statuses

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-06-20 10:00:00.000000

The purchase-order lifecycle is DRAFT -> SENT -> PAID. The previously
retained-but-disabled 'billed' and 'canceled' values are removed from the
status CHECK constraint entirely. No transition ever wrote those values
(the state machine exposed no edge into them), so no data backfill is
required; an UPDATE is included only as a defensive no-op guard.

Chained off d3e4f5a6b7c8 (PO payments + balance). Clean upgrade/downgrade
on PostgreSQL 16.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_purchase_orders_valid_status"
_TABLE = "purchase_orders"

# New set: the active three-status lifecycle only.
_NEW_STATUSES = "'draft', 'sent', 'paid'"
# Previous set carried the disabled legacy values.
_OLD_STATUSES = "'draft', 'sent', 'billed', 'canceled', 'paid'"


def upgrade() -> None:
    # Defensive: no row should be in a removed state (no edge ever wrote
    # them), but guard the CHECK swap against any stray legacy data.
    op.execute(
        "UPDATE purchase_orders SET status = 'draft' "
        "WHERE status IN ('billed', 'canceled')"
    )
    # Drop-and-recreate: PostgreSQL has no ALTER CONSTRAINT for a CHECK body.
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        f"status IN ({_NEW_STATUSES})",
    )


def downgrade() -> None:
    # Restore the wider set (re-admits the legacy disabled values).
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        f"status IN ({_OLD_STATUSES})",
    )
