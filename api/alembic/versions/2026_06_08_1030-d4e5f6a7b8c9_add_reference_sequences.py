"""add reference_sequences high-water-mark table (W3.8 / P-4 / V-DI-5)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-08 10:30:00.000000

A persistent, monotonic counter per numbering scope so a reference suffix is
never reused after the most-recent record is hard-deleted (a live MAX(suffix)+1
would otherwise drop back and reissue the freed number).

The table is backfilled from the existing maxima so numbering continues from
the current high-water mark rather than restarting:
- date-scoped invoice/quote numbers  -> scope_key 'invoice_number_<PREFIX-YYYYMMDD>' / 'quote_number_<...>'
- global invoice/quote references     -> scope_key 'invoice_reference_gen' / 'quote_reference_gen'
- expense number/reference            -> scope_key 'expense_number_<...>' / 'expense_reference_gen'

Backfill is best-effort and scoped to the patterns the generator emits; any
scope not pre-seeded simply starts from its live MAX on first generate.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.common.reference_triggers import (
    CREATE_REFERENCE_TRIGGERS_SQL,
    DROP_REFERENCE_TRIGGERS_SQL,
)


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reference_sequences",
        sa.Column("scope_key", sa.String(length=255), nullable=False),
        sa.Column(
            "last_value",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.PrimaryKeyConstraint("scope_key", name="pk_reference_sequences"),
    )

    # Backfill global (non-date-scoped) reference counters from current maxima.
    # Suffix layout: "<PREFIX>-NNNN"; split on the last '-' and cast.
    op.execute(
        """
        INSERT INTO reference_sequences (scope_key, last_value)
        SELECT 'invoice_reference_gen',
               COALESCE(MAX(CAST(split_part(invoice_reference, '-', 2) AS BIGINT)), 0)
        FROM invoices
        WHERE invoice_reference ~ '^[A-Za-z]+-[0-9]+$'
        HAVING COUNT(*) > 0
        """
    )
    op.execute(
        """
        INSERT INTO reference_sequences (scope_key, last_value)
        SELECT 'quote_reference_gen',
               COALESCE(MAX(CAST(split_part(quote_reference, '-', 2) AS BIGINT)), 0)
        FROM quotes
        WHERE quote_reference ~ '^[A-Za-z]+-[0-9]+$'
        HAVING COUNT(*) > 0
        """
    )
    op.execute(
        """
        INSERT INTO reference_sequences (scope_key, last_value)
        SELECT 'expense_reference_gen',
               COALESCE(MAX(CAST(split_part(expense_reference, '-', 2) AS BIGINT)), 0)
        FROM expenses
        WHERE expense_reference ~ '^[A-Za-z]+-[0-9]+$'
        HAVING COUNT(*) > 0
        """
    )

    # Backfill date-scoped number counters (one row per prefix-day actually
    # present). Suffix layout: "<PREFIX>-YYYYMMDD-NNN"; the scope key mirrors
    # the generator's "<lock_key>_<PREFIX-YYYYMMDD>".
    op.execute(
        """
        INSERT INTO reference_sequences (scope_key, last_value)
        SELECT 'invoice_number_' || substring(invoice_number from 1 for 12),
               MAX(CAST(split_part(invoice_number, '-', 3) AS BIGINT))
        FROM invoices
        WHERE invoice_number ~ '^[A-Za-z]+-[0-9]{8}-[0-9]+$'
        GROUP BY substring(invoice_number from 1 for 12)
        """
    )
    op.execute(
        """
        INSERT INTO reference_sequences (scope_key, last_value)
        SELECT 'quote_number_' || substring(quote_number from 1 for 12),
               MAX(CAST(split_part(quote_number, '-', 3) AS BIGINT))
        FROM quotes
        WHERE quote_number ~ '^[A-Za-z]+-[0-9]{8}-[0-9]+$'
        GROUP BY substring(quote_number from 1 for 12)
        """
    )
    op.execute(
        """
        INSERT INTO reference_sequences (scope_key, last_value)
        SELECT 'expense_number_' || substring(expense_number from 1 for 12),
               MAX(CAST(split_part(expense_number, '-', 3) AS BIGINT))
        FROM expenses
        WHERE expense_number ~ '^[A-Za-z]+-[0-9]{8}-[0-9]+$'
        GROUP BY substring(expense_number from 1 for 12)
        """
    )

    # Install the AFTER INSERT triggers that keep the high-water mark advanced
    # for references created outside the generator.
    op.execute(CREATE_REFERENCE_TRIGGERS_SQL)


def downgrade() -> None:
    op.execute(DROP_REFERENCE_TRIGGERS_SQL)
    op.drop_table("reference_sequences")
