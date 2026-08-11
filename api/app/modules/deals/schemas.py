"""Pydantic schemas for the Sales Desk deals module (issue #39).

Derived metrics (never stored, always computed server-side from the stage
record) exposed on every deal payload:

- ``age_days``        days since the FIRST activity ("deal age");
- ``idle_days``       days since the LAST activity;
- ``days_in_pipeline``  total run: first activity -> last activity for
  closed/parked deals, first activity -> today for open ones (the design's
  "Nd in pipeline" / "Nd total" ribbon);
- ``stage_position`` / ``stage_progress``  the design's "Stage N of 5"
  strip, where position 5 is the closed (won/lost) end-cap.

Lists and aggregates carry ``value_usd`` (USD equivalent at the fixed FX
table) so multi-currency rows can be summed; detail views display the native
``value`` + ``currency``.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.constants.enums import (
    DEAL_STAGE_PROGRESS_TOTAL,
    BillingCurrency,
    DealStage,
    DealStatus,
)

# Request schemas


class DealCreate(BaseModel):
    """Request body for creating a new deal."""

    customer_id: UUID = Field(
        ..., description="Customer (company) ID", alias="customerId"
    )
    owner_id: UUID = Field(
        ..., description="Sales rep (user) who owns the deal", alias="ownerId"
    )
    product: str = Field(
        ..., min_length=1, max_length=100, description="Product / plan"
    )
    seats: int = Field(..., gt=0, le=1_000_000, description="Number of seats")
    value: Decimal = Field(
        ...,
        ge=0,
        le=Decimal("9999999999999.99"),
        description="Annual deal value in the deal currency",
    )
    currency: BillingCurrency | None = Field(
        None,
        description=(
            "Deal value currency. Omit to follow the customer's "
            "primary_currency billing profile (defaults to USD)."
        ),
    )
    note: str | None = Field(
        None,
        max_length=2000,
        description="Optional opening stage-record note",
    )

    model_config = {"populate_by_name": True}


class DealUpdate(BaseModel):
    """Partial update of an open deal's editable fields."""

    product: str | None = Field(None, min_length=1, max_length=100)
    seats: int | None = Field(None, gt=0, le=1_000_000)
    value: Decimal | None = Field(None, ge=0, le=Decimal("9999999999999.99"))
    owner_id: UUID | None = Field(None, alias="ownerId")

    model_config = {"populate_by_name": True}


class DealActivityCreate(BaseModel):
    """Request body for logging an activity on the current stage."""

    note: str = Field(..., max_length=2000, description="What happened")


class DealAdvanceRequest(BaseModel):
    """Request body for advancing a deal to its next stage."""

    note: str = Field(
        ...,
        max_length=2000,
        description="Mandatory stage record — 'A note is required to advance or close.'",
    )


class DealCloseRequest(BaseModel):
    """Request body for closing a deal as won or lost."""

    result: Literal["won", "lost"] = Field(..., description="Close outcome")
    reason: str = Field(
        ...,
        max_length=50,
        description="Enumerated main reason we won/lost (prototype lists)",
    )
    note: str = Field(
        ...,
        max_length=2000,
        description="Mandatory close note — 'A note is required to advance or close.'",
    )


class DealParkRequest(BaseModel):
    """Request body for parking a deal into the future pipeline."""

    parked_until: date = Field(
        ..., description="Re-engage date", alias="parkedUntil"
    )
    note: str = Field(
        ...,
        max_length=2000,
        description="Mandatory record of why now isn't the time",
    )

    model_config = {"populate_by_name": True}


# Response schemas


class DealActivityResponse(BaseModel):
    """One stage-record timeline entry."""

    id: UUID
    stage_label: str
    note: str
    activity_date: date
    author_id: UUID | None = None
    author_name: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DealSummary(BaseModel):
    """List-row projection of a deal with derived metrics."""

    id: UUID
    customer_id: UUID
    customer_name: str
    owner_id: UUID | None = None
    owner_name: str | None = None
    product: str
    seats: int
    value: Decimal
    currency: str
    value_usd: Decimal = Field(
        description="USD equivalent at the fixed FX table (for aggregation)"
    )
    status: DealStatus
    stage: DealStage
    stage_label: str
    stage_position: int = Field(
        description="1-based progress position; 5 when closed (won/lost)"
    )
    stage_total: int = DEAL_STAGE_PROGRESS_TOTAL
    stage_progress: str = Field(description="Design copy, e.g. 'Stage 2 of 5'")
    age_days: int = Field(description="Days since the first recorded activity")
    idle_days: int = Field(description="Days since the last recorded activity")
    days_in_pipeline: int = Field(
        description=(
            "Total pipeline run: first->last activity when closed/parked, "
            "first activity->today while open"
        )
    )
    parked_until: date | None = None
    resume_stage: DealStage | None = None
    close_reason: str | None = None
    latest_stage_label: str | None = None
    latest_note: str | None = None
    latest_activity_date: date | None = None
    version: int
    created_at: datetime


class DealResponse(DealSummary):
    """Full deal detail including the close note and stage record."""

    close_note: str | None = None
    activities: list[DealActivityResponse] = []
    updated_at: datetime


class DealStageAggregate(BaseModel):
    """Per-stage open-pipeline aggregate (stage strip)."""

    stage: DealStage
    stage_label: str
    count: int
    value_usd: Decimal


class DealOwnerAggregate(BaseModel):
    """Per-rep aggregate (rep scoreboard)."""

    owner_id: UUID | None = None
    owner_name: str | None = None
    open_count: int
    open_value_usd: Decimal
    won_count: int
    lost_count: int


class DealAggregatesResponse(BaseModel):
    """Trivial per-stage and per-rep aggregates for the pipeline header.

    Richer analytics (win rates over time, avg days per stage, hygiene
    buckets) belong to the analytics backend (#45).
    """

    stages: list[DealStageAggregate]
    owners: list[DealOwnerAggregate]


class DealDeleteResponse(BaseModel):
    """Result of a hard delete."""

    id: UUID
    deleted: bool = True
