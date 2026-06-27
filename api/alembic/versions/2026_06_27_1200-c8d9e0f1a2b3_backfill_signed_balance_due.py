"""Backfill signed balance_due for rows overpaid before the overpayment fix

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-06-27 12:00:00.000000

Migration a6b7c8d9e0f1 dropped the ``balance_due >= 0`` CHECK on invoices,
expenses and purchase_orders so an overpayment can drive the derived ledger
value negative (a credit owed back). However, any document that was
overpaid *before* that fix had its balance clamped to 0 by the old CHECK /
service guards, and a6b7c8d9e0f1 only dropped the constraints — it never
reconciled the already-clamped rows. Those rows therefore still read as an
exact 0.00 settlement instead of showing the real negative balance.

This is a pure data migration. For each table it recomputes the canonical
ledger value

    balance_due = <total_column> - amount_paid

and writes it back ONLY for rows where the stored balance_due drifted from
that formula (the clamped overpaid rows, plus any other historical drift).
The total column differs per table:

    invoices         -> total_due
    expenses         -> total_due
    purchase_orders  -> total

Status is deliberately not touched: PAID remains the correct ledger status
for an over-settled document, and "Overpaid" is derived in the UI from the
(now correct) negative balance. amount_paid and total[_due] are the inputs
and are left as-is.

Idempotent: re-running writes nothing because every row already satisfies
the formula after the first pass.

Downgrade re-clamps any negative balances back to 0, mirroring the pre-fix
state (it cannot recover the original clamped-vs-untouched distinction, but
0 is exactly what the old constraint would have stored).
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, total_column) for each payable/receivable document. balance_due and
# amount_paid are named identically across all three tables.
_BALANCE_TABLES: tuple[tuple[str, str], ...] = (
    ("invoices", "total_due"),
    ("expenses", "total_due"),
    ("purchase_orders", "total"),
)


def upgrade() -> None:
    for table, total_column in _BALANCE_TABLES:
        # Recompute the canonical balance only where the stored value drifted,
        # so the write set is limited to the historically clamped/overpaid
        # rows and the migration is a no-op on a healthy database.
        op.execute(
            f"UPDATE {table} "
            f"SET balance_due = {total_column} - amount_paid "
            f"WHERE balance_due <> {total_column} - amount_paid"
        )


def downgrade() -> None:
    # Mirror the pre-fix state: the old CHECK could only ever hold a
    # non-negative balance, so clamp the credits back to 0.
    for table, _total_column in _BALANCE_TABLES:
        op.execute(f"UPDATE {table} SET balance_due = 0 WHERE balance_due < 0")
