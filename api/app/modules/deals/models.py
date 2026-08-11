"""Sales Desk deal + stage-history ORM models (issue #39).

A Deal is a first-class pipeline entity linked to an existing customer and
owned by a sales rep (user). Its lifecycle is a strict state machine:

- ``status='open'`` deals sit at one of the four ordered stages
  (activation -> qualification -> proposal_quote -> negotiation) and only
  ever advance one stage at a time;
- ``status='parked'`` deals are frozen in the future pipeline with a
  ``parked_until`` date and the ``resume_stage`` they return to;
- ``status='won'|'lost'`` are terminal and carry an enumerated close
  reason plus a free-text close note.

Every stage advance, activity log, close, park and resume writes a
DealActivity row — the stage record timeline. The FIRST activity's date
drives the derived "deal age" metric and the LAST activity's date drives
"idle days"; both are computed, never stored.
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
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.database import Base
from app.constants.enums import (
    DealLostReason,
    DealStage,
    DealStatus,
    DealWonReason,
)

_STAGE_IN_LIST = ", ".join(f"'{s.value}'" for s in DealStage)
_STATUS_IN_LIST = ", ".join(f"'{s.value}'" for s in DealStatus)
_CLOSE_REASON_IN_LIST = ", ".join(
    f"'{r.value}'" for r in (*DealWonReason, *DealLostReason)
)


class Deal(Base):
    """One sales pipeline deal (deal-desk prototype parity)."""

    __tablename__ = "deals"

    __table_args__ = (
        CheckConstraint(
            f"stage IN ({_STAGE_IN_LIST})",
            name="ck_deals_valid_stage",
        ),
        CheckConstraint(
            f"status IN ({_STATUS_IN_LIST})",
            name="ck_deals_valid_status",
        ),
        CheckConstraint(
            "currency IN ('USD', 'KES')",
            name="ck_deals_valid_currency",
        ),
        CheckConstraint(
            "seats > 0",
            name="ck_deals_seats_positive",
        ),
        CheckConstraint(
            "value >= 0",
            name="ck_deals_value_non_negative",
        ),
        CheckConstraint(
            f"resume_stage IS NULL OR resume_stage IN ({_STAGE_IN_LIST})",
            name="ck_deals_valid_resume_stage",
        ),
        CheckConstraint(
            f"close_reason IS NULL OR close_reason IN ({_CLOSE_REASON_IN_LIST})",
            name="ck_deals_valid_close_reason",
        ),
        # Closed deals always carry a reason; open/parked deals never do.
        CheckConstraint(
            "(status IN ('won', 'lost')) = (close_reason IS NOT NULL)",
            name="ck_deals_close_reason_iff_closed",
        ),
        # Parked deals always know when to resurface and where to resume.
        CheckConstraint(
            "(status = 'parked') = (parked_until IS NOT NULL)",
            name="ck_deals_parked_until_iff_parked",
        ),
        Index("ix_deals_status_stage", "status", "stage"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Customer (company) this deal belongs to",
    )

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Sales rep who owns this deal (null after user deletion)",
    )

    product: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Product / plan being sold (e.g. 'Microsoft 365 E5')",
    )

    seats: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Number of seats/licences",
    )

    value: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        comment="Annual deal value in the deal's own currency (no floats)",
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        comment=(
            "Deal value currency — follows the customer's primary_currency "
            "billing profile (USD|KES); EUR/GBP are display-only elsewhere"
        ),
    )

    stage: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DealStage.ACTIVATION,
        server_default=text("'activation'"),
        index=True,
        comment="Current pipeline stage (only meaningful while open)",
    )

    status: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=DealStatus.OPEN,
        server_default=text("'open'"),
        index=True,
        comment="Lifecycle status: open | won | lost | parked",
    )

    parked_until: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Re-engage date while parked in the future pipeline",
    )

    resume_stage: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Stage the deal returns to when resumed from parked",
    )

    close_reason: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Enumerated won/lost reason (prototype lists); closed only",
    )

    close_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Free-text note recorded when the deal was closed",
    )

    version: Mapped[int] = mapped_column(
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="Optimistic-locking version counter",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
        comment="Record creation timestamp",
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
        comment="Record last update timestamp",
    )

    # Relationships
    customer = relationship("Customer")
    owner = relationship("User")
    activities = relationship(
        "DealActivity",
        back_populates="deal",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DealActivity.activity_date.asc(), DealActivity.created_at.asc()",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<Deal(id={self.id}, customer_id={self.customer_id}, "
            f"status={self.status}, stage={self.stage})>"
        )


class DealActivity(Base):
    """One immutable stage-record entry on a deal's timeline.

    ``stage_label`` is the display label the prototype writes into the
    history ('Activation', 'Proposal & Quote', 'Closed — Won', 'Moved to
    future pipeline', ...) — free text by design, since closing and parking
    write labels that are not pipeline stages. The note is mandatory and
    non-blank: 'A note is required to advance or close.'
    """

    __tablename__ = "deal_activities"

    __table_args__ = (
        CheckConstraint(
            "TRIM(note) <> ''",
            name="ck_deal_activities_note_not_blank",
        ),
        Index("ix_deal_activities_deal_date", "deal_id", "activity_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Deal this record belongs to",
    )

    stage_label: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Display label of the stage/event at the time of the record",
    )

    note: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Mandatory non-blank record note",
    )

    activity_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Organisation-local date of the record (drives age/idle)",
    )

    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who wrote the record (null = system or deleted user)",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
        comment="Exact creation instant (orders same-day records)",
    )

    # Relationships
    deal = relationship("Deal", back_populates="activities")
    author = relationship("User")

    def __repr__(self) -> str:
        return (
            f"<DealActivity(deal_id={self.deal_id}, "
            f"stage_label='{self.stage_label}', date={self.activity_date})>"
        )
