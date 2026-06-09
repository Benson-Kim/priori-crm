"""trigram (pg_trgm) GIN indexes for LIKE-safe search (W3.1 / P-2)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-04 12:00:00.000000

The list endpoints search with ILIKE '%term%'. A leading wildcard cannot use a
btree index, so each search was a full table scan. Trigram (pg_trgm) GIN
indexes make substring ILIKE index-assisted. These are PostgreSQL-only
constructs; the extension and indexes are created defensively with IF NOT
EXISTS so re-runs and partially-applied environments are safe.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index_name, table, column) for each searched text column.
_TRGM_INDEXES: list[tuple[str, str, str]] = [
    # invoices (INV-DBA-1)
    ("ix_invoices_invoice_number_trgm", "invoices", "invoice_number"),
    ("ix_invoices_invoice_reference_trgm", "invoices", "invoice_reference"),
    # quotes (QT-DBA-2)
    ("ix_quotes_quote_number_trgm", "quotes", "quote_number"),
    ("ix_quotes_quote_reference_trgm", "quotes", "quote_reference"),
    # expenses (EXP-DBA-1)
    ("ix_expenses_expense_number_trgm", "expenses", "expense_number"),
    ("ix_expenses_expense_reference_trgm", "expenses", "expense_reference"),
    # vendors (VEND-DBA-2)
    ("ix_vendors_vendor_name_trgm", "vendors", "vendor_name"),
    ("ix_vendors_email_trgm", "vendors", "email"),
    # customers (CUST-DBA-3) - names + company + email are the searched columns
    ("ix_customers_first_name_trgm", "customers", "first_name"),
    ("ix_customers_last_name_trgm", "customers", "last_name"),
    ("ix_customers_company_name_trgm", "customers", "company_name"),
    ("ix_customers_email_trgm", "customers", "email"),
]


def upgrade() -> None:
    bind = op.get_bind()
    # Trigram indexes are a PostgreSQL feature; skip cleanly on other backends
    # (e.g. the SQLite fallback used for pure-logic unit tests).
    if bind.dialect.name != "postgresql":
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for index_name, table, column in _TRGM_INDEXES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} "
            f"ON {table} USING gin ({column} gin_trgm_ops)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for index_name, _table, _column in _TRGM_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
    # Leave the pg_trgm extension in place; other objects may rely on it.
