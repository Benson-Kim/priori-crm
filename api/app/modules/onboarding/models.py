"""SQLAlchemy models for the Sales Desk onboarding module.

An Onboarding is the delivery checklist created automatically — inside the
same transaction — when a deal is closed WON (issue #41). Its tasks are
copied verbatim from the org's onboarding task template at CREATE TIME
ONLY and the template version is snapshotted, so editing the template
never mutates existing checklists (template versioning).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.database import Base


class Onboarding(Base):
    """One delivery checklist for a won deal.

    ``plan_label`` is the display string the Onboarding screen renders
    verbatim ("{product} · {seats} seats", e.g. "Microsoft 365 E5 · 200
    seats"), captured from the deal at creation. Percent complete and the
    "N of M tasks complete" counts are always computed server-side from
    the task rows — they are never stored.
    """

    __tablename__ = "onboardings"

    __table_args__ = (
        # Exactly one checklist per won deal.
        UniqueConstraint("deal_id", name="uq_onboardings_deal_id"),
        CheckConstraint(
            "template_version > 0",
            name="ck_onboardings_template_version_positive",
        ),
        # Onboarding list hot path: newest checklists per customer.
        Index("ix_onboardings_customer_created", "customer_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("deals.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Won deal this checklist was created from",
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Customer (company) being onboarded",
    )

    plan_label: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
        comment=(
            "Display plan line captured from the deal at creation: "
            "'{product} · {seats} seats'"
        ),
    )

    template_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment=(
            "Owner onboarding-template version this checklist was created "
            "from (template versioning: later edits never mutate it)"
        ),
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When every task was done (cleared if a task is re-opened)",
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
    deal = relationship("Deal", lazy="joined")

    customer = relationship("Customer", lazy="joined")

    tasks = relationship(
        "OnboardingTask",
        back_populates="onboarding",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="OnboardingTask.position",
        lazy="selectin",
    )

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    def __repr__(self) -> str:
        return (
            f"<Onboarding(id={self.id}, deal_id={self.deal_id}, "
            f"plan_label='{self.plan_label}')>"
        )


class OnboardingTask(Base):
    """One ordered checklist step (position, label, done, completed_at)."""

    __tablename__ = "onboarding_tasks"

    __table_args__ = (
        UniqueConstraint(
            "onboarding_id", "position", name="uq_onboarding_tasks_position"
        ),
        CheckConstraint("position > 0", name="ck_onboarding_tasks_position_positive"),
        CheckConstraint(
            "length(trim(label)) > 0",
            name="ck_onboarding_tasks_label_not_empty",
        ),
        # done and completed_at always move together.
        CheckConstraint(
            "(done = true AND completed_at IS NOT NULL) OR "
            "(done = false AND completed_at IS NULL)",
            name="ck_onboarding_tasks_done_completed_at_consistent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    onboarding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("onboardings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="1-based step order ('Step N' in the design)",
    )

    label: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Task label copied from the template at creation",
    )

    done: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Whether this step is complete",
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the step was completed (null while not done)",
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

    onboarding = relationship("Onboarding", back_populates="tasks")

    def __repr__(self) -> str:
        return (
            f"<OnboardingTask(onboarding_id={self.onboarding_id}, "
            f"position={self.position}, label='{self.label}', done={self.done})>"
        )
