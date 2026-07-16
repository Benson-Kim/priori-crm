"""Add general-ledger tables (double-entry core, issue #7)

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-07-16 10:00:00.000000

accounts / journal_entries / journal_lines. Entries are append-only and
idempotent per source event (unique (source_type, source_id)); lines
carry exactly one side (CHECK). The built-in chart of accounts is
get-or-created by the service on first use, so no seed step is needed
and the create_all test schema behaves identically.

After deploying, run POST /api/v1/ledger/internal/backfill once (with
X-Internal-Secret) to post history from existing documents; the call is
idempotent and safe to re-run.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "type IN ('asset', 'liability', 'equity', 'income', 'expense')",
            name="ck_accounts_valid_type",
        ),
        sa.UniqueConstraint("code", name="uq_accounts_code"),
    )
    op.create_index("ix_accounts_code", "accounts", ["code"])

    op.create_table(
        "journal_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("memo", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "source_type", "source_id", name="uq_journal_entries_source"
        ),
    )
    op.create_index("ix_journal_entries_entry_date", "journal_entries", ["entry_date"])

    op.create_table(
        "journal_lines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("journal_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "debit",
            sa.Numeric(precision=15, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "credit",
            sa.Numeric(precision=15, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.CheckConstraint(
            "(debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)",
            name="ck_journal_lines_single_side",
        ),
    )
    op.create_index("ix_journal_lines_entry", "journal_lines", ["entry_id"])
    op.create_index("ix_journal_lines_account", "journal_lines", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_journal_lines_account", table_name="journal_lines")
    op.drop_index("ix_journal_lines_entry", table_name="journal_lines")
    op.drop_table("journal_lines")
    op.drop_index("ix_journal_entries_entry_date", table_name="journal_entries")
    op.drop_table("journal_entries")
    op.drop_index("ix_accounts_code", table_name="accounts")
    op.drop_table("accounts")
