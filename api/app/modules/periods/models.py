"""Accounting periods: open/closed windows that lock financial writes.

A closed period freezes history: no document may be created, edited or
paid with a date inside it (see app/common/period_guard.py). Closing and
reopening are privileged, audited operations. Periods must not overlap;
the service enforces this on create (a privileged, low-frequency path).
"""

import uuid
from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Date, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.database import Base


class PeriodStatus(StrEnum):
    """Lifecycle of an accounting period."""

    OPEN = "open"
    CLOSED = "closed"


class AccountingPeriod(Base):
    """One accounting period (e.g. a month) that can be closed."""

    __tablename__ = "accounting_periods"

    __table_args__ = (
        CheckConstraint(
            "end_date >= start_date",
            name="ck_accounting_periods_range_valid",
        ),
        CheckConstraint(
            "status IN ('open', 'closed')",
            name="ck_accounting_periods_valid_status",
        ),
        # The guard's hot query: closed periods containing a date.
        Index(
            "ix_accounting_periods_status_dates",
            "status",
            "start_date",
            "end_date",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=PeriodStatus.OPEN.value,
        server_default=text("'open'"),
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who closed the period (null = system)",
    )
    reopen_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Mandatory justification recorded on the last reopen",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return f"<AccountingPeriod {self.start_date}..{self.end_date} {self.status}>"
