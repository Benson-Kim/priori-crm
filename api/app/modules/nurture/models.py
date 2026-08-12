"""SQLAlchemy models for the Sales Desk future pipeline (issue #40).

A NurtureProspect is a company we plan to engage on a future date but who
is NOT yet a customer. The "Engage" action atomically creates a customer
(via the existing customers service) plus an open deal at stage Activation
and removes the prospect, so a prospect never coexists with its customer.

Nothing about "due" is persisted: the ``due_state`` badges and the
notification counts are computed server-side against the org-local
accounting date boundary (``common/reporting_time.py``) at read time.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.database import Base


class NurtureProspect(Base):
    """One planned prospect on the future pipeline nurture list."""

    __tablename__ = "nurture_prospects"

    __table_args__ = (
        CheckConstraint(
            "length(trim(company_name)) > 0",
            name="ck_nurture_prospects_company_not_empty",
        ),
        CheckConstraint(
            "est_annual_value IS NULL OR est_annual_value >= 0",
            name="ck_nurture_prospects_est_value_non_negative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Sales rep who owns this prospect (null = unassigned)",
    )

    company_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Company we plan to engage (not yet a customer)",
    )

    contact: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Contact person at the company",
    )

    note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Why/when to engage (rendered verbatim on the list)",
    )

    engage_on: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
        comment="Engage-by date driving the due badges and notifications",
    )

    est_annual_value: Mapped[Decimal | None] = mapped_column(
        Numeric(15, 2),
        nullable=True,
        comment="Estimated annual value (est. ARR) if we win the company",
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who created this prospect",
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

    owner = relationship("User", foreign_keys=[owner_id], lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<NurtureProspect(id={self.id}, company='{self.company_name}', "
            f"engage_on={self.engage_on})>"
        )
