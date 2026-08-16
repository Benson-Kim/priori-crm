"""trigram (pg_trgm) GIN indexes for LIKE-safe search

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-04 12:00:00.000000

The list endpoints search with ILIKE '%term%'. A leading wildcard cannot use a
btree index, so each search was a full table scan. Trigram (pg_trgm) GIN
indexes make substring ILIKE index-assisted. These are PostgreSQL-only
constructs; the extension and indexes are created defensively with IF NOT
EXISTS so re-runs and partially-applied environments are safe.

NOTE — amended after this revision was already applied in production/CI
(deployment-pipeline MR, 2026-08): upgrade() now skips cleanly when pg_trgm
is not installable (no postgresql-contrib, e.g. the MochaHost staging
server) instead of aborting the migration chain. Editing an applied
migration is normally forbidden; it is safe here because databases that
already ran the original version are unaffected — the guard only changes
behaviour where pg_trgm is unavailable, and every statement is IF NOT
EXISTS-idempotent. Hosts that skipped the indexes catch up later via
deploy/enable_trgm_indexes.sql.
"""

import logging
from typing import Sequence, Union

from alembic import op


logger = logging.getLogger("alembic.runtime.migration")

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index_name, table, column) for each searched text column.
_TRGM_INDEXES: list[tuple[str, str, str]] = [
    # invoices
    ("ix_invoices_invoice_number_trgm", "invoices", "invoice_number"),
    ("ix_invoices_invoice_reference_trgm", "invoices", "invoice_reference"),
    # quotes
    ("ix_quotes_quote_number_trgm", "quotes", "quote_number"),
    ("ix_quotes_quote_reference_trgm", "quotes", "quote_reference"),
    # expenses
    ("ix_expenses_expense_number_trgm", "expenses", "expense_number"),
    ("ix_expenses_expense_reference_trgm", "expenses", "expense_reference"),
    # vendors
    ("ix_vendors_vendor_name_trgm", "vendors", "vendor_name"),
    ("ix_vendors_email_trgm", "vendors", "email"),
    # customers - names + company + email are the searched columns
    ("ix_customers_first_name_trgm", "customers", "first_name"),
    ("ix_customers_last_name_trgm", "customers", "last_name"),
    ("ix_customers_company_name_trgm", "customers", "company_name"),
    ("ix_customers_email_trgm", "customers", "email"),
]


def trgm_available(bind) -> bool:
    """True when pg_trgm is installed or installable on this server.

    Shared/managed PostgreSQL hosts routinely ship without the contrib package,
    and then pg_trgm is not merely un-installed but absent from
    pg_available_extensions. An unguarded CREATE EXTENSION aborts the migration
    with:

        could not open extension control file
        ".../pg_trgm.control": No such file or directory

    Observed on the MochaHost cPanel staging host (PostgreSQL 13.23). Checking
    availability keeps hosts that DO ship contrib on the identical code path.
    """
    return bool(
        bind.exec_driver_sql(
            "SELECT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm')"
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()
    # Trigram indexes are a PostgreSQL feature; skip cleanly on other backends
    # (e.g. the SQLite fallback used for pure-logic unit tests).
    if bind.dialect.name != "postgresql":
        return

    if not trgm_available(bind):
        # These indexes are a pure performance optimisation — ILIKE '%term%'
        # still returns correct results without them, just via a sequential
        # scan. Skipping keeps the environment deployable; failing here would
        # block the whole migration chain over an index.
        logger.warning(
            "pg_trgm is not available on this server; skipping %d trigram search "
            "indexes. Search will use sequential scans. To add them later, install "
            "the postgresql-contrib package and run deploy/enable_trgm_indexes.sql.",
            len(_TRGM_INDEXES),
        )
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
