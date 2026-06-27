"""Allow overpayment: drop balance_due >= 0 checks

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-06-26 14:00:00.000000

The application records payments rather than taking them, so an overpayment
(amount_paid > total) is a legitimate, recordable event. balance_due is the
pure derived ledger value ``total - amount_paid`` and must be allowed to go
negative to represent a credit owed back to the payer.

This migration drops the ``balance_due >= 0`` CHECK constraint on the three
payable/receivable documents: invoices, expenses and purchase_orders. The
``amount_paid >= 0`` and total >= 0 checks are intentionally kept (a payment
is always positive; a document total is never negative).

Downgrade re-adds the checks, first clamping any negative balances to 0 so
the constraint re-add cannot fail on overpaid rows created while it was off.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, constraint_name) for each balance_due >= 0 check being dropped.
_BALANCE_CHECKS: tuple[tuple[str, str], ...] = (
    ("invoices", "ck_invoices_balance_non_negative"),
    ("expenses", "ck_expenses_balance_due_non_negative"),
    ("purchase_orders", "ck_purchase_orders_balance_due_non_negative"),
)


def upgrade() -> None:
    for table, constraint in _BALANCE_CHECKS:
        # IF EXISTS keeps the migration idempotent across environments where
        # the test schema (create_all) may already omit the constraint.
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "{constraint}"')


def downgrade() -> None:
    for table, constraint in _BALANCE_CHECKS:
        # Clamp any overpaid (negative) balances created while the check was
        # absent, otherwise re-adding the constraint would fail.
        op.execute(f"UPDATE {table} SET balance_due = 0 WHERE balance_due < 0")
        op.execute(
            f'ALTER TABLE {table} ADD CONSTRAINT "{constraint}" '
            "CHECK (balance_due >= 0)"
        )
