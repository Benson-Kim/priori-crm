"""Double-entry general ledger: accounts, journal entries, journal lines.

Entries are append-only evidence, never state: application code creates
them in the same transaction as the document mutation they describe and
never updates or deletes them (corrections are mirrored reversal
entries). The unique (source_type, source_id) key makes every posting
idempotent, which is what allows the backfill to be safely re-runnable.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.database import Base


class Account(Base):
    """One chart-of-accounts entry (e.g. 1100 Accounts Receivable)."""

    __tablename__ = "accounts"

    __table_args__ = (
        CheckConstraint(
            "type IN ('asset', 'liability', 'equity', 'income', 'expense')",
            name="ck_accounts_valid_type",
        ),
        UniqueConstraint("code", name="uq_accounts_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="asset | liability | equity | income | expense",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def __repr__(self) -> str:
        return f"<Account {self.code} {self.name}>"


class JournalEntry(Base):
    """One immutable, balanced journal entry."""

    __tablename__ = "journal_entries"

    __table_args__ = (
        # Idempotent posting: one entry per source event, forever.
        UniqueConstraint(
            "source_type", "source_id", name="uq_journal_entries_source"
        ),
        Index("ix_journal_entries_entry_date", "entry_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        comment="invoice_issued | invoice_payment | expense_recorded | ...",
    )
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    memo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def __repr__(self) -> str:
        return f"<JournalEntry {self.source_type}/{self.source_id}>"


class JournalLine(Base):
    """One debit or credit line of a journal entry."""

    __tablename__ = "journal_lines"

    __table_args__ = (
        # Exactly one side per line; zero-amount lines are never written.
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)",
            name="ck_journal_lines_single_side",
        ),
        Index("ix_journal_lines_entry", "entry_id"),
        Index("ix_journal_lines_account", "account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journal_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    debit: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
    )
    credit: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
    )
