"""SQLAlchemy models for the Sales Desk deals module.

A Deal is a first-class pipeline entity linked to an existing customer.
Its stage history lives in DealActivity rows (append-only): every stage
advance, close, park and resume writes one record, and the derived
``age_days`` / ``idle_days`` values are computed from the first / latest
record — the deal is only as fresh as its last note.
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
    BillingCurrency,
    DealLostReason,
    DealStage,
    DealStatus,
    DealWonReason,
)

#: SQL IN-list literals shared by the ORM CHECK constraints and the Alembic
#: migration, derived from the enums so the two layers cannot drift.
_DEAL_CURRENCY_CHECK_VALUES = ", ".join(f"'{m.value}'" for m in BillingCurrency)
_DEAL_STAGE_CHECK_VALUES = ", ".join(f"'{m.value}'" for m in DealStage)
_DEAL_STATUS_CHECK_VALUES = ", ".join(f"'{m.value}'" for m in DealStatus)
_DEAL_CLOSE_REASON_CHECK_VALUES = ", ".join(
    f"'{m.value}'" for m in (*DealWonReason, *DealLostReason)
)


class Deal(Base):
    """One pipeline deal for a customer.

    Lifecycle: OPEN (activation → qualification → proposal_quote →
    negotiation, one stage at a time) → WON | LOST (terminal, with an
    enumerated close reason + mandatory note). An OPEN deal may be PARKED
    into the future pipeline (``parked_until`` + ``resume_stage``) and
    resumed back to the stage it left.
    """

    __tablename__ = "deals"

    __table_args__ = (
        CheckConstraint(
            f"currency IN ({_DEAL_CURRENCY_CHECK_VALUES})",
            name="ck_deals_valid_currency",
        ),
        CheckConstraint(
            f"stage IN ({_DEAL_STAGE_CHECK_VALUES})",
            name="ck_deals_valid_stage",
        ),
        CheckConstraint(
            f"status IN ({_DEAL_STATUS_CHECK_VALUES})",
            name="ck_deals_valid_status",
        ),
        CheckConstraint(
            "resume_stage IS NULL OR "
            f"resume_stage IN ({_DEAL_STAGE_CHECK_VALUES})",
            name="ck_deals_valid_resume_stage",
        ),
        CheckConstraint(
            "close_reason IS NULL OR "
            f"close_reason IN ({_DEAL_CLOSE_REASON_CHECK_VALUES})",
            name="ck_deals_valid_close_reason",
        ),
        CheckConstraint("seats > 0", name="ck_deals_seats_positive"),
        CheckConstraint("value >= 0", name="ck_deals_value_non_negative"),
        # Parked deals must always know when and where to resume …
        CheckConstraint(
            "status != 'parked' OR "
            "(parked_until IS NOT NULL AND resume_stage IS NOT NULL)",
            name="ck_deals_parked_fields_present",
        ),
        # … and park bookkeeping never lingers on non-parked deals.
        CheckConstraint(
            "status = 'parked' OR (parked_until IS NULL AND resume_stage IS NULL)",
            name="ck_deals_parked_fields_absent",
        ),
        # Closed deals always carry the evidence: reason, note, timestamp.
        CheckConstraint(
            "status NOT IN ('won', 'lost') OR "
            "(close_reason IS NOT NULL AND close_note IS NOT NULL "
            "AND closed_at IS NOT NULL)",
            name="ck_deals_closed_fields_present",
        ),
        CheckConstraint(
            "status IN ('won', 'lost') OR "
            "(close_reason IS NULL AND close_note IS NULL AND closed_at IS NULL)",
            name="ck_deals_closed_fields_absent",
        ),
        # List-filter hot paths: tab (status), owner rail, stage columns,
        # and the per-customer deals rollup on the Companies screen.
        Index("ix_deals_status_stage", "status", "stage"),
        Index("ix_deals_owner_status", "owner_id", "status"),
        Index("ix_deals_customer_status", "customer_id", "status"),
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

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Sales rep who owns this deal",
    )

    product: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Product being sold (e.g. Microsoft 365 Business Premium)",
    )

    seats: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Number of seats/licences",
    )

    value: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        comment="Annual deal value (ARR) in the deal currency",
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        comment=(
            "Deal currency (USD|KES) — must match one of the customer's "
            "billing-profile currencies"
        ),
    )

    stage: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DealStage.ACTIVATION,
        server_default=text("'activation'"),
        index=True,
        comment="Current (or last open) pipeline stage",
    )

    status: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=DealStatus.OPEN,
        server_default=text("'open'"),
        index=True,
        comment="Deal lifecycle status: open | won | lost | parked",
    )

    parked_until: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Re-engage date while the deal sits in the future pipeline",
    )

    resume_stage: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Stage the deal returns to when re-engaged from parking",
    )

    close_reason: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Enumerated main reason the deal was won/lost",
    )

    close_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Mandatory free-text note recorded at close",
    )

    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the deal was closed (won or lost)",
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who created this deal",
    )

    version: Mapped[int] = mapped_column(
        Integer,
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
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    customer = relationship("Customer", lazy="joined")

    owner = relationship("User", foreign_keys=[owner_id], lazy="joined")

    activities = relationship(
        "DealActivity",
        back_populates="deal",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DealActivity.activity_date, DealActivity.created_at",
        lazy="selectin",
    )

    @property
    def is_open(self) -> bool:
        return self.status == DealStatus.OPEN

    @property
    def is_closed(self) -> bool:
        return self.status in (DealStatus.WON, DealStatus.LOST)

    def __repr__(self) -> str:
        return (
            f"<Deal(id={self.id}, customer_id={self.customer_id}, "
            f"stage={self.stage}, status={self.status}, value={self.value})>"
        )


class DealActivity(Base):
    """One append-only stage-record entry of a deal's history.

    Written on create, log-activity, stage advance, close, park and
    resume. ``stage_label`` is the display label the drawer renders
    verbatim (e.g. "Proposal & Quote", "Closed — Won", "Moved to future
    pipeline"), captured at write time so history never re-labels itself
    when a deal moves on.
    """

    __tablename__ = "deal_activities"

    __table_args__ = (
        # A record is worthless without its note — DB-level backstop for
        # the service/schema validation ("A note is required to advance
        # or close.").
        CheckConstraint(
            "length(trim(note)) > 0",
            name="ck_deal_activities_note_not_empty",
        ),
        # History reads: newest/oldest record per deal.
        Index(
            "ix_deal_activities_deal_date",
            "deal_id",
            "activity_date",
            "created_at",
        ),
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
    )

    stage_label: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Display label of the record (stage name or lifecycle event)",
    )

    note: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Mandatory note — every stage keeps a record",
    )

    activity_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Organization-local date of the record (drives age/idle)",
    )

    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who wrote the record (null = system)",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
    )

    deal = relationship("Deal", back_populates="activities")

    def __repr__(self) -> str:
        return (
            f"<DealActivity(deal_id={self.deal_id}, "
            f"stage_label='{self.stage_label}', date={self.activity_date})>"
        )
