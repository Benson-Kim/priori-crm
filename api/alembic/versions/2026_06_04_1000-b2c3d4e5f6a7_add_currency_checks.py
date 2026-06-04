"""add currency CHECK constraints to money tables (W2.3 / P-11)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-04 10:00:00.000000

Constrains the currency column to the supported ISO 4217 domain
('KES','USD','EUR','GBP') on invoices, quotes, expenses, and vendors,
mirroring the existing customers currency CHECK. Unknown codes are
rejected at the database.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CURRENCY_CHECK = "currency IN ('KES', 'USD', 'EUR', 'GBP')"

_TABLES = (
    ("invoices", "ck_invoices_valid_currency"),
    ("quotes", "ck_quotes_valid_currency"),
    ("expenses", "ck_expenses_valid_currency"),
    ("vendors", "ck_vendors_valid_currency"),
)


def upgrade() -> None:
    for table, constraint in _TABLES:
        op.create_check_constraint(constraint, table, _CURRENCY_CHECK)


def downgrade() -> None:
    for table, constraint in _TABLES:
        op.drop_constraint(constraint, table, type_='check')
