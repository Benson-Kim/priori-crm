"""PO-18: allow 'paid' purchase-order status (disable billed/canceled)

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-06-18 12:00:00.000000

Extends ck_purchase_orders_valid_status to accept the new 'paid' status
introduced by the v1 lifecycle change (DRAFT -> SENT -> PAID, work item
#19). The legacy 'billed' and 'canceled' values are intentionally KEPT in
the CHECK set: they are disabled at the application layer (the state
machine exposes no edge into them) rather than removed, so re-enabling them
later needs no data migration and any historical rows remain valid.

Chained off the current head b1c2d3e4f5a6 (owner settings defaults) to keep
a single linear history. Clean upgrade/downgrade on PostgreSQL 16.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_purchase_orders_valid_status"
_TABLE = "purchase_orders"

# New set includes 'paid'; legacy values retained (disabled in app, not DB).
_NEW_STATUSES = "'draft', 'sent', 'billed', 'canceled', 'paid'"
# Original set as created by the PO-01 migration.
_OLD_STATUSES = "'draft', 'sent', 'billed', 'canceled'"


def upgrade() -> None:
    # Drop-and-recreate: PostgreSQL has no ALTER CONSTRAINT for a CHECK body.
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        f"status IN ({_NEW_STATUSES})",
    )


def downgrade() -> None:
    # Restore the original set. Safe only if no row is in the 'paid' state;
    # the lifecycle change is forward-only in practice, but the downgrade is
    # provided for a clean reverse on an unmigrated dataset.
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        f"status IN ({_OLD_STATUSES})",
    )
